# routes/efactura/primite_contari_routes.py
"""
Route of the «Contari» of the received invoices (slice 01EF-02). `@require_session`; the unit and the role are the session's. The rules are in
primite_contari.py. It only CALCULATES; it writes nothing.

  POST /api/efactura/contari/calcul   body { ids: [IdPrimita...], idClsf?, idSablonE?, idSablonP?, platit?: { "<IdPrimita>": value }, an? }
        -> { poate_scrie, furnizor, clasificatii, clasificatie, clasificatieSugerata, sabloaneEvidentiere, sabloanePlata, idSablonE, idSablonP,
             subunitate: { IdSubunitate, Denumire } | null, subunitati: [{ IdSubunitate, Denumire }]  (those with the sector+source of the classification),
             note: { An, Urmatoare, Existente: [{ NumarNC, DataNc, Documente }] } | null  (the notes of the subunit and year, to choose from or to type a new number),
             coduri, contIban, evidenta: [...], plata: {...}, operatii: [...], avertismente: [{ Cod, Grav, Text }] }
        body fields: ids, idClsf, idSablonE, idSablonP, platit, an, idSubunitate (when several subunits share the sector+source), numarNC (save only)

POST /api/efactura/contari/salveaza  body as above + { confirmari: [codes], idSubunitate? } -> 201 { IdNota, NumarNC, An, Subunitate, Operatii: [...] }   (01EF-04: books the EVIDENCE, role «Contabil»)
        refusal 409 CONFIRMARE_NECESARA carries { confirmari_necesare: [{ Cod, Text }] }: the operator confirms them and the call is repeated with their codes in `confirmari`.

Errors are `{ "error": "<text>", "reason": "<CODE>" }`. Audit journal: EF_CONTARE.
"""
from flask import g

from . import efactura_bp, primite_contari
from .factura_routes import _audit, _body, _guarded, _json
from .tokens import EfEroare
from routes.auth.guard import require_session

_CAMPURI = {"ids", "idClsf", "idSablonE", "idSablonP", "platit", "an", "idSubunitate", "numarNC"}


@efactura_bp.route("/api/efactura/contari/calcul", methods=["POST"])
@require_session
@_guarded("contari/calcul")
def contari_calcul():
    body = _body()
    if set(body) - _CAMPURI:
        raise EfEroare("Corpul conține câmpuri nepermise.", "CAMP_NEPERMIS", 400)
    role = (getattr(g.session, "ctx", None) or {}).get("Role")
    return _json(primite_contari.calcul(g.session.db_name, body, role))


@efactura_bp.route("/api/efactura/contari/salveaza", methods=["POST"])
@require_session
@_guarded("contari/salveaza")
def contari_salveaza():
    body = _body()
    if set(body) - (_CAMPURI | {"confirmari"}):
        raise EfEroare("Corpul conține câmpuri nepermise.", "CAMP_NEPERMIS", 400)
    role = (getattr(g.session, "ctx", None) or {}).get("Role")
    rezultat = primite_contari.salveaza(g.session.db_name, body, role, g.session.username)
    _audit("EF_CONTARE", ",".join(str(i) for i in body.get("ids", [])), "NC=%s operatii=%d" % (rezultat["NumarNC"], len(rezultat["Operatii"])))
    return _json(rezultat, 201)
