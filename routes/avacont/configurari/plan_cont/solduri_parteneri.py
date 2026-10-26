# routes/avacont/configurari/plan_cont/solduri_parteneri.py
"""
Opening balances of the PARTNERS (slice AV01-08, table TZ_SoldInitial with IdPartener set; the Access `ParteneriSI`).

The balances of the account itself (IdPartener NULL) are in solduri.py. Here one partner has one row per (year, account); the account is any
account of the chart of ANY subunit of the unit (the partner window lists them all). The window edits the whole list of a partner at once.

    GET  /api/avacont/parteneri/conturi
        -> 200 { "conturi": [ { "idcont", "simbol", "denumire", "capitol", "subunitate" } ] }
        The ACTIVE accounts of every subunit, for the account list of the partner's balances grid.

    GET  /api/avacont/parteneri/cu-solduri?an=<year>
        -> 200 { "parteneri": [ idpartener, ... ] }   the partners that have at least one balance row in that year (the list of the partner window offers them).

    GET  /api/avacont/parteneri/solduri?partener=<id>&an=<year>
        -> 200 { "solduri": [ { "idcont", "simbol", "denumire", "capitol", "sold_debitor", "sold_creditor", "rulaj_debitor", "rulaj_creditor" } ] }
        The partner's rows of that year (inactive accounts too: a stored balance is never hidden).

    POST /api/avacont/parteneri/solduri
        { "partener", "an", "randuri": [ { "idcont", "sold_debitor", "sold_creditor", "rulaj_debitor", "rulaj_creditor" } ] }
        -> 200 { "randuri": n }.  REPLACES the partner's list of that year in one transaction: the rows sent are written, the others deleted.
        400 for an invalid amount / year / a repeated account, 404 for an unknown partner or account.
"""
import logging

import mysql.connector
from flask import g, request

from routes.auth.guard import require_session
from utils.database import get_kbot_connection

from ... import avacont_bp
from ...common import json_utf8, rollback
from .solduri import _CAMPURI, _suma

logger = logging.getLogger(__name__)


@avacont_bp.route("/api/avacont/parteneri/conturi", methods=["GET"])
@require_session
def get_conturi_parteneri():
    """The active accounts of every subunit."""
    db_name = g.session.db_name
    conn = None
    try:
        conn = get_kbot_connection(db_name)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT P.IdCont, P.SimbolCont, P.Denumire, U.Capitol, U.IdSubunitate "
            "  FROM TZ_PlanCont P JOIN TZ_Subunitati U ON U.IdSubunitate = P.IdSubunitate "
            " WHERE P.Activ = 1 ORDER BY U.Capitol, P.SimbolCont")
        lista = [{"idcont": int(r[0]), "simbol": r[1], "denumire": r[2] or "", "capitol": r[3], "subunitate": int(r[4])}
                 for r in cursor.fetchall()]
        logger.info("[avacont.solduri_parteneri] %s: %s conturi", db_name, len(lista))
        return json_utf8({"conturi": lista}, 200)
    except Exception as e:
        logger.error("[avacont.solduri_parteneri] conturi %s: %s", db_name, e, exc_info=True)
        return json_utf8({"error": f"Eroare la citirea conturilor: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()


@avacont_bp.route("/api/avacont/parteneri/cu-solduri", methods=["GET"])
@require_session
def get_parteneri_cu_solduri():
    """The ids of the partners that have a balance row in one year."""
    an = request.args.get("an", type=int)
    if an is None:
        return json_utf8({"error": "Anul este obligatoriu."}, 400)
    db_name = g.session.db_name
    conn = None
    try:
        conn = get_kbot_connection(db_name)
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT IdPartener FROM TZ_SoldInitial WHERE An = %s AND IdPartener IS NOT NULL", (an,))
        lista = [int(r[0]) for r in cursor.fetchall()]
        logger.info("[avacont.solduri_parteneri] %s: %s, %s parteneri cu solduri", db_name, an, len(lista))
        return json_utf8({"parteneri": lista}, 200)
    except Exception as e:
        logger.error("[avacont.solduri_parteneri] cu-solduri %s: %s", db_name, e, exc_info=True)
        return json_utf8({"error": f"Eroare la citirea partenerilor cu solduri: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()


@avacont_bp.route("/api/avacont/parteneri/solduri", methods=["GET"])
@require_session
def get_solduri_partener():
    """The balance rows of one partner for one year."""
    partener = request.args.get("partener", type=int)
    an = request.args.get("an", type=int)
    if partener is None or an is None:
        return json_utf8({"error": "Partenerul și anul sunt obligatorii."}, 400)
    db_name = g.session.db_name
    conn = None
    try:
        conn = get_kbot_connection(db_name)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT S.IdCont, P.SimbolCont, P.Denumire, U.Capitol, S.SoldDebitor, S.SoldCreditor, S.RulajDebitor, S.RulajCreditor "
            "  FROM TZ_SoldInitial S JOIN TZ_PlanCont P ON P.IdCont = S.IdCont "
            "  JOIN TZ_Subunitati U ON U.IdSubunitate = P.IdSubunitate "
            " WHERE S.IdPartener = %s AND S.An = %s ORDER BY U.Capitol, P.SimbolCont", (partener, an))
        lista = [{"idcont": int(r[0]), "simbol": r[1], "denumire": r[2] or "", "capitol": r[3],
                  "sold_debitor": float(r[4]), "sold_creditor": float(r[5]),
                  "rulaj_debitor": float(r[6]), "rulaj_creditor": float(r[7])} for r in cursor.fetchall()]
        logger.info("[avacont.solduri_parteneri] %s: partenerul %s, %s, %s randuri", db_name, partener, an, len(lista))
        return json_utf8({"solduri": lista}, 200)
    except Exception as e:
        logger.error("[avacont.solduri_parteneri] citire %s: %s", db_name, e, exc_info=True)
        return json_utf8({"error": f"Eroare la citirea soldurilor partenerului: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()


@avacont_bp.route("/api/avacont/parteneri/solduri", methods=["POST"])
@require_session
def post_solduri_partener():
    """Replaces the balance rows of ONE partner for one year."""
    corp = request.get_json(silent=True)
    if not isinstance(corp, dict):
        return json_utf8({"error": "Corp JSON lipsă sau nevalid."}, 400)
    try:
        partener = int(corp.get("partener"))
        an = int(corp.get("an"))
    except (TypeError, ValueError):
        return json_utf8({"error": "Partenerul și anul sunt obligatorii."}, 400)
    if an < 2000 or an > 2100:
        return json_utf8({"error": "Anul nu este valid."}, 400)
    randuri = corp.get("randuri")
    if not isinstance(randuri, list):
        return json_utf8({"error": "Lista de solduri lipsește."}, 400)

    de_scris = []
    vazute = set()
    for i, rand in enumerate(randuri, start=1):
        if not isinstance(rand, dict):
            return json_utf8({"error": f"Soldurile, rândul {i}: date nevalide."}, 400)
        try:
            idcont = int(rand.get("idcont"))
        except (TypeError, ValueError):
            return json_utf8({"error": f"Soldurile, rândul {i}: alegeți contul."}, 400)
        if idcont in vazute:
            return json_utf8({"error": f"Soldurile, rândul {i}: contul apare de două ori."}, 400)
        vazute.add(idcont)
        valori = {}
        for cheie, nume in _CAMPURI:
            val, eroare = _suma(rand, cheie, nume)
            if eroare:
                return json_utf8({"error": f"Soldurile, rândul {i}: {eroare}"}, 400)
            valori[cheie] = val
        de_scris.append((idcont, valori))

    db_name = g.session.db_name
    conn = None
    try:
        conn = get_kbot_connection(db_name)
        conn.autocommit = False
        cursor = conn.cursor()
        if not conn.in_transaction:
            conn.start_transaction()
        cursor.execute("SELECT 1 FROM Parteneri WHERE IdPartener = %s", (partener,))
        if not cursor.fetchone():
            conn.rollback()
            return json_utf8({"error": "Partenerul nu mai există."}, 404)
        for idcont, valori in de_scris:
            cursor.execute("SELECT 1 FROM TZ_PlanCont WHERE IdCont = %s", (idcont,))
            if not cursor.fetchone():
                conn.rollback()
                return json_utf8({"error": "Un cont din listă nu mai există."}, 404)
            cursor.execute(
                "INSERT INTO TZ_SoldInitial (An, IdCont, IdPartener, SoldDebitor, SoldCreditor, RulajDebitor, RulajCreditor) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) "
                "ON DUPLICATE KEY UPDATE SoldDebitor = VALUES(SoldDebitor), SoldCreditor = VALUES(SoldCreditor), "
                "RulajDebitor = VALUES(RulajDebitor), RulajCreditor = VALUES(RulajCreditor)",
                (an, idcont, partener, valori["sold_debitor"], valori["sold_creditor"],
                 valori["rulaj_debitor"], valori["rulaj_creditor"]))
        # The rows the window no longer lists are removed (only this partner, only this year).
        if de_scris:
            marcaje = ", ".join(["%s"] * len(de_scris))
            cursor.execute(
                f"DELETE FROM TZ_SoldInitial WHERE IdPartener = %s AND An = %s AND IdCont NOT IN ({marcaje})",
                (partener, an, *[c for c, _ in de_scris]))
        else:
            cursor.execute("DELETE FROM TZ_SoldInitial WHERE IdPartener = %s AND An = %s", (partener, an))
        conn.commit()
        logger.info("[avacont.solduri_parteneri] %s: partenerul %s, %s salvat (%s randuri)", db_name, partener, an, len(de_scris))
        return json_utf8({"randuri": len(de_scris)}, 200)
    except mysql.connector.Error as e:
        rollback(conn)
        logger.error("[avacont.solduri_parteneri] salvare %s: %s", db_name, e, exc_info=True)
        return json_utf8({"error": f"Eroare la salvarea soldurilor partenerului: {e}"}, 500)
    except Exception as e:
        rollback(conn)
        logger.error("[avacont.solduri_parteneri] salvare %s: %s", db_name, e, exc_info=True)
        return json_utf8({"error": f"Eroare la salvarea soldurilor partenerului: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()
