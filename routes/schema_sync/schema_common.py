"""
schema_common.py -- shared plumbing.

Connection, logging, target discovery, the execution-priority table, and
creation of the control table. No stored procedures are involved
anywhere in this package: the control table is created by this module if
it does not exist, and migrated if its object_type enum is out of date.

Logging follows the pattern in utils/logger.py -- same formatter, same
RotatingFileHandler, same ip field -- but writes to its own file, since
a hand-run tool announcing "SERVER LOGGING INITIALIZED" into the API log
would be noise.
"""

import logging
import os
import re
import sys
from logging.handlers import RotatingFileHandler

import mysql.connector

from config import DB_CONFIG_NEW

# The unit registry. Source of the target list. Never itself a target.
COMMON_DB = "AVACONT_COMUN"

# The reference schema every target is compared against. Never a target.
SOURCE_DB = "AVACONT_SURSA"

# Where schema_diff_log lives. NOT in AVACONT_SURSA: that schema is the
# template every unit is cloned from, so a control table placed there
# would be created in every unit database on the next sync. It lives in
# AVACONT_COMUN, which is never a sync target.
CONTROL_DB = COMMON_DB

# Tables that belong to this tool and must never be diffed, wherever
# they happen to sit.
EXCLUDED_TABLES = {"schema_diff_log"}

# ---------------------------------------------------------------------
# Columns the sync must not look at -- see docs/PLAN_ForexeIngest.md 3.2
# ---------------------------------------------------------------------
# These eleven primary keys are plain `INT NOT NULL` in AVACONT_SURSA and
# `INT NOT NULL AUTO_INCREMENT` in every MIGRATED unit database. That is
# deliberate and permanent, not drift:
#
#   * The reference schema keeps them plain so that during a migration a
#     row arriving with a missing, NULL or zero id RAISES instead of
#     silently receiving a fabricated key from MariaDB (plan 3.1).
#   * The ALTER that adds AUTO_INCREMENT is the LAST thing that happens
#     to a unit database, once, after its transfer is verified. It is
#     never applied to AVACONT_SURSA.
#
# So every migrated database differs from the reference on exactly these
# eleven columns, forever, and the sync must neither report nor rewrite
# that difference.
#
# WRITTEN OUT AS A NAMED LIST ON PURPOSE. A rule such as "skip anything
# auto_increment" would quietly swallow a real difference somewhere else;
# this cannot.
#
# ACCEPTED COST, stated plainly: the exemption covers the WHOLE column,
# not merely its AUTO_INCREMENT attribute. If anyone ever deliberately
# changes one of these eleven columns in AVACONT_SURSA -- a type change, a
# width change -- it will NOT propagate, and the divergence will NOT be
# reported. This list is where they will have to look.
#
# Safe because nothing edits these columns: they are primary keys whose
# only property that ever changes is the one the migrator sets, once.
#
# Compared case-insensitively (see is_exempt_column): information_schema
# reports names in whatever case the server stores them, and a server
# running with lower_case_table_names=1 folds them.
EXEMPT_COLUMNS = {
    ("FX_Istoric",      "ID"),
    ("FX_Receptii_R",   "IDRR"),
    ("FX_Receptii_H",   "IDRH"),
    ("FX_Receptii",     "IDR"),
    ("FX_Receptii_RHR", "IDRHR"),
    ("FX_Plati",        "IdPlataFX"),
    ("FX_Rezervari",    "IDRZ"),
    # Adaugate 08.09.2026, odata cu importul de extrase (felia 0057): routes/forexe/
    # extrase.py leaga randurile copil prin `cursor.lastrowid`, care pe o cheie INT
    # simpla raspunde 0. Aceeasi lista ca AutoIncrementStep.Targets din KBot.Migrator --
    # cele doua descriu O SINGURA decizie si se schimba impreuna.
    ("FX_Extrase_F",    "IDEXF"),
    ("FX_Extrase_H",    "IDEXH"),
    ("FX_Extrase",      "IDFXE"),
    # Adaugata 09.09.2026. `Clasificatii_Venituri` e Access-ul `ClasificatiiV`
    # redenumit, si a intrat in AVACONT_SURSA declarata AUTO_INCREMENT -- adica
    # exact fara paza descrisa mai sus, pe singura cheie spre care arata alte
    # doua lucruri: `FX_Extrase_H.IdClsfV` si cheia straina a lui
    # `Clasificatii_Venituri_Rectificari`. Operatorul a facut-o INT UNSIGNED NOT
    # NULL pe server in aceeasi zi, deci acum se poarta ca celelalte zece:
    # simpla in referinta, AUTO_INCREMENT dupa migrare.
    ("Clasificatii_Venituri", "IdClsfV"),
}

# Lower-cased once at import so the per-column test below is a plain set
# lookup rather than a scan. A set is a dictionary with keys and no
# values -- membership is a hash-table probe, not a loop.
_EXEMPT_COLUMNS_LOWER = {(t.lower(), c.lower()) for t, c in EXEMPT_COLUMNS}


def is_exempt_column(table_name: str, column_name: str) -> bool:
    """True when this (table, column) is on the EXEMPT_COLUMNS list."""
    if table_name is None or column_name is None:
        return False
    return (table_name.lower(), column_name.lower()) in _EXEMPT_COLUMNS_LOWER

FORBIDDEN_TARGETS = {COMMON_DB, SOURCE_DB, "information_schema",
                     "performance_schema", "mysql", "sys"}

# A unit database is named with a three-digit prefix (000_DEMO, 001_...).
# Everything else on the server -- the template, the registry, the server's
# own schemas -- is not a unit and is never offered as a target.
UNIT_DB_RE = re.compile(r"^\d{3}")

LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "schema_sync.log")

# Where generated .sql files land when --out is not given. Relative to
# the directory the tool is run from, and meant to be gitignored whole:
# every run drops another timestamped file, and they are output, not
# source.
OUT_DIR = "schema_diff"

OBJECT_TYPES = ("TABLE", "COLUMN", "COLLATION", "INDEX", "PK", "FK",
                "RENAME_CLEANUP")
ACTION_TYPES = ("CREATE", "ADD", "MODIFY", "DROP", "RENAME")

# Execution order. COLLATION sits at 8: after the columns exist (7),
# before any key is built on top of them (9, 10, 11).
PRIORITY = {
    ("FK",             "DROP"):    1,
    ("INDEX",          "DROP"):    2,
    ("PK",             "DROP"):    3,
    ("PK",             "MODIFY"):  3,
    ("COLUMN",         "DROP"):    4,
    ("TABLE",          "DROP"):    5,
    ("TABLE",          "CREATE"):  6,
    ("COLUMN",         "ADD"):     7,
    ("COLUMN",         "MODIFY"):  7,
    ("COLUMN",         "RENAME"):  7,
    ("COLLATION",      "MODIFY"):  8,
    ("PK",             "CREATE"):  9,
    ("INDEX",          "CREATE"): 10,
    ("FK",             "CREATE"): 11,
    ("RENAME_CLEANUP", "MODIFY"): 99,
}
PRIORITY_DEFAULT = 50


class SchemaSyncError(Exception):
    """Every condition that must stop the run."""


def priority_of(object_type: str, action_type: str) -> int:
    return PRIORITY.get((object_type, action_type), PRIORITY_DEFAULT)


# ---------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------

def setup_logging(verbose: bool = False) -> logging.Logger:
    """Configure and return the logger. Idempotent."""
    try:
        from utils.logger import RequestIPFilter
    except ImportError:
        class RequestIPFilter(logging.Filter):
            def filter(self, record):
                record.ip = "-"
                return True

    logger = logging.getLogger("schema_sync")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    if logger.handlers:
        return logger

    fmt = logging.Formatter(
        "%(asctime)s - %(levelname)s - %(ip)s - %(message)s")
    ip_filter = RequestIPFilter()

    fh = RotatingFileHandler(LOG_FILE, maxBytes=10 * 1024 * 1024,
                             backupCount=5, encoding="utf-8")
    fh.setFormatter(fmt)
    fh.addFilter(ip_filter)
    fh.setLevel(logging.DEBUG)
    logger.addHandler(fh)

    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    ch.addFilter(ip_filter)
    ch.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.addHandler(ch)

    return logger


# ---------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------

def connect(database: str = None):
    """Open a connection. autocommit on -- DDL commits implicitly in
    MariaDB regardless, so pretending otherwise would be a lie."""
    cfg = dict(DB_CONFIG_NEW)
    if database:
        cfg["database"] = database
    cfg.setdefault("charset", "utf8mb4")
    cfg["autocommit"] = True
    try:
        return mysql.connector.connect(**cfg)
    except mysql.connector.Error as exc:
        raise SchemaSyncError(
            f"Conectare eșuată la {cfg.get('host')}:{cfg.get('port')} — {exc}"
        ) from exc


def query(conn, sql: str, params=()) -> list:
    """Run a SELECT and return rows as dicts."""
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(sql, params)
        return cur.fetchall()
    finally:
        cur.close()


def server_version(conn) -> tuple:
    """(is_mariadb, version_string). Governs how COLUMN_DEFAULT is read."""
    row = query(conn, "SELECT VERSION() AS v")[0]
    v = row["v"] or ""
    return ("mariadb" in v.lower(), v)


# ---------------------------------------------------------------------
# Control table
# ---------------------------------------------------------------------

def ensure_control_table(conn, logger) -> None:
    """Create schema_diff_log if absent; widen its enums if outdated.

    Replaces what used to be a hand-run ALTER. Everything the package
    needs on the server, the package builds.
    """
    obj_enum = ",".join(f"'{o}'" for o in OBJECT_TYPES)
    act_enum = ",".join(f"'{a}'" for a in ACTION_TYPES)

    rows = query(conn,
                 "SELECT COUNT(*) AS n FROM information_schema.TABLES "
                 "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'schema_diff_log'",
                 (CONTROL_DB,))
    exists = rows[0]["n"] > 0

    cur = conn.cursor()
    try:
        if not exists:
            cur.execute(f"""
                CREATE TABLE `{CONTROL_DB}`.`schema_diff_log` (
                  `id`             BIGINT       NOT NULL AUTO_INCREMENT,
                  `target_db`      VARCHAR(255)     NULL,
                  `table_name`     VARCHAR(255)     NULL,
                  `object_name`    VARCHAR(255)     NULL,
                  `object_type`    ENUM({obj_enum}) NULL,
                  `action_type`    ENUM({act_enum}) NULL,
                  `ddl_sql`        LONGTEXT         NULL,
                  `sync_mode`      ENUM('SAFE','FORCE') NULL,
                  `is_destructive` TINYINT      NOT NULL DEFAULT 0,
                  `priority`       INT          NOT NULL DEFAULT 50,
                  `created_at`     TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  `executed_at`    DATETIME         NULL,
                  `error_msg`      TEXT             NULL,
                  PRIMARY KEY (`id`),
                  KEY `ix_sdl_pending` (`executed_at`, `priority`, `id`)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)
            logger.info("Creat `%s`.`schema_diff_log`.", CONTROL_DB)
            return

        # Existing table: widen the enums and add priority if missing.
        cols = {c["COLUMN_NAME"]: c for c in query(
            conn,
            "SELECT COLUMN_NAME, COLUMN_TYPE FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'schema_diff_log'",
            (CONTROL_DB,))}

        if "object_type" in cols:
            missing = [o for o in OBJECT_TYPES
                       if f"'{o}'" not in cols["object_type"]["COLUMN_TYPE"]]
            if missing:
                cur.execute(f"ALTER TABLE `{CONTROL_DB}`.`schema_diff_log` "
                            f"MODIFY COLUMN `object_type` ENUM({obj_enum}) NULL")
                logger.info("Extins object_type cu: %s", ", ".join(missing))

        if "priority" not in cols:
            cur.execute(f"ALTER TABLE `{CONTROL_DB}`.`schema_diff_log` "
                        f"ADD COLUMN `priority` INT NOT NULL DEFAULT 50 "
                        f"AFTER `is_destructive`")
            logger.info("Adăugată coloana priority.")
    finally:
        cur.close()


def check_prerequisites(conn, logger) -> None:
    """Verify what the run depends on. Raises on failure."""
    rows = query(conn, "SELECT @@sql_mode AS m")
    sql_mode = rows[0]["m"] or ""
    if ("STRICT_TRANS_TABLES" not in sql_mode
            and "STRICT_ALL_TABLES" not in sql_mode):
        raise SchemaSyncError(
            "sql_mode nu conține STRICT_TRANS_TABLES. O conversie de charset "
            "care nu încape ar trunchia datele în tăcere, fără eroare.\n"
            f"  sql_mode curent: {sql_mode or '(gol)'}")

    rows = query(conn,
                 "SELECT COUNT(*) AS n FROM information_schema.SCHEMATA "
                 "WHERE SCHEMA_NAME = %s", (SOURCE_DB,))
    if rows[0]["n"] == 0:
        raise SchemaSyncError(f"Schema {SOURCE_DB} nu există.")

    charsets = query(
        conn,
        "SELECT CHARACTER_SET_NAME cs, COLLATION_NAME co, COUNT(*) n "
        "FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = %s AND CHARACTER_SET_NAME IS NOT NULL "
        "GROUP BY cs, co", (SOURCE_DB,))
    if len(charsets) == 1:
        c = charsets[0]
        logger.info("Sursa %s: %s / %s (%d coloane) — uniformă.",
                    SOURCE_DB, c["cs"], c["co"], c["n"])
    else:
        logger.warning("Sursa %s are %d combinații charset/collation; vor fi "
                       "propagate exact așa cum sunt:", SOURCE_DB, len(charsets))
        for c in charsets:
            logger.warning("    %s / %s — %d coloane", c["cs"], c["co"], c["n"])


def drop_legacy_procedures(conn, logger) -> None:
    """Remove the stored procedures this package replaces.

    Left callable, they would bypass the destructive gate entirely.
    """
    cur = conn.cursor()
    try:
        for name in ("proc_ExecuteSchemaDiff", "proc_SchemaDiff_DDL",
                     "proc_SchemaDiff_CreateTable"):
            for row in query(conn,
                             "SELECT ROUTINE_SCHEMA s FROM "
                             "information_schema.ROUTINES WHERE ROUTINE_NAME "
                             "= %s AND ROUTINE_TYPE = 'PROCEDURE'", (name,)):
                cur.execute(f"DROP PROCEDURE `{row['s']}`.`{name}`")
                logger.info("Ștearsă procedura `%s`.`%s`.", row["s"], name)
    finally:
        cur.close()


# ---------------------------------------------------------------------
# Targets
# ---------------------------------------------------------------------

def discover_targets(conn, logger) -> list:
    """Distinct DbName from AVACONT_COMUN.CAI, restricted to what exists."""
    named = [r["DbName"] for r in query(
        conn, f"SELECT DISTINCT DbName FROM `{COMMON_DB}`.`CAI` "
              f"WHERE DbName IS NOT NULL AND DbName <> '' ORDER BY DbName")]
    if not named:
        raise SchemaSyncError(f"`{COMMON_DB}`.`CAI` nu conține niciun DbName.")

    forbidden = [d for d in named if d in FORBIDDEN_TARGETS]
    if forbidden:
        raise SchemaSyncError(
            "CAI.DbName conține baze interzise ca țintă: "
            f"{', '.join(forbidden)}. Corectați registrul CAI.")

    existing = {r["SCHEMA_NAME"] for r in query(
        conn, "SELECT SCHEMA_NAME FROM information_schema.SCHEMATA")}

    targets = [d for d in named if d in existing]
    for d in (d for d in named if d not in existing):
        logger.warning("CAI listează `%s`, dar baza nu există — ignorată.", d)

    if not targets:
        raise SchemaSyncError("Nicio bază din CAI nu există pe server.")

    logger.info("Ținte: %d — %s", len(targets), ", ".join(targets))
    return targets


def parse_targets(spec: str) -> list:
    targets = [t.strip() for t in spec.split(",") if t.strip()]
    bad = [t for t in targets if t in FORBIDDEN_TARGETS]
    if bad:
        raise SchemaSyncError(f"Ținte interzise: {', '.join(bad)}")
    if not targets:
        raise SchemaSyncError("Lista de ținte este goală.")
    return targets


def verify_targets(conn, targets: list, logger) -> list:
    """Check that every EXPLICITLY named target exists. Raises if not.

    Discovery warns about a database listed in CAI but absent from the
    server and moves on -- the registry is allowed to run ahead of
    reality. A name typed by hand is different: a typo would otherwise
    produce a warning, "Schemele sunt deja sincronizate" and exit code
    0, which reads exactly like a clean run against the right database.
    """
    existing = {r["SCHEMA_NAME"].lower() for r in query(
        conn, "SELECT SCHEMA_NAME FROM information_schema.SCHEMATA")}

    missing = [t for t in targets if t.lower() not in existing]
    if missing:
        raise SchemaSyncError(
            f"Baze inexistente pe server: {', '.join(missing)}.\n"
            f"  Verificați numele. Nu s-a generat și nu s-a executat nimic.")

    logger.info("Ținte (indicate explicit): %d — %s",
                len(targets), ", ".join(targets))
    return targets


def list_unit_databases(conn) -> list:
    """The unit databases that ACTUALLY EXIST, plus what CAI only claims.

    The server is the source, not the registry: every schema in
    information_schema.SCHEMATA whose name starts with three digits, with
    exists=True. CAI is consulted for one thing only -- in_cai, so a real
    database absent from the registry is visible instead of quietly gone.

    A name CAI lists that the server does NOT have is appended with
    exists=False. It can never be a target, and the caller is expected to
    show it disabled: dropping it silently would hide a broken registry
    row, which is exactly the thing worth seeing.

    Reads nothing else and writes nothing: safe to call before the
    control table exists.
    """
    registered = {}
    for r in query(conn, f"SELECT DISTINCT DbName FROM `{COMMON_DB}`.`CAI` "
                         f"WHERE DbName IS NOT NULL AND DbName <> ''"):
        registered[r["DbName"].lower()] = r["DbName"]

    # Every schema, not just the unit ones: a CAI row naming AVACONT_COMUN
    # must not be reported as missing from the server.
    on_server = {r["name"].lower() for r in query(
        conn, "SELECT SCHEMA_NAME AS name FROM information_schema.SCHEMATA")}

    rows = query(conn, "SELECT SCHEMA_NAME AS name "
                       "FROM information_schema.SCHEMATA "
                       "ORDER BY SCHEMA_NAME")

    listed = [{"name": r["name"],
               "in_cai": r["name"].lower() in registered,
               "exists": True}
              for r in rows
              if UNIT_DB_RE.match(r["name"] or "")
              and r["name"] not in FORBIDDEN_TARGETS]

    listed.extend({"name": registered[k], "in_cai": True, "exists": False}
                  for k in sorted(registered) if k not in on_server)

    return listed


# ---------------------------------------------------------------------
# Reading pending work
# ---------------------------------------------------------------------

def fetch_pending(conn, targets: list = None) -> list:
    sql = (f"SELECT id, target_db, table_name, object_name, object_type, "
           f"action_type, ddl_sql, sync_mode, is_destructive, priority "
           f"FROM `{CONTROL_DB}`.`schema_diff_log` "
           f"WHERE executed_at IS NULL "
           f"AND (error_msg IS NULL OR error_msg = '')")
    params = []
    if targets:
        ph = ",".join(["%s"] * (len(targets) + 1))
        sql += f" AND target_db IN ({ph})"
        params = list(targets) + [SOURCE_DB]  # RENAME_CLEANUP marker
    sql += " ORDER BY priority, id"
    return query(conn, sql, params)


def summarise(rows: list) -> str:
    if not rows:
        return "  (nimic în așteptare)"
    counts = {}
    for r in rows:
        key = (r["priority"], r["object_type"], r["action_type"],
               bool(r["is_destructive"]))
        counts[key] = counts.get(key, 0) + 1
    lines = []
    for (prio, obj, act, destr), n in sorted(counts.items()):
        mark = "   [DISTRUCTIV]" if destr else ""
        lines.append(f"  {n:5d}  {obj:<15} {act:<8}{mark}")
    return "\n".join(lines)
