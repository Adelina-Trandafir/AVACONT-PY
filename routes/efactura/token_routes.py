# routes/efactura/token_routes.py
"""
The three token routes of E-Factura (slice 00EF-04). All `@require_session` (K-BOT bearer): the unit is the
session's (`g.session.db_name` = `Unitati.DC`), never a parameter of the request.

    POST /api/efactura/token/start
        no body
        -> 200 { "authorize_url": "...", "state": "<32 hex>", "expira_in_secunde": 900 }
           The PC calls `authorize_url` with its certificate (TLS client certificate), reads `code=` from the
           final address, and sends it back with the `state`. Nothing in the address is secret.

    POST /api/efactura/token/cod
        { "state": "...", "code": "...", "certificat": "<CN, display only>", "amprenta": "<thumbprint, display only>" }
        -> 200 the same body as `stare`   (the code was exchanged, the tokens stored encrypted)
           400 STATE_INVALID | COD_INVALID, 502 ANAF_REFUZ | ANAF_INDISPONIBIL, 503 EF_NECONFIGURAT | TABELE_LIPSA

    GET  /api/efactura/token/stare
        -> 200 { "configurat", "exista", "cui", "valabil_pana", "avertizeaza_de_la", "zile_ramase",
                 "avertizeaza", "trebuie_reinnoit", "certificat", "autorizat_de", "autorizat_la",
                 "ultima_eroare", "ultima_eroare_la" }   dates are UTC ISO text ending in Z
           The PC shows a notice when `avertizeaza` and offers the certificate step when `trebuie_reinnoit`.

Errors are `{ "error": "<Romanian text>", "reason": "<CODE>" }`. No answer ever contains a token, the client
secret or the code. Every authorisation is written in the audit journal (`Jurnal`, action EF_TOKEN_AUTORIZARE).
"""
import json
import logging
import re

from flask import current_app, g, request

from routes.auth.auth import log_action
from routes.auth.guard import require_session

from . import efactura_bp, tokens

logger = logging.getLogger(__name__)

_STATE = re.compile(r"^[0-9a-f]{32}$")


def _json(payload, status=200):
    """JSON with LITERAL diacritics (ensure_ascii=False): `error` is Romanian text for the operator."""
    body = json.dumps(payload, ensure_ascii=False)
    return current_app.response_class(body, status=status, mimetype="application/json")


def _refuse(err):
    return _json({"error": str(err), "reason": err.reason}, err.status)


def _failed(where, err):
    logger.error("[efactura] %s: %s", where, type(err).__name__, exc_info=True)
    return _json({"error": "Eroare internă la procesarea tokenului E-Factura.", "reason": "EROARE_SERVER"}, 500)


@efactura_bp.route("/api/efactura/token/start", methods=["POST"])
@require_session
def token_start():
    try:
        return _json(tokens.start(g.session.db_name, g.session.username))
    except tokens.EfEroare as err:
        return _refuse(err)
    except Exception as err:
        return _failed("token/start", err)


@efactura_bp.route("/api/efactura/token/cod", methods=["POST"])
@require_session
def token_cod():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _json({"error": "Corpul cererii nu este JSON.", "reason": "COD_INVALID"}, 400)
    state = data.get("state")
    if not isinstance(state, str) or not _STATE.match(state):
        return _json({"error": "Cererea de autorizare nu este recunoscută. Reluați pasul cu certificatul.",
                      "reason": "STATE_INVALID"}, 400)
    code = data.get("code")
    if not isinstance(code, str):
        return _json({"error": "Codul de autorizare lipsește sau este invalid.", "reason": "COD_INVALID"}, 400)
    label = data.get("certificat")
    thumbprint = data.get("amprenta")

    dc, username = g.session.db_name, g.session.username
    try:
        answer = tokens.finish(dc, username, state, code,
                               label if isinstance(label, str) else None,
                               thumbprint if isinstance(thumbprint, str) else None)
    except tokens.EfEroare as err:
        log_action(username, dc, "EF_TOKEN_AUTORIZARE", detalii=err.reason, rezultat="ESEC",
                   masina=g.session.pcname, ip=request.remote_addr)
        return _refuse(err)
    except Exception as err:
        log_action(username, dc, "EF_TOKEN_AUTORIZARE", detalii="EROARE_SERVER", rezultat="ESEC",
                   masina=g.session.pcname, ip=request.remote_addr)
        return _failed("token/cod", err)

    log_action(username, dc, "EF_TOKEN_AUTORIZARE", tinta=answer.get("cui"), detalii=answer.get("certificat"),
               masina=g.session.pcname, ip=request.remote_addr)
    return _json(answer)


@efactura_bp.route("/api/efactura/token/stare", methods=["GET"])
@require_session
def token_stare():
    try:
        return _json(tokens.stare(g.session.db_name))
    except tokens.EfEroare as err:
        return _refuse(err)
    except Exception as err:
        return _failed("token/stare", err)
