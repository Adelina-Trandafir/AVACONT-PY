# routes/efactura/primite_arhiva_routes.py
"""
Routes of the archive of the received invoices (slice 01EF-03). All `@require_session`; the unit, the user and the role are the session's.
The rules are in primite_arhiva.py. Every WRITE needs the role «Contabil» (403 ROL_INTERZIS otherwise).

  GET    /api/efactura/arhiva                       -> { arhivate: [{ IdPrimita, DataArhivare, UN, Motiv }] }
  POST   /api/efactura/primite/<id>/arhiva          body { motiv? } -> 201, { IdPrimita, DataArhivare, UN, Motiv }
  DELETE /api/efactura/primite/<id>/arhiva          -> { IdPrimita, arhivata: false }

Errors are `{ "error": "<text>", "reason": "<CODE>" }`. Audit journal: EF_ARHIVEAZA, EF_SCOATE_ARHIVA.
"""
from flask import g

from . import efactura_bp, primite_arhiva
from .factura_routes import _audit, _body, _guarded, _json
from .tokens import EfEroare
from routes.auth.guard import require_session


def _role():
    return (getattr(g.session, "ctx", None) or {}).get("Role")


@efactura_bp.route("/api/efactura/arhiva", methods=["GET"])
@require_session
@_guarded("arhiva/lista")
def arhiva_lista():
    return _json(primite_arhiva.lista(g.session.db_name))


@efactura_bp.route("/api/efactura/primite/<int:id_primita>/arhiva", methods=["POST"])
@require_session
@_guarded("arhiva/arhiveaza")
def arhiva_arhiveaza(id_primita):
    body = request_body_or_empty()
    if set(body) - {"motiv"}:
        raise EfEroare("Corpul poate conține doar «motiv».", "CAMP_NEPERMIS", 400)
    row = primite_arhiva.arhiveaza(g.session.db_name, id_primita, _role(), g.session.username, body.get("motiv"))
    _audit("EF_ARHIVEAZA", str(id_primita))
    return _json(row, 201)


@efactura_bp.route("/api/efactura/primite/<int:id_primita>/arhiva", methods=["DELETE"])
@require_session
@_guarded("arhiva/scoate")
def arhiva_scoate(id_primita):
    row = primite_arhiva.scoate(g.session.db_name, id_primita, _role())
    _audit("EF_SCOATE_ARHIVA", str(id_primita))
    return _json(row)


def request_body_or_empty():
    """The body may be missing (archiving without a reason); a body that is present must be a JSON object."""
    from flask import request
    if not request.get_data(cache=True):
        return {}
    return _body()
