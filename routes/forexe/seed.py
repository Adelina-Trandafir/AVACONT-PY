# routes/forexe/seed.py
# -----------------------------------------------------------------------------
# One-shot Access -> MariaDB seed for the un-migrated FX_ tables.
#
# Two endpoints, both keyed on the target DC (= db_name):
#   POST /api/forexe/seed/schema   -> DROP TABLE IF EXISTS + CREATE TABLE
#   POST /api/forexe/seed/rows     -> optional TRUNCATE, then INSERT ... ON DUPLICATE KEY UPDATE
#
# Design rules honoured here:
#   * The Access primary-key IDs are preserved verbatim (no AUTO_INCREMENT during the
#     seed) so intra-family FK columns (IDRH, IDRR, IDRZ, IDEXF, IDR, IDH ...) stay valid.
#   * No cross-family FK constraints are created (FX_ORD*/FX_DDF* stay decoupled).
#   * The VBA side introspects the live Access TableDef and sends the column list + DAO
#     types, so the CREATE mirrors the Access schema exactly (column parity guaranteed).
#   * IdUnitate is NOT a routing key here; the VBA already scoped the rows by DC.
#   * Server-side allow-list on both the table name and every column identifier; all
#     values are parametrized. No client string ever reaches SQL as an identifier
#     without passing the allow-list + regex.
#
# Romanian, real diacritics, ensure_ascii=False on every response body.
# -----------------------------------------------------------------------------

import re
import json

from flask import Blueprint, request, Response

# Seed-ul e condus de VBA (FOREXE legacy) — se autentifica cu X-Api-Key, NU cu tokenul
# bearer (acela e DOAR pentru aplicatiile VB.NET / K-BOT). De aceea guard-ul e
# require_api_key din utils/security.py, nu require_session.
from utils.database import get_db_connection    # conn = get_db_connection(db_name); finally: conn.close()
from utils.security import require_api_key      # X-Api-Key legacy (flota FOREXE veche)

seed_bp = Blueprint("forexe_seed", __name__)

# --- allow-list: exactly the un-migrated FX_ set, minus the deprecated FX_Parteneri ----
ALLOWED_TABLES = {
    "FX_Angajamente",
    "FX_Indicatori",
    "FX_Istoric",
    "FX_Salarii",
    "FX_Rezervari",
    "FX_Rezervarii_IMG",
    "FX_Extrase",
    "FX_Extrase_F",
    "FX_Extrase_H",
    "FX_Receptii",
    "FX_Receptii_H",
    "FX_Receptii_R",
    "FX_Receptii_RHR",
    "FX_Receptii_IMG",
    "FX_Receptii_Plati",
    "FX_Plati",
}

# Identifiers we are willing to emit into SQL (after allow-list checks).
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# db_name shape: 000_DEMO, 075_CEVM, ...
_DBNAME_RE = re.compile(r"^[0-9]{3}_[A-Za-z0-9]+$")

# Max rows accepted per /rows request (VBA chunks to this; keep payloads modest so
# Memo/IMG batches do not blow past Flask MAX_CONTENT_LENGTH).
MAX_ROWS_PER_REQUEST = 1000

# DAO field-type code -> MariaDB column type.
#   1 Boolean 2 Byte 3 Integer 4 Long 5 Currency 6 Single 7 Double 8 Date
#   9 Binary 10 Text 11 LongBinary 12 Memo 15 GUID 16 BigInt 20 Decimal
def _dao_to_mariadb(dao_type, size):
    try:
        t = int(dao_type)
    except (TypeError, ValueError):
        t = 12  # treat unknown as Memo/LONGTEXT

    if t == 1:
        return "TINYINT(1)"
    if t == 2:
        return "TINYINT UNSIGNED"
    if t == 3:
        return "SMALLINT"
    if t == 4:
        return "INT"
    if t == 5:
        return "DECIMAL(19,4)"
    if t == 6:
        return "FLOAT"
    if t == 7:
        return "DOUBLE"
    if t == 8:
        return "DATETIME"
    if t == 9:
        return "VARBINARY(510)"
    if t == 10:
        n = 255
        try:
            n = int(size)
        except (TypeError, ValueError):
            n = 255
        if n <= 0 or n > 1000:
            n = 255
        return "VARCHAR(%d)" % n
    if t == 11:
        return "LONGBLOB"
    if t == 12:
        return "LONGTEXT"
    if t == 15:
        return "CHAR(38)"
    if t == 16:
        return "BIGINT"
    if t == 20:
        return "DECIMAL(28,6)"
    return "LONGTEXT"


def _json(payload, status=200):
    return Response(
        json.dumps(payload, ensure_ascii=False),
        status=status,
        mimetype="application/json; charset=utf-8",
    )


def _err(message, status):
    return _json({"ok": False, "error": message}, status)


def _validate_db_name(db_name):
    if not db_name or not _DBNAME_RE.match(db_name):
        return False
    return True


def _validate_table(table):
    return bool(table) and table in ALLOWED_TABLES


def _validate_columns(columns):
    """columns: list of names. Every one must be a safe identifier."""
    if not isinstance(columns, list) or not columns:
        return False
    for c in columns:
        if not isinstance(c, str) or not _IDENT_RE.match(c):
            return False
    return True


# -----------------------------------------------------------------------------
# 1) SCHEMA: DROP TABLE IF EXISTS + CREATE TABLE
#
# Body:
# {
#   "db_name": "075_CEVM",
#   "table":   "FX_Receptii",
#   "columns": [ {"name":"IDR","dao_type":4,"size":4,"required":true}, ... ],
#   "pk":      ["IDR"]                      # 0..n columns; INT PK stays NON auto-increment
# }
# -----------------------------------------------------------------------------
@seed_bp.route("/api/forexe/seed/schema", methods=["POST"])
@require_api_key
def seed_schema():
    body = request.get_json(silent=True) or {}

    db_name = body.get("db_name")
    table = body.get("table")
    columns = body.get("columns")
    pk = body.get("pk") or []

    if not _validate_db_name(db_name):
        return _err("Numele bazei de date (DC) este invalid.", 400)
    if not _validate_table(table):
        return _err("Tabelul „%s” nu este permis pentru seed." % table, 400)
    if not isinstance(columns, list) or not columns:
        return _err("Lista de coloane lipsește sau este goală.", 400)

    col_names = [c.get("name") for c in columns]
    if not _validate_columns(col_names):
        return _err("Cel puțin o coloană are un nume invalid.", 400)

    if not isinstance(pk, list) or not _all_idents(pk):
        return _err("Cheia primară conține un identificator invalid.", 400)
    pk_set = set(pk)
    if not pk_set.issubset(set(col_names)):
        return _err("Cheia primară conține coloane inexistente.", 400)

    # Build the column definitions.
    defs = []
    for c in columns:
        name = c.get("name")
        col_type = _dao_to_mariadb(c.get("dao_type"), c.get("size"))
        not_null = " NOT NULL" if (name in pk_set or c.get("required")) else " NULL"
        defs.append("  `%s` %s%s" % (name, col_type, not_null))

    if pk:
        defs.append("  PRIMARY KEY (%s)" % ",".join("`%s`" % p for p in pk))

    create_sql = (
        "CREATE TABLE `%s` (\n%s\n) ENGINE=InnoDB "
        "DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;"
        % (table, ",\n".join(defs))
    )
    drop_sql = "DROP TABLE IF EXISTS `%s`;" % table

    conn = None
    try:
        conn = get_db_connection(db_name)
        cur = conn.cursor()
        cur.execute("SET FOREIGN_KEY_CHECKS=0;")
        cur.execute(drop_sql)
        cur.execute(create_sql)
        cur.execute("SET FOREIGN_KEY_CHECKS=1;")
        conn.commit()
    except Exception as exc:  # surface loudly, never swallow
        return _err("Eroare la crearea tabelului „%s”: %s" % (table, exc), 500)
    finally:
        if conn is not None:
            conn.close()

    return _json({"ok": True, "table": table, "ddl": create_sql})


# -----------------------------------------------------------------------------
# 2) ROWS: optional TRUNCATE, then chunked INSERT ... ON DUPLICATE KEY UPDATE
#
# Body:
# {
#   "db_name":        "075_CEVM",
#   "table":          "FX_Receptii",
#   "columns":        ["IDR","IDRH","CodAI", ...],   # order matches every row
#   "rows":           [ [264, 225, "AAB..-AAB", ...], ... ],  # values: num|str|bool|null
#   "truncate_first": true            # true only on the first chunk of the table
# }
# Dates arrive as "YYYY-MM-DD HH:MM:SS" strings; booleans as 0/1; missing as null.
# -----------------------------------------------------------------------------
@seed_bp.route("/api/forexe/seed/rows", methods=["POST"])
@require_api_key
def seed_rows():
    body = request.get_json(silent=True) or {}

    db_name = body.get("db_name")
    table = body.get("table")
    columns = body.get("columns")
    rows = body.get("rows")
    truncate_first = bool(body.get("truncate_first"))

    if not _validate_db_name(db_name):
        return _err("Numele bazei de date (DC) este invalid.", 400)
    if not _validate_table(table):
        return _err("Tabelul „%s” nu este permis pentru seed." % table, 400)
    if not _validate_columns(columns):
        return _err("Lista de coloane lipsește sau conține un nume invalid.", 400)
    if not isinstance(rows, list):
        return _err("Câmpul „rows” trebuie să fie o listă.", 400)
    if len(rows) > MAX_ROWS_PER_REQUEST:
        return _err(
            "Prea multe rânduri într-o singură cerere (max %d)." % MAX_ROWS_PER_REQUEST,
            400,
        )

    ncols = len(columns)
    for r in rows:
        if not isinstance(r, list) or len(r) != ncols:
            return _err("Un rând nu are numărul corect de valori.", 400)

    col_list = ",".join("`%s`" % c for c in columns)
    placeholders = ",".join(["%s"] * ncols)
    updates = ",".join("`%s`=VALUES(`%s`)" % (c, c) for c in columns)
    insert_sql = (
        "INSERT INTO `%s` (%s) VALUES (%s) ON DUPLICATE KEY UPDATE %s"
        % (table, col_list, placeholders, updates)
    )

    inserted = 0
    conn = None
    try:
        conn = get_db_connection(db_name)
        cur = conn.cursor()
        if truncate_first:
            cur.execute("TRUNCATE TABLE `%s`;" % table)
        if rows:
            cur.executemany(insert_sql, [tuple(r) for r in rows])
            inserted = cur.rowcount
        conn.commit()
    except Exception as exc:  # surface loudly, never swallow
        return _err("Eroare la inserarea în „%s”: %s" % (table, exc), 500)
    finally:
        if conn is not None:
            conn.close()

    return _json(
        {"ok": True, "table": table, "received": len(rows), "affected": inserted}
    )


def _all_idents(values):
    for v in values:
        if not isinstance(v, str) or not _IDENT_RE.match(v):
            return False
    return True
