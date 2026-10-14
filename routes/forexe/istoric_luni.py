# routes/forexe/istoric_luni.py
"""
Stergerea ULTIMEI luni din istoricul FOREXE (Setari > FOREXE, cererea operatorului 12.10.2026).

DE CE EXISTA
============
Cand valorile unei luni au iesit gresit (rezervari calculate din baza altei luni etc.), singura
cale curata de a le reface e sa dispara randurile lunii din `FX_Istoric` si sa fie aduse din nou
din FOREXE: ingestia le scrie iar, cu lantul corect. Operatia e DISTRUCTIVA, deci e stransa de
patru reguli:

  * se sterge DOAR o luna intreaga (luna calendaristica a lui `FX_Istoric.DataFX`);
  * DOAR ultima luna in ordine cronologica (cea a celui mai nou rand din istoric) -- o luna din
    mijloc ar lasa lantul de rezervari al lunilor de dupa ea fara baza;
  * NICIODATA daca luna are DDF (o revizie `FX_DDF_REV.DataRev` in luna, sau o rezervare legata de
    un DDF) ori ORD (`FX_ORD.DataORD` in luna): acelea sunt documente emise, nu se rup;
  * totul intr-o singura tranzactie.

CE SE STERGE
============
Randurile `FX_Istoric` ale lunii. Restul pleaca singur, prin cheile straine ON DELETE CASCADE:
`FX_Rezervari`, `FX_Plati`, `FX_Receptii_H` (instantaneele) si `FX_Receptii` (liniile lor).
Singurul lucru pe care cascada nu-l face e receptia in sine (`FX_Receptii_R`): se sterg DOAR cele
datate in luna stearsa (`DataR`). O receptie dintr-o luna mai veche ramane chiar daca nu-i mai ramane
niciun instantaneu: la urmatoarea descarcare din FOREXE informatia trebuie sa se poata reface, nu sa se
piarda.

Ruta nu hotaraste "cine are voie": poarta cu parola a optiunilor avansate e in K-BOT, iar serverul
cere doar sesiunea. NEVERIFICAT pe MariaDB: nimic rulat aici.
"""
import json
import logging
from datetime import date, datetime
from typing import List, Optional, Tuple

from flask import request, g, current_app

from routes.auth.guard import require_session
from utils.database import get_kbot_connection

from . import forexe_bp

logger = logging.getLogger(__name__)

_ULTIMA_ZI_SQL = "SELECT MAX(DataFX) FROM FX_Istoric"

_ISTORIC_N_SQL = "SELECT COUNT(*) FROM FX_Istoric WHERE DataFX >= %s AND DataFX < %s"
_ANGAJAMENTE_N_SQL = (
    "SELECT COUNT(DISTINCT CodAngajament) FROM FX_Istoric WHERE DataFX >= %s AND DataFX < %s")
_REZERVARI_N_SQL = (
    "SELECT COUNT(*) FROM FX_Rezervari R JOIN FX_Istoric I ON I.ID = R.IDH "
    "WHERE I.DataFX >= %s AND I.DataFX < %s")
_PLATI_N_SQL = (
    "SELECT COUNT(*) FROM FX_Plati P JOIN FX_Istoric I ON I.ID = P.IDH "
    "WHERE I.DataFX >= %s AND I.DataFX < %s")
_RECEPTII_N_SQL = (
    "SELECT COUNT(*) FROM FX_Receptii_H H JOIN FX_Istoric I ON I.ID = H.IDH "
    "WHERE I.DataFX >= %s AND I.DataFX < %s")

# DDF in the month: a revision dated in it, or a reservation of the month that a DDF touches.
_DDF_REV_N_SQL = "SELECT COUNT(*) FROM FX_DDF_REV WHERE DataRev >= %s AND DataRev < %s"
_DDF_REZ_N_SQL = (
    "SELECT COUNT(*) FROM FX_Rezervari R JOIN FX_Istoric I ON I.ID = R.IDH "
    "WHERE I.DataFX >= %s AND I.DataFX < %s AND (R.AreDDF = 1 OR R.IDREV IS NOT NULL)")
_ORD_N_SQL = "SELECT COUNT(*) FROM FX_ORD WHERE DataORD >= %s AND DataORD < %s"

_STERGE_ISTORIC_SQL = "DELETE FROM FX_Istoric WHERE DataFX >= %s AND DataFX < %s"
# Receptiile datate in luna: ele nu au ce cauta fara istoricul lunii (instantaneele lor au plecat).
_STERGE_RECEPTII_LUNA_SQL = "DELETE FROM FX_Receptii_R WHERE DataR >= %s AND DataR < %s"


def _json_utf8(payload, status):
    body = json.dumps(payload, ensure_ascii=False)
    return current_app.response_class(body, status=status, mimetype="application/json")


def _interval(luna: date) -> Tuple[datetime, datetime]:
    """[prima zi a lunii, prima zi a lunii urmatoare)."""
    inceput = datetime(luna.year, luna.month, 1)
    urmatoarea = datetime(luna.year + 1, 1, 1) if luna.month == 12 \
        else datetime(luna.year, luna.month + 1, 1)
    return inceput, urmatoarea


def _eticheta(luna: date) -> str:
    return f"{luna.month:02d}.{luna.year}"


def _parseaza_luna(text) -> Optional[date]:
    """'YYYY-MM' -> prima zi a lunii; altceva -> None."""
    try:
        an, luna = str(text or "").strip().split("-")
        return date(int(an), int(luna), 1)
    except (ValueError, TypeError):
        return None


def _numara(cursor, sql: str, inceput: datetime, sfarsit: datetime) -> int:
    cursor.execute(sql, (inceput, sfarsit))
    rand = cursor.fetchone()
    return int(rand[0] or 0) if rand else 0


def _ultima_luna(cursor) -> Optional[date]:
    cursor.execute(_ULTIMA_ZI_SQL)
    rand = cursor.fetchone()
    if not rand or rand[0] is None:
        return None
    return date(rand[0].year, rand[0].month, 1)


def _descrie(cursor, luna: date) -> dict:
    inceput, sfarsit = _interval(luna)
    ddf = _numara(cursor, _DDF_REV_N_SQL, inceput, sfarsit) + _numara(
        cursor, _DDF_REZ_N_SQL, inceput, sfarsit)
    return {
        "luna": f"{luna.year}-{luna.month:02d}",
        "eticheta": _eticheta(luna),
        "randuri_istoric": _numara(cursor, _ISTORIC_N_SQL, inceput, sfarsit),
        "angajamente": _numara(cursor, _ANGAJAMENTE_N_SQL, inceput, sfarsit),
        "rezervari": _numara(cursor, _REZERVARI_N_SQL, inceput, sfarsit),
        "receptii": _numara(cursor, _RECEPTII_N_SQL, inceput, sfarsit),
        "plati": _numara(cursor, _PLATI_N_SQL, inceput, sfarsit),
        "ddf": ddf,
        "ord": _numara(cursor, _ORD_N_SQL, inceput, sfarsit),
    }


def _motiv_blocare(info: dict) -> Optional[str]:
    """Textul pentru operator cand luna are documente emise; None cand se poate sterge."""
    if info["ddf"] == 0 and info["ord"] == 0:
        return None
    parti: List[str] = []
    if info["ddf"]:
        parti.append(f"{info['ddf']} document(e) de fundamentare (DDF)")
    if info["ord"]:
        parti.append(f"{info['ord']} ordonanțare(ări) (ORD)")
    return (f"Luna {info['eticheta']} nu se poate șterge: are " + " și ".join(parti) +
            ". Documentele emise nu se rup; ștergeți mai întâi documentele sau lăsați luna.")


@forexe_bp.route("/api/forexe/istoric/luna-ultima", methods=["GET"])
@require_session
def get_istoric_luna_ultima():
    """
    Ultima luna din istoric si ce ar sterge ea.

    Raspuns 200: { luna: null } cand istoricul e gol, altfel
      { luna: 'YYYY-MM', eticheta: 'MM.YYYY', randuri_istoric, angajamente, rezervari, receptii,
        plati, ddf, ord, blocata: bool, motiv: str|null }.
    """
    conn = None
    try:
        conn = get_kbot_connection(g.session.db_name)
        cursor = conn.cursor()
        luna = _ultima_luna(cursor)
        if luna is None:
            return _json_utf8({"luna": None}, 200)
        info = _descrie(cursor, luna)
        motiv = _motiv_blocare(info)
        info["blocata"] = motiv is not None
        info["motiv"] = motiv
        return _json_utf8(info, 200)
    except Exception as e:
        logger.error(f"[forexe.istoric.luna_ultima] {e}", exc_info=True)
        return _json_utf8({"error": f"Eroare la citirea ultimei luni din istoric: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()


@forexe_bp.route("/api/forexe/istoric/sterge-luna", methods=["POST"])
@require_session
def post_istoric_sterge_luna():
    """
    Sterge din istoric ULTIMA luna (vezi capul fisierului pentru reguli).

    Corp: { luna: 'YYYY-MM' } -- luna ceruta trebuie sa fie chiar ultima.
    Raspuns 200: { luna, eticheta, randuri_istoric, angajamente, rezervari, receptii, plati,
                   receptii_r_sterse } (cifrele de dinainte de stergere).
    409 cu `error` cand luna nu e ultima ori are DDF / ORD; 400 la corp gresit.
    """
    date_in = request.get_json(silent=True)
    luna_ceruta = _parseaza_luna(date_in.get("luna") if isinstance(date_in, dict) else None)
    if luna_ceruta is None:
        return _json_utf8({"error": "Luna lipsește sau nu are forma AAAA-LL."}, 400)

    conn = None
    try:
        conn = get_kbot_connection(g.session.db_name)
        cursor = conn.cursor()
        ultima = _ultima_luna(cursor)
        if ultima is None:
            return _json_utf8({"error": "Istoricul este gol: nu este nimic de șters."}, 409)
        if luna_ceruta != ultima:
            return _json_utf8({"error": (
                f"Se poate șterge doar ultima lună din istoric ({_eticheta(ultima)}), "
                f"nu {_eticheta(luna_ceruta)}.")}, 409)

        info = _descrie(cursor, ultima)
        motiv = _motiv_blocare(info)
        if motiv is not None:
            return _json_utf8({"error": motiv}, 409)

        inceput, sfarsit = _interval(ultima)
        cursor.execute(_STERGE_ISTORIC_SQL, (inceput, sfarsit))
        sterse = int(cursor.rowcount or 0)

        cursor.execute(_STERGE_RECEPTII_LUNA_SQL, (inceput, sfarsit))
        receptii_r_sterse = int(cursor.rowcount or 0)

        conn.commit()
        info["receptii_r_sterse"] = receptii_r_sterse
        logger.info("[forexe.istoric.sterge_luna] %s: luna=%s -> %s randuri de istoric, %s",
                    g.session.db_name, info["luna"], sterse, info)
        return _json_utf8(info, 200)
    except Exception as e:
        if conn is not None:
            conn.rollback()
        logger.error(f"[forexe.istoric.sterge_luna] {e}", exc_info=True)
        return _json_utf8({"error": f"Eroare la ștergerea lunii din istoric: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()
