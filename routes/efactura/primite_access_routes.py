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
                                                                           DataAdaugare } ] } ] } ],
                                                           context: { an, fisier, cui, lot, loturi, cerute, citite_lot, citite }  (optional) }
        -> 200 { mesaje_noi, mesaje_sarite, facturi_noi, facturi_sarite, linii, note, mesaje_factura, operatii, operatii_sarite,
                 id_primite: [..the invoices ADDED..], probleme: [text], erori: [{ id_sol, motiv }] }
  POST /api/efactura/primite/access/finalizeaza        body { id_primite: [..], referinte: [ { IdSol, NrFact, RefIdSol, RefNrFact } ],
                                                           context: { an, fisier, cui, acces_total, citite, necitite_total, necitite: [id] }  (optional) }
        -> 200 { referinte_setate, legare: { citite, legate, fara_furnizor, fara_receptie, nesigure, mai_multe_clasificatii, conflicte, omisa } }
  POST /api/efactura/primite/access/exporta            body { an, deja: [IdSol, ..], limita }
        -> 200 { mesaje: [ the same shape as `preia` takes, plus IdPrimitaRef info as RefIdSol / RefNrFact ], ramase, total_lipsa }

Errors are `{ "error": "<text>", "reason": "<CODE>" }`. Audit journal (AVACONT_COMUN.Jurnal): EF_PRIMITE_ACCESS_PREIA (one row per batch, always), EF_PRIMITE_ACCESS_FINAL (end of a run, always),
EF_PRIMITE_ACCESS_EXPORTA. What the rows hold: primite_access_jurnal.py.
"""
import time

from . import efactura_bp, primite_access, primite_access_jurnal as jurnal
from .factura_routes import _audit, _body, _guarded, _json
from routes.auth.guard import require_session
from flask import g


def _ms(start):
    return int((time.monotonic() - start) * 1000)


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
    mesaje, ctx, start = body.get("mesaje"), jurnal.context(body.get("context")), time.monotonic()
    try:
        answer = primite_access.preia(g.session.db_name, mesaje)
    except Exception as err:
        _audit("EF_PRIMITE_ACCESS_PREIA", None, jurnal.detalii_eroare(mesaje, ctx, err, _ms(start)), "EROARE")
        raise
    # one row per batch, also when nothing new was added (slice 00EF-44): see primite_access_jurnal.py
    _audit("EF_PRIMITE_ACCESS_PREIA", None, jurnal.detalii_preia(answer, mesaje, ctx, _ms(start)), jurnal.rezultat_preia(answer))
    return _json(answer)


@efactura_bp.route("/api/efactura/primite/access/finalizeaza", methods=["POST"])
@require_session
@_guarded("primite/access/finalizeaza")
def primite_access_finalizeaza():
    body = _body()
    ids, referinte = body.get("id_primite", []), body.get("referinte", [])
    ctx, start = jurnal.context(body.get("context")), time.monotonic()
    try:
        answer = primite_access.finalizeaza(g.session.db_name, ids, referinte)
    except Exception as err:
        _audit("EF_PRIMITE_ACCESS_FINAL", None, jurnal.detalii_eroare(None, ctx, err, _ms(start)), "EROARE")
        raise
    _audit("EF_PRIMITE_ACCESS_FINAL", None,
           jurnal.detalii_final(answer, len(ids) if isinstance(ids, list) else 0, len(referinte) if isinstance(referinte, list) else 0,
                                ctx, _ms(start)))
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
