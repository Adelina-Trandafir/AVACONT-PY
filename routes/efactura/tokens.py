# routes/efactura/tokens.py
"""
The token steps of E-Factura (slice 00EF-04), in the order the plan's «authorise step» table gives them.

    start(dc, un)            step 2  the authorise address + a single-use `state`
    finish(dc, un, ...)      step 5  spend the state, exchange the code, encrypt, store
    stare(dc)                step 6  what the PC may know: dates, certificate label, who, last error
    access_token(dc)         later   a live access token for the server's own ANAF calls (slices 00EF-06/07);
                                     renews it first when it is about to expire. NEVER returned by a route.

The PC never receives a token or a secret. It learns only the dates and labels `stare` returns.

THE ONE DATE THAT MATTERS is `RefreshExpiraLa`: when the refresh token stops working and the certificate
step has to be done again. ANAF's token answer carries `expires_in` for the ACCESS token only, so the server
COMPUTES it: moment of the certificate step + `EF_REFRESH_DAYS` (365). It is an assumption, not a fact; a
refresh does not move it (see token_store.save_refreshed). The PC warns from 7 days before.

Every function opens and closes its own connection to AVACONT_COMUN (`get_kbot_connection(COMMON_DB)`,
transactional) and writes the failures it cannot hide to the server log -- without a token, a code or a
secret in the line.
"""
import logging
from contextlib import contextmanager
from datetime import timedelta

import mysql.connector

from utils.database import COMMON_DB, get_kbot_connection

from . import cripto, ef_config, oauth, token_store

logger = logging.getLogger(__name__)

WARN_DAYS = 7                                   # the PC warns this many days before RefreshExpiraLa
ACCESS_LEEWAY = timedelta(minutes=5)            # an access token this close to its end is renewed first
MAX_LABEL = 255
MAX_THUMBPRINT = 64
CODE_MAX = 4096
SQL_FILE = "sql/00EF_02_efactura_comun.sql"

_ER_NO_SUCH_TABLE = 1146


class EfEroare(RuntimeError):
    """A refusal the route turns into `{"error": message, "reason": reason}` with `status`.
    The message is Romanian and carries no token, code or secret."""

    def __init__(self, message, reason, status):
        super().__init__(message)
        self.reason = reason
        self.status = status


class TokenNecesar(EfEroare):
    """The unit has no usable token: the certificate step has to be done (again)."""

    def __init__(self, message):
        super().__init__(message, "TOKEN_NECESAR", 409)


class TokenIndisponibil(EfEroare):
    """ANAF could not be reached for the renewal; the token itself may well be fine. Try again later."""

    def __init__(self, message):
        super().__init__(message, "ANAF_INDISPONIBIL", 502)


@contextmanager
def _connection():
    """A transactional connection to AVACONT_COMUN: rolled back on any error, always closed.
    A missing table (the DDL was not run) becomes a refusal that names the file to run."""
    conn = get_kbot_connection(COMMON_DB)
    try:
        yield conn
    except mysql.connector.Error as err:
        _rollback(conn)
        if getattr(err, "errno", None) == _ER_NO_SUCH_TABLE:
            raise EfEroare(
                f"Tabelele E-Factura nu există în baza comună. Rulați {SQL_FILE}.",
                "TABELE_LIPSA", 503) from err
        raise
    except BaseException:
        _rollback(conn)
        raise
    finally:
        if conn.is_connected():
            conn.close()


def _rollback(conn):
    try:
        conn.rollback()
    except mysql.connector.Error as err:
        logger.error("[efactura] rollback failed: %s", err)


def _config():
    """The server's client data, or a 503 that names the missing VARIABLES (never a value)."""
    try:
        return ef_config.load()
    except ef_config.EfNotConfigured as err:
        logger.error("[efactura] %s", err)
        raise EfEroare(
            "Serverul nu are configurate datele aplicației ANAF (lipsesc sau sunt greșite: "
            + ", ".join(err.names) + "). Completați /etc/avacont/efactura.env și reporniți serviciul.",
            "EF_NECONFIGURAT", 503) from err


def _crypto(action):
    """Runs a cripto call; its failure becomes the 500 / «redo the certificate step» refusal."""
    try:
        return action()
    except cripto.CriptoError as err:
        logger.error("[efactura] %s", err)
        raise EfEroare(str(err), "CRIPTO", 500) from err


# ---------------------------------------------------------------------------------------------
# step 2
# ---------------------------------------------------------------------------------------------
def start(dc, username):
    """The authorise address the PC must call with its certificate, and the `state` to send back with the code."""
    cfg = _config()
    with _connection() as conn:
        cursor = conn.cursor()
        if token_store.unit_cui(cursor, dc) is None:
            raise EfEroare("Unitatea nu are un cod fiscal valid în evidența K-BOT.", "CUI_LIPSA", 409)
        token_store.purge_starts(cursor)
        state = token_store.create_start(cursor, dc, username)
        conn.commit()
    return {
        "authorize_url": oauth.authorize_url(cfg),
        "state": state,
        "expira_in_secunde": token_store.START_LIFETIME_MINUTES * 60,
    }


# ---------------------------------------------------------------------------------------------
# step 5
# ---------------------------------------------------------------------------------------------
def finish(dc, username, state, code, cert_label, cert_thumbprint):
    """Spends `state`, exchanges `code` for the tokens, stores them encrypted. Answers `stare(dc)`."""
    cfg = _config()
    code = (code or "").strip()
    if not code or len(code) > CODE_MAX:
        raise EfEroare("Codul de autorizare lipsește sau este invalid.", "COD_INVALID", 400)
    cert_label = _short(cert_label, MAX_LABEL)
    cert_thumbprint = _short(cert_thumbprint, MAX_THUMBPRINT)

    with _connection() as conn:
        cursor = conn.cursor()
        cui = token_store.unit_cui(cursor, dc)
        if cui is None:
            raise EfEroare("Unitatea nu are un cod fiscal valid în evidența K-BOT.", "CUI_LIPSA", 409)
        spent = token_store.spend_start(cursor, state, dc, username)
        conn.commit()                       # spent for good, whatever happens to the exchange below
    if not spent:
        raise EfEroare("Cererea de autorizare a expirat sau nu a pornit de aici. Reluați pasul cu certificatul.",
                       "STATE_INVALID", 400)

    try:
        tokens = oauth.exchange_code(cfg, code)
    except oauth.OauthError as err:
        _remember_error(dc, str(err))
        raise EfEroare(str(err), "ANAF_REFUZ" if err.definitive else "ANAF_INDISPONIBIL", 502) from err

    now = token_store.utc_now()
    access_crypt = _crypto(lambda: cripto.encrypt(tokens.access_token, cfg.token_key))
    refresh_crypt = _crypto(lambda: cripto.encrypt(tokens.refresh_token, cfg.token_key))
    with _connection() as conn:
        cursor = conn.cursor()
        token_store.save_authorised(
            cursor, dc, cui, access_crypt, refresh_crypt,
            now + timedelta(seconds=tokens.expires_in), now + timedelta(days=cfg.refresh_days),
            username, cert_label, cert_thumbprint)
        conn.commit()
    return stare(dc)


def _short(text, limit):
    """Display text typed by the PC: control characters out, cut to the column."""
    cleaned = "".join(ch for ch in (text or "") if ch.isprintable()).strip()
    return cleaned[:limit] or None


def _remember_error(dc, text):
    """Writes a failure on the unit's row when it has one (a first authorisation has none yet)."""
    with _connection() as conn:
        cursor = conn.cursor()
        if token_store.read_row(cursor, dc) is not None:
            token_store.save_error(cursor, dc, text)
            conn.commit()


# ---------------------------------------------------------------------------------------------
# step 6
# ---------------------------------------------------------------------------------------------
def stare(dc):
    """What the PC may know about the unit's token. No token, no secret."""
    configured = ef_config.is_configured()
    with _connection() as conn:
        row = token_store.read_row(conn.cursor(), dc)
    return describe(row, configured, token_store.utc_now())


def describe(row, configured, now):
    """The answer of `stare` from a row (or None) -- pure, so it can be read without a database.

    `trebuie_reinnoit`: there is no token, no refresh token, or its date has passed -> the PC offers the certificate step.
    `avertizeaza`:      a token that still works but ends within WARN_DAYS -> the PC shows its notice.
    """
    base = {
        "configurat": bool(configured),
        "exista": row is not None,
        "cui": None,
        "valabil_pana": None,
        "avertizeaza_de_la": None,
        "zile_ramase": None,
        "avertizeaza": False,
        "trebuie_reinnoit": True,
        "certificat": None,
        "autorizat_de": None,
        "autorizat_la": None,
        "ultima_eroare": None,
        "ultima_eroare_la": None,
    }
    if row is None:
        return base

    expires = row["RefreshExpiraLa"]
    usable = bool(row["RefreshTokenCrypt"]) and expires is not None and expires > now
    base.update({
        "cui": row["CUI"],
        "certificat": row["CertEticheta"],
        "autorizat_de": row["AutorizatDe"],
        "autorizat_la": _iso(row["DataAutorizare"]),
        "ultima_eroare": row["UltimaEroare"],
        "ultima_eroare_la": _iso(row["UltimaEroareLa"]),
        "trebuie_reinnoit": not usable,
    })
    if expires is not None:
        warn_from = expires - timedelta(days=WARN_DAYS)
        base["valabil_pana"] = _iso(expires)
        base["avertizeaza_de_la"] = _iso(warn_from)
        base["zile_ramase"] = max(0, (expires - now).days) if usable else 0
        base["avertizeaza"] = usable and now >= warn_from
    return base


def _iso(moment):
    """UTC datetime -> ISO text with a trailing Z; None stays None."""
    return None if moment is None else moment.replace(microsecond=0).isoformat() + "Z"


# ---------------------------------------------------------------------------------------------
# later: the server's own ANAF calls
# ---------------------------------------------------------------------------------------------
def access_token(dc):
    """(cui, access token) for the server's own calls to ANAF (slices 00EF-06 / 00EF-07). Not reachable from any route.

    Renews the access token first when it ends within ACCESS_LEEWAY. The row is LOCKED (`FOR UPDATE`) for
    the whole renewal: ANAF hands out a new refresh token with each renewal, so two workers renewing at the
    same moment would spend the old one twice; the second waits, re-reads, and finds a fresh token.

    Raises TokenNecesar when the unit must do the certificate step (no token, refresh token past its date,
    ANAF refused it, or the stored token cannot be read with the current key) and TokenIndisponibil when
    ANAF could not be reached (the token is left as it was).
    """
    cfg = _config()
    with _connection() as conn:
        cursor = conn.cursor()
        row = token_store.read_row(cursor, dc, for_update=True)
        now = token_store.utc_now()
        if row is None or not row["RefreshTokenCrypt"] or row["RefreshExpiraLa"] is None \
                or row["RefreshExpiraLa"] <= now:
            raise TokenNecesar("Unitatea nu are un token ANAF valabil. Faceți pasul cu certificatul.")

        if row["AccessTokenCrypt"] and row["AccessExpiraLa"] is not None \
                and row["AccessExpiraLa"] - ACCESS_LEEWAY > now:
            token = _crypto_token(lambda: cripto.decrypt(row["AccessTokenCrypt"], cfg.token_key))
            return row["CUI"], token

        old_refresh = _crypto_token(lambda: cripto.decrypt(row["RefreshTokenCrypt"], cfg.token_key))
        try:
            fresh = oauth.refresh(cfg, old_refresh)
        except oauth.OauthError as err:
            if err.definitive:
                token_store.save_refresh_dead(cursor, dc, str(err))
            else:
                token_store.save_error(cursor, dc, str(err))
            conn.commit()
            if err.definitive:
                raise TokenNecesar("ANAF a refuzat reînnoirea tokenului. Faceți din nou pasul cu certificatul.") from err
            raise TokenIndisponibil(str(err)) from err

        access_crypt = _crypto(lambda: cripto.encrypt(fresh.access_token, cfg.token_key))
        refresh_crypt = _crypto(lambda: cripto.encrypt(fresh.refresh_token or old_refresh, cfg.token_key))
        token_store.save_refreshed(
            cursor, dc, access_crypt, refresh_crypt, now + timedelta(seconds=fresh.expires_in))
        conn.commit()
        return row["CUI"], fresh.access_token


def _crypto_token(action):
    """A stored token that cannot be read means the unit has to authorise again."""
    try:
        return action()
    except cripto.CriptoError as err:
        logger.error("[efactura] %s", err)
        raise TokenNecesar(str(err)) from err
