# routes/avacont/configurari/plan_cont/solduri.py
"""
Opening balances and cumulated turnovers of the accounts of a subunit (slice AV01-04, table TZ_SoldInitial).

One row per (year, account); the row of the account itself has IdPartener NULL (partner-level balances come with the partners).
The window edits the four amounts of one account at a time, in the account's panel or in the balances grid.

    GET  /api/avacont/conturi/solduri?subunitate=<id>&an=<year>
        -> 200 { "solduri": [ { "idcont", "sold_debitor", "sold_creditor", "rulaj_debitor", "rulaj_creditor" } ] }
        Only the accounts of that subunit that HAVE a row for that year; an account without one is zero for the client.

    POST /api/avacont/conturi/solduri
        { "idcont", "an", "sold_debitor", "sold_creditor", "rulaj_debitor", "rulaj_creditor" }
        -> 200 { "idcont": n }.  Writes (or replaces) the four amounts. 400 for a negative / invalid amount or year, 404 for an
        unknown account. Amounts are decimals with two places.
"""
import logging
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

import mysql.connector
from flask import g, request

from routes.auth.guard import require_session
from utils.database import get_kbot_connection

from ... import avacont_bp
from ...common import json_utf8, rollback

logger = logging.getLogger(__name__)

_MAX = Decimal("9999999999999.99")
_CAMPURI = (("sold_debitor", "Soldul inițial debitor"), ("sold_creditor", "Soldul inițial creditor"),
            ("rulaj_debitor", "Rulajul cumulat debitor"), ("rulaj_creditor", "Rulajul cumulat creditor"))


def _suma(corp: dict, cheie: str, nume: str):
    """(Decimal, error): one amount of the body, two places, never negative."""
    brut = corp.get(cheie)
    if brut is None or str(brut).strip() == "":
        return Decimal("0.00"), None
    try:
        val = Decimal(str(brut).strip().replace(",", ".")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return None, f"{nume} nu este un număr."
    if val < 0:
        return None, f"{nume} nu poate fi negativ."
    if val > _MAX:
        return None, f"{nume} este prea mare."
    return val, None


@avacont_bp.route("/api/avacont/conturi/solduri", methods=["GET"])
@require_session
def get_solduri():
    """The balance rows of one subunit's accounts for one year."""
    sub = request.args.get("subunitate", type=int)
    an = request.args.get("an", type=int)
    if sub is None or an is None:
        return json_utf8({"error": "Subunitatea și anul sunt obligatorii."}, 400)
    db_name = g.session.db_name
    conn = None
    try:
        conn = get_kbot_connection(db_name)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT S.IdCont, S.SoldDebitor, S.SoldCreditor, S.RulajDebitor, S.RulajCreditor "
            "  FROM TZ_SoldInitial S JOIN TZ_PlanCont P ON P.IdCont = S.IdCont "
            " WHERE P.IdSubunitate = %s AND S.An = %s AND S.IdPartener IS NULL", (sub, an))
        lista = [{"idcont": int(r[0]), "sold_debitor": float(r[1]), "sold_creditor": float(r[2]),
                  "rulaj_debitor": float(r[3]), "rulaj_creditor": float(r[4])} for r in cursor.fetchall()]
        logger.info("[avacont.solduri] %s: subunitatea %s, %s, %s randuri", db_name, sub, an, len(lista))
        return json_utf8({"solduri": lista}, 200)
    except Exception as e:
        logger.error("[avacont.solduri] citire %s: %s", db_name, e, exc_info=True)
        return json_utf8({"error": f"Eroare la citirea soldurilor: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()


@avacont_bp.route("/api/avacont/conturi/solduri", methods=["POST"])
@require_session
def post_sold():
    """Writes the opening balance and turnovers of ONE account for one year."""
    corp = request.get_json(silent=True)
    if not isinstance(corp, dict):
        return json_utf8({"error": "Corp JSON lipsă sau nevalid."}, 400)
    try:
        idcont = int(corp.get("idcont"))
        an = int(corp.get("an"))
    except (TypeError, ValueError):
        return json_utf8({"error": "Contul și anul sunt obligatorii."}, 400)
    if an < 2000 or an > 2100:
        return json_utf8({"error": "Anul nu este valid."}, 400)
    valori = {}
    for cheie, nume in _CAMPURI:
        val, eroare = _suma(corp, cheie, nume)
        if eroare:
            return json_utf8({"error": eroare}, 400)
        valori[cheie] = val

    db_name = g.session.db_name
    conn = None
    try:
        conn = get_kbot_connection(db_name)
        conn.autocommit = False
        cursor = conn.cursor()
        if not conn.in_transaction:
            conn.start_transaction()
        cursor.execute("SELECT 1 FROM TZ_PlanCont WHERE IdCont = %s", (idcont,))
        if not cursor.fetchone():
            conn.rollback()
            return json_utf8({"error": "Contul nu mai există."}, 404)
        cursor.execute(
            "INSERT INTO TZ_SoldInitial (An, IdCont, IdPartener, SoldDebitor, SoldCreditor, RulajDebitor, RulajCreditor) "
            "VALUES (%s, %s, NULL, %s, %s, %s, %s) "
            "ON DUPLICATE KEY UPDATE SoldDebitor = VALUES(SoldDebitor), SoldCreditor = VALUES(SoldCreditor), "
            "RulajDebitor = VALUES(RulajDebitor), RulajCreditor = VALUES(RulajCreditor)",
            (an, idcont, valori["sold_debitor"], valori["sold_creditor"], valori["rulaj_debitor"], valori["rulaj_creditor"]))
        conn.commit()
        logger.info("[avacont.solduri] %s: contul %s, %s salvat", db_name, idcont, an)
        return json_utf8({"idcont": idcont}, 200)
    except mysql.connector.Error as e:
        rollback(conn)
        logger.error("[avacont.solduri] salvare %s: %s", db_name, e, exc_info=True)
        return json_utf8({"error": f"Eroare la salvarea soldurilor: {e}"}, 500)
    except Exception as e:
        rollback(conn)
        logger.error("[avacont.solduri] salvare %s: %s", db_name, e, exc_info=True)
        return json_utf8({"error": f"Eroare la salvarea soldurilor: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()
