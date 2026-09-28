"""
idclsfacc_0080_04.py -- one-off for slice 0080-04, on AVACONT_SURSA and on every unit
database (`NNN_*`).

Operator, 25.09.2026: "it should only be left in Clasificatii - that should be the source of
truth." `IdClsfAcc` (the Access classification id) is DROPPED from the five tables that still
carry a copy of it next to `IdClsf`:

    FX_ORD_TBL, FX_DDF_REV_SA, FX_DDF_REV_SB, FX_DDF_REV_PRT, Parteneri_Coduri

On all five `IdClsf` is already `Clasificatii.IDClsf` (a real foreign key), so the Access id
stays reachable as `Clasificatii.IdClsfAcc` through it. Nothing is rewritten; the column is
only dropped. The seven tables of slice 0080-01 never kept it (see extrase_clsf_0080.py).

THE PRIOR TEST (the rule of 0080-01: if it fails, nothing is done until the operator has
looked). Phase 1 reads EVERY target database and stops the whole run, before any change
anywhere, when a row would lose information by the drop:
  * `IdClsfAcc` is set (non-zero) but `IdClsf` is empty (NULL / 0): the Access id is the only
    classification the row has;
  * `IdClsfAcc` disagrees with `Clasificatii.IdClsfAcc` of the row's `IdClsf`: the copy and
    the source of truth say different things, and someone has to say which is right;
  * `IdClsf` points at no `Clasificatii` row (the foreign keys should make this impossible).
It prints every problem with sample keys.

PHASE 2, per database, only when phase 1 is clean everywhere:
  1. mysqldump of the tables that still have the column, to <backup-dir>/<db>_0080_<stamp>.sql
     -- the dump is the only copy of the dropped values, and the way back.
  2. `ALTER TABLE ... DROP COLUMN IdClsfAcc`, one table at a time.

Re-runnable: a table without the column is done and is skipped.

Deploy the 0080-04 server code in the same maintenance window: the old code writes
`IdClsfAcc` into these tables (1054 once the column is gone), and the new code does not write
it (1364 on FX_DDF_REV_SA / _SB, where it is NOT NULL, until the column is gone). Both
migrators refuse a target that still has the column.

Usage (from the PYTHON folder, with the venv, on the server):
    python -m scripts.idclsfacc_0080_04 --dry-run        # phase 1 only; changes nothing
    python -m scripts.idclsfacc_0080_04                  # every database
    python -m scripts.idclsfacc_0080_04 --db 000_DEMO    # one database
"""

import argparse
import sys
from dataclasses import dataclass, field

import mysql.connector

from routes.schema_sync.schema_common import FORBIDDEN_TARGETS, SOURCE_DB, query
from scripts.extrase_clsf_0080 import (MigrationError, connect, dump_tables, execute,
                                       list_databases, q, restore_command)

ACC = "IdClsfAcc"

# table -> primary key, for the sample rows of a problem report
TABLES = {
    "FX_ORD_TBL": "IDORDTBLP",
    "FX_DDF_REV_SA": "IdSecA",
    "FX_DDF_REV_SB": "IdSecB",
    "FX_DDF_REV_PRT": "IDREVP",
    "Parteneri_Coduri": "IdPartenerAng",
}

SAMPLE_ROWS = 20


@dataclass
class DbState:
    db: str
    pending: list = field(default_factory=list)        # tables that still have IdClsfAcc
    has_clasificatii: bool = False
    problems: list = field(default_factory=list)       # operator-facing lines


# ---------------------------------------------------------------------------
# Phase 1 -- read only
# ---------------------------------------------------------------------------
def inspect(conn, db: str) -> DbState:
    st = DbState(db)
    rows = query(conn,
                 "SELECT TABLE_NAME AS t, COLUMN_NAME AS c FROM information_schema.COLUMNS "
                 "WHERE TABLE_SCHEMA = %s", (db,))
    columns = {}
    for r in rows:
        columns.setdefault(r["t"], set()).add(r["c"])
    st.has_clasificatii = "IdClsfAcc" in columns.get("Clasificatii", set())
    st.pending = [t for t in TABLES if ACC in columns.get(t, set())]
    return st


def check(conn, st: DbState):
    if not st.pending:
        return
    if not st.has_clasificatii:
        st.problems.append("Clasificatii (cu IdClsfAcc) lipsește — nu se poate verifica nimic.")
        return
    db = st.db
    for table in st.pending:
        key = TABLES[table]
        src = f"{q(db)}.{q(table)} T LEFT JOIN {q(db)}.`Clasificatii` C ON C.IDClsf = T.IdClsf"
        checks = (
            ("are IdClsfAcc, dar IdClsf e gol — id-ul Access ar fi singura clasificație",
             "T.IdClsfAcc IS NOT NULL AND T.IdClsfAcc <> 0 "
             "AND (T.IdClsf IS NULL OR T.IdClsf = 0)"),
            ("IdClsf nu există în Clasificatii",
             "T.IdClsf IS NOT NULL AND T.IdClsf <> 0 AND C.IDClsf IS NULL"),
            ("IdClsfAcc diferă de Clasificatii.IdClsfAcc al lui IdClsf",
             "T.IdClsfAcc IS NOT NULL AND T.IdClsfAcc <> 0 "
             "AND C.IDClsf IS NOT NULL AND C.IdClsfAcc <> T.IdClsfAcc"),
        )
        for what, where in checks:
            found = query(conn,
                          f"SELECT T.{q(key)} AS cheie, T.IdClsf AS id_clsf, "
                          f"T.IdClsfAcc AS id_acc, C.IdClsfAcc AS id_acc_nomenclator "
                          f"FROM {src} WHERE {where} ORDER BY T.{q(key)}")
            if not found:
                continue
            st.problems.append(f"{table}: {len(found)} rânduri — {what}")
            for r in found[:SAMPLE_ROWS]:
                st.problems.append(
                    f"    {key}={r['cheie']}: IdClsf={r['id_clsf']}, IdClsfAcc={r['id_acc']}, "
                    f"Clasificatii.IdClsfAcc={r['id_acc_nomenclator']}")
            if len(found) > SAMPLE_ROWS:
                st.problems.append(f"    ... și încă {len(found) - SAMPLE_ROWS}")


def report(st: DbState, say) -> bool:
    """Print phase 1 for one database. True = clean."""
    say(f"{st.db}: IdClsfAcc de șters pe {', '.join(st.pending) or 'niciun tabel'}")
    for line in st.problems:
        say("  " + line)
    return not st.problems


# ---------------------------------------------------------------------------
# Phase 2 -- one database
# ---------------------------------------------------------------------------
def migrate_database(conn, st: DbState, backup_dir: str, say) -> str:
    db = st.db
    if not st.pending:
        say(f"{db}: nimic de făcut — sărit.")
        return "skipped"

    dump_path = dump_tables(db, list(st.pending), backup_dir, say)
    restore = restore_command(db, dump_path)
    try:
        for table in st.pending:
            execute(conn, f"ALTER TABLE {q(db)}.{q(table)} DROP COLUMN {q(ACC)}")
            say(f"  {table}: IdClsfAcc șters")
    except mysql.connector.Error as exc:
        raise MigrationError(
            f"{db}: MariaDB a refuzat (errno {getattr(exc, 'errno', '?')}): {exc}\n"
            f"  Restaurare: {restore}") from exc

    say(f"{db}: gata; copia: {dump_path}")
    return "migrated"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Șterge IdClsfAcc de pe FX_ORD_TBL, FX_DDF_REV_SA/_SB/_PRT și "
                    "Parteneri_Coduri; id-ul Access rămâne doar în Clasificatii "
                    "(slice 0080-04).")
    p.add_argument("--dry-run", action="store_true",
                   help="doar verificarea (faza 1); nu modifică nimic")
    p.add_argument("--db", help="o singură bază (implicit: AVACONT_SURSA + toate unitățile)")
    p.add_argument("--backup-dir", default="backup",
                   help="folderul copiilor de siguranță (implicit: backup)")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    say = print
    try:
        conn = connect()
        try:
            if args.db:
                if args.db in FORBIDDEN_TARGETS and args.db != SOURCE_DB:
                    raise MigrationError(f"Bază interzisă: {args.db}")
                targets = [args.db]
            else:
                targets = list_databases(conn)

            say(f"Faza 1 — verificare pe {len(targets)} baze (nimic nu se modifică).")
            states, clean = [], True
            for db in targets:
                st = inspect(conn, db)
                check(conn, st)
                clean = report(st, say) and clean
                states.append(st)
            if not clean:
                say("OPRIT: faza 1 a găsit probleme. Nicio bază nu a fost modificată.")
                return 1
            if args.dry_run:
                say("Faza 1 curată. --dry-run: nu se modifică nimic.")
                return 0

            say("Faza 2 — ștergerea coloanei.")
            done = skipped = 0
            for st in states:
                if migrate_database(conn, st, args.backup_dir, say) == "migrated":
                    done += 1
                else:
                    skipped += 1
            say(f"Gata: {done} baze modificate, {skipped} sărite.")
            return 0
        finally:
            conn.close()
    except MigrationError as exc:
        print(f"EROARE: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
