"""
The web area of registered users (slice 0110-03): READ-ONLY viewing of the data that is
already on the MariaDB server. Nothing here writes business data.

    GET  /portal                                 the page
    POST /api/portal/login      {email, parola}  -> password checked, code mailed
    POST /api/portal/verifica   {pending, cod}   -> the portal session token
    POST /api/portal/logout
    GET  /api/portal/me                          -> who, units, active unit, periods
    POST /api/portal/unit       {db_name}        -> opens one of the user's units
    GET  /api/portal/session/info                -> idle window, extensions (for the page's timer)
    GET  /api/portal/session/check               -> is the session alive?
    POST /api/portal/session/extend              -> slides the window, counts the extension
    POST /api/portal/session/renew               -> restarts a live session's duration on page load

WHO GETS IN. Two factors, as for the password change (slice 0072) and the operator page
(slice 0075-05): the K-BOT password, proven by a MariaDB login AS the user (auth.verify_operator,
the password is thrown away at once), then a six-digit code mailed to that address. Any role
may enter; the user must have at least one unit in Unitati_Utilizatori.
(A client certificate, nginx mTLS, is slice 0110-04 -- routes/portal/certificat.py -- and
skips both; the password + code route stays as the fallback.)

WHY NOT THE K-BOT BEARER SESSION. That token is for the VB.NET client and every data route of
the desktop app trusts it. The portal session is a note in the same STORE under its own name,
carried in its own header (X-Portal-Token): a K-BOT token is not a portal token and a portal
token is not a K-BOT token, so neither can be replayed on the other side. No cookie, so no
CSRF; the token lives in sessionStorage of the page.

WHICH UNIT. The session note holds the unit the user opened. /api/portal/unit accepts only a
unit the user really has (read again from Unitati_Utilizatori, never from the client), so a
portal data route that reads `g.portal["db_name"]` can never be pointed at another unit.

SESSION TIME. PORTAL_SESSION_MINUTES in config.py defines the duration (default 30 minutes).
Loading a page renews a live session; background checks and normal activity do not restart
its deadline. Expired sessions require authentication again. The authentication-code trust
window stays separate. State is in memory/Redis of STORE; the rate limiter is in-process.
"""
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from functools import wraps

import mysql.connector
from flask import Blueprint, current_app, g, make_response, request, send_from_directory

from routes.auth import mailer
from routes.auth.auth import log_action, verify_operator
from routes.auth.ratelimit import LIMITER
from routes.auth.session_store import STORE
from routes.portal import drepturi
from routes.portal.session_settings import SESSION_SECONDS
from utils.database import get_kbot_comun_connection

logger = logging.getLogger(__name__)

portal_bp = Blueprint("portal", __name__)

TOKEN_HEADER = "X-Portal-Token"

_NOTE_PENDING = "portal_pending"        # password right, code not typed yet
_NOTE_SESSION = "portal_session"        # both factors passed
_NOTE_TRUST = "portal_trust"            # the code was typed in this browser: password alone is enough until the hour ends

CODE_TTL = 10 * 60
CODE_MAX_ATTEMPTS = 5
PORTAL_MAX = SESSION_SECONDS            # from login / the latest page-load renewal
PORTAL_IDLE = PORTAL_MAX
PORTAL_MAX_EXTENSIONS = 0
PORTAL_TRUST = 60 * 60                  # code reuse after password login is independent of session renewal

_PAGE_FILE = "portal.html"

# Slice 0110-04. With `ssl_verify_client optional` nginx asks for a client certificate on EVERY
# connection to that host, so the picker would also show on the public pages. When the
# operator serves the certificate calls from a host of their own (https://cert.example.ro), these
# two environment values say so; both empty = the same host (fine for a try-out):
#   PORTAL_CERT_ORIGIN  that host's origin: the page calls /api/portal/certificat/* there, and the
#                       CSP lets the page connect to it.
#   PORTAL_SITE_ORIGIN  the site's own origin: the certificate host answers its cross-origin calls.
_CERT_ORIGIN_PLACEHOLDER = "__CERT_ORIGIN__"


def _origin_setting(name):
    value = (os.environ.get(name) or "").strip().rstrip("/")
    return value if value.startswith("https://") else ""


def _page_headers():
    connect = "'self'"
    cert_origin = _origin_setting("PORTAL_CERT_ORIGIN")
    if cert_origin:
        connect += " " + cert_origin
    return {
        "Content-Security-Policy": (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            f"img-src 'self' data:; connect-src {connect}; frame-ancestors 'none'; "
            "base-uri 'none'; form-action 'none'"
        ),
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "same-origin",
        "Cache-Control": "no-store",
    }

_MSG_RATE = "Prea multe încercări eșuate. Reîncercați peste 15 minute."
_MSG_SESSION = "Sesiunea a expirat. Autentificați-vă din nou."
_MSG_MAIL_OFF = "Trimiterea e-mailului nu este configurată pe server. Contactați-ne."


def _json(payload, status=200):
    """JSON with LITERAL diacritics, whatever the app-wide setting is."""
    body = json.dumps(payload, ensure_ascii=False)
    return current_app.response_class(body, status=status, mimetype="application/json")


def _fail(reason, message, status):
    return _json({"error": message, "reason": reason}, status)


def _hash(code):
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _ip():
    # ProxyFix is active in main.py: the real client, not nginx.
    return request.remote_addr


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------
@portal_bp.route("/portal", methods=["GET"])
@portal_bp.route("/portal/", methods=["GET"])
def portal_page():
    path = os.path.join(current_app.static_folder, _PAGE_FILE)
    with open(path, "r", encoding="utf-8") as handle:
        html = handle.read().replace(_CERT_ORIGIN_PLACEHOLDER, _origin_setting("PORTAL_CERT_ORIGIN"))
    response = make_response(html)
    response.mimetype = "text/html"
    response.headers.update(_page_headers())
    return response


@portal_bp.after_request
def _cert_cors(response):
    """The certificate calls come from the site's page to the certificate host (another origin):
    answer them for that one origin only, with the one header the page adds."""
    site = _origin_setting("PORTAL_SITE_ORIGIN")
    if site and request.path.startswith("/api/portal/certificat/") \
            and request.headers.get("Origin") == site:
        response.headers["Access-Control-Allow-Origin"] = site
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Portal-Token"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Vary"] = "Origin"
    return response


# ---------------------------------------------------------------------------
# The units of a user, from the common database (never from the client)
# ---------------------------------------------------------------------------
def _user_units(email):
    conn = None
    try:
        conn = get_kbot_comun_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT Unitati.DC AS DC, Unitati.NumeUnitate AS NumeUnitate, Unitati.CF AS CF, "
            "Unitati_Utilizatori.Rol AS Rol "
            "FROM Unitati_Utilizatori INNER JOIN Unitati ON Unitati_Utilizatori.DC = Unitati.DC "
            "WHERE Unitati_Utilizatori.UN = %s ORDER BY Unitati.NumeUnitate",
            (email,),
        )
        return cur.fetchall()
    finally:
        if conn is not None and conn.is_connected():
            conn.close()


def _unit_periods(db_name):
    conn = None
    try:
        conn = get_kbot_comun_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT AN, SS, CodProgram FROM Unitati_Ani WHERE DC = %s ORDER BY AN DESC, SS",
            (db_name,),
        )
        return cur.fetchall()
    finally:
        if conn is not None and conn.is_connected():
            conn.close()


# ---------------------------------------------------------------------------
# Sign-in: password, then the mailed code
# ---------------------------------------------------------------------------
@portal_bp.route("/api/portal/login", methods=["POST"])
def portal_login():
    body = request.get_json(silent=True) or {}
    email = str(body.get("email") or "").strip().lower()
    parola = body.get("parola") or ""
    if not email or not parola or not isinstance(parola, str):
        return _fail("DATE_INCOMPLETE", "Introduceți adresa de e-mail și parola.", 400)

    ip = _ip()
    if LIMITER.is_blocked(ip, email):
        return _fail("RATE_LIMITED", _MSG_RATE, 429)
    if not mailer.is_configured():
        return _fail("MAIL_NOT_CONFIGURED", _MSG_MAIL_OFF, 503)

    try:
        right = verify_operator(email, parola)
    except mysql.connector.Error as err:
        logger.error("portal login: password check failed for %s: %s",
                     mailer.mask_address(email), err)
        return _fail("DB_ERROR", "Parola nu a putut fi verificată. Reîncercați.", 500)
    if not right:
        LIMITER.record_failure(ip, email)
        log_action(email, None, "PORTAL_AUTH_FAIL", rezultat="EROARE", ip=ip)
        return _fail("CREDENTIALE", "Utilizator sau parolă incorecte.", 401)

    try:
        units = _user_units(email)
    except mysql.connector.Error as err:
        logger.error("portal login: units of %s unreadable: %s", mailer.mask_address(email), err)
        return _fail("DB_ERROR", "Unitățile nu au putut fi citite. Reîncercați.", 500)
    if not units:
        # Right password, no unit: counted, like a wrong one -- nobody probes accounts for free.
        LIMITER.record_failure(ip, email)
        log_action(email, None, "PORTAL_DENIED", detalii="no unit", rezultat="EROARE", ip=ip)
        return _fail("FARA_UNITATI", "Contul nu are nicio unitate asociată.", 403)

    # The code of the last hour is still good in this browser: password + that browser's trust token are enough.
    trusted = _trusted_since(email, body.get("trust"))
    if trusted is not None:
        LIMITER.record_success(ip, email)
        return open_session(email, units, ip, how="trusted code", issued_at=trusted)

    code = f"{secrets.randbelow(1_000_000):06d}"
    try:
        mailer.send_portal_code(email, code, CODE_TTL // 60)
    except Exception as err:            # smtplib, socket: nothing arrived, say so
        logger.error("portal code mail to %s failed: %s", mailer.mask_address(email), err)
        return _fail("MAIL_FAILED", "E-mailul cu codul nu a putut fi trimis. Reîncercați.", 502)

    LIMITER.record_success(ip, email)
    pending = secrets.token_urlsafe(32)
    STORE.put_note(pending, _NOTE_PENDING,
                   {"email": email, "hash": _hash(code), "attempts": 0, "issued_at": time.time()},
                   CODE_TTL)
    return _json({"pending": pending, "email_masked": mailer.mask_address(email),
                  "expires_in": CODE_TTL})


@portal_bp.route("/api/portal/verifica", methods=["POST"])
def portal_verifica():
    body = request.get_json(silent=True) or {}
    pending = str(body.get("pending") or "").strip()
    code = str(body.get("cod") or "").strip()
    note = STORE.get_note(pending, _NOTE_PENDING) if pending else None
    if note is None:
        return _fail("COD_EXPIRAT", "Codul a expirat. Autentificați-vă din nou.", 401)
    if not code:
        return _fail("COD_ABSENT", "Introduceți codul primit pe e-mail.", 400)

    email = note["email"]
    ip = _ip()
    if LIMITER.is_blocked(ip, email):
        return _fail("RATE_LIMITED", _MSG_RATE, 429)

    if not hmac.compare_digest(note.get("hash", ""), _hash(code)):
        LIMITER.record_failure(ip, email)
        attempts = int(note.get("attempts", 0)) + 1
        if attempts >= CODE_MAX_ATTEMPTS:
            STORE.delete_note(pending, _NOTE_PENDING)
            log_action(email, None, "PORTAL_AUTH_FAIL", detalii="code attempts exhausted",
                       rezultat="EROARE", ip=ip)
            return _fail("COD_EPUIZAT", "Prea multe coduri greșite. Autentificați-vă din nou.", 401)
        note["attempts"] = attempts
        left = CODE_TTL - int(time.time() - float(note.get("issued_at", time.time())))
        STORE.put_note(pending, _NOTE_PENDING, note, max(1, left))
        return _fail("COD_GRESIT", "Codul este greșit.", 400)

    STORE.delete_note(pending, _NOTE_PENDING)
    try:
        units = _user_units(email)
    except mysql.connector.Error as err:
        logger.error("portal verify: units of %s unreadable: %s", mailer.mask_address(email), err)
        return _fail("DB_ERROR", "Unitățile nu au putut fi citite. Reîncercați.", 500)
    if not units:
        return _fail("FARA_UNITATI", "Contul nu are nicio unitate asociată.", 403)

    LIMITER.record_success(ip, email)
    now = time.time()
    trust = secrets.token_urlsafe(32)
    STORE.put_note(trust, _NOTE_TRUST, {"email": email, "issued_at": now}, PORTAL_TRUST)
    return open_session(email, units, ip, issued_at=now, trust=trust)


def _trusted_since(email, trust):
    """When the code was typed in this browser, if that was less than an hour ago and for this account."""
    trust = str(trust or "").strip()
    note = STORE.get_note(trust, _NOTE_TRUST) if trust else None
    if note is None or note.get("email") != email:
        return None
    issued = float(note.get("issued_at", 0))
    return issued if time.time() < issued + PORTAL_TRUST else None


def open_session(email, units, ip, how=None, issued_at=None, trust=None):
    """Both factors passed (password + code, or an enrolled certificate): the portal session.
    `how` is only the journal's remark. `issued_at` retains when the code was typed.
    A user with one unit gets it opened at once."""
    token = secrets.token_urlsafe(32)
    issued_at = time.time() if issued_at is None else issued_at
    started = time.time()
    note = {"email": email, "issued_at": issued_at, "renewed_at": started, "extensions": 0, "db_name": None, "role": None}
    if len(units) == 1:
        note["db_name"], note["role"] = units[0]["DC"], units[0]["Rol"]
    ttl = PORTAL_MAX
    note["idle_until"] = started + ttl
    STORE.put_note(token, _NOTE_SESSION, note, ttl)
    log_action(email, note["db_name"], "PORTAL_LOGIN", detalii=how, ip=ip)
    payload = {"token": token, "expires_in": ttl}
    if trust:
        payload["trust"] = trust
    return _json(payload)


# ---------------------------------------------------------------------------
# Session: guard, logout, who am I, timer routes
# ---------------------------------------------------------------------------
def _session_deadline(note):
    return float(note.get("renewed_at", note.get("issued_at", 0))) + PORTAL_MAX


def _live_session():
    """`(token, note, None)` for a live portal session, else `(None, None, <401 response>)`.
    Does NOT slide the window: the callers decide (everything but /session/check does)."""
    token = (request.headers.get(TOKEN_HEADER) or "").strip()
    note = STORE.get_note(token, _NOTE_SESSION) if token else None
    if note is None:
        return None, None, _fail("SESIUNE_EXPIRATA", _MSG_SESSION, 401)
    if time.time() >= _session_deadline(note):
        STORE.delete_note(token, _NOTE_SESSION)
        return None, None, _fail("SESIUNE_EXPIRATA", _MSG_SESSION, 401)
    return token, note, None


def _slide(token, note):
    """Renews the idle window, never past the absolute cap."""
    # Preserve a renewal completed while a data request was using an older Redis snapshot.
    current = STORE.get_note(token, _NOTE_SESSION)
    if current and _session_deadline(current) > _session_deadline(note):
        note["renewed_at"] = current.get("renewed_at", current.get("issued_at", 0))
    left_to_cap = _session_deadline(note) - time.time()
    ttl = max(1, int(min(PORTAL_IDLE, left_to_cap)))
    note["idle_until"] = time.time() + ttl
    STORE.put_note(token, _NOTE_SESSION, note, ttl)
    return ttl


def require_portal_session(fn):
    """
    Guard for the portal's data routes (slice 0110-06). Sets `g.portal` to
    {"email", "db_name", "role"} and slides the idle window. A session with no unit opened
    yet is refused with 409 UNITATE_NEDESCHISA: a data route never runs without a unit.
    """
    @wraps(fn)
    def wrapper(*args, **kwargs):
        token, note, refusal = _live_session()
        if refusal is not None:
            return refusal
        if not note.get("db_name"):
            return _fail("UNITATE_NEDESCHISA", "Alegeți unitatea.", 409)
        _slide(token, note)
        g.portal = {"email": note["email"], "db_name": note["db_name"], "role": note.get("role")}
        return fn(*args, **kwargs)
    return wrapper


@portal_bp.route("/api/portal/logout", methods=["POST"])
def portal_logout():
    token = (request.headers.get(TOKEN_HEADER) or "").strip()
    if token:
        note = STORE.get_note(token, _NOTE_SESSION)
        STORE.delete_note(token, _NOTE_SESSION)
        if note:
            log_action(note.get("email"), note.get("db_name"), "PORTAL_LOGOUT", ip=_ip())
    return _json({"ok": True})


@portal_bp.route("/api/test-mode", methods=["GET"])
def test_mode():
    """The ONE switch of the pages' test mode (console logs, debug tools). Public, read-only. Read from config.py (TEST_MODE = True / False)
    on the server; when config.py has no such line, from the KBOT_TEST_MODE environment variable; when neither is set it is ON.
    config.py is read when the service starts: restart it after a change."""
    try:
        import config
        value = getattr(config, "TEST_MODE", None)
    except ImportError:
        value = None
    if value is None:
        value = os.environ.get("KBOT_TEST_MODE", "1")
    on = value if isinstance(value, bool) else str(value).strip().lower() not in ("0", "false", "off", "no")
    response = _json({"test_mode": on})
    response.headers["Cache-Control"] = "no-store"
    return response


@portal_bp.route("/api/portal/me", methods=["GET"])
def portal_me():
    token, note, refusal = _live_session()
    if refusal is not None:
        return refusal
    try:
        units = _user_units(note["email"])
        periods = _unit_periods(note["db_name"]) if note.get("db_name") else []
        sections = drepturi.sections_of(note["email"], note.get("db_name"))
        operations = {k: sorted(drepturi.operations_of(note["email"], note.get("db_name"), k)) for k in sections}
    except mysql.connector.Error as err:
        logger.error("portal me: read failed for %s: %s", mailer.mask_address(note["email"]), err)
        return _fail("DB_ERROR", "Datele contului nu au putut fi citite.", 500)
    _slide(token, note)
    return _json({"email": note["email"], "units": units, "db_name": note.get("db_name"),
                  "role": note.get("role"), "periods": periods, "sections": sections,
                  "operations": operations})


@portal_bp.route("/api/portal/unit", methods=["POST"])
def portal_unit():
    token, note, refusal = _live_session()
    if refusal is not None:
        return refusal
    db_name = str((request.get_json(silent=True) or {}).get("db_name") or "").strip()
    try:
        units = _user_units(note["email"])
        mine = next((u for u in units if u["DC"] == db_name), None)
        if mine is None:
            log_action(note["email"], db_name or None, "PORTAL_DENIED", detalii="unit not owned",
                       rezultat="EROARE", ip=_ip())
            return _fail("UNITATE_INTERZISA", "Acces interzis pentru această unitate.", 403)
        periods = _unit_periods(db_name)
        sections = drepturi.sections_of(note["email"], db_name)
    except mysql.connector.Error as err:
        logger.error("portal unit: read failed for %s: %s", mailer.mask_address(note["email"]), err)
        return _fail("DB_ERROR", "Unitatea nu a putut fi deschisă.", 500)
    note["db_name"], note["role"] = mine["DC"], mine["Rol"]
    _slide(token, note)
    log_action(note["email"], db_name, "PORTAL_UNIT", ip=_ip())
    return _json({"db_name": db_name, "role": mine["Rol"], "periods": periods, "sections": sections})


def _remaining_idle(note):
    """Seconds left on the idle window, from the moment written at the last slide. Read-only."""
    return max(0, int(min(float(note.get("idle_until", 0)), _session_deadline(note)) - time.time()))


@portal_bp.route("/api/portal/session/renew", methods=["POST"])
def portal_session_renew():
    token, note, refusal = _live_session()
    if refusal is not None:
        return refusal
    now = time.time()
    note["renewed_at"] = now
    note["idle_until"] = now + PORTAL_MAX
    note["extensions"] = 0
    STORE.put_note(token, _NOTE_SESSION, note, PORTAL_MAX)
    return _json({"authenticated": True, "expires_in": PORTAL_MAX,
                  "max_extensions": PORTAL_MAX_EXTENSIONS, "extensions": 0})


@portal_bp.route("/api/portal/session/info", methods=["GET"])
def portal_session_info():
    token, note, refusal = _live_session()
    if refusal is not None:
        return _json({"authenticated": False}, 401)
    ttl = _slide(token, note)
    return _json({"authenticated": True, "expires_in": ttl,
                  "max_extensions": PORTAL_MAX_EXTENSIONS,
                  "extensions": int(note.get("extensions", 0))})


@portal_bp.route("/api/portal/session/check", methods=["GET"])
def portal_session_check():
    # Does not slide: the page asks it just before warning, to learn the session is still alive.
    token, note, refusal = _live_session()
    if refusal is not None:
        return _json({"authenticated": False}, 401)
    return _json({"authenticated": True, "expires_in": _remaining_idle(note)})


@portal_bp.route("/api/portal/session/extend", methods=["POST"])
def portal_session_extend():
    token, note, refusal = _live_session()
    if refusal is not None:
        return refusal
    if int(note.get("extensions", 0)) >= PORTAL_MAX_EXTENSIONS:
        return _fail("PRELUNGIRI_EPUIZATE", "Nu mai puteți prelungi sesiunea. Autentificați-vă din nou.", 409)
    note["extensions"] = int(note.get("extensions", 0)) + 1
    ttl = _slide(token, note)
    return _json({"ok": True, "new_expires_in": ttl, "extensions": note["extensions"]})
