"""
Sign-in to the web area with a qualified digital certificate (slice 0110-04).

    GET  /api/portal/certificat/stare            -> does this connection carry a certificate, is it enrolled
    POST /api/portal/certificat/inroleaza        -> remembers this connection's certificate for the user
    POST /api/portal/certificat/login            -> the portal session, no password, no code
    POST /api/portal/certificat/dezactiveaza     {id}

WHAT nginx DOES. It terminates TLS, asks the browser for a client certificate
(`ssl_verify_client optional`), checks the chain against the qualified authorities it trusts
(`ssl_client_certificate`), the validity dates and nothing else, and hands the result to this
server in headers (see docs/worklog/SLICE-0110-04-portal-certificat.md for the nginx block):

    X-SSL-Verify   SUCCESS | FAILED:<reason> | NONE
    X-SSL-Cert     the certificate, PEM, URL-escaped ($ssl_client_escaped_cert)
    X-SSL-Subject  $ssl_client_s_dn        X-SSL-Issuer  $ssl_client_i_dn
    X-SSL-End      $ssl_client_v_end

The headers are trusted because gunicorn listens on 127.0.0.1 only and nginx SETS every one of
them on every request (a value sent by the client is replaced, an empty one is dropped).

WHO THE CERTIFICATE BELONGS TO. Not read from a field of the person. The user proves who they
are with password + mailed code (the ordinary portal session), presses «Înrolează», and the
SHA-256 of the certificate's bytes is stored against their e-mail (Utilizatori_Certificate).
From then on the same certificate signs them in. A renewed certificate is a different one: it is
enrolled again the same way, and enrolling switches the user's older ones off. The e-mail inside
the certificate (when the subject has one) is only COMPARED with the account's and the result
written in the journal; it never blocks, because a certificate often carries a personal address.

REVOKING. `Activ = 0` on the row (the operator in the database, or the user from the portal). The
server does not ask the authority's revocation lists: a revoked certificate is switched off here.

FALLBACK. Every refusal here is a plain JSON `reason`; the page answers it by showing the
password + code form, which is always available.
"""
import base64
import binascii
import hashlib
import logging
import re
from datetime import datetime, timezone
from urllib.parse import unquote

import mysql.connector
from flask import request

from routes.auth import mailer
from routes.auth.auth import COMMON_DB, log_action
from routes.auth.ratelimit import LIMITER
from utils.database import get_kbot_comun_connection, get_kbot_connection

from .portal import _fail, _ip, _json, _live_session, _slide, _user_units, open_session, portal_bp

logger = logging.getLogger(__name__)

_MSG_NO_CERT = "Browserul nu a trimis niciun certificat."
_MSG_BAD_CERT = "Certificatul nu a putut fi validat."
_MSG_EXPIRED = "Certificatul a expirat. Autentificați-vă cu parola și codul, apoi înrolați certificatul nou."
_MSG_UNKNOWN = "Certificatul nu este înrolat. Autentificați-vă cu parola și codul și înrolați-l."

_PEM_BODY = re.compile(r"-----BEGIN CERTIFICATE-----(.+?)-----END CERTIFICATE-----", re.DOTALL)
_DN_EMAIL = re.compile(r"(?:^|[,/])\s*(?:emailAddress|E)=([^,/]+)", re.IGNORECASE)


class _CertError(Exception):
    """A certificate the connection cannot use; `reason` and `message` go to the page."""

    def __init__(self, reason, message, status=401):
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.status = status


def _presented():
    """The certificate this connection carries, as a dict, or raises _CertError.

    Keys: amprenta (SHA-256 hex of the DER bytes), subiect, emitent, valid_pana (naive UTC
    datetime or None), email (the one in the subject or None)."""
    verify = (request.headers.get("X-SSL-Verify") or "").strip()
    escaped = request.headers.get("X-SSL-Cert") or ""
    if not escaped or verify in ("", "NONE"):
        raise _CertError("CERT_ABSENT", _MSG_NO_CERT)
    if verify != "SUCCESS":
        if "expired" in verify.lower():
            raise _CertError("CERT_EXPIRAT", _MSG_EXPIRED)
        logger.warning("portal certificate refused by nginx: %s", verify)
        raise _CertError("CERT_INVALID", _MSG_BAD_CERT)

    found = _PEM_BODY.search(unquote(escaped))
    if found is None:
        raise _CertError("CERT_INVALID", _MSG_BAD_CERT)
    try:
        der = base64.b64decode("".join(found.group(1).split()), validate=True)
    except (binascii.Error, ValueError) as err:
        raise _CertError("CERT_INVALID", _MSG_BAD_CERT) from err

    subject = (request.headers.get("X-SSL-Subject") or "").strip()
    issuer = (request.headers.get("X-SSL-Issuer") or "").strip()
    mail = _DN_EMAIL.search(subject)
    return {
        "amprenta": hashlib.sha256(der).hexdigest(),
        "subiect": subject[:400] or None,
        "emitent": issuer[:400] or None,
        "valid_pana": _parse_end(request.headers.get("X-SSL-End")),
        "email": mail.group(1).strip().lower() if mail else None,
    }


def _parse_end(text):
    """nginx's $ssl_client_v_end, e.g. 'Oct  5 12:00:00 2027 GMT'. None when unreadable."""
    try:
        return datetime.strptime(" ".join((text or "").split()), "%b %d %H:%M:%S %Y GMT")
    except ValueError:
        return None


def _refuse(err):
    return _fail(err.reason, err.message, err.status)


# ---------------------------------------------------------------------------
# State of this connection
# ---------------------------------------------------------------------------
@portal_bp.route("/api/portal/certificat/stare", methods=["GET"])
def certificat_stare():
    token, note, refusal = _live_session()
    if refusal is not None:
        return refusal
    email = note["email"]
    try:
        rows = _my_certificates(email)
    except mysql.connector.Error as err:
        logger.error("portal certificate state of %s: %s", mailer.mask_address(email), err)
        return _fail("DB_ERROR", "Certificatele nu au putut fi citite.", 500)
    _slide(token, note)

    here = None
    try:
        here = _presented()["amprenta"]
    except _CertError as err:
        reason = err.reason
    else:
        reason = None
    return _json({
        "certificat_prezent": here is not None,
        "motiv": reason,
        "acest_certificat_inrolat": any(r["Activ"] and r["Amprenta"] == here for r in rows),
        "certificate": [{
            "id": r["IdCertificat"], "subiect": r["Subiect"], "emitent": r["Emitent"],
            "valid_pana": r["ValidPana"].isoformat() if r["ValidPana"] else None,
            "activ": bool(r["Activ"]), "aici": r["Amprenta"] == here,
        } for r in rows],
    })


def _my_certificates(email):
    conn = None
    try:
        conn = get_kbot_comun_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT IdCertificat, Amprenta, Subiect, Emitent, ValidPana, Activ "
            "FROM Utilizatori_Certificate WHERE UN = %s ORDER BY IdCertificat DESC",
            (email,),
        )
        return cur.fetchall()
    finally:
        if conn is not None and conn.is_connected():
            conn.close()


# ---------------------------------------------------------------------------
# Enrolment: needs the password + code session
# ---------------------------------------------------------------------------
@portal_bp.route("/api/portal/certificat/inroleaza", methods=["POST"])
def certificat_inroleaza():
    token, note, refusal = _live_session()
    if refusal is not None:
        return refusal
    email = note["email"]
    ip = _ip()
    try:
        cert = _presented()
    except _CertError as err:
        log_action(email, note.get("db_name"), "PORTAL_CERT_ENROL", detalii=err.reason,
                   rezultat="EROARE", ip=ip)
        return _refuse(err)

    # Compare, never block: a certificate often carries a personal address.
    remark = None
    if cert["email"] and cert["email"] != email:
        remark = "e-mail in certificate differs from the account"

    conn = None
    try:
        conn = get_kbot_connection(COMMON_DB)
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT UN, Activ FROM Utilizatori_Certificate WHERE Amprenta = %s FOR UPDATE",
                    (cert["amprenta"],))
        known = cur.fetchone()
        if known is not None and known["UN"] != email:
            conn.rollback()
            # The same certificate is already another person's: refused, and written down.
            log_action(email, note.get("db_name"), "PORTAL_CERT_ENROL",
                       detalii="certificate belongs to another account", rezultat="EROARE", ip=ip)
            return _fail("CERT_AL_ALTUIA", "Certificatul este deja înrolat pe alt cont.", 409)
        # One active certificate per user: the older ones are switched off.
        cur.execute(
            "UPDATE Utilizatori_Certificate SET Activ = 0, DataDezactivare = %s "
            "WHERE UN = %s AND Activ = 1 AND Amprenta <> %s",
            (datetime.now(timezone.utc).replace(tzinfo=None), email, cert["amprenta"]),
        )
        if known is None:
            cur.execute(
                "INSERT INTO Utilizatori_Certificate (UN, Amprenta, Subiect, Emitent, ValidPana) "
                "VALUES (%s, %s, %s, %s, %s)",
                (email, cert["amprenta"], cert["subiect"], cert["emitent"], cert["valid_pana"]),
            )
        else:
            cur.execute(
                "UPDATE Utilizatori_Certificate SET Activ = 1, DataDezactivare = NULL, "
                "Subiect = %s, Emitent = %s, ValidPana = %s WHERE Amprenta = %s",
                (cert["subiect"], cert["emitent"], cert["valid_pana"], cert["amprenta"]),
            )
        conn.commit()
    except mysql.connector.Error as err:
        logger.error("portal certificate enrolment of %s: %s", mailer.mask_address(email), err)
        if conn is not None:
            try:
                conn.rollback()
            except mysql.connector.Error:
                pass
        return _fail("DB_ERROR", "Certificatul nu a putut fi înrolat. Reîncercați.", 500)
    finally:
        if conn is not None and conn.is_connected():
            conn.close()

    _slide(token, note)
    log_action(email, note.get("db_name"), "PORTAL_CERT_ENROL", tinta=cert["amprenta"][:16],
               detalii=remark, ip=ip)
    return _json({"ok": True, "valid_pana": cert["valid_pana"].isoformat() if cert["valid_pana"] else None})


# ---------------------------------------------------------------------------
# Sign-in with the certificate
# ---------------------------------------------------------------------------
@portal_bp.route("/api/portal/certificat/login", methods=["POST"])
def certificat_login():
    ip = _ip()
    try:
        cert = _presented()
    except _CertError as err:
        return _refuse(err)

    # The rate limit is per (address, certificate): a stolen-certificate probe is slowed like a password one.
    who = "cert:" + cert["amprenta"][:16]
    if LIMITER.is_blocked(ip, who):
        return _fail("RATE_LIMITED", "Prea multe încercări eșuate. Reîncercați peste 15 minute.", 429)

    conn = None
    try:
        conn = get_kbot_connection(COMMON_DB)
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT UN, Activ, ValidPana FROM Utilizatori_Certificate WHERE Amprenta = %s",
                    (cert["amprenta"],))
        row = cur.fetchone()
        if row is not None and row["Activ"]:
            cur.execute("UPDATE Utilizatori_Certificate SET UltimaFolosire = %s WHERE Amprenta = %s",
                        (datetime.now(timezone.utc).replace(tzinfo=None), cert["amprenta"]))
            conn.commit()
    except mysql.connector.Error as err:
        logger.error("portal certificate login: lookup failed: %s", err)
        return _fail("DB_ERROR", "Certificatul nu a putut fi verificat. Reîncercați.", 500)
    finally:
        if conn is not None and conn.is_connected():
            conn.close()

    if row is None or not row["Activ"]:
        LIMITER.record_failure(ip, who)
        email = row["UN"] if row is not None else "(necunoscut)"
        log_action(email, None, "PORTAL_AUTH_FAIL",
                   detalii="certificate " + ("switched off" if row is not None else "not enrolled"),
                   rezultat="EROARE", ip=ip)
        return _fail("CERT_NEINROLAT", _MSG_UNKNOWN, 401)

    email = row["UN"]
    try:
        units = _user_units(email)
    except mysql.connector.Error as err:
        logger.error("portal certificate login: units of %s unreadable: %s",
                     mailer.mask_address(email), err)
        return _fail("DB_ERROR", "Unitățile nu au putut fi citite. Reîncercați.", 500)
    if not units:
        LIMITER.record_failure(ip, who)
        log_action(email, None, "PORTAL_DENIED", detalii="no unit (certificate)", rezultat="EROARE", ip=ip)
        return _fail("FARA_UNITATI", "Contul nu are nicio unitate asociată.", 403)

    LIMITER.record_success(ip, who)
    return open_session(email, units, ip, how="certificate")


# ---------------------------------------------------------------------------
# The user switches one of their own certificates off
# ---------------------------------------------------------------------------
@portal_bp.route("/api/portal/certificat/dezactiveaza", methods=["POST"])
def certificat_dezactiveaza():
    token, note, refusal = _live_session()
    if refusal is not None:
        return refusal
    email = note["email"]
    try:
        cert_id = int((request.get_json(silent=True) or {}).get("id"))
    except (TypeError, ValueError):
        return _fail("DATE_INCOMPLETE", "Certificatul nu a fost indicat.", 400)

    conn = None
    try:
        conn = get_kbot_connection(COMMON_DB)
        cur = conn.cursor()
        # UN in the WHERE: a user can only ever switch off their own.
        cur.execute(
            "UPDATE Utilizatori_Certificate SET Activ = 0, DataDezactivare = %s "
            "WHERE IdCertificat = %s AND UN = %s AND Activ = 1",
            (datetime.now(timezone.utc).replace(tzinfo=None), cert_id, email),
        )
        changed = cur.rowcount
        conn.commit()
    except mysql.connector.Error as err:
        logger.error("portal certificate switch-off of %s: %s", mailer.mask_address(email), err)
        if conn is not None:
            try:
                conn.rollback()
            except mysql.connector.Error:
                pass
        return _fail("DB_ERROR", "Certificatul nu a putut fi dezactivat. Reîncercați.", 500)
    finally:
        if conn is not None and conn.is_connected():
            conn.close()

    if not changed:
        return _fail("CERT_LIPSA", "Certificatul nu există sau este deja dezactivat.", 404)
    _slide(token, note)
    log_action(email, note.get("db_name"), "PORTAL_CERT_OFF", tinta=str(cert_id), ip=_ip())
    return _json({"ok": True})
