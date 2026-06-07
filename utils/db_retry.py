# utils/db_retry.py
"""
Conexiune DB + retry pe deadlock / lock timeout.

Relocat din ddf.py. Doua corecturi fata de monolit:
  1. _run_with_retry era definit de DOUA ori in ddf.py — aici exista o
     singura copie.
  2. Foloseste mysql.connector si time fara ca acestea sa fie importate in
     ddf.py — aici sunt importate corect, altfel orice deadlock real ar fi
     aruncat NameError in loc de retry.
"""
import logging
import time

import mysql.connector

from utils.database import get_db_connection

logger = logging.getLogger(__name__)

MAX_DEADLOCK_RETRIES = 3
DEADLOCK_RETRY_SLEEP = 0.2               # secunde (se inmulteste cu attempt)

ERRNO_DEADLOCK       = 1213
ERRNO_LOCK_TIMEOUT   = 1205


def _get_conn_cursor(data: dict):
    """Deschide conexiune si cursor dictionary pentru db_name din payload."""
    conn   = get_db_connection(data.get("db_name"))
    cursor = conn.cursor(dictionary=True)
    return conn, cursor


def _close(conn, cursor):
    """Inchide cursor si conexiune silentios (ignore erori la close)."""
    for obj in (cursor, conn):
        if obj:
            try:
                obj.close()
            except Exception:
                pass


def _run_with_retry(operation, data: dict):
    """
    Executa operation(cursor) -> result in tranzactie explicita.
    Retry automat la deadlock (errno 1213) sau lock timeout (errno 1205),
    cu sleep progresiv: attempt * DEADLOCK_RETRY_SLEEP secunde.

    IntegrityError (FK violation, duplicate key) -> raise imediat, fara retry
    (sunt erori de date, nu de concurenta).

    Orice alta exceptie -> rollback + raise.
    """
    last_err = None
    for attempt in range(1, MAX_DEADLOCK_RETRIES + 1):
        conn = cursor = None
        try:
            conn, cursor = _get_conn_cursor(data)
            result = operation(cursor)
            conn.commit()
            return result

        except mysql.connector.errors.IntegrityError as e:
            # Eroare de integritate (FK, UNIQUE) — nu are sens sa reincerci
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
            raise ValueError(f"Eroare integritate DB: {e.msg}") from e

        except mysql.connector.errors.DatabaseError as e:
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass

            if e.errno in (ERRNO_DEADLOCK, ERRNO_LOCK_TIMEOUT):
                last_err = e
                if attempt < MAX_DEADLOCK_RETRIES:
                    sleep_time = DEADLOCK_RETRY_SLEEP * attempt
                    logger.warning(
                        f"[RETRY] errno={e.errno} attempt={attempt}/{MAX_DEADLOCK_RETRIES} "
                        f"sleep={sleep_time:.2f}s"
                    )
                    time.sleep(sleep_time)
                    continue
                raise ValueError(
                    f"Deadlock/lock timeout dupa {MAX_DEADLOCK_RETRIES} "
                    f"incercari: {e.msg}"
                ) from e

            raise

        except Exception:
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
            raise

        finally:
            _close(conn, cursor)

    raise last_err