# routes/avacont/configurari/clasificatii_venituri/clasificatii_venituri.py
"""
The revenue classifications of the unit database (slice AV01-11): the «Venituri» page of the «Clasificatii» window.
Tables: Clasificatii_Venituri (the classifications, Capitol / SubCapitol / Paragraf, no unit), Clasificatii_Venituri_Buget (the budget of a
classification for a unit and a year as VERSIONS, the model of Clasificatii_Buget) and Clasificatii_Venituri_Rectificari (the corrections).
DDL: sql/AVACONT/Configurari/ClasificatiiVenituri/AV01_11_clasificatii_venituri_buget.sql.

The BUDGET belongs to a UNIT: the row of `Unitati` named by a sector+source ("ss", the title bar of the main window) and the year ("an").
A request that needs the unit and gets no concrete sector+source (the title bar on «Toate») is refused with a Romanian message.

CHILD SUBUNITS: a child subunit (TZ_Subunitati.IdSubunitateParinte, as the transfer from Access leaves it) has the SAME sector+source as its parent, so it works on
the parent's data: the one unit named by that source and the year. Nothing is blocked; the summary only says so ("copii": {"parinte": name, "copii": [names]}, or null)
and the page shows the notice (utils/subunitati_copil.py). A child never has classifications or a budget of its own: there is no second set to keep.

    GET  /api/avacont/clasificatii-venituri?ss=02E
        -> 200 { "items": [ { "idclsfv", "ss", "capitol", "subcapitol", "paragraf", "denumire" } ],
                 "names": { "capitol": {"01": ...}, "subcapitol": {"0101": ...} } }
        The revenue classifications of the database; the names of the two upper levels come from AVACONT_COMUN.DefaClsfV.
        A classification belongs to a SECTOR+SOURCE (Clasificatii_Venituri.SS, sql/.../AV01_12): "ss" narrows the list to that source; no "ss" or "*"
        («Toate» in the title bar) = every source.

    GET  /api/avacont/clasificatii-venituri/nomenclator?ss=02E
        -> 200 { "items": [ { "clsfv", "capitol", "subcapitol", "paragraf", "denumire", "exista" } ] }
        The nomenclator codes of type VN (DefaClsfV.Tip); "exista" = the classification is already in Clasificatii_Venituri FOR THAT SOURCE.

    POST /api/avacont/clasificatii-venituri/adauga          { "clsfv": "010100", "ss": "02E" }
        -> 200 { "idclsfv": n }. The classification takes its name (capitals) from the nomenclator; the key is the largest one + 1.
        400 for a code that is not a revenue code of the nomenclator or a missing source, 409 for one that already exists on that source.

    DELETE /api/avacont/clasificatii-venituri/<idclsfv>
        -> 200 { "idclsfv": n }. 409 (with the places named) when anything depends on the classification: a used classification is never deleted.
        What counts as «depends» is decided in dependente.py: every table with a foreign key to Clasificatii_Venituri.IdClsfV is found by itself,
        and a table without one (FX_Extrase_H) is subscribed there with one line. 404 when it is gone already.

    GET  /api/avacont/clasificatii-venituri/sumar?an=2026&ss=02A
        -> 200 { "items": [ { "idclsfv", "budget": {"id", "data_inceput", "trim1".."trim4"}|null, "corrections": {"trim1".."trim4"}|null } ],
                 "copii": { "parinte": "...", "copii": ["..."] } | null }
        The LAST budget version of the year and the TOTAL of the year's corrections, for every classification that has one (the nodes above the leaves).

    GET  /api/avacont/clasificatii-venituri/<idclsfv>/buget?an=2026&ss=02A
        -> 200 { "budgets": [ {"id", "data_inceput", "trim1".."trim4"} ], "corrections": [ {"id", "document", "data", "trim1".."trim4"} ] }

    POST /api/avacont/clasificatii-venituri/<idclsfv>/buget
        { "an": 2026, "ss": "02A", "budgets": [ {"id": null|n, "data_inceput": "yyyy-mm-dd", "trim1".."trim4"} ], "deleted_budgets": [ids],
          "corrections": [ {"id": null|n, "document", "data": "yyyy-mm-dd", "trim1".."trim4"} ], "deleted": [ids] }
        -> 200 the same body as the GET, read back after the commit. ONE transaction.

The year of a correction is YEAR(Data) (the table has no year column): a correction must carry a date inside the year being edited.
"""
import logging
from datetime import date

import mysql.connector
from flask import g, request
from mysql.connector import errorcode

from routes.auth.guard import require_session
from utils.database import COMMON_DB, get_kbot_connection
from utils.subunitati_copil import child_sources

from ... import avacont_bp
from ...common import json_utf8, rollback
from .dependente import CLASIFICATII_VENITURI

logger = logging.getLogger(__name__)

# Clasificatii_Venituri_Rectificari.Document is varchar(255); Clasificatii_Venituri.Denumire too.
_MAX_TEXT = 255
_QUARTERS = ("trim1", "trim2", "trim3", "trim4")

_SQL_ITEMS = (
    "SELECT IdClsfV, SS, Capitol, SubCapitol, Paragraf, Denumire FROM Clasificatii_Venituri "
    " ORDER BY Capitol, SubCapitol, Paragraf, SS"
)
_SQL_ITEMS_SS = (
    "SELECT IdClsfV, SS, Capitol, SubCapitol, Paragraf, Denumire FROM Clasificatii_Venituri "
    " WHERE SS = %s ORDER BY Capitol, SubCapitol, Paragraf"
)
_SQL_NAMES = f"SELECT ClsfV, DENUMIREA FROM {COMMON_DB}.DefaClsfV WHERE RIGHT(ClsfV, 2) = '00'"
_SQL_NOMENCLATOR = (
    "SELECT D.ClsfV, D.Capitol, D.Subcapitol, D.Paragraf, D.DENUMIREA, "
    "       EXISTS (SELECT 1 FROM Clasificatii_Venituri C "
    "                WHERE C.SS = %s AND C.Capitol = D.Capitol AND C.SubCapitol = D.Subcapitol AND C.Paragraf = D.Paragraf) AS Exista "
    f"  FROM {COMMON_DB}.DefaClsfV D WHERE D.Tip = 'VN' ORDER BY D.ClsfV"
)
_SQL_NOMENCLATOR_ONE = f"SELECT DENUMIREA FROM {COMMON_DB}.DefaClsfV WHERE ClsfV = %s AND Tip = 'VN'"
_SQL_EXISTS = (
    "SELECT 1 FROM Clasificatii_Venituri WHERE SS = %s AND Capitol = %s AND SubCapitol = %s AND Paragraf = %s LIMIT 1"
)
_SQL_NEXT_ID = "SELECT COALESCE(MAX(IdClsfV), 0) + 1 AS Nou FROM Clasificatii_Venituri FOR UPDATE"
_SQL_INSERT = (
    "INSERT INTO Clasificatii_Venituri (IdClsfV, SS, Capitol, SubCapitol, Paragraf, Denumire) VALUES (%s, %s, %s, %s, %s, %s)"
)
_SQL_ONE = "SELECT IdClsfV FROM Clasificatii_Venituri WHERE IdClsfV = %s"
_SQL_UNIT = "SELECT IdUnitate FROM Unitati WHERE SursaSector = %s AND An = %s AND COALESCE(Ascuns, 0) = 0"

_SQL_BUDGETS = (
    "SELECT IdBuget, DataInceput, Trim1, Trim2, Trim3, Trim4 FROM Clasificatii_Venituri_Buget "
    " WHERE IdClsfV = %s AND IdUnitate = %s AND An = %s ORDER BY DataInceput, IdBuget"
)
_SQL_CORRECTIONS = (
    "SELECT ID, Document, Data, Trim1, Trim2, Trim3, Trim4 FROM Clasificatii_Venituri_Rectificari "
    " WHERE IdClsfV = %s AND IdUnitate = %s AND YEAR(Data) = %s ORDER BY Data, Document, ID"
)
_SQL_BUDGET_UPDATE = (
    "UPDATE Clasificatii_Venituri_Buget SET DataInceput = %s, Trim1 = %s, Trim2 = %s, Trim3 = %s, Trim4 = %s "
    " WHERE IdBuget = %s AND IdClsfV = %s AND IdUnitate = %s AND An = %s"
)
_SQL_BUDGET_INSERT = (
    "INSERT INTO Clasificatii_Venituri_Buget (IdClsfV, IdUnitate, An, DataInceput, Trim1, Trim2, Trim3, Trim4) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
)
_SQL_BUDGET_DELETE = "DELETE FROM Clasificatii_Venituri_Buget WHERE IdBuget = %s AND IdClsfV = %s AND IdUnitate = %s"
_SQL_CORRECTION_UPDATE = (
    "UPDATE Clasificatii_Venituri_Rectificari SET Document = %s, Data = %s, Trim1 = %s, Trim2 = %s, Trim3 = %s, Trim4 = %s "
    " WHERE ID = %s AND IdClsfV = %s AND IdUnitate = %s"
)
_SQL_CORRECTION_INSERT = (
    "INSERT INTO Clasificatii_Venituri_Rectificari (IdClsfV, IdUnitate, Data, Document, Trim1, Trim2, Trim3, Trim4) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
)
_SQL_CORRECTION_DELETE = "DELETE FROM Clasificatii_Venituri_Rectificari WHERE ID = %s AND IdClsfV = %s AND IdUnitate = %s"

_SQL_SUMMARY_BUDGETS = (
    "SELECT B.IdClsfV, B.IdBuget, B.DataInceput, B.Trim1, B.Trim2, B.Trim3, B.Trim4 "
    "  FROM Clasificatii_Venituri_Buget B "
    "  JOIN (SELECT IdClsfV, MAX(DataInceput) AS D FROM Clasificatii_Venituri_Buget "
    "         WHERE IdUnitate = %s AND An = %s GROUP BY IdClsfV) M "
    "    ON M.IdClsfV = B.IdClsfV AND M.D = B.DataInceput "
    " WHERE B.IdUnitate = %s AND B.An = %s"
)
_SQL_SUMMARY_CORRECTIONS = (
    "SELECT IdClsfV, SUM(Trim1) AS Trim1, SUM(Trim2) AS Trim2, SUM(Trim3) AS Trim3, SUM(Trim4) AS Trim4 "
    "  FROM Clasificatii_Venituri_Rectificari WHERE IdUnitate = %s AND Data >= %s AND Data < %s GROUP BY IdClsfV"
)


class _Refused(ValueError):
    """A request the operator can fix; the message is Romanian and goes to the `error` field."""


def _text(value) -> str:
    return "" if value is None else str(value).strip()


def _number(value):
    return None if value is None else float(value)


def _day(value):
    """A DATE (or DATETIME) column as yyyy-mm-dd; None stays None."""
    if value is None:
        return None
    return (value.date() if hasattr(value, "date") else value).isoformat()


def _year(raw) -> int:
    try:
        an = int(raw)
    except (TypeError, ValueError):
        raise _Refused("Anul lipsește sau nu este un număr.")
    if an < 2000 or an > 2100:
        raise _Refused(f"Anul {an} nu este valid.")
    return an


def _amount(row: dict, key: str, where: str):
    raw = row.get(key)
    if raw is None or raw == "":
        return None
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise _Refused(f"{where}: valoarea din «{key.replace('trim', 'Trim. ')}» nu este un număr.")
    return float(raw)


def _ss(raw_ss) -> str:
    ss = _text(raw_ss).upper()
    if not ss:
        raise _Refused("Alegeți o sursă-sector (nu «Toate») din bara de titlu: bugetul de venituri este al unei unități.")
    return ss


def _scope_ss(raw_ss):
    """The source a LIST is narrowed to; None for «Toate» (no value or "*")."""
    ss = _text(raw_ss).upper()
    return None if ss in ("", "*") else ss


def _unit(cursor, raw_ss, an: int) -> int:
    """IdUnitate of the sector+source and year; a Romanian refusal when there is none or the choice is ambiguous."""
    ss = _ss(raw_ss)
    cursor.execute(_SQL_UNIT, (ss, an))
    rows = cursor.fetchall()
    if not rows:
        raise _Refused(f"Nu există o unitate cu sursa-sector {ss} în anul {an}.")
    if len(rows) > 1:
        raise _Refused(f"Sursa-sector {ss} are mai multe unități în anul {an}; nu se poate alege una singură.")
    return int(rows[0]["IdUnitate"])


def _classification(cursor, id_clsfv: int):
    cursor.execute(_SQL_ONE, (id_clsfv,))
    if cursor.fetchone() is None:
        raise _Refused(f"Clasificația {id_clsfv} nu mai există în baza de date.")


# ---------------------------------------------------------------------------
# The classifications
# ---------------------------------------------------------------------------
@avacont_bp.route("/api/avacont/clasificatii-venituri", methods=["GET"])
@require_session
def cv_get_clasificatii():
    """The revenue classifications of the session's database (one source, or every source), with the names of the two upper levels."""
    db_name = g.session.db_name
    conn = None
    try:
        ss = _scope_ss(request.args.get("ss"))
        conn = get_kbot_connection(db_name)
        cursor = conn.cursor(dictionary=True, buffered=True)
        if ss is None:
            cursor.execute(_SQL_ITEMS)
        else:
            cursor.execute(_SQL_ITEMS_SS, (ss,))
        items = [{
            "idclsfv": int(r["IdClsfV"]),
            "ss": _text(r["SS"]),
            "capitol": _text(r["Capitol"]),
            "subcapitol": _text(r["SubCapitol"]),
            "paragraf": _text(r["Paragraf"]),
            "denumire": _text(r["Denumire"]),
        } for r in cursor.fetchall()]
        cursor.execute(_SQL_NAMES)
        capitol, subcapitol = {}, {}
        for r in cursor.fetchall():
            code = _text(r["ClsfV"])
            name = _text(r["DENUMIREA"])
            if len(code) != 6:
                continue
            if code[2:] == "0000":
                capitol[code[:2]] = name
            else:
                subcapitol[code[:4]] = name
        return json_utf8({"items": items, "names": {"capitol": capitol, "subcapitol": subcapitol}}, 200)
    except Exception as e:
        logger.error("[avacont.clasificatii_venituri] arbore %s: %s", db_name, e, exc_info=True)
        return json_utf8({"error": f"Eroare la citirea clasificațiilor de venituri: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()


@avacont_bp.route("/api/avacont/clasificatii-venituri/nomenclator", methods=["GET"])
@require_session
def cv_get_nomenclator():
    """The revenue codes of the nomenclator (the lists of the «add» window)."""
    db_name = g.session.db_name
    conn = None
    try:
        ss = _ss(request.args.get("ss"))
        conn = get_kbot_connection(db_name)
        cursor = conn.cursor(dictionary=True, buffered=True)
        cursor.execute(_SQL_NOMENCLATOR, (ss,))
        items = [{
            "clsfv": _text(r["ClsfV"]),
            "capitol": _text(r["Capitol"]),
            "subcapitol": _text(r["Subcapitol"]),
            "paragraf": _text(r["Paragraf"]),
            "denumire": _text(r["DENUMIREA"]),
            "exista": bool(r["Exista"]),
        } for r in cursor.fetchall()]
        return json_utf8({"items": items}, 200)
    except _Refused as e:
        return json_utf8({"error": str(e)}, 400)
    except Exception as e:
        logger.error("[avacont.clasificatii_venituri] nomenclator %s: %s", db_name, e, exc_info=True)
        return json_utf8({"error": f"Eroare la citirea nomenclatorului de venituri: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()


@avacont_bp.route("/api/avacont/clasificatii-venituri/adauga", methods=["POST"])
@require_session
def cv_post_adauga():
    """Adds one revenue classification from the nomenclator."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return json_utf8({"error": "Corp JSON lipsă sau nevalid."}, 400)

    db_name = g.session.db_name
    conn = None
    try:
        code = _text(body.get("clsfv"))
        if len(code) != 6:
            raise _Refused("Alegeți clasificația de adăugat (capitol, subcapitol, paragraf).")
        ss = _ss(body.get("ss"))
        conn = get_kbot_connection(db_name)
        conn.autocommit = False
        cursor = conn.cursor(dictionary=True, buffered=True)
        cursor.execute(_SQL_NOMENCLATOR_ONE, (code,))
        row = cursor.fetchone()
        if row is None:
            raise _Refused(f"Codul {code} nu este o clasificație de venituri din nomenclator.")
        capitol, subcapitol, paragraf = code[:2], code[2:4], code[4:6]
        cursor.execute(_SQL_EXISTS, (ss, capitol, subcapitol, paragraf))
        if cursor.fetchone() is not None:
            return json_utf8({"error": f"Clasificația {capitol}.{subcapitol}.{paragraf} există deja pe sursa {ss}."}, 409)
        if not conn.in_transaction:
            conn.start_transaction()
        cursor.execute(_SQL_NEXT_ID)
        new_id = int(cursor.fetchone()["Nou"])
        cursor.execute(_SQL_INSERT, (new_id, ss, capitol, subcapitol, paragraf, _text(row["DENUMIREA"]).upper()[:_MAX_TEXT]))
        conn.commit()
        logger.info("[avacont.clasificatii_venituri] %s: adaugata %s pe %s (id %s)", db_name, code, ss, new_id)
        return json_utf8({"idclsfv": new_id}, 200)
    except _Refused as e:
        rollback(conn)
        return json_utf8({"error": str(e)}, 400)
    except mysql.connector.IntegrityError as e:
        rollback(conn)
        if e.errno == 1062:
            return json_utf8({"error": "Clasificația există deja sau altcineva a adăugat una în același timp; încercați din nou."}, 409)
        logger.error("[avacont.clasificatii_venituri] adaugare %s: %s", db_name, e, exc_info=True)
        return json_utf8({"error": f"Eroare la adăugarea clasificației: {e}"}, 500)
    except Exception as e:
        rollback(conn)
        logger.error("[avacont.clasificatii_venituri] adaugare %s: %s", db_name, e, exc_info=True)
        return json_utf8({"error": f"Eroare la adăugarea clasificației: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()


@avacont_bp.route("/api/avacont/clasificatii-venituri/<int:id_clsfv>", methods=["DELETE"])
@require_session
def cv_delete(id_clsfv):
    """Deletes a revenue classification that is used nowhere; one with a budget, corrections or bank-statement headers is REFUSED."""
    db_name = g.session.db_name
    conn = None
    try:
        conn = get_kbot_connection(db_name)
        conn.autocommit = False
        cursor = conn.cursor(dictionary=True, buffered=True)
        if not conn.in_transaction:
            conn.start_transaction()
        cursor.execute("SELECT SS, Capitol, SubCapitol, Paragraf FROM Clasificatii_Venituri WHERE IdClsfV = %s FOR UPDATE", (id_clsfv,))
        row = cursor.fetchone()
        if row is None:
            conn.rollback()
            return json_utf8({"error": "Clasificația nu mai există."}, 404)
        code = f"{_text(row['Capitol'])}.{_text(row['SubCapitol'])}.{_text(row['Paragraf'])}"
        # Every table that depends on a classification (found by foreign key, or subscribed in dependente.py) decides here: a used one stays.
        used = CLASIFICATII_VENITURI.usage(cursor, id_clsfv)
        if used:
            conn.rollback()
            return json_utf8({"error": f"Clasificația {code} ({_text(row['SS'])}) nu se poate șterge: este folosită în "
                                       + ", ".join(used) + "."}, 409)
        cursor.execute("DELETE FROM Clasificatii_Venituri WHERE IdClsfV = %s", (id_clsfv,))
        conn.commit()
        logger.info("[avacont.clasificatii_venituri] %s: stearsa %s pe %s (id %s)", db_name, code, _text(row["SS"]), id_clsfv)
        return json_utf8({"idclsfv": id_clsfv}, 200)
    except mysql.connector.Error as e:
        rollback(conn)
        if e.errno == errorcode.ER_ROW_IS_REFERENCED_2:
            # A place the list above does not know yet still blocks (the foreign keys are RESTRICT).
            return json_utf8({"error": "Clasificația nu se poate șterge: este folosită în buget sau rectificări."}, 409)
        logger.error("[avacont.clasificatii_venituri] stergere %s/%s: %s", db_name, id_clsfv, e, exc_info=True)
        return json_utf8({"error": f"Eroare la ștergerea clasificației: {e}"}, 500)
    except Exception as e:
        rollback(conn)
        logger.error("[avacont.clasificatii_venituri] stergere %s/%s: %s", db_name, id_clsfv, e, exc_info=True)
        return json_utf8({"error": f"Eroare la ștergerea clasificației: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()


# ---------------------------------------------------------------------------
# Budget + corrections
# ---------------------------------------------------------------------------
def _read_budget(cursor, id_clsfv: int, id_unitate: int, an: int) -> dict:
    cursor.execute(_SQL_BUDGETS, (id_clsfv, id_unitate, an))
    budgets = [{
        "id": int(r["IdBuget"]),
        "data_inceput": _day(r["DataInceput"]),
        "trim1": _number(r["Trim1"]), "trim2": _number(r["Trim2"]),
        "trim3": _number(r["Trim3"]), "trim4": _number(r["Trim4"]),
    } for r in cursor.fetchall()]
    cursor.execute(_SQL_CORRECTIONS, (id_clsfv, id_unitate, an))
    corrections = [{
        "id": int(r["ID"]),
        "document": _text(r["Document"]),
        "data": _day(r["Data"]),
        "trim1": _number(r["Trim1"]), "trim2": _number(r["Trim2"]),
        "trim3": _number(r["Trim3"]), "trim4": _number(r["Trim4"]),
    } for r in cursor.fetchall()]
    return {"budgets": budgets, "corrections": corrections}


@avacont_bp.route("/api/avacont/clasificatii-venituri/sumar", methods=["GET"])
@require_session
def cv_get_sumar():
    """The last budget version and the corrections total of every classification, for a unit and a year."""
    db_name = g.session.db_name
    conn = None
    try:
        an = _year(request.args.get("an"))
        conn = get_kbot_connection(db_name)
        cursor = conn.cursor(dictionary=True, buffered=True)
        ss = _ss(request.args.get("ss"))
        id_unitate = _unit(cursor, ss, an)
        cursor.execute(_SQL_SUMMARY_BUDGETS, (id_unitate, an, id_unitate, an))
        budgets = {}
        for r in cursor.fetchall():
            budgets[int(r["IdClsfV"])] = {
                "id": int(r["IdBuget"]),
                "data_inceput": _day(r["DataInceput"]),
                "trim1": _number(r["Trim1"]), "trim2": _number(r["Trim2"]),
                "trim3": _number(r["Trim3"]), "trim4": _number(r["Trim4"]),
            }
        cursor.execute(_SQL_SUMMARY_CORRECTIONS, (id_unitate, date(an, 1, 1), date(an + 1, 1, 1)))
        corrections = {}
        for r in cursor.fetchall():
            corrections[int(r["IdClsfV"])] = {
                "trim1": _number(r["Trim1"]), "trim2": _number(r["Trim2"]),
                "trim3": _number(r["Trim3"]), "trim4": _number(r["Trim4"]),
            }
        items = [{"idclsfv": k, "budget": budgets.get(k), "corrections": corrections.get(k)}
                 for k in sorted(set(budgets) | set(corrections))]
        return json_utf8({"items": items, "copii": child_sources(cursor).get(ss)}, 200)
    except _Refused as e:
        return json_utf8({"error": str(e)}, 400)
    except Exception as e:
        logger.error("[avacont.clasificatii_venituri] sumar %s: %s", db_name, e, exc_info=True)
        return json_utf8({"error": f"Eroare la citirea sumarului de buget: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()


@avacont_bp.route("/api/avacont/clasificatii-venituri/<int:id_clsfv>/buget", methods=["GET"])
@require_session
def cv_get_buget(id_clsfv):
    """The budget of one classification for a unit and a year, and that year's corrections."""
    db_name = g.session.db_name
    conn = None
    try:
        an = _year(request.args.get("an"))
        conn = get_kbot_connection(db_name)
        cursor = conn.cursor(dictionary=True, buffered=True)
        id_unitate = _unit(cursor, request.args.get("ss"), an)
        _classification(cursor, id_clsfv)
        return json_utf8(_read_budget(cursor, id_clsfv, id_unitate, an), 200)
    except _Refused as e:
        return json_utf8({"error": str(e)}, 400)
    except Exception as e:
        logger.error("[avacont.clasificatii_venituri] buget %s/%s: %s", db_name, id_clsfv, e, exc_info=True)
        return json_utf8({"error": f"Eroare la citirea bugetului: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()


def _id_list(body: dict, key: str, label: str) -> list:
    raw = body.get(key) or []
    if not isinstance(raw, list):
        raise _Refused(f"{label} nu are forma așteptată.")
    try:
        return [int(v) for v in raw]
    except (TypeError, ValueError):
        raise _Refused(f"{label} nu are forma așteptată.")


def _save_budgets(cursor, id_clsfv: int, id_unitate: int, an: int, body: dict):
    """Removals first (so a date can be freed and used again in the same save), then every version updated or added."""
    for row_id in _id_list(body, "deleted_budgets", "Lista versiunilor de buget șterse"):
        cursor.execute(_SQL_BUDGET_DELETE, (row_id, id_clsfv, id_unitate))
    budgets = body.get("budgets")
    if not isinstance(budgets, list):
        raise _Refused("Lipsesc versiunile de buget («budgets»).")
    seen = set()
    for index, row in enumerate(budgets, start=1):
        if not isinstance(row, dict):
            raise _Refused(f"Bugetul {index} nu are forma așteptată.")
        where = f"Bugetul {index}"
        try:
            start = date.fromisoformat(_text(row.get("data_inceput")))
        except ValueError:
            raise _Refused(f"{where}: lipsește data de început sau nu este o dată.")
        if start.year != an:
            raise _Refused(f"{where}: data de început {start:%d.%m.%Y} nu este în anul {an}.")
        if start in seen:
            raise _Refused(f"{where}: există deja un buget care începe la {start:%d.%m.%Y}.")
        seen.add(start)
        amounts = [_amount(row, q, where) for q in _QUARTERS]
        if row.get("id"):
            cursor.execute(_SQL_BUDGET_UPDATE, (start, *amounts, int(row["id"]), id_clsfv, id_unitate, an))
        else:
            cursor.execute(_SQL_BUDGET_INSERT, (id_clsfv, id_unitate, an, start, *amounts))


def _save_corrections(cursor, id_clsfv: int, id_unitate: int, an: int, body: dict):
    for row_id in _id_list(body, "deleted", "Lista rectificărilor șterse"):
        cursor.execute(_SQL_CORRECTION_DELETE, (row_id, id_clsfv, id_unitate))
    corrections = body.get("corrections") or []
    if not isinstance(corrections, list):
        raise _Refused("Lista rectificărilor nu are forma așteptată.")
    for index, row in enumerate(corrections, start=1):
        if not isinstance(row, dict):
            raise _Refused(f"Rectificarea {index} nu are forma așteptată.")
        where = f"Rectificarea {index}"
        document = _text(row.get("document"))
        if not document:
            raise _Refused(f"{where}: lipsește numărul documentului.")
        if len(document) > _MAX_TEXT:
            raise _Refused(f"{where}: numărul documentului are peste {_MAX_TEXT} caractere.")
        try:
            day = date.fromisoformat(_text(row.get("data")))
        except ValueError:
            raise _Refused(f"{where} ({document}): lipsește data sau nu este o dată.")
        if day.year != an:
            raise _Refused(f"{where} ({document}): data {day:%d.%m.%Y} nu este în anul {an}.")
        amounts = [_amount(row, q, where) for q in _QUARTERS]
        if row.get("id"):
            cursor.execute(_SQL_CORRECTION_UPDATE, (document, day, *amounts, int(row["id"]), id_clsfv, id_unitate))
        else:
            cursor.execute(_SQL_CORRECTION_INSERT, (id_clsfv, id_unitate, day, document, *amounts))


@avacont_bp.route("/api/avacont/clasificatii-venituri/<int:id_clsfv>/buget", methods=["POST"])
@require_session
def cv_post_buget(id_clsfv):
    """Saves the budget versions and the corrections of one classification for a unit and a year, all or nothing."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return json_utf8({"error": "Corp JSON lipsă sau nevalid."}, 400)

    db_name = g.session.db_name
    conn = None
    try:
        an = _year(body.get("an"))
        conn = get_kbot_connection(db_name)
        conn.autocommit = False
        cursor = conn.cursor(dictionary=True, buffered=True)
        id_unitate = _unit(cursor, body.get("ss"), an)
        _classification(cursor, id_clsfv)
        if not conn.in_transaction:
            conn.start_transaction()
        _save_budgets(cursor, id_clsfv, id_unitate, an, body)
        _save_corrections(cursor, id_clsfv, id_unitate, an, body)
        conn.commit()
        logger.info("[avacont.clasificatii_venituri] %s: buget %s/%s/%s salvat", db_name, id_clsfv, id_unitate, an)
        return json_utf8(_read_budget(cursor, id_clsfv, id_unitate, an), 200)
    except _Refused as e:
        rollback(conn)
        return json_utf8({"error": str(e)}, 400)
    except mysql.connector.IntegrityError as e:
        rollback(conn)
        if e.errno == 1062:
            if "UK_CV_Buget" in str(e):
                return json_utf8({"error": "Există deja un buget cu aceeași dată de început pe această clasificație."}, 409)
            return json_utf8({"error": "Există deja o rectificare cu același document și aceeași dată pe această clasificație."}, 409)
        logger.error("[avacont.clasificatii_venituri] salvare %s/%s: %s", db_name, id_clsfv, e, exc_info=True)
        return json_utf8({"error": f"Eroare la salvarea bugetului: {e}"}, 500)
    except Exception as e:
        rollback(conn)
        logger.error("[avacont.clasificatii_venituri] salvare %s/%s: %s", db_name, id_clsfv, e, exc_info=True)
        return json_utf8({"error": f"Eroare la salvarea bugetului: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()
