"""
clasificatii_sursa.py -- make `Clasificatii.Sursa` a WRITTEN column (slice 0075-00).

WHY. `Clasificatii.Sector`, `Sursa` and `SS` are GENERATED from `right(Capitol, 2)`,
and `SS` carries the foreign key into `AVACONT_COMUN.DefaSursaSector`. The CASE only
knows the capitol endings 00/01/02/10, so the generated `SS` can be `01A`, `02A` or
`02E` and nothing else -- while `DefaSursaSector` has fourteen values (`01D`, `01F`,
`03A`, `08A`, ...). The source letter (A/C/D/E/F/G) cannot be read from the capitol at
all, so it has to be stored. Decision D15/D19 in docs/PLAN_AutoProvisioning.md: `Sursa`
becomes a plain `char(1) NOT NULL DEFAULT 'A'`, `Sector` keeps its CASE extended to
03/04/05/08, and `SS = concat(<sector case>, Sursa)` stays generated so the foreign key
keeps working.

WHAT IT DOES, per database (AVACONT_SURSA first -- it is the template -- then every
unit database, `NNN_*` in information_schema.SCHEMATA):

  1. mysqldump of `Clasificatii` alone, to <backup-dir>/<db>_Clasificatii_<stamp>.sql.
  2. Snapshot of the three columns: `_snap_clsf (IDClsf, Sector, Sursa, SS)`.
  3. `ADD COLUMN Sursa_w char(1) NULL`, then `UPDATE ... SET Sursa_w = Sursa` -- the
     values are COPIED, so nothing depends on how MariaDB converts a generated column.
  4. One ALTER TABLE: drop the FK on SS, drop SS, drop the generated Sursa, rename
     Sursa_w to Sursa (NOT NULL DEFAULT 'A'), redefine Sector with the extended CASE,
     re-add SS (generated, STORED), its index and its foreign key.
  5. Verify against the snapshot: same row count, and ZERO rows whose Sector, Sursa or
     SS differ (`<=>`). A difference stops the run and prints the restore command; the
     snapshot is dropped only after the check passes.

Why no existing row can change: today only the four capitol endings pass the foreign
key (any other ending computes SS = '', which DefaSursaSector refuses), and for those
four the extended CASE and the copied Sursa give exactly the old values. Step 5 proves
it instead of trusting the argument.

There is no rollback: DDL commits implicitly. Recovery is the dump of step 1, restored
by hand with the command the script prints (`--restore` prints it again).

UNVERIFIED on MariaDB 10.11 (the developer has no server): that one ALTER TABLE may
drop `Sursa` while `SS` -- whose expression inlines the same CASE, not the column --
is dropped in the same statement. If the server refuses the single statement, run
again with `--split-alter`: the same clauses in three statements (drop, rename/modify,
re-add). Between them the table has no SS for a few seconds; the dump covers that.

Usage (from the PYTHON folder, with the venv):
    python -m scripts.clasificatii_sursa --dry-run           # list, counts, state; writes nothing
    python -m scripts.clasificatii_sursa                     # every database
    python -m scripts.clasificatii_sursa --db AVACONT_SURSA  # one database
    python -m scripts.clasificatii_sursa --split-alter       # see above
    python -m scripts.clasificatii_sursa --restore --db 001_GR23 --file backup/001_GR23_Clasificatii_x.sql
"""

import argparse
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime

import mysql.connector

from config import DB_CONFIG_NEW
from routes.schema_sync.schema_common import (FORBIDDEN_TARGETS, SOURCE_DB,
                                              UNIT_DB_RE, query)
from routes.schema_sync.schema_execute import find_dump_tool

TABLE = "Clasificatii"
SNAPSHOT = "_snap_clsf"
TEMP_COLUMN = "Sursa_w"
SS_INDEX = "idx_SS"
SS_FK = "Clasificatii__DefaSS"
COMMON_DB = "AVACONT_COMUN"

# The sector CASE, extended. `00`/`01` -> 01 and `10` -> 02 are the historical rules;
# 03/04/05/08 are the sectors DefaSursaSector knows and the old CASE did not.
SECTOR_CASE_SQL = (
    "case right(coalesce(`Capitol`,''),2) "
    "when '00' then '01' when '01' then '01' "
    "when '02' then '02' when '10' then '02' "
    "when '03' then '03' when '04' then '04' when '05' then '05' when '08' then '08' "
    "else '' end"
)

# The eight endings the extended CASE maps; used by the state check only.
EXTENDED_ENDINGS = ("'03'", "'04'", "'05'", "'08'")


class MigrationError(RuntimeError):
    """Anything that must stop the run. The message is what the operator reads."""


@dataclass
class TableState:
    db: str
    exists: bool                 # Clasificatii exists in this database
    rows: int = 0
    sursa_generated: bool = False
    sector_extended: bool = False
    ss_fk_name: str = None       # the FK constraint on SS, as the server names it
    ss_index_name: str = None    # the index on SS, as the server names it
    leftovers: tuple = ()        # `_snap_clsf` / `Sursa_w` from an earlier, failed run

    @property
    def migrated(self) -> bool:
        return self.exists and (not self.sursa_generated) and self.sector_extended


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
                 "SELECT COLUMN_NAME, EXTRA, GENERATION_EXPRESSION "
                 "FROM information_schema.COLUMNS "
                 "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s",
                 (db, TABLE))
    if not cols:
        return TableState(db=db, exists=False)
    by_name = {c["COLUMN_NAME"]: c for c in cols}
    if "Sursa" not in by_name or "Sector" not in by_name or "Capitol" not in by_name:
        raise MigrationError(
            f"{db}.{TABLE} nu are coloanele Capitol/Sector/Sursa — structură necunoscută.")

    state = TableState(db=db, exists=True)
    state.rows = query(conn, f"SELECT COUNT(*) AS n FROM {q(db)}.{q(TABLE)}")[0]["n"]
    state.sursa_generated = "GENERATED" in (by_name["Sursa"]["EXTRA"] or "").upper()
    sector_expr = by_name["Sector"]["GENERATION_EXPRESSION"] or ""
    state.sector_extended = all(e in sector_expr for e in EXTENDED_ENDINGS)

    fk = query(conn,
               "SELECT k.CONSTRAINT_NAME AS name "
               "FROM information_schema.KEY_COLUMN_USAGE k "
               "WHERE k.TABLE_SCHEMA = %s AND k.TABLE_NAME = %s AND k.COLUMN_NAME = 'SS' "
               "AND k.REFERENCED_TABLE_NAME IS NOT NULL",
               (db, TABLE))
    state.ss_fk_name = fk[0]["name"] if fk else None

    idx = query(conn,
                "SELECT DISTINCT INDEX_NAME AS name FROM information_schema.STATISTICS "
                "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s AND COLUMN_NAME = 'SS'",
                (db, TABLE))
    state.ss_index_name = idx[0]["name"] if idx else None

    leftovers = []
    if TEMP_COLUMN in by_name:
        leftovers.append(TEMP_COLUMN)
    snap = query(conn, "SELECT 1 AS x FROM information_schema.TABLES "
                       "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s", (db, SNAPSHOT))
    if snap:
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
    drops += ["DROP COLUMN `SS`", "DROP COLUMN `Sursa`"]

    changes = [
        f"CHANGE COLUMN {q(TEMP_COLUMN)} `Sursa` char(1) NOT NULL DEFAULT 'A' AFTER `Sector`",
        f"MODIFY COLUMN `Sector` varchar(2) AS ({SECTOR_CASE_SQL}) STORED",
    ]
    adds = [
        f"ADD COLUMN `SS` varchar(3) AS (concat({SECTOR_CASE_SQL}, `Sursa`)) STORED AFTER `Sursa`",
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
def migrate_database(conn, db: str, backup_dir: str, split: bool, say) -> str:
    """Steps 1-5 on one database. Returns 'migrated' | 'skipped'. Raises on failure."""
    state = inspect(conn, db)
    if not state.exists:
        say(f"{db}: fără tabel {TABLE} — sărit.")
        return "skipped"
    if state.leftovers:
        raise MigrationError(
            f"{db}: rămășițe ale unei rulări anterioare ({', '.join(state.leftovers)}). "
            f"Verificați starea tabelului și ștergeți-le de mână înainte de a relua.")
    if state.migrated:
        say(f"{db}: Sursa este deja coloană scrisă și Sector are CASE-ul extins — sărit.")
        return "skipped"
    if not state.sursa_generated:
        raise MigrationError(
            f"{db}: Sursa nu este generată, dar Sector nu are CASE-ul extins — stare "
            f"intermediară necunoscută. Nu se continuă.")

    say(f"{db}: {state.rows} rânduri; FK pe SS = {state.ss_fk_name}, "
        f"index pe SS = {state.ss_index_name or '(niciunul)'}")

    # 1. backup
    dump_path = dump_table(db, backup_dir, say)
    restore = restore_command(db, dump_path)

    try:
        # 2. snapshot
        execute(conn, f"CREATE TABLE {q(db)}.{q(SNAPSHOT)} AS "
                      f"SELECT IDClsf, Sector, Sursa, SS FROM {q(db)}.{q(TABLE)}")
        # 3. copy the values into a written column
        execute(conn, f"ALTER TABLE {q(db)}.{q(TABLE)} ADD COLUMN {q(TEMP_COLUMN)} char(1) NULL")
        execute(conn, f"UPDATE {q(db)}.{q(TABLE)} SET {q(TEMP_COLUMN)} = `Sursa`")
        # 4. the structural change
        for stmt in alter_statements(state, split):
            say(f"  {stmt[:120]}{'…' if len(stmt) > 120 else ''}")
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
        description="Clasificatii.Sursa: din coloană generată în coloană scrisă (slice 0075-00).")
    p.add_argument("--dry-run", action="store_true",
                   help="listează bazele, numărul de rânduri și starea; nu modifică nimic")
    p.add_argument("--db", help="o singură bază (implicit: AVACONT_SURSA + toate unitățile)")
    p.add_argument("--backup-dir", default="backup",
                   help="folderul copiilor de siguranță (implicit: backup)")
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
                              "de migrat" if st.sursa_generated else "stare necunoscută")
                    extra = f"; rămășițe: {', '.join(st.leftovers)}" if st.leftovers else ""
                    say(f"  {db:<16} {st.rows:>7} rânduri  {status:<16} FK SS={st.ss_fk_name} "
                        f"index SS={st.ss_index_name}{extra}")
                return 0

            done = skipped = 0
            for db in targets:
                result = migrate_database(conn, db, args.backup_dir, args.split_alter, say)
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
