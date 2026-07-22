# routes/forexe/rezervari.py
"""
Ruta Rezervari pentru frmFX_MAIN_REZ (felia 0014, vederea Rezervari).

Contract (GET /api/forexe/rezervari?cod=<CodAngajament>):
    { "rows": [ {...}, ... ] }

Scope: baza conectata ESTE unitatea (o baza MariaDB = o unitate), deci nu exista
parametru db_name / id_unitate — baza vine din sesiune (g.session.db_name), exact
ca la /api/forexe/tree si /api/forexe/sumar. Un token nu poate tinti alta baza
decat cea pe care s-a logat.

Granulatia: UN RAND per inregistrare FX_Rezervari a angajamentului. Endpoint-ul este
un cititor "brut" deliberat — NU pre-formeaza arborele. Clientul (RezervariView)
grupeaza randurile pe luni (folder) si pe (data, tip operatie) (frunza) si umple grila
master/detail. Motivul: aceeasi lista de randuri hraneste si arborele si grila, deci
o modelare pe server ar duplica-o pe fir.

Sursa Access:
  - qFX_REZERVARI_TREE : arborele. Confirma DOUA lucruri pe care planul le avea „deschise":
      * totalul lunar al folderului = SUM(R_Valoare) pe (CodAngajament, luna, an)
        (coloana `TOTALL` din query — nu era derivabil din screenshot singur);
      * ordinea tipurilor in interiorul unei zile: Initiala(0) < Marire(1) < Micsorare(2)
        (cheia `strData = Switch(...)`).
  - QFX_DDF_REZERVARI : valoarea frunzei = SUM(IIf(EInitiala, R_Initiala, R_Valoare))
    pe grupul (data, tip). Clientul o calculeaza; serverul trimite doar coloanele brute.
  - frmFX_MAIN_REZ_LISTA : grila = Clsf, R_CreditBug, R_Initiala, R_Valoare, R_Definitiva.

Clasificatia (Clsf/Denumire) — aceleasi decizii ca la Sumar (felia 0011-03), NU se
reghicesc:
  - Se trece prin FX_Indicatori (join pe CodAI), NU prin FX_Rezervari.IdClsf. Motiv:
    `FX_Indicatori.IdClsf` este VERIFICAT ca id Access (= Clasificatii.IdClsfAcc) pe
    date reale in 0011-03; directia cheii `FX_Rezervari.IdClsf` nu a fost verificata
    live, deci nu ne bazam pe ea pentru eticheta. qFX_REZERVARI_TREE face exact acelasi
    drum (RZ INNER JOIN FX_Indicatori ON CodAI, apoi Clasificatii).
  - Clasificatii se citeste prin SUBINTEROGARE SCALARA cu LIMIT 1, nu prin join:
    nomenclatorul are duplicate reale pe (IdClsfAcc, IdUnitate) (vezi 0011-03), deci un
    join ar multiplica randurile-rezervare. Subinterogarea garanteaza UN rand per
    rezervare indiferent de duplicate, si pastreaza „fara clasificatie -> gol".
  - Predicatul IdUnitate RAMANE la nomenclator: baza per-unitate tine Clasificatii
    pentru MAI MULTE unitati (regula „drop IdUnitate" e doar pentru tabelele FX_).

LEFT JOIN FX_Indicatori (nu INNER, desi Access foloseste INNER): o rezervare nu are
voie sa DISPARA pentru ca ii lipseste indicatorul/eticheta — ar disparea bani din
arbore fara urma. In practica exista un FK (FX_Indicatori__FX_Rezervari pe CodAI), deci
INNER si LEFT dau acelasi rezultat; LEFT e centura de siguranta. Clsf ramane gol daca
indicatorul lipseste.

Fara filtru pe R_Valoare (spre deosebire de qFX_REZERVARI_TREE, care are R_Valoare<>0):
endpoint-ul intoarce randurile brute, iar filtrarea/gruparea e treaba clientului. Un
rand cu R_Valoare=0 nu schimba totalul lunar (SUM(R_Valoare)) si nu produce o frunza
vizibila decat daca si valoarea ei (SUM(IIf(EInitiala,R_Initiala,R_Valoare))) e non-zero.

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

# Un rand per FX_Rezervari al angajamentului. Clsf/Denumire prin subinterogari scalare
# (LIMIT 1) cheiate pe FX_Indicatori.IdClsf (= id Access) + IdUnitate. Ordinea reproduce
# arborele: data crescator, apoi clasificatie, apoi IDRZ (stabil intre refresh-uri).
_SQL = (
    "SELECT R.IDRZ, R.CodIndicator, R.DataRezervare, "
    "R.R_CreditBug, R.R_Initiala, R.R_Valoare, R.R_Definitiva, "
    "R.EInitiala, R.EMarire, R.EMicsorare, R.AreDDF, "
    "(SELECT C.Clsf FROM Clasificatii C "
    "  WHERE C.IdClsfAcc = I.IdClsf AND C.IdUnitate = I.IdUnitate "
    "  LIMIT 1) AS Clsf, "
    "(SELECT C.Denumire FROM Clasificatii C "
    "  WHERE C.IdClsfAcc = I.IdClsf AND C.IdUnitate = I.IdUnitate "
    "  LIMIT 1) AS Denumire "
    "FROM FX_Rezervari R "
    "LEFT JOIN FX_Indicatori I ON I.CodAI = R.CodAI "
    "WHERE R.CodAngajament = %s "
    "ORDER BY R.DataRezervare, Clsf, R.IDRZ"
)


def _json_utf8(payload, status):
    """Raspuns JSON cu diacritice LITERALE (ensure_ascii=False): Denumire contine
    text romanesc si trebuie sa ajunga la client ca UTF-8 real, nu \\uXXXX."""
    body = json.dumps(payload, ensure_ascii=False)
    return current_app.response_class(body, status=status, mimetype="application/json")


def _iso(value):
    """DataRezervare -> 'YYYY-MM-DD' (ISO) sau None. E DATETIME in schema, dar
    rezervarile sunt pe zi — .date() taie ora deterministic, iar clientul grupeaza
    pe (an, luna) si pe data exacta din acest ISO."""
    if value is None:
        return None
    try:
        return value.date().isoformat()
    except AttributeError:
        return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _num(value):
    """Coloana de bani -> float. DOUBLE vine ca float; None (rand fara valoare)
    devine 0.0, ca grila sa arate «0,00», nu gol."""
    return float(value) if value is not None else 0.0


@forexe_bp.route("/api/forexe/rezervari", methods=["GET"])
@require_session
def get_rezervari():
    """Rezervarile unui angajament: un rand per inregistrare FX_Rezervari.

    Query: cod (obligatoriu) = CodAngajament.
    Returneaza { rows: [ {idrz, cod_indicator, clsf, denumire, data_rezervare,
    r_credit_bug, r_initiala, r_valoare, r_definitiva, e_initiala, e_marire,
    e_micsorare, are_ddf}, ... ] }.

    Un `cod` necunoscut / fara rezervari NU este 404: un angajament fara rezervari
    este legitim, deci raspunsul este 200 cu rows=[].
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
        cursor.execute(_SQL, (cod,))

        rows = []
        for (idrz, cod_indicator, data_rezervare, r_credit_bug, r_initiala,
             r_valoare, r_definitiva, e_initiala, e_marire, e_micsorare, are_ddf,
             clsf, denumire) in cursor.fetchall():
            rows.append({
                "idrz": int(idrz) if idrz is not None else None,
                "cod_indicator": cod_indicator,
                "clsf": clsf,
                "denumire": denumire,
                "data_rezervare": _iso(data_rezervare),
                "r_credit_bug": _num(r_credit_bug),
                "r_initiala": _num(r_initiala),
                "r_valoare": _num(r_valoare),
                "r_definitiva": _num(r_definitiva),
                "e_initiala": bool(e_initiala),
                "e_marire": bool(e_marire),
                "e_micsorare": bool(e_micsorare),
                "are_ddf": bool(are_ddf),
            })

        logger.info("[forexe.rezervari] %s: cod=%s -> %s randuri", db_name, cod, len(rows))
        return _json_utf8({"rows": rows}, 200)
    except Exception as e:
        # Fara inghitire: o eroare de baza intoarce motivul, NU o lista goala — o lista
        # goala ar minti operatorul ca angajamentul nu are rezervari.
        logger.error(f"[forexe.rezervari] {e}", exc_info=True)
        return _json_utf8({"error": f"Eroare la citirea rezervărilor: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()
