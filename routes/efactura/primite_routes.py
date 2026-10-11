# routes/efactura/primite_routes.py
"""
Routes of the received e-invoices (slice 00EF-17). All `@require_session`; the unit is the session's. The rules are in primite.py.

  POST /api/efactura/primite/sincronizeaza   body { "zile": 1..60, "limita": 1..50 (default 20) }
        -> 200 { cui, zile, gasite, adaugate, sarite, ramase, erori: [{ id_solicitare, motiv }] }
           the screen repeats the call while `ramase` > 0 (a progress bar); nothing is doubled (EF_Mesaje.IdSol is unique)
  GET  /api/efactura/primite?an=&luna=&q=&iddf=&cui=
        -> 200 { facturi: [{ IdPrimita, IdSol, NrFact, DataFact, DataScad, CotaTVA, TVA, Valoare, Total, CUI, CuiNormalizat,
                             DenumireP, Atasament, Tip, Semn, Ref, IdPrimitaRef, Nou, legatura? }] }   (amounts as numbers)
  GET  /api/efactura/primite/<id>            -> { factura, linii, cote: [{ Categorie, CotaTVA, Baza, TVA }], note, mesaje, atasamente: [{ nume, mime, octeti }],
                                                  legaturi: { auto: [{ IDDF, CodAngajament }], manual: [...] } }
  POST /api/efactura/primite/<id>/citita     -> 204, the message is no longer «nou»
  GET  /api/efactura/primite/<id>/xml        -> the XML kept from ANAF
  GET  /api/efactura/primite/<id>/zip        -> the signed zip, downloaded again from ANAF
  GET  /api/efactura/primite/<id>/pdf        -> the invoice drawn by ANAF's service (application/pdf)
  GET  /api/efactura/primite/<id>/atasamente/<n> -> the n-th (0-based) file embedded in the XML
  GET  /api/efactura/primite-ddf?q=          -> { ddf: [{ IDDF, CodAngajament, ObiectDDF, NumePartener, CodFiscal }] }  (picker for a manual link, 00EF-20)
  POST /api/efactura/primite/<id>/asociere   body { "iddf": n }   manual link to a DDF
  DELETE /api/efactura/primite/<id>/asociere/<iddf>

Errors are `{ "error": "<text>", "reason": "<CODE>" }`. Audit journal: EF_PRIMITE_SINCRONIZEAZA, EF_PRIMITE_ASOCIERE.
"""
from flask import current_app, g, request

from . import efactura_bp, primite
from .factura_routes import _SAFE_NAME, _audit, _body, _guarded, _json, _query_int
from .tokens import EfEroare
from routes.auth.guard import require_session


def _file_response(data, name, mimetype, inline=False):
    safe = _SAFE_NAME.sub("_", name)
    response = current_app.response_class(data, mimetype=mimetype)
    response.headers["Content-Disposition"] = '%s; filename="%s"' % ("inline" if inline else "attachment", safe)
    response.headers["X-Nume-Fisier"] = safe
    return response


def _whole(body, key, minimum, maximum, default=None):
    value = body.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise EfEroare(f"«{key}» trebuie sa fie un numar intre {minimum} si {maximum}.", "CAMP_INVALID", 400)
    return value


@efactura_bp.route("/api/efactura/primite/sincronizeaza", methods=["POST"])
@require_session
@_guarded("primite/sincronizeaza")
def primite_sincronizeaza():
    body = _body()
    if set(body) - {"zile", "limita"}:
        raise EfEroare("Corpul poate contine doar «zile» si «limita».", "CAMP_NEPERMIS", 400)
    answer = primite.sincronizeaza(g.session.db_name, _whole(body, "zile", 1, 60),
                                   _whole(body, "limita", 1, primite.MAX_BATCH, primite.DEFAULT_BATCH))
    if answer["adaugate"]:
        _audit("EF_PRIMITE_SINCRONIZEAZA", answer["cui"], "adaugate=%d ramase=%d erori=%d" % (
            answer["adaugate"], answer["ramase"], len(answer["erori"])))
    return _json(answer)


@efactura_bp.route("/api/efactura/primite", methods=["GET"])
@require_session
@_guarded("primite/lista")
def primite_lista():
    year = _query_int("an", None, 1990, 2100)
    month = _query_int("luna", None, 1, 12)
    iddf = _query_int("iddf", None, 1, 2147483647)
    return _json(primite.lista(g.session.db_name, year, month, (request.args.get("q") or "").strip()[:100] or None,
                               iddf, (request.args.get("cui") or "").strip()[:32] or None))


@efactura_bp.route("/api/efactura/primite/<int:id_primita>", methods=["GET"])
@require_session
@_guarded("primite/detaliu")
def primite_detaliu(id_primita):
    return _json(primite.detaliu(g.session.db_name, id_primita))


@efactura_bp.route("/api/efactura/primite/<int:id_primita>/citita", methods=["POST"])
@require_session
@_guarded("primite/citita")
def primite_citita(id_primita):
    primite.marcheaza_citita(g.session.db_name, id_primita)
    return current_app.response_class(status=204)


@efactura_bp.route("/api/efactura/primite/<int:id_primita>/xml", methods=["GET"])
@require_session
@_guarded("primite/xml")
def primite_xml(id_primita):
    return _file_response(*primite.xml(g.session.db_name, id_primita), "application/xml")


@efactura_bp.route("/api/efactura/primite/<int:id_primita>/zip", methods=["GET"])
@require_session
@_guarded("primite/zip")
def primite_zip(id_primita):
    return _file_response(*primite.zip_anaf(g.session.db_name, id_primita), "application/zip")


@efactura_bp.route("/api/efactura/primite/<int:id_primita>/pdf", methods=["GET"])
@require_session
@_guarded("primite/pdf")
def primite_pdf(id_primita):
    return _file_response(*primite.pdf_anaf(g.session.db_name, id_primita), "application/pdf", inline=True)


@efactura_bp.route("/api/efactura/primite/<int:id_primita>/atasamente/<int:index>", methods=["GET"])
@require_session
@_guarded("primite/atasament")
def primite_atasament(id_primita, index):
    data, name, mime = primite.atasament(g.session.db_name, id_primita, index)
    return _file_response(data, name, mime, inline=mime == "application/pdf")


@efactura_bp.route("/api/efactura/primite/<int:id_primita>/asociere", methods=["POST"])
@require_session
@_guarded("primite/asociere")
def primite_asociaza(id_primita):
    body = _body()
    if set(body) - {"iddf"}:
        raise EfEroare("Corpul poate contine doar «iddf».", "CAMP_NEPERMIS", 400)
    iddf = _whole(body, "iddf", 1, 2147483647)
    primite.asociaza(g.session.db_name, id_primita, iddf, g.session.username)
    _audit("EF_PRIMITE_ASOCIERE", str(id_primita), "IDDF=%s" % iddf)
    return current_app.response_class(status=204)


@efactura_bp.route("/api/efactura/primite/<int:id_primita>/asociere/<int:iddf>", methods=["DELETE"])
@require_session
@_guarded("primite/dezasociere")
def primite_dezasociaza(id_primita, iddf):
    primite.desasociaza(g.session.db_name, id_primita, iddf)
    _audit("EF_PRIMITE_ASOCIERE", str(id_primita), "scos IDDF=%s" % iddf)
    return current_app.response_class(status=204)


@efactura_bp.route("/api/efactura/primite-ddf", methods=["GET"])
@require_session
@_guarded("primite/ddf")
def primite_ddf_lista():
    return _json(primite.lista_ddf(g.session.db_name, (request.args.get("q") or "").strip()[:100] or None))
