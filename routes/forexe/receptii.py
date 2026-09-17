# routes/forexe/receptii.py
"""
Ruta Receptii pentru frmFX_MAIN_REC (felia 0015, vederea Receptii).

Contract (GET /api/forexe/receptii?cod=<CodAngajament>):
    { "cod": "<CodAngajament>", "receptii": [ {...}, ... ], "plati": [ {...}, ... ] }

Scope: baza conectata ESTE unitatea (o baza MariaDB = o unitate), deci nu exista
parametru db_name / id_unitate — baza vine din sesiune (g.session.db_name), exact
ca la /api/forexe/tree, /api/forexe/sumar si /api/forexe/rezervari. Un token nu poate
tinti alta baza decat cea pe care s-a logat.

Granulatia `receptii` (17.09.2026): UN RAND per (receptie R, indicator RHR, antet H), cu
linia FX_Receptii a ACELUI indicator in ACEL antet purtata pe rand (IDR/Valoare NULL ->
0 cand antetul nu are linie pe indicator). Endpoint-ul este un cititor "brut" deliberat —
NU pre-formeaza arborele si NU deduplica randurile pe fir. Clientul (ReceptiiView)
deriva:
  * arborele: radacina «Toate receptiile» -> luna -> receptie (IDRR), dedus prin distinct;
  * grila LISTA (per nod selectat): un rand per clsf, din ULTIMUL antet al fiecarei
    receptii (felia 0065) -- nu un agregat peste toate antetele lantului;
  * tooltip-ul de receptie / luna: cumulul totalurilor ULTIMULUI antet al fiecarei
    receptii + `plati` (felia 0065; pana atunci suma DIFH-urilor, care e NULL pe un
    antet asezat din editor inainte de 0065 si pierdea receptia din total).
Aceeasi lista de randuri hraneste si arborele si grila si tooltip-ul, deci o modelare
pe server ar duplica-o pe fir.

Sursa Access (verificata in export, NU reghicita):
  - qFX_MAIN_REC_TREE     : arbore 2 niveluri, R INNER JOIN H ON IDRR; NU filtreaza Sters.
                            Ordinea: R.NRCRT, R.DataR, H.NrCrt, H.DataH.
  - qFX_MAIN_REC_LISTA_IND: grila per antet = rand-total (IDR=-1, Sum(DIF)) UNION ALL
                            randuri per clsf (Sum(Valoare), cu FX_Indicatori.NrCrt +
                            eticheta clasificatiei). NB (revizuire operator 2026-07-22):
                            clientul agrega LISTA la ORICE nivel (luna/receptie/antet), iar
                            coloana Descriere arata `Denumire` clasificatiei (bine definita
                            per clsf la orice nivel), nu Descrierea antetului — de aceea se
                            intoarce si `denumire`. `descriere_h` (antetul) ramane pe fir ca
                            coloana bruta, dar grila nu o mai foloseste.
  - qFX_MAIN_REC_TT_PLATI : platile receptiei = Data_plata, Suma din FX_Plati WHERE
                            CodAngajament = cod, ORDER BY Data_plata. FARA alt filtru
                            (nici Incarcat/Preluat/Tip) — confirmat in export.

Join-ul clasificatiei — aceleasi decizii ca la Sumar (felia 0011-03), NU se reghicesc:
  - Se trece prin FX_Indicatori (join pe CodAI), NU prin FX_Receptii.IdClsf/Clsf
    (denormalizat, poate fi gol pe date reale). `FX_Indicatori.IdClsf` este VERIFICAT
    ca id Access (= Clasificatii.IdClsfAcc) in 0011-03.
  - Clasificatii se citeste prin SUBINTEROGARE SCALARA cu LIMIT 1, nu prin join:
    nomenclatorul are duplicate reale pe (IdClsfAcc, IdUnitate), deci un join ar
    multiplica randurile. Subinterogarea garanteaza UN Clsf per linie, indiferent de
    duplicate, si pastreaza „fara clasificatie -> gol".
  - Predicatul IdUnitate RAMANE la nomenclator (regula „drop IdUnitate" e doar pentru
    tabelele FX_).

ANTETUL E RADACINA INTEROGARII, NU RECEPTIA (felia 0062). Pana la 0062 se pornea din
FX_Receptii_R cu INNER JOIN pe H, ca in qFX_MAIN_REC_TREE. Operatorul a constatat insa ca
Access pierde IDRR-ul pe FX_Receptii_H, iar dupa migrare antetele ajung cu `IDRR IS NULL`:
INNER JOIN-ul le pierdea pe toate si vederea spunea «angajamentul nu are receptii» cand el
avea, doar neasezate pe o receptie. Acum se porneste din H, cu LEFT JOIN spre R: un antet
neasezat vine cu campurile de receptie NULL (`idrr` None pe fir; clientul il arata intr-un
dosar «Instantanee neasezate» si il trimite la editorul de legaturi). O receptie FARA niciun
antet nu apare -- nici in Access nu aparea (R INNER JOIN H).

LEFT JOIN FX_Receptii (nu INNER): randul de STERGERE (F21) nu are linii de receptie si
nu are voie sa DISPARA din arbore — qFX_MAIN_REC_TREE il arata (R INNER JOIN H, fara
dependenta de FX_Receptii). Cu LEFT JOIN, un asemenea antet vine cu un rand avand
campurile de linie NULL; clientul il pastreaza in arbore, iar grila lui arata doar
randul-total (Sum(DIF)=0).
LEFT JOIN FX_Indicatori: analog, eticheta lipsa nu sterge linia.

F32 (17.09.2026): ORICE ALT antet fara linii -- doar totalul, fara niciun indicator, si
care nu e stergere -- NU e instantaneu, e o eroare lasata de vechea aplicatie Access, si
se lasa afara cu `SNAPSHOT_COUNTS_SQL` (acelasi filtru ca editorul de legaturi, ingestia
si DIFH). Pana pe 17.09 arborele il arata, ca Access; operatorul a hotarat ca se ignora
peste tot.

CONVENTIA CHEILOR MariaDB vs Access: vezi nota extinsa din routes/forexe/sumar.py.
Pe scurt: NU deduce cheia din numele coloanei — numara randurile inainte si dupa join.
"""
import json
import logging

from flask import request, g, current_app

from routes.auth.guard import require_session
from utils.database import get_kbot_connection

from . import forexe_bp
from .prelucrare_helpers import SNAPSHOT_COUNTS_SQL

logger = logging.getLogger(__name__)

# STUPID FACUT DE CLAUDE!!!!
# Un rand per FX_Receptii (IDR) al angajamentului, cu antetul (H) si receptia (R) purtate.
# Clsf prin subinterogare scalara (LIMIT 1) cheiata pe FX_Indicatori.IdClsf (= id Access)
# + IdUnitate. Ordinea reproduce arborele Access (R.NRCRT, R.DataR, H.NrCrt, H.DataH),
# cu Rc.IDR ca tiebreaker stabil intre refresh-uri.
# _SQL_RECEPTII = (
#     "SELECT "
#     "R.IDRR, R.NRCRT AS NrCrtR, R.DataR, R.SumaAntet, R.Incarcat, R.Preluat, "
#     "R.Reconstituit, R.ReconstituitNesigur, "
#     "R.Descriere AS DescriereR, "
#     "H.IDRH, H.NrCrt AS NrCrtH, H.DataH, H.Total, H.DIFH, H.Sters AS StersH, "
#     "COALESCE(H.EsteStergere, 0) AS EsteStergere, "
#     "H.Descriere AS DescriereH, "
#     "Rc.IDR, Rc.IdClsf, Rc.CodIndicator, I.NrCrt AS NrCrtInd, Rc.Valoare, Rc.DIF, "
#     "(SELECT C.Clsf FROM Clasificatii C "
#     "  WHERE C.IdClsfAcc = I.IdClsf AND C.IdUnitate = I.IdUnitate "
#     "  LIMIT 1) AS Clsf, "
#     "(SELECT C.Denumire FROM Clasificatii C "
#     "  WHERE C.IdClsfAcc = I.IdClsf AND C.IdUnitate = I.IdUnitate "
#     "  LIMIT 1) AS Denumire "
#     "FROM FX_Receptii_H H "
#     "LEFT JOIN FX_Receptii_R R ON R.IDRR = H.IDRR "
#     "LEFT JOIN FX_Receptii Rc  ON Rc.IDRH = H.IDRH "
#     "LEFT JOIN FX_Indicatori I ON I.CodAI = Rc.CodAI "
#     "WHERE H.CodAngajament = %s AND " + SNAPSHOT_COUNTS_SQL + " "
#     "ORDER BY R.NRCRT, R.DataR, H.NrCrt, H.DataH, Rc.IDR"
# )
_SQL_RECEPTII = (
    "SELECT "
    "R.IDRR, R.NRCRT AS NrCrtR, R.DataR, R.SumaAntet, R.Incarcat, R.Preluat, "
    "R.Reconstituit, R.ReconstituitNesigur, "
    "R.Descriere AS DescriereR, "
    "H.IDRH, H.NrCrt AS NrCrtH, H.DataH, H.Total, H.DIFH, H.Sters AS StersH, "
    "COALESCE(H.EsteStergere, 0) AS EsteStergere, "
    "H.Descriere AS DescriereH, "
    # The indicator's identity (IdClsf, CodIndicator, Clsf, Denumire) comes from RHR/I,
    # the value from THAT indicator's line in THIS header (Rc, matched on CodAI too).
    "Rc.IDR, I.IdClsf, RHR.CodIndicator, I.NrCrt AS NrCrtInd, Rc.Valoare, Rc.DIF, "
    "(SELECT C.Clsf FROM Clasificatii C "
    "  WHERE C.IdClsfAcc = I.IdClsf AND C.IdUnitate = I.IdUnitate "
    "  LIMIT 1) AS Clsf, "
    "(SELECT C.Denumire FROM Clasificatii C "
    "  WHERE C.IdClsfAcc = I.IdClsf AND C.IdUnitate = I.IdUnitate "
    "  LIMIT 1) AS Denumire "
    "FROM FX_Receptii_R R "
    "INNER JOIN FX_Receptii_RHR RHR ON R.IDRR = RHR.IDRR "
    "INNER JOIN (SELECT I.CodAI, I.IdClsf, I.IdUnitate, I.NrCrt "
    "            FROM FX_Indicatori I "
    "            WHERE I.CodAngajament = %s) AS I ON I.CodAI = RHR.CodAI "
    "LEFT JOIN FX_Receptii_H H ON H.IDRR = R.IDRR "
    # One row per (receptie, indicator, header). Without `Rc.CodAI = RHR.CodAI` every
    # line of the header was crossed with every indicator of the receptie, so each
    # indicator carried the header's whole total (operator's finding, 17.09.2026).
    # An indicator with no line in this header comes with Rc NULL -> Valoare 0.
    "LEFT JOIN FX_Receptii Rc  ON Rc.IDRH = H.IDRH AND Rc.CodAI = RHR.CodAI "
    "WHERE R.CodAngajament = %s AND " + SNAPSHOT_COUNTS_SQL + " "
    "ORDER BY R.NRCRT, R.DataR, H.NrCrt, H.DataH, I.NrCrt, Rc.IDR"
)

# Platile angajamentului (qFX_MAIN_REC_TT_PLATI): Data_plata + Suma, fara alt filtru.
# Folosite doar de tooltip-ul de receptie (felia 0015-02); se trimit din 0015-01 ca
# jumatatea de client sa fie un singur apel.
_SQL_PLATI = (
    "SELECT P.Data_plata, P.Suma "
    "FROM FX_Plati P "
    "WHERE P.CodAngajament = %s "
    "ORDER BY P.Data_plata"
)


def _json_utf8(payload, status):
    """Raspuns JSON cu diacritice LITERALE (ensure_ascii=False): DescriereH contine
    text romanesc si trebuie sa ajunga la client ca UTF-8 real, nu \\uXXXX."""
    body = json.dumps(payload, ensure_ascii=False)
    return current_app.response_class(body, status=status, mimetype="application/json")


def _iso(value):
    """DateTime -> 'YYYY-MM-DD' (ISO) sau None. DataR/DataH/Data_plata sunt DATETIME in
    schema, dar vederea grupeaza pe zi — .date() taie ora deterministic."""
    if value is None:
        return None
    try:
        return value.date().isoformat()
    except AttributeError:
        return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _isoTime(value):
    """DateTime -> 'YYYY-MM-DDTHH:MM:SS' (ISO) sau None. Păstrează ora, minutele și secundele."""
    if value is None:
        return None
    try:
        return value.isoformat(sep='T', timespec='seconds')
    except AttributeError:
        return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _num(value):
    """Coloana de bani -> float. DOUBLE vine ca float; None devine 0.0, ca grila/tooltip
    sa arate «0,00», nu gol."""
    return float(value) if value is not None else 0.0


def _opt_int(value):
    """Coloana Long optionala (NrCrt) -> int sau None. NrCrt poate lipsi pe un indicator,
    iar randul-total al grilei nu are NrCrt — clientul le arata gol, deci pastram None."""
    return int(value) if value is not None else None


@forexe_bp.route("/api/forexe/receptii", methods=["GET"])
@require_session
def get_receptii():
    """Receptiile unui angajament: un rand per linie FX_Receptii + lista de plati.

    Query: cod (obligatoriu) = CodAngajament.
    Returneaza { cod, receptii: [ {idrr, nrcrt_r, data_r, suma_antet, incarcat,
    preluat, reconstituit, reconstituit_nesigur, descriere_r, idrh, nrcrt_h, data_h,
    total, difh, sters_h, este_stergere, descriere_h, idr, id_clsf,
    cod_indicator, clsf, denumire, nrcrt_ind, valoare, dif}, ... ], plati: [ {data_plata,
    suma}, ... ] }.

    Un `cod` necunoscut / fara receptii NU este 404: un angajament fara receptii este
    legitim, deci raspunsul este 200 cu receptii=[] (si plati dupa caz).
    """
    cod = request.args.get("cod")
    if cod is None or str(cod).strip() == "":
        return _json_utf8({"error": "Parametru lipsă: cod"}, 400)
    cod = str(cod).strip()

    # Scope: baza sesiunii, niciodata din cerere (o baza = o unitate).
    db_name = g.session.db_name

    conn = None
    try:
        conn = get_kbot_connection(db_name)
        cursor = conn.cursor()

        # SQL parametrizat — `cod` nu se interpoleaza NICIODATA in text.
        cursor.execute(_SQL_RECEPTII, (cod, cod, ))
        receptii = []
        for (idrr, nrcrt_r, data_r, suma_antet, incarcat, preluat,
             reconstituit, reconstituit_nesigur, descriere_r,
             idrh, nrcrt_h, data_h, total, difh, sters_h, este_stergere, descriere_h,
             idr, id_clsf, cod_indicator, nrcrt_ind, valoare, dif, clsf,
             denumire) in cursor.fetchall():
            receptii.append({
                # None = antet neasezat pe nicio receptie (H.IDRR NULL, felia 0062).
                "idrr": int(idrr) if idrr is not None else None,
                "nrcrt_r": _opt_int(nrcrt_r),
                "data_r": _iso(data_r),
                "suma_antet": _num(suma_antet),
                "incarcat": bool(incarcat),
                "preluat": bool(preluat),
                "reconstituit": bool(reconstituit),
                "reconstituit_nesigur": bool(reconstituit_nesigur),
                "descriere_r": descriere_r,
                "idrh": int(idrh) if idrh is not None else None,
                "nrcrt_h": _opt_int(nrcrt_h),
                "data_h": _isoTime(data_h),
                "total": _num(total),
                "difh": _num(difh),
                "sters_h": bool(sters_h),
                "este_stergere": bool(este_stergere),
                "descriere_h": descriere_h,
                # NULL cand antetul nu are linii de receptie (ramura LEFT JOIN).
                "idr": int(idr) if idr is not None else None,
                "id_clsf": int(id_clsf) if id_clsf is not None else 0,
                "cod_indicator": cod_indicator,
                "clsf": clsf,
                "denumire": denumire,
                "nrcrt_ind": _opt_int(nrcrt_ind),
                "valoare": _num(valoare),
                "dif": _num(dif),
            })

        cursor.execute(_SQL_PLATI, (cod,))
        plati = [
            {"data_plata": _iso(data_plata), "suma": _num(suma)}
            for (data_plata, suma) in cursor.fetchall()
        ]

        logger.info("[forexe.receptii] %s: cod=%s -> %s randuri, %s plati",
                    db_name, cod, len(receptii), len(plati))
        return _json_utf8({"cod": cod, "receptii": receptii, "plati": plati}, 200)
    except Exception as e:
        # Fara inghitire: o eroare de baza intoarce motivul, NU o lista goala — o lista
        # goala ar minti operatorul ca angajamentul nu are receptii.
        logger.error(f"[forexe.receptii] {e}", exc_info=True)
        return _json_utf8({"error": f"Eroare la citirea recepțiilor: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()
