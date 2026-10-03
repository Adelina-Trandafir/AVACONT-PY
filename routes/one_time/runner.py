"""
runner.py -- one-time queries, run once on AVACONT_SURSA and on every unit database (slice 0103).

WHY. Some changes are DATA, not structure: give the existing budget rows a start date, drop an old
key, fix values. schema_sync only carries structure, so such a change used to be a hand-run script,
database by database, with nothing to say where it had been run. This tool runs ONE query text on
every database, remembers it there, and refuses to run it twice.

THE LEDGER. Every database (the template AVACONT_SURSA included) has `Interogari_Unice`:
  Hash        SHA-256 of the query's statements (comments and line ends do not count) -- the identity
  Nume        the name the operator gave it (unique): the same name with a DIFFERENT text is refused
  RulatLa, RulatDe, Randuri, Interogare
A database whose ledger already holds the hash is skipped. A NEW unit is born with the ledger rows
of AVACONT_SURSA copied in (routes/inregistrare/provizionare.py), i.e. with every query «run».
That is why the template is a target of every run, and the FIRST one: it carries the answer to
«has this been run?» for every unit that does not exist yet. If the template fails, the run stops
there and no unit is touched.

HOW A RUN GOES
  1. Statements are cut from the text (strings, backticks and comments respected). `DELIMITER` and
     `DROP DATABASE / SCHEMA` are refused.
  2. Targets: AVACONT_SURSA, then every database named in AVACONT_COMUN.CAI that exists (the same
     discovery as schema_sync). Unit databases the registry does not list are NAMED and left alone.
  3. A look at every target's ledger first. A name already used by a different text anywhere
     stops the run BEFORE anything is executed.
  4. `--view` stops here: nothing is written, not even the ledger table.
  5. `--run` executes, database by database, each in its own database (table names in the query are
     NOT qualified). The statements run in one transaction, but DDL commits implicitly in MariaDB,
     so write the query so that running it again does no harm. The ledger row is written only
     after every statement succeeded; a database that fails is reported, left unmarked, and the
     run goes on with the next unit.
  6. A target without the ledger table gets it first (`CREATE TABLE ... LIKE AVACONT_SURSA`).

Exit codes: 0 everything done or already done; 1 at least one database failed; 2 refused, nothing
executed (bad input, name clash).

No prompts: commands over SSH have no terminal. The confirmation is the operator's, in AvacontPush.

    python -m routes.one_time.runner --name 0102_buget --sql-b64 <base64> --view
    python -m routes.one_time.runner --name 0102_buget --sql-file q.sql --run --targets 000_DEMO,018_GRRS
    python -m routes.one_time.runner --status
"""

import argparse
import base64
import hashlib
import logging
import os
import re
import sys
from logging.handlers import RotatingFileHandler

import mysql.connector

from routes.schema_sync.schema_common import (
    SOURCE_DB, SchemaSyncError, connect, discover_targets, list_unit_databases, parse_targets, query,
    verify_targets)

LEDGER = "Interogari_Unice"

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_REFUSED = 2

NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,100}$")

# The query travels on a command line (base64); keep it well under what a shell accepts.
MAX_SQL_BYTES = 100_000

RAN_BY = "AvacontPush"

# The ledger as the template has it (sql/0102_01_sursa.sql). Used only to create it on
# AVACONT_SURSA when that script was not run; every other database copies the template's table.
_LEDGER_DDL = (
    "CREATE TABLE IF NOT EXISTS `{db}`.`" + LEDGER + "` ("
    " `Hash` char(64) NOT NULL COMMENT 'Slice 0103: SHA-256 of the normalized query text',"
    " `Nume` varchar(100) NOT NULL COMMENT 'The name the operator gave the query',"
    " `RulatLa` datetime NOT NULL DEFAULT current_timestamp() COMMENT 'When it was run on this database',"
    " `RulatDe` varchar(64) NULL DEFAULT NULL COMMENT 'AvacontPush, or provisioning when a new unit is born with it marked done',"
    " `Randuri` int(11) NULL DEFAULT NULL COMMENT 'Rows the statements reported as affected',"
    " `Interogare` mediumtext NULL DEFAULT NULL COMMENT 'The normalized text that ran',"
    " PRIMARY KEY (`Hash`) USING BTREE,"
    " UNIQUE INDEX `uq_interogari_unice_nume`(`Nume` ASC) USING BTREE"
    ") ENGINE = InnoDB CHARACTER SET = utf8mb3 COLLATE = utf8mb3_general_ci ROW_FORMAT = Dynamic"
)

LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "one_time.log")

_FORBIDDEN_STATEMENT = re.compile(r"^\s*DROP\s+(DATABASE|SCHEMA)\b", re.IGNORECASE)
_DELIMITER_LINE = re.compile(r"^\s*DELIMITER\b", re.IGNORECASE | re.MULTILINE)

logger = logging.getLogger("one_time")


class QueryRefused(Exception):
    """Input or state the operator has to fix; nothing was executed."""


# ---------------------------------------------------------------------------------
# Output and log
# ---------------------------------------------------------------------------------

def _setup_logging() -> None:
    if logger.handlers:
        return
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    logger.addHandler(handler)


def say(message: str = "") -> None:
    """To the operator (stdout) and to the log file."""
    print(message, flush=True)
    if message:
        logger.info(message)


# ---------------------------------------------------------------------------------
# The query text
# ---------------------------------------------------------------------------------

def split_statements(text: str) -> list:
    """The statements of `text`, without comments.

    Strings ('..', "..") and backticked names are respected, so a `;` inside them does not cut.
    `-- `, `#` and `/* */` comments are dropped; an executable `/*! ... */` is kept.
    """
    statements = []
    buffer = []
    i, n = 0, len(text)
    quote = None
    while i < n:
        c = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if quote:
            buffer.append(c)
            if c == "\\" and quote != "`" and i + 1 < n:
                buffer.append(nxt)
                i += 2
                continue
            if c == quote:
                quote = None
            i += 1
            continue
        if c in ("'", '"', "`"):
            quote = c
            buffer.append(c)
            i += 1
            continue
        if c == "-" and nxt == "-" and (i + 2 >= n or text[i + 2] in " \t\r\n"):
            end = text.find("\n", i)
            i = n if end == -1 else end
            continue
        if c == "#":
            end = text.find("\n", i)
            i = n if end == -1 else end
            continue
        if c == "/" and nxt == "*":
            end = text.find("*/", i + 2)
            if end == -1:
                raise QueryRefused("Comentariul /* nu este închis.")
            if text.startswith("/*!", i) or text.startswith("/*M!", i):
                buffer.append(text[i:end + 2])
            i = end + 2
            continue
        if c == ";":
            statement = "".join(buffer).strip()
            if statement:
                statements.append(statement)
            buffer = []
            i += 1
            continue
        buffer.append(c)
        i += 1
    if quote:
        raise QueryRefused("Un șir între ghilimele nu este închis.")
    tail = "".join(buffer).strip()
    if tail:
        statements.append(tail)
    return statements


def prepare(name: str, raw_text: str) -> tuple:
    """(statements, normalized_text, sha256). Raises QueryRefused on anything unusable."""
    name = (name or "").strip()
    if not NAME_RE.match(name):
        raise QueryRefused("Numele interogării: 3–100 de caractere, doar litere, cifre, _ . -")
    if len(raw_text.encode("utf-8")) > MAX_SQL_BYTES:
        raise QueryRefused(f"Interogarea are peste {MAX_SQL_BYTES // 1000} KB; împărțiți-o în mai multe.")
    text = raw_text.replace("﻿", "").replace("\r\n", "\n").replace("\r", "\n")
    if _DELIMITER_LINE.search(text):
        raise QueryRefused("DELIMITER nu este acceptat; scrieți doar instrucțiuni simple, separate prin ;")
    statements = split_statements(text)
    if not statements:
        raise QueryRefused("Interogarea nu conține nicio instrucțiune.")
    for statement in statements:
        if _FORBIDDEN_STATEMENT.match(statement):
            raise QueryRefused("DROP DATABASE / DROP SCHEMA nu se rulează prin acest instrument.")
    normalized = ";\n".join(statements) + ";\n"
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return statements, normalized, digest


# ---------------------------------------------------------------------------------
# Targets and ledger
# ---------------------------------------------------------------------------------

def collect_targets(conn, explicit: str = None) -> list:
    """AVACONT_SURSA first, then the units.

    `explicit` (--targets, what AvacontPush sends: the databases the operator ticked after the
    server listed the ones that really exist) names the units; a name the server does not have
    stops the run. Without it, the units of the registry (CAI) that exist on the server.
    """
    if explicit:
        units = verify_targets(conn, parse_targets(explicit), logger)
        return [SOURCE_DB] + [u for u in units if u != SOURCE_DB]
    units = discover_targets(conn, logger)
    for item in list_unit_databases(conn):
        if item["exists"] and not item["in_cai"]:
            say(f"ATENȚIE: baza {item['name']} există pe server, dar nu e în CAI — nu e atinsă.")
    return [SOURCE_DB] + [u for u in units if u != SOURCE_DB]


def _has_ledger(conn, db: str) -> bool:
    rows = query(conn,
                 "SELECT COUNT(*) AS n FROM information_schema.TABLES "
                 "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s", (db, LEDGER))
    return rows[0]["n"] > 0


def ensure_ledger(conn, db: str) -> None:
    """Creates the ledger on `db` when it is missing: the template builds its own, a unit copies
    the template's."""
    if _has_ledger(conn, db):
        return
    cursor = conn.cursor()
    try:
        if db == SOURCE_DB:
            cursor.execute(_LEDGER_DDL.format(db=SOURCE_DB))
        else:
            if not _has_ledger(conn, SOURCE_DB):
                raise SchemaSyncError(f"`{SOURCE_DB}` nu are încă `{LEDGER}`.")
            cursor.execute(f"CREATE TABLE IF NOT EXISTS `{db}`.`{LEDGER}` LIKE `{SOURCE_DB}`.`{LEDGER}`")
    finally:
        cursor.close()
    say(f"  {db}: creat `{LEDGER}`.")


def ledger_state(conn, db: str, name: str, digest: str) -> tuple:
    """(state, detail) with state DONE | PENDING | CONFLICT. Writes nothing."""
    if not _has_ledger(conn, db):
        return "PENDING", "fără tabelul registru (se creează la rulare)"
    rows = query(conn,
                 f"SELECT Hash, Nume, RulatLa FROM `{db}`.`{LEDGER}` WHERE Hash = %s OR Nume = %s",
                 (digest, name))
    for row in rows:
        if row["Hash"] == digest:
            return "DONE", f"deja rulată la {row['RulatLa']:%d.%m.%Y %H:%M}"
    if rows:
        return "CONFLICT", (f"numele «{name}» există deja cu alt text (rulat la "
                            f"{rows[0]['RulatLa']:%d.%m.%Y %H:%M})")
    return "PENDING", "de rulat"


# ---------------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------------

def _run_on(conn_admin, db: str, name: str, statements: list, normalized: str, digest: str) -> int:
    """Executes the statements in `db` and writes the ledger row. Returns the rows reported."""
    ensure_ledger(conn_admin, db)
    unit = connect(db)
    try:
        unit.autocommit = False
        cursor = unit.cursor()
        affected = 0
        try:
            for index, statement in enumerate(statements, start=1):
                try:
                    cursor.execute(statement)
                    if cursor.with_rows:
                        cursor.fetchall()
                    elif cursor.rowcount and cursor.rowcount > 0:
                        affected += cursor.rowcount
                except mysql.connector.Error as exc:
                    unit.rollback()
                    raise SchemaSyncError(
                        f"instrucțiunea {index} din {len(statements)} a eșuat: [{exc.errno}] {exc.msg}") from exc
            cursor.execute(
                f"INSERT INTO `{LEDGER}` (Hash, Nume, RulatDe, Randuri, Interogare) "
                "VALUES (%s, %s, %s, %s, %s)",
                (digest, name, RAN_BY, affected, normalized))
            unit.commit()
        finally:
            cursor.close()
        return affected
    finally:
        unit.close()


def run(name: str, raw_text: str, view_only: bool, targets: str = None) -> int:
    statements, normalized, digest = prepare(name, raw_text)
    say(f"Interogarea «{name}»: {len(statements)} instrucțiuni, SHA-256 {digest[:16]}…")

    conn = connect()
    try:
        names = collect_targets(conn, targets)
        plan = []
        for db in names:
            state, detail = ledger_state(conn, db, name, digest)
            plan.append((db, state, detail))
            say(f"  {db:<16} {state:<9} {detail}")

        conflicts = [p for p in plan if p[1] == "CONFLICT"]
        if conflicts:
            say("")
            say("REFUZAT: același nume, alt text. Nu s-a executat nimic. "
                "Folosiți alt nume pentru o interogare nouă.")
            return EXIT_REFUSED

        pending = [p[0] for p in plan if p[1] == "PENDING"]
        if view_only:
            say("")
            say(f"Nu s-a executat nimic. De rulat pe {len(pending)} din {len(plan)} baze.")
            return EXIT_OK
        if not pending:
            say("")
            say("Nimic de făcut: toate bazele au rulat deja interogarea.")
            return EXIT_OK

        say("")
        failed = []
        for db in pending:
            try:
                affected = _run_on(conn, db, name, statements, normalized, digest)
                say(f"  {db}: OK ({affected} rânduri)")
            except (SchemaSyncError, mysql.connector.Error) as exc:
                failed.append(db)
                say(f"  {db}: EȘUAT — {exc}")
                if db == SOURCE_DB:
                    say("Șablonul a eșuat: se oprește, nicio unitate nu a fost atinsă.")
                    break

        say("")
        if failed:
            say(f"Terminat cu erori pe: {', '.join(failed)}. Cele eșuate nu sunt marcate ca rulate.")
            return EXIT_FAILED
        say(f"Terminat: rulată pe {len(pending)} baze, restul o aveau deja.")
        return EXIT_OK
    finally:
        conn.close()


def status() -> int:
    """The ledger of every target, one line per query."""
    conn = connect()
    try:
        for db in collect_targets(conn):
            if not _has_ledger(conn, db):
                say(f"{db}: fără registru")
                continue
            rows = query(conn, f"SELECT Nume, RulatLa, RulatDe, Randuri FROM `{db}`.`{LEDGER}` "
                               "ORDER BY RulatLa, Nume")
            say(f"{db}: {len(rows)} interogări")
            for row in rows:
                say(f"    {row['Nume']:<40} {row['RulatLa']:%d.%m.%Y %H:%M}  {row['RulatDe'] or '-'}"
                    f"  ({row['Randuri']} rânduri)")
        return EXIT_OK
    finally:
        conn.close()


# ---------------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Interogări unice, pe toate bazele (slice 0103).")
    parser.add_argument("--name", help="numele interogării")
    parser.add_argument("--targets", help="unitățile (separate prin virgulă); AVACONT_SURSA se adaugă mereu, prima. "
                                          "Fără: toate unitățile din CAI care există")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--sql-b64", help="textul interogării, base64 (UTF-8)")
    source.add_argument("--sql-file", help="un fișier .sql de pe server")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--view", action="store_true", help="arată ce s-ar rula; nu execută nimic")
    mode.add_argument("--run", action="store_true", help="execută")
    mode.add_argument("--status", action="store_true", help="registrul fiecărei baze")
    args = parser.parse_args(argv)

    _setup_logging()
    try:
        if args.status:
            return status()
        if not args.name or not (args.sql_b64 or args.sql_file):
            raise QueryRefused("Trebuie --name și --sql-b64 (sau --sql-file).")
        if args.sql_b64:
            try:
                text = base64.b64decode(args.sql_b64, validate=True).decode("utf-8")
            except (ValueError, UnicodeDecodeError) as exc:
                raise QueryRefused(f"Textul interogării nu poate fi citit: {exc}") from exc
        else:
            with open(args.sql_file, "r", encoding="utf-8-sig") as handle:
                text = handle.read()
        return run(args.name, text, view_only=args.view, targets=args.targets)
    except QueryRefused as exc:
        say(f"REFUZAT: {exc}")
        return EXIT_REFUSED
    except SchemaSyncError as exc:
        say(f"EROARE: {exc}")
        return EXIT_FAILED
    except Exception:
        logger.exception("one_time: eroare neașteptată")
        raise


if __name__ == "__main__":
    sys.exit(main())
