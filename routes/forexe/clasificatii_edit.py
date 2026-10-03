# routes/forexe/clasificatii_edit.py
"""
The «Clasificatii bugetare» window of K-BOT (slice 0087-01): the classification tree of the
session's database, the yearly budget of one classification and its corrections.

    GET  /api/forexe/nomenclatoare/clasificatii?an=2026
        -> 200 { "items": [ { "id_clsf", "id_unitate", "id_clsf_acc", "capitol", "subcapitol", "articol", "alineat",
                              "denumire", "ss", "clsf" }, ... ],
                 "names": { "capitol": {"65": ...}, "subcapitol": {"650402": ...},
                            "articol": {"10.01": ...}, "ss": {"02A": ...} } }

    GET  /api/forexe/nomenclatoare/clasificatii/<id_clsf>/buget?an=2026
        -> 200 { "budgets": [ {"id", "data_inceput", "trim1".."trim4"}, ... ],
                 "corrections": [ {"id", "document", "data", "trim1".."trim4"}, ... ] }

    GET  /api/forexe/nomenclatoare/clasificatii/sumar-buget?an=2026
        -> 200 { "items": [ { "id_clsf", "activ": true when some quarter of some budget version or
                              correction of the year is not zero, "budget": {"id", "data_inceput", "trim1".."trim4"}|null,
                              "corrections": {"trim1".."trim4"}|null } ] }
           (the last budget version and the corrections total, for the non-leaf nodes of the tree)

    POST /api/forexe/nomenclatoare/clasificatii/<id_clsf>/buget
        { "an": 2026,
          "budgets": [ {"id": null|n, "data_inceput": "yyyy-mm-dd", "trim1".."trim4"} ],
          "deleted_budgets": [ids],
          "corrections": [ {"id": null|n, "document", "data": "yyyy-mm-dd", "trim1".."trim4"} ],
          "deleted": [ids] }
        -> 200 the same body as the GET, read back after the commit

    GET  /api/forexe/nomenclatoare/clasificatii/nomenclator
        -> 200 { "ss": [ {"code", "name"} ],            (the sector-sources of THIS database)
                 "f": { "codes": [ {"code", "name"} ], "groups": {} },
                 "e": { "codes": [ {"code", "name"} ], "groups": {"20": ..., "2001": ...} } }

    GET  /api/forexe/nomenclatoare/clasificatii/verificare-buget?data=2026-10-02[&angajament=AAB2...]   (slice 0103-04)
        -> 200 { "data", "trimestru", "items": [ { "id_clsf", "id_unitate", "clsf", "denumire", "ss",
                 "buget_kbot": number|null, "credit_fx": number|null, "diferenta", "egal" } ] }

    POST /api/forexe/nomenclatoare/clasificatii/adauga
        { "an": 2026, "ss": ["02A"], "f": ["650402"], "e": ["200101"] }
        -> 200 { "requested", "inserted", "existing" }

WHERE THE DATA LIVES (MariaDB_Schema/AVACONT_SURSA.sql, AVACONT_COMUN.sql)
  * Clasificatii: Capitol / Subcapitol / Articol / Alineat / Denumire, per IdUnitate. The tree is
    Capitol > Subcapitol > Articol > Alineat (operator, 26.09.2026).
  * Level names are not in Clasificatii. Capitol = DefaClsfF(left(Capitol,2) + "0000"),
    Subcapitol = DefaClsfF(left(Capitol,2) + Subcapitol without the dot), Articol =
    DefaArticol(Articol); the sector-source name comes from DefaSursaSector.
  * Clasificatii_Buget (slice 0102): one row = one VERSION of the budget of a classification for a
    year, starting on DataInceput; unique key (IdClsf, An, DataInceput). The DDF reads the version
    in force on the revision's day (routes/forexe/budget_on_day.py). TOTAL is a generated column,
    never written and never shown: there is no yearly total of a budget.
  * Clasificatii_Rectificari has NO year column: the year of a correction is YEAR(Data), so a
    correction must carry a date inside the year being edited. Unique (IdClsf, Data, Document).

ADDING CLASSIFICATIONS reuses the registration rules word for word: the three lists are the ones
the public registration page offers (routes/inregistrare/nomenclatoare.py) and the rows are built
and checked by routes/inregistrare/randuri.py. Only the sector-sources that already have a unit in
this database can be chosen -- a new sector-source is a new unit, which is the registration's job.
Rows already present (same unit, capitol, subcapitol, articol, alineat) are skipped, never doubled.
"""
import json
import logging
from datetime import date

import mysql.connector
from flask import g, current_app, request

from routes.auth.guard import require_session
from routes.inregistrare import nomenclatoare as registration_lists
from routes.inregistrare import randuri
from utils.database import COMMON_DB, get_kbot_connection

from . import forexe_bp
from .budget_on_day import as_day, budget_on_day, quarter_of

logger = logging.getLogger(__name__)

# Clasificatii_Rectificari.Document is varchar(255).
_MAX_DOCUMENT = 255

_SQL_ITEMS = (
    "SELECT C.IDClsf, C.IdUnitate, C.IdClsfAcc, C.Capitol, C.Subcapitol, C.Articol, C.Alineat, C.Denumire, C.SS, C.Clsf "
    "  FROM Clasificatii C "
    "  LEFT JOIN Unitati U ON U.IdUnitate = C.IdUnitate "
    " WHERE COALESCE(U.Ascuns, 0) = 0 "
    " ORDER BY C.Capitol, C.Subcapitol, C.Articol, C.Alineat"
)
_SQL_NAMES_F = (
    f"SELECT ClsfF, MAX(Denumire) AS Denumire FROM {COMMON_DB}.DefaClsfF "
    " WHERE ClsfF IS NOT NULL AND ClsfF <> '' GROUP BY ClsfF"
)
_SQL_NAMES_ARTICOL = (
    f"SELECT Articol, MAX(Denumire) AS Denumire FROM {COMMON_DB}.DefaArticol GROUP BY Articol"
)
_SQL_NAMES_SS = f"SELECT SursaSector, Denumire FROM {COMMON_DB}.DefaSursaSector"

_SQL_ONE = (
    "SELECT IDClsf, IdUnitate, Capitol, Subcapitol, Articol, Alineat "
    "  FROM Clasificatii WHERE IDClsf = %s"
)
_SQL_BUDGETS = (
    "SELECT IdBuget, DataInceput, Trim1, Trim2, Trim3, Trim4 FROM Clasificatii_Buget "
    " WHERE IdClsf = %s AND An = %s ORDER BY DataInceput, IdBuget"
)
_SQL_CORRECTIONS = (
    "SELECT ID, Document, Data, Trim1, Trim2, Trim3, Trim4 "
    "  FROM Clasificatii_Rectificari "
    " WHERE IdClsf = %s AND YEAR(Data) = %s "
    " ORDER BY Data, Document, ID"
)
_SQL_BUDGET_UPDATE = (
    "UPDATE Clasificatii_Buget "
    "   SET DataInceput = %s, Trim1 = %s, Trim2 = %s, Trim3 = %s, Trim4 = %s "
    " WHERE IdBuget = %s AND IdClsf = %s AND An = %s"
)
_SQL_BUDGET_INSERT = (
    "INSERT INTO Clasificatii_Buget (IdClsf, IdUnitate, An, DataInceput, Trim1, Trim2, Trim3, Trim4) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
)
_SQL_BUDGET_DELETE = "DELETE FROM Clasificatii_Buget WHERE IdBuget = %s AND IdClsf = %s"
_SQL_CORRECTION_UPDATE = (
    "UPDATE Clasificatii_Rectificari "
    "   SET Document = %s, Data = %s, Trim1 = %s, Trim2 = %s, Trim3 = %s, Trim4 = %s "
    " WHERE ID = %s AND IdClsf = %s"
)
_SQL_CORRECTION_INSERT = (
    "INSERT INTO Clasificatii_Rectificari "
    "(IdClsf, Capitol, Subcapitol, Articol, Alineat, Data, Document, Trim1, Trim2, Trim3, Trim4) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
)
_SQL_CORRECTION_DELETE = "DELETE FROM Clasificatii_Rectificari WHERE ID = %s AND IdClsf = %s"

_SQL_UNITS = "SELECT IdUnitate, SursaSector, An FROM Unitati WHERE COALESCE(Ascuns, 0) = 0"
_SQL_EXISTS = (
    "SELECT 1 FROM Clasificatii "
    " WHERE IdUnitate = %s AND Capitol = %s AND Subcapitol = %s AND Articol = %s AND Alineat = %s "
    " LIMIT 1"
)

_QUARTERS = ("trim1", "trim2", "trim3", "trim4")


class _Refused(ValueError):
    """A request the operator can fix; the message is Romanian and goes to the `error` field."""


def _json_utf8(payload, status):
    """A JSON response with LITERAL diacritics (ensure_ascii=False)."""
    body = json.dumps(payload, ensure_ascii=False, default=str)
    return current_app.response_class(body, status=status, mimetype="application/json")


def _text(value) -> str:
    return "" if value is None else str(value).strip()


def _number(value):
    return None if value is None else float(value)


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


# ---------------------------------------------------------------------------
# Tree
# ---------------------------------------------------------------------------
def read_names(cursor) -> dict:
    """The names of the three upper tree levels and of the sector-sources."""
    cursor.execute(_SQL_NAMES_F)
    f_names = {_text(r["ClsfF"]): _text(r["Denumire"]) for r in cursor.fetchall()}
    capitol = {code[:2]: name for code, name in f_names.items() if code.endswith("0000")}
    cursor.execute(_SQL_NAMES_ARTICOL)
    articol = {_text(r["Articol"]): _text(r["Denumire"]) for r in cursor.fetchall()}
    cursor.execute(_SQL_NAMES_SS)
    ss = {_text(r["SursaSector"]): _text(r["Denumire"]) for r in cursor.fetchall()}
    return {"capitol": capitol, "subcapitol": f_names, "articol": articol, "ss": ss}


@forexe_bp.route("/api/forexe/nomenclatoare/clasificatii", methods=["GET"])
@require_session
def get_clasificatii_tree():
    """Every classification of the session's database, with the names of the tree levels."""
    db_name = g.session.db_name
    conn = None
    try:
        conn = get_kbot_connection(db_name)
        cursor = conn.cursor(dictionary=True, buffered=True)
        cursor.execute(_SQL_ITEMS)
        items = [{
            "id_clsf": int(r["IDClsf"]),
            "id_unitate": int(r["IdUnitate"]) if r["IdUnitate"] is not None else None,
            "id_clsf_acc": int(r["IdClsfAcc"]) if r["IdClsfAcc"] is not None else None,
            "capitol": _text(r["Capitol"]),
            "subcapitol": _text(r["Subcapitol"]),
            "articol": _text(r["Articol"]),
            "alineat": _text(r["Alineat"]),
            "denumire": _text(r["Denumire"]),
            "ss": _text(r["SS"]),
            "clsf": _text(r["Clsf"]),
        } for r in cursor.fetchall()]
        return _json_utf8({"items": items, "names": read_names(cursor)}, 200)
    except Exception as e:
        logger.error("[forexe.clasificatii_edit] tree %s: %s", db_name, e, exc_info=True)
        return _json_utf8({"error": f"Eroare la citirea clasificațiilor: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()


# ---------------------------------------------------------------------------
# Budget + corrections of one classification
# ---------------------------------------------------------------------------
def _read_budget(cursor, id_clsf: int, an: int) -> dict:
    cursor.execute(_SQL_BUDGETS, (id_clsf, an))
    budgets = [{
        "id": int(r["IdBuget"]),
        "data_inceput": r["DataInceput"].isoformat() if r["DataInceput"] is not None else None,
        "trim1": _number(r["Trim1"]), "trim2": _number(r["Trim2"]),
        "trim3": _number(r["Trim3"]), "trim4": _number(r["Trim4"]),
    } for r in cursor.fetchall()]
    cursor.execute(_SQL_CORRECTIONS, (id_clsf, an))
    corrections = [{
        "id": int(r["ID"]),
        "document": _text(r["Document"]),
        "data": r["Data"].date().isoformat() if r["Data"] is not None else None,
        "trim1": _number(r["Trim1"]), "trim2": _number(r["Trim2"]),
        "trim3": _number(r["Trim3"]), "trim4": _number(r["Trim4"]),
    } for r in cursor.fetchall()]
    return {"budgets": budgets, "corrections": corrections}


# Summary for a non-leaf tree node: per classification, the LAST budget version of the year (greatest
# DataInceput) and the TOTAL of the year's corrections per quarter. One row per classification at
# most; (IdClsf, An, DataInceput) is unique, so the join to the maximum cannot double a row.
_SQL_SUMMARY_BUDGETS = (
    "SELECT B.IdClsf, B.IdBuget, B.DataInceput, B.Trim1, B.Trim2, B.Trim3, B.Trim4 "
    "  FROM Clasificatii_Buget B "
    "  JOIN (SELECT IdClsf, MAX(DataInceput) AS D FROM Clasificatii_Buget WHERE An = %s GROUP BY IdClsf) M "
    "    ON M.IdClsf = B.IdClsf AND M.D = B.DataInceput "
    " WHERE B.An = %s"
)
_SQL_SUMMARY_CORRECTIONS = (
    "SELECT IdClsf, SUM(Trim1) AS Trim1, SUM(Trim2) AS Trim2, SUM(Trim3) AS Trim3, SUM(Trim4) AS Trim4 "
    "  FROM Clasificatii_Rectificari "
    " WHERE Data >= %s AND Data < %s "
    " GROUP BY IdClsf"
)


# A classification has MOVEMENT in the year when ANY quarter of ANY of its budget versions or ANY
# quarter of ANY of its corrections is not zero. The quarters are tested one by one, never through a
# total: a correction of +1000 in quarter 1 and -1000 in quarter 2 totals 0 and is still activity.
_SQL_SUMMARY_ACTIVE = (
    "SELECT IdClsf FROM Clasificatii_Buget "
    " WHERE An = %s AND (COALESCE(Trim1, 0) <> 0 OR COALESCE(Trim2, 0) <> 0 "
    "                 OR COALESCE(Trim3, 0) <> 0 OR COALESCE(Trim4, 0) <> 0) "
    " UNION "
    "SELECT IdClsf FROM Clasificatii_Rectificari "
    " WHERE Data >= %s AND Data < %s AND (COALESCE(Trim1, 0) <> 0 OR COALESCE(Trim2, 0) <> 0 "
    "                                  OR COALESCE(Trim3, 0) <> 0 OR COALESCE(Trim4, 0) <> 0)"
)


def _read_summary(cursor, an: int) -> list:
    """{"id_clsf", "activ", "budget": {...}|null, "corrections": {"trim1".."trim4"}|null} for every
    classification that has a budget version or a correction in the year."""
    cursor.execute(_SQL_SUMMARY_ACTIVE, (an, date(an, 1, 1), date(an + 1, 1, 1)))
    active = {int(r["IdClsf"]) for r in cursor.fetchall()}
    cursor.execute(_SQL_SUMMARY_BUDGETS, (an, an))
    budgets = {}
    for r in cursor.fetchall():
        budgets[int(r["IdClsf"])] = {
            "id": int(r["IdBuget"]),
            "data_inceput": r["DataInceput"].isoformat() if r["DataInceput"] is not None else None,
            "trim1": _number(r["Trim1"]), "trim2": _number(r["Trim2"]),
            "trim3": _number(r["Trim3"]), "trim4": _number(r["Trim4"]),
        }
    cursor.execute(_SQL_SUMMARY_CORRECTIONS, (date(an, 1, 1), date(an + 1, 1, 1)))
    corrections = {}
    for r in cursor.fetchall():
        corrections[int(r["IdClsf"])] = {
            "trim1": _number(r["Trim1"]), "trim2": _number(r["Trim2"]),
            "trim3": _number(r["Trim3"]), "trim4": _number(r["Trim4"]),
        }
    return [{"id_clsf": id_clsf, "activ": id_clsf in active,
             "budget": budgets.get(id_clsf), "corrections": corrections.get(id_clsf)}
            for id_clsf in sorted(set(budgets) | set(corrections))]


@forexe_bp.route("/api/forexe/nomenclatoare/clasificatii/sumar-buget", methods=["GET"])
@require_session
def get_clasificatii_sumar_buget():
    """The last budget version and the corrections total of every classification, for one year."""
    db_name = g.session.db_name
    conn = None
    try:
        an = _year(request.args.get("an"))
        conn = get_kbot_connection(db_name)
        cursor = conn.cursor(dictionary=True, buffered=True)
        return _json_utf8({"items": _read_summary(cursor, an)}, 200)
    except _Refused as e:
        return _json_utf8({"error": str(e)}, 400)
    except Exception as e:
        logger.error("[forexe.clasificatii_edit] summary %s: %s", db_name, e, exc_info=True)
        return _json_utf8({"error": f"Eroare la citirea sumarului de buget: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()


def _classification(cursor, id_clsf: int) -> dict:
    cursor.execute(_SQL_ONE, (id_clsf,))
    row = cursor.fetchone()
    if row is None:
        raise _Refused(f"Clasificația {id_clsf} nu mai există în baza de date.")
    return row


@forexe_bp.route("/api/forexe/nomenclatoare/clasificatii/<int:id_clsf>/buget", methods=["GET"])
@require_session
def get_clasificatie_buget(id_clsf):
    """The budget of one classification for one year, and that year's corrections."""
    db_name = g.session.db_name
    conn = None
    try:
        an = _year(request.args.get("an"))
        conn = get_kbot_connection(db_name)
        cursor = conn.cursor(dictionary=True, buffered=True)
        _classification(cursor, id_clsf)
        return _json_utf8(_read_budget(cursor, id_clsf, an), 200)
    except _Refused as e:
        return _json_utf8({"error": str(e)}, 400)
    except Exception as e:
        logger.error("[forexe.clasificatii_edit] budget %s/%s: %s", db_name, id_clsf, e, exc_info=True)
        return _json_utf8({"error": f"Eroare la citirea bugetului: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()


def _save_budgets(cursor, clsf: dict, an: int, body: dict):
    """The budget versions of the year: removals first (so a date can be freed and used again in
    the same save), then every version of the request updated or added."""
    id_clsf = int(clsf["IDClsf"])
    deleted = body.get("deleted_budgets") or []
    if not isinstance(deleted, list):
        raise _Refused("Lista versiunilor de buget șterse nu are forma așteptată.")
    for raw_id in deleted:
        cursor.execute(_SQL_BUDGET_DELETE, (int(raw_id), id_clsf))

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
        row_id = row.get("id")
        if row_id:
            cursor.execute(_SQL_BUDGET_UPDATE, (start, *amounts, int(row_id), id_clsf, an))
        else:
            cursor.execute(_SQL_BUDGET_INSERT, (
                id_clsf, int(clsf["IdUnitate"]), an, start, *amounts))


def _save_budget(cursor, clsf: dict, an: int, body: dict):
    id_clsf = int(clsf["IDClsf"])
    _save_budgets(cursor, clsf, an, body)

    deleted = body.get("deleted") or []
    if not isinstance(deleted, list):
        raise _Refused("Lista rectificărilor șterse nu are forma așteptată.")
    for raw_id in deleted:
        cursor.execute(_SQL_CORRECTION_DELETE, (int(raw_id), id_clsf))

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
        if len(document) > _MAX_DOCUMENT:
            raise _Refused(f"{where}: numărul documentului are peste {_MAX_DOCUMENT} caractere.")
        try:
            day = date.fromisoformat(_text(row.get("data")))
        except ValueError:
            raise _Refused(f"{where} ({document}): lipsește data sau nu este o dată.")
        if day.year != an:
            raise _Refused(f"{where} ({document}): data {day:%d.%m.%Y} nu este în anul {an}.")
        amounts = [_amount(row, q, where) for q in _QUARTERS]
        row_id = row.get("id")
        if row_id:
            cursor.execute(_SQL_CORRECTION_UPDATE, (document, day, *amounts, int(row_id), id_clsf))
        else:
            cursor.execute(_SQL_CORRECTION_INSERT, (
                id_clsf, clsf["Capitol"], clsf["Subcapitol"], clsf["Articol"], clsf["Alineat"],
                day, document, *amounts))


@forexe_bp.route("/api/forexe/nomenclatoare/clasificatii/<int:id_clsf>/buget", methods=["POST"])
@require_session
def post_clasificatie_buget(id_clsf):
    """Saves the budget and the corrections of one classification, all or nothing."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _json_utf8({"error": "Corp JSON lipsă sau nevalid."}, 400)

    db_name = g.session.db_name
    conn = None
    try:
        an = _year(body.get("an"))
        conn = get_kbot_connection(db_name)
        conn.autocommit = False
        cursor = conn.cursor(dictionary=True, buffered=True)
        if not conn.in_transaction:
            conn.start_transaction()
        clsf = _classification(cursor, id_clsf)
        _save_budget(cursor, clsf, an, body)
        conn.commit()
        logger.info("[forexe.clasificatii_edit] %s: budget %s/%s saved", db_name, id_clsf, an)
        return _json_utf8(_read_budget(cursor, id_clsf, an), 200)
    except _Refused as e:
        _rollback(conn)
        return _json_utf8({"error": str(e)}, 400)
    except mysql.connector.IntegrityError as e:
        _rollback(conn)
        if e.errno == 1062:
            if "uq_clasificatii_buget" in str(e):
                return _json_utf8({"error": "Există deja un buget cu aceeași dată de început pe "
                                            "această clasificație."}, 409)
            return _json_utf8({"error": "Există deja o rectificare cu același document și aceeași "
                                        "dată pe această clasificație."}, 409)
        logger.error("[forexe.clasificatii_edit] budget save %s/%s: %s", db_name, id_clsf, e, exc_info=True)
        return _json_utf8({"error": f"Eroare la salvarea bugetului: {e}"}, 500)
    except Exception as e:
        _rollback(conn)
        logger.error("[forexe.clasificatii_edit] budget save %s/%s: %s", db_name, id_clsf, e, exc_info=True)
        return _json_utf8({"error": f"Eroare la salvarea bugetului: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()


# ---------------------------------------------------------------------------
# Adding classifications (the registration's lists and rules)
# ---------------------------------------------------------------------------
def _units_by_ss(cursor, an: int) -> dict:
    """
    {SS: IdUnitate} for the units of this database. When one SS has several units, the one of
    the requested year wins; with no such unit the SS is left out (ambiguous, never guessed).
    """
    cursor.execute(_SQL_UNITS)
    by_ss = {}
    for r in cursor.fetchall():
        by_ss.setdefault(_text(r["SursaSector"]), []).append(r)
    out = {}
    for ss, rows in by_ss.items():
        if len(rows) == 1:
            out[ss] = int(rows[0]["IdUnitate"])
            continue
        of_year = [r for r in rows if r["An"] is not None and int(r["An"]) == an]
        if len(of_year) == 1:
            out[ss] = int(of_year[0]["IdUnitate"])
    return out


@forexe_bp.route("/api/forexe/nomenclatoare/clasificatii/nomenclator", methods=["GET"])
@require_session
def get_clasificatii_nomenclator():
    """The three lists the «add classifications» window picks from."""
    db_name = g.session.db_name
    conn = None
    try:
        an = _year(request.args.get("an"))
        conn = get_kbot_connection(db_name)
        cursor = conn.cursor(dictionary=True, buffered=True)
        units = _units_by_ss(cursor, an)
        cursor.execute(_SQL_NAMES_SS)
        ss_names = {_text(r["SursaSector"]): _text(r["Denumire"]) for r in cursor.fetchall()}
        ss = [{"code": code, "name": ss_names.get(code) or code} for code in sorted(units)]
        f_codes = registration_lists.read_clasificatii(conn, "F")
        e_codes = registration_lists.read_clasificatii(conn, "E")
        return _json_utf8({
            "ss": ss,
            "f": {"codes": [{"code": c["cod"], "name": c["denumire"]} for c in f_codes],
                  "groups": registration_lists.read_group_captions(conn, "F")},
            "e": {"codes": [{"code": c["cod"], "name": c["denumire"]} for c in e_codes],
                  "groups": registration_lists.read_group_captions(conn, "E")},
        }, 200)
    except _Refused as e:
        return _json_utf8({"error": str(e)}, 400)
    except Exception as e:
        logger.error("[forexe.clasificatii_edit] nomenclator %s: %s", db_name, e, exc_info=True)
        return _json_utf8({"error": f"Eroare la citirea nomenclatoarelor: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()


def _code_list(body: dict, key: str, label: str) -> list:
    raw = body.get(key)
    if not isinstance(raw, list) or not raw:
        raise _Refused(f"Nu ați ales nicio {label}.")
    return sorted({_text(v) for v in raw if _text(v)})


@forexe_bp.route("/api/forexe/nomenclatoare/clasificatii/adauga", methods=["POST"])
@require_session
def post_clasificatii_adauga():
    """Adds F x E x SS classifications; rows already present are skipped."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _json_utf8({"error": "Corp JSON lipsă sau nevalid."}, 400)

    db_name = g.session.db_name
    conn = None
    try:
        an = _year(body.get("an"))
        ss_list = _code_list(body, "ss", "sursă-sector")
        f_list = _code_list(body, "f", "clasificație funcțională")
        e_list = _code_list(body, "e", "clasificație economică")

        conn = get_kbot_connection(db_name)
        conn.autocommit = False
        cursor = conn.cursor(dictionary=True, buffered=True)
        units = _units_by_ss(cursor, an)
        unknown = [ss for ss in ss_list if ss not in units]
        if unknown:
            raise _Refused("Sursele-sector " + ", ".join(unknown) + " nu au o unitate în această "
                           "bază de date; se pot alege doar sursele unității.")

        # The same five dictionaries and the same checks as the registration, so a row that
        # would fail a foreign key is refused here, in Romanian, before anything is written.
        plain = conn.cursor(buffered=True)
        rows = randuri.build(ss_list, f_list, e_list, units, randuri.read_dictionaries(conn))

        if not conn.in_transaction:
            conn.start_transaction()
        inserted = 0
        existing = 0
        for row in rows:
            id_unitate, capitol, subcapitol, articol, alineat = row[:5]
            cursor.execute(_SQL_EXISTS, (id_unitate, capitol, subcapitol, articol, alineat))
            if cursor.fetchone() is not None:
                existing += 1
                continue
            plain.execute(randuri.INSERT_SQL, row)
            inserted += 1
        conn.commit()
        logger.info("[forexe.clasificatii_edit] %s: add requested %s, inserted %s, existing %s",
                    db_name, len(rows), inserted, existing)
        return _json_utf8({"requested": len(rows), "inserted": inserted, "existing": existing}, 200)
    except (_Refused, randuri.RanduriInvalide) as e:
        _rollback(conn)
        return _json_utf8({"error": str(e)}, 400)
    except Exception as e:
        _rollback(conn)
        logger.error("[forexe.clasificatii_edit] add %s: %s", db_name, e, exc_info=True)
        return _json_utf8({"error": f"Eroare la adăugarea clasificațiilor: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()


def _rollback(conn):
    if conn is None:
        return
    try:
        conn.rollback()
    except Exception:
        logger.warning("[forexe.clasificatii_edit] rollback failed", exc_info=True)


# ---------------------------------------------------------------------------
# Check: the budget FOREXE reported == the budget K-BOT holds (slice 0103-04)
# ---------------------------------------------------------------------------
# What FOREXE reported = the `Credit_Bugetar` of the MOST RECENTLY downloaded indicator row of each
# classification (FX_Indicatori.DTQ). What K-BOT holds = the version in force on the day plus its
# rectifications, cumulative to the quarter of the day (budget_on_day.py -- the same rule the DDF
# uses). Rows whose two figures differ by less than half a cent are equal.
_SQL_CHECK_CLASSIFICATIONS = (
    "SELECT C.IDClsf, C.IdUnitate, C.Clsf, C.Denumire, C.SS "
    "  FROM Clasificatii C "
    "  LEFT JOIN Unitati U ON U.IdUnitate = C.IdUnitate "
    " WHERE COALESCE(U.Ascuns, 0) = 0 "
    " ORDER BY C.Capitol, C.Subcapitol, C.Articol, C.Alineat"
)
_SQL_CHECK_FX_ANGAJAMENT = (
    "SELECT IdClsf, Credit_Bugetar FROM FX_Indicatori "
    " WHERE IdClsf IS NOT NULL AND IdClsf <> 0 AND CodAngajament = %s "
    " ORDER BY DTQ DESC"
)
_SQL_CHECK_FX = (
    "SELECT IdClsf, Credit_Bugetar FROM FX_Indicatori "
    " WHERE IdClsf IS NOT NULL AND IdClsf <> 0 "
    " ORDER BY DTQ DESC"
)
_EQUAL_WITHIN = 0.005


def check_budget(cursor, day: date, cod_angajament: str = "") -> list:
    """One entry per classification that has a K-BOT budget on `day` or an indicator row. With
    `cod_angajament` only the classifications of that angajament, against ITS indicator rows (the
    automatic check after a download)."""
    if cod_angajament:
        cursor.execute(_SQL_CHECK_FX_ANGAJAMENT, (cod_angajament,))
    else:
        cursor.execute(_SQL_CHECK_FX)
    credit_fx = {}
    for r in cursor.fetchall():
        credit_fx.setdefault(int(r["IdClsf"]), _number(r["Credit_Bugetar"]))

    cursor.execute(_SQL_CHECK_CLASSIFICATIONS)
    classifications = cursor.fetchall()
    items = []
    for c in classifications:
        id_clsf = int(c["IDClsf"])
        if cod_angajament and id_clsf not in credit_fx:
            continue
        kbot = budget_on_day(cursor, id_clsf, day)
        fx = credit_fx.get(id_clsf)
        if kbot is None and fx is None:
            continue
        diff = round((fx or 0.0) - (kbot or 0.0), 2)
        items.append({
            "id_clsf": id_clsf,
            "id_unitate": int(c["IdUnitate"]) if c["IdUnitate"] is not None else None,
            "clsf": _text(c["Clsf"]),
            "denumire": _text(c["Denumire"]),
            "ss": _text(c["SS"]),
            "buget_kbot": kbot,
            "credit_fx": fx,
            "diferenta": diff,
            "egal": kbot is not None and fx is not None and abs(diff) < _EQUAL_WITHIN,
        })
    return items


@forexe_bp.route("/api/forexe/nomenclatoare/clasificatii/verificare-buget", methods=["GET"])
@require_session
def get_verificare_buget():
    """The FOREXE credit against the K-BOT budget (+ rectifications) of every classification."""
    db_name = g.session.db_name
    conn = None
    try:
        raw_day = request.args.get("data")
        day = as_day(raw_day) if raw_day else date.today()
        if day is None:
            raise _Refused("Data nu este o dată (aaaa-ll-zz).")
        conn = get_kbot_connection(db_name)
        cursor = conn.cursor(dictionary=True, buffered=True)
        items = check_budget(cursor, day, _text(request.args.get("angajament")))
        return _json_utf8({"data": day.isoformat(), "trimestru": quarter_of(day), "items": items}, 200)
    except _Refused as e:
        return _json_utf8({"error": str(e)}, 400)
    except Exception as e:
        logger.error("[forexe.clasificatii_edit] check %s: %s", db_name, e, exc_info=True)
        return _json_utf8({"error": f"Eroare la verificarea bugetului: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()
