# routes/avacont/configurari/sablon_salarii/sablon_salarii.py
"""
The salary templates of the unit database (slice AV01-15; tables TZ_SablonSalarii + TZ_SablonSalariiLinie; the Access DefaSalarii + VA + PC + PN,
button OCS, form OCS). Named SablonSalarii by the operator.

An ARTICLE (a classification of the pay roll, NN.NN.NN, of one SUBUNIT) has ONE commitment operation (debit / credit base accounts + partners) and lines of
three kinds: RETINERE (kept from the salary; its DEBIT is the CREDIT of the article), VIRAMENT (the payment of what was kept; it may point at the
RETINERE it pays), NUMERAR (salaries paid in cash). The accounts are BASE accounts (AVACONT_COMUN.DefaCont); the full account is composed when a month is applied.

    GET  /api/avacont/sabloane-salarii
        -> 200 { "articole": [ { "idarticol", "idsubunitate", "clasificatie", "explicatie", "contdebit", "contcredit",
                                 "partdebitmod", "idpartdebit", "partcreditmod", "idpartcredit",
                                 "linii": [ { "idlinie", "tip", "idparinte", "explicatie", "contdebit", "contcredit", "partdebitmod", "idpartdebit",
                                              "partcreditmod", "idpartcredit", "estecm" } ] } ],
                 "subunitati": [ { "idsubunitate", "capitol", "sursasector", "denumire" } ],
                 "conturi":    [ { "simbol", "denumire" } ],
                 "parteneri":  [ { "idpartener", "cod", "denumire" } ] }
        Every article of the unit database (the window keeps the ones of the chosen subunit), plus the lists its combos need.

    POST /api/avacont/sabloane-salarii
        { "articole": [ { "idarticol": n | null, ...article..., "linii": [ { "ref": n, "idlinie": n | null, "parinte": ref | null, ...line... } ] } ], "sterse": [ idarticol, ... ] }
        -> 200 { "articole": n }.  ONE transaction: the articles in "sterse" are deleted, then every article sent is updated or inserted together with its
        lines (a line with idlinie is updated, one without is added, an old line that is not sent is deleted). "ref" is a number the window gives every
        line so that a VIRAMENT can name the RETINERE it pays even when that one is not saved yet ("parinte" = the ref of that line).
        400 for an invalid article (the message names it), 409 for a line or article that is used and cannot be deleted.

Rules checked here: an article needs at least ONE virament (operator, 23.10.2026); every account exists; a fixed partner needs a partner; at most one
medical-leave line per kind; a RETINERE takes its debit (account + partner) from the credit of its article, whatever the window sent; the classification is NN.NN.NN.
"""
import logging
import re

import mysql.connector
from mysql.connector import errorcode
from flask import g, request

from routes.auth.guard import require_session
from utils.database import get_kbot_connection

from ... import avacont_bp
from ...common import json_utf8, rollback

logger = logging.getLogger(__name__)

_CLASIFICATIE = re.compile(r"^[0-9]{2}\.[0-9]{2}\.[0-9]{2}$")
_MODURI = ("NIMIC", "OPERATIE", "FIX")
_TIPURI = ("RETINERE", "VIRAMENT", "NUMERAR")


@avacont_bp.route("/api/avacont/sabloane-salarii", methods=["GET"])
@require_session
def get_sabloane_salarii():
    """Every article with its lines, and the lists the window's combos need."""
    db_name = g.session.db_name
    conn = None
    try:
        conn = get_kbot_connection(db_name)
        cursor = conn.cursor()
        cursor.execute("SELECT IdArticol, IdSubunitate, Clasificatie, Explicatie, ContDebit, ContCredit, PartDebitMod, IdPartDebit, PartCreditMod, IdPartCredit "
                       "  FROM TZ_SablonSalarii ORDER BY IdSubunitate, Clasificatie, IdArticol")
        articole = {}
        for r in cursor.fetchall():
            articole[int(r[0])] = {
                "idarticol": int(r[0]), "idsubunitate": int(r[1]), "clasificatie": r[2], "explicatie": r[3] or "",
                "contdebit": int(r[4]), "contcredit": int(r[5]),
                "partdebitmod": r[6], "idpartdebit": int(r[7]) if r[7] is not None else None,
                "partcreditmod": r[8], "idpartcredit": int(r[9]) if r[9] is not None else None, "linii": []}
        cursor.execute("SELECT IdLinie, IdArticol, Tip, IdParinte, Explicatie, ContDebit, ContCredit, PartDebitMod, IdPartDebit, PartCreditMod, IdPartCredit, EsteCM "
                       "  FROM TZ_SablonSalariiLinie ORDER BY IdArticol, Tip, Ordine, IdLinie")
        for r in cursor.fetchall():
            articol = articole.get(int(r[1]))
            if articol is None:
                continue
            articol["linii"].append({
                "idlinie": int(r[0]), "tip": r[2], "idparinte": int(r[3]) if r[3] is not None else None, "explicatie": r[4] or "",
                "contdebit": int(r[5]), "contcredit": int(r[6]),
                "partdebitmod": r[7], "idpartdebit": int(r[8]) if r[8] is not None else None,
                "partcreditmod": r[9], "idpartcredit": int(r[10]) if r[10] is not None else None, "estecm": bool(r[11])})
        cursor.execute("SELECT IdSubunitate, Capitol, SursaSector, Denumire FROM TZ_Subunitati ORDER BY Capitol, Denumire")
        subunitati = [{"idsubunitate": int(r[0]), "capitol": r[1], "sursasector": r[2], "denumire": r[3] or ""} for r in cursor.fetchall()]
        cursor.execute("SELECT Simbol, DENUMIRE FROM AVACONT_COMUN.DefaCont ORDER BY Simbol")
        conturi = [{"simbol": int(r[0]), "denumire": r[1] or ""} for r in cursor.fetchall()]
        cursor.execute("SELECT IdPartener, CodPartener, DenumirePartener FROM Parteneri ORDER BY CodPartener")
        parteneri = [{"idpartener": int(r[0]), "cod": r[1], "denumire": r[2] or ""} for r in cursor.fetchall()]
        logger.info("[avacont.sablon_salarii] %s: %s articole", db_name, len(articole))
        return json_utf8({"articole": list(articole.values()), "subunitati": subunitati, "conturi": conturi, "parteneri": parteneri}, 200)
    except Exception as e:
        logger.error("[avacont.sablon_salarii] citire %s: %s", db_name, e, exc_info=True)
        return json_utf8({"error": f"Eroare la citirea șabloanelor de salarii: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()


def _intreg(valoare):
    """int(valoare) or None when it is missing; ValueError when it is not a number."""
    if valoare is None or valoare == "":
        return None
    return int(valoare)


def _parte(brut: dict, nume: str, prefix_mod: str, prefix_id: str):
    """(mode, partner id, error) of one side. `nume` names the row in the message."""
    mod = str(brut.get(prefix_mod) or "NIMIC").strip().upper()
    if mod not in _MODURI:
        return None, None, f"{nume}: partenerul trebuie să fie «fără», «al operației» sau «fix»."
    idp = _intreg(brut.get(prefix_id))
    if mod == "FIX" and idp is None:
        return None, None, f"{nume}: alegeți partenerul fix."
    return mod, (idp if mod == "FIX" else None), None


def _valideaza_linie(articol: dict, nr: int, brut):
    """(line, error): one line checked. `nr` is the 1-based position among the lines sent, for the message."""
    nume = f"Articolul {articol['clasificatie']}, linia {nr}"
    if not isinstance(brut, dict):
        return None, f"{nume}: date nevalide."
    tip = str(brut.get("tip") or "").strip().upper()
    if tip not in _TIPURI:
        return None, f"{nume}: felul liniei trebuie să fie reținere, virament sau numerar."
    try:
        ref = _intreg(brut.get("ref"))
        idlinie = _intreg(brut.get("idlinie"))
        parinte = _intreg(brut.get("parinte"))
        cont_debit = _intreg(brut.get("contdebit"))
        cont_credit = _intreg(brut.get("contcredit"))
    except (TypeError, ValueError):
        return None, f"{nume}: număr nevalid."
    explicatie = str(brut.get("explicatie") or "").strip()
    if not explicatie:
        return None, f"{nume}: completați explicația."
    if len(explicatie) > 255:
        return None, f"{nume}: explicația este prea lungă."
    if cont_debit is None or cont_credit is None:
        return None, f"{nume}: alegeți contul debitor și contul creditor."
    try:
        mod_d, id_d, eroare = _parte(brut, nume, "partdebitmod", "idpartdebit")
        if eroare:
            return None, eroare
        mod_c, id_c, eroare = _parte(brut, nume, "partcreditmod", "idpartcredit")
        if eroare:
            return None, eroare
    except (TypeError, ValueError):
        return None, f"{nume}: număr nevalid."
    cm = bool(brut.get("estecm"))
    if cm and tip == "NUMERAR":
        return None, f"{nume}: «CM» se bifează doar la rețineri și viramente."
    if parinte is not None and tip != "VIRAMENT":
        return None, f"{nume}: doar un virament poate plăti o reținere."
    return {"ref": ref, "idlinie": idlinie, "parinte": parinte, "tip": tip, "explicatie": explicatie, "contdebit": cont_debit, "contcredit": cont_credit,
            "partdebitmod": mod_d, "idpartdebit": id_d, "partcreditmod": mod_c, "idpartcredit": id_c, "estecm": cm}, None


def _valideaza_articol(brut, nr: int):
    """(article, error): one article with its lines checked."""
    if not isinstance(brut, dict):
        return None, f"Articolul {nr}: date nevalide."
    clasificatie = str(brut.get("clasificatie") or "").strip()
    if not _CLASIFICATIE.match(clasificatie):
        return None, f"Articolul {nr}: clasificația trebuie să fie de felul 10.01.01."
    nume = f"Articolul {clasificatie}"
    explicatie = str(brut.get("explicatie") or "").strip()
    if not explicatie:
        return None, f"{nume}: completați explicația."
    if len(explicatie) > 255:
        return None, f"{nume}: explicația este prea lungă."
    try:
        idarticol = _intreg(brut.get("idarticol"))
        idsub = _intreg(brut.get("idsubunitate"))
        cont_debit = _intreg(brut.get("contdebit"))
        cont_credit = _intreg(brut.get("contcredit"))
    except (TypeError, ValueError):
        return None, f"{nume}: număr nevalid."
    if idsub is None:
        return None, f"{nume}: alegeți capitolul (subunitatea)."
    if cont_debit is None or cont_credit is None:
        return None, f"{nume}: alegeți contul debitor și contul creditor al angajamentului."
    try:
        mod_d, id_d, eroare = _parte(brut, nume, "partdebitmod", "idpartdebit")
        if eroare:
            return None, eroare
        mod_c, id_c, eroare = _parte(brut, nume, "partcreditmod", "idpartcredit")
        if eroare:
            return None, eroare
    except (TypeError, ValueError):
        return None, f"{nume}: număr nevalid."
    articol = {"idarticol": idarticol, "idsubunitate": idsub, "clasificatie": clasificatie, "explicatie": explicatie,
               "contdebit": cont_debit, "contcredit": cont_credit, "partdebitmod": mod_d, "idpartdebit": id_d,
               "partcreditmod": mod_c, "idpartcredit": id_c, "linii": []}
    linii_brut = brut.get("linii")
    if not isinstance(linii_brut, list):
        return None, f"{nume}: lista de linii lipsește."
    refuri = set()
    for i, lb in enumerate(linii_brut, start=1):
        linie, eroare = _valideaza_linie(articol, i, lb)
        if eroare:
            return None, eroare
        if linie["ref"] is None or linie["ref"] in refuri:
            return None, f"{nume}, linia {i}: referința liniei lipsește sau se repetă."
        refuri.add(linie["ref"])
        articol["linii"].append(linie)
    tipuri = {l["ref"]: l["tip"] for l in articol["linii"]}
    for l in articol["linii"]:
        if l["parinte"] is not None and tipuri.get(l["parinte"]) != "RETINERE":
            l["parinte"] = None          # the retention it paid is gone: the virament stays, without a link
        if l["tip"] == "RETINERE":
            # The debit of a retention IS the credit of its article.
            l["contdebit"] = articol["contcredit"]
            l["partdebitmod"] = articol["partcreditmod"]
            l["idpartdebit"] = articol["idpartcredit"]
    if not any(l["tip"] == "VIRAMENT" for l in articol["linii"]):
        return None, f"{nume}: un articol are nevoie de cel puțin un virament; adăugați unul înainte de salvare."
    for tip in ("RETINERE", "VIRAMENT"):
        if sum(1 for l in articol["linii"] if l["tip"] == tip and l["estecm"]) > 1:
            return None, f"{nume}: «CM» se poate bifa la cel mult o linie din fiecare fel."
    return articol, None


def _valideaza(corp: dict):
    """(articole, sterse, error): the body checked; error is the Romanian message of the first problem."""
    articole_brut = corp.get("articole")
    sterse_brut = corp.get("sterse", [])
    if not isinstance(articole_brut, list) or not isinstance(sterse_brut, list):
        return None, None, "Lista de articole lipsește."
    sterse = []
    for s in sterse_brut:
        try:
            sterse.append(int(s))
        except (TypeError, ValueError):
            return None, None, "Un articol de șters nu este valid."
    articole = []
    for i, brut in enumerate(articole_brut, start=1):
        articol, eroare = _valideaza_articol(brut, i)
        if eroare:
            return None, None, eroare
        articole.append(articol)
    return articole, sterse, None


def _verifica_referinte(cursor, articole):
    """The message for the first subunit / account / partner an article points at that does not exist; None when all exist."""
    cursor.execute("SELECT IdSubunitate FROM TZ_Subunitati")
    subunitati = {int(r[0]) for r in cursor.fetchall()}
    cursor.execute("SELECT Simbol FROM AVACONT_COMUN.DefaCont")
    conturi = {int(r[0]) for r in cursor.fetchall()}
    cursor.execute("SELECT IdPartener FROM Parteneri")
    parteneri = {int(r[0]) for r in cursor.fetchall()}
    for a in articole:
        nume = f"Articolul {a['clasificatie']}"
        if a["idsubunitate"] not in subunitati:
            return f"{nume}: capitolul ales nu există."
        for rand in [a] + a["linii"]:
            for cont in (rand["contdebit"], rand["contcredit"]):
                if cont not in conturi:
                    return f"{nume}: contul {cont} nu există în nomenclator."
            for part in (rand["idpartdebit"], rand["idpartcredit"]):
                if part is not None and part not in parteneri:
                    return f"{nume}: un partener fix ales nu mai există."
    return None


def _scrie_articol(cursor, a):
    """Writes one article and its lines (inside the caller's transaction); returns None or a (message, status) pair."""
    if a["idarticol"] is None:
        cursor.execute(
            "INSERT INTO TZ_SablonSalarii (IdSubunitate, Clasificatie, Explicatie, ContDebit, ContCredit, PartDebitMod, IdPartDebit, PartCreditMod, IdPartCredit) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (a["idsubunitate"], a["clasificatie"], a["explicatie"], a["contdebit"], a["contcredit"], a["partdebitmod"], a["idpartdebit"],
             a["partcreditmod"], a["idpartcredit"]))
        a["idarticol"] = int(cursor.lastrowid)
        vechi = set()
    else:
        cursor.execute("SELECT 1 FROM TZ_SablonSalarii WHERE IdArticol = %s", (a["idarticol"],))
        if not cursor.fetchone():
            return f"Articolul {a['clasificatie']} nu mai există (l-a șters altcineva).", 404
        cursor.execute(
            "UPDATE TZ_SablonSalarii SET IdSubunitate = %s, Clasificatie = %s, Explicatie = %s, ContDebit = %s, ContCredit = %s, PartDebitMod = %s, "
            "IdPartDebit = %s, PartCreditMod = %s, IdPartCredit = %s WHERE IdArticol = %s",
            (a["idsubunitate"], a["clasificatie"], a["explicatie"], a["contdebit"], a["contcredit"], a["partdebitmod"], a["idpartdebit"],
             a["partcreditmod"], a["idpartcredit"], a["idarticol"]))
        cursor.execute("SELECT IdLinie FROM TZ_SablonSalariiLinie WHERE IdArticol = %s", (a["idarticol"],))
        vechi = {int(r[0]) for r in cursor.fetchall()}
    pastrate = {l["idlinie"] for l in a["linii"] if l["idlinie"] is not None}
    for idl in vechi - pastrate:
        try:
            cursor.execute("DELETE FROM TZ_SablonSalariiLinie WHERE IdLinie = %s", (idl,))
        except mysql.connector.Error as e:
            if e.errno == errorcode.ER_ROW_IS_REFERENCED_2:
                return f"Articolul {a['clasificatie']}: o linie ștearsă este folosită de operații și nu se poate șterge.", 409
            raise
    ids = {}
    ordine = {}
    for l in a["linii"]:
        ordine[l["tip"]] = ordine.get(l["tip"], 0) + 1
        valori = (l["tip"], ordine[l["tip"]], l["explicatie"], l["contdebit"], l["contcredit"], l["partdebitmod"], l["idpartdebit"],
                  l["partcreditmod"], l["idpartcredit"], int(l["estecm"]))
        if l["idlinie"] is not None and l["idlinie"] in vechi:
            cursor.execute(
                "UPDATE TZ_SablonSalariiLinie SET Tip = %s, IdParinte = NULL, Ordine = %s, Explicatie = %s, ContDebit = %s, ContCredit = %s, PartDebitMod = %s, "
                "IdPartDebit = %s, PartCreditMod = %s, IdPartCredit = %s, EsteCM = %s WHERE IdLinie = %s AND IdArticol = %s",
                (valori[0], valori[1], valori[2], valori[3], valori[4], valori[5], valori[6], valori[7], valori[8], valori[9], l["idlinie"], a["idarticol"]))
            ids[l["ref"]] = l["idlinie"]
        else:
            cursor.execute(
                "INSERT INTO TZ_SablonSalariiLinie (IdArticol, Tip, Ordine, Explicatie, ContDebit, ContCredit, PartDebitMod, IdPartDebit, PartCreditMod, "
                "IdPartCredit, EsteCM) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)", (a["idarticol"],) + valori)
            ids[l["ref"]] = int(cursor.lastrowid)
    for l in a["linii"]:
        if l["parinte"] is not None and l["parinte"] in ids:
            cursor.execute("UPDATE TZ_SablonSalariiLinie SET IdParinte = %s WHERE IdLinie = %s", (ids[l["parinte"]], ids[l["ref"]]))
    return None


@avacont_bp.route("/api/avacont/sabloane-salarii", methods=["POST"])
@require_session
def post_sabloane_salarii():
    """Writes the articles the window changed."""
    corp = request.get_json(silent=True)
    if not isinstance(corp, dict):
        return json_utf8({"error": "Corp JSON lipsă sau nevalid."}, 400)
    articole, sterse, eroare = _valideaza(corp)
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
        eroare = _verifica_referinte(cursor, articole)
        if eroare:
            rollback(conn)
            return json_utf8({"error": eroare}, 400)
        for ida in sterse:
            cursor.execute("SELECT Clasificatie FROM TZ_SablonSalarii WHERE IdArticol = %s", (ida,))
            gasit = cursor.fetchone()
            if not gasit:
                continue
            try:
                cursor.execute("DELETE FROM TZ_SablonSalarii WHERE IdArticol = %s", (ida,))
            except mysql.connector.Error as e:
                if e.errno == errorcode.ER_ROW_IS_REFERENCED_2:
                    rollback(conn)
                    return json_utf8({"error": f"Articolul {gasit[0]} este folosit de salarii salvate și nu se poate șterge."}, 409)
                raise
        for a in articole:
            problema = _scrie_articol(cursor, a)
            if problema:
                rollback(conn)
                return json_utf8({"error": problema[0]}, problema[1])
        conn.commit()
        logger.info("[avacont.sablon_salarii] %s: %s articole salvate, %s sterse", db_name, len(articole), len(sterse))
        return json_utf8({"articole": len(articole)}, 200)
    except mysql.connector.Error as e:
        rollback(conn)
        logger.error("[avacont.sablon_salarii] salvare %s: %s", db_name, e, exc_info=True)
        return json_utf8({"error": f"Eroare la salvarea șabloanelor de salarii: {e}"}, 500)
    except Exception as e:
        rollback(conn)
        logger.error("[avacont.sablon_salarii] salvare %s: %s", db_name, e, exc_info=True)
        return json_utf8({"error": f"Eroare la salvarea șabloanelor de salarii: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()
