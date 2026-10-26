# routes/avacont/conturi.py
"""
The chart of accounts of a subunit (slice AV01-04, table TZ_PlanCont, sql/AVACONT/Configurari/PlanCont/AV01_04_conturi_sursa.sql).

One row = one full account. The base account is a code of AVACONT_COMUN.DefaCont (7 digits);
the row adds, when the base account asks for them, the functional (ClsfF) and economic (ClsfE)
classification, and an optional analytic. The symbol of the account is written HERE, never by the client:

    SimbolCont = Cont + SursaSector of the ACCOUNT + [ClsfF] + [ClsfE] + [ "." + Analitic ]
    e.g. 1170000 + 01A  ->  117000001A        (checked on the Access plan, baza2026.accdb)

The sector+source belongs to the account: the client sends the one chosen in the title bar, or typed by the operator
when the title bar is on «Toate» (SLICE-0097-06). Not sent = the default of the subunit.

    GET    /api/avacont/conturi/nomenclator
        -> 200 { "clase": [ { "clasa", "denumire" } ],
                 "conturi": [ { "simbol", "denumire", "tip", "clsff", "clsfe" } ],
                 "clsff": [ { "cod", "denumire" } ], "clsfe": [ { "cod", "denumire" } ],
                 "surse": [ { "cod", "denumire" } ] }
        "surse" = every sector+source (DefaSursaSector), for the subunit editor. What the «add account» editor offers. `clsff` / `clsfe` on a base account = 1 when it must be
        split by that classification (checked on the dump: 528 none, 131 ClsfF only, 159 both, 0 ClsfE only).

    GET    /api/avacont/conturi?subunitate=<id>
        -> 200 { "subunitate": { ...as /subunitati... } | null,
                 "conturi": [ { "idcont", "cont", "sursasector", "analitic", "clsff", "clsfe", "simbol", "denumire",
                                "fel", "extra", "activ" } ] }
        No `subunitate` = the default subunit. A unit with no subunit answers { "subunitate": null, "conturi": [] }.

    POST   /api/avacont/conturi
        { "idcont": null | n, "idsubunitate", "cont", "sursasector", "analitic", "clsff", "clsfe", "denumire", "fel",
          "extra", "activ", "confirmat_analitic": bool }
        -> 200 { "idcont": n, "simbol": "..." }.  400 for an invalid field; 404 for an unknown id;
        409 "The account already exists"; 409 with reason CONFIRMARE_ANALITIC when an analytic is put on a
        401* / class 6 / class 7 account and the body does not carry `confirmat_analitic` (the client asks
        and sends it again). Changing an existing account's base account, classification or analytic is
        refused once it is used (the symbol would change under the operations that carry it).

    DELETE /api/avacont/conturi/<idcont>
        -> 200 { "idcont": n }.  409 when the account is used anywhere (operations, templates, opening
        balances): BLOCKED, whatever the user, with a message saying where. Never a cascade.
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

logger = logging.getLogger(__name__)

REASON_CONFIRMARE_ANALITIC = "CONFIRMARE_ANALITIC"

_RE_ANALITIC = re.compile(r"^[0-9A-Z_]{1,10}$")
_DENUMIRE_MAX = 255

# Where an account can be used: (table, column that holds IdCont, what the operator calls it).
# Later stages APPEND here (note lines, templates). The foreign keys are RESTRICT, so a place missing from this
# list still blocks the delete (the generic message below); the list only gives the clearer one.
_FOLOSIRI = (
    ("TZ_SoldInitial", "IdCont", "solduri inițiale"),
)

_SQL_SUBUNITATE = (
    "SELECT S.IdSubunitate, S.Denumire, S.Capitol, S.SursaSector, S.ZecimaleSalarii, S.Implicita, S.Activ, "
    "       (SELECT COUNT(*) FROM TZ_PlanCont P WHERE P.IdSubunitate = S.IdSubunitate) "
    "  FROM TZ_Subunitati S WHERE {conditie} LIMIT 1"
)


def _subunitate_json(r):
    return {"idsubunitate": int(r[0]), "denumire": r[1] or "", "capitol": r[2] or "", "sursasector": r[3] or "",
            "zecimale": int(r[4] or 0), "implicita": bool(r[5]), "activ": bool(r[6]), "conturi": int(r[7] or 0)}


def compune_simbol(cont: int, sursasector: str, clsff: str, clsfe: str, analitic: str) -> str:
    """The symbol of an account: base + sector/source + [ClsfF] + [ClsfE] + [.analitic]."""
    simbol = f"{cont}{sursasector}{clsff or ''}{clsfe or ''}"
    if analitic:
        simbol += "." + analitic
    return simbol


@avacont_bp.route("/api/avacont/conturi/nomenclator", methods=["GET"])
@require_session
def get_nomenclator_conturi():
    """The base accounts, the classes and the classifications the account editor offers."""
    db_name = g.session.db_name
    conn = None
    try:
        conn = get_kbot_connection(db_name)
        cursor = conn.cursor()
        cursor.execute("SELECT Clasa, Denumire FROM AVACONT_COMUN.DefaClase ORDER BY Clasa")
        clase = [{"clasa": int(r[0]), "denumire": r[1] or ""} for r in cursor.fetchall()]
        cursor.execute("SELECT Simbol, DENUMIRE, TIP, COALESCE(CLSFF, 0), COALESCE(CLSFE, 0) "
                       "  FROM AVACONT_COMUN.DefaCont ORDER BY Simbol")
        conturi = [{"simbol": int(r[0]), "denumire": r[1] or "", "tip": r[2] or "",
                    "clsff": int(r[3]), "clsfe": int(r[4])} for r in cursor.fetchall()]
        cursor.execute("SELECT ClsfF, Denumire FROM AVACONT_COMUN.DefaClsfF ORDER BY ClsfF")
        clsff = [{"cod": r[0], "denumire": r[1] or ""} for r in cursor.fetchall()]
        cursor.execute("SELECT ClsfE, Denumire FROM AVACONT_COMUN.DefaClsfE ORDER BY ClsfE")
        clsfe = [{"cod": r[0], "denumire": r[1] or ""} for r in cursor.fetchall()]
        cursor.execute("SELECT SursaSector, Denumire FROM AVACONT_COMUN.DefaSursaSector ORDER BY SursaSector")
        surse = [{"cod": r[0], "denumire": r[1] or ""} for r in cursor.fetchall()]
        logger.info("[avacont.conturi] nomenclator %s: %s conturi, %s ClsfF, %s ClsfE", db_name,
                    len(conturi), len(clsff), len(clsfe))
        return json_utf8({"clase": clase, "conturi": conturi, "clsff": clsff, "clsfe": clsfe, "surse": surse}, 200)
    except Exception as e:
        logger.error("[avacont.conturi] nomenclator %s: %s", db_name, e, exc_info=True)
        return json_utf8({"error": f"Eroare la citirea nomenclatorului de conturi: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()


@avacont_bp.route("/api/avacont/conturi", methods=["GET"])
@require_session
def get_conturi():
    """The chart of accounts of one subunit (the default one when none is asked for)."""
    db_name = g.session.db_name
    conn = None
    try:
        conn = get_kbot_connection(db_name)
        cursor = conn.cursor()
        cerut = request.args.get("subunitate", type=int)
        if cerut is not None:
            cursor.execute(_SQL_SUBUNITATE.format(conditie="S.IdSubunitate = %s"), (cerut,))
        else:
            cursor.execute(_SQL_SUBUNITATE.format(conditie="S.Implicita = 1"))
        sub = cursor.fetchone()
        if not sub:
            return json_utf8({"subunitate": None, "conturi": []}, 200)
        cursor.execute(
            "SELECT IdCont, Cont, Analitic, ClsfF, ClsfE, SimbolCont, Denumire, FelCont, Extra, Activ, SursaSector "
            "  FROM TZ_PlanCont WHERE IdSubunitate = %s ORDER BY SimbolCont", (int(sub[0]),))
        conturi = [{"idcont": int(r[0]), "cont": int(r[1]), "analitic": r[2] or "", "clsff": r[3] or "",
                    "clsfe": r[4] or "", "simbol": r[5] or "", "denumire": r[6] or "", "fel": r[7] or "",
                    "extra": bool(r[8]), "activ": bool(r[9]), "sursasector": r[10] or ""}
                   for r in cursor.fetchall()]
        logger.info("[avacont.conturi] %s: subunitatea %s, %s conturi", db_name, sub[0], len(conturi))
        return json_utf8({"subunitate": _subunitate_json(sub), "conturi": conturi}, 200)
    except Exception as e:
        logger.error("[avacont.conturi] citire %s: %s", db_name, e, exc_info=True)
        return json_utf8({"error": f"Eroare la citirea planului de conturi: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()


def _valideaza(corp: dict):
    """(values, error): the cleaned body of an account save, or the Romanian reason it is refused."""
    try:
        idsub = int(corp.get("idsubunitate"))
        cont = int(corp.get("cont"))
    except (TypeError, ValueError):
        return None, "Alegeți subunitatea și contul."
    denumire = text(corp, "denumire")
    if not denumire:
        return None, "Introduceți denumirea contului."
    if len(denumire) > _DENUMIRE_MAX:
        return None, f"Denumirea contului are cel mult {_DENUMIRE_MAX} de caractere."
    analitic = text(corp, "analitic").upper()
    if analitic and not _RE_ANALITIC.match(analitic):
        return None, "Analiticul poate conține doar cifre, litere mari și «_» (cel mult 10 caractere)."
    fel = text(corp, "fel").upper()
    if fel and fel not in ("A", "B", "P"):
        return None, "Felul contului este A (activ), B (bifuncțional) sau P (pasiv)."
    idcont = corp.get("idcont")
    if idcont is not None:
        try:
            idcont = int(idcont)
        except (TypeError, ValueError):
            return None, "Numărul contului nu este valid."
    return {"idcont": idcont, "idsubunitate": idsub, "cont": cont, "sursasector": text(corp, "sursasector").upper(),
            "analitic": analitic,
            "clsff": text(corp, "clsff"), "clsfe": text(corp, "clsfe"), "denumire": denumire, "fel": fel,
            "extra": flag(corp, "extra"), "activ": flag(corp, "activ", True),
            "confirmat_analitic": flag(corp, "confirmat_analitic")}, None


def _folosit(cursor, idcont: int):
    """The first place that uses the account as (what the operator calls it, how many rows), or None."""
    for tabel, coloana, nume in _FOLOSIRI:
        cursor.execute(f"SELECT COUNT(*) FROM {tabel} WHERE {coloana} = %s", (idcont,))
        nr = int(cursor.fetchone()[0])
        if nr > 0:
            return nume, nr
    return None


@avacont_bp.route("/api/avacont/conturi", methods=["POST"])
@require_session
def post_cont():
    """Saves one account of a subunit's chart (new or existing)."""
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

        cursor.execute("SELECT SursaSector FROM TZ_Subunitati WHERE IdSubunitate = %s", (v["idsubunitate"],))
        sub = cursor.fetchone()
        if not sub:
            conn.rollback()
            return json_utf8({"error": "Subunitatea nu mai există."}, 404)

        cursor.execute("SELECT TIP, COALESCE(CLSFF, 0), COALESCE(CLSFE, 0) FROM AVACONT_COMUN.DefaCont "
                       "WHERE Simbol = %s", (v["cont"],))
        baza = cursor.fetchone()
        if not baza:
            conn.rollback()
            return json_utf8({"error": f"Contul {v['cont']} nu există în nomenclator."}, 400)
        cere_f, cere_e = bool(baza[1]), bool(baza[2])

        # The base account says whether a classification MAY be given: not asked -> refused (the symbol would not mean
        # the same thing for two accounts of the same base); asked -> optional, the client only suggests one.
        # ClsfE never without ClsfF (Access rule; the table has the same CHECK).
        if not cere_f and v["clsff"]:
            conn.rollback()
            return json_utf8({"error": "Contul nu se împarte pe clasificație funcțională: lăsați ClsfF gol."}, 400)
        if not cere_e and v["clsfe"]:
            conn.rollback()
            return json_utf8({"error": "Contul nu se împarte pe clasificație economică: lăsați ClsfE gol."}, 400)
        if v["clsfe"] and not v["clsff"]:
            conn.rollback()
            return json_utf8({"error": "Clasificația economică nu se poate folosi fără cea funcțională."}, 400)
        for nume, valoare, tabel, coloana in (("ClsfF", v["clsff"], "DefaClsfF", "ClsfF"),
                                              ("ClsfE", v["clsfe"], "DefaClsfE", "ClsfE")):
            if valoare:
                cursor.execute(f"SELECT 1 FROM AVACONT_COMUN.{tabel} WHERE {coloana} = %s LIMIT 1", (valoare,))
                if not cursor.fetchone():
                    conn.rollback()
                    return json_utf8({"error": f"{nume} «{valoare}» nu există în nomenclator."}, 400)

        # Analytic on a supplier account (401*) or an expense / revenue account is unusual: ask once.
        cifre = str(v["cont"])
        if v["analitic"] and not v["confirmat_analitic"] and (cifre.startswith("401") or cifre[:1] in ("6", "7")):
            conn.rollback()
            return json_utf8({"error": "Contul de furnizori (401), cheltuielile și veniturile nu se despart de "
                                       "obicei pe analitice. Continuați?",
                              "reason": REASON_CONFIRMARE_ANALITIC}, 409)

        fel = v["fel"] or (baza[0] or "")
        if fel not in ("A", "B", "P"):
            conn.rollback()
            return json_utf8({"error": "Alegeți felul contului (A, B sau P)."}, 400)
        # The sector+source of the account; not sent = the default of the subunit.
        ss = v["sursasector"] or sub[0]
        cursor.execute("SELECT 1 FROM AVACONT_COMUN.DefaSursaSector WHERE SursaSector = %s LIMIT 1", (ss,))
        if not cursor.fetchone():
            conn.rollback()
            return json_utf8({"error": f"Sectorul și sursa «{ss}» nu există."}, 400)
        simbol = compune_simbol(v["cont"], ss, v["clsff"], v["clsfe"], v["analitic"])
        idcont = v["idcont"]

        if idcont is None:
            cursor.execute(
                "INSERT INTO TZ_PlanCont (IdSubunitate, SursaSector, Cont, Analitic, ClsfF, ClsfE, SimbolCont, Denumire, "
                "FelCont, Extra, Activ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (v["idsubunitate"], ss, v["cont"], v["analitic"] or None, v["clsff"] or None, v["clsfe"] or None,
                 simbol, v["denumire"], fel, 1 if v["extra"] else 0, 1 if v["activ"] else 0))
            idcont = int(cursor.lastrowid)
        else:
            cursor.execute("SELECT IdSubunitate, SimbolCont FROM TZ_PlanCont WHERE IdCont = %s FOR UPDATE", (idcont,))
            vechi = cursor.fetchone()
            if not vechi or int(vechi[0]) != v["idsubunitate"]:
                conn.rollback()
                return json_utf8({"error": "Contul nu mai există."}, 404)
            if vechi[1] != simbol:
                folosit = _folosit(cursor, idcont)
                if folosit:
                    conn.rollback()
                    return json_utf8({"error": f"Simbolul contului nu se poate schimba: contul este folosit în "
                                               f"{folosit[0]} ({folosit[1]})."}, 409)
            cursor.execute(
                "UPDATE TZ_PlanCont SET SursaSector = %s, Cont = %s, Analitic = %s, ClsfF = %s, ClsfE = %s, "
                "SimbolCont = %s, Denumire = %s, FelCont = %s, Extra = %s, Activ = %s WHERE IdCont = %s",
                (ss, v["cont"], v["analitic"] or None, v["clsff"] or None, v["clsfe"] or None, simbol, v["denumire"],
                 fel, 1 if v["extra"] else 0, 1 if v["activ"] else 0, idcont))
        conn.commit()
        logger.info("[avacont.conturi] %s: contul %s (%s)", db_name, idcont, simbol)
        return json_utf8({"idcont": idcont, "simbol": simbol}, 200)
    except mysql.connector.Error as e:
        rollback(conn)
        if e.errno == errorcode.ER_DUP_ENTRY:
            return json_utf8({"error": "Contul există deja!"}, 409)
        logger.error("[avacont.conturi] salvare %s: %s", db_name, e, exc_info=True)
        return json_utf8({"error": f"Eroare la salvarea contului: {e}"}, 500)
    except Exception as e:
        rollback(conn)
        logger.error("[avacont.conturi] salvare %s: %s", db_name, e, exc_info=True)
        return json_utf8({"error": f"Eroare la salvarea contului: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()


@avacont_bp.route("/api/avacont/conturi/<int:idcont>", methods=["DELETE"])
@require_session
def delete_cont(idcont: int):
    """Deletes an account that is used nowhere. An account with operations is BLOCKED, whatever the user."""
    db_name = g.session.db_name
    conn = None
    try:
        conn = get_kbot_connection(db_name)
        conn.autocommit = False
        cursor = conn.cursor()
        if not conn.in_transaction:
            conn.start_transaction()
        cursor.execute("SELECT SimbolCont FROM TZ_PlanCont WHERE IdCont = %s FOR UPDATE", (idcont,))
        rand = cursor.fetchone()
        if not rand:
            conn.rollback()
            return json_utf8({"error": "Contul nu mai există."}, 404)
        folosit = _folosit(cursor, idcont)
        if folosit:
            conn.rollback()
            return json_utf8({"error": f"Contul {rand[0]} nu se poate șterge: este folosit în {folosit[0]} "
                                       f"({folosit[1]}). Dacă nu se mai lucrează cu el, marcați-l ca inactiv."}, 409)
        cursor.execute("DELETE FROM TZ_PlanCont WHERE IdCont = %s", (idcont,))
        conn.commit()
        logger.info("[avacont.conturi] %s: contul %s (%s) sters", db_name, idcont, rand[0])
        return json_utf8({"idcont": idcont}, 200)
    except mysql.connector.Error as e:
        rollback(conn)
        if e.errno == errorcode.ER_ROW_IS_REFERENCED_2:
            # A place the list above does not know yet still blocks (the foreign key is RESTRICT).
            return json_utf8({"error": "Contul nu se poate șterge: este folosit în operații sau șabloane. "
                                       "Dacă nu se mai lucrează cu el, marcați-l ca inactiv."}, 409)
        logger.error("[avacont.conturi] stergere %s: %s", db_name, e, exc_info=True)
        return json_utf8({"error": f"Eroare la ștergerea contului: {e}"}, 500)
    except Exception as e:
        rollback(conn)
        logger.error("[avacont.conturi] stergere %s: %s", db_name, e, exc_info=True)
        return json_utf8({"error": f"Eroare la ștergerea contului: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()
