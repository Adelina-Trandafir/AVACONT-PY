# routes/avacont/subunitati.py
"""
Subunits of a unit (slice AV01-04, table TZ_Subunitati, sql/AVACONT/Configurari/PlanCont/AV01_04_conturi_sursa.sql).

A subunit is another subordinate unit with its own accounting. It decides the budget chapter and
the sector+source of its accounts (65.01 = 01A, 65.02 = 02A, 65.10 = 02E, 65.11 = 01A), and holds
the salary decimals (the old Access flag Zec2). It is NOT linked to VC_Subunitati (VERCON).

    GET    /api/avacont/subunitati
        -> 200 { "subunitati": [ { "idsubunitate", "denumire", "capitol", "sursasector",
                                   "zecimale", "implicita", "activ", "conturi" } ] }
        "conturi" = how many accounts the subunit's chart holds.

    POST   /api/avacont/subunitati
        { "idsubunitate": null | n, "denumire", "capitol", "sursasector", "zecimale": 0 | 2,
          "implicita": bool, "activ": bool }
        -> 200 { "idsubunitate": n }.  400 for an invalid field, 404 for an unknown id, 409 for a
        denomination another subunit has, or a chapter another subunit has that is not of its family (a chapter repeats only between a
        subunit and its children; rule and switch in capitol_familie.py, script AV01_04e). The sector+source here is the DEFAULT of a new
        account (each account carries its own, see conturi.py), so it can change at any time.
        The FIRST subunit of a unit is always the default one. Marking another as default moves the
        mark; the default one can never be unmarked directly (mark another instead).

    DELETE /api/avacont/subunitati/<id>
        -> 200 { "idsubunitate": n }.  409 for the default subunit, or one that holds accounts
        (or is used elsewhere: the foreign keys are RESTRICT, never cascade).

No row is created by a read: a unit with no subunit shows an empty list and the window asks for the first one.
"""
import logging
import re

import mysql.connector
from flask import g, request
from mysql.connector import errorcode

from routes.auth.guard import require_session
from utils.database import get_kbot_connection

from ... import avacont_bp
from ...common import flag, json_utf8, rollback, text
from .capitol_familie import conflict_capitol

logger = logging.getLogger(__name__)

_RE_CAPITOL = re.compile(r"^\d{2}\.\d{2}$")
_DENUMIRE_MAX = 100

_SQL_LISTA = (
    "SELECT S.IdSubunitate, S.Denumire, S.Capitol, S.SursaSector, S.ZecimaleSalarii, S.Implicita, S.Activ, "
    "       (SELECT COUNT(*) FROM TZ_PlanCont P WHERE P.IdSubunitate = S.IdSubunitate) "
    "  FROM TZ_Subunitati S ORDER BY S.Capitol, S.IdSubunitate"
)


def _valideaza(corp: dict):
    """(values, error): the cleaned body of a save, or the Romanian reason it is refused."""
    denumire = text(corp, "denumire")
    if not denumire:
        return None, "Introduceți denumirea subunității."
    if len(denumire) > _DENUMIRE_MAX:
        return None, f"Denumirea subunității are cel mult {_DENUMIRE_MAX} de caractere."
    capitol = text(corp, "capitol")
    if not _RE_CAPITOL.match(capitol):
        return None, "Capitolul trebuie să aibă forma 65.01 (două cifre, punct, două cifre)."
    sursasector = text(corp, "sursasector").upper()
    if not sursasector:
        return None, "Alegeți sectorul și sursa."
    try:
        zecimale = int(corp.get("zecimale") if corp.get("zecimale") is not None else 0)
    except (TypeError, ValueError):
        return None, "Numărul de zecimale nu este valid."
    if zecimale not in (0, 2):
        return None, "Zecimalele salariilor pot fi 0 sau 2."
    idsub = corp.get("idsubunitate")
    if idsub is not None:
        try:
            idsub = int(idsub)
        except (TypeError, ValueError):
            return None, "Numărul subunității nu este valid."
    return {"idsubunitate": idsub, "denumire": denumire, "capitol": capitol, "sursasector": sursasector,
            "zecimale": zecimale, "implicita": flag(corp, "implicita"), "activ": flag(corp, "activ", True)}, None


@avacont_bp.route("/api/avacont/subunitati", methods=["GET"])
@require_session
def get_subunitati():
    """Every subunit of the unit, with the size of its chart of accounts."""
    db_name = g.session.db_name
    conn = None
    try:
        conn = get_kbot_connection(db_name)
        cursor = conn.cursor()
        cursor.execute(_SQL_LISTA)
        lista = [{"idsubunitate": int(r[0]), "denumire": r[1] or "", "capitol": r[2] or "",
                  "sursasector": r[3] or "", "zecimale": int(r[4] or 0), "implicita": bool(r[5]),
                  "activ": bool(r[6]), "conturi": int(r[7] or 0)} for r in cursor.fetchall()]
        logger.info("[avacont.subunitati] %s: %s subunitati", db_name, len(lista))
        return json_utf8({"subunitati": lista}, 200)
    except Exception as e:
        logger.error("[avacont.subunitati] citire %s: %s", db_name, e, exc_info=True)
        return json_utf8({"error": f"Eroare la citirea subunităților: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()


@avacont_bp.route("/api/avacont/subunitati", methods=["POST"])
@require_session
def post_subunitate():
    """Saves one subunit (new or existing)."""
    corp = request.get_json(silent=True)
    if not isinstance(corp, dict):
        return json_utf8({"error": "Corp JSON lipsă sau nevalid."}, 400)
    v, eroare = _valideaza(corp)
    if eroare:
        return json_utf8({"error": eroare}, 400)

    db_name = g.session.db_name
    conn = None
    try:
        conn = get_kbot_connection(db_name)
        conn.autocommit = False
        cursor = conn.cursor()
        if not conn.in_transaction:
            conn.start_transaction()

        cursor.execute("SELECT 1 FROM AVACONT_COMUN.DefaSursaSector WHERE SursaSector = %s LIMIT 1",
                       (v["sursasector"],))
        if not cursor.fetchone():
            conn.rollback()
            return json_utf8({"error": f"Sectorul și sursa «{v['sursasector']}» nu există."}, 400)

        cursor.execute("SELECT COUNT(*) FROM TZ_Subunitati FOR UPDATE")
        total = int(cursor.fetchone()[0])
        idsub = v["idsubunitate"]
        implicita = v["implicita"]

        # The chapter repeats only inside one family (a subunit and its children); see capitol_familie.py (it has the switch).
        conflict = conflict_capitol(cursor, idsub, v["capitol"])
        if conflict:
            conn.rollback()
            return json_utf8({"error": conflict}, 409)

        if idsub is None:
            if total == 0:
                implicita = True            # the first subunit of a unit is the default one
            cursor.execute(
                "INSERT INTO TZ_Subunitati (Denumire, Capitol, SursaSector, ZecimaleSalarii, Implicita, Activ) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (v["denumire"], v["capitol"], v["sursasector"], v["zecimale"], 1 if implicita else 0,
                 1 if v["activ"] else 0))
            idsub = int(cursor.lastrowid)
        else:
            cursor.execute("SELECT Implicita FROM TZ_Subunitati WHERE IdSubunitate = %s FOR UPDATE", (idsub,))
            vechi = cursor.fetchone()
            if not vechi:
                conn.rollback()
                return json_utf8({"error": "Subunitatea nu mai există."}, 404)
            # The sector+source of the subunit is only the default of a NEW account (each account carries its own),
            # so it can change even when the chart is not empty.
            if bool(vechi[0]) and not implicita:
                conn.rollback()
                return json_utf8({"error": "Subunitatea implicită nu poate fi debifată direct: "
                                           "marcați altă subunitate ca implicită."}, 400)
            cursor.execute(
                "UPDATE TZ_Subunitati SET Denumire = %s, Capitol = %s, SursaSector = %s, ZecimaleSalarii = %s, "
                "Implicita = %s, Activ = %s WHERE IdSubunitate = %s",
                (v["denumire"], v["capitol"], v["sursasector"], v["zecimale"], 1 if implicita else 0,
                 1 if v["activ"] else 0, idsub))

        if implicita:
            cursor.execute("UPDATE TZ_Subunitati SET Implicita = 0 WHERE IdSubunitate <> %s AND Implicita = 1", (idsub,))
        conn.commit()
        logger.info("[avacont.subunitati] %s: subunitatea %s «%s»", db_name, idsub, v["denumire"])
        return json_utf8({"idsubunitate": idsub}, 200)
    except mysql.connector.Error as e:
        rollback(conn)
        if e.errno == errorcode.ER_DUP_ENTRY:
            if "Capitol" in str(e.msg):
                # A unit database that still has the old UNIQUE index on the chapter: AV01_04e was not run / synchronised there.
                return json_utf8({"error": "Baza unității are încă indexul unic pe capitol, deci același capitol nu poate "
                                           "fi folosit de două subunități. Cereți actualizarea schemei bazei."}, 409)
            return json_utf8({"error": "Există deja o subunitate cu această denumire."}, 409)
        logger.error("[avacont.subunitati] salvare %s: %s", db_name, e, exc_info=True)
        return json_utf8({"error": f"Eroare la salvarea subunității: {e}"}, 500)
    except Exception as e:
        rollback(conn)
        logger.error("[avacont.subunitati] salvare %s: %s", db_name, e, exc_info=True)
        return json_utf8({"error": f"Eroare la salvarea subunității: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()


@avacont_bp.route("/api/avacont/subunitati/<int:idsub>", methods=["DELETE"])
@require_session
def delete_subunitate(idsub: int):
    """Deletes a subunit that holds nothing and is not the default one."""
    db_name = g.session.db_name
    conn = None
    try:
        conn = get_kbot_connection(db_name)
        conn.autocommit = False
        cursor = conn.cursor()
        if not conn.in_transaction:
            conn.start_transaction()
        cursor.execute("SELECT Implicita FROM TZ_Subunitati WHERE IdSubunitate = %s FOR UPDATE", (idsub,))
        rand = cursor.fetchone()
        if not rand:
            conn.rollback()
            return json_utf8({"error": "Subunitatea nu mai există."}, 404)
        if bool(rand[0]):
            conn.rollback()
            return json_utf8({"error": "Subunitatea implicită nu se poate șterge: marcați întâi altă subunitate "
                                       "ca implicită."}, 409)
        cursor.execute("SELECT COUNT(*) FROM TZ_PlanCont WHERE IdSubunitate = %s", (idsub,))
        nr = int(cursor.fetchone()[0])
        if nr > 0:
            conn.rollback()
            return json_utf8({"error": f"Subunitatea nu se poate șterge: planul ei de conturi are {nr} conturi."}, 409)
        cursor.execute("DELETE FROM TZ_Subunitati WHERE IdSubunitate = %s", (idsub,))
        conn.commit()
        logger.info("[avacont.subunitati] %s: subunitatea %s stearsa", db_name, idsub)
        return json_utf8({"idsubunitate": idsub}, 200)
    except mysql.connector.Error as e:
        rollback(conn)
        if e.errno == errorcode.ER_ROW_IS_REFERENCED_2:
            return json_utf8({"error": "Subunitatea nu se poate șterge: este folosită în alte date."}, 409)
        logger.error("[avacont.subunitati] stergere %s: %s", db_name, e, exc_info=True)
        return json_utf8({"error": f"Eroare la ștergerea subunității: {e}"}, 500)
    except Exception as e:
        rollback(conn)
        logger.error("[avacont.subunitati] stergere %s: %s", db_name, e, exc_info=True)
        return json_utf8({"error": f"Eroare la ștergerea subunității: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()
