# routes/efactura/primite_vercon_routes.py
"""
Routes of the sending of a received invoice to VERCON (slice 00EF-45 = EV01, plan docs/EV_PLAN.md). All `@require_session`; the unit and the role are
the session's. The rules are in primite_vercon.py. Every WRITE needs the role «Contabil» (403 ROL_INTERZIS otherwise).

  GET    /api/efactura/vercon/conturi                       -> { gestiuni: [{ SubunitId, GestiuneId, Subunitate, Gestiune, Tip, conturi: [{ IdMasina, Masina }] }] }
                                                              the tree of accounts of every active gestiune (MF and OI too)
  GET    /api/efactura/vercon/stari                         -> { trimiteri: [{ IdPrimita, IdTrimitere, Stare, Revenita, MotivReturnare, Nir: [{ Gestiune, Numar, NumarSub }] }] }
                                                              one short line per invoice that was ever sent (for the tree of the received invoices)
  GET    /api/efactura/primite/<id>/vercon                  -> { poate_scrie, poate_trimite, motiv, factura, partener, duplicate, trimitere, istoric,
                                                                 linii: [{ IdLinie, NrLinie, Denumire, Cpv, Unit, Cant, Pret, Valoare, ValoareCuTVA, IdMasina, Cont, Gestiune }] }
  POST   /api/efactura/primite/<id>/vercon                  body { conturi: [{ IdLinie, IdMasina | null }], observatie? } -> 201, the state as GET
                                                              ALL the lines are sent; a line not named in `conturi` has no proposed account
  PUT    /api/efactura/primite/<id>/vercon/conturi          body { conturi } -> the state as GET   (only while the sending waits)
  DELETE /api/efactura/primite/<id>/vercon/<idTrimitere>    -> the state as GET   (takes it back, only while it waits)

Errors are `{ "error": "<text>", "reason": "<CODE>" }`. Audit journal: EF_VC_TRIMITE, EF_VC_CONTURI, EF_VC_RETRAGE.
"""
from flask import g

from . import efactura_bp, primite_vercon
from .factura_routes import _audit, _body, _guarded, _json
from .tokens import EfEroare
from routes.auth.guard import require_session


def _role():
    return (getattr(g.session, "ctx", None) or {}).get("Role")


@efactura_bp.route("/api/efactura/vercon/conturi", methods=["GET"])
@require_session
@_guarded("vercon/conturi")
def vercon_conturi():
    return _json(primite_vercon.conturi_arbore(g.session.db_name))


@efactura_bp.route("/api/efactura/vercon/stari", methods=["GET"])
@require_session
@_guarded("vercon/stari")
def vercon_stari():
    return _json(primite_vercon.stari(g.session.db_name))


@efactura_bp.route("/api/efactura/primite/<int:id_primita>/vercon", methods=["GET"])
@require_session
@_guarded("vercon/starea")
def vercon_starea(id_primita):
    return _json(primite_vercon.starea(g.session.db_name, id_primita, _role()))


@efactura_bp.route("/api/efactura/primite/<int:id_primita>/vercon", methods=["POST"])
@require_session
@_guarded("vercon/trimite")
def vercon_trimite(id_primita):
    body = _body()
    if set(body) - {"conturi", "observatie"}:
        raise EfEroare("Corpul poate conține doar «conturi» și «observatie».", "CAMP_NEPERMIS", 400)
    state = primite_vercon.trimite(g.session.db_name, id_primita, _role(), g.session.username, body.get("conturi"), body.get("observatie"))
    _audit("EF_VC_TRIMITE", str(id_primita), "linii=%d" % len(state["linii"]))
    return _json(state, 201)


@efactura_bp.route("/api/efactura/primite/<int:id_primita>/vercon/conturi", methods=["PUT"])
@require_session
@_guarded("vercon/conturi-schimba")
def vercon_schimba_conturi(id_primita):
    body = _body()
    if set(body) - {"conturi"}:
        raise EfEroare("Corpul poate conține doar «conturi».", "CAMP_NEPERMIS", 400)
    state = primite_vercon.schimba_conturi(g.session.db_name, id_primita, _role(), body.get("conturi"))
    _audit("EF_VC_CONTURI", str(id_primita), "conturi=%d" % len(body.get("conturi") or []))
    return _json(state)


@efactura_bp.route("/api/efactura/primite/<int:id_primita>/vercon/<int:id_trimitere>", methods=["DELETE"])
@require_session
@_guarded("vercon/retrage")
def vercon_retrage(id_primita, id_trimitere):
    state = primite_vercon.retrage(g.session.db_name, id_primita, _role(), id_trimitere)
    _audit("EF_VC_RETRAGE", str(id_primita), "trimitere=%d" % id_trimitere)
    return _json(state)
