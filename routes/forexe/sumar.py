# routes/forexe/sumar.py
"""
Ruta Sumar pentru frmFX_MAIN_Sumar (felia 0011).

Contract (GET /api/forexe/sumar?cod=<CodAngajament>):
    { "header": {...} | null, "rows": [ {...}, ... ] }

Scope: baza conectata ESTE unitatea (o baza MariaDB = o unitate), deci nu exista
parametru db_name / id_unitate — baza vine din sesiune (g.session.db_name), exact
ca la /api/forexe/tree. Un token nu poate tinti alta baza decat cea pe care s-a logat.

Sursa: FX_System_Export/QUERIES/qFX_MAIN_SUMAR.md (v1; qFX_MAIN_SUMAR_2 este o
varianta mai veche cu Nz()/subinterogari corelate — NU se porteaza).

Granulatia: un rand per INDICATOR al angajamentului. Coloanele de antet
(CodAngajament .. Preluat) se repeta identic pe fiecare rand in Access; aici sunt
ridicate o singura data in "header", ca sa nu calatoreasca duplicate pe fir.

Traducere Access -> MariaDB (deciziile operatorului, felia 0011):
  ClasificatiiG -> Clasificatii, ParteneriG -> Parteneri (nu exista tabele „G”).
  TotalReceptii = SUM(FX_Receptii.DIF) — DELTA, nu Valoare. Portat fidel.
  Fara filtru SS: sumarul arata TOTI indicatorii angajamentului.
  Un indicator FARA clasificatie trebuie sa APARA in grila, cu Clsf gol — altfel
  dispare din sumar fara urma (de aceea nu se face INNER JOIN, ca in Access).

IdUnitate — CORECTIE 0011-03, regula initiala era prea larga:
  „Se elimina predicatele de join pe IdUnitate (o baza = o unitate)” se aplica DOAR
  tabelelor FX_. NOMENCLATOARELE PARTAJATE (Clasificatii, Parteneri, s.a.) sunt
  stocate in baza per-unitate pentru MAI MULTE unitati — pe 000_DEMO, Clasificatii
  contine 8 (48, 75, 76, 121, 123, 135, 136, 157). Fara predicatul IdUnitate,
  join-ul spre ele face fan-out. Deci: la nomenclatoare, IdUnitate RAMANE.

Clsf / Partener — subinterogari scalare, nu join-uri (0011-03):
  Chiar cu ambele predicate corecte, Clasificatii mai are duplicate reale
  ((75,79), (75,84), (75,90), (75,92), (75,93) — fiecare de doua ori), deci un join
  ar multiplica randurile-indicator. O subinterogare scalara cu LIMIT 1 garanteaza
  UN rand per indicator indiferent de duplicate, si pastreaza „fara clasificatie
  -> gol”.
  La Partener defectul era mai grav decat afisajul: join-ul spre Parteneri statea
  INAINTEA lui GROUP BY, deci un partener duplicat multiplica SUM(SA.ValCur) si
  UMFLA TotalRevizii. Mutat si el in subinterogare scalara.

Clsf (blocantul feliei, rezolvat 2026-07-18 pe DDL-ul real, nu prin ghicit):
  RAMURA A. `Clasificatii.Clsf` EXISTA ca o coloana GENERATED ALWAYS ... STORED:
      concat_ws('.', Capitol, Subcapitol, Articol, Alineat)
  deci se selecteaza DIRECT (C.Clsf), fara compunere in SQL. Are si index
  (idx_Clsf), deci si ORDER BY C.Clsf este ieftin. Formatul rezultat
  („65.02.04.02.20.01.03”) se potriveste cu datele reale din
  FX_System_Export/TABLES/FX_Receptii.md, coloana Clsf.
  Tot din DDL: `Titlu` = left(Articol,2), ceea ce explica Mid(Clsf,13,2) din Access.

WHERE IST.Descriere = 'Angajament nou.' (cu PUNCT final — literalul Access):
  exact un rand de istoric per angajament, confirmat de operator, deci INNER JOIN-ul
  nu multiplica randurile si nu e nevoie de dedup.

Optimizari fata de transcrierea literala (Access grupa pe TOT tabelul, apoi facea
join — pe MariaDB asta inseamna un full scan per agregat per cerere):
  - filtrul `cod` este impins in FIECARE derivata (aggRez/aggRec/aggPlati/aggRev);
  - aggOrd ramane cheiat pe CodAI (asta ii da granulatia, nu se schimba), dar se
    restrange cu DOUA conditii: `T.CodAngajament = %s` (direct, coloana exista pe
    FX_ORD_TBL — vezi routes/ord/tbl.py:53) SI `T.CodAI IN (SELECT CodAI FROM
    FX_Indicatori WHERE CodAngajament = %s)`. A doua e redundanta cat timp cele doua
    coloane sunt consistente; se pastreaza deliberat ca centura de siguranta, pentru
    ca nicio constrangere din schema nu impune consistenta lor.

Devieri deliberate (mici, documentate in worklog):
  - COALESCE(...,0) pe cele cinci totaluri: Access v1 le lasa Null, dar varianta v2
    a operatorului le trece prin Nz(...,0). Grila arata «0,00», nu gol.
  - ROUND(...,2) DOAR pe TotalRevizii si TotalOrdonantari — exact ca in Access.
    Rezervari/Receptii/Plati raman SUM() simplu; formatarea N2 e treaba grilei.
  - ORDER BY adaugat (Access nu are niciunul), ca grila sa fie stabila intre refresh-uri.
"""
import json
import logging

from flask import request, g, current_app

from routes.auth.guard import require_session
from utils.database import get_db_connection

from . import forexe_bp

logger = logging.getLogger(__name__)

# ===========================================================================
# CONVENTIA CHEILOR MariaDB vs Access — citeste asta INAINTE de a porta un join.
# ===========================================================================
# Regula generala: cand un tabel are DOUA coloane cu acelasi nume de baza, cea
# specifica MariaDB este cheia primara REALA, iar cealalta pastreaza id-ul Access
# venit din import. Un port literal al join-ului din Access leaga cheia GRESITA.
#
#   familia FX_ORD : sufixul „P” = PK MariaDB      -> FX_ORD_TBL.IDORDTBLP
#                    numele fara „P” = id Access   -> FX_ORD_TBL.IDORDTBL
#   Clasificatii   : IDClsf    = PK MariaDB („PY”)
#                    IdClsfAcc = id Access
#
# ...DAR tabelele FX_ NU respecta conventia: `FX_Indicatori.IdClsf` tine ID-UL
# ACCESS, deci se potriveste cu `Clasificatii.IdClsfAcc`, NU cu `Clasificatii.IDClsf`.
# Numele coloanei minte. Verificat pe 000_DEMO (felia 0011-03):
#   FX_Indicatori                                          -> 29 randuri
#   ... WHERE IdClsf <> 0                                  -> 25
#   JOIN Clasificatii ON I.IdClsf = C.IDClsf               ->  0  (cheie gresita)
#   JOIN Clasificatii ON I.IdClsf = C.IdClsfAcc            -> 67  (fan-out unitati)
#   ... AND I.IdUnitate = C.IdUnitate                      -> 50  (fan-out duplicate)
#
# Capcana a costat TREI rulari pe felia asta: aggOrd (R.IDORDTBL -> R.IDORDTBLP),
# apoi cheia spre Clasificatii, apoi predicatul IdUnitate lipsa + duplicatele.
# Vederea ORD, care urmeaza, lucreaza numai cu familia FX_ORD.
#
# Concluzie operationala: NU deduce cheia din numele coloanei — numara randurile
# inainte si dupa join. Un join care intoarce 0 sau mai multe randuri decat tabelul
# din stanga e un defect, nu o particularitate a datelor.
_SQL = (
    "SELECT A.CodAngajament, IST.DataFX, A.DataCreare, A.DataDefinitivare, "
    "A.Descriere, A.Stare, A.Incarcat, A.Preluat, "
    "(SELECT C.Clsf FROM Clasificatii C "
    "  WHERE C.IdClsfAcc = I.IdClsf AND C.IdUnitate = I.IdUnitate "
    "  LIMIT 1) AS Clsf, "
    "I.CodIndicator, aggRev.Partener, "
    "COALESCE(aggRez.TotalRezervari, 0)    AS TotalRezervari, "
    "COALESCE(aggRec.TotalReceptii, 0)     AS TotalReceptii, "
    "COALESCE(aggPlati.TotalPlati, 0)      AS TotalPlati, "
    "COALESCE(aggRev.TotalRevizii, 0)      AS TotalRevizii, "
    "COALESCE(aggOrd.TotalOrdonantari, 0)  AS TotalOrdonantari "
    "FROM FX_Angajamente A "
    "INNER JOIN FX_Indicatori I ON A.CodAngajament = I.CodAngajament "
    "INNER JOIN FX_Istoric IST  ON A.CodAngajament = IST.CodAngajament "
    "LEFT JOIN (SELECT CodAngajament, CodIndicator, SUM(R_Valoare) AS TotalRezervari "
    "           FROM FX_Rezervari WHERE CodAngajament = %s "
    "           GROUP BY CodAngajament, CodIndicator) aggRez "
    "       ON aggRez.CodAngajament = I.CodAngajament "
    "      AND aggRez.CodIndicator  = I.CodIndicator "
    "LEFT JOIN (SELECT CodAngajament, CodIndicator, SUM(DIF) AS TotalReceptii "
    "           FROM FX_Receptii WHERE CodAngajament = %s "
    "           GROUP BY CodAngajament, CodIndicator) aggRec "
    "       ON aggRec.CodAngajament = I.CodAngajament "
    "      AND aggRec.CodIndicator  = I.CodIndicator "
    "LEFT JOIN (SELECT CodAngajament, CodIndicator, SUM(Suma) AS TotalPlati "
    "           FROM FX_Plati WHERE CodAngajament = %s "
    "           GROUP BY CodAngajament, CodIndicator) aggPlati "
    "       ON aggPlati.CodAngajament = I.CodAngajament "
    "      AND aggPlati.CodIndicator  = I.CodIndicator "
    "LEFT JOIN (SELECT SA.CodAngajament, SA.CodIndicator, "
    "                  ROUND(SUM(SA.ValCur), 2) AS TotalRevizii, "
    "                  MIN((SELECT P.DenumirePartener FROM Parteneri P "
    "                        WHERE P.CodPartener = SA.CodPartener "
    "                          AND P.IdUnitate   = SA.IdUnitate "
    "                        LIMIT 1)) AS Partener "
    "           FROM FX_DDF_REV_SA SA "
    "           WHERE SA.CodAngajament = %s "
    "           GROUP BY SA.CodAngajament, SA.CodIndicator) aggRev "
    "       ON aggRev.CodAngajament = I.CodAngajament "
    "      AND aggRev.CodIndicator  = I.CodIndicator "
    "LEFT JOIN (SELECT T.CodAI, ROUND(SUM(R.Valoare), 2) AS TotalOrdonantari "
    "           FROM FX_ORD_TBL_REC R "
    "           INNER JOIN FX_ORD_TBL T ON R.IDORDTBLP = T.IDORDTBLP "
    "           WHERE T.CodAngajament = %s "
    "             AND T.CodAI IN (SELECT CodAI FROM FX_Indicatori "
    "                             WHERE CodAngajament = %s) "
    "           GROUP BY T.CodAI) aggOrd ON I.CodAI = aggOrd.CodAI "
    "WHERE A.CodAngajament = %s "
    "GROUP BY I.CodIndicator "
    "ORDER BY Clsf, I.CodIndicator"
)


def _json_utf8(payload, status):
    """Raspuns JSON cu diacritice LITERALE (ensure_ascii=False): Descriere/Stare
    contin «În derulare» si trebuie sa ajunga la client ca UTF-8 real, nu \\uXXXX."""
    body = json.dumps(payload, ensure_ascii=False)
    return current_app.response_class(body, status=status, mimetype="application/json")


def _iso(value):
    """Data -> 'YYYY-MM-DD' (ISO) sau None. DataFX/DataCreare sunt DATETIME in
    schema, dar sumarul afiseaza doar ziua — .date() taie ora deterministic."""
    if value is None:
        return None
    try:
        return value.date().isoformat()
    except AttributeError:
        # Deja date (fara ora) sau string venit dintr-un driver mai vechi.
        return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _num(value):
    """Total -> float. Coloanele DOUBLE vin ca float, dar SUM() peste DECIMAL
    intoarce Decimal, care nu e serializabil JSON. COALESCE garanteaza non-None."""
    return float(value) if value is not None else 0.0


@forexe_bp.route("/api/forexe/sumar", methods=["GET"])
@require_session
def get_sumar():
    """Sumarul unui angajament: antet + un rand per indicator.

    Query: cod (obligatoriu) = CodAngajament.
    Returneaza { header: {cod_angajament, data_fx, data_creare, data_definitivare,
    descriere, stare, incarcat, preluat} | null, rows: [ {clsf, cod_indicator,
    partener, total_rezervari, total_receptii, total_plati, total_revizii,
    total_ordonantari}, ... ] }.

    Un `cod` necunoscut NU este 404: un angajament fara indicatori este legitim,
    deci raspunsul este 200 cu header=null si rows=[].
    """
    cod = request.args.get("cod")
    if cod is None or str(cod).strip() == "":
        return _json_utf8({"error": "Parametru lipsă: cod"}, 400)
    cod = str(cod).strip()

    # Scope: baza sesiunii, niciodata din cerere (vezi nota din capul fisierului).
    db_name = g.session.db_name

    conn = None
    try:
        conn = get_db_connection(db_name)
        cursor = conn.cursor()
        # Cei sapte %s in ordinea aparitiei in textul SQL: aggRez, aggRec, aggPlati,
        # aggRev, apoi DOI in aggOrd (T.CodAngajament + subinterogarea pe CodAI) si
        # la final filtrul exterior. SQL parametrizat — `cod` nu se interpoleaza NICIODATA.
        cursor.execute(_SQL, (cod, cod, cod, cod, cod, cod, cod))

        header = None
        rows = []
        for (cod_ang, data_fx, data_creare, data_def, descriere, stare, incarcat,
             preluat, clsf, cod_indicator, partener, total_rez, total_rec,
             total_plati, total_rev, total_ord) in cursor.fetchall():
            if header is None:
                # Coloanele de antet se repeta identic pe fiecare rand -> primul castiga.
                header = {
                    "cod_angajament": cod_ang,
                    "data_fx": _iso(data_fx),
                    "data_creare": _iso(data_creare),
                    "data_definitivare": _iso(data_def),
                    "descriere": descriere,
                    "stare": stare,
                    "incarcat": bool(incarcat),
                    "preluat": bool(preluat),
                }
            rows.append({
                "clsf": clsf,
                "cod_indicator": cod_indicator,
                "partener": partener,
                "total_rezervari": _num(total_rez),
                "total_receptii": _num(total_rec),
                "total_plati": _num(total_plati),
                "total_revizii": _num(total_rev),
                "total_ordonantari": _num(total_ord),
            })

        logger.info("[forexe.sumar] %s: cod=%s -> %s randuri", db_name, cod, len(rows))
        return _json_utf8({"header": header, "rows": rows}, 200)
    except Exception as e:
        # Fara inghitire: o eroare de baza intoarce motivul, NU un sumar gol —
        # un sumar gol ar minti operatorul ca angajamentul nu are indicatori.
        logger.error(f"[forexe.sumar] {e}", exc_info=True)
        return _json_utf8({"error": f"Eroare la citirea sumarului: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()
