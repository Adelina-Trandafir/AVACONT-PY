"""
Limits on the Access sync routes (security plan, S2): they stay behind a key that lives inside an Access
file, so each call is bounded no matter who holds the key.

Applied AFTER `require_access_sync`, per route:

  * the body is at most MAX_BYTES;
  * at most MAX_ROWS rows in one request (the rows of `data` / `clasificatii`, plus the nested
    `rectificari`); more is refused whole with 413, nothing is cut or partly written;
  * `db_name` must be a registered unit (AVACONT_COMUN.Unitati.DC): the key never reaches a database
    that is not a unit (AVACONT_COMUN itself, AVACONT_SURSA, any other schema);
  * ONE successful call per (route, unit, day). A second one the same day is refused with 429. A call that
    ends with an error does not use the day up (the slot is given back), so a failed sync can be retried.

State lives in AVACONT_COMUN.Access_Sync_Limite (sql/SEC02_access_sync_limite.sql). When the table cannot
be reached the route refuses (503): a limit that cannot be checked is not a limit.
"""
import logging
from datetime import datetime, timezone
from functools import wraps

import mysql.connector
from flask import current_app, jsonify, request

from utils.dbname import validate_db_name

logger = logging.getLogger(__name__)

MAX_ROWS = 200
MAX_BYTES = 1024 * 1024
COMMON_DB = "AVACONT_COMUN"
_ER_DUP_ENTRY = 1062


def _today():
    """The calendar day of the business (Romania); UTC when the time zone database is not installed."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Europe/Bucharest")).date()
    except Exception:
        return datetime.now(timezone.utc).date()


def count_rows(payload):
    """Rows in a sync body: the items of `data` or `clasificatii`, plus every nested `rectificari`."""
    total = 0
    for key in ("data", "clasificatii"):
        items = payload.get(key)
        if isinstance(items, list):
            total += len(items)
            for item in items:
                if isinstance(item, dict) and isinstance(item.get("rectificari"), list):
                    total += len(item["rectificari"])
    return total


def _conn():
    from utils.database import get_kbot_connection
    return get_kbot_connection(COMMON_DB)


def _dc_exists(dc):
    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM Unitati WHERE DC = %s", (dc,))
        return cur.fetchone() is not None
    finally:
        conn.close()


def _claim(kind, dc, day, rows):
    """True = the day's slot was free and is now taken; False = already used today."""
    conn = _conn()
    try:
        cur = conn.cursor()
        try:
            cur.execute("INSERT INTO Access_Sync_Limite (Ruta, DC, Zi, Randuri, Moment) "
                        "VALUES (%s, %s, %s, %s, UTC_TIMESTAMP())", (kind, dc, day, rows))
            conn.commit()
            return True
        except mysql.connector.Error as err:
            if getattr(err, "errno", None) == _ER_DUP_ENTRY:
                return False
            raise
    finally:
        conn.close()


def _release(kind, dc, day):
    try:
        conn = _conn()
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM Access_Sync_Limite WHERE Ruta = %s AND DC = %s AND Zi = %s", (kind, dc, day))
            conn.commit()
        finally:
            conn.close()
    except Exception as err:        # the slot stays used until tomorrow: say so, do not hide it
        logger.error("access sync: could not give the slot back (%s, %s): %s", kind, dc, err)


def _refuse(status, message, reason):
    return jsonify({"error": message, "reason": reason}), status


def access_sync_limits(kind):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if request.content_length is not None and request.content_length > MAX_BYTES:
                return _refuse(413, f"Cererea depășește {MAX_BYTES // 1024} KB.", "ACCESS_SYNC_TOO_LARGE")
            payload = request.get_json(silent=True)
            if not isinstance(payload, dict):
                return _refuse(400, "Corp JSON invalid.", "ACCESS_SYNC_BAD_BODY")
            try:
                dc = validate_db_name(payload.get("db_name"))
            except ValueError:
                return _refuse(400, "db_name invalid.", "ACCESS_SYNC_BAD_DB")

            rows = count_rows(payload)
            if rows > MAX_ROWS:
                return _refuse(413, f"Se acceptă cel mult {MAX_ROWS} de rânduri într-o trimitere; "
                                    f"cererea are {rows}. Nu s-a scris nimic.", "ACCESS_SYNC_TOO_MANY_ROWS")
            day = _today()
            try:
                if not _dc_exists(dc):
                    logger.warning("access sync refused: %s is not a registered unit (route %s)", dc, kind)
                    return _refuse(403, "Unitatea nu este înregistrată.", "ACCESS_SYNC_UNKNOWN_UNIT")
                if not _claim(kind, dc, day, rows):
                    return _refuse(429, f"Sincronizarea «{kind}» pentru {dc} a fost deja trimisă azi "
                                        f"({day.strftime('%d.%m.%Y')}). Încercați din nou mâine.",
                                   "ACCESS_SYNC_ALREADY_TODAY")
            except mysql.connector.Error as err:
                logger.error("access sync limits unavailable (%s, %s): %s", kind, dc, err)
                return _refuse(503, "Limitele de trimitere nu pot fi verificate acum. Reîncercați mai târziu.",
                               "ACCESS_SYNC_LIMITS_UNAVAILABLE")

            try:
                response = current_app.make_response(func(*args, **kwargs))
            except Exception:
                _release(kind, dc, day)
                raise
            if response.status_code >= 400:
                _release(kind, dc, day)
            else:
                logger.info("access sync ok: route=%s unit=%s rows=%d", kind, dc, rows)
            return response
        return wrapper
    return decorator
