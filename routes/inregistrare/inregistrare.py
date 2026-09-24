# routes/inregistrare/inregistrare.py
"""
The first three calls of the self-service registration page (slice 0075-01,
plan 5.2 and 5.3).

    POST /api/inregistrare/anaf      {cf}     -> opens a registration, pre-fills it
    POST /api/inregistrare/cod       {email}  -> mails a six-digit code
    POST /api/inregistrare/verifica  {cod}    -> proves the applicant reads that inbox

Everything here runs BEFORE anyone is authenticated, on a page anybody on the
internet can open. So, per the plan: the rate limiter on every route, parameterized
SQL only, a reason code beside every Romanian message, and no exception swallowed --
each one is logged where it happens and reported honestly to the caller.

THE TOKEN. `/anaf` mints it; the two routes after it want it back in the
`X-Registration-Token` header. A header rather than the body because 5.4 (`/nume`)
and the later nomenclator calls are GETs, and one way of carrying it beats two.
Where the registration lives and why it starts at the ANAF lookup: routes/
inregistrare/store.py.

THE CODE. Same machinery as the password change of slice 0072 (auth.py): six digits
from `secrets`, only the SHA-256 hash stored, `hmac.compare_digest` to compare, ten
minutes, five wrong tries and the code is thrown away. The one difference is where
it is kept -- a note on the registration token instead of a note on a session.

WHAT THE TWO DATABASE CHECKS ARE FOR. `/anaf` refuses a fiscal code that already has
a database (decision D11): the applicant is an existing customer and needs access,
not a second database. `/cod` refuses an e-mail that is already a MariaDB account or
already appears in `Unitati_Utilizatori`, and says so in words that do not tell a
stranger which of the two it was. That the service account can read `mysql.user` on
the K-BOT server was checked on the machine, 22.09.2026: it can.
"""
import hashlib
import hmac
import json
import logging
import secrets
import time

import mysql.connector
from flask import Blueprint, current_app, request, send_from_directory

from routes.auth import mailer
from routes.auth.ratelimit import LIMITER
from utils.database import get_kbot_comun_connection

from . import anaf as anaf_client
from . import cerere as cerere_mod
from . import nomenclatoare
from . import nume
from . import parola as parola_mod
from . import store

logger = logging.getLogger(__name__)

inregistrare_bp = Blueprint("inregistrare", __name__)

# The header the page sends back on every call after /anaf.
TOKEN_HEADER = "X-Registration-Token"

# Life of one six-digit code, and how many wrong tries it survives (slice 0072).
CODE_TTL = 10 * 60
CODE_MAX_ATTEMPTS = 5

# `Unitati_Utilizatori.UN` is varchar(80) and `sql_mode` has STRICT_TRANS_TABLES, so
# a longer address would be error 1406 at the far end of the wizard. Refused here,
# on the screen where the applicant can still do something about it.
EMAIL_MAX_LENGTH = 80

# Sentences used by more than one route. Everything the applicant reads is Romanian
# with literal diacritics; the reason codes beside them are ASCII and stable.
_MSG_RATE_LIMITED = "Prea multe încercări. Așteptați câteva minute și reluați."
_MSG_TOKEN = "Sesiunea de înregistrare a expirat. Reluați de la codul fiscal."
_MSG_MAIL_NOT_CONFIGURED = (
    "Trimiterea e-mailului nu este configurată pe server. Contactați-ne."
)


def _json(payload, status):
    """JSON with LITERAL diacritics, whatever the app-wide setting is."""
    body = json.dumps(payload, ensure_ascii=False)
    return current_app.response_class(body, status=status, mimetype="application/json")


def _fail(reason, message, status):
    return _json({"error": message, "reason": reason}, status)


def _ip():
    # ProxyFix is active in main.py, so this is the real client address and the
    # limiter's per-IP bucket is per client rather than per nginx.
    return request.remote_addr


# ---------------------------------------------------------------------------
# POST /api/inregistrare/anaf   {cf}
# Step 1. Refuses a fiscal code that already has a database, asks ANAF about the
# rest, and opens the registration around the answer.
# ---------------------------------------------------------------------------
@inregistrare_bp.route("/api/inregistrare/anaf", methods=["POST"])
def inregistrare_anaf():
    data = request.get_json(silent=True) or {}
    cf = anaf_client.normalize_cf(data.get("cf"))
    if not anaf_client.is_valid_cf(cf):
        return _fail(
            "CF_INVALID",
            "Codul fiscal trebuie să conțină între "
            f"{anaf_client.MIN_CF_DIGITS} și {anaf_client.MAX_CF_DIGITS} cifre.",
            400,
        )

    ip = _ip()
    if LIMITER.is_blocked(ip, cf):
        return _fail("RATE_LIMITED", _MSG_RATE_LIMITED, 429)

    try:
        taken = _cf_has_database(cf)
    except mysql.connector.Error as err:
        logger.error("inregistrare_anaf CF check failed for %s: %s", cf, err)
        return _fail(
            "DB_ERROR",
            "Verificarea codului fiscal nu a putut fi făcută. Reîncercați mai târziu.",
            500,
        )
    if taken:
        # D11. Said plainly, because it is good news for the applicant: the database
        # exists, what they need is access to it.
        return _fail(
            "CF_EXISTS",
            "Pentru acest cod fiscal există deja o bază de date. "
            "Contactați-ne pentru acces.",
            409,
        )

    try:
        fields = anaf_client.lookup(cf)
    except anaf_client.AnafNotFound:
        LIMITER.record_failure(ip, cf)
        return _fail(
            "ANAF_NOT_FOUND",
            "Codul fiscal nu a fost găsit în baza de date ANAF.",
            404,
        )
    except anaf_client.AnafIncomplete:
        return _fail(
            "ANAF_INCOMPLETE",
            "Răspunsul ANAF nu conține denumirea unității. Contactați-ne.",
            502,
        )
    except anaf_client.AnafUnavailable:
        return _fail(
            "ANAF_UNAVAILABLE",
            "Serverul ANAF a generat o eroare. Reîncercați mai târziu.",
            502,
        )
    LIMITER.record_success(ip, cf)

    # The applicant may have come back to this screen to look up a different code.
    # The registration they had is finished with, so it goes rather than lingering
    # for the rest of its half hour with a proven e-mail attached to it.
    store.discard((request.headers.get(TOKEN_HEADER) or "").strip())

    token, expires_in = store.create(cf, fields)
    logger.info("registration opened for CF %s from %s", cf, ip)
    return _json(
        {"token": token, "expires_in": expires_in, "cf": cf, "unitate": fields}, 200
    )


# ---------------------------------------------------------------------------
# POST /api/inregistrare/cod   {email}
# Step 2a. Attaches an e-mail to the registration and mails it a code.
# ---------------------------------------------------------------------------
@inregistrare_bp.route("/api/inregistrare/cod", methods=["POST"])
def inregistrare_cod():
    token, registration, refusal = _registration()
    if refusal is not None:
        return refusal

    body = request.get_json(silent=True) or {}
    email = (body.get("email") or "").strip().lower()
    if not _is_email(email):
        return _fail("EMAIL_INVALID", "Introduceți o adresă de e-mail validă.", 400)
    if len(email) > EMAIL_MAX_LENGTH:
        return _fail(
            "EMAIL_TOO_LONG",
            f"Adresa de e-mail nu poate depăși {EMAIL_MAX_LENGTH} de caractere.",
            400,
        )

    ip = _ip()
    if LIMITER.is_blocked(ip, email):
        return _fail("RATE_LIMITED", _MSG_RATE_LIMITED, 429)

    # Said before anything else is spent: a code nobody can receive is not a code.
    if not mailer.is_configured():
        return _fail("MAIL_NOT_CONFIGURED", _MSG_MAIL_NOT_CONFIGURED, 503)

    try:
        taken = _email_taken(email)
    except mysql.connector.Error as err:
        logger.error(
            "inregistrare_cod e-mail check failed for %s: %s",
            mailer.mask_address(email), err,
        )
        return _fail(
            "DB_ERROR",
            "Verificarea adresei nu a putut fi făcută. Reîncercați mai târziu.",
            500,
        )
    if taken:
        # Deliberately vague: a stranger must not learn from this page whether an
        # address is a MariaDB account, a K-BOT user, or neither.
        return _fail(
            "EMAIL_TAKEN",
            "Această adresă de e-mail nu poate fi folosită. "
            "Folosiți altă adresă sau contactați-ne.",
            409,
        )

    code = f"{secrets.randbelow(1_000_000):06d}"
    try:
        mailer.send_registration_code(email, code, CODE_TTL // 60)
    except mailer.MailNotConfigured:
        return _fail("MAIL_NOT_CONFIGURED", _MSG_MAIL_NOT_CONFIGURED, 503)
    except Exception as err:            # smtplib, socket: nothing arrived, say so
        logger.error(
            "registration code mail failed for %s: %s",
            mailer.mask_address(email), err,
        )
        return _fail(
            "MAIL_FAILED",
            "E-mailul cu codul nu a putut fi trimis. Reîncercați mai târziu.",
            502,
        )

    # A fresh code replaces the previous one, and a change of address un-verifies the
    # registration: whoever proved the old inbox proved nothing about this one.
    now = time.time()
    registration["email"] = email
    registration["code_hash"] = _hash_code(code)
    registration["code_attempts"] = 0
    registration["code_expires_at"] = now + CODE_TTL
    registration["verified"] = False
    left = store.save(token, registration)
    if left <= 0:
        return _fail("TOKEN_EXPIRED", _MSG_TOKEN, 401)

    logger.info("registration code sent to %s", mailer.mask_address(email))
    # The honest number: a code cannot outlive the registration that carries it.
    return _json(
        {"email_masked": mailer.mask_address(email),
         "expires_in": min(CODE_TTL, left)},
        200,
    )


# ---------------------------------------------------------------------------
# POST /api/inregistrare/verifica   {cod}
# Step 2b. The code back from the inbox.
# ---------------------------------------------------------------------------
@inregistrare_bp.route("/api/inregistrare/verifica", methods=["POST"])
def inregistrare_verifica():
    token, registration, refusal = _registration()
    if refusal is not None:
        return refusal

    body = request.get_json(silent=True) or {}
    code = (body.get("cod") or "").strip()
    if not code:
        return _fail("CODE_ABSENT", "Introduceți codul primit pe e-mail.", 400)

    stored = registration.get("code_hash") or ""
    if not stored:
        return _fail("CODE_NOT_REQUESTED", "Cereți întâi un cod de confirmare.", 400)

    if time.time() >= float(registration.get("code_expires_at", 0)):
        _forget_code(token, registration)
        return _fail("CODE_EXPIRED", "Codul a expirat. Cereți un cod nou.", 400)

    email = registration.get("email") or ""
    ip = _ip()

    if not hmac.compare_digest(stored, _hash_code(code)):
        attempts = int(registration.get("code_attempts", 0)) + 1
        LIMITER.record_failure(ip, email)
        if attempts >= CODE_MAX_ATTEMPTS:
            _forget_code(token, registration)
            return _fail(
                "CODE_ATTEMPTS_EXHAUSTED",
                "Prea multe coduri greșite. Cereți un cod nou.",
                400,
            )
        registration["code_attempts"] = attempts
        store.save(token, registration)
        return _fail("CODE_WRONG", "Codul de confirmare este greșit.", 400)

    # Right. The code is spent, the address is proven, the registration goes on.
    registration["verified"] = True
    registration["code_hash"] = None
    registration["code_attempts"] = 0
    registration["code_expires_at"] = 0.0
    left = store.save(token, registration)
    LIMITER.record_success(ip, email)
    logger.info("registration verified for %s", mailer.mask_address(email))
    return _json(
        {"ok": True,
         "email_masked": mailer.mask_address(email),
         "expires_in": left},
        200,
    )


# ---------------------------------------------------------------------------
# GET /api/inregistrare/sursasector
# GET /api/inregistrare/clasificatii?tip=F|E
# Steps 4 and 5. The lists the applicant picks from. Behind the token like
# everything after step 1 -- these are small dictionaries, but they are the
# customer's nomenclature, not public reference data.
# ---------------------------------------------------------------------------
@inregistrare_bp.route("/api/inregistrare/sursasector", methods=["GET"])
def inregistrare_sursasector():
    _token, _registration_data, refusal = _registration()
    if refusal is not None:
        return refusal

    conn = None
    try:
        conn = get_kbot_comun_connection()
        return _json({"surse": nomenclatoare.read_sursasector(conn)}, 200)
    except mysql.connector.Error as err:
        logger.error("inregistrare_sursasector failed: %s", err)
        return _fail("DB_ERROR", "Lista surselor-sector nu a putut fi citită.", 500)
    finally:
        if conn is not None and conn.is_connected():
            conn.close()


@inregistrare_bp.route("/api/inregistrare/clasificatii", methods=["GET"])
def inregistrare_clasificatii():
    _token, _registration_data, refusal = _registration()
    if refusal is not None:
        return refusal

    tip = (request.args.get("tip") or "").strip().upper()
    if tip not in ("F", "E"):
        return _fail("TIP_INVALID", "Tipul clasificației trebuie să fie F sau E.", 400)

    conn = None
    try:
        conn = get_kbot_comun_connection()
        return _json({
            "tip": tip,
            "coduri": nomenclatoare.read_clasificatii(conn, tip),
            "grupuri": nomenclatoare.read_group_captions(conn, tip),
        }, 200)
    except mysql.connector.Error as err:
        logger.error("inregistrare_clasificatii(%s) failed: %s", tip, err)
        return _fail("DB_ERROR", "Nomenclatorul nu a putut fi citit.", 500)
    finally:
        if conn is not None and conn.is_connected():
            conn.close()


# ---------------------------------------------------------------------------
# GET /api/inregistrare/nume?denumire=
# Step 3. A PREVIEW of the database name. The real one is computed again at
# approval (plan 5.4), because a number free now can be taken by the time the
# operator gets round to the request.
# ---------------------------------------------------------------------------
@inregistrare_bp.route("/api/inregistrare/nume", methods=["GET"])
def inregistrare_nume():
    _token, _registration_data, refusal = _registration()
    if refusal is not None:
        return refusal

    denumire = nume.normalize_name(request.args.get("denumire"))
    if not denumire:
        return _fail("DENUMIRE_ABSENTA", "Introduceți denumirea unității.", 400)
    if nume.has_forbidden_characters(denumire):
        return _fail("DENUMIRE_CARACTERE_INTERZISE", nume.NAME_CHARACTERS_MESSAGE, 400)

    try:
        key = nume.letter_key(denumire)
    except nume.NameTooShort:
        return _fail(
            "DENUMIRE_PREA_SCURTA",
            "Denumirea unității trebuie să conțină cel puțin patru litere.",
            400,
        )

    conn = None
    try:
        conn = get_kbot_comun_connection()
        number = nume.first_free_number(nume.used_prefixes(conn))
    except nume.NoFreeNumber:
        logger.error("no free database number left between 111 and 199")
        return _fail(
            "NUMAR_EPUIZAT",
            "Nu mai există numere libere pentru baze noi. Contactați-ne.",
            409,
        )
    except mysql.connector.Error as err:
        logger.error("inregistrare_nume failed: %s", err)
        return _fail("DB_ERROR", "Numele bazei nu a putut fi calculat.", 500)
    finally:
        if conn is not None and conn.is_connected():
            conn.close()

    # `previzualizare` says out loud what the plan says in 5.4: this is not a promise.
    return _json({"db_name": nume.db_name(number, key), "numar": number,
                  "litere": key, "previzualizare": True}, 200)


# ---------------------------------------------------------------------------
# POST /api/inregistrare/cerere
# Step 6. Everything checked once more and written as one row for the operator.
# ---------------------------------------------------------------------------
@inregistrare_bp.route("/api/inregistrare/cerere", methods=["POST"])
def inregistrare_cerere():
    token, registration, refusal = _registration()
    if refusal is not None:
        return refusal

    body = request.get_json(silent=True) or {}
    conn = None
    try:
        conn = get_kbot_comun_connection()
        # D11 once more. The fiscal code was free when the wizard opened; half an
        # hour is long enough for the operator to have created that unit by hand.
        if _cf_has_database(registration.get("cf") or ""):
            return _fail(
                "CF_EXISTS",
                "Pentru acest cod fiscal există deja o bază de date. "
                "Contactați-ne pentru acces.",
                409,
            )
        checked = cerere_mod.validate(conn, registration, body)
    except cerere_mod.CerereInvalid as err:
        return _fail(err.reason, err.message, 400)
    except mysql.connector.Error as err:
        logger.error("inregistrare_cerere validation failed: %s", err)
        return _fail("DB_ERROR", "Cererea nu a putut fi verificată. Reîncercați.", 500)
    finally:
        if conn is not None and conn.is_connected():
            conn.close()

    try:
        id_cerere = cerere_mod.insert(checked, registration, _ip())
    except mysql.connector.Error as err:
        logger.error("inregistrare_cerere insert failed: %s", err)
        return _fail("DB_ERROR", "Cererea nu a putut fi înregistrată. Reîncercați.", 500)

    # The request IS recorded at this point. A notification that does not go out is
    # worth a warning in the log and an honest field in the answer -- never a failure
    # that would invite the applicant to send everything a second time.
    notified = _notify_operator(id_cerere, checked, registration)

    # The registration is finished with. Leaving the note alive would let the same
    # token post a second request against a proven address.
    store.discard(token)

    logger.info("registration request %s filed for CF %s", id_cerere,
                registration.get("cf"))
    return _json({"id_cerere": id_cerere,
                  "randuri": checked["randuri"],
                  "operator_anuntat": notified}, 200)


def _notify_operator(id_cerere, checked, registration):
    """True when the operator's mail went out. Every failure is logged, none raised."""
    try:
        address = mailer.operator_address()
        if not address:
            logger.warning("OPERATOR_EMAIL is not configured: request %s not announced",
                           id_cerere)
            return False
        mailer.send_registration_notice(
            address,
            id_cerere=id_cerere,
            denumire=checked["denumire"],
            cf=registration.get("cf") or "",
            email=registration.get("email") or "",
            randuri=checked["randuri"],
        )
        return True
    except Exception as err:
        logger.error("operator notice for request %s failed: %s", id_cerere, err)
        return False


# ---------------------------------------------------------------------------
# GET /inregistrare
# The page itself (slice 0075-04). Its CSS and JS live beside it in PYTHON/static/,
# which Flask already serves at /static/ as the app's default static folder, so
# only the page needs a route of its own -- a clean URL for the link people get.
#
# The headers are for a page anyone can open. 'unsafe-inline' is on styles only:
# the tree component writes style="..." attributes into its markup. No script is
# inline and nothing uses eval, so scripts stay 'self'.
# ---------------------------------------------------------------------------
_PAGE_FILE = "inregistrare.html"
_PAGE_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
        "base-uri 'none'; form-action 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    # Revalidated on every visit, so a new version of the page is never held back.
    "Cache-Control": "no-cache",
}


@inregistrare_bp.route("/inregistrare", methods=["GET"])
@inregistrare_bp.route("/inregistrare/", methods=["GET"])
def inregistrare_page():
    response = send_from_directory(current_app.static_folder, _PAGE_FILE)
    response.headers.update(_PAGE_HEADERS)
    return response


# ---------------------------------------------------------------------------
# GET  /parola                          the page the approval mail links to
# POST /api/inregistrare/parola/stare   {token}          -> is the link alive?
# POST /api/inregistrare/parola         {token, parola}  -> set it, spend the link
# Slice 0075-03, plan 5.6 step 8. The token arrives in the link's fragment, so it
# reaches the server only in these POST bodies -- never in a URL, never in a log.
# ---------------------------------------------------------------------------
_PAROLA_FILE = "parola.html"
_MSG_LINK_DEAD = (
    "Linkul nu mai este valabil: a expirat sau a fost deja folosit. "
    "Contactați-ne pentru un link nou."
)


@inregistrare_bp.route("/parola", methods=["GET"])
def parola_page():
    response = send_from_directory(current_app.static_folder, _PAROLA_FILE)
    response.headers.update(_PAGE_HEADERS)
    return response


@inregistrare_bp.route("/api/inregistrare/parola/stare", methods=["POST"])
def parola_stare():
    body = request.get_json(silent=True) or {}
    token = (body.get("token") or "").strip()
    ip = _ip()
    if LIMITER.is_blocked(ip, "parola"):
        return _fail("RATE_LIMITED", _MSG_RATE_LIMITED, 429)
    try:
        link = parola_mod.find(token)
    except mysql.connector.Error as err:
        logger.error("parola_stare lookup failed: %s", err)
        return _fail("DB_ERROR", "Linkul nu a putut fi verificat. Reîncercați mai târziu.", 500)
    if link is None:
        LIMITER.record_failure(ip, "parola")
        return _fail("LINK_INVALID", _MSG_LINK_DEAD, 404)
    return _json({"email_masked": mailer.mask_address(link["email"]),
                  "denumire": link["denumire"],
                  "expires_in": link["ramas"]}, 200)


@inregistrare_bp.route("/api/inregistrare/parola", methods=["POST"])
def parola_set():
    body = request.get_json(silent=True) or {}
    token = (body.get("token") or "").strip()
    parola = body.get("parola")
    ip = _ip()
    if LIMITER.is_blocked(ip, "parola"):
        return _fail("RATE_LIMITED", _MSG_RATE_LIMITED, 429)

    problem = parola_mod.password_problem(parola)
    if problem:
        return _fail("PAROLA_INVALIDA", problem, 400)

    try:
        link = parola_mod.find(token)
    except mysql.connector.Error as err:
        logger.error("parola_set lookup failed: %s", err)
        return _fail("DB_ERROR", "Linkul nu a putut fi verificat. Reîncercați mai târziu.", 500)
    if link is None:
        LIMITER.record_failure(ip, "parola")
        return _fail("LINK_INVALID", _MSG_LINK_DEAD, 404)

    try:
        parola_mod.set_password(link, token, parola)
    except Exception as err:        # MariaDB refusal or missing provisioning config
        logger.error("password set for request %s failed: %s", link["id_cerere"], err)
        return _fail("PAROLA_NESETATA",
                     "Parola nu a putut fi setată pe server. Reîncercați mai târziu.", 500)

    LIMITER.record_success(ip, "parola")
    logger.info("password chosen for request %s (%s)", link["id_cerere"],
                mailer.mask_address(link["email"]))
    return _json({"ok": True, "utilizator": link["email"]}, 200)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _registration():
    """
    `(token, registration, None)` for a live registration, or
    `(None, None, <401 response>)`. Both routes after /anaf start with this line.
    """
    token = (request.headers.get(TOKEN_HEADER) or "").strip()
    if not token:
        return None, None, _fail("TOKEN_ABSENT", _MSG_TOKEN, 401)
    registration = store.read(token)
    if registration is None:
        return None, None, _fail("TOKEN_UNKNOWN", _MSG_TOKEN, 401)
    return token, registration, None


def _forget_code(token, registration):
    """Throws the code away and keeps the registration -- a new one can be asked for."""
    registration["code_hash"] = None
    registration["code_attempts"] = 0
    registration["code_expires_at"] = 0.0
    store.save(token, registration)


def _hash_code(code) -> str:
    # Six digits with a ten-minute life: a plain SHA-256 keeps it out of the store in
    # clear, and compare_digest keeps the comparison constant-time. Same as auth.py.
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _is_email(text) -> bool:
    """
    Shape only: one `@`, something on the left, a dotted domain on the right, no
    whitespace. The real proof is the code that lands in the inbox.
    """
    if not text or any(ch.isspace() for ch in text):
        return False
    local, sep, domain = text.partition("@")
    if not sep or not local or not domain:
        return False
    if "@" in domain:
        return False
    return "." in domain and not domain.startswith(".") and not domain.endswith(".")


def _cf_has_database(cf):
    """
    True when this fiscal code already owns a unit database (D11).

    `CAI` and `AVACONT_COMUN.Unitati` are both consulted: the plan's finding F0
    showed the two lists are not the same, and either one is reason enough to refuse.
    Codes are stored both bare and with the `RO` prefix, so both spellings are asked
    for -- that keeps the comparison on a plain indexed column instead of wrapping it
    in REPLACE().
    """
    spellings = (cf, "RO" + cf)
    conn = get_kbot_comun_connection()
    try:
        # buffered: two queries on one cursor, and an unread LIMIT 1 result would
        # make the second execute() raise "Unread result found".
        cur = conn.cursor(buffered=True)
        cur.execute("SELECT 1 FROM CAI WHERE CF IN (%s, %s) LIMIT 1", spellings)
        if cur.fetchone() is not None:
            return True
        cur.execute("SELECT 1 FROM Unitati WHERE CF IN (%s, %s) LIMIT 1", spellings)
        return cur.fetchone() is not None
    finally:
        if conn.is_connected():
            conn.close()


def _email_taken(email):
    """
    True when the address is already a K-BOT user or already a MariaDB account.

    Both matter, for different reasons: a row in `Unitati_Utilizatori` means the
    person already has access to some unit, and an account in `mysql.user` means
    step 7 of the provisioning job would fail at `CREATE USER` after the database was
    already built. Better refused on the first screen.

    That the service account can read `mysql.user` here was checked on the K-BOT
    server on 22.09.2026: it can.
    """
    conn = get_kbot_comun_connection()
    try:
        cur = conn.cursor(buffered=True)
        cur.execute("SELECT 1 FROM Unitati_Utilizatori WHERE UN = %s LIMIT 1", (email,))
        if cur.fetchone() is not None:
            return True
        cur.execute("SELECT 1 FROM mysql.user WHERE User = %s LIMIT 1", (email,))
        return cur.fetchone() is not None
    finally:
        if conn.is_connected():
            conn.close()
