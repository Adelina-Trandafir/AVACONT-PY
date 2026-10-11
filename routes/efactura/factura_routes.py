# routes/efactura/factura_routes.py
"""
Routes of the ISSUED invoices of E-Factura (slice 00EF-06). All `@require_session` (K-BOT bearer): the unit is the
session's (`g.session.db_name`), never a parameter of the request. The rules are in facturi.py.

  Issuer         GET  /api/efactura/furnizor                       -> { exista, furnizor, are_facturi }  (the unit's row of
                                                                   AVACONT_COMUN.Unitati_Date, 00EF-13;
                                                                   are_facturi = the series and number are fixed)
                 GET|PUT /api/efactura/furnizor/conturi           { conturi: [{ Cont }] } -> { conturi: [{ IdCont, Cont, Banca }] }
                                                                   the unit's own IBANs (Unitati_Conturi); the bank is deduced from the account
                 PUT  /api/efactura/furnizor                       { Denumire, CodFiscal, Adresa, Orasul, Judetul, Mail,
                                                                     Telefon, SerieFactura, NumarInitial, AfiseazaPrimiteNoi }
                                                                   -> as GET; 409 SERIE_BLOCATA when invoices exist and the series or
                                                                   the first number changed
                 POST /api/efactura/furnizor/anaf                  -> as GET; takes the name, county, city and address from
                                                                   ANAF (as often as asked)
  Units          GET  /api/efactura/um?q=                          -> { um: [{ Cod, Explicatie }] }   (UN/ECE codes)
  Customers      GET  /api/efactura/clienti?q=&limit=              -> { clienti: [...] }
                 POST /api/efactura/clienti                        -> 201 the customer
                 POST /api/efactura/clienti/anaf                   { CodFiscal } -> { client } the fields ANAF knows (nothing written)
                 GET|PUT|DELETE /api/efactura/clienti/<id>
  Invoices       GET  /api/efactura/facturi?an=&q=&limit=          -> { facturi: [header + ClientDenumire] }, newest first
                 POST /api/efactura/facturi                        { IdClient, DataFactura, ContPlata, Comentarii, BT_13,
                                                                     AtasamentOriginal, linii: [{ NrCrt, Continut, Um, Cant, PU }] }
                                                                   -> 201 the invoice; series and number are the server's
                 GET  /api/efactura/facturi/numar-urmator          -> { serie, numar }
                 GET|PUT|DELETE /api/efactura/facturi/<id>         PUT: a draft only (an accepted invoice is
                                                                   corrected by sending it again, see trimitere_routes.py)
                 POST /api/efactura/facturi/<id>/storno            body as POST /facturi (the replacement)
                                                                   -> 201 { storno, factura }
                 GET  /api/efactura/facturi/<id>/xml               -> the UBL file (application/xml)
                 POST /api/efactura/facturi/<id>/valideaza         { anaf: true|false } (default false)
                                                                   -> { constatari, valida_local, anaf, valida, nume_fisier }

Errors are `{ "error": "<Romanian text>", "reason": "<CODE>" }` (400 bad input, 404, 409 not allowed now, 503 tables
missing). An invoice's detail carries `stare` (ciorna / incarcata / refuzata / acceptata) and `poate_modifica`,
`poate_corecta`, `poate_storna`, `poate_sterge`, so the screen never repeats the rules.
Audit journal (`Jurnal`): EF_FURNIZOR_MODIFICA, EF_FACTURA_ADAUGA / _MODIFICA / _STERGE / _STORNO.
"""
import json
import logging
import re
from functools import wraps

from flask import current_app, g, request

from routes.auth.auth import log_action
from routes.auth.guard import require_session

from . import efactura_bp, facturi
from .tokens import EfEroare

logger = logging.getLogger(__name__)

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]")
_MAX_Q = 100


def _json(payload, status=200):
    """JSON with LITERAL diacritics (ensure_ascii=False): `error` is Romanian text for the operator."""
    body = json.dumps(payload, ensure_ascii=False)
    return current_app.response_class(body, status=status, mimetype="application/json")


def _guarded(where):
    """Turns a refusal into `{error, reason}` and any other failure into the generic 500 (logged with its stack)."""
    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            try:
                return view(*args, **kwargs)
            except EfEroare as err:
                return _json({"error": str(err), "reason": err.reason, **getattr(err, "extra", {})}, err.status)
            except Exception as err:
                logger.error("[efactura] %s: %s", where, type(err).__name__, exc_info=True)
                return _json({"error": "Eroare internă la procesarea facturii E-Factura.", "reason": "EROARE_SERVER"}, 500)
        return wrapper
    return decorator


def _body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise EfEroare("Corpul cererii nu este JSON.", "CORP_INVALID", 400)
    return data


def _query_text():
    text = (request.args.get("q") or "").strip()
    if len(text) > _MAX_Q:
        raise EfEroare("Textul căutat este prea lung.", "CAMP_INVALID", 400)
    return text or None


def _query_int(name, default, minimum, maximum):
    raw = request.args.get(name)
    if raw is None or raw == "":
        return default
    try:
        number = int(raw)
    except ValueError:
        raise EfEroare(f"Parametrul «{name}» nu este un număr.", "CAMP_INVALID", 400) from None
    if not minimum <= number <= maximum:
        raise EfEroare(f"Parametrul «{name}» este în afara limitelor.", "CAMP_INVALID", 400)
    return number


def _audit(action, target=None, details=None):
    session = g.session
    log_action(session.username, session.db_name, action, tinta=target, detalii=details,
               masina=session.pcname, ip=request.remote_addr)


def _label(invoice):
    """«SERIE_NUMAR» of an invoice detail, for the journal."""
    parts = [str(invoice.get("SerieFactura") or ""), str(invoice["NumarFactura"])]
    return "_".join(part for part in parts if part)


# ---------------------------------------------------------------------------------------------
# issuer and lists
# ---------------------------------------------------------------------------------------------
@efactura_bp.route("/api/efactura/furnizor", methods=["GET"])
@require_session
@_guarded("furnizor/get")
def furnizor_get():
    return _json(facturi.furnizor_get(g.session.db_name))


@efactura_bp.route("/api/efactura/furnizor", methods=["PUT"])
@require_session
@_guarded("furnizor/put")
def furnizor_put():
    answer = facturi.furnizor_set(g.session.db_name, _body())
    _audit("EF_FURNIZOR_MODIFICA", answer["furnizor"]["CodFiscal"])
    return _json(answer)


@efactura_bp.route("/api/efactura/furnizor/anaf", methods=["POST"])
@require_session
@_guarded("furnizor/anaf")
def furnizor_anaf():
    answer = facturi.furnizor_anaf(g.session.db_name)
    _audit("EF_FURNIZOR_ANAF", answer["furnizor"]["CodFiscal"])
    return _json(answer)


@efactura_bp.route("/api/efactura/furnizor/conturi", methods=["GET"])
@require_session
@_guarded("furnizor/conturi/get")
def furnizor_conturi_get():
    return _json(facturi.conturi_get(g.session.db_name))


@efactura_bp.route("/api/efactura/furnizor/conturi", methods=["PUT"])
@require_session
@_guarded("furnizor/conturi/put")
def furnizor_conturi_put():
    answer = facturi.conturi_set(g.session.db_name, _body())
    _audit("EF_FURNIZOR_CONTURI", None, "conturi=%d" % len(answer["conturi"]))
    return _json(answer)


@efactura_bp.route("/api/efactura/um", methods=["GET"])
@require_session
@_guarded("um")
def um_list():
    return _json(facturi.um_list(_query_text()))


# ---------------------------------------------------------------------------------------------
# customers
# ---------------------------------------------------------------------------------------------
@efactura_bp.route("/api/efactura/clienti", methods=["GET"])
@require_session
@_guarded("clienti/list")
def clienti_list():
    return _json(facturi.clienti_list(g.session.db_name, _query_text(), _query_int("limit", 200, 1, facturi.MAX_LIST)))


@efactura_bp.route("/api/efactura/clienti", methods=["POST"])
@require_session
@_guarded("clienti/post")
def clienti_post():
    return _json(facturi.client_create(g.session.db_name, _body()), 201)


@efactura_bp.route("/api/efactura/clienti/anaf", methods=["POST"])
@require_session
@_guarded("clienti/anaf")
def clienti_anaf():
    return _json(facturi.client_anaf(g.session.db_name, _body()))


@efactura_bp.route("/api/efactura/clienti/<int:id_client>", methods=["GET"])
@require_session
@_guarded("clienti/get")
def clienti_get(id_client):
    return _json(facturi.client_get(g.session.db_name, id_client))


@efactura_bp.route("/api/efactura/clienti/<int:id_client>", methods=["PUT"])
@require_session
@_guarded("clienti/put")
def clienti_put(id_client):
    return _json(facturi.client_update(g.session.db_name, id_client, _body()))


@efactura_bp.route("/api/efactura/clienti/<int:id_client>", methods=["DELETE"])
@require_session
@_guarded("clienti/delete")
def clienti_delete(id_client):
    return _json(facturi.client_delete(g.session.db_name, id_client))


# ---------------------------------------------------------------------------------------------
# invoices
# ---------------------------------------------------------------------------------------------
@efactura_bp.route("/api/efactura/facturi", methods=["GET"])
@require_session
@_guarded("facturi/list")
def facturi_list():
    return _json(facturi.facturi_list(
        g.session.db_name, _query_int("an", None, 2000, 2100), _query_text(),
        _query_int("limit", facturi.MAX_LIST if request.args.get("an") else facturi.DEFAULT_LIST, 1, facturi.MAX_LIST)))


@efactura_bp.route("/api/efactura/facturi", methods=["POST"])
@require_session
@_guarded("facturi/post")
def facturi_post():
    invoice = facturi.factura_create(g.session.db_name, _body())
    _audit("EF_FACTURA_ADAUGA", _label(invoice), "IdFactura=%s total=%s" % (invoice["IdFactura"], invoice["total"]))
    return _json(invoice, 201)


@efactura_bp.route("/api/efactura/facturi/numar-urmator", methods=["GET"])
@require_session
@_guarded("facturi/numar-urmator")
def facturi_numar_urmator():
    return _json(facturi.numar_urmator(g.session.db_name))


@efactura_bp.route("/api/efactura/facturi/<int:id_factura>", methods=["GET"])
@require_session
@_guarded("facturi/get")
def facturi_get(id_factura):
    return _json(facturi.factura_get(g.session.db_name, id_factura))


@efactura_bp.route("/api/efactura/facturi/<int:id_factura>", methods=["PUT"])
@require_session
@_guarded("facturi/put")
def facturi_put(id_factura):
    invoice = facturi.factura_update(g.session.db_name, id_factura, _body())
    _audit("EF_FACTURA_MODIFICA", _label(invoice), "IdFactura=%s" % invoice["IdFactura"])
    return _json(invoice)


@efactura_bp.route("/api/efactura/facturi/<int:id_factura>", methods=["DELETE"])
@require_session
@_guarded("facturi/delete")
def facturi_delete(id_factura):
    before = facturi.factura_get(g.session.db_name, id_factura)
    answer = facturi.factura_delete(g.session.db_name, id_factura)
    _audit("EF_FACTURA_STERGE", _label(before), "IdFactura=%s" % id_factura)
    return _json(answer)


@efactura_bp.route("/api/efactura/facturi/<int:id_factura>/storno", methods=["POST"])
@require_session
@_guarded("facturi/storno")
def facturi_storno(id_factura):
    answer = facturi.factura_storno(g.session.db_name, id_factura, _body())
    _audit("EF_FACTURA_STORNO", _label(answer["storno"]),
           "cancels IdFactura=%s; replaced by %s" % (id_factura, _label(answer["factura"])))
    return _json(answer, 201)


@efactura_bp.route("/api/efactura/facturi/<int:id_factura>/xml", methods=["GET"])
@require_session
@_guarded("facturi/xml")
def facturi_xml(id_factura):
    xml, name = facturi.factura_xml(g.session.db_name, id_factura)
    safe = _SAFE_NAME.sub("_", name)
    response = current_app.response_class(xml, mimetype="application/xml")
    response.headers["Content-Disposition"] = 'inline; filename="%s"' % safe
    response.headers["X-Nume-Fisier"] = safe
    return response


@efactura_bp.route("/api/efactura/facturi/<int:id_factura>/valideaza", methods=["POST"])
@require_session
@_guarded("facturi/valideaza")
def facturi_valideaza(id_factura):
    data = request.get_json(silent=True)
    with_anaf = isinstance(data, dict) and data.get("anaf") is True
    return _json(facturi.factura_valideaza(g.session.db_name, id_factura, with_anaf))
