"""
admin_dbview.py -- the verbs of KBotAdmin's page «Tabele» (sliceless). All of them only READ.

    db-tables --db D                        the tables and views of one unit database (name, engine, estimated rows, sizes)
    db-table  --db D --table T [--limit N]  one table: its columns, keys, indexes, what refers to it, and the first page of rows
    db-rows   --db D --table T --offset O --limit N     a further page of rows
    db-cell   --db D --table T --column C (--key-json J | --offset O)   the WHOLE content of one cell (text or blob)

A cell in a page is shown short on purpose (a page travels over SSH as one JSON line):
    null            -> null
    blob column     -> {"b": length}                       (the bytes never leave the server in a page)
    long text       -> {"t": first TEXT_PREVIEW chars, "more": true}   only when the text is longer than that
    anything else   -> its value (dates and decimals as strings, so nothing is rounded on the way)
db-cell returns the full content of one cell (capped, see CELL_TEXT_CAP / CELL_BLOB_CAP).

Only the unit databases are reachable (admin_common._resolve_db); the table and the column are checked against
information_schema before they reach SQL, and quoted.
"""
import base64
import json
from datetime import date, datetime, timedelta
from decimal import Decimal

from routes.admin_cli.admin_common import AdminError, _iso, _q, _resolve_db, _rows
from routes.schema_sync.schema_common import connect

MAX_PAGE = 5000
DEFAULT_PAGE = 1000
TEXT_PREVIEW = 32000          # characters of a long text that travel inside a page
CELL_TEXT_CAP = 4000000       # characters of one text cell db-cell returns
CELL_BLOB_CAP = 8 * 1024 * 1024   # bytes of one blob cell db-cell returns

BLOB_TYPES = {"tinyblob", "blob", "mediumblob", "longblob", "binary", "varbinary"}
LONG_TEXT_TYPES = {"text", "mediumtext", "longtext"}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _s(value):
    """information_schema may hand text back as bytes: always a str (or None)."""
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", "replace")
    return str(value)


def _i(value):
    return None if value is None else int(value)


def _one_db(conn, wanted):
    dbs = _resolve_db(conn, wanted)
    if (wanted or "").upper() == "ALL" or len(dbs) != 1:
        raise AdminError("Alegeți o singură bază.")
    return dbs[0]


def _tables(conn, db):
    return _rows(conn,
                 "SELECT TABLE_NAME AS name, TABLE_TYPE AS kind, ENGINE AS engine, TABLE_ROWS AS rows_est, "
                 "DATA_LENGTH AS data_bytes, INDEX_LENGTH AS index_bytes, AUTO_INCREMENT AS auto_increment, "
                 "CREATE_TIME AS created, UPDATE_TIME AS updated, TABLE_COLLATION AS table_collation, "
                 "TABLE_COMMENT AS comment FROM information_schema.TABLES WHERE TABLE_SCHEMA = %s "
                 "ORDER BY TABLE_NAME", (db,))


def _table_row(conn, db, table):
    for row in _tables(conn, db):
        if _s(row["name"]) == table:
            return row
    raise AdminError("Tabelul «%s» nu există în baza «%s»." % (table, db))


def _columns(conn, db, table):
    out = []
    for r in _rows(conn,
                   "SELECT COLUMN_NAME AS name, ORDINAL_POSITION AS pos, COLUMN_TYPE AS column_type, DATA_TYPE AS data_type, "
                   "CHARACTER_MAXIMUM_LENGTH AS char_len, NUMERIC_PRECISION AS num_prec, NUMERIC_SCALE AS num_scale, "
                   "DATETIME_PRECISION AS dt_prec, IS_NULLABLE AS nullable, COLUMN_DEFAULT AS default_value, EXTRA AS extra, "
                   "COLUMN_KEY AS col_key, COLLATION_NAME AS collation, COLUMN_COMMENT AS comment "
                   "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s ORDER BY ORDINAL_POSITION",
                   (db, table)):
        out.append({
            "name": _s(r["name"]), "pos": _i(r["pos"]), "column_type": _s(r["column_type"]),
            "data_type": (_s(r["data_type"]) or "").lower(), "char_len": _i(r["char_len"]),
            "num_prec": _i(r["num_prec"]), "num_scale": _i(r["num_scale"]), "dt_prec": _i(r["dt_prec"]),
            "nullable": (_s(r["nullable"]) or "").upper() == "YES", "default_value": _s(r["default_value"]),
            "extra": _s(r["extra"]) or "", "col_key": _s(r["col_key"]) or "", "collation": _s(r["collation"]),
            "comment": _s(r["comment"]) or "",
        })
    return out


def _primary_key(conn, db, table):
    return [_s(r["col"]) for r in _rows(
        conn,
        "SELECT k.COLUMN_NAME AS col FROM information_schema.TABLE_CONSTRAINTS c "
        "JOIN information_schema.KEY_COLUMN_USAGE k ON k.CONSTRAINT_SCHEMA = c.CONSTRAINT_SCHEMA "
        "AND k.CONSTRAINT_NAME = c.CONSTRAINT_NAME AND k.TABLE_NAME = c.TABLE_NAME "
        "WHERE c.TABLE_SCHEMA = %s AND c.TABLE_NAME = %s AND c.CONSTRAINT_TYPE = 'PRIMARY KEY' "
        "ORDER BY k.ORDINAL_POSITION", (db, table))]


def _foreign_keys(conn, db, table):
    """The keys this table points out with: one entry per constraint, columns in key order."""
    rules = {_s(r["name"]): r for r in _rows(
        conn,
        "SELECT CONSTRAINT_NAME AS name, UPDATE_RULE AS upd, DELETE_RULE AS del FROM information_schema.REFERENTIAL_CONSTRAINTS "
        "WHERE CONSTRAINT_SCHEMA = %s AND TABLE_NAME = %s", (db, table))}
    out = {}
    for r in _rows(
            conn,
            "SELECT CONSTRAINT_NAME AS name, COLUMN_NAME AS col, REFERENCED_TABLE_SCHEMA AS ref_db, REFERENCED_TABLE_NAME AS ref_table, "
            "REFERENCED_COLUMN_NAME AS ref_col FROM information_schema.KEY_COLUMN_USAGE "
            "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s AND REFERENCED_TABLE_NAME IS NOT NULL "
            "ORDER BY CONSTRAINT_NAME, ORDINAL_POSITION", (db, table)):
        name = _s(r["name"])
        fk = out.setdefault(name, {"name": name, "columns": [], "ref_db": _s(r["ref_db"]), "ref_table": _s(r["ref_table"]),
                                   "ref_columns": [], "on_update": _s(rules.get(name, {}).get("upd")),
                                   "on_delete": _s(rules.get(name, {}).get("del"))})
        fk["columns"].append(_s(r["col"]))
        fk["ref_columns"].append(_s(r["ref_col"]))
    return list(out.values())


def _referenced_by(conn, db, table):
    """The keys of OTHER tables that point at this one."""
    out = {}
    for r in _rows(
            conn,
            "SELECT CONSTRAINT_SCHEMA AS from_db, CONSTRAINT_NAME AS name, TABLE_NAME AS from_table, COLUMN_NAME AS col, "
            "REFERENCED_COLUMN_NAME AS ref_col FROM information_schema.KEY_COLUMN_USAGE "
            "WHERE REFERENCED_TABLE_SCHEMA = %s AND REFERENCED_TABLE_NAME = %s "
            "ORDER BY TABLE_NAME, CONSTRAINT_NAME, ORDINAL_POSITION", (db, table)):
        key = (_s(r["from_db"]), _s(r["from_table"]), _s(r["name"]))
        ref = out.setdefault(key, {"name": key[2], "from_db": key[0], "table": key[1], "columns": [], "ref_columns": []})
        ref["columns"].append(_s(r["col"]))
        ref["ref_columns"].append(_s(r["ref_col"]))
    return list(out.values())


def _indexes(conn, db, table):
    out = {}
    for r in _rows(
            conn,
            "SELECT INDEX_NAME AS name, NON_UNIQUE AS non_unique, INDEX_TYPE AS index_type, COLUMN_NAME AS col "
            "FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s "
            "ORDER BY INDEX_NAME, SEQ_IN_INDEX", (db, table)):
        name = _s(r["name"])
        idx = out.setdefault(name, {"name": name, "unique": int(r["non_unique"]) == 0, "type": _s(r["index_type"]), "columns": []})
        idx["columns"].append(_s(r["col"]))
    return list(out.values())


# ---------------------------------------------------------------------------
# Rows
# ---------------------------------------------------------------------------
def _select_list(columns):
    """Blobs travel as their length, long texts as a preview: a page never carries bulky cells."""
    parts = []
    for c in columns:
        q = _q(c["name"])
        if c["data_type"] in BLOB_TYPES:
            parts.append("LENGTH(%s) AS %s" % (q, q))
        elif c["data_type"] in LONG_TEXT_TYPES:
            parts.append("LEFT(%s, %d) AS %s" % (q, TEXT_PREVIEW + 1, q))
        else:
            parts.append(q)
    return ", ".join(parts)


def _cell(value, data_type):
    if value is None:
        return None
    if data_type in BLOB_TYPES:
        return {"b": int(value)}
    if isinstance(value, (bytes, bytearray)):
        return {"b": len(value)}
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S") + (".%06d" % value.microsecond if value.microsecond else "")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, timedelta):
        total = int(value.total_seconds())
        sign = "-" if total < 0 else ""
        total = abs(total)
        return "%s%02d:%02d:%02d" % (sign, total // 3600, (total % 3600) // 60, total % 60)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, str) and data_type in LONG_TEXT_TYPES and len(value) > TEXT_PREVIEW:
        return {"t": value[:TEXT_PREVIEW], "more": True}
    return value


def _order_by(pk):
    return (" ORDER BY " + ", ".join(_q(c) for c in pk)) if pk else ""


def _page(conn, db, table, columns, pk, offset, limit):
    sql = "SELECT %s FROM %s.%s%s LIMIT %d OFFSET %d" % (
        _select_list(columns), _q(db), _q(table), _order_by(pk), limit + 1, offset)
    cur = conn.cursor()
    try:
        cur.execute(sql)
        fetched = cur.fetchall()
    finally:
        cur.close()
    has_more = len(fetched) > limit
    types = [c["data_type"] for c in columns]
    rows = [[_cell(v, types[i]) for i, v in enumerate(row)] for row in fetched[:limit]]
    return {"offset": offset, "limit": limit, "rows": rows, "has_more": has_more}


def _check_limit(limit):
    if limit < 1 or limit > MAX_PAGE:
        raise AdminError("Numărul de rânduri cerut trebuie să fie între 1 și %d." % MAX_PAGE)
    return limit


# ---------------------------------------------------------------------------
# Verbs
# ---------------------------------------------------------------------------
def cmd_db_tables(args):
    conn = connect()
    try:
        db = _one_db(conn, args.db)
        counts = {}
        for r in _rows(conn, "SELECT TABLE_NAME AS name, COUNT(*) AS n FROM information_schema.COLUMNS "
                             "WHERE TABLE_SCHEMA = %s GROUP BY TABLE_NAME", (db,)):
            counts[_s(r["name"])] = int(r["n"])
        tables = []
        for r in _tables(conn, db):
            name = _s(r["name"])
            tables.append({"name": name, "kind": _s(r["kind"]), "engine": _s(r["engine"]), "rows_est": _i(r["rows_est"]) or 0,
                           "data_bytes": _i(r["data_bytes"]) or 0, "index_bytes": _i(r["index_bytes"]) or 0,
                           "columns_n": counts.get(name, 0), "comment": _s(r["comment"]) or ""})
        return {"db": db, "tables": tables}
    finally:
        if conn.is_connected():
            conn.close()


def cmd_db_table(args):
    limit = _check_limit(args.limit)
    conn = connect()
    try:
        db = _one_db(conn, args.db)
        row = _table_row(conn, db, args.table)
        table = _s(row["name"])
        columns = _columns(conn, db, table)
        pk = _primary_key(conn, db, table)
        info = {"name": table, "kind": _s(row["kind"]), "engine": _s(row["engine"]), "rows_est": _i(row["rows_est"]) or 0,
                "data_bytes": _i(row["data_bytes"]) or 0, "index_bytes": _i(row["index_bytes"]) or 0,
                "auto_increment": _i(row["auto_increment"]), "created": _iso(row["created"]), "updated": _iso(row["updated"]),
                "collation": _s(row["table_collation"]), "comment": _s(row["comment"]) or ""}
        return {"db": db, "info": info, "columns": columns, "primary_key": pk,
                "foreign_keys": _foreign_keys(conn, db, table), "referenced_by": _referenced_by(conn, db, table),
                "indexes": _indexes(conn, db, table), "page": _page(conn, db, table, columns, pk, 0, limit)}
    finally:
        if conn.is_connected():
            conn.close()


def cmd_db_rows(args):
    limit = _check_limit(args.limit)
    if args.offset < 0:
        raise AdminError("Poziția cerută nu e validă.")
    conn = connect()
    try:
        db = _one_db(conn, args.db)
        table = _s(_table_row(conn, db, args.table)["name"])
        return {"page": _page(conn, db, table, _columns(conn, db, table), _primary_key(conn, db, table), args.offset, limit)}
    finally:
        if conn.is_connected():
            conn.close()


def cmd_db_cell(args):
    conn = connect()
    try:
        db = _one_db(conn, args.db)
        table = _s(_table_row(conn, db, args.table)["name"])
        columns = _columns(conn, db, table)
        wanted = [c for c in columns if c["name"] == args.column]
        if not wanted:
            raise AdminError("Coloana «%s» nu există în tabelul «%s»." % (args.column, table))
        column = wanted[0]
        pk = _primary_key(conn, db, table)
        q = _q(column["name"])
        is_blob = column["data_type"] in BLOB_TYPES
        if is_blob:
            select = "SUBSTRING(%s, 1, %d), LENGTH(%s)" % (q, CELL_BLOB_CAP, q)
        else:
            select = "LEFT(%s, %d), CHAR_LENGTH(%s)" % (q, CELL_TEXT_CAP, q)
        params = []
        if args.key_json:
            try:
                key = json.loads(args.key_json)
            except ValueError as err:
                raise AdminError("Cheia rândului nu poate fi citită: %s" % err)
            if not pk or not isinstance(key, dict) or set(key) != set(pk):
                raise AdminError("Cheia rândului nu se potrivește cu cheia primară a tabelului.")
            where = " WHERE " + " AND ".join("%s = %%s" % _q(c) for c in pk)
            params = [key[c] for c in pk]
            tail = " LIMIT 1"
        elif args.offset is not None and args.offset >= 0:
            where = ""
            tail = _order_by(pk) + " LIMIT 1 OFFSET %d" % args.offset
        else:
            raise AdminError("Lipsește cheia sau poziția rândului.")
        cur = conn.cursor()
        try:
            cur.execute("SELECT %s FROM %s.%s%s%s" % (select, _q(db), _q(table), where, tail), params)
            found = cur.fetchone()
        finally:
            cur.close()
        if found is None:
            raise AdminError("Rândul nu mai există (tabelul s-a schimbat între timp). Reîncărcați tabelul.")
        content, length = found
        if content is None:
            return {"kind": "null", "length": 0, "truncated": False}
        length = int(length)
        if is_blob:
            raw = bytes(content)
            return {"kind": "blob", "length": length, "truncated": length > len(raw), "b64": base64.b64encode(raw).decode("ascii")}
        text = _s(content)
        return {"kind": "text", "length": length, "truncated": length > len(text), "text": text}
    finally:
        if conn.is_connected():
            conn.close()


def register(sub):
    p = sub.add_parser("db-tables")
    p.add_argument("--db", required=True)
    p.set_defaults(fn=cmd_db_tables)

    p = sub.add_parser("db-table")
    p.add_argument("--db", required=True)
    p.add_argument("--table", required=True)
    p.add_argument("--limit", type=int, default=DEFAULT_PAGE)
    p.set_defaults(fn=cmd_db_table)

    p = sub.add_parser("db-rows")
    p.add_argument("--db", required=True)
    p.add_argument("--table", required=True)
    p.add_argument("--offset", type=int, default=0)
    p.add_argument("--limit", type=int, default=DEFAULT_PAGE)
    p.set_defaults(fn=cmd_db_rows)

    p = sub.add_parser("db-cell")
    p.add_argument("--db", required=True)
    p.add_argument("--table", required=True)
    p.add_argument("--column", required=True)
    p.add_argument("--key-json", default="")
    p.add_argument("--offset", type=int, default=None)
    p.set_defaults(fn=cmd_db_cell)
