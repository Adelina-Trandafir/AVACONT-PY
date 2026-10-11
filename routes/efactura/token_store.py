# routes/efactura/token_store.py
"""
`EF_Token` and `EF_TokenStart` in AVACONT_COMUN (sql/00EF_02_efactura_comun.sql), slice 00EF-04.

Plain SQL on a cursor the caller owns (the caller opens `get_kbot_connection(COMMON_DB)`, commits and
closes): the steps in tokens.py decide where a transaction starts and ends.

The two token columns are written and read ENCRYPTED (cripto.py); this module never sees a plain token.
Every date is UTC, naive (the column comments say UTC); the only database-clock comparison is the age of
a start row, done in SQL (`NOW()`) so both sides of it use one clock.
"""
import secrets
from datetime import datetime, timezone

from routes.inregistrare import anaf

START_LIFETIME_MINUTES = 15

COLUMNS = (
    "DC", "CUI", "AccessTokenCrypt", "RefreshTokenCrypt", "AccessExpiraLa", "RefreshExpiraLa",
    "DataAutorizare", "DataReinnoire", "AutorizatDe", "CertEticheta", "CertAmprenta",
    "UltimaEroare", "UltimaEroareLa",
)

_SQL_CUI = "SELECT CF FROM Unitati WHERE DC = %s"
_SQL_PURGE = (
    "DELETE FROM EF_TokenStart WHERE DataAdaugare < DATE_SUB(NOW(), INTERVAL %s MINUTE)"
)
_SQL_START = "INSERT INTO EF_TokenStart (State, DC, UN) VALUES (%s, %s, %s)"
# Atomic single use: the row is deleted by the same statement that checks it, so two requests carrying
# the same state cannot both win. The unit and the user must be the ones that asked for it.
_SQL_SPEND = (
    "DELETE FROM EF_TokenStart WHERE State = %s AND DC = %s AND UN = %s "
    "AND DataAdaugare >= DATE_SUB(NOW(), INTERVAL %s MINUTE)"
)
_SQL_ROW = "SELECT " + ", ".join(COLUMNS) + " FROM EF_Token WHERE DC = %s"
_SQL_UPSERT = (
    "INSERT INTO EF_Token (DC, CUI, AccessTokenCrypt, RefreshTokenCrypt, AccessExpiraLa, RefreshExpiraLa, "
    "DataAutorizare, DataReinnoire, AutorizatDe, CertEticheta, CertAmprenta, UltimaEroare, UltimaEroareLa) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, NULL, %s, %s, %s, NULL, NULL) "
    "ON DUPLICATE KEY UPDATE CUI = VALUES(CUI), AccessTokenCrypt = VALUES(AccessTokenCrypt), "
    "RefreshTokenCrypt = VALUES(RefreshTokenCrypt), AccessExpiraLa = VALUES(AccessExpiraLa), "
    "RefreshExpiraLa = VALUES(RefreshExpiraLa), DataAutorizare = VALUES(DataAutorizare), "
    "DataReinnoire = NULL, AutorizatDe = VALUES(AutorizatDe), CertEticheta = VALUES(CertEticheta), "
    "CertAmprenta = VALUES(CertAmprenta), UltimaEroare = NULL, UltimaEroareLa = NULL"
)
_SQL_REFRESHED = (
    "UPDATE EF_Token SET AccessTokenCrypt = %s, RefreshTokenCrypt = %s, AccessExpiraLa = %s, "
    "DataReinnoire = %s, UltimaEroare = NULL, UltimaEroareLa = NULL WHERE DC = %s"
)
_SQL_ERROR = "UPDATE EF_Token SET UltimaEroare = %s, UltimaEroareLa = %s WHERE DC = %s"
_SQL_REFRESH_DEAD = (
    "UPDATE EF_Token SET UltimaEroare = %s, UltimaEroareLa = %s, RefreshExpiraLa = %s WHERE DC = %s"
)


def utc_now():
    """Naive UTC `now`: what every date column of EF_Token holds."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def unit_cui(cursor, dc):
    """The tax code of the unit, digits only (`Unitati.CF` without «RO», spaces, dots); None when the
    unit is unknown or the code is not a plausible tax code."""
    cursor.execute(_SQL_CUI, (dc,))
    row = cursor.fetchone()
    if row is None:
        return None
    cui = anaf.normalize_cf(row[0])
    return cui if anaf.is_valid_cf(cui) else None


def purge_starts(cursor):
    """Old start rows go whenever this module writes to that table (the 15 minute rule of the DDL)."""
    cursor.execute(_SQL_PURGE, (START_LIFETIME_MINUTES,))


def create_start(cursor, dc, username):
    """A new single-use state (32 hex characters) for this unit and user."""
    state = secrets.token_hex(16)
    cursor.execute(_SQL_START, (state, dc, username))
    return state


def spend_start(cursor, state, dc, username):
    """True when `state` was handed to THIS unit and user less than 15 minutes ago and was still unspent;
    the row is gone afterwards, whatever the caller does next."""
    cursor.execute(_SQL_SPEND, (state, dc, username, START_LIFETIME_MINUTES))
    return cursor.rowcount == 1


def read_row(cursor, dc, for_update=False):
    """The EF_Token row of the unit as a dict keyed by column, or None."""
    cursor.execute(_SQL_ROW + (" FOR UPDATE" if for_update else ""), (dc,))
    row = cursor.fetchone()
    return None if row is None else dict(zip(COLUMNS, row))


def save_authorised(cursor, dc, cui, access_crypt, refresh_crypt, access_expires, refresh_expires,
                    username, cert_label, cert_thumbprint):
    """The result of the certificate step: both tokens (encrypted), their dates, who and with what."""
    cursor.execute(_SQL_UPSERT, (
        dc, cui, access_crypt, refresh_crypt, access_expires, refresh_expires,
        utc_now(), username, cert_label, cert_thumbprint,
    ))


def save_refreshed(cursor, dc, access_crypt, refresh_crypt, access_expires):
    """A successful refresh. `RefreshExpiraLa` is NOT touched: whether a new refresh token lives a whole new
    period is not verified, and a warning that comes early is the harmless mistake."""
    cursor.execute(_SQL_REFRESHED, (access_crypt, refresh_crypt, access_expires, utc_now(), dc))


def save_error(cursor, dc, text):
    """A failure that may pass (network, 5xx): remembered for display, the tokens stay as they are."""
    cursor.execute(_SQL_ERROR, (text[:255], utc_now(), dc))


def save_refresh_dead(cursor, dc, text):
    """ANAF said the refresh token is no good: it is over as of now, so the unit must do the certificate
    step again (`RefreshExpiraLa = now` makes the state say so)."""
    now = utc_now()
    cursor.execute(_SQL_REFRESH_DEAD, (text[:255], now, now, dc))
