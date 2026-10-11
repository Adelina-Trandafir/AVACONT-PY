# routes/efactura/trimitere_routes.py
"""
Routes for sending an issued invoice to ANAF and reading what ANAF says (slice 00EF-07). All `@require_session`; the unit is
the session's. The rules are in trimitere.py; the ANAF calls in anaf_api.py; the flow is described in trimitere.py.

  POST /api/efactura/facturi/<id>/trimite     body none (a draft), or { "corectie": { "Comentarii", "BT_13" } } (an accepted invoice);
                                              `atasament_pdf` (base64) = the classic PDF, required when the invoice has AtasamentOriginal (00EF-14)
        -> 200 { factura, id_incarcare, constatari }          ANAF took the file; the invoice is now «incarcata»
           409 INVALIDA (+ constatari) | RESPINSA_DE_VALIDATOR (+ mesaje) | ANAF_REFUZA_INCARCAREA (+ mesaje) | DEJA_TRIMISA |
               DEJA_ACCEPTATA | REFUZATA | TOKEN_NECESAR,   502 ANAF_INDISPONIBIL | VALIDARE_INDISPONIBILA
  POST /api/efactura/facturi/<id>/verifica    no body; the screen calls it 2-3 seconds after `trimite`, and again while it says in_prelucrare
        -> 200 { rezultat: acceptata | refuzata | in_prelucrare | necunoscut, stare_anaf, mesaj, factura }
  GET  /api/efactura/facturi/<id>/descarca    -> the zip of an accepted invoice (application/zip)
  GET  /api/efactura/facturi/<id>/pdf-anaf    -> the accepted invoice as ANAF draws it (application/pdf; slice 00EF-09)
  GET  /api/efactura/mesaje?zile=20&primite=1 -> { cui, zile, mesaje: [{ id, data_creare, id_incarcare, id_solicitare, cif_emitent,
                                                   cif_beneficiar, tip, detalii, deja_in_baza }] }
  GET  /api/efactura/mesaje/<id>/descarca -> the zip of one message (`id` = the message field of that name, NOT id_solicitare; fixed 00EF-17)

Errors are `{ "error": "<Romanian text>", "reason": "<CODE>", ... }`. Audit journal: EF_FACTURA_TRIMITE, EF_FACTURA_STARE.
No answer carries a token.
"""
from flask import current_app, g, request

from . import efactura_bp, trimitere
from .factura_routes import _SAFE_NAME, _audit, _body, _guarded, _json, _label, _query_int
from .tokens import EfEroare
from routes.auth.guard import require_session


def _zip_response(data, name):
    safe = _SAFE_NAME.sub("_", name)
    response = current_app.response_class(data, mimetype="application/zip")
    response.headers["Content-Disposition"] = 'attachment; filename="%s"' % safe
    response.headers["X-Nume-Fisier"] = safe
    return response


@efactura_bp.route("/api/efactura/facturi/<int:id_factura>/trimite", methods=["POST"])
@require_session
@_guarded("facturi/trimite")
def facturi_trimite(id_factura):
    data = request.get_json(silent=True)
    correction = None
    pdf_b64 = None
    if data is not None:
        body = _body()
        if set(body) - {"corectie", "atasament_pdf"}:
            raise EfEroare("Corpul poate conține doar «corectie» și «atasament_pdf».", "CAMP_NEPERMIS", 400)
        correction = body.get("corectie")
        pdf_b64 = body.get("atasament_pdf")
        if pdf_b64 is not None and not isinstance(pdf_b64, str):
            raise EfEroare("«atasament_pdf» trebuie să fie un text base64.", "CAMP_INVALID", 400)
    answer = trimitere.trimite(g.session.db_name, id_factura, correction, pdf_b64)
    _audit("EF_FACTURA_TRIMITE", _label(answer["factura"]),
           "IdFactura=%s id_incarcare=%s%s" % (id_factura, answer["id_incarcare"], " corectie" if correction is not None else ""))
    return _json(answer)


@efactura_bp.route("/api/efactura/facturi/<int:id_factura>/verifica", methods=["POST"])
@require_session
@_guarded("facturi/verifica")
def facturi_verifica(id_factura):
    answer = trimitere.verifica(g.session.db_name, id_factura)
    if answer["rezultat"] in ("acceptata", "refuzata"):
        _audit("EF_FACTURA_STARE", _label(answer["factura"]), "IdFactura=%s %s" % (id_factura, answer["rezultat"]))
    return _json(answer)


@efactura_bp.route("/api/efactura/facturi/<int:id_factura>/descarca", methods=["GET"])
@require_session
@_guarded("facturi/descarca")
def facturi_descarca(id_factura):
    return _zip_response(*trimitere.descarca(g.session.db_name, id_factura))


@efactura_bp.route("/api/efactura/facturi/<int:id_factura>/pdf-anaf", methods=["GET"])
@require_session
@_guarded("facturi/pdf-anaf")
def facturi_pdf_anaf(id_factura):
    data, name = trimitere.pdf_anaf(g.session.db_name, id_factura)
    safe = _SAFE_NAME.sub("_", name)
    response = current_app.response_class(data, mimetype="application/pdf")
    response.headers["Content-Disposition"] = 'inline; filename="%s"' % safe
    response.headers["X-Nume-Fisier"] = safe
    return response


@efactura_bp.route("/api/efactura/mesaje", methods=["GET"])
@require_session
@_guarded("mesaje")
def mesaje_list():
    days = _query_int("zile", 20, 1, trimitere.MAX_DAYS)
    received = request.args.get("primite", "1") not in ("0", "false")
    return _json(trimitere.mesaje(g.session.db_name, days, received))


@efactura_bp.route("/api/efactura/mesaje/<id_sol>/descarca", methods=["GET"])
@require_session
@_guarded("mesaje/descarca")
def mesaje_descarca(id_sol):
    return _zip_response(*trimitere.descarca_mesaj(g.session.db_name, id_sol))
