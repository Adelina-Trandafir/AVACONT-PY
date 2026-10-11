"""
The public «Cere mai multe detalii» form of K-BOT (slice 0110-02).

GET  /detalii        the page (same look as the presentation page, one form)
POST /api/detalii    JSON {nume, institutie, cf, email, telefon, mesaj, acord, website, t}

No login, no cookie, no data read from the database. A request is written to
AVACONT_COMUN.FX_CereriDetalii (sql/0110_02_fx_cereri_detalii.sql) FIRST and mailed to
config.DETALII_EMAIL second, so a mail that does not go out never loses the request.

Protection against bots, in the same spirit as the registration pages (no CAPTCHA):
  * a hidden field («website») that people never see: filled in = a bot, answered with a
    success that stores nothing;
  * a signed timestamp issued with the page: a form sent in under MIN_SECONDS, or older than
    MAX_SECONDS, is refused (the key lives in this process; a restart only makes an open page
    ask to be reloaded);
  * the shared rate limiter, one bucket per client address, every attempt counted;
  * every length capped, characters outside the BMP dropped (the table is utf8mb3).
Nothing here sends mail to the address the visitor typed: it is only used as Reply-To.
"""
import hashlib
import hmac
import json
import logging
import re
import secrets
import time

import mysql.connector
from flask import Blueprint, current_app, make_response, render_template, request

from routes.auth import mailer
from routes.auth.ratelimit import LIMITER
from utils.database import COMMON_DB, get_kbot_connection

logger = logging.getLogger(__name__)

detalii_bp = Blueprint("detalii", __name__, template_folder="templates")

MIN_SECONDS = 3
MAX_SECONDS = 2 * 60 * 60
_FORM_KEY = secrets.token_bytes(32)

# `FX_CereriDetalii.Email` is varchar(80), same cap as the registration wizard.
_LIMITS = {"nume": 120, "institutie": 160, "cf": 20, "email": 80, "telefon": 30, "mesaj": 2000}

_EMAIL = re.compile(r"^[^@\s<>,;:\"']+@[^@\s<>,;:\"']+\.[^@\s<>,;:\"']{2,}$")
_CF = re.compile(r"^(RO)?\s?\d{2,10}$", re.IGNORECASE)
_PHONE = re.compile(r"^\+?[\d\s().-]{6,29}$")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_NON_BMP = re.compile("[\U00010000-\U0010ffff]")

_BUCKET = "detalii"

_PAGE_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
        "base-uri 'none'; form-action 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "Cache-Control": "no-store",
}

# Everything the visitor reads is Romanian with literal diacritics; reasons are ASCII.
_MSG_RATE = "Prea multe încercări. Reîncercați peste câteva minute."
_MSG_EXPIRED = "Pagina a stat deschisă prea mult. Reîncărcați-o și trimiteți din nou."
_MSG_TOO_FAST = "Cererea a fost trimisă prea repede. Așteptați o clipă și apăsați din nou."
_MSG_DB = "Cererea nu a putut fi înregistrată. Reîncercați sau scrieți-ne direct."
_MSG_ACORD = "Bifați acordul pentru prelucrarea datelor, ca să putem răspunde."


def _json(payload, status):
    body = json.dumps(payload, ensure_ascii=False)
    return current_app.response_class(body, status=status, mimetype="application/json")


def _fail(reason, message, status):
    return _json({"error": message, "reason": reason}, status)


def _ip():
    # ProxyFix is active in main.py: this is the real client, not nginx.
    return request.remote_addr


def _sign(issued):
    return hmac.new(_FORM_KEY, str(issued).encode("ascii"), hashlib.sha256).hexdigest()


def _issue_token():
    issued = int(time.time())
    return f"{issued}.{_sign(issued)}"


def _token_age(token):
    """Seconds since the page was issued, or None when the token is not ours."""
    try:
        issued_text, _, mac = str(token).partition(".")
        issued = int(issued_text)
        if not hmac.compare_digest(mac, _sign(issued)):
            return None
        return time.time() - issued
    except Exception:
        return None


def _clean(value, limit):
    """One trimmed text, control and non-BMP characters dropped, cut to `limit`."""
    text = _NON_BMP.sub("", _CONTROL.sub("", str(value or ""))).strip()
    return text[:limit]


@detalii_bp.route("/detalii", methods=["GET"])
@detalii_bp.route("/detalii/", methods=["GET"])
def detalii_page():
    try:
        response = make_response(render_template("detalii/index.html", token=_issue_token()))
    except Exception:
        logger.exception("detalii_page failed")
        return current_app.response_class("Eroare.", status=500, mimetype="text/plain")
    response.headers.update(_PAGE_HEADERS)
    return response


@detalii_bp.route("/api/detalii", methods=["POST"])
def detalii_send():
    ip = _ip()
    if LIMITER.is_blocked(ip, _BUCKET):
        return _fail("RATE_LIMITED", _MSG_RATE, 429)
    # Every attempt counts, good or bad: five a quarter of an hour is plenty for a person.
    LIMITER.record_failure(ip, _BUCKET)

    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _fail("BAD_REQUEST", "Cerere invalidă.", 400)

    # Robots fill every field they find. A person never sees this one.
    if str(body.get("website") or "").strip():
        logger.info("detalii: honeypot filled from %s, nothing stored", ip)
        return _json({"ok": True}, 200)

    age = _token_age(body.get("t"))
    if age is None or age > MAX_SECONDS:
        return _fail("TOKEN", _MSG_EXPIRED, 400)
    if age < MIN_SECONDS:
        return _fail("TOO_FAST", _MSG_TOO_FAST, 400)

    values = {k: _clean(body.get(k), n) for k, n in _LIMITS.items()}
    nume, institutie, email = values["nume"], values["institutie"], values["email"]
    cf, telefon, mesaj = values["cf"], values["telefon"], values["mesaj"]

    if len(nume) < 2:
        return _fail("NUME", "Scrieți numele dumneavoastră.", 400)
    if len(institutie) < 2:
        return _fail("INSTITUTIE", "Scrieți numele instituției.", 400)
    if not _EMAIL.match(email):
        return _fail("EMAIL", "Adresa de e-mail nu pare corectă.", 400)
    if cf and not _CF.match(cf):
        return _fail("CF", "Codul fiscal nu pare corect (de exemplu 12345678 sau RO12345678).", 400)
    if telefon and not _PHONE.match(telefon):
        return _fail("TELEFON", "Numărul de telefon nu pare corect.", 400)
    if body.get("acord") is not True:
        return _fail("ACORD", _MSG_ACORD, 400)

    # The request is recorded first: the mail is a convenience, the row is the record.
    try:
        id_cerere = _insert(nume, institutie, cf, email, telefon, mesaj, ip)
    except mysql.connector.Error as err:
        logger.error("detalii insert failed: %s", err)
        return _fail("DB_ERROR", _MSG_DB, 500)

    notified = _notify(id_cerere, nume, institutie, cf, email, telefon, mesaj)
    logger.info("detalii request %s filed (mail %s)", id_cerere, "sent" if notified else "NOT sent")
    return _json({"ok": True, "id_cerere": id_cerere}, 200)


def _insert(nume, institutie, cf, email, telefon, mesaj, ip):
    conn = get_kbot_connection(COMMON_DB)
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO FX_CereriDetalii (Nume, Institutie, CF, Email, Telefon, Mesaj, IpAddress) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (nume, institutie, cf or None, email, telefon or None, mesaj or None, ip),
        )
        id_cerere = cur.lastrowid
        conn.commit()
        return id_cerere
    except Exception:
        conn.rollback()
        raise
    finally:
        if conn.is_connected():
            conn.close()


def _notify(id_cerere, nume, institutie, cf, email, telefon, mesaj):
    """True when the mail went out. A failure is logged, never raised: the row is saved."""
    try:
        address = mailer.details_address()
        if not address:
            logger.warning("DETALII_EMAIL is not configured: request %s not mailed", id_cerere)
            return False
        mailer.send_details_notice(address, id_cerere, nume, institutie, cf, email, telefon, mesaj)
        _mark_notified(id_cerere)
        return True
    except Exception as err:
        logger.error("detalii mail for request %s failed: %s", id_cerere, err)
        return False


def _mark_notified(id_cerere):
    conn = get_kbot_connection(COMMON_DB)
    try:
        cur = conn.cursor()
        cur.execute("UPDATE FX_CereriDetalii SET Notificat = 1 WHERE IdCerere = %s", (id_cerere,))
        conn.commit()
    except Exception as err:
        conn.rollback()
        logger.error("detalii: could not flag request %s as mailed: %s", id_cerere, err)
    finally:
        if conn.is_connected():
            conn.close()
