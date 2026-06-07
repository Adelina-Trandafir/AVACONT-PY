# utils/database.py
import mysql.connector
import logging
from config import DB_CONFIG

logger = logging.getLogger(__name__)


def get_db_connection(db_name=None):
    config = DB_CONFIG.copy()
    if db_name:
        config["database"] = db_name

    # Timeouts: nu modificam DB_CONFIG, setam local
    config.setdefault("connection_timeout", 10)   # seconds: connect
    config.setdefault("read_timeout",        30)   # seconds: asteptare raspuns query
    config.setdefault("write_timeout",       30)   # seconds: asteptare write

    try:
        conn = mysql.connector.connect(**config)
        conn.autocommit = False   # explicit, desi e default False in mysql.connector
        return conn
    except mysql.connector.Error as err:
        logger.error(f"Eroare conectare MySQL (DB: {db_name}): {err}")
        raise