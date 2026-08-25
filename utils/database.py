# utils/database.py
"""
Connections to the two MariaDB servers this API serves.

There are TWO servers, and which one a route talks to is decided by WHO calls it:

  * DB_CONFIG      -- the LEGACY server. The Access/VBA clients still write their
                      data there, so every X-Api-Key route (admin, nomenclatoare,
                      clasificatii, parteneri, salarii, ddf/, ord/, ftp, wfls,
                      forexe/seed, migrare/, schema_sync) keeps using it.
                      Reached through get_db_connection().

  * DB_CONFIG_NEW  -- the K-BOT server. Everything KBot.App does lives here:
                      routes/auth/* and routes/forexe/* (the bearer-token routes,
                      guarded by require_session). Reached through
                      get_kbot_connection().

The two accounts are the same people on both machines (AVACONT, Admin, and the
operators' own e-mail logins) -- only the address differs. That is why nothing
here takes a user name: the caller picks a SERVER, not an identity.

The guard on a route is the rule of thumb, with ONE deliberate exception:
routes/migrare/* and routes/schema_sync/* are X-Api-Key routes that serve
KBot.Migrator, and they stay on DB_CONFIG because the Python half of the
migration is no longer driven -- KBot.Migrator does that work itself, in VB.NET,
against the address in migrator-settings.json.
"""
import mysql.connector
import logging

import config
from config import DB_CONFIG

logger = logging.getLogger(__name__)

# Baza comuna care contine tabelele de login (Unitati, Unitati_Utilizatori,
# Unitati_Ani, Jurnal). Exista pe AMBELE servere; K-BOT o citeste pe cea noua.
COMMON_DB = "AVACONT_COMUN"


def _timeouts(cfg):
    """The same three timeouts on every connection, set on a COPY of the config."""
    cfg.setdefault("connection_timeout", 10)   # seconds: connect
    cfg.setdefault("read_timeout",        30)   # seconds: asteptare raspuns query
    cfg.setdefault("write_timeout",       30)   # seconds: asteptare write
    return cfg


def get_db_connection(db_name=None):
    """
    LEGACY server (DB_CONFIG) -- the machine the Access/VBA clients write to.

    Every X-Api-Key route uses this. K-BOT routes must NOT: they use
    get_kbot_connection() instead.
    """
    config = DB_CONFIG.copy()
    if db_name:
        config["database"] = db_name

    # Timeouts: nu modificam DB_CONFIG, setam local
    _timeouts(config)

    try:
        conn = mysql.connector.connect(**config)
        conn.autocommit = False   # explicit, desi e default False in mysql.connector
        return conn
    except mysql.connector.Error as err:
        logger.error(f"Eroare conectare MySQL (DB: {db_name}): {err}")
        raise


# ---------------------------------------------------------------------------
# K-BOT server (DB_CONFIG_NEW)
# ---------------------------------------------------------------------------
def _kbot_config():
    """
    DB_CONFIG_NEW as a fresh dict, read LAZILY from the config module.

    Lazy on purpose: the offline tests stand a stub `config` module in
    sys.modules that carries only DB_CONFIG, and a module-level
    `from config import DB_CONFIG_NEW` would break their import. Missing is a
    real failure, though -- never a quiet fallback to the legacy server, which
    would send K-BOT's writes to the wrong machine.
    """
    cfg = getattr(config, "DB_CONFIG_NEW", None)
    if cfg is None:
        raise RuntimeError(
            "DB_CONFIG_NEW lipseste din config: serverul K-BOT nu este configurat."
        )
    return dict(cfg)


def kbot_server_address():
    """
    (host, port) of the K-BOT server.

    For the ONE place that cannot use a connection helper: routes/auth/auth.py
    proves an operator's identity by logging in AS them, so it needs the address
    without the service account's credentials.
    """
    cfg = _kbot_config()
    return cfg["host"], cfg.get("port", 3306)


def get_kbot_connection(db_name=None):
    """
    K-BOT server (DB_CONFIG_NEW) -- what KBot.App reads and writes.

    Same shape as get_db_connection(), same service account name, different
    machine. Used by routes/auth/* and routes/forexe/*.
    """
    cfg = _timeouts(_kbot_config())
    if db_name:
        cfg["database"] = db_name

    try:
        conn = mysql.connector.connect(**cfg)
        conn.autocommit = False
        return conn
    except mysql.connector.Error as err:
        logger.error(f"Eroare conectare MySQL K-BOT (DB: {db_name}): {err}")
        raise


def get_kbot_comun_connection():
    """
    AVACONT_COMUN on the K-BOT server -- READING the login tables
    (Unitati, Unitati_Utilizatori, Unitati_Ani).

    The writes to that database (LastSS, Jurnal) keep going through
    get_kbot_connection(COMMON_DB), which is transactional. The two paths stay
    separate because they were separate before, and telling a read apart from a
    write at the call site is worth one extra function.

    HISTORY, because the name changed and the guarantee with it: on the legacy
    server these reads went through a separate read-only account, 'db_reader'
    (READER_DB_CONFIG). That account does NOT exist on the new machine, so the
    read-only guarantee is gone and the name no longer claims one. If 'db_reader'
    is created there later, giving it back is one function -- the callers in
    auth.py are the only ones.

    autocommit=True: a read needs no transaction, and leaving one open would pin
    a snapshot for the life of the connection.
    """
    cfg = _timeouts(_kbot_config())
    cfg["database"] = COMMON_DB

    try:
        conn = mysql.connector.connect(**cfg)
        conn.autocommit = True
        return conn
    except mysql.connector.Error as err:
        logger.error(f"Eroare conectare K-BOT (AVACONT_COMUN): {err}")
        raise
