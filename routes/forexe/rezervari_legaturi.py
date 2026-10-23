# routes/forexe/rezervari_legaturi.py
"""
Rezervari legate de o revizie care NU le cuprinde (slice 0114-26).

DE CE EXISTA
============
O revizie de fundamentare acopera o singura zi si un singur tip de operatie (generatorul din
ddf_edit.py ia cea mai veche zi nelegata, iar in ea cel mai mic tip: initiala, marire, micsorare).
Totusi o rezervare poate ajunge legata de o revizie care nu o cuprinde: pasul 8.1b din salvarea
reviziei lega ORICE rezervare al carei rand de istoric purta numarul reviziei in marcajul
«(IDREV: n)», fara sa verifice ca suma ei e in revizie. Rezultat (angajament AAB2FTFNE46): rezervarea
din 05.10 (+3000) a ramas legata de revizia din 30.06, `AreDDF = 1`, deci nu mai avea revizie de facut
si arborele de rezervari nu-i mai punea «+».

CE FACE
=======
  * `gaseste_legaturi_gresite`: pentru fiecare revizie, aduna rezervarile legate de ea. Daca ele nu au
    toate aceeasi cheie (zi + tip de operatie), cele cu cheia cea mai mica sunt cele ale reviziei, iar
    restul sunt straine. Un rand e raportat DOAR daca: suma rezervarilor legate nu se potriveste cu
    suma liniilor reviziei (FX_DDF_REV_SA.ValCur, pe clasificatie), IAR fara cele straine sumele se
    potrivesc pe toate clasificatiile. Altfel nu se spune nimic (nu ghicim).
  * `POST /api/forexe/rezervari/repara-legaturi` {cod, aplica}: proba sau scriere. Rezervarea straina
    redevine fara revizie (`IDREV = NULL`, `AreDDF = 0`), marcajul din istoric (`FX_Istoric.IDREV`)
    se goleste, iar copiile capturilor ei din revizia gresita se scot (la salvarea reviziei
    potrivite se fac din nou, din FX_Rezervarii_IMG). Nu atinge sumele, revizia sau alte rezervari.

Tot ce scrie rutica e intr-o singura tranzactie; proba nu scrie nimic.

NEVERIFICAT: nu a rulat pe MariaDB. Regula s-a gandit pe datele angajamentului AAB2FTFNE46 (revizia
114: SA = -31136, rezervari legate -31136 si +3000).
"""
import json
import logging
from collections import defaultdict
from datetime import date, datetime
from typing import Dict, List, Optional

from flask import request, g, current_app

from routes.auth.guard import require_session
from utils.database import get_kbot_connection

from . import forexe_bp

logger = logging.getLogger(__name__)

_EPS = 0.005
_MAX_DETALII = 60

_LEGATE_SQL = (
    "SELECT R.IDRZ, R.IDH, R.CodAngajament, R.CodIndicator, R.IdClsf, R.DataRezervare, R.IDREV, "
    "       R.EInitiala, R.EMarire, R.R_Initiala, R.R_Valoare, V.DataRev "
    "  FROM FX_Rezervari R JOIN FX_DDF_REV V ON V.IDREV = R.IDREV "
    " WHERE R.IDREV IS NOT NULL AND R.AreDDF = 1{filtru} "
    " ORDER BY R.IDREV, R.DataRezervare, R.IDRZ"
)
_SA_SQL = (
    "SELECT IDREV, IdClsf, SUM(ValCur) AS Suma FROM FX_DDF_REV_SA{filtru} GROUP BY IDREV, IdClsf"
)


def _json_utf8(payload, status):
    body = json.dumps(payload, ensure_ascii=False, default=str)
    return current_app.response_class(body, status=status, mimetype="application/json")


def _zi(v) -> date:
    """A DATE / DATETIME -> date (date.min when there is none)."""
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return date.min


def _valoare(r: dict) -> float:
    """What the reservation puts into a revision line: the initial value on an initial row, else R_Valoare
    (the same choice _SQL_GEN_REZERVARI makes in ddf_edit.py)."""
    return float((r["R_Initiala"] if r["EInitiala"] else r["R_Valoare"]) or 0)


def cheie_rezervare(r: dict):
    """(day, operation type) -- what one generated revision holds in common. Initial rows come first and
    count as one event whatever their dates (the generator puts them all on the last initial day)."""
    if r["EInitiala"]:
        return (date.min, 0)
    return (_zi(r["DataRezervare"]), 1 if r["EMarire"] else 2)


def gaseste_legaturi_gresite(cursor, cod: Optional[str] = None) -> Dict[str, List[dict]]:
    """
    Reservations linked to a revision that does not cover them. `cod` None = the whole database (the
    tree). Returns {CodAngajament: [ {idrz, idh, indicator, data, idrev, valoare, data_revizie}, ... ]}.
    The cursor is a DICTIONARY one.
    """
    filtru_r = " AND R.CodAngajament = %s" if cod else ""
    filtru_sa = " WHERE CodAngajament = %s" if cod else ""
    parametri = (cod,) if cod else ()

    cursor.execute(_LEGATE_SQL.format(filtru=filtru_r), parametri)
    legate = cursor.fetchall()
    if not legate:
        return {}

    cursor.execute(_SA_SQL.format(filtru=filtru_sa), parametri)
    sa: Dict[tuple, float] = {}
    clase_sa: Dict[int, set] = defaultdict(set)
    for r in cursor.fetchall():
        idrev = int(r["IDREV"])
        clasa = int(r["IdClsf"] or 0)
        sa[(idrev, clasa)] = float(r["Suma"] or 0)
        clase_sa[idrev].add(clasa)

    pe_revizie: Dict[int, List[dict]] = defaultdict(list)
    for r in legate:
        pe_revizie[int(r["IDREV"])].append(r)

    rezultat: Dict[str, List[dict]] = defaultdict(list)
    for idrev, randuri in pe_revizie.items():
        chei = {cheie_rezervare(r) for r in randuri}
        if len(chei) < 2:
            continue
        legitima = min(chei)
        bune = [r for r in randuri if cheie_rezervare(r) == legitima]
        straine = [r for r in randuri if cheie_rezervare(r) != legitima]
        if any(r["EInitiala"] for r in straine):
            continue

        clase = {int(r["IdClsf"] or 0) for r in randuri} | clase_sa[idrev]

        def suma(rs: List[dict], clasa: int) -> float:
            return sum(_valoare(r) for r in rs if int(r["IdClsf"] or 0) == clasa)

        # The revision must disagree with ALL its linked reservations ...
        if all(abs(suma(randuri, c) - sa.get((idrev, c), 0.0)) <= _EPS for c in clase):
            continue
        # ... and agree once the foreign ones are taken out. Otherwise we cannot say which is wrong.
        if any(abs(suma(bune, c) - sa.get((idrev, c), 0.0)) > _EPS for c in clase):
            continue

        for r in straine:
            rezultat[str(r["CodAngajament"])].append({
                "idrz": int(r["IDRZ"]),
                "idh": int(r["IDH"]) if r["IDH"] is not None else None,
                "indicator": r["CodIndicator"] or "",
                "data": _zi(r["DataRezervare"]),
                "idrev": idrev,
                "valoare": round(_valoare(r), 2),
                "data_revizie": _zi(r["DataRev"]) if r["DataRev"] is not None else None,
            })
    for lista in rezultat.values():
        lista.sort(key=lambda x: (x["data"], x["idrz"]))
    return dict(rezultat)


def sir_pentru_arbore(lista: List[dict]) -> Optional[str]:
    """«yyyy-MM-dd~indicator~idrev~valoare~yyyy-MM-dd(revizie)|...», oldest first; None = nothing wrong.
    No «%» in it, so no escaping through the driver (same reason as the other tree columns)."""
    if not lista:
        return None
    return "|".join(
        "~".join((
            x["data"].isoformat(),
            str(x["indicator"]).replace("~", " ").replace("|", " "),
            str(x["idrev"]),
            f"{x['valoare']:.2f}",
            x["data_revizie"].isoformat() if x["data_revizie"] else "",
        ))
        for x in lista)


def _tabela_exista(cursor, nume: str) -> bool:
    cursor.execute("SELECT COUNT(*) AS n FROM information_schema.TABLES "
                   "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s", (nume,))
    return int((cursor.fetchone() or {}).get("n") or 0) > 0


def _desprinde_capturi(cursor, idrz: int, idrev: int) -> int:
    """The revision's own copies of the pictures that belong ONLY to this reservation are removed (the right
    revision makes them again when it is saved, from FX_Rezervarii_IMG). Call AFTER the reservation was
    unlinked: the names another reservation of the revision still has are kept."""
    if not _tabela_exista(cursor, "FX_Rezervarii_IMG"):
        return 0
    cursor.execute("SELECT DISTINCT Nume FROM FX_Rezervarii_IMG WHERE IDRZ = %s", (idrz,))
    nume = [str(r["Nume"]) for r in cursor.fetchall() if r["Nume"]]
    ramase = []
    for n in nume:
        cursor.execute(
            "SELECT 1 AS x FROM FX_Rezervarii_IMG I JOIN FX_Rezervari R2 ON R2.IDRZ = I.IDRZ "
            " WHERE R2.IDREV = %s AND I.Nume = %s LIMIT 1", (idrev, n))
        if cursor.fetchone() is None:
            ramase.append(n)
    if not ramase:
        return 0
    sabloane = ", ".join(["%s"] * len(ramase))
    cursor.execute(
        f"SELECT IdRevAtt FROM FX_DDF_REV_ATT WHERE IDREV = %s AND PrtScr = 1 AND CaleFisier IN ({sabloane})",
        (idrev,) + tuple(ramase))
    ids = [int(r["IdRevAtt"]) for r in cursor.fetchall()]
    if not ids:
        return 0
    s2 = ", ".join(["%s"] * len(ids))
    if _tabela_exista(cursor, "FX_DDF_REV_ATT_IMG"):
        cursor.execute(f"DELETE FROM FX_DDF_REV_ATT_IMG WHERE IdRevAtt IN ({s2})", tuple(ids))
    cursor.execute(f"DELETE FROM FX_DDF_REV_ATT WHERE IdRevAtt IN ({s2})", tuple(ids))
    return len(ids)


def repara_legaturi(cursor, cod: str, aplica: bool) -> dict:
    """The repair itself; writes only when `aplica`."""
    gresite = gaseste_legaturi_gresite(cursor, cod).get(cod, [])
    rez = {"de_reparat": len(gresite), "reparate": 0, "capturi_scoase": 0,
           "detalii": [], "avertismente": []}
    for x in gresite[:_MAX_DETALII]:
        rez["detalii"].append({
            "data": x["data"].isoformat(), "indicator": x["indicator"], "idrev": x["idrev"],
            "valoare": x["valoare"],
            "data_revizie": x["data_revizie"].isoformat() if x["data_revizie"] else ""})
    if len(gresite) > _MAX_DETALII:
        rez["avertismente"].append(f"Se arată primele {_MAX_DETALII} din {len(gresite)} rezervări.")
    if not aplica:
        return rez

    for x in gresite:
        cursor.execute(
            "UPDATE FX_Rezervari SET IDREV = NULL, AreDDF = FALSE WHERE IDRZ = %s AND IDREV = %s",
            (x["idrz"], x["idrev"]))
        if x["idh"] is not None:
            cursor.execute("UPDATE FX_Istoric SET IDREV = NULL WHERE ID = %s AND IDREV = %s",
                           (x["idh"], x["idrev"]))
        rez["capturi_scoase"] += _desprinde_capturi(cursor, x["idrz"], x["idrev"])
        rez["reparate"] += 1
    return rez


@forexe_bp.route("/api/forexe/rezervari/repara-legaturi", methods=["POST"])
@require_session
def post_repara_legaturi():
    """
    Frees the reservations of an angajament that are linked to a revision which does not cover them, so
    they get their own revision (the «+» of the Rezervari tree comes back on them).

    Body:   { cod, aplica? } -- `aplica` missing / false = a PROBE (writes nothing).
    Answer: { cod, aplicat, de_reparat, reparate, capturi_scoase,
              detalii: [ {data, indicator, idrev, valoare, data_revizie} ], avertismente }.
    """
    date_in = request.get_json(silent=True)
    if not isinstance(date_in, dict) or str(date_in.get("cod") or "").strip() == "":
        return _json_utf8({"error": "Câmp lipsă: cod"}, 400)
    cod = str(date_in["cod"]).strip()
    aplica = bool(date_in.get("aplica", False))

    conn = None
    try:
        conn = get_kbot_connection(g.session.db_name)
        cursor = conn.cursor(dictionary=True)
        rezultat = repara_legaturi(cursor, cod, aplica)
        if aplica:
            conn.commit()
        else:
            conn.rollback()
        rezultat["cod"] = cod
        rezultat["aplicat"] = aplica
        logger.info("[forexe.rezervari.legaturi] %s: cod=%s aplica=%s -> de_reparat=%s reparate=%s capturi=%s",
                    g.session.db_name, cod, aplica, rezultat["de_reparat"], rezultat["reparate"],
                    rezultat["capturi_scoase"])
        return _json_utf8(rezultat, 200)
    except Exception as e:
        if conn is not None:
            conn.rollback()
        logger.error(f"[forexe.rezervari.legaturi] {e}", exc_info=True)
        return _json_utf8({"error": f"Eroare la repararea legăturilor rezervărilor: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()
