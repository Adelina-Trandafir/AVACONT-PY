# routes/efactura/primite_ke_routes.py
"""
Routes of the links invoice <-> reception (slices KE01..KE07, plan docs/KE_PLAN.md). All `@require_session`; the unit and the role are the
session's. The rules are in primite_ke.py. Every WRITE needs the role «Contabil» (403 ROL_INTERZIS otherwise).

  GET    /api/efactura/primite/<id>/ke                 -> { poate_scrie, total, alocat, rest, stare, nealocat_liniilor, fara_clasificatie,
                                                           linii: [{ IdLinie, NrLinie, Denumire, Cpv, Valoare, CotaTVA, CotaDedusa,
                                                                     ValoareCuTVA, IdClsf, Clsf }],
                                                           pe_clasificatie: [{ IdClsf, Clsf, Valoare, Alocat, Rest }],
                                                           legaturi: [{ IdLegatura, CodAngajament, IdClsf, Clsf, Suma, Tip, UN, IDRR, IDRH, IDR,
                                                                        DataR, TipReceptie, DescriereR, AsteaptaDescarcarea }],
                                                           angajamente: [{ CodAngajament, Descriere, Stare, Sursa }] }
  PUT    /api/efactura/primite/<id>/ke/clasificatii    body { linii: [{ IdLinie, IdClsf | null }] } -> the state as GET
  GET    /api/efactura/ke/angajamente?q=               -> { angajamente: [{ CodAngajament, Descriere, Alias, Stare, DataCreare, Parteneri }] }
  GET    /api/efactura/ke/angajament/<cod>/receptii    -> { indicatori: [...], receptii: [{ IDRR, DataR, TipReceptie, Descriere, IDRH, Total,
                                                           pe_clasificatie: [{ IdClsf, Clsf, Valoare, Alocat, Rest }] }] }
  POST   /api/efactura/primite/<id>/ke/verifica        body { CodAngajament, Tip, IDRR?, alocari: [{ IdClsf, Suma }] }
                                                       -> { ok, probleme, avertismente, randuri: [{ IdClsf, CodAI, CodIndicator, Clsf, ClsfSal, SS,
                                                                                                  Suma, ValoareVeche?, ValoareNoua? }] }
  POST   /api/efactura/primite/<id>/ke/legaturi        body { CodAngajament, Tip, IDRR?, IDRH?, IDR?, alocari } -> 201, the state as GET
  DELETE /api/efactura/primite/<id>/ke/legaturi/<idl>  -> the state as GET

Errors are `{ "error": "<text>", "reason": "<CODE>" }`. Audit journal: EF_KE_CLASIFICARE, EF_KE_LEGA, EF_KE_STERGE.
"""
from flask import g

from . import efactura_bp, primite_ke
from .factura_routes import _audit, _body, _guarded, _json, _query_text
from .tokens import EfEroare
from routes.auth.guard import require_session


def _role():
    return (getattr(g.session, "ctx", None) or {}).get("Role")


def _text(body, key):
    value = body.get(key)
    if not isinstance(value, str) or not value.strip():
        raise EfEroare(f"«{key}» trebuie sa fie text.", "CAMP_INVALID", 400)
    return value.strip()


def _optional_int(body, key):
    value = body.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise EfEroare(f"«{key}» trebuie sa fie un numar intreg.", "CAMP_INVALID", 400)
    return value


@efactura_bp.route("/api/efactura/primite/<int:id_primita>/ke", methods=["GET"])
@require_session
@_guarded("ke/starea")
def ke_starea(id_primita):
    return _json(primite_ke.starea(g.session.db_name, id_primita, _role()))


@efactura_bp.route("/api/efactura/primite/<int:id_primita>/ke/clasificatii", methods=["PUT"])
@require_session
@_guarded("ke/clasificatii")
def ke_clasificatii(id_primita):
    body = _body()
    if set(body) - {"linii"}:
        raise EfEroare("Corpul poate contine doar «linii».", "CAMP_NEPERMIS", 400)
    primite_ke.seteaza_clasificatii(g.session.db_name, id_primita, _role(), body.get("linii"))
    _audit("EF_KE_CLASIFICARE", str(id_primita), "linii=%d" % len(body.get("linii") or []))
    return _json(primite_ke.starea(g.session.db_name, id_primita, _role()))


@efactura_bp.route("/api/efactura/ke/angajamente", methods=["GET"])
@require_session
@_guarded("ke/angajamente")
def ke_angajamente():
    return _json(primite_ke.angajamente(g.session.db_name, _query_text()))


@efactura_bp.route("/api/efactura/ke/angajament/<path:cod>/receptii", methods=["GET"])
@require_session
@_guarded("ke/receptii")
def ke_receptii(cod):
    return _json(primite_ke.receptii_angajament(g.session.db_name, cod))


@efactura_bp.route("/api/efactura/primite/<int:id_primita>/ke/verifica", methods=["POST"])
@require_session
@_guarded("ke/verifica")
def ke_verifica(id_primita):
    body = _body()
    if set(body) - {"CodAngajament", "Tip", "IDRR", "alocari"}:
        raise EfEroare("Corpul contine campuri nepermise.", "CAMP_NEPERMIS", 400)
    return _json(primite_ke.verifica(g.session.db_name, id_primita, _role(), _text(body, "CodAngajament"), _text(body, "Tip"),
                                     body.get("alocari"), _optional_int(body, "IDRR")))


@efactura_bp.route("/api/efactura/primite/<int:id_primita>/ke/legaturi", methods=["POST"])
@require_session
@_guarded("ke/leaga")
def ke_leaga(id_primita):
    body = _body()
    if set(body) - {"CodAngajament", "Tip", "IDRR", "IDRH", "IDR", "alocari"}:
        raise EfEroare("Corpul contine campuri nepermise.", "CAMP_NEPERMIS", 400)
    state = primite_ke.leaga(g.session.db_name, id_primita, _role(), g.session.username, _text(body, "CodAngajament"),
                             _text(body, "Tip"), _optional_int(body, "IDRR"), _optional_int(body, "IDRH"), _optional_int(body, "IDR"),
                             body.get("alocari"))
    _audit("EF_KE_LEGA", str(id_primita), "cod=%s tip=%s" % (body.get("CodAngajament"), body.get("Tip")))
    return _json(state, 201)


@efactura_bp.route("/api/efactura/primite/<int:id_primita>/ke/legaturi/<int:id_legatura>", methods=["DELETE"])
@require_session
@_guarded("ke/sterge")
def ke_sterge(id_primita, id_legatura):
    state = primite_ke.sterge_legatura(g.session.db_name, id_primita, _role(), id_legatura)
    _audit("EF_KE_STERGE", str(id_primita), "legatura=%d" % id_legatura)
    return _json(state)
