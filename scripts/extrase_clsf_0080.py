"""
extrase_clsf_0080.py -- one-off for slice 0080-01. Two structural changes, on AVACONT_SURSA
and on every unit database (`NNN_*`):

  A. `FX_Extrase.DataDoc` goes from varchar to DATE.
     The text holds MIXED shapes, sometimes inside one database (operator, 24.09.2026):
       dd.MM.yyyy            -- written by Access and by the K-BOT import before 0080-01
       dd/MM/yyyy
       yyyy-mm-dd hh:mm:ss   -- the time is dropped
     Each distinct value is read by the SAME reader the migrator uses
     (`routes.migrare.parser.parse_value` with a `date` target), so an old Access file
     pushed later is read exactly like the rows already here. An empty string is NULL.

  B. `IdClsf` on the seven FX_ tables that held the ACCESS id (FX_Extrase_H,
     FX_Indicatori, FX_Istoric, FX_Plati, FX_Receptii, FX_Receptii_RHR, FX_Rezervari) is
     rewritten with the MariaDB key, `Clasificatii.IDClsf`, found by the Access id + the
     row's unit (see `utils/clsf_pair.py`). NO copy of the Access id is kept on these
     tables (operator, 24.09.2026); it stays in `Clasificatii.IdClsfAcc`. An Access id NULL
     or 0 means "no classification" and leaves `IdClsf` NULL. A converted `IdClsf` carries
     the column comment `clsf_pair.MARKER` -- that is how a re-run knows it is done.

THE PRIOR TEST (operator, 24.09.2026: "if it fails, nothing will be done to the data until
I see what is going on"). Phase 1 reads EVERY target database and stops the whole run,
before any change anywhere, when it finds:
  * a DataDoc value the reader cannot turn into a date;
  * a classification that resolves to zero `Clasificatii` rows in its unit, or to more
    than one (the known duplicates on IdClsfAcc + IdUnitate), or whose row has no unit.
It prints every problem with sample keys. Ambiguous day/month readings (`04/05/2025`) are
not problems -- they are read day first, as in the migrator -- but every one is printed.

PHASE 2, per database, only when phase 1 is clean everywhere:
  1. mysqldump of the eight tables to <backup-dir>/<db>_0080_<stamp>.sql.
  2. DDL: a temporary `IdClsfAcc_0080` next to `IdClsf` on each table that still needs
     it, and `DataDoc_0080 date` next to `DataDoc`.
  3. One transaction: copy the Access id into the temporary, rewrite `IdClsf`, fill the
     date temporary, verify, COMMIT. A failed verification rolls back; the temporaries
     stay empty and a re-run drops them.
  4. DDL: the marker comment on `IdClsf` and the temporary dropped; the text `DataDoc`
     dropped and `DataDoc_0080` renamed to `DataDoc`.
  A database interrupted between 3 and 4 has FILLED temporaries: a re-run sees that and
  only finishes step 4 (it must never read an already rewritten `IdClsf` as Access ids).

Re-runnable: a table whose `IdClsf` carries the marker and a `DataDoc` of type date are done
and are skipped. There is no automatic rollback of the DDL -- the dump of step 1 is the way
back, and the script prints the restore line.

Deploy the 0080-01 server code in the same maintenance window: the old code reads `IdClsf`
as an Access id, which it no longer is, and the new code writes the MariaDB key into a
database this script has not reached yet.

TABLES CONVERTED BY HAND (operator, 24.09.2026: "I had already changed some databases").
Those have an `IdClsfAcc` column already (FX_Plati, in some databases) -- the operator's own
mark. There `IdClsf` already holds `Clasificatii.IDClsf`, so it is NOT translated: phase 1
checks instead that every non-zero value is an existing `Clasificatii.IDClsf`, and phase 2
drops `IdClsfAcc` (no copy of the Access id is kept -- the dump of step 1 has it) and marks
`IdClsf`. The other tables of those databases are converted as usual.

Usage (from the PYTHON folder, with the venv, on the server):
    python -m scripts.extrase_clsf_0080 --dry-run        # phase 1 only; changes nothing
    python -m scripts.extrase_clsf_0080                  # every database
    python -m scripts.extrase_clsf_0080 --db 000_DEMO    # one database
"""

import argparse
import datetime
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass, field

import mysql.connector

from config import DB_CONFIG_NEW
from routes.migrare.parser import parse_value
from routes.schema_sync.schema_common import (FORBIDDEN_TARGETS, SOURCE_DB,
                                              UNIT_DB_RE, query)
from routes.schema_sync.schema_execute import find_dump_tool
from utils import clsf_pair

EXTRASE = "FX_Extrase"
DATA_DOC = "DataDoc"
TMP_ACC = "IdClsfAcc_0080"
TMP_DOC = "DataDoc_0080"
DATE_META = {"tip": "date", "nume": DATA_DOC}


class MigrationError(RuntimeError):
    """Anything that must stop the run. The message is what the operator reads."""


@dataclass
class DbState:
    db: str
    tables: set = field(default_factory=set)            # which of the eight exist
    columns: dict = field(default_factory=dict)         # table -> {column: DATA_TYPE}
    idclsf: dict = field(default_factory=dict)          # table -> IdClsf's information_schema row
    already_keys: set = field(default_factory=set)      # tables whose IdClsf is ALREADY IDClsf
    # phase 1 results
    doc_values: dict = field(default_factory=dict)      # distinct text -> date or None
    doc_bad: list = field(default_factory=list)
    doc_ambiguous: list = field(default_factory=list)
    clsf_problems: dict = field(default_factory=dict)

    def has(self, table, column):
        return column in self.columns.get(table, {})

    def pair_pending(self):
        """Pair tables that exist and whose `IdClsf` does not carry the marker yet."""
        return [t.name for t in clsf_pair.PAIR_TABLES
                if t.name in self.tables and self.has(t.name, "IdClsf")
                and clsf_pair.CONVERTED_TAG not in
                (self.idclsf.get(t.name, {}).get("comment") or "")]

    def doc_pending(self):
        return (EXTRASE in self.tables
                and self.columns[EXTRASE].get(DATA_DOC, "date") != "date")


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------
def connect():
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
        return cur.rowcount
    finally:
        cur.close()


def q(name: str) -> str:
    if "`" in name:
        raise MigrationError(f"Identificator invalid: {name!r}")
    return f"`{name}`"


def list_databases(conn) -> list:
    rows = query(conn, "SELECT SCHEMA_NAME AS name FROM information_schema.SCHEMATA "
                       "ORDER BY SCHEMA_NAME")
    names = [r["name"] for r in rows]
    if SOURCE_DB not in names:
        raise MigrationError(f"Baza șablon {SOURCE_DB} nu există pe server.")
    return [SOURCE_DB] + [n for n in names
                          if UNIT_DB_RE.match(n or "") and n not in FORBIDDEN_TARGETS]


# ---------------------------------------------------------------------------
# Phase 1 -- read only
# ---------------------------------------------------------------------------
def inspect(conn, db: str) -> DbState:
    st = DbState(db)
    wanted = set(clsf_pair.PAIR_TABLE_NAMES) | {EXTRASE, "Clasificatii"}
    rows = query(conn,
                 "SELECT TABLE_NAME AS t, COLUMN_NAME AS c, DATA_TYPE AS d, "
                 "COLUMN_TYPE AS ct, IS_NULLABLE AS n, COLUMN_DEFAULT AS dflt, "
                 "COLUMN_COMMENT AS cm "
                 "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = %s", (db,))
    for r in rows:
        if r["t"] in wanted:
            st.tables.add(r["t"])
            st.columns.setdefault(r["t"], {})[r["c"]] = (r["d"] or "").lower()
            if r["c"] == "IdClsf":
                st.idclsf[r["t"]] = {"type": r["ct"], "nullable": r["n"] == "YES",
                                     "default": r["dflt"], "comment": r["cm"] or ""}
    return st


def read_data_doc(conn, st: DbState):
    if not st.doc_pending():
        return
    rows = query(conn, f"SELECT DISTINCT {q(DATA_DOC)} AS v FROM {q(st.db)}.{q(EXTRASE)} "
                       f"WHERE {q(DATA_DOC)} IS NOT NULL")
    for r in rows:
        text = r["v"]
        if isinstance(text, (bytes, bytearray)):
            text = text.decode("utf-8", errors="replace")
        if str(text).strip() == "":
            st.doc_values[text] = None
            continue
        value, note, ambiguous = parse_value(DATE_META, text)
        if isinstance(value, datetime.date) and not isinstance(value, datetime.datetime):
            st.doc_values[text] = value
            if ambiguous:
                st.doc_ambiguous.append(f"«{text}» → {value.isoformat()}")
        else:
            st.doc_bad.append(str(text))


def check_clsf(conn, st: DbState):
    """
    The pair tables still to convert are checked on their CURRENT `IdClsf` (the Access
    id); a table interrupted after its commit has the Access id in `IdClsfAcc_0080`.
    """
    pending = st.pair_pending()
    if not pending:
        return
    if "Clasificatii" not in st.tables:
        st.clsf_problems["Clasificatii"] = {"randuri": 0, "mostre": [],
                                            "mesaj": "tabelul Clasificatii lipsește"}
        return
    cur = conn.cursor(dictionary=True)
    try:
        for name in pending:
            t = clsf_pair.pair_table(name)
            if t.via_indicator and "FX_Indicatori" not in st.tables:
                st.clsf_problems[name] = {"randuri": 0, "mostre": [],
                                          "mesaj": "FX_Indicatori lipsește, unitatea nu se află"}
                continue
            if name in st.already_keys:
                # Converted by hand: the values must BE Clasificatii keys.
                cur.execute(
                    f"SELECT T.{q(t.key)} AS cheie, T.IdClsf AS id_acc, NULL AS id_unitate, "
                    f"0 AS potriviri FROM {q(st.db)}.{q(name)} T "
                    f"LEFT JOIN {q(st.db)}.`Clasificatii` C ON C.IDClsf = T.IdClsf "
                    f"WHERE T.IdClsf IS NOT NULL AND T.IdClsf <> 0 AND C.IDClsf IS NULL")
                rows = cur.fetchall()
                if rows:
                    st.clsf_problems[name] = {
                        "randuri": len(rows), "mostre": rows[:clsf_pair.SAMPLE_ROWS],
                        "mesaj": f"dat ca deja convertit, dar {len(rows)} valori IdClsf nu "
                                 f"sunt chei din Clasificatii (ex. cheie "
                                 f"{rows[0]['cheie']}: IdClsf {rows[0]['id_acc']})"}
                continue
            acc = TMP_ACC if _temp_filled(conn, st, name) else "IdClsf"
            if acc == TMP_ACC:
                continue    # committed already; only step 4 is left to do
            cur.execute(clsf_pair.problem_sql(t, st.db, acc))
            rows = cur.fetchall()
            if rows:
                st.clsf_problems[name] = {"randuri": len(rows),
                                          "mostre": rows[:clsf_pair.SAMPLE_ROWS]}
    finally:
        cur.close()


def _temp_filled(conn, st: DbState, table: str) -> bool:
    """True when `IdClsfAcc_0080` exists AND carries values: step 3 committed there."""
    if not st.has(table, TMP_ACC):
        return False
    n = query(conn, f"SELECT COUNT(*) AS n FROM {q(st.db)}.{q(table)} "
                    f"WHERE {q(TMP_ACC)} IS NOT NULL")[0]["n"]
    return n > 0


def report(st: DbState, say) -> bool:
    """Print phase 1 for one database. True = clean."""
    pending = st.pair_pending()
    say(f"{st.db}: DataDoc {'de convertit' if st.doc_pending() else 'gata / absent'}; "
        f"IdClsf de rescris pe {', '.join(t for t in pending if t not in st.already_keys) or 'niciun tabel'}"
        + (f"; deja convertite de mână (se verifică, se șterge IdClsfAcc, se marchează): {', '.join(sorted(st.already_keys & set(pending)))}"
           if st.already_keys & set(pending) else ""))
    if st.doc_pending():
        good = sum(1 for v in st.doc_values.values() if v is not None)
        say(f"  DataDoc: {len(st.doc_values) + len(st.doc_bad)} valori distincte, "
            f"{good} citite, {len(st.doc_bad)} necitibile")
    for text in st.doc_bad[:clsf_pair.SAMPLE_ROWS]:
        say(f"    NECITIBIL: «{text}»")
    if len(st.doc_bad) > clsf_pair.SAMPLE_ROWS:
        say(f"    ... și încă {len(st.doc_bad) - clsf_pair.SAMPLE_ROWS}")
    for line in st.doc_ambiguous:
        say(f"    ambiguu, citit zi-întâi: {line}")
    for table, info in st.clsf_problems.items():
        if info.get("mesaj"):
            say(f"  {table}: {info['mesaj']}")
    rest = {k: v for k, v in st.clsf_problems.items() if not v.get("mesaj")}
    for line in clsf_pair.describe_problems(rest):
        say("  " + line)
    return not st.doc_bad and not st.clsf_problems


# ---------------------------------------------------------------------------
# Phase 2 -- one database
# ---------------------------------------------------------------------------
def _client_args() -> list:
    return [f"--host={DB_CONFIG_NEW['host']}",
            f"--port={DB_CONFIG_NEW.get('port', 3306)}",
            f"--user={DB_CONFIG_NEW['user']}",
            f"--password={DB_CONFIG_NEW['password']}"]


def dump_tables(db: str, tables: list, backup_dir: str, say) -> str:
    tool = find_dump_tool()
    if not tool:
        raise MigrationError("mysqldump / mariadb-dump nu a fost găsit. Fără copie de "
                             "siguranță nu se modifică nimic.")
    os.makedirs(backup_dir, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(backup_dir, f"{db}_0080_{stamp}.sql")
    cmd = [tool] + _client_args() + ["--single-transaction",
                                     "--default-character-set=utf8mb4", db] + tables
    shown = " ".join(shlex.quote(c) for c in cmd if not c.startswith("--password"))
    say(f"  copie: {shown} --password=*** > {out_path}")
    with open(out_path, "wb") as fh:
        proc = subprocess.run(cmd, stdout=fh, stderr=subprocess.PIPE, check=False)
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", errors="replace").strip()
        raise MigrationError(f"mysqldump a eșuat pentru {db} (cod {proc.returncode}): {err}")
    if os.path.getsize(out_path) == 0:
        raise MigrationError(f"Copia {out_path} este goală — nu se continuă.")
    return out_path


def restore_command(db: str, dump_path: str) -> str:
    args = " ".join(shlex.quote(a) for a in _client_args() if not a.startswith("--password"))
    return f"mysql {args} --password=*** {shlex.quote(db)} < {shlex.quote(dump_path)}"


def migrate_database(conn, st: DbState, backup_dir: str, say) -> str:
    db = st.db
    pending = st.pair_pending()
    doc = st.doc_pending()
    if not pending and not doc:
        say(f"{db}: nimic de făcut — sărit.")
        return "skipped"

    to_dump = [t for t in pending] + ([EXTRASE] if doc else [])
    dump_path = dump_tables(db, to_dump, backup_dir, say)
    restore = restore_command(db, dump_path)

    # Tables converted by hand are only marked in step 4.
    translate = [t for t in pending if t not in st.already_keys]
    try:
        # Which tables already committed step 3 (interrupted before step 4).
        filled = {t for t in translate if _temp_filled(conn, st, t)}
        doc_filled = doc and st.has(EXTRASE, TMP_DOC) and query(
            conn, f"SELECT COUNT(*) AS n FROM {q(db)}.{q(EXTRASE)} "
                  f"WHERE {q(TMP_DOC)} IS NOT NULL")[0]["n"] > 0

        # 2. temporaries (an EMPTY leftover is dropped and made again)
        for t in translate:
            if t in filled:
                continue
            if st.has(t, TMP_ACC):
                execute(conn, f"ALTER TABLE {q(db)}.{q(t)} DROP COLUMN {q(TMP_ACC)}")
            execute(conn, f"ALTER TABLE {q(db)}.{q(t)} ADD COLUMN {q(TMP_ACC)} "
                          f"int(11) NULL DEFAULT NULL AFTER `IdClsf`")
        if doc and not doc_filled:
            if st.has(EXTRASE, TMP_DOC):
                execute(conn, f"ALTER TABLE {q(db)}.{q(EXTRASE)} DROP COLUMN {q(TMP_DOC)}")
            execute(conn, f"ALTER TABLE {q(db)}.{q(EXTRASE)} ADD COLUMN {q(TMP_DOC)} "
                          f"date NULL DEFAULT NULL AFTER {q(DATA_DOC)}")

        # 3. data, in one transaction
        todo = [t for t in translate if t not in filled]
        if todo or (doc and not doc_filled):
            conn.autocommit = False
            try:
                cur = conn.cursor()
                for t in todo:
                    cur.execute(f"UPDATE {q(db)}.{q(t)} SET {q(TMP_ACC)} = IdClsf, IdClsf = NULL")
                    cur.execute(clsf_pair.fill_sql(clsf_pair.pair_table(t), db, TMP_ACC))
                    say(f"  {t}: IdClsf rescris pe {cur.rowcount} rânduri")
                if doc and not doc_filled:
                    n = 0
                    for text, value in st.doc_values.items():
                        if value is None:
                            continue
                        cur.execute(f"UPDATE {q(db)}.{q(EXTRASE)} SET {q(TMP_DOC)} = %s "
                                    f"WHERE {q(DATA_DOC)} = %s", (value, text))
                        n += cur.rowcount
                    say(f"  {EXTRASE}: DataDoc convertit pe {n} rânduri")
                cur.close()
                _verify(conn, st, todo, doc and not doc_filled)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.autocommit = True

        # 4. mark IdClsf as converted and drop the temporary (no Access id is kept)
        for t in pending:
            drop = (f", DROP COLUMN {q(TMP_ACC)}" if t in translate
                    else ", DROP COLUMN `IdClsfAcc`")
            execute(conn, f"ALTER TABLE {q(db)}.{q(t)} "
                          f"MODIFY COLUMN `IdClsf` {_idclsf_definition(st, t)}{drop}")
        if doc:
            execute(conn, f"ALTER TABLE {q(db)}.{q(EXTRASE)} DROP COLUMN {q(DATA_DOC)}, "
                          f"CHANGE COLUMN {q(TMP_DOC)} {q(DATA_DOC)} date NULL DEFAULT NULL "
                          f"AFTER `DataBanca`")
    except mysql.connector.Error as exc:
        raise MigrationError(
            f"{db}: MariaDB a refuzat (errno {getattr(exc, 'errno', '?')}): {exc}\n"
            f"  Restaurare: {restore}") from exc
    except MigrationError as exc:
        raise MigrationError(f"{exc}\n  Restaurare: {restore}") from exc

    say(f"{db}: gata; copia: {dump_path}")
    return "migrated"


def _idclsf_definition(st: DbState, table: str) -> str:
    """`IdClsf`'s own definition, unchanged, with the marker comment added."""
    info = st.idclsf[table]
    col_type = info["type"] or "int(11)"
    null = "NULL" if info["nullable"] else "NOT NULL"
    default = info["default"]
    if default is None:
        dflt = "DEFAULT NULL" if info["nullable"] else ""
    else:
        dflt = f"DEFAULT {default}"
    return f"{col_type} {null} {dflt} COMMENT '{clsf_pair.MARKER}'".replace("  ", " ")


def _verify(conn, st: DbState, tables: list, doc: bool):
    """Inside the open transaction. Raises -> the caller rolls back."""
    db = st.db
    cur = conn.cursor()
    try:
        for t in tables:
            cur.execute(f"SELECT COUNT(*) FROM {q(db)}.{q(t)} "
                        f"WHERE {q(TMP_ACC)} IS NOT NULL AND {q(TMP_ACC)} <> 0 "
                        f"AND IdClsf IS NULL")
            (lost,) = cur.fetchone()
            if lost:
                raise MigrationError(
                    f"{db}.{t}: {lost} rânduri cu clasificație Access au rămas fără IdClsf "
                    f"după rescriere. Nimic nu s-a păstrat (rollback).")
        if doc:
            cur.execute(f"SELECT COUNT(*) FROM {q(db)}.{q(EXTRASE)} "
                        f"WHERE TRIM({q(DATA_DOC)}) <> '' AND {q(TMP_DOC)} IS NULL")
            (lost,) = cur.fetchone()
            if lost:
                raise MigrationError(
                    f"{db}.{EXTRASE}: {lost} valori DataDoc au rămas neconvertite. "
                    f"Nimic nu s-a păstrat (rollback).")
    finally:
        cur.close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="FX_Extrase.DataDoc → date și IdClsf = Clasificatii.IDClsf pe cele "
                    "șapte tabele FX_ (slice 0080-01).")
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
                # Converted by hand: the operator added IdClsfAcc himself.
                st.already_keys = {t for t in st.pair_pending() if st.has(t, "IdClsfAcc")}
                read_data_doc(conn, st)
                check_clsf(conn, st)
                clean = report(st, say) and clean
                states.append(st)
            if not clean:
                say("OPRIT: faza 1 a găsit probleme. Nicio bază nu a fost modificată.")
                return 1
            if args.dry_run:
                say("Faza 1 curată. --dry-run: nu se modifică nimic.")
                return 0

            say("Faza 2 — conversia.")
            done = skipped = 0
            for st in states:
                if migrate_database(conn, st, args.backup_dir, say) == "migrated":
                    done += 1
                else:
                    skipped += 1
            say(f"Gata: {done} baze convertite, {skipped} sărite.")
            return 0
        finally:
            conn.close()
    except MigrationError as exc:
        print(f"EROARE: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
