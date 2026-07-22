# routes/forexe/receptii.py
"""
Ruta Receptii pentru frmFX_MAIN_REC (felia 0015, vederea Receptii).

Contract (GET /api/forexe/receptii?cod=<CodAngajament>):
    { "cod": "<CodAngajament>", "receptii": [ {...}, ... ], "plati": [ {...}, ... ] }

Scope: baza conectata ESTE unitatea (o baza MariaDB = o unitate), deci nu exista
parametru db_name / id_unitate — baza vine din sesiune (g.session.db_name), exact
ca la /api/forexe/tree, /api/forexe/sumar si /api/forexe/rezervari. Un token nu poate
tinti alta baza decat cea pe care s-a logat.

Granulatia `receptii`: UN RAND per linie FX_Receptii (IDR), cu parintele antet (H) si
parintele receptie (R) purtate pe rand. Endpoint-ul este un cititor "brut" deliberat —
NU pre-formeaza arborele si NU deduplica randurile pe fir. Clientul (ReceptiiView)
deriva:
  * arborele pe 2 niveluri: receptie (IDRR) -> antet (IDRH), dedus prin distinct;
  * grila LISTA (per antet selectat): un rand-total sintetic + un rand per clsf;
  * (felia 0015-02) tooltip-ul de receptie, din DIFH-uri + `plati`.
Aceeasi lista de randuri hraneste si arborele si grila si tooltip-ul, deci o modelare
pe server ar duplica-o pe fir.

Sursa Access (verificata in export, NU reghicita):
  - qFX_MAIN_REC_TREE     : arbore 2 niveluri, R INNER JOIN H ON IDRR; NU filtreaza Sters.
                            Ordinea: R.NRCRT, R.DataR, H.NrCrt, H.DataH.
  - qFX_MAIN_REC_LISTA_IND: grila per antet = rand-total (IDR=-1, Sum(DIF)) UNION ALL
                            randuri per clsf (Sum(Valoare), cu FX_Indicatori.NrCrt +
                            eticheta clasificatiei). Descrierea afisata = Descrierea
                            ANTETULUI (FX_Receptii_H.Descriere), Nz -> 'Toti indicatorii'
                            pe randul-total.
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

LEFT JOIN FX_Receptii (nu INNER): un antet FARA linii de receptie nu are voie sa DISPARA
din arbore — qFX_MAIN_REC_TREE il arata (R INNER JOIN H, fara dependenta de FX_Receptii).
Cu LEFT JOIN, un asemenea antet vine cu un rand avand campurile de linie NULL; clientul
il pastreaza in arbore, iar grila lui arata doar randul-total (Sum(DIF)=0).
LEFT JOIN FX_Indicatori: analog, eticheta lipsa nu sterge linia.

CONVENTIA CHEILOR MariaDB vs Access: vezi nota extinsa din routes/forexe/sumar.py.
Pe scurt: NU deduce cheia din numele coloanei — numara randurile inainte si dupa join.
"""
import json
import logging

from flask import request, g, current_app

from routes.auth.guard import require_session
from utils.database import get_db_connection

from . import forexe_bp

logger = logging.getLogger(__name__)

# Un rand per FX_Receptii (IDR) al angajamentului, cu antetul (H) si receptia (R) purtate.
# Clsf prin subinterogare scalara (LIMIT 1) cheiata pe FX_Indicatori.IdClsf (= id Access)
# + IdUnitate. Ordinea reproduce arborele Access (R.NRCRT, R.DataR, H.NrCrt, H.DataH),
# cu Rc.IDR ca tiebreaker stabil intre refresh-uri.
_SQL_RECEPTII = (
    "SELECT "
    "R.IDRR, R.NRCRT AS NrCrtR, R.DataR, R.SumaAntet, R.Incarcat, R.Preluat, "
    "H.IDRH, H.NrCrt AS NrCrtH, H.DataH, H.Total, H.DIFH, H.Sters AS StersH, "
    "H.Descriere AS DescriereH, "
    "Rc.IDR, Rc.IdClsf, Rc.CodIndicator, I.NrCrt AS NrCrtInd, Rc.Valoare, Rc.DIF, "
    "(SELECT C.Clsf FROM Clasificatii C "
    "  WHERE C.IdClsfAcc = I.IdClsf AND C.IdUnitate = I.IdUnitate "
    "  LIMIT 1) AS Clsf "
    "FROM FX_Receptii_R R "
    "INNER JOIN FX_Receptii_H H ON H.IDRR = R.IDRR "
    "LEFT JOIN FX_Receptii Rc  ON Rc.IDRH = H.IDRH "
    "LEFT JOIN FX_Indicatori I ON I.CodAI = Rc.CodAI "
    "WHERE R.CodAngajament = %s "
    "ORDER BY R.NRCRT, R.DataR, H.NrCrt, H.DataH, Rc.IDR"
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
    preluat, idrh, nrcrt_h, data_h, total, difh, sters_h, descriere_h, idr, id_clsf,
    cod_indicator, clsf, nrcrt_ind, valoare, dif}, ... ], plati: [ {data_plata, suma},
    ... ] }.

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
        conn = get_db_connection(db_name)
        cursor = conn.cursor()

        # SQL parametrizat — `cod` nu se interpoleaza NICIODATA in text.
        cursor.execute(_SQL_RECEPTII, (cod,))
        receptii = []
        for (idrr, nrcrt_r, data_r, suma_antet, incarcat, preluat,
             idrh, nrcrt_h, data_h, total, difh, sters_h, descriere_h,
             idr, id_clsf, cod_indicator, nrcrt_ind, valoare, dif, clsf) in cursor.fetchall():
            receptii.append({
                "idrr": int(idrr) if idrr is not None else None,
                "nrcrt_r": _opt_int(nrcrt_r),
                "data_r": _iso(data_r),
                "suma_antet": _num(suma_antet),
                "incarcat": bool(incarcat),
                "preluat": bool(preluat),
                "idrh": int(idrh) if idrh is not None else None,
                "nrcrt_h": _opt_int(nrcrt_h),
                "data_h": _iso(data_h),
                "total": _num(total),
                "difh": _num(difh),
                "sters_h": bool(sters_h),
                "descriere_h": descriere_h,
                # NULL cand antetul nu are linii de receptie (ramura LEFT JOIN).
                "idr": int(idr) if idr is not None else None,
                "id_clsf": int(id_clsf) if id_clsf is not None else 0,
                "cod_indicator": cod_indicator,
                "clsf": clsf,
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
