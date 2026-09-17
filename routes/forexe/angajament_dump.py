# routes/forexe/angajament_dump.py
"""
Diagnostic dump of ONE angajament: every row, in every FX_ table, that belongs to it.

    GET /api/forexe/angajament/dump?db_name=<DC>&cod=<CodAngajament>

Purpose: a developer hands the whole picture of an angajament to a person (or an
assistant) and asks questions about it. It is NOT a view the operator sees and NOT
a contract any VB client depends on -- the shape is "raw tables, raw columns".

What goes on the wire:
  - `tables`  : { "<table>": { "count", "key", "skipped_columns", "rows": [ {col: val} ] } }
                one entry per table that was queried, in dependency order (parents
                before children), rows as plain dicts keyed by the REAL column names.
  - `lookups` : the nomenclator rows the FX_ rows point at (Clasificatii by
                IdClsfAcc + IdUnitate from FX_Indicatori, Parteneri by CodPartener
                from FX_DDF_REV_SA), so IdClsf / CodPartener can be read by a human.
  - `missing_tables` : child tables in the static chain that do not exist in this DC.

What stays OUT, deliberately:
  - FX_Extrase / FX_Extrase_F / FX_Extrase_H (the bank statements) -- requested.
  - tmp% tables, *_IMG / *_PDF tables, and every BLOB / BINARY column elsewhere:
    a dump is text. Each skipped column is listed under `skipped_columns` and its
    byte size travels as `<col>_bytes`, so "is there an attachment" is still answerable.

How the rows are found (two passes, both parameterised, cod never interpolated):
  1. DISCOVERY: every table in the DC that has a `CodAngajament` column is read with
     `WHERE CodAngajament = %s`. New tables that carry the code appear here with no
     code change.
  2. CHAIN: child tables that do NOT carry the code are reached through their parent
     keys (FX_Receptii by IDRH from FX_Receptii_H, FX_DDF_REV by IDDF from FX_DDF,
     FX_DDF_REV_* by IDREV, FX_ORD_TBL / PART / ATT by IDORDP, FX_ORD_TBL_REC by
     IDORDTBLP, FX_ORD_DOC by IDORDPARTP). A child already found in pass 1 is not
     read twice.

Guard: `require_session_or_api_key` -- callable from K-BOT with the bearer, or by hand
with the legacy X-Api-Key (curl). `db_name` comes from the query on purpose: the caller
names the DC, the session's own DC is not consulted.
"""
import datetime
import decimal
import json
import logging

from flask import request, current_app

from routes.auth.guard import require_session_or_api_key
from utils.database import get_kbot_connection
from routes.admin import _validate_db_name

from . import forexe_bp

logger = logging.getLogger(__name__)

# Column types that never travel: replaced by their byte length (`<col>_bytes`).
_BINARY_TYPES = frozenset(("blob", "tinyblob", "mediumblob", "longblob", "binary", "varbinary"))

# Table name prefixes / suffixes that are left out of the dump entirely.
_EXCLUDED_PREFIXES = ("tmp", "FX_Extrase")
_EXCLUDED_SUFFIXES = ("_IMG", "_PDF")

# Preferred order for the discovery pass (parents / the well-known tables first);
# anything else with a CodAngajament column follows alphabetically.
_PREFERRED_ORDER = (
    "FX_Angajamente", "FX_Indicatori", "FX_Istoric", "FX_Rezervari",
    "FX_Receptii_R", "FX_Receptii_RHR", "FX_Receptii_H", "FX_Receptii",
    "FX_Receptii_Plati", "FX_Plati", "FX_DDF", "FX_DDF_REV", "FX_DDF_REV_SA",
    "FX_DDF_REV_SB", "FX_DDF_REV_ATT", "FX_DDF_REV_PRT", "FX_ORD", "FX_ORD_PART",
    "FX_ORD_TBL", "FX_ORD_TBL_REC", "FX_ORD_DOC", "FX_ORD_ATT",
)

# Static chain: (child, child key column, parent, parent key column). Processed in
# order, so a parent that is itself a child (FX_DDF_REV, FX_ORD_TBL, FX_ORD_PART)
# has already been read when its children come up.
_CHAIN = (
    ("FX_Receptii",       "IDRH",       "FX_Receptii_H", "IDRH"),
    ("FX_Receptii_Plati", "IDRH",       "FX_Receptii_H", "IDRH"),
    ("FX_DDF_REV",        "IDDF",       "FX_DDF",        "IDDF"),
    ("FX_DDF_REV_SA",     "IDREV",      "FX_DDF_REV",    "IDREV"),
    ("FX_DDF_REV_SB",     "IDREV",      "FX_DDF_REV",    "IDREV"),
    ("FX_DDF_REV_ATT",    "IDREV",      "FX_DDF_REV",    "IDREV"),
    ("FX_DDF_REV_PRT",    "IDREV",      "FX_DDF_REV",    "IDREV"),
    ("FX_ORD_PART",       "IDORDP",     "FX_ORD",        "IDORDP"),
    ("FX_ORD_TBL",        "IDORDP",     "FX_ORD",        "IDORDP"),
    ("FX_ORD_ATT",        "IDORDP",     "FX_ORD",        "IDORDP"),
    ("FX_ORD_TBL_REC",    "IDORDTBLP",  "FX_ORD_TBL",    "IDORDTBLP"),
    ("FX_ORD_DOC",        "IDORDPARTP", "FX_ORD_PART",   "IDORDPARTP"),
)

_SQL_COLUMNS = (
    "SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, COLUMN_KEY, ORDINAL_POSITION "
    "FROM information_schema.COLUMNS "
    "WHERE TABLE_SCHEMA = %s "
    "ORDER BY TABLE_NAME, ORDINAL_POSITION"
)

# Nomenclator lookups. Clasificatii: FX_Indicatori.IdClsf is the ACCESS id, which
# matches Clasificatii.IdClsfAcc (never IDClsf) and needs IdUnitate as well -- the
# nomenclator is stored for several units in one DC (see sumar.py, R6).
_SQL_CLASIFICATII = (
    "SELECT C.* FROM Clasificatii C "
    "WHERE EXISTS (SELECT 1 FROM FX_Indicatori I "
    "              WHERE I.CodAngajament = %s "
    "                AND I.IdClsf = C.IdClsfAcc AND I.IdUnitate = C.IdUnitate) "
    "ORDER BY C.IdUnitate, C.IdClsfAcc"
)
_SQL_PARTENERI = (
    "SELECT P.* FROM Parteneri P "
    "WHERE P.CodPartener IN (SELECT SA.CodPartener FROM FX_DDF_REV_SA SA "
    "                        WHERE SA.CodAngajament = %s "
    "                          AND SA.CodPartener IS NOT NULL) "
    "ORDER BY P.CodPartener"
)


def _json_utf8(payload, status):
    """JSON with literal diacritics (ensure_ascii=False): Descriere / Denumire are
    Romanian text and must reach the reader as real UTF-8, not \\uXXXX."""
    body = json.dumps(payload, ensure_ascii=False, default=_jsonable)
    return current_app.response_class(body, status=status, mimetype="application/json")


def _jsonable(value):
    """json.dumps fallback for the driver's own types: dates -> ISO, Decimal -> float,
    timedelta (TIME columns) -> str, stray bytes -> byte count marker."""
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, datetime.timedelta):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        return f"<{len(value)} bytes>"
    return str(value)


def _is_excluded(table):
    return (table.startswith(_EXCLUDED_PREFIXES)
            or table.endswith(_EXCLUDED_SUFFIXES))


def load_schema(cursor, db_name):
    """{table: {"columns": [(name, data_type)], "pk": [name]}} for the DC, minus the
    excluded tables. Reads information_schema once; every SELECT is built from it."""
    cursor.execute(_SQL_COLUMNS, (db_name,))
    schema = {}
    for row in cursor.fetchall():
        table = row["TABLE_NAME"]
        if _is_excluded(table):
            continue
        entry = schema.setdefault(table, {"columns": [], "pk": []})
        entry["columns"].append((row["COLUMN_NAME"], str(row["DATA_TYPE"]).lower()))
        if row["COLUMN_KEY"] == "PRI":
            entry["pk"].append(row["COLUMN_NAME"])
    return schema


def build_select(table, entry, key_column, key_count):
    """SELECT for one table: every non-binary column verbatim, binary ones as
    OCTET_LENGTH(...) AS <col>_bytes, `WHERE <key> IN (%s, ...)` with one placeholder
    per key value, ORDER BY the primary key (or nothing). Returns (sql, skipped)."""
    parts = []
    skipped = []
    for name, data_type in entry["columns"]:
        if data_type in _BINARY_TYPES:
            parts.append(f"OCTET_LENGTH(`{name}`) AS `{name}_bytes`")
            skipped.append(name)
        else:
            parts.append(f"`{name}`")
    placeholders = ", ".join(["%s"] * key_count)
    order = ""
    if entry["pk"]:
        order = " ORDER BY " + ", ".join(f"`{c}`" for c in entry["pk"])
    sql = (f"SELECT {', '.join(parts)} FROM `{table}` "
           f"WHERE `{key_column}` IN ({placeholders}){order}")
    return sql, skipped


def _read_table(cursor, table, entry, key_column, key_values):
    """Runs the SELECT for `table` on `key_values` and returns the dump entry.
    No keys (childless parent) -> no query, empty rows."""
    values = list(key_values)
    sql, skipped = build_select(table, entry, key_column, max(len(values), 1))
    if values:
        cursor.execute(sql, values)
        rows = cursor.fetchall()
    else:
        rows = []
    return {
        "count": len(rows),
        "key": key_column,
        "skipped_columns": skipped,
        "rows": rows,
    }


def has_column(entry, column):
    return any(name == column for name, _ in entry["columns"])


def discovery_order(schema):
    """Tables with a CodAngajament column: preferred order first, the rest A-Z."""
    found = [t for t, e in schema.items() if has_column(e, "CodAngajament")]
    ranked = [t for t in _PREFERRED_ORDER if t in found]
    rest = sorted(t for t in found if t not in ranked)
    return ranked + rest


def dump_angajament(cursor, db_name, cod):
    """The whole dump as a dict (no Flask): schema, discovery pass, chain pass, lookups."""
    schema = load_schema(cursor, db_name)
    tables = {}
    missing = []

    # Pass 1: every table that carries the code.
    for table in discovery_order(schema):
        tables[table] = _read_table(cursor, table, schema[table], "CodAngajament", [cod])

    # Pass 2: children reached through their parent's key.
    for child, child_col, parent, parent_col in _CHAIN:
        if child in tables:
            continue                                   # found in pass 1 already
        if child not in schema:
            missing.append(child)
            continue
        if not has_column(schema[child], child_col):
            missing.append(f"{child}.{child_col}")
            continue
        parent_rows = tables.get(parent, {}).get("rows", [])
        keys = sorted({r[parent_col] for r in parent_rows if r.get(parent_col) is not None})
        tables[child] = _read_table(cursor, child, schema[child], child_col, keys)

    # Lookups: only when the tables they need exist in this DC.
    lookups = {}
    if "Clasificatii" in schema and "FX_Indicatori" in schema:
        cursor.execute(_SQL_CLASIFICATII, (cod,))
        lookups["Clasificatii"] = cursor.fetchall()
    if "Parteneri" in schema and "FX_DDF_REV_SA" in schema:
        cursor.execute(_SQL_PARTENERI, (cod,))
        lookups["Parteneri"] = cursor.fetchall()

    return {
        "db_name": db_name,
        "cod": cod,
        "found": tables.get("FX_Angajamente", {}).get("count", 0) > 0,
        "tables": tables,
        "lookups": lookups,
        "missing_tables": missing,
    }


@forexe_bp.route("/api/forexe/angajament/dump", methods=["GET"])
@require_session_or_api_key
def get_angajament_dump():
    """Query: db_name (the DC, required), cod (CodAngajament, required).

    An unknown code is NOT a 404: the answer is 200 with `found: false` and empty
    tables, so "nothing there" and "the request failed" stay distinguishable.
    """
    db_name = request.args.get("db_name")
    if db_name is None or str(db_name).strip() == "":
        return _json_utf8({"error": "Parametru lipsă: db_name"}, 400)
    cod = request.args.get("cod")
    if cod is None or str(cod).strip() == "":
        return _json_utf8({"error": "Parametru lipsă: cod"}, 400)
    cod = str(cod).strip()
    try:
        db_name = _validate_db_name(db_name.strip())
    except ValueError as e:
        return _json_utf8({"error": str(e)}, 400)

    conn = None
    try:
        conn = get_kbot_connection(db_name)
        cursor = conn.cursor(dictionary=True)
        payload = dump_angajament(cursor, db_name, cod)
        logger.info("[forexe.angajament_dump] %s: cod=%s -> tables=%s found=%s",
                    db_name, cod,
                    {t: v["count"] for t, v in payload["tables"].items()},
                    payload["found"])
        return _json_utf8(payload, 200)
    except Exception as e:
        logger.error(f"[forexe.angajament_dump] {e}", exc_info=True)
        return _json_utf8({"error": f"Eroare la citirea angajamentului: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()
