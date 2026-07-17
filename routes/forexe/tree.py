# routes/forexe/tree.py
"""
Ruta arborelui de angajamente pentru MainForm (felia 0008).

Contract (GET /api/forexe/tree?ss=<SS>&an=<AN>&include_hidden=0|1):
    { "db_name": "<unit db>", "count": N, "rows": [ {...}, ... ] }

Scope: baza conectata ESTE unitatea (in MariaDB o baza = o unitate). Nu exista
parametru id_unitate si nici filtru DC — db_name vine din sesiune (g.session.db_name),
nu din cerere, deci un token nu poate tinti alta baza decat cea pe care s-a logat.

Sursa (citita verbatim din FX_System_Export/QUERIES/ la 2026-07-15) — endpoint-ul
compune DOUA query-uri Access, nu una:
  - row-source : qFX_MAIN_TREE_DESCRIERE (populeaza NODURILE arborelui prin
                 mdl_FX_PopulareTree.Angajamente_SQL) -> coloanele de afisare;
  - flags      : qFX_MAIN_TREE (sursa rcAngInd in frmFX_MAIN.RefreshTreeQuery)
                 -> cele noua Are*.

Traducere Access -> MariaDB:
  CBool((SELECT Count(*) ...)>0)  -> EXISTS (SELECT 1 ...)   [best practice MariaDB]
  ConcatRelated('SS','FX_Indicatori',...) -> GROUP_CONCAT(DISTINCT i.SS SEPARATOR ';')
  Nz([FX_DDF]![IDDF],0)<>0        -> IDDF IS NOT NULL (IDDF e coloana pe FX_Angajamente)

Costul nu conteaza: arborele are cel mult cateva sute de randuri, deci noua EXISTS
corelate per rand sunt acceptabile in schimbul claritatii.
"""
import logging

from flask import request, jsonify, g, current_app
import json

from routes.auth.guard import require_session    # bearer opac (Felia 1 auth)
from utils.database import get_db_connection     # R5 verificat: database.py:9

from . import forexe_bp

logger = logging.getLogger(__name__)

# Cele noua flag-uri, fiecare un EXISTS corelat — deci NICIUN join spre FX_DDF.
# Asta nu e doar stil: un LEFT JOIN FX_DDF ar dubla randul angajamentului daca
# vreodata apar doua DDF-uri pe acelasi CodAngajament (planul spune 1-la-cel-mult-1,
# dar datele nu au constrangere care sa o impuna), iar un GROUP BY care contine
# d.IDDF NU ar repara dublajul. Fara join, „un rand per CodAngajament” e garantat
# de PRIMARY KEY, fara GROUP BY deloc.
#   AreDDF      : IDDF e coloana PE FX_Angajamente -> IS NOT NULL e raspunsul complet.
#   ArePartener : FX_DDF.PartAng = 1; fara DDF nu exista rand -> EXISTS fals.
#   AreOrd      : lantul angajament -> DDF -> ORD (FX_ORD.IDDF = FX_DDF.IDDF).
_SELECT = (
    "SELECT a.CodAngajament, a.IDDF, a.Descriere, a.Stare, a.DataCreare, "
    "a.DataDefinitivare, a.Incarcat, a.Preluat, a.Salarii, a.ASCUNS, "
    "(SELECT GROUP_CONCAT(DISTINCT i.SS ORDER BY i.SS SEPARATOR ';') "
    " FROM FX_Indicatori i WHERE i.CodAngajament = a.CodAngajament) AS Surse, "
    "EXISTS (SELECT 1 FROM FX_Indicatori i WHERE i.CodAngajament = a.CodAngajament) AS AreIndicatori, "
    "EXISTS (SELECT 1 FROM FX_Istoric x WHERE x.CodAngajament = a.CodAngajament) AS AreIstoric, "
    "EXISTS (SELECT 1 FROM FX_DDF_REV_SA r WHERE r.CodAngajament = a.CodAngajament) AS AreRevizii, "
    "EXISTS (SELECT 1 FROM FX_Rezervari z WHERE z.CodAngajament = a.CodAngajament) AS AreRezervari, "
    "EXISTS (SELECT 1 FROM FX_Receptii_H h WHERE h.CodAngajament = a.CodAngajament) AS AreReceptii, "
    "EXISTS (SELECT 1 FROM FX_Plati p WHERE p.CodAngajament = a.CodAngajament) AS ArePlati, "
    "(a.IDDF IS NOT NULL) AS AreDDF, "
    "EXISTS (SELECT 1 FROM FX_DDF d "
    "        WHERE d.CodAngajament = a.CodAngajament AND d.PartAng = 1) AS ArePartener, "
    "EXISTS (SELECT 1 FROM FX_DDF d JOIN FX_ORD o ON o.IDDF = d.IDDF "
    "        WHERE d.CodAngajament = a.CodAngajament) AS AreOrd "
    "FROM FX_Angajamente a "
)

# WHERE-ul (PLAN_TreeDataApi.md):
#   An     : randurile cu DataCreare NULL se arata INTOTDEAUNA — nu sunt inca
#            descarcate din FOREXE, deci nu au an.
#   SS     : ingusteaza CARE angajamente apar; Surse ramane lista completa (afisare).
#   Ascuns : implicit exclude ASCUNS<>0; include_hidden=1 le readuce (btnOpt).
#   Stare  : exclude Anulat/Suspendat. NU vine din qFX_MAIN_TREE (acela nu are WHERE
#            deloc) — vine din qFX_MAIN_TREE_DATA:7 si mdl_FX_PopulareTree.md:253,
#            unde conditia legacy este
#            ((InStr(1,[Stare],'Anulat')>0) Or (InStr(1,[Stare],'Suspendat')>0)
#             Or [Ascuns]) = False.
#            LIKE e case-insensitive pe colatia utf8mb4_general_ci a bazei, deci
#            oglindeste InStr() din Access fara functii suplimentare.
_WHERE = (
    "WHERE (YEAR(a.DataCreare) = %s OR a.DataCreare IS NULL) "
    "AND EXISTS (SELECT 1 FROM FX_Indicatori i "
    "            WHERE i.CodAngajament = a.CodAngajament AND i.SS = %s) "
    "AND (%s = 1 OR a.ASCUNS = 0) "
    "AND COALESCE(a.Stare, '') NOT LIKE '%%Anulat%%' "
    "AND COALESCE(a.Stare, '') NOT LIKE '%%Suspendat%%' "
)

# Fara GROUP BY: nu exista join care sa multiplice randurile (vezi nota de la _SELECT),
# iar CodAngajament e PRIMARY KEY pe FX_Angajamente -> exact un rand per angajament.
# Ordinea oglindeste qFX_MAIN_TREE_DESCRIERE ("ORDER BY O, Descriere"; O e ramura de
# orfani, care aici nu exista — nu filtram pe indicatori decat prin SS).
_ORDER = "ORDER BY a.Descriere"

_SQL = _SELECT + _WHERE + _ORDER


def _json_utf8(payload, status):
    """Raspuns JSON cu diacritice LITERALE (ensure_ascii=False): Descriere/Stare
    contin «În derulare» si trebuie sa ajunga la client ca UTF-8 real, nu \\uXXXX."""
    body = json.dumps(payload, ensure_ascii=False)
    return current_app.response_class(body, status=status, mimetype="application/json")


@forexe_bp.route("/api/forexe/tree", methods=["GET"])
@require_session
def get_tree():
    """Arborele de angajamente al bazei conectate, filtrat pe an + SS.

    Query: an (obligatoriu, intreg), ss (obligatoriu), include_hidden (0/1, implicit 0).
    Returneaza { db_name, count, rows: [ {CodAngajament, IDDF, Descriere, Stare,
    DataCreare, DataDefinitivare, Incarcat, Preluat, Salarii, Ascuns, Surse,
    AreIndicatori, AreIstoric, AreRevizii, AreRezervari, AreReceptii, ArePlati,
    AreDDF, ArePartener, AreOrd}, ... ] }.
    """
    an_raw = request.args.get("an")
    if an_raw is None or str(an_raw).strip() == "":
        return _json_utf8({"error": "Parametru lipsă: an"}, 400)
    try:
        an = int(an_raw)
    except (TypeError, ValueError):
        return _json_utf8({"error": "an invalid (așteptat întreg)"}, 400)

    ss = request.args.get("ss")
    if ss is None or str(ss).strip() == "":
        return _json_utf8({"error": "Parametru lipsă: ss"}, 400)
    ss = str(ss).strip()

    include_hidden = 1 if str(request.args.get("include_hidden", "0")).strip() == "1" else 0

    # Scope: baza sesiunii, niciodata din cerere (vezi nota din capul fisierului).
    db_name = g.session.db_name

    conn = None
    try:
        conn = get_db_connection(db_name)
        cursor = conn.cursor()
        cursor.execute(_SQL, (an, ss, include_hidden))
        rows = []
        for (cod, iddf, descriere, stare, data_creare, data_def, incarcat, preluat,
             salarii, ascuns, surse, are_indicatori, are_istoric, are_revizii,
             are_rezervari, are_receptii, are_plati, are_ddf, are_partener,
             are_ord) in cursor.fetchall():
            rows.append({
                "CodAngajament": cod,
                "IDDF": iddf,
                "Descriere": descriere,
                "Stare": stare,
                "DataCreare": data_creare.isoformat() if data_creare is not None else None,
                "DataDefinitivare": data_def.isoformat() if data_def is not None else None,
                "Incarcat": bool(incarcat),
                "Preluat": bool(preluat),
                "Salarii": bool(salarii),
                "Ascuns": bool(ascuns),
                "Surse": surse,
                "AreIndicatori": bool(are_indicatori),
                "AreIstoric": bool(are_istoric),
                "AreRevizii": bool(are_revizii),
                "AreRezervari": bool(are_rezervari),
                "AreReceptii": bool(are_receptii),
                "ArePlati": bool(are_plati),
                "AreDDF": bool(are_ddf),
                "ArePartener": bool(are_partener),
                "AreOrd": bool(are_ord),
            })
        logger.info("[forexe.tree] %s: an=%s ss=%s include_hidden=%s -> %s randuri",
                    db_name, an, ss, include_hidden, len(rows))
        return _json_utf8({"db_name": db_name, "count": len(rows), "rows": rows}, 200)
    except Exception as e:
        # Fara inghitire: o eroare de baza intoarce motivul, NU un arbore gol —
        # un arbore gol ar minti operatorul ca unitatea nu are angajamente.
        logger.error(f"[forexe.tree] {e}", exc_info=True)
        return _json_utf8({"error": f"Eroare la citirea arborelui: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()
