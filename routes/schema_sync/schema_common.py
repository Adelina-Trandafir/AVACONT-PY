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
import sys
from logging.handlers import RotatingFileHandler

import mysql.connector

from config import DB_CONFIG

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

FORBIDDEN_TARGETS = {COMMON_DB, SOURCE_DB, "information_schema",
                     "performance_schema", "mysql", "sys"}

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
    cfg = dict(DB_CONFIG)
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
