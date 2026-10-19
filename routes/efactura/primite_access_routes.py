# routes/efactura/primite_access_routes.py
"""
Routes of the exchange with the Access e-invoice store (slice 00EF-43). All `@require_session`; the unit is the session's. The rules are in
primite_access.py, the link to receptions in primite_access_legare.py. Only a client that runs the Access application uses them.

  GET  /api/efactura/primite/access/stare
        -> 200 { cui }                                   the unit's tax code (the PC keeps only the Access messages of this code)
  POST /api/efactura/primite/access/preia                body { mesaje: [ { IdSol, IdIncarcare, CifEmitent, DataMesaj, CuiUnitate, Nou,
                                                           NumeFisier, Xml (base64), DataAdaugare,
                                                           facturi: [ { NrFact, DataFact, DataScad, CotaTVA, TVA, Valoare, Total, CUI,
                                                             DenumireP, Adresa, Atasament, Tip, Semn, Ref, DataAdaugare,
                                                             linii: [ { NrLinie, Denumire, Explicatie, Unit, Cant, Pret, Valoare } ],
                                                             note: [ { Nota, DataAdaugare } ],
                                                             mesaje: [ { IdSol, IdMesajAnaf, Mesaj, DataMesaj, DataAdaugare } ],
                                                             operatii: [ { IdOperatiePrimita, IdOperatie, IdClsfAcc, IdUnitate, Sursa, Suma,
                                                                           DataAdaugare } ] } ] } ] }
        -> 200 { mesaje_noi, mesaje_sarite, facturi_noi, facturi_sarite, linii, note, mesaje_factura, operatii, operatii_sarite,
                 id_primite: [..the invoices ADDED..], probleme: [text], erori: [{ id_sol, motiv }] }
  POST /api/efactura/primite/access/finalizeaza        body { id_primite: [..], referinte: [ { IdSol, NrFact, RefIdSol, RefNrFact } ] }
        -> 200 { referinte_setate, legare: { citite, legate, fara_furnizor, fara_receptie, nesigure, mai_multe_clasificatii, conflicte, omisa } }
  POST /api/efactura/primite/access/exporta            body { an, deja: [IdSol, ..], limita }
        -> 200 { mesaje: [ the same shape as `preia` takes, plus IdPrimitaRef info as RefIdSol / RefNrFact ], ramase, total_lipsa }

Errors are `{ "error": "<text>", "reason": "<CODE>" }`. Audit journal: EF_PRIMITE_ACCESS_PREIA, EF_PRIMITE_ACCESS_EXPORTA.
"""
from . import efactura_bp, primite_access
from .factura_routes import _audit, _body, _guarded, _json
from routes.auth.guard import require_session
from flask import g


@efactura_bp.route("/api/efactura/primite/access/stare", methods=["GET"])
@require_session
@_guarded("primite/access/stare")
def primite_access_stare():
    return _json(primite_access.stare(g.session.db_name))


@efactura_bp.route("/api/efactura/primite/access/preia", methods=["POST"])
@require_session
@_guarded("primite/access/preia")
def primite_access_preia():
    body = _body()
    answer = primite_access.preia(g.session.db_name, body.get("mesaje"))
    if answer["mesaje_noi"] or answer["facturi_noi"] or answer["operatii"]:
        _audit("EF_PRIMITE_ACCESS_PREIA", None, "mesaje=%d facturi=%d operatii=%d erori=%d" % (
            answer["mesaje_noi"], answer["facturi_noi"], answer["operatii"], len(answer["erori"])))
    return _json(answer)


@efactura_bp.route("/api/efactura/primite/access/finalizeaza", methods=["POST"])
@require_session
@_guarded("primite/access/finalizeaza")
def primite_access_finalizeaza():
    body = _body()
    answer = primite_access.finalizeaza(g.session.db_name, body.get("id_primite", []), body.get("referinte", []))
    if answer["legare"]["legate"]:
        _audit("EF_PRIMITE_ACCESS_PREIA", None, "legate automat=%d" % answer["legare"]["legate"])
    return _json(answer)


@efactura_bp.route("/api/efactura/primite/access/exporta", methods=["POST"])
@require_session
@_guarded("primite/access/exporta")
def primite_access_exporta():
    body = _body()
    answer = primite_access.exporta(g.session.db_name, body.get("an"), body.get("deja", []), body.get("limita", 20))
    if answer["mesaje"]:
        _audit("EF_PRIMITE_ACCESS_EXPORTA", None, "mesaje=%d ramase=%d" % (len(answer["mesaje"]), answer["ramase"]))
    return _json(answer)
