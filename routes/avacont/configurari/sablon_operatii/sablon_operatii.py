# routes/avacont/configurari/sablon_operatii/sablon_operatii.py
"""
The operation templates of the unit database (slice AV01-14; tables TZ_SablonOperatii + TZ_SablonOperatiiLinie; the Access DefaNoteA + DefaNote,
button OCI, form Operatii2020). Named SablonOperatii by the operator (27.10.2026).

A template = a HEADER (journal, type M/A, kind E/P/A, explanation, department, payment text, classification key) + LINES (debit account, credit account,
who the partner is on each side, three flags). The accounts of a line are BASE accounts (AVACONT_COMUN.DefaCont); the full account is composed when the
template is used (stage 2). The sector+source of a template is the one of its journal: the window shows the templates whose journal has the sector+source of the
title bar, or all of them on «Toate».

    GET  /api/avacont/sabloane-operatii
        -> 200 { "sabloane": [ { "idsablon", "idjurnal", "tip", "fel", "explicatie", "compartiment", "felop", "cheie",
                                 "linii": [ { "idlinie", "forma", "contdebit", "contcredit", "partdebitmod", "idpartdebit",
                                              "partcreditmod", "idpartcredit", "pa", "ordonantare", "chelt",
                                              "idclsf", "analiticdebit", "analiticcredit" } ] } ],
                 "jurnale":   [ { "idjurnal", "sursasector", "cod", "denumire" } ],
                 "conturi":   [ { "simbol", "denumire" } ],
                 "conturi_plan": [ { "ss", "simbol", "cont", "analitic", "clsff", "clsfe", "denumire" } ],
                 "clsf":      [ { "idclsf", "clsf", "clsff", "clsfe", "ss", "capitol", "articol", "alineat", "denumire" } ],
                 "parteneri": [ { "idpartener", "cod", "denumire" } ],
                 "clasificatii": [ { "cheie", "ss", "forme": [ "04.02", ... ] } ],
                 "nume_clsfe": [ { "cod", "denumire" } ] }
        "clasificatii" = the keys (ClsfX) the tree of type M hangs templates on; "nume_clsfe" = the names of the economic titles / articles / paragraphs
        (DefaClsfE) the tree shows. Every template of the unit database (the window filters by the journal's sector+source), plus the lists its combos need.
        "conturi_plan" = the chart of accounts of the unit (slice AV01-17): one row per symbol and sector+source, with the analytic and, for an account tied to a
        classification, its functional (ClsfF = chapter + form) and economic (ClsfE = article + paragraph) part; the line combos list these. A line keeps the BASE
        account (contdebit / contcredit) plus the analytic (analiticdebit / analiticcredit); "idclsf" links the line to its classification (Clasificatii.IDClsf):
        type A sends it per line (null = none), type M gets it from the server (key of the template + form of the line + sector+source of the journal).
        "clsf" = every concrete classification of the unit, for the combo of a line of type A.

    POST /api/avacont/sabloane-operatii
        { "sabloane": [ { "idsablon": n | null, ...header..., "linii": [ ...lines... ] } ], "sterse": [ idsablon, ... ] }
        -> 200 { "sabloane": n }.  ONE transaction: the templates in "sterse" are deleted, then every template sent is updated (idsablon given) or
        inserted (idsablon null) together with ALL its lines (the lines of an updated template are replaced). Only the templates the window changed are
        sent. 400 for an invalid template (the message names it), 409 for a template that is used and cannot be deleted.

Rules checked here (operator, 27.10.2026): type M = a classification key + a partner on ONE side (none on the other); type A = anything else, the key may
be empty. A template needs at least one line. The fixed partner (FIX) needs a partner; the flags need a classification (type M: the key; type A: the
classification of the line, slice AV01-17); on type M PA only with kind E, Ordonantare only with kind P;
FelOP only with kind P. The form (subchapter) of a line is NN.NN, unique inside a template; lines without a form (type A, where the classification is missing) may repeat.
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

_CHEIE = re.compile(r"^[0-9X.]{1,30}$")
_FORMA = re.compile(r"^[0-9]{2}\.[0-9]{2}$")
_ANALITIC = re.compile(r"^[0-9A-Z_]{1,10}$")
_MODURI = ("NIMIC", "OPERATIE", "FIX")


@avacont_bp.route("/api/avacont/sabloane-operatii", methods=["GET"])
@require_session
def get_sabloane_operatii():
    """Every template with its lines, and the lists the window's combos need."""
    db_name = g.session.db_name
    conn = None
    try:
        conn = get_kbot_connection(db_name)
        cursor = conn.cursor()
        cursor.execute("SELECT IdSablon, IdJurnal, Tip, Fel, Explicatie, Compartiment, FelOP, Cheie "
                       "  FROM TZ_SablonOperatii ORDER BY Tip, Fel, Explicatie, IdSablon")
        sabloane = {}
        for r in cursor.fetchall():
            sabloane[int(r[0])] = {"idsablon": int(r[0]), "idjurnal": int(r[1]), "tip": r[2], "fel": r[3], "explicatie": r[4] or "",
                                   "compartiment": r[5] or "", "felop": r[6] or "", "cheie": r[7] or "", "linii": []}
        cursor.execute("SELECT IdLinie, IdSablon, Forma, ContDebit, ContCredit, PartDebitMod, IdPartDebit, PartCreditMod, IdPartCredit, "
                       "       PA, Ordonantare, Chelt, IdClsf, AnaliticDebit, AnaliticCredit "
                       "  FROM TZ_SablonOperatiiLinie ORDER BY IdSablon, Forma, IdLinie")
        for r in cursor.fetchall():
            sablon = sabloane.get(int(r[1]))
            if sablon is None:
                continue
            sablon["linii"].append({
                "idlinie": int(r[0]), "forma": r[2] or "", "contdebit": int(r[3]), "contcredit": int(r[4]),
                "partdebitmod": r[5], "idpartdebit": int(r[6]) if r[6] is not None else None,
                "partcreditmod": r[7], "idpartcredit": int(r[8]) if r[8] is not None else None,
                "pa": bool(r[9]), "ordonantare": bool(r[10]), "chelt": bool(r[11]),
                "idclsf": int(r[12]) if r[12] is not None else None, "analiticdebit": r[13] or "", "analiticcredit": r[14] or ""})
        cursor.execute("SELECT IdJurnal, SursaSector, Cod, Denumire FROM TZ_Jurnale ORDER BY SursaSector, Cod")
        jurnale = [{"idjurnal": int(r[0]), "sursasector": r[1], "cod": r[2], "denumire": r[3] or ""} for r in cursor.fetchall()]
        cursor.execute("SELECT Simbol, DENUMIRE FROM AVACONT_COMUN.DefaCont ORDER BY Simbol")
        conturi = [{"simbol": int(r[0]), "denumire": r[1] or ""} for r in cursor.fetchall()]
        # Every concrete classification (one row of Clasificatii): what the classification combo of a line of type A offers, and what IdClsf points at.
        cursor.execute("SELECT IDClsf, Clsf, ClsfF, ClsfE, SS, Capitol, Articol, Alineat, Denumire FROM Clasificatii ORDER BY SS, Clsf")
        clsf_linii = [{"idclsf": int(r[0]), "clsf": r[1], "clsff": r[2] or "", "clsfe": r[3] or "", "ss": r[4], "capitol": r[5], "articol": r[6],
                       "alineat": r[7], "denumire": r[8] or ""} for r in cursor.fetchall()]
        # The chart of accounts of the unit (every subunit), one row per account symbol and sector+source: the combos of the lines offer these, with the
        # analytic and, when the account is tied to a classification, the functional (ClsfF) and economic (ClsfE) part of it.
        cursor.execute("SELECT SursaSector, SimbolCont, Cont, Analitic, ClsfF, ClsfE, MIN(Denumire) FROM TZ_PlanCont WHERE Activ = 1 "
                       "GROUP BY SursaSector, SimbolCont, Cont, Analitic, ClsfF, ClsfE ORDER BY SursaSector, SimbolCont")
        conturi_plan = [{"ss": r[0], "simbol": r[1], "cont": int(r[2]), "analitic": r[3] or "", "clsff": r[4] or "", "clsfe": r[5] or "",
                         "denumire": r[6] or ""} for r in cursor.fetchall()]
        cursor.execute("SELECT IdPartener, CodPartener, DenumirePartener FROM Parteneri ORDER BY CodPartener")
        parteneri = [{"idpartener": int(r[0]), "cod": r[1], "denumire": r[2] or ""} for r in cursor.fetchall()]
        # The classifications the tree of type M hangs the templates on: one row per key (ClsfX, subchapter written XX.XX) and sector+source, with its forms.
        cursor.execute("SELECT ClsfX, SS, Subcapitol FROM Clasificatii ORDER BY ClsfX, SS, Subcapitol")
        clasificatii = {}
        for r in cursor.fetchall():
            intrare = clasificatii.setdefault((r[0], r[1]), {"cheie": r[0], "ss": r[1], "forme": []})
            if r[2] and r[2] not in intrare["forme"]:
                intrare["forme"].append(r[2])
        cursor.execute("SELECT ClsfE, Denumire FROM AVACONT_COMUN.DefaClsfE ORDER BY ClsfE")
        nume_clsfe = [{"cod": r[0], "denumire": (r[1] or "").strip()} for r in cursor.fetchall()]
        logger.info("[avacont.sablon_operatii] %s: %s sabloane", db_name, len(sabloane))
        return json_utf8({"sabloane": list(sabloane.values()), "jurnale": jurnale, "conturi": conturi, "conturi_plan": conturi_plan,
                          "clsf": clsf_linii, "parteneri": parteneri, "clasificatii": list(clasificatii.values()), "nume_clsfe": nume_clsfe}, 200)
    except Exception as e:
        logger.error("[avacont.sablon_operatii] citire %s: %s", db_name, e, exc_info=True)
        return json_utf8({"error": f"Eroare la citirea șabloanelor de operații: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()


def _intreg(valoare):
    """int(valoare) or None when it is missing; ValueError when it is not a number."""
    if valoare is None or valoare == "":
        return None
    return int(valoare)


def _valideaza_linie(sablon: dict, nr: int, brut):
    """(line, error): one line checked against its header. `nr` is the 1-based position, for the message."""
    nume = f"Șablonul «{sablon['explicatie']}», linia {nr}"
    if not isinstance(brut, dict):
        return None, f"{nume}: date nevalide."
    try:
        idlinie = _intreg(brut.get("idlinie"))
        cont_debit = _intreg(brut.get("contdebit"))
        cont_credit = _intreg(brut.get("contcredit"))
        id_part_debit = _intreg(brut.get("idpartdebit"))
        id_part_credit = _intreg(brut.get("idpartcredit"))
        id_clsf = _intreg(brut.get("idclsf"))
    except (TypeError, ValueError):
        return None, f"{nume}: număr nevalid."
    forma = str(brut.get("forma") or "").strip()
    if forma and not _FORMA.match(forma):
        return None, f"{nume}: forma trebuie să fie de felul 04.02."
    if cont_debit is None or cont_credit is None:
        return None, f"{nume}: alegeți contul debitor și contul creditor."
    mod_debit = str(brut.get("partdebitmod") or "NIMIC").strip().upper()
    mod_credit = str(brut.get("partcreditmod") or "NIMIC").strip().upper()
    if mod_debit not in _MODURI or mod_credit not in _MODURI:
        return None, f"{nume}: partenerul trebuie să fie «fără», «al operației» sau «fix»."
    if mod_debit == "FIX" and id_part_debit is None:
        return None, f"{nume}: alegeți partenerul fix de la debit."
    if mod_credit == "FIX" and id_part_credit is None:
        return None, f"{nume}: alegeți partenerul fix de la credit."
    if mod_debit != "FIX":
        id_part_debit = None
    if mod_credit != "FIX":
        id_part_credit = None
    if sablon["tip"] == "M" and (mod_debit != "NIMIC") == (mod_credit != "NIMIC"):
        return None, (f"{nume}: o operație cu clasificație are partener pe o singură parte "
                      "(debit sau credit), nu pe amândouă și nu pe niciuna.")
    analitic_debit = str(brut.get("analiticdebit") or "").strip().upper()
    analitic_credit = str(brut.get("analiticcredit") or "").strip().upper()
    if (analitic_debit and not _ANALITIC.match(analitic_debit)) or (analitic_credit and not _ANALITIC.match(analitic_credit)):
        return None, f"{nume}: analiticul poate avea doar cifre, litere mari și _ (cel mult 10 caractere)."
    # The classification of the line is sent by type A only; type M gets it from the server (key of the template + form + journal).
    id_clsf = id_clsf if sablon["tip"] == "A" else None
    pa = bool(brut.get("pa"))
    ordonantare = bool(brut.get("ordonantare"))
    chelt = bool(brut.get("chelt"))
    if (pa or ordonantare or chelt) and sablon["tip"] == "A" and id_clsf is None:
        return None, f"{nume}: bifele O, P/A și C cer o clasificație pe linie."
    if (pa or ordonantare or chelt) and sablon["tip"] not in ("M", "A"):
        return None, f"{nume}: bifele PA, Ordonanțare și Cheltuială cer o clasificație."
    if sablon["tip"] == "M":
        if pa and sablon["fel"] != "E":
            return None, f"{nume}: PA se bifează doar la evidențiere."
        if ordonantare and sablon["fel"] != "P":
            return None, f"{nume}: Ordonanțare se bifează doar la plată."
    return {"idlinie": idlinie, "forma": forma or None, "contdebit": cont_debit, "contcredit": cont_credit,
            "partdebitmod": mod_debit, "idpartdebit": id_part_debit, "partcreditmod": mod_credit, "idpartcredit": id_part_credit,
            "pa": pa, "ordonantare": ordonantare, "chelt": chelt,
            "idclsf": id_clsf, "analiticdebit": analitic_debit or None, "analiticcredit": analitic_credit or None}, None


def _valideaza_sablon(brut, nr: int):
    """(template, error): one template (header + lines) checked. `nr` is the 1-based position, for the message."""
    if not isinstance(brut, dict):
        return None, f"Șablonul {nr}: date nevalide."
    explicatie = str(brut.get("explicatie") or "").strip()
    if not explicatie:
        return None, f"Șablonul {nr}: completați explicația."
    if len(explicatie) > 255:
        return None, f"Șablonul «{explicatie[:40]}…»: explicația este prea lungă."
    try:
        idsablon = _intreg(brut.get("idsablon"))
        idjurnal = _intreg(brut.get("idjurnal"))
    except (TypeError, ValueError):
        return None, f"Șablonul «{explicatie}»: număr nevalid."
    if idjurnal is None:
        return None, f"Șablonul «{explicatie}»: alegeți jurnalul."
    tip = str(brut.get("tip") or "").strip().upper()
    fel = str(brut.get("fel") or "").strip().upper()
    if tip not in ("M", "A"):
        return None, f"Șablonul «{explicatie}»: tipul trebuie să fie M sau A."
    if fel not in ("E", "P", "A"):
        return None, f"Șablonul «{explicatie}»: alegeți felul (evidențiere, plată sau alte operații)."
    compartiment = str(brut.get("compartiment") or "").strip()
    felop = str(brut.get("felop") or "").strip()
    cheie = str(brut.get("cheie") or "").strip().upper()
    if len(compartiment) > 50 or len(felop) > 50:
        return None, f"Șablonul «{explicatie}»: compartimentul și felul OP au cel mult 50 de caractere."
    if felop and fel != "P":
        return None, f"Șablonul «{explicatie}»: felul OP se completează doar la plată."
    if cheie and not _CHEIE.match(cheie):
        return None, f"Șablonul «{explicatie}»: cheia clasificației poate avea doar cifre, X și puncte (de ex. 65.01.XX.XX.57.02.02)."
    if tip == "M" and not cheie:
        return None, f"Șablonul «{explicatie}»: o operație cu clasificație (tip M) cere cheia clasificației."
    sablon = {"idsablon": idsablon, "idjurnal": idjurnal, "tip": tip, "fel": fel, "explicatie": explicatie,
              "compartiment": compartiment or None, "felop": felop or None, "cheie": cheie or None, "linii": []}
    linii_brut = brut.get("linii")
    if not isinstance(linii_brut, list) or not linii_brut:
        return None, f"Șablonul «{explicatie}»: adăugați cel puțin o linie."
    formele = set()
    for i, lb in enumerate(linii_brut, start=1):
        linie, eroare = _valideaza_linie(sablon, i, lb)
        if eroare:
            return None, eroare
        if linie["forma"] is not None:
            if linie["forma"] in formele:
                return None, f"Șablonul «{explicatie}», linia {i}: forma {linie['forma']} apare de două ori."
            formele.add(linie["forma"])
        sablon["linii"].append(linie)
    return sablon, None


def _valideaza(corp: dict):
    """(sabloane, sterse, error): the body checked; error is the Romanian message of the first problem."""
    sabloane_brut = corp.get("sabloane")
    sterse_brut = corp.get("sterse", [])
    if not isinstance(sabloane_brut, list) or not isinstance(sterse_brut, list):
        return None, None, "Lista de șabloane lipsește."
    sterse = []
    for s in sterse_brut:
        try:
            sterse.append(int(s))
        except (TypeError, ValueError):
            return None, None, "Un șablon de șters nu este valid."
    sabloane = []
    for i, brut in enumerate(sabloane_brut, start=1):
        sablon, eroare = _valideaza_sablon(brut, i)
        if eroare:
            return None, None, eroare
        sabloane.append(sablon)
    return sabloane, sterse, None


def _verifica_referinte(cursor, sabloane):
    """The error message for the first journal / account / partner a template points at that does not exist; None when all exist."""
    cursor.execute("SELECT IdJurnal FROM TZ_Jurnale")
    jurnale = {int(r[0]) for r in cursor.fetchall()}
    cursor.execute("SELECT Simbol FROM AVACONT_COMUN.DefaCont")
    conturi = {int(r[0]) for r in cursor.fetchall()}
    cursor.execute("SELECT IdPartener FROM Parteneri")
    parteneri = {int(r[0]) for r in cursor.fetchall()}
    cursor.execute("SELECT IDClsf FROM Clasificatii")
    clasificatii = {int(r[0]) for r in cursor.fetchall()}
    cursor.execute("SELECT DISTINCT Cont, Analitic FROM TZ_PlanCont WHERE Analitic IS NOT NULL AND Analitic <> ''")
    analitice = {(int(r[0]), r[1]) for r in cursor.fetchall()}
    for s in sabloane:
        if s["idjurnal"] not in jurnale:
            return f"Șablonul «{s['explicatie']}»: jurnalul ales nu există."
        for l in s["linii"]:
            if l["idclsf"] is not None and l["idclsf"] not in clasificatii:
                return f"Șablonul «{s['explicatie']}»: o clasificație aleasă nu mai există."
            for cont, analitic in ((l["contdebit"], l["analiticdebit"]), (l["contcredit"], l["analiticcredit"])):
                if cont not in conturi:
                    return f"Șablonul «{s['explicatie']}»: contul {cont} nu există în nomenclator."
                if analitic is not None and (cont, analitic) not in analitice:
                    return f"Șablonul «{s['explicatie']}»: contul {cont}.{analitic} nu există în planul de conturi."
            for part in (l["idpartdebit"], l["idpartcredit"]):
                if part is not None and part not in parteneri:
                    return f"Șablonul «{s['explicatie']}»: un partener fix ales nu mai există."
    return None


@avacont_bp.route("/api/avacont/sabloane-operatii", methods=["POST"])
@require_session
def post_sabloane_operatii():
    """Writes the templates the window changed."""
    corp = request.get_json(silent=True)
    if not isinstance(corp, dict):
        return json_utf8({"error": "Corp JSON lipsă sau nevalid."}, 400)
    sabloane, sterse, eroare = _valideaza(corp)
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
        eroare = _verifica_referinte(cursor, sabloane)
        if eroare:
            rollback(conn)
            return json_utf8({"error": eroare}, 400)
        for ids in sterse:
            cursor.execute("SELECT Explicatie FROM TZ_SablonOperatii WHERE IdSablon = %s", (ids,))
            gasit = cursor.fetchone()
            if not gasit:
                continue
            try:
                cursor.execute("DELETE FROM TZ_SablonOperatii WHERE IdSablon = %s", (ids,))
            except mysql.connector.Error as e:
                if e.errno == errorcode.ER_ROW_IS_REFERENCED_2:
                    rollback(conn)
                    return json_utf8({"error": f"Șablonul «{gasit[0]}» este folosit de operații și nu se poate șterge."}, 409)
                raise
        for s in sabloane:
            if s["idsablon"] is None:
                cursor.execute(
                    "INSERT INTO TZ_SablonOperatii (IdJurnal, Tip, Fel, Explicatie, Compartiment, FelOP, Cheie) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (s["idjurnal"], s["tip"], s["fel"], s["explicatie"], s["compartiment"], s["felop"], s["cheie"]))
                s["idsablon"] = int(cursor.lastrowid)
            else:
                cursor.execute(
                    "UPDATE TZ_SablonOperatii SET IdJurnal = %s, Tip = %s, Fel = %s, Explicatie = %s, Compartiment = %s, FelOP = %s, Cheie = %s "
                    "WHERE IdSablon = %s",
                    (s["idjurnal"], s["tip"], s["fel"], s["explicatie"], s["compartiment"], s["felop"], s["cheie"], s["idsablon"]))
                if cursor.rowcount == 0:
                    cursor.execute("SELECT 1 FROM TZ_SablonOperatii WHERE IdSablon = %s", (s["idsablon"],))
                    if not cursor.fetchone():
                        rollback(conn)
                        return json_utf8({"error": f"Șablonul «{s['explicatie']}» nu mai există (l-a șters altcineva)."}, 404)
                cursor.execute("DELETE FROM TZ_SablonOperatiiLinie WHERE IdSablon = %s", (s["idsablon"],))
            for l in s["linii"]:
                if s["tip"] == "M" and s["cheie"] and l["forma"]:
                    # Type M: the classification of the line is the key of the template with the form of the line, in the sector+source of the journal.
                    cursor.execute("SELECT IDClsf FROM Clasificatii WHERE ClsfX = %s AND Subcapitol = %s "
                                   "AND SS = (SELECT SursaSector FROM TZ_Jurnale WHERE IdJurnal = %s) ORDER BY IDClsf LIMIT 1",
                                   (s["cheie"], l["forma"], s["idjurnal"]))
                    gasit = cursor.fetchone()
                    l["idclsf"] = int(gasit[0]) if gasit else None
                cursor.execute(
                    "INSERT INTO TZ_SablonOperatiiLinie (IdSablon, Forma, ContDebit, ContCredit, PartDebitMod, IdPartDebit, PartCreditMod, IdPartCredit, "
                    "PA, Ordonantare, Chelt, IdClsf, AnaliticDebit, AnaliticCredit) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (s["idsablon"], l["forma"], l["contdebit"], l["contcredit"], l["partdebitmod"], l["idpartdebit"], l["partcreditmod"],
                     l["idpartcredit"], int(l["pa"]), int(l["ordonantare"]), int(l["chelt"]), l["idclsf"], l["analiticdebit"], l["analiticcredit"]))
        conn.commit()
        logger.info("[avacont.sablon_operatii] %s: %s sabloane salvate, %s sterse", db_name, len(sabloane), len(sterse))
        return json_utf8({"sabloane": len(sabloane)}, 200)
    except mysql.connector.Error as e:
        rollback(conn)
        logger.error("[avacont.sablon_operatii] salvare %s: %s", db_name, e, exc_info=True)
        return json_utf8({"error": f"Eroare la salvarea șabloanelor de operații: {e}"}, 500)
    except Exception as e:
        rollback(conn)
        logger.error("[avacont.sablon_operatii] salvare %s: %s", db_name, e, exc_info=True)
        return json_utf8({"error": f"Eroare la salvarea șabloanelor de operații: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()
