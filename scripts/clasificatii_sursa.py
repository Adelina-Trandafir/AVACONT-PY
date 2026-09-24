"""
clasificatii_sursa.py -- make `Clasificatii.Sector`, `Sursa` and `SS` WRITTEN columns
(slice 0075-00).

WHY. The three were GENERATED from `right(Capitol, 2)`, and `SS` carries the foreign key
into `AVACONT_COMUN.DefaSursaSector`. The CASE only knew the capitol endings 00/01/02/10,
so the generated `SS` could be `01A`, `02A` or `02E` and nothing else -- while
DefaSursaSector has fourteen values (`01D`, `01F`, `03A`, `08A`, ...). The source letter
(A/C/D/E/F/G) cannot be read from the capitol at all: `01A`, `01D`, `01F` and `01G` all end
in `01`. It is operator input pretending to be derived, so it has to be stored.

WHY ALL THREE, AND WHY ONLY THESE THREE. The first attempt kept `SS` generated as
`concat(<sector case>, Sursa)`. MariaDB 10.11 refuses that with **error 1901** -- proven on
this server 22.09.2026 in a FRESH table, so it is the expression shape, not the ALTER:

    Function or expression 'concat(case right(coalesce(`Capitol`,''),2) ... ,`Sursa`)'
    cannot be used in the GENERATED ALWAYS AS clause of `SS`

So `SS` cannot be generated from another column and must be written; `Sector` and `Sursa`
are the halves of it and follow. The other six generated columns (`Clsf`, `Titlu`,
`ClsfSal`, `ClsfF`, `ClsfE`, `ClsfX`) are left exactly as they are: they are pure functions
of Capitol/Subcapitol/Articol/Alineat, every writer already supplies those four, and two of
them carry foreign keys of their own. Generated, they CANNOT disagree with the base columns.
Written, an UPDATE that changes `Capitol` and forgets `ClsfF` would produce a row that
passes every foreign key and still lies. Decision of 22.09.2026.

WHAT IT DOES, per database (AVACONT_SURSA first -- it is the template -- then every unit
database, `NNN_*` in information_schema.SCHEMATA):

  1. mysqldump of `Clasificatii` alone, to <backup-dir>/<db>_Clasificatii_<stamp>.sql.
  2. Snapshot of the three columns: `_snap_clsf (IDClsf, Sector, Sursa, SS)`.
  3. Three plain columns `Sector_w` / `Sursa_w` / `SS_w`, filled from the generated ones.
     The values are COPIED, so nothing depends on how MariaDB treats the conversion.
  4. One ALTER TABLE: drop the foreign key and its index, drop the three generated columns,
     rename the three copies into their place with the right types and positions, put the
     index and the foreign key back. No generated expression is created, so 1901 cannot
     recur.
  5. Verify against the snapshot: same row count, and ZERO rows whose Sector, Sursa or SS
     differ (`<=>`). A difference stops the run and prints the restore command; the snapshot
     is dropped only after the check passes.

AFTER THIS, `SS` IS `NOT NULL` WITH NO DEFAULT and a live foreign key, so an INSERT that
omits it fails loudly (1364) instead of quietly storing a wrong sector. Every writer must
pass it -- see `routes/clasificatii_ss.py`, which holds the one rule they all share.
Deploy those writers and run this in the same maintenance window: between the two, an insert
into `Clasificatii` fails. Reads are unaffected throughout.

There is no rollback: DDL commits implicitly. Recovery is the dump of step 1, restored by
hand with the command the script prints (`--restore` prints it again).

Usage (from the PYTHON folder, with the venv):
    python -m scripts.clasificatii_sursa --dry-run            # list, counts, state; writes nothing
    python -m scripts.clasificatii_sursa                      # every database
    python -m scripts.clasificatii_sursa --db AVACONT_SURSA   # one database
    python -m scripts.clasificatii_sursa --clean-leftovers    # clear a failed run's temporaries
    python -m scripts.clasificatii_sursa --split-alter        # step 4 in three statements
    python -m scripts.clasificatii_sursa --restore --db 001_GR23 --file backup/001_GR23_...sql
"""

import argparse
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime

import mysql.connector

from config import DB_CONFIG_NEW
from routes.schema_sync.schema_common import (FORBIDDEN_TARGETS, SOURCE_DB,
                                              UNIT_DB_RE, query)
from routes.schema_sync.schema_execute import find_dump_tool

TABLE = "Clasificatii"
SNAPSHOT = "_snap_clsf"
SS_INDEX = "idx_SS"
SS_FK = "Clasificatii__DefaSS"
COMMON_DB = "AVACONT_COMUN"

# The three columns that stop being generated, in table order, each with the type it gets
# and the column it must sit after. `SS` deliberately has NO default: a writer that forgets
# it must fail (1364), not store a wrong sector.
CONVERTED = (
    ("Sector", "varchar(2) NOT NULL DEFAULT ''", "ClsfX"),
    ("Sursa", "char(1) NOT NULL DEFAULT 'A'", "Sector"),
    ("SS", "varchar(3) NOT NULL", "Sursa"),
)

TEMP_SUFFIX = "_w"


class MigrationError(RuntimeError):
    """Anything that must stop the run. The message is what the operator reads."""


@dataclass
class TableState:
    db: str
    exists: bool                 # Clasificatii exists in this database
    rows: int = 0
    generated: tuple = ()        # which of the three are still GENERATED
    ss_fk_name: str = None       # the FK constraint on SS, as the server names it
    ss_index_name: str = None    # the index on SS, as the server names it
    leftovers: tuple = ()        # `_snap_clsf` / `*_w` from an earlier, failed run
    positions: dict = field(default_factory=dict)   # column -> the column it sits after

    @property
    def migrated(self) -> bool:
        return self.exists and not self.generated

    @property
    def untouched(self) -> bool:
        """True when all three are still generated: nothing structural has happened yet."""
        return self.exists and len(self.generated) == len(CONVERTED)


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------
def connect():
    """K-BOT server (DB_CONFIG_NEW), autocommit on: DDL commits implicitly anyway."""
    cfg = dict(DB_CONFIG_NEW)
    cfg.setdefault("charset", "utf8mb4")
    cfg["autocommit"] = True
    try:
        return mysql.connector.connect(**cfg)
    except mysql.connector.Error as exc:
        raise MigrationError(
            f"Conectare eșuată la {cfg.get('host')}:{cfg.get('port')} — {exc}") from exc


def execute(conn, sql, params=()):
    cur = conn.cursor()
    try:
        cur.execute(sql, params)
    finally:
        cur.close()


def q(name: str) -> str:
    """Backtick-quote an identifier that already passed the pattern check."""
    if "`" in name:
        raise MigrationError(f"Identificator invalid: {name!r}")
    return f"`{name}`"


# ---------------------------------------------------------------------------
# Discovery and inspection
# ---------------------------------------------------------------------------
def list_databases(conn) -> list:
    """AVACONT_SURSA first, then every unit database on the server, sorted."""
    rows = query(conn, "SELECT SCHEMA_NAME AS name FROM information_schema.SCHEMATA "
                       "ORDER BY SCHEMA_NAME")
    names = [r["name"] for r in rows]
    units = [n for n in names if UNIT_DB_RE.match(n or "") and n not in FORBIDDEN_TARGETS]
    if SOURCE_DB not in names:
        raise MigrationError(f"Baza șablon {SOURCE_DB} nu există pe server.")
    return [SOURCE_DB] + units


def inspect(conn, db: str) -> TableState:
    cols = query(conn,
                 "SELECT COLUMN_NAME, EXTRA, ORDINAL_POSITION "
                 "FROM information_schema.COLUMNS "
                 "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s "
                 "ORDER BY ORDINAL_POSITION",
                 (db, TABLE))
    if not cols:
        return TableState(db=db, exists=False)
    by_name = {c["COLUMN_NAME"]: c for c in cols}
    order = [c["COLUMN_NAME"] for c in cols]

    missing = [name for name, _type, _after in CONVERTED if name not in by_name]
    if missing:
        raise MigrationError(
            f"{db}.{TABLE} nu are coloanele {', '.join(missing)} — structură necunoscută.")

    state = TableState(db=db, exists=True)
    state.rows = query(conn, f"SELECT COUNT(*) AS n FROM {q(db)}.{q(TABLE)}")[0]["n"]
    state.generated = tuple(
        name for name, _type, _after in CONVERTED
        if "GENERATED" in (by_name[name]["EXTRA"] or "").upper())

    # The column each one currently sits after, so the ALTER puts them back where they were
    # even if this table's order differs from the template's.
    for name, _type, _after in CONVERTED:
        idx = order.index(name)
        state.positions[name] = order[idx - 1] if idx > 0 else None

    fk = query(conn,
               "SELECT CONSTRAINT_NAME AS name FROM information_schema.KEY_COLUMN_USAGE "
               "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s AND COLUMN_NAME = 'SS' "
               "AND REFERENCED_TABLE_NAME IS NOT NULL",
               (db, TABLE))
    state.ss_fk_name = fk[0]["name"] if fk else None

    idx = query(conn,
                "SELECT DISTINCT INDEX_NAME AS name FROM information_schema.STATISTICS "
                "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s AND COLUMN_NAME = 'SS'",
                (db, TABLE))
    state.ss_index_name = idx[0]["name"] if idx else None

    leftovers = [name + TEMP_SUFFIX for name, _t, _a in CONVERTED
                 if name + TEMP_SUFFIX in by_name]
    if query(conn, "SELECT 1 AS x FROM information_schema.TABLES "
                   "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s", (db, SNAPSHOT)):
        leftovers.append(SNAPSHOT)
    state.leftovers = tuple(leftovers)
    return state


# ---------------------------------------------------------------------------
# Statements (pure: testable without a server)
# ---------------------------------------------------------------------------
def alter_clauses(state: TableState) -> tuple:
    """(drop_clauses, change_clauses, add_clauses) for the ALTER of step 4."""
    if not state.ss_fk_name:
        raise MigrationError(
            f"{state.db}.{TABLE}: nu există cheie străină pe SS — structura nu e cea așteptată.")

    drops = [f"DROP FOREIGN KEY {q(state.ss_fk_name)}"]
    if state.ss_index_name:
        drops.append(f"DROP KEY {q(state.ss_index_name)}")
    # Reverse table order: SS, then Sursa, then Sector. Nothing depends on the order here
    # (none of the three is referenced by another expression once SS stops being generated),
    # but dropping the foreign key's own column first keeps the statement readable.
    drops += [f"DROP COLUMN {q(name)}" for name, _t, _a in reversed(CONVERTED)]

    changes = []
    for name, coltype, default_after in CONVERTED:
        after = state.positions.get(name) or default_after
        # A column that sat after one of the three being dropped: fall back to the layout
        # the template has, so the ALTER never names a column that no longer exists.
        if after in [n for n, _t, _a in CONVERTED]:
            after = default_after
        changes.append(
            f"CHANGE COLUMN {q(name + TEMP_SUFFIX)} {q(name)} {coltype} AFTER {q(after)}")

    adds = [
        f"ADD KEY {q(SS_INDEX)} (`SS`)",
        f"ADD CONSTRAINT {q(SS_FK)} FOREIGN KEY (`SS`) "
        f"REFERENCES {q(COMMON_DB)}.`DefaSursaSector` (`SursaSector`) "
        f"ON DELETE RESTRICT ON UPDATE RESTRICT",
    ]
    return drops, changes, adds


def alter_statements(state: TableState, split: bool = False) -> list:
    """The ALTER TABLE(s) of step 4: one statement, or three with --split-alter."""
    head = f"ALTER TABLE {q(state.db)}.{q(TABLE)} "
    drops, changes, adds = alter_clauses(state)
    if split:
        return [head + ", ".join(drops), head + ", ".join(changes), head + ", ".join(adds)]
    return [head + ", ".join(drops + changes + adds)]


def temp_column_statements(db: str) -> list:
    """Step 3: the plain copies, added and filled. Nullable while they are temporary."""
    table = f"{q(db)}.{q(TABLE)}"
    adds = ", ".join(f"ADD COLUMN {q(name + TEMP_SUFFIX)} "
                     f"{coltype.split(' NOT NULL')[0]} NULL"
                     for name, coltype, _after in CONVERTED)
    sets = ", ".join(f"{q(name + TEMP_SUFFIX)} = {q(name)}"
                     for name, _t, _a in CONVERTED)
    return [f"ALTER TABLE {table} {adds}", f"UPDATE {table} SET {sets}"]


def cleanup_statements(state: TableState) -> list:
    """Drop what a failed run left behind. Only ever called on an untouched table."""
    stmts = []
    temps = [name for name in state.leftovers if name != SNAPSHOT]
    if temps:
        stmts.append(f"ALTER TABLE {q(state.db)}.{q(TABLE)} " +
                     ", ".join(f"DROP COLUMN {q(t)}" for t in temps))
    if SNAPSHOT in state.leftovers:
        stmts.append(f"DROP TABLE {q(state.db)}.{q(SNAPSHOT)}")
    return stmts


def verify_sql(db: str) -> tuple:
    """(count query for the table, count query for the snapshot, difference query)."""
    t = f"{q(db)}.{q(TABLE)}"
    s = f"{q(db)}.{q(SNAPSHOT)}"
    return (
        f"SELECT COUNT(*) AS n FROM {t}",
        f"SELECT COUNT(*) AS n FROM {s}",
        f"SELECT COUNT(*) AS n FROM {t} c JOIN {s} s ON s.IDClsf = c.IDClsf "
        f"WHERE NOT (c.Sector <=> s.Sector) OR NOT (c.Sursa <=> s.Sursa) OR NOT (c.SS <=> s.SS)",
    )


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------
def _client_args() -> list:
    return [f"--host={DB_CONFIG_NEW['host']}",
            f"--port={DB_CONFIG_NEW.get('port', 3306)}",
            f"--user={DB_CONFIG_NEW['user']}",
            f"--password={DB_CONFIG_NEW['password']}"]


def dump_table(db: str, backup_dir: str, say) -> str:
    tool = find_dump_tool()
    if not tool:
        raise MigrationError(
            "mysqldump / mariadb-dump nu a fost găsit. Fără copie de siguranță nu se modifică nimic.")
    os.makedirs(backup_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(backup_dir, f"{db}_{TABLE}_{stamp}.sql")
    cmd = [tool] + _client_args() + ["--single-transaction",
                                     "--default-character-set=utf8mb4", db, TABLE]
    shown = " ".join(shlex.quote(c) for c in cmd if not c.startswith("--password"))
    say(f"  copie: {shown} --password=*** > {out_path}")
    try:
        with open(out_path, "wb") as fh:
            proc = subprocess.run(cmd, stdout=fh, stderr=subprocess.PIPE, check=False)
    except OSError as exc:
        raise MigrationError(f"Copia pentru {db} nu a putut fi scrisă în {out_path}: {exc}") from exc
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", errors="replace").strip()
        raise MigrationError(f"mysqldump a eșuat pentru {db} (cod {proc.returncode}): {err}")
    if os.path.getsize(out_path) == 0:
        raise MigrationError(f"Copia {out_path} este goală — nu se continuă.")
    return out_path


def restore_command(db: str, dump_path: str) -> str:
    """The exact shell line that puts the table back. Printed, never run by the script."""
    args = " ".join(shlex.quote(a) for a in _client_args() if not a.startswith("--password"))
    return f"mysql {args} --password=*** {shlex.quote(db)} < {shlex.quote(dump_path)}"


# ---------------------------------------------------------------------------
# One database
# ---------------------------------------------------------------------------
def migrate_database(conn, db: str, backup_dir: str, split: bool, say,
                     clean_leftovers: bool = False) -> str:
    """Steps 1-5 on one database. Returns 'migrated' | 'skipped'. Raises on failure."""
    state = inspect(conn, db)
    if not state.exists:
        say(f"{db}: fără tabel {TABLE} — sărit.")
        return "skipped"
    if state.migrated:
        say(f"{db}: Sector/Sursa/SS sunt deja coloane scrise — sărit.")
        return "skipped"
    if not state.untouched:
        raise MigrationError(
            f"{db}: doar {', '.join(state.generated)} mai sunt generate — stare intermediară "
            f"necunoscută. Nu se continuă; verificați tabelul de mână.")
    if state.leftovers:
        if not clean_leftovers:
            raise MigrationError(
                f"{db}: rămășițe ale unei rulări anterioare ({', '.join(state.leftovers)}). "
                f"Tabelul este neatins, deci pot fi șterse: reluați cu --clean-leftovers.")
        for stmt in cleanup_statements(state):
            say(f"  curățare: {stmt}")
            execute(conn, stmt)
        state = inspect(conn, db)

    say(f"{db}: {state.rows} rânduri; FK pe SS = {state.ss_fk_name}, "
        f"index pe SS = {state.ss_index_name or '(niciunul)'}")

    # 1. backup
    dump_path = dump_table(db, backup_dir, say)
    restore = restore_command(db, dump_path)

    try:
        # 2. snapshot
        execute(conn, f"CREATE TABLE {q(db)}.{q(SNAPSHOT)} AS "
                      f"SELECT IDClsf, Sector, Sursa, SS FROM {q(db)}.{q(TABLE)}")
        # 3. copy the values into plain columns
        for stmt in temp_column_statements(db):
            execute(conn, stmt)
        # 4. the structural change
        for stmt in alter_statements(state, split):
            say(f"  {stmt[:140]}{'…' if len(stmt) > 140 else ''}")
            execute(conn, stmt)
        # 5. verify against the snapshot
        n_table, n_snap, n_diff = verify_sql(db)
        rows_after = query(conn, n_table)[0]["n"]
        rows_snap = query(conn, n_snap)[0]["n"]
        diff = query(conn, n_diff)[0]["n"]
        if rows_after != rows_snap or diff != 0:
            raise MigrationError(
                f"{db}: verificarea a eșuat — rânduri {rows_after} față de {rows_snap} în "
                f"instantaneu, {diff} rânduri cu Sector/Sursa/SS diferite. "
                f"Instantaneul {SNAPSHOT} a fost păstrat pentru comparație.")
    except mysql.connector.Error as exc:
        raise MigrationError(
            f"{db}: MariaDB a refuzat (errno {getattr(exc, 'errno', '?')}): {exc}\n"
            f"  Restaurare: {restore}") from exc
    except MigrationError as exc:
        raise MigrationError(f"{exc}\n  Restaurare: {restore}") from exc

    execute(conn, f"DROP TABLE {q(db)}.{q(SNAPSHOT)}")
    say(f"{db}: gata — {rows_after} rânduri, 0 diferențe; copia: {dump_path}")
    return "migrated"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Clasificatii: Sector/Sursa/SS din coloane generate în coloane scrise "
                    "(slice 0075-00).")
    p.add_argument("--dry-run", action="store_true",
                   help="listează bazele, numărul de rânduri și starea; nu modifică nimic")
    p.add_argument("--db", help="o singură bază (implicit: AVACONT_SURSA + toate unitățile)")
    p.add_argument("--backup-dir", default="backup",
                   help="folderul copiilor de siguranță (implicit: backup)")
    p.add_argument("--clean-leftovers", action="store_true",
                   help="șterge coloanele și instantaneul rămase de la o rulare eșuată "
                        "(doar pe un tabel neatins) și continuă")
    p.add_argument("--split-alter", action="store_true",
                   help="ALTER-ul din pasul 4 în trei instrucțiuni, dacă serverul refuză una singură")
    p.add_argument("--restore", action="store_true",
                   help="afișează comanda de restaurare pentru --db și --file; nu rulează nimic")
    p.add_argument("--file", help="copia .sql pentru --restore")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    say = print

    if args.restore:
        if not args.db or not args.file:
            print("--restore cere --db și --file.", file=sys.stderr)
            return 2
        say(restore_command(args.db, args.file))
        return 0

    try:
        conn = connect()
        try:
            if args.db:
                if args.db in FORBIDDEN_TARGETS and args.db != SOURCE_DB:
                    raise MigrationError(f"Bază interzisă: {args.db}")
                targets = [args.db]
            else:
                targets = list_databases(conn)

            if args.dry_run:
                say(f"Server: {DB_CONFIG_NEW['host']}:{DB_CONFIG_NEW.get('port', 3306)} — "
                    f"{len(targets)} baze de verificat (nimic nu se modifică).")
                for db in targets:
                    st = inspect(conn, db)
                    if not st.exists:
                        say(f"  {db:<16} fără tabel {TABLE}")
                        continue
                    status = ("migrat" if st.migrated else
                              "de migrat" if st.untouched else
                              f"stare intermediară ({', '.join(st.generated)} generate)")
                    extra = f"; rămășițe: {', '.join(st.leftovers)}" if st.leftovers else ""
                    say(f"  {db:<16} {st.rows:>7} rânduri  {status:<22} FK SS={st.ss_fk_name} "
                        f"index SS={st.ss_index_name}{extra}")
                return 0

            done = skipped = 0
            for db in targets:
                result = migrate_database(conn, db, args.backup_dir, args.split_alter, say,
                                          clean_leftovers=args.clean_leftovers)
                if result == "migrated":
                    done += 1
                else:
                    skipped += 1
            say(f"Gata: {done} baze migrate, {skipped} sărite.")
            return 0
        finally:
            conn.close()
    except MigrationError as exc:
        print(f"OPRIT: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
