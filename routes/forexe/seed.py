# routes/forexe/seed.py
# -----------------------------------------------------------------------------
# One-shot Access -> MariaDB seed for the un-migrated FX_ tables.
#
# Three endpoints, all keyed on the target DC (= db_name):
#   POST /api/forexe/seed/schema   -> DROP TABLE IF EXISTS + CREATE TABLE
#   POST /api/forexe/seed/rows     -> optional TRUNCATE, then INSERT ... ON DUPLICATE KEY UPDATE
#                                     (mode="insert_missing" => randurile existente NU se ating)
#   GET  /api/forexe/seed/columns  -> SHOW COLUMNS (read-only introspection)
#   GET|POST /api/forexe/seed/ids  -> exista aceste id-uri? (read-only, allow-list PROPRIU)
#
# NOTE (slice 0012-01): /schema stays in the code but the migration utility does NOT
# call it. Locked decision, variant A (non-destructive): the tables already exist in
# MariaDB with clean DDL and are not recreated from the DAO types. /columns exists so
# the caller can read the REAL column list instead of re-deriving it from Access.
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

# --- allow-list SEPARATA, ingusta, DOAR pentru /seed/ids ----------------------
# /ids citeste tabele pe care seed-ul NU are voie sa le scrie (familia DDF). De aceea
# NU refoloseste ALLOWED_TABLES: o pereche (tabel, coloana) explicita, si nimic altceva.
# Motivul existentei rutei: pe MariaDB FX_DDF.IDDF si FX_DDF_REV.IDREV sunt AUTO_INCREMENT
# si nu pastreaza id-ul Access alaturi, deci migratorul VERIFICA acele id-uri inainte de
# scriere. Nu traduce si nu ghiceste nimic — la lipsa, opreste DC-ul.
ALLOWED_ID_PAIRS = {
    ("FX_DDF", "IDDF"),
    ("FX_DDF_REV", "IDREV"),
}

# Max id-uri acceptate intr-o singura cerere /ids (GET sau POST).
MAX_IDS_PER_REQUEST = 1000

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
#   "truncate_first": true,           # true only on the first chunk of the table
#   "mode": "overwrite"               # optional; "overwrite" (implicit) | "insert_missing"
# }
# Dates arrive as "YYYY-MM-DD HH:MM:SS" strings; booleans as 0/1; missing as null.
#
# mode="overwrite"       -> ON DUPLICATE KEY UPDATE <toate coloanele> (comportamentul istoric;
#                           Access suprascrie MariaDB). Ramane implicit ca apelantii existenti
#                           sa nu se schimbe.
# mode="insert_missing"  -> ON DUPLICATE KEY UPDATE <prima coloana> = <prima coloana>, adica o
#                           auto-atribuire care nu face nimic. Un rand deja prezent pe MariaDB
#                           ramane NEATINS.
#
# De ce auto-atribuirea si NU `INSERT IGNORE`: IGNORE degradeaza la avertisment si erorile de
# tip, trunchierile si violarile de constrangere — adica ar inghiti esecuri reale. Forma de
# mai sus suprima EXCLUSIV cazul cheii duplicate.
#
# Sub aceasta forma `cursor.rowcount` e 1 pentru fiecare rand inserat si 0 pentru fiecare
# duplicat sarit, deci raspunsul poate raporta "inserted" si "skipped" exact.
#
# truncate_first + mode="insert_missing" -> 400. Combinatia e intotdeauna o greseala: golesti
# tabelul si apoi ceri sa nu suprascrii nimic.
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
    mode = body.get("mode") or "overwrite"

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
    if mode not in ("overwrite", "insert_missing"):
        return _err(
            "Modul de scriere „%s” este necunoscut (permise: „overwrite”, „insert_missing”)."
            % mode,
            400,
        )
    if truncate_first and mode == "insert_missing":
        return _err(
            "„truncate_first” nu se poate combina cu modul „insert_missing”: "
            "ai goli tabelul și apoi ai cere să nu suprascrii nimic.",
            400,
        )

    ncols = len(columns)
    for r in rows:
        if not isinstance(r, list) or len(r) != ncols:
            return _err("Un rând nu are numărul corect de valori.", 400)

    col_list = ",".join("`%s`" % c for c in columns)
    placeholders = ",".join(["%s"] * ncols)
    if mode == "insert_missing":
        # Auto-atribuire fara efect pe prima coloana: suprima DOAR cheia duplicata.
        updates = "`%s`=`%s`" % (columns[0], columns[0])
    else:
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

    # In modul insert_missing rowcount e 1/rand inserat si 0/duplicat sarit, deci diferenta
    # fata de numarul de randuri primite este exact numarul celor sarite.
    payload = {"ok": True, "table": table, "received": len(rows), "affected": inserted}
    if mode == "insert_missing":
        payload["mode"] = mode
        payload["inserted"] = inserted
        payload["skipped"] = len(rows) - inserted
    return _json(payload)


# -----------------------------------------------------------------------------
# 3) COLUMNS: introspecție read-only a coloanelor unui tabel deja existent.
#
#   GET /api/forexe/seed/columns?db_name=075_CEVM&table=FX_Receptii
#   -> {"ok": true, "table": "FX_Receptii", "columns": ["IDR", "IDRH", ...]}
#
# Utilitarul de migrare cere lista reală de coloane din MariaDB ca să construiască
# INSERT-urile spre /rows fără să recreeze schema (varianta A, nedistructivă: tabelele
# există deja cu DDL curat, nu se regenerează din tipurile DAO).
#
# Tabel inexistent -> 200 cu listă goală, NU 404: apelantul trebuie să poată distinge
# «tabelul nu are coloane aici» de o eroare de rețea/autentificare. De aceea existența
# se testează întâi cu SHOW TABLES LIKE (care nu aruncă), și abia apoi SHOW COLUMNS.
#
# Strict read-only: nicio scriere, niciun DDL, niciun commit.
# -----------------------------------------------------------------------------
@seed_bp.route("/api/forexe/seed/columns", methods=["GET"])
@require_api_key
def seed_columns():
    db_name = request.args.get("db_name")
    table = request.args.get("table")

    if not _validate_db_name(db_name):
        return _err("Numele bazei de date (DC) este invalid.", 400)
    if not _validate_table(table):
        return _err("Tabelul „%s” nu este permis pentru seed." % table, 400)

    columns = []
    conn = None
    try:
        conn = get_db_connection(db_name)
        cur = conn.cursor()
        # LIKE pe un literal parametrizat: numele a trecut deja allow-list-ul, dar
        # aici nici nu ajunge in SQL ca identificator.
        cur.execute("SHOW TABLES LIKE %s", (table,))
        if cur.fetchone() is not None:
            # Identificatorul e sigur: `table` e membru al ALLOWED_TABLES.
            cur.execute("SHOW COLUMNS FROM `%s`" % table)
            # SHOW COLUMNS: Field, Type, Null, Key, Default, Extra — ordinea din tabel.
            columns = [r[0] for r in cur.fetchall()]
    except Exception as exc:  # surface loudly, never swallow
        return _err(
            "Eroare la citirea coloanelor tabelului „%s”: %s" % (table, exc), 500
        )
    finally:
        if conn is not None:
            conn.close()

    return _json({"ok": True, "table": table, "columns": columns})


def _all_idents(values):
    for v in values:
        if not isinstance(v, str) or not _IDENT_RE.match(v):
            return False
    return True


# -----------------------------------------------------------------------------
# 4) IDS: exista aceste id-uri in tabelul tinta? Strict read-only.
#
#   GET  /api/forexe/seed/ids?db_name=045_CTER&table=FX_DDF&column=IDDF&values=73,77,79
#   POST /api/forexe/seed/ids   {"db_name":"045_CTER","table":"FX_DDF",
#                                "column":"IDDF","values":[73,77,79]}
#   -> 200 {"ok": true, "table": "FX_DDF", "column": "IDDF",
#           "found": [73, 77], "missing": [79]}
#
# DE CE EXISTA: in setul migrat, sapte coloane arata spre familia DDF —
# FX_Angajamente.IDDF, FX_Salarii.IDDF, FX_Salarii.IDREV, FX_Rezervari.IDREV,
# FX_Receptii.IDREV, FX_Plati.IDREV, FX_Istoric.IDREV. Pe MariaDB FX_DDF.IDDF si
# FX_DDF_REV.IDREV sunt AUTO_INCREMENT si NU pastreaza id-ul Access alaturi, deci
# potrivirea celor doua parti e o PRESUPUNERE. Ruta asta o verifica inainte de prima
# scriere. Nu traduce nimic, nu remapeaza nimic: la prima lipsa, migratorul opreste DC-ul
# si listeaza id-urile care lipsesc.
#
# Allow-list PROPRIE (ALLOWED_ID_PAIRS), nu ALLOWED_TABLES: ruta citeste tocmai tabelele
# pe care seed-ul are interzis sa le scrie. Perechea (tabel, coloana) e fixa; niciun
# identificator din client nu ajunge in SQL fara sa treaca prin ea.
#
# Valorile sunt intregi (IDDF/IDREV sunt Long in Access, INT pe MariaDB) si intra in SQL
# DOAR parametrizate. Varianta POST exista pentru loturile prea mari pentru un URL.
#
# Tabel inexistent -> 500 cu mesaj explicit, NU 200 cu «toate lipsesc»: alea doua sunt
# diagnostice complet diferite, iar al doilea ar minti. (Difera intentionat de /columns,
# unde lista goala e un raspuns cu sens.)
#
# Strict read-only: niciun INSERT, niciun DDL, niciun commit. conn.close() in finally.
# -----------------------------------------------------------------------------
@seed_bp.route("/api/forexe/seed/ids", methods=["GET", "POST"])
@require_api_key
def seed_ids():
    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        db_name = body.get("db_name")
        table = body.get("table")
        column = body.get("column")
        raw_values = body.get("values")
    else:
        db_name = request.args.get("db_name")
        table = request.args.get("table")
        column = request.args.get("column")
        raw = request.args.get("values")
        # Lista goala si parametrul absent sunt lucruri diferite: "" -> [], lipsa -> None.
        if raw is None:
            raw_values = None
        else:
            raw_values = [p for p in raw.split(",") if p.strip() != ""]

    if not _validate_db_name(db_name):
        return _err("Numele bazei de date (DC) este invalid.", 400)
    if not isinstance(table, str) or not isinstance(column, str):
        return _err("Tabelul și coloana sunt obligatorii.", 400)
    if (table, column) not in ALLOWED_ID_PAIRS:
        return _err(
            "Perechea tabel/coloană „%s”.„%s” nu este permisă pentru verificarea de id-uri."
            % (table, column),
            400,
        )
    if not isinstance(raw_values, list):
        return _err("Câmpul „values” lipsește sau nu este o listă.", 400)
    if len(raw_values) > MAX_IDS_PER_REQUEST:
        return _err(
            "Prea multe id-uri într-o singură cerere (max %d)." % MAX_IDS_PER_REQUEST,
            400,
        )

    # Intregi, si numai intregi. Un id care nu e intreg e o eroare de apelant, nu un id lipsa.
    wanted = []
    seen = set()
    for v in raw_values:
        if isinstance(v, bool) or not isinstance(v, (int, str)):
            return _err("Id-ul „%s” nu este un număr întreg." % (v,), 400)
        try:
            n = int(str(v).strip())
        except (TypeError, ValueError):
            return _err("Id-ul „%s” nu este un număr întreg." % (v,), 400)
        if n not in seen:
            seen.add(n)
            wanted.append(n)

    if not wanted:
        return _json(
            {"ok": True, "table": table, "column": column, "found": [], "missing": []}
        )

    found = []
    conn = None
    try:
        conn = get_db_connection(db_name)
        cur = conn.cursor()
        # Ca la /columns: SHOW COLUMNS/SELECT pe un tabel inexistent arunca, iar adulmecarea
        # errno-ului ar inghiti la fel de bine o eroare reala de drepturi. Testam intai
        # existenta cu numele ca VALOARE parametrizata.
        cur.execute("SHOW TABLES LIKE %s", (table,))
        if cur.fetchone() is None:
            return _err(
                "Tabelul „%s” nu există în baza „%s”. Schema nu a fost instalată acolo."
                % (table, db_name),
                500,
            )
        placeholders = ",".join(["%s"] * len(wanted))
        # Identificatorii vin EXCLUSIV din ALLOWED_ID_PAIRS; valorile sunt parametrizate.
        cur.execute(
            "SELECT DISTINCT `%s` FROM `%s` WHERE `%s` IN (%s)"
            % (column, table, column, placeholders),
            tuple(wanted),
        )
        found = [int(r[0]) for r in cur.fetchall() if r[0] is not None]
    except Exception as exc:  # surface loudly, never swallow
        return _err(
            "Eroare la verificarea id-urilor în „%s”: %s" % (table, exc), 500
        )
    finally:
        if conn is not None:
            conn.close()

    found_set = set(found)
    return _json(
        {
            "ok": True,
            "table": table,
            "column": column,
            "found": sorted(found_set),
            "missing": sorted(n for n in wanted if n not in found_set),
        }
    )
