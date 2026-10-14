# routes/actiuni.py
"""
Recent actions of an operator: the data writes, written to the audit table `Jurnal` and read back.

WRITE SIDE. `init_app(app)` registers one `after_request` hook. After a request that

  - came through an authenticated session (g.session),
  - has a method that writes (POST / PUT / DELETE),
  - matched a route listed in `_LABELS` below, and
  - succeeded (status < 400),

one row goes to `Jurnal` (Actiune = 'SCRIERE', Tinta = the route, Detalii = the Romanian sentence the
operator reads in the side panel). The list is a WHITELIST on purpose: many POST routes only read (lists,
checks), and a new route must be named here by someone who knows what it does. Best-effort like every
audit write: a failure is logged and never reaches the caller.

READ SIDE.
    GET /api/actiuni?n=10
    200 { "actiuni": [ {"moment": "<ISO, UTC>", "text": "...", "tinta": "<route>"}, ... ] }
Only the caller's own rows, of the unit they are connected to (the user and the unit come from the session,
never from the query), newest first. A server without this module simply has no route: the client shows an
empty list.
"""
import logging
from datetime import datetime, timezone

import mysql.connector
from flask import Blueprint, g, request

from routes.auth.guard import require_session, json_response
from utils.database import get_kbot_connection, COMMON_DB

logger = logging.getLogger(__name__)

actiuni_bp = Blueprint("actiuni", __name__)

ACTION_NAME = "SCRIERE"
DEFAULT_COUNT = 10
MAX_COUNT = 50

# (METHOD, Flask rule) -> the sentence the operator reads.
_LABELS = {
    # DDF / ORD
    ("POST", "/api/forexe/ddf/save"): "Salvat un document DDF",
    ("DELETE", "/api/forexe/ddf/<int:iddf>"): "Șters un document DDF",
    ("DELETE", "/api/forexe/ddf/rev/<int:idrev>"): "Ștearsă o revizie DDF",
    ("DELETE", "/api/forexe/ddf/<int:iddf>/luna/<int:an>/<int:luna>"): "Ștearsă o lună dintr-un DDF",
    ("POST", "/api/forexe/ddf/trimitere/<int:idrev>/start"): "Pornită trimiterea unui DDF",
    ("POST", "/api/forexe/ddf/trimitere/<int:idrev>/anuleaza-semnatura"): "Anulată semnătura unui DDF",
    ("POST", "/api/forexe/ord/save"): "Salvată o ordonanțare (ORD)",
    ("DELETE", "/api/forexe/ord/<int:idordp>"): "Ștearsă o ordonanțare (ORD)",
    # Note CAB / operatiuni
    ("POST", "/api/forexe/note-cab"): "Creată o notă de corecție CAB",
    ("POST", "/api/forexe/note-cab/lot"): "Create note de corecție CAB (lot)",
    ("POST", "/api/forexe/note-cab/<int:idnc>/trimitere"): "Trimisă o notă de corecție CAB",
    # Angajamente, grupe, extrase, asocieri
    ("POST", "/api/forexe/angajamente/upsert"): "Actualizat un angajament",
    ("POST", "/api/forexe/grupe"): "Salvată o grupă de angajamente",
    ("POST", "/api/forexe/grupe/<int:idgr>/angajamente"): "Modificată componența unei grupe",
    ("POST", "/api/forexe/grupe/alias"): "Salvat un alias de grupă",
    ("POST", "/api/forexe/extrase/import"): "Importat extrase de cont",
    ("POST", "/api/forexe/asociere"): "Asociere recepții",
    ("POST", "/api/forexe/asociere/corectie"): "Corecție de asociere",
    ("POST", "/api/forexe/prelucrare"): "Prelucrare FOREXE",
    ("POST", "/api/forexe/istoric/sterge-luna"): "Ștearsă o lună din istoric",
    ("POST", "/api/forexe/receptii/refacere"): "Refăcute recepțiile",
    ("POST", "/api/forexe/rezervari/reanaliza"): "Reanalizate rezervările",
    # Nomenclatoare
    ("POST", "/api/forexe/nomenclatoare/clasificatii/adauga"): "Adăugată o clasificație",
    ("POST", "/api/forexe/nomenclatoare/clasificatii/<int:id_clsf>/buget"): "Modificat bugetul unei clasificații",
    ("POST", "/api/forexe/nomenclatoare/parteneri"): "Salvat un partener",
    ("DELETE", "/api/forexe/nomenclatoare/parteneri/<int:id_partener>"): "Șters un partener",
    ("POST", "/api/clasificatii/insert"): "Adăugate clasificații",
    ("POST", "/api/clasificatii/clasificatii_complete_upsert"): "Actualizate clasificațiile",
    ("POST", "/api/parteneri/upsert"): "Actualizați parteneri",
    # E-Factura
    ("PUT", "/api/efactura/furnizor"): "Salvate datele unității (furnizor E-Factura)",
    ("PUT", "/api/efactura/furnizor/conturi"): "Salvate conturile unității",
    ("POST", "/api/efactura/clienti"): "Adăugat un client E-Factura",
    ("PUT", "/api/efactura/clienti/<int:id_client>"): "Modificat un client E-Factura",
    ("DELETE", "/api/efactura/clienti/<int:id_client>"): "Șters un client E-Factura",
    ("POST", "/api/efactura/facturi"): "Creată o factură E-Factura",
    ("PUT", "/api/efactura/facturi/<int:id_factura>"): "Modificată o factură E-Factura",
    ("DELETE", "/api/efactura/facturi/<int:id_factura>"): "Ștearsă o factură E-Factura",
    ("POST", "/api/efactura/facturi/<int:id_factura>/storno"): "Stornată o factură E-Factura",
    ("POST", "/api/efactura/facturi/<int:id_factura>/trimite"): "Trimisă o factură la ANAF",
    ("POST", "/api/efactura/primite/sincronizeaza"): "Sincronizate facturile primite de la ANAF",
    ("POST", "/api/efactura/primite/<int:id_primita>/asociere"): "Asociată o factură primită",
}


def init_app(app):
    """Registers the blueprint and the after_request hook on the Flask app."""
    app.register_blueprint(actiuni_bp)
    app.after_request(_record_write)


def _record_write(response):
    try:
        if request.method not in ("POST", "PUT", "DELETE") or response.status_code >= 400:
            return response
        session = getattr(g, "session", None)
        rule = request.url_rule.rule if request.url_rule is not None else None
        label = _LABELS.get((request.method, rule))
        if session is None or label is None:
            return response
        _insert(session, label, rule)
    except Exception:       # noqa: BLE001  the audit must never break the answer
        logger.exception("[actiuni] write hook failed")
    return response


def _insert(session, label, rule):
    conn = None
    try:
        conn = get_kbot_connection(COMMON_DB)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO Jurnal (Moment, UN, DC, Actiune, Tinta, Detalii, Rezultat, Masina, IP) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (datetime.now(timezone.utc), session.username, session.db_name, ACTION_NAME,
             rule[:128], label, "OK", (getattr(session, "pcname", "") or None), request.remote_addr))
        conn.commit()
    except mysql.connector.Error as err:
        logger.error("[actiuni] audit write failed (%s / %s): %s", rule, session.username, err)
    finally:
        if conn is not None and conn.is_connected():
            conn.close()


@actiuni_bp.route("/api/actiuni", methods=["GET"])
@require_session
def actiuni_recente():
    try:
        try:
            n = max(1, min(MAX_COUNT, int(request.args.get("n", DEFAULT_COUNT))))
        except (TypeError, ValueError):
            n = DEFAULT_COUNT
        conn = get_kbot_connection(COMMON_DB)
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT Moment, Detalii, Tinta FROM Jurnal WHERE UN = %s AND DC = %s AND Actiune = %s "
                "ORDER BY Id DESC LIMIT %s",
                (g.session.username, g.session.db_name, ACTION_NAME, n))
            rows = cur.fetchall()
        finally:
            if conn.is_connected():
                conn.close()
        return json_response({"actiuni": [
            {"moment": r[0].replace(tzinfo=timezone.utc).isoformat(), "text": r[1] or "", "tinta": r[2] or ""}
            for r in rows]}, 200)
    except Exception as ex:     # noqa: BLE001
        logger.exception("[actiuni] read: %s", ex)
        return json_response({"error": "Acțiunile recente nu au putut fi citite."}, 500)
