# utils/database.py
import mysql.connector
import logging

import config
from config import DB_CONFIG

logger = logging.getLogger(__name__)

# Baza comuna care contine tabelele de login (Unitati, Unitati_Utilizatori,
# Unitati_Ani, Jurnal). Contul read-only 'db_reader' are SELECT doar aici.
COMMON_DB = "AVACONT_COMUN"


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


def get_comun_reader_connection():
    """
    Conexiune READ-ONLY la AVACONT_COMUN prin contul 'db_reader'.

    Este SINGURUL cont folosit de API pentru a CITI tabelele de login
    (Unitati, Unitati_Utilizatori, Unitati_Ani). Nu scrie niciodata — scrierile
    (LastSS, Jurnal) merg pe contul de serviciu din DB_CONFIG via get_db_connection.

    Credentialele vin din READER_DB_CONFIG (config): un dict de tip DB_CONFIG cu
    userul/parola contului 'db_reader'. Citit lazy, la apel, ca importul modulului
    sa nu pice cand config-ul de test nu defineste READER_DB_CONFIG.
    """
    reader_cfg = getattr(config, "READER_DB_CONFIG", None)
    if reader_cfg is None:
        raise RuntimeError(
            "READER_DB_CONFIG lipseste din config: contul read-only 'db_reader' "
            "pentru AVACONT_COMUN nu este configurat."
        )

    cfg = dict(reader_cfg)
    cfg["database"] = COMMON_DB
    cfg.setdefault("connection_timeout", 10)   # seconds: connect
    cfg.setdefault("read_timeout",        30)   # seconds: asteptare raspuns query

    try:
        conn = mysql.connector.connect(**cfg)
        conn.autocommit = True    # read-only: fara tranzactii de gestionat
        return conn
    except mysql.connector.Error as err:
        logger.error(f"Eroare conectare reader (AVACONT_COMUN): {err}")
        raise