"""
admin_source.py -- the verbs of KBotAdmin's page «Interogare pe SURSA» (sliceless). All of them only READ.

    source-read --sql-b64 B          what the statements of a query text touch (tables, columns, keys); nothing runs
    source-queries                   the queries that ran on AVACONT_SURSA (its Interogari_Unice ledger), with the
                                     objects each touches and the unit databases that already hold it
    source-check --name N            the objects of query N against the REAL structure of AVACONT_SURSA
    source-dump                      the structure of AVACONT_SURSA as a dump text (SHOW CREATE TABLE of every table); the program
                                     writes it over MariaDB_Schema/AVACONT_SURSA.sql so the schema pages read the new structure

Running the query and writing the units' ledger are done by routes.one_time.runner (--only-source, --mark).
The reader and the checker are routes/one_time/sql_objects.py.
"""
import base64

import mysql.connector
from utils.clock import utc_now

from routes.admin_cli.admin_common import AdminError, _iso, _q, _rows, _unit_dbs
from routes.one_time import sql_objects
from routes.one_time.runner import LEDGER, QueryRefused, prepare
from routes.schema_sync.schema_common import SOURCE_DB, connect, query

PREVIEW_NAME = "citire.fara.nume"      # prepare() wants a valid name; a read has none


def _ledger(conn, db):
    """The rows of one database's ledger; empty when it has none yet."""
    try:
        return _rows(conn, "SELECT Hash, Nume, RulatLa, RulatDe, Randuri, Interogare FROM %s.%s ORDER BY RulatLa DESC"
                     % (_q(db), _q(LEDGER)))
    except mysql.connector.Error as err:
        if getattr(err, "errno", None) == 1146:      # table missing: nothing ran there
            return []
        raise


def _statements(text):
    try:
        return prepare(PREVIEW_NAME, text)[0]
    except QueryRefused as err:
        raise AdminError(str(err))


def cmd_source_read(args):
    try:
        text = base64.b64decode(args.sql_b64, validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as err:
        raise AdminError("Textul interogării nu poate fi citit: %s" % err)
    statements = _statements(text)
    return {"statements": len(statements), "objects": sql_objects.describe(statements)}


def cmd_source_queries(args):
    conn = connect()
    try:
        units = _unit_dbs(conn)
        template = _ledger(conn, SOURCE_DB)
        done_by_db = {db: {r["Hash"] for r in _ledger(conn, db)} for db in units}
    finally:
        conn.close()
    items = []
    for row in template:
        text = row["Interogare"] or ""
        try:
            objects = sql_objects.describe(_statements(text))
        except AdminError:
            objects = []            # a text the reader refuses (older runner): shown without objects
        reached = [db for db in units if row["Hash"] in done_by_db[db]]
        missing = [db for db in units if row["Hash"] not in done_by_db[db]]
        items.append({"name": row["Nume"], "hash": row["Hash"], "ran_at": _iso(row["RulatLa"]),
                      "ran_by": row["RulatDe"] or "", "rows": row["Randuri"], "text": text,
                      "done": len(reached), "total": len(units), "missing": missing, "objects": objects})
    return {"items": items, "units": len(units)}


def cmd_source_check(args):
    conn = connect()
    try:
        rows = [r for r in _ledger(conn, SOURCE_DB) if r["Nume"] == args.name]
        if not rows:
            raise AdminError("«%s» nu a rulat pe %s." % (args.name, SOURCE_DB))
        objects = sql_objects.describe(_statements(rows[0]["Interogare"] or ""))
        checks = sql_objects.verify(query, conn, SOURCE_DB, objects)
    finally:
        conn.close()
    bad = [c for c in checks if c["state"] in ("missing", "still_there")]
    return {"name": args.name, "checks": checks, "bad": len(bad),
            "unchecked": len([c for c in checks if c["state"] == "unchecked"]),
            "data_statements": len([o for o in objects if o["kind"] == "DATA"])}


def _text(value):
    return value.decode("utf-8") if isinstance(value, (bytes, bytearray)) else value


def cmd_source_dump(args):
    """AVACONT_SURSA as dump text, in the shape the schema pages read (DROP TABLE IF EXISTS, then CREATE TABLE). Structure only:
    the rows of the old Navicat dump are not carried. Views are not dumped; their names are returned."""
    conn = connect(SOURCE_DB)
    try:
        cursor = conn.cursor()
        try:
            cursor.execute("SHOW FULL TABLES")
            entries = [(_text(row[0]), _text(row[1])) for row in cursor.fetchall()]
            tables = sorted((n for n, kind in entries if kind == "BASE TABLE"), key=str.casefold)
            views = sorted(n for n, kind in entries if kind != "BASE TABLE")
            parts = ["/*\n KBotAdmin: structure of %s (SHOW CREATE TABLE of every table), made %s UTC.\n*/\n\n"
                     "SET NAMES utf8mb4;\nSET FOREIGN_KEY_CHECKS = 0;\n"
                     % (SOURCE_DB, utc_now().strftime("%d/%m/%Y %H:%M:%S"))]
            for name in tables:
                cursor.execute("SHOW CREATE TABLE %s" % _q(name))
                ddl = _text(cursor.fetchone()[1])
                parts.append("\n-- ----------------------------\n-- Table structure for %s\n-- ----------------------------\n"
                             "DROP TABLE IF EXISTS %s;\n%s;\n" % (name, _q(name), ddl))
            parts.append("\nSET FOREIGN_KEY_CHECKS = 1;\n")
        finally:
            cursor.close()
    finally:
        conn.close()
    return {"database": SOURCE_DB, "tables": len(tables), "views": views, "text": "".join(parts)}


def register(sub):
    p = sub.add_parser("source-read")
    p.add_argument("--sql-b64", required=True)
    p.set_defaults(fn=cmd_source_read)

    sub.add_parser("source-queries").set_defaults(fn=cmd_source_queries)

    sub.add_parser("source-dump").set_defaults(fn=cmd_source_dump)

    p = sub.add_parser("source-check")
    p.add_argument("--name", required=True)
    p.set_defaults(fn=cmd_source_check)
