# routes/forexe/plati.py
"""
Ruta Plati pentru frmFX_MAIN_PLATI (felia 0017, vederea Plăți).

Contract (GET /api/forexe/plati?cod=<CodAngajament>):
    { "cod": "<CodAngajament>", "plati": [ {...}, ... ] }

Scope: baza conectata ESTE unitatea (o baza MariaDB = o unitate), deci nu exista
parametru db_name / id_unitate — baza vine din sesiune (g.session.db_name), exact
ca la /api/forexe/tree, /sumar, /rezervari si /receptii. Un token nu poate tinti alta
baza decat cea pe care s-a logat.

Granulatia: UN RAND per inregistrare FX_Plati a angajamentului. Endpoint-ul este un
cititor "brut" deliberat — NU pre-formeaza arborele. Clientul (PlatiView) deriva:
  * arborele pe 3 niveluri: luna -> zi (frunza, TOATE randurile zilei intr-un nod) ->
    plata (IdPlataFX);
  * grila LISTA (FILTRATA, nu agregata — spre deosebire de Recepții): randurile nodului;
  * panoul de detaliu = extrasul bancar, DEJA pe rand (FX_Extrase 1:1 cu Referinta).

Sursa Access (verificata in export, NU reghicita):
  - frmFX_MAIN_PLATI (Show_Plati)  : arbore luna -> zi; nivel 2 (IdPlataFX) e cod dormant
                                     dar mcTree_Click/RightIconClick/RefreshPlataLista au
                                     ramura Level=2 vie -> clientul construieste toate 3.
  - qFX_MAIN_PLATI_LISTA           : coloanele grilei (Clsf, Data_Plata, NrOP->NrDoc, Suma,
                                     platitor_nume) + IsNull(ORC.IDORDREC) -> ordonantat.
                                     Access citeste `P.Clsf` (denormalizat), NU nomenclatorul.
  - frmFX_MAIN_PLATI_LISTA_DETALII : extrasul bancar (FX_Extrase), inner-join pe H in Access
                                     dar fara sa selecteze nimic din H -> aici LEFT JOIN.

Clasificatia (clsf/denumire) — acelasi drum ca Sumar 0011-03 / Recepții 0015, NU se
reghiceste (contrazice SCHITA planului, care cheia direct pe P.IdClsf):
  - Se trece prin FX_Indicatori (join pe CodAI, PK -> 1:1, fara fan-out), fiindca
    `FX_Indicatori.IdClsf` este VERIFICAT ca id Access (= Clasificatii.IdClsfAcc) in
    0011-03; directia lui `FX_Plati.IdClsf` NU e verificata live.
  - Clasificatii se citeste prin SUBINTEROGARE SCALARA cu LIMIT 1 (nomenclatorul are
    duplicate reale pe (IdClsfAcc, IdUnitate); un join ar multiplica randurile), cu
    predicatul IdUnitate PASTRAT la nomenclator.
  - Se intorc AMANDOUA: `clsf`/`denumire` din nomenclator + `clsf_plata` = coloana bruta
    `FX_Plati.Clsf`; clientul cade pe `clsf_plata` cand `clsf` e gol, ca o plata sa nu
    ramana fara clasificatie afisata.

LEFT JOIN FX_Extrase (nu INNER, nici INNER pe FX_Extrase_H ca Access): un extras lipsa
NU are voie sa stearga plata din lista — panoul de detaliu isi arata starea goala.
`are_ord` se calculeaza contra unui derivat DISTINCT (o plata pe mai multe linii de
ordonantare nu poate duplica randul-plata).

CONVENTIA CHEILOR MariaDB vs Access: vezi nota extinsa din routes/forexe/sumar.py.
Pe scurt: NU deduce cheia din numele coloanei — numara randurile inainte si dupa join.
Aici FX_Extrase poate fan-out daca Referinta NU e 1:1 (afirmatie de operator, nu constrangere
de schema) — testul host-only asserteaza count(plati) == count(FX_Plati).
"""
import json
import logging

from flask import request, g, current_app

from routes.auth.guard import require_session
from utils.database import get_db_connection

from . import forexe_bp

logger = logging.getLogger(__name__)

# Un rand per FX_Plati al angajamentului, cu extrasul (FX_Extrase) purtat pe rand.
# clsf/denumire prin subinterogari scalare (LIMIT 1) cheiate pe FX_Indicatori.IdClsf
# (= id Access) + IdUnitate. are_ord contra unui derivat DISTINCT. Ordinea reproduce
# arborele si alegerea celei mai vechi zile ne-ordonantate: Data_plata crescator, apoi
# IdPlataFX ca tiebreaker stabil intre refresh-uri.
_SQL = (
    "SELECT "
    "P.IdPlataFX, P.IdClsf, P.CodAI, P.CodIndicator, P.NrOP, "
    "P.Data_plata, P.Suma, P.Tip, P.Incarcat, P.Preluat, "
    "P.Referinta_TREZOR, P.Clsf AS clsf_plata, "
    "(SELECT C.Clsf FROM Clasificatii C "
    "  WHERE C.IdClsfAcc = I.IdClsf AND C.IdUnitate = I.IdUnitate "
    "  LIMIT 1) AS clsf, "
    "(SELECT C.Denumire FROM Clasificatii C "
    "  WHERE C.IdClsfAcc = I.IdClsf AND C.IdUnitate = I.IdUnitate "
    "  LIMIT 1) AS denumire, "
    "(orc.IdPlataFX IS NOT NULL) AS are_ord, "
    "E.IDFXE, E.DataBanca, E.DataDoc, E.NrDoc AS nr_doc_extras, "
    "E.Referinta, E.platitor_nume, E.platitor_cui, E.platitor_iban, "
    "E.suma_debit, E.suma_credit, E.Explicatii "
    "FROM FX_Plati P "
    "LEFT JOIN FX_Indicatori I ON I.CodAI = P.CodAI "
    "LEFT JOIN FX_Extrase E ON E.Referinta = P.Referinta_TREZOR "
    "LEFT JOIN (SELECT DISTINCT IdPlataFX FROM FX_ORD_TBL_REC) orc "
    "       ON orc.IdPlataFX = P.IdPlataFX "
    "WHERE P.CodAngajament = %s "
    "ORDER BY P.Data_plata, P.IdPlataFX"
)


def _json_utf8(payload, status):
    """Raspuns JSON cu diacritice LITERALE (ensure_ascii=False): denumire/platitor/explicatii
    contin text romanesc si trebuie sa ajunga la client ca UTF-8 real, nu \\uXXXX."""
    body = json.dumps(payload, ensure_ascii=False)
    return current_app.response_class(body, status=status, mimetype="application/json")


def _iso(value):
    """DateTime -> 'YYYY-MM-DD' (ISO) sau None. Data_plata/DataBanca sunt DATETIME in schema,
    dar vederea grupeaza/afiseaza pe zi — .date() taie ora deterministic. DataDoc e TEXT in
    FX_Extrase, deci nu trece pe aici (ramane string brut)."""
    if value is None:
        return None
    try:
        return value.date().isoformat()
    except AttributeError:
        return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _num(value):
    """Coloana de bani -> float. DOUBLE vine ca float; None devine 0.0 (server-side), ca grila
    sa arate «0,00», nu gol. Se aplica la suma/suma_debit/suma_credit."""
    return float(value) if value is not None else 0.0


@forexe_bp.route("/api/forexe/plati", methods=["GET"])
@require_session
def get_plati():
    """Plățile unui angajament: un rand per inregistrare FX_Plati, cu extrasul bancar purtat.

    Query: cod (obligatoriu) = CodAngajament.
    Returneaza { cod, plati: [ {id_plata_fx, id_clsf, cod_ai, cod_indicator, nr_op,
    data_plata, suma, tip, incarcat, preluat, referinta_trezor, clsf, denumire, clsf_plata,
    are_ord, idfxe, data_banca, data_doc, nr_doc_extras, referinta, platitor_nume,
    platitor_cui, platitor_iban, suma_debit, suma_credit, explicatii}, ... ] }.

    Un `cod` necunoscut / fara plati NU este 404: un angajament fara plati este legitim,
    deci raspunsul este 200 cu plati=[].
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
        plati = []
        for (id_plata_fx, id_clsf, cod_ai, cod_indicator, nr_op,
             data_plata, suma, tip, incarcat, preluat,
             referinta_trezor, clsf_plata, clsf, denumire, are_ord,
             idfxe, data_banca, data_doc, nr_doc_extras,
             referinta, platitor_nume, platitor_cui, platitor_iban,
             suma_debit, suma_credit, explicatii) in cursor.fetchall():
            plati.append({
                "id_plata_fx": int(id_plata_fx) if id_plata_fx is not None else None,
                "id_clsf": int(id_clsf) if id_clsf is not None else 0,
                "cod_ai": cod_ai,
                "cod_indicator": cod_indicator,
                "nr_op": nr_op,
                "data_plata": _iso(data_plata),
                "suma": _num(suma),
                "tip": tip,
                "incarcat": bool(incarcat),
                "preluat": bool(preluat),
                "referinta_trezor": referinta_trezor,
                # Nomenclator (poate fi gol daca indicatorul nu are clasificatie); clientul
                # cade pe clsf_plata (coloana bruta FX_Plati.Clsf) cand clsf e gol.
                "clsf": clsf,
                "denumire": denumire,
                "clsf_plata": clsf_plata,
                # Ordonantat? — bool real (0/1 din EXISTS-ul DISTINCT). Conduce iconita «+».
                "are_ord": bool(are_ord),
                # Extrasul bancar (LEFT JOIN) — toate NULL cand plata nu are extras asociat.
                "idfxe": int(idfxe) if idfxe is not None else None,
                "data_banca": _iso(data_banca),
                "data_doc": data_doc,          # TEXT in FX_Extrase — string brut, nu data
                "nr_doc_extras": nr_doc_extras,
                "referinta": referinta,
                "platitor_nume": platitor_nume,
                "platitor_cui": platitor_cui,
                "platitor_iban": platitor_iban,
                "suma_debit": _num(suma_debit),
                "suma_credit": _num(suma_credit),
                "explicatii": explicatii,
            })

        logger.info("[forexe.plati] %s: cod=%s -> %s randuri", db_name, cod, len(plati))
        return _json_utf8({"cod": cod, "plati": plati}, 200)
    except Exception as e:
        # Fara inghitire: o eroare de baza intoarce motivul, NU o lista goala — o lista
        # goala ar minti operatorul ca angajamentul nu are plati.
        logger.error(f"[forexe.plati] {e}", exc_info=True)
        return _json_utf8({"error": f"Eroare la citirea plăților: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()
