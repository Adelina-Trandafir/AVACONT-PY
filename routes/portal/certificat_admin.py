"""SLICE-WB02-01: Administrator enrolment, using the portal's existing mTLS flow."""
import logging
import os
from functools import wraps
from html import escape

import mysql.connector
from flask import current_app, g, make_response, request

from . import certificat
from .portal import (
    _fail, _json, _live_session, _origin_setting, _page_headers, _slide,
    _user_units, portal_bp,
)

logger = logging.getLogger(__name__)


@portal_bp.after_request
def _administrator_certificate_headers(response):
    if request.path.startswith("/api/portal/certificat/administrator/"):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def _require_administrator(function):
    @wraps(function)
    def guarded(*args, **kwargs):
        token, note, refusal = _live_session()
        if refusal is not None:
            return refusal
        try:
            units = _user_units(note["email"])
        except mysql.connector.Error:
            logger.exception("administrator certificate: cannot read current role")
            return _fail("DB_ERROR", "Drepturile contului nu au putut fi citite.", 500)
        unit = next((row for row in units if row["DC"] == note.get("db_name")), None)
        role = str((unit or {}).get("Rol") or "").strip().casefold()
        if role != "administrator":
            return _fail("ROL_INTERZIS", "Înrolarea din această pagină este disponibilă numai Administratorului unității curente.", 403)
        g.certificate_account = {"email": note["email"], "db_name": unit["DC"]}
        _slide(token, note)
        return function(*args, **kwargs)
    return guarded


@portal_bp.get("/certificat-administrator")
def certificat_administrator_page():
    # Navigation carries no token: the public shell reads no account data. Every API is guarded.
    path = os.path.join(current_app.static_folder, "certificat-administrator.html")
    with open(path, "r", encoding="utf-8") as handle:
        html = handle.read().replace("__CERT_ORIGIN__", escape(_origin_setting("PORTAL_CERT_ORIGIN"), quote=True))
    response = make_response(html)
    response.mimetype = "text/html"
    response.headers.update(_page_headers())
    return response


@portal_bp.get("/api/portal/certificat/administrator/acces")
@_require_administrator
def certificat_administrator_acces():
    return _json(g.certificate_account)


@portal_bp.get("/api/portal/certificat/administrator/stare")
@_require_administrator
def certificat_administrator_stare():
    return certificat.certificat_stare()


@portal_bp.post("/api/portal/certificat/administrator/inroleaza")
@_require_administrator
def certificat_administrator_inroleaza():
    # Identity comes only from the live session. The original flow validates nginx's mTLS
    # headers, rejects another account's fingerprint and journals enrolment in one transaction.
    return certificat.certificat_inroleaza()


@portal_bp.post("/api/portal/certificat/administrator/dezactiveaza")
@_require_administrator
def certificat_administrator_dezactiveaza():
    return certificat.certificat_dezactiveaza()
