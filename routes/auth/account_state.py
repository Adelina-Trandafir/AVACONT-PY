# routes/auth/account_state.py
"""
Per-account password state (security plan S1): MustChangePassword, PasswordChangedAt, AuthVersion.

One row per MariaDB/operator account in AVACONT_COMUN.Conturi_Securitate (sql/SEC01), shared by
every unit of the account. A missing row means "not flagged, version 1". A missing TABLE is an
operator error (DDL not run): it is logged loudly and read as "not flagged", so a deploy that
precedes the DDL does not lock everybody out; it does NOT silently enforce anything.

AuthVersion goes up on every forced reset and on every password change; a session minted under
an older version is dead (see current_version / guard.py).
"""
import logging
import threading
import time

import mysql.connector

from utils.database import get_kbot_connection, get_kbot_comun_connection

logger = logging.getLogger(__name__)

COMMON_DB = "AVACONT_COMUN"
_ER_NO_SUCH_TABLE = 1146
_VERSION_CACHE_TTL = 30          # seconds a version lookup is reused by the guard
_cache = {}                       # username -> (version, fetched_at)
_cache_lock = threading.Lock()


def get_state(username):
    """-> (must_change: bool, auth_version: int). Raises mysql.connector.Error on real DB errors."""
    conn = None
    try:
        conn = get_kbot_comun_connection()
        cur = conn.cursor()
        cur.execute("SELECT MustChangePassword, AuthVersion FROM Conturi_Securitate WHERE UN = %s",
                    (username,))
        row = cur.fetchone()
    except mysql.connector.Error as err:
        if getattr(err, "errno", None) == _ER_NO_SUCH_TABLE:
            logger.error("Conturi_Securitate missing: run sql/SEC01_conturi_securitate.sql "
                         "(password-change enforcement is OFF until then)")
            return False, 1
        raise
    finally:
        if conn is not None and conn.is_connected():
            conn.close()
    if row is None:
        return False, 1
    return bool(row[0]), int(row[1])


def current_version(username):
    """AuthVersion for the guard, cached _VERSION_CACHE_TTL seconds. On a DB error the last
    known value is used; with none known the error is raised (the guard answers 503)."""
    now = time.time()
    with _cache_lock:
        hit = _cache.get(username)
        if hit is not None and now - hit[1] < _VERSION_CACHE_TTL:
            return hit[0]
    try:
        _must, version = get_state(username)
    except mysql.connector.Error:
        if hit is not None:
            logger.warning("AuthVersion lookup failed for a session, using the last known value")
            return hit[0]
        raise
    with _cache_lock:
        _cache[username] = (version, now)
    return version


def _forget(username):
    with _cache_lock:
        _cache.pop(username, None)


def _write(sql, username):
    conn = None
    try:
        conn = get_kbot_connection(COMMON_DB)
        cur = conn.cursor()
        cur.execute(sql, (username,))
        conn.commit()
    except mysql.connector.Error:
        if conn is not None:
            try:
                conn.rollback()
            except mysql.connector.Error:
                pass
        raise
    finally:
        if conn is not None and conn.is_connected():
            conn.close()
    _forget(username)


def mark_must_change(username):
    """Administrator reset: flag the account and bump AuthVersion (old sessions die)."""
    _write("INSERT INTO Conturi_Securitate (UN, MustChangePassword, AuthVersion) VALUES (%s, 1, 2) "
           "ON DUPLICATE KEY UPDATE MustChangePassword = 1, AuthVersion = AuthVersion + 1", username)


def complete_change(username):
    """After a successful change: clear the flag, stamp the time, bump AuthVersion."""
    _write("INSERT INTO Conturi_Securitate (UN, MustChangePassword, PasswordChangedAt, AuthVersion) "
           "VALUES (%s, 0, UTC_TIMESTAMP(), 2) "
           "ON DUPLICATE KEY UPDATE MustChangePassword = 0, PasswordChangedAt = UTC_TIMESTAMP(), "
           "AuthVersion = AuthVersion + 1", username)
