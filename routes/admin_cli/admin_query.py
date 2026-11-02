"""
admin_query.py -- the verb of KBotAdmin's tree group «_Interogari» (page «Tabele», sliceless). It only READS.

    query-run --db D --sql-b64 B [--params-b64 P] [--limit N]
                                            run one query text, written by the owner in KBotAdmin's own catalog, on database D

The query texts live in KBotAdmin (KBotAdmin/Interogari/*.sql), not on the server and not in the databases: the text travels
here, is checked, runs once, and nothing is stored. A parameter is written :name in the text; its value comes from
--params-b64 (base64 of a JSON object {name: value}, value = text, number, true/false or null).

What is checked before anything runs:
    - one statement only, and it starts with SELECT / WITH / SHOW / DESCRIBE / EXPLAIN;
    - no INTO OUTFILE / DUMPFILE, no locking read (FOR UPDATE, LOCK IN SHARE MODE), no executable comment (/*! ... */);
    - the connection is opened READ ONLY (START TRANSACTION READ ONLY) and a statement may take 60 seconds at most.
Every :name is replaced by a quoted literal made HERE (text escaped, numbers checked); the text then reaches the server with no
parameter list, so a literal % in a LIKE pattern passes untouched (see the note on mysql.connector and % in the project memory).

The answer has the shape of db-table's, without the keys: {"db", "columns": [...], "page": {...}, "elapsed_ms"} -- KBotAdmin shows
it in the same grid. A cell is null, a number, a string (dates and decimals as strings), or {"b": n} for a blob. A text longer
than TEXT_CAP characters is cut and ends with « ...».
"""
import base64
import json
import math
import re
import time
from datetime import date, datetime, timedelta
from decimal import Decimal

from routes.admin_cli.admin_common import AdminError, _resolve_db
from routes.schema_sync.schema_common import connect

MAX_LIMIT = 5000
DEFAULT_LIMIT = 2000
TEXT_CAP = 32000
STATEMENT_SECONDS = 60

_RX_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_RX_START = re.compile(r"^\s*(SELECT|WITH|SHOW|DESCRIBE|DESC|EXPLAIN)\b", re.IGNORECASE)
_RX_FORBIDDEN = re.compile(r"\bINTO\s+(OUTFILE|DUMPFILE)\b|\bFOR\s+UPDATE\b|\bLOCK\s+IN\s+SHARE\s+MODE\b", re.IGNORECASE)

# mysql.connector FieldType names -> the data_type names KBotAdmin's grid knows (it decides the format and the alignment by them)
_TYPE_BY_FIELD = {
    "TINY": "tinyint", "SHORT": "smallint", "INT24": "mediumint", "LONG": "int", "LONGLONG": "bigint", "YEAR": "year",
    "DECIMAL": "decimal", "NEWDECIMAL": "decimal", "FLOAT": "float", "DOUBLE": "double",
    "DATE": "date", "NEWDATE": "date", "DATETIME": "datetime", "TIMESTAMP": "timestamp", "TIME": "time",
}

_TEXT_FIELDS = {"VAR_STRING", "STRING", "VARCHAR", "ENUM", "SET", "JSON"}


def _b64_text(value, what):
    try:
        return base64.b64decode(value.encode("ascii"), validate=True).decode("utf-8")
    except (ValueError, UnicodeError):
        raise AdminError("%s nu poate fi citit (base64 / UTF-8 invalid)." % what)


def _scan(sql, literals):
    """
    One pass over the text. Returns (text to run, text for the checks): comments are gone from both, quoted parts stay in the first
    and are blanked in the second, and every :name outside quotes becomes the quoted literal literals[name] in the first.
    """
    run, code, used = [], [], []
    i, n = 0, len(sql)
    while i < n:
        c = sql[i]
        if c in "'\"`":
            j = i + 1
            while j < n:
                if sql[j] == "\\" and c != "`":
                    j += 2
                    continue
                if sql[j] == c:
                    if j + 1 < n and sql[j + 1] == c:
                        j += 2
                        continue
                    break
                j += 1
            if j >= n:
                raise AdminError("Textul interogarii are un ghilimel neinchis.")
            run.append(sql[i:j + 1])
            code.append(" ")
            i = j + 1
        elif sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            if end < 0:
                raise AdminError("Textul interogarii are un comentariu /* ... */ neinchis.")
            if sql.startswith("/*!", i) or sql.startswith("/*M!", i):
                raise AdminError("Comentariile executabile (/*! ... */) nu sunt permise.")
            run.append(" ")
            code.append(" ")
            i = end + 2
        elif c == "#" or (sql.startswith("--", i) and (i + 2 >= n or sql[i + 2] in " \t\r\n")):
            end = sql.find("\n", i)
            i = n if end < 0 else end
            run.append(" ")
            code.append(" ")
        elif c == ":" and i + 1 < n and (sql[i + 1].isalpha() or sql[i + 1] == "_"):
            name = _RX_NAME.match(sql, i + 1).group(0)
            if name not in literals:
                raise AdminError("Interogarea cere parametrul «%s», care nu a fost dat." % name)
            if name not in used:
                used.append(name)
            run.append(literals[name])
            code.append(" ")
            i += 1 + len(name)
        else:
            run.append(c)
            code.append(c)
            i += 1
    return "".join(run).strip(), "".join(code).strip(), used


def _literal(value, name):
    """A value of the parameters object as a SQL literal. Only plain values are accepted; the text is escaped here."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            raise AdminError("Parametrul «%s» nu este un numar valid." % name)
        return repr(value)
    if isinstance(value, str):
        out = value.replace("\\", "\\\\").replace("'", "\\'").replace("\0", "\\0").replace("\n", "\\n").replace("\r", "\\r").replace("\x1a", "\\Z")
        return "'" + out + "'"
    raise AdminError("Parametrul «%s» are o valoare neacceptata (doar text, numar, da/nu sau gol)." % name)


def _load_params(params_b64):
    if not params_b64:
        return {}
    try:
        params = json.loads(_b64_text(params_b64, "Parametrii"))
    except ValueError as err:
        raise AdminError("Parametrii nu pot fi cititi: %s" % err)
    if not isinstance(params, dict):
        raise AdminError("Parametrii trebuie sa fie un obiect «nume: valoare».")
    return params


def _as_text(fetched, description):
    """information_schema (and some SHOW) hand text columns back as bytes: a column of a text type whose bytes are valid UTF-8 is text."""
    from mysql.connector import FieldType
    textual = {i for i, d in enumerate(description) if FieldType.get_info(d[1]) in _TEXT_FIELDS}
    if not textual:
        return fetched
    out = []
    for row in fetched:
        row = list(row)
        for i in textual:
            if isinstance(row[i], (bytes, bytearray)):
                try:
                    row[i] = bytes(row[i]).decode("utf-8")
                except UnicodeDecodeError:
                    pass        # real binary content: stays bytes, shown as a blob
        out.append(row)
    return out


def _data_type(field_name, values):
    """The data_type name of a result column: by the type code the server sent, a text/blob split by what the cells really are."""
    kind = _TYPE_BY_FIELD.get(field_name)
    if kind:
        return kind
    if any(isinstance(v, (bytes, bytearray)) for v in values):
        return "blob"
    return "varchar"


def _cell(value, data_type):
    if value is None:
        return None
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
    if isinstance(value, str) and len(value) > TEXT_CAP:
        return value[:TEXT_CAP] + " ..."
    return value


def _columns(description, fetched):
    """The columns of the answer, in the shape of db-table's, with the facts the grid needs to format them."""
    from mysql.connector import FieldType
    out, seen = [], {}
    for index, desc in enumerate(description):
        values = [row[index] for row in fetched]
        field_name = FieldType.get_info(desc[1])
        data_type = _data_type(field_name, values)
        name = desc[0] if isinstance(desc[0], str) else desc[0].decode("utf-8", "replace")
        seen[name] = seen.get(name, 0) + 1
        if seen[name] > 1:      # two columns of the same name cannot both be a grid column
            name = "%s_%d" % (name, seen[name])
        column = {"name": name, "pos": index + 1, "column_type": data_type, "data_type": data_type, "nullable": True}
        if data_type == "decimal":
            column["num_prec"] = 18
            column["num_scale"] = max([0] + [max(0, -Decimal(v).as_tuple().exponent) for v in values if isinstance(v, Decimal)])
        elif data_type in ("datetime", "timestamp"):
            column["dt_prec"] = 6 if any(isinstance(v, datetime) and v.microsecond for v in values) else 0
        elif data_type == "varchar":
            column["char_len"] = max([1] + [len(v) for v in values if isinstance(v, str)])
        out.append(column)
    return out


def cmd_query_run(args):
    sql = _b64_text(args.sql_b64, "Textul interogarii")
    limit = args.limit
    if limit < 1 or limit > MAX_LIMIT:
        raise AdminError("Numarul de randuri cerut trebuie sa fie intre 1 si %d." % MAX_LIMIT)
    params = _load_params(args.params_b64)
    literals = {name: _literal(value, name) for name, value in params.items()}
    text, code, _used = _scan(sql, literals)
    code = code.rstrip("; \t\r\n")
    if not code:
        raise AdminError("Textul interogarii este gol.")
    if ";" in code:
        raise AdminError("Interogarea trebuie sa fie o singura instructiune (am gasit ';' in mijloc).")
    if not _RX_START.match(code):
        raise AdminError("Se accepta doar interogari care incep cu SELECT, WITH, SHOW, DESCRIBE sau EXPLAIN.")
    found = _RX_FORBIDDEN.search(code)
    if found:
        raise AdminError("Interogarea nu poate contine «%s»." % found.group(0).upper())
    text = text.rstrip("; \t\r\n")

    conn = connect()
    try:
        dbs = _resolve_db(conn, args.db)
        if (args.db or "").upper() == "ALL" or len(dbs) != 1:
            raise AdminError("Alegeti o singura baza.")
        conn.database = dbs[0]
        cur = conn.cursor(buffered=True)   # sql_select_limit below keeps what is buffered to one page
        try:
            # The backslash escapes of _literal assume this mode is off; a query that sets nothing else keeps the server's own.
            cur.execute("SET SESSION sql_mode = REPLACE(@@sql_mode, 'NO_BACKSLASH_ESCAPES', '')")
            cur.execute("SET SESSION max_statement_time = %d" % STATEMENT_SECONDS)
            cur.execute("SET SESSION sql_select_limit = %d" % (limit + 1))      # one row more than a page: tells that there is more
            cur.execute("START TRANSACTION READ ONLY")
            started = time.monotonic()
            cur.execute(text)
            description = cur.description
            if description is None:
                raise AdminError("Interogarea nu intoarce randuri.")
            fetched = cur.fetchall()
            elapsed = int((time.monotonic() - started) * 1000)
            has_more = len(fetched) > limit
            fetched = _as_text(fetched[:limit], description)
            columns = _columns(description, fetched)
            types = [c["data_type"] for c in columns]
            rows = [[_cell(v, types[i]) for i, v in enumerate(row)] for row in fetched]
            cur.execute("ROLLBACK")
        finally:
            cur.close()
        return {"db": dbs[0], "columns": columns, "elapsed_ms": elapsed,
                "page": {"offset": 0, "limit": limit, "rows": rows, "has_more": has_more}}
    finally:
        if conn.is_connected():
            conn.close()


def register(sub):
    p = sub.add_parser("query-run")
    p.add_argument("--db", required=True)
    p.add_argument("--sql-b64", required=True)
    p.add_argument("--params-b64", default="")
    p.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    p.set_defaults(fn=cmd_query_run)
