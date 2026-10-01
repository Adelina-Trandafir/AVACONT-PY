# routes/forexe/rezervari_reanaliza.py
"""
Reanalizarea rezervarilor unui angajament, din FX_Istoric, FARA redescarcare.

DE CE EXISTA
============
La trecerea din Access unele randuri de istoric au ajuns cu `Rez_Ord`, `TipRand`,
`Val_Rezervare_Ant` sau `Val_Rezervare_Dif` gresite: o rezervare nu si-a recunoscut la
vremea ei rezervarile anterioare, deci valoarea ei curenta (`FX_Rezervari.R_Valoare`) nu
tine cont de cele vechi. Istoricul are insa toate randurile, deci lantul se poate reface.

CE FACE, EXACT
==============
Reia peste TOT istoricul prelucrat al angajamentului aceiasi doi pasi ai ingestiei:
  * pasul 3a, doar partea `Rez_Ord` (`step3a_populeaza_istoric`): ordinalul de ordonare;
  * pasul 3b, `_calculeaza_val_rezervare_dif`: `TipRand`, `Val_Rezervare_Ant`,
    `Val_Rezervare_Dif`,
dar de la zero (nicio valoare anterioara data), in ordinea (zi, Rez_Ord, ID). Apoi:
  * `FX_Istoric`: se scriu doar coloanele care difera;
  * `FX_Rezervari` (dupa `IDH`, doar randurile care NU sunt initiale): `R_Anterioara` si
    `R_Valoare` iau valorile noi, iar `EMarire` / `EMicsorare` urmeaza semnul lui
    `R_Valoare`, ca la pasul 3d. Randurile initiale (`R_Valoare = Val_AngLeg`) nu se ating.

Ce NU face: nu insereaza si nu sterge randuri de `FX_Rezervari`, nu atinge `IDREV` /
`AreDDF`. Un rand al carui `TipRand` s-ar muta intre initiala/definitiva si influenta se
SEMNALEAZA (avertisment), nu se rescrie -- ar cere alt rand de rezervare.

DOUA MODURI, ACEEASI PLIMBARE
=============================
`aplica = false` (implicit) e o PROBA: numara si arata ce s-ar schimba, fara sa scrie.
`aplica = true` scrie, intr-o singura tranzactie.

NEVERIFICAT: ca ID-ul crescator reproduce ordinea in care FOREXE a dat randurile la
randurile migrate din Access; ca bucla se reia dintr-o singura bucata (ingestia o reia la
fiecare descarcare, cu steagurile resetate).
"""
import json
import logging
from datetime import date
from typing import Dict, List, Optional

from flask import request, g, current_app

from routes.auth.guard import require_session
from utils.database import get_kbot_connection

from . import forexe_bp
from .prelucrare_helpers import (
    cod_ai,
    fx_extract_cod_indicator,
    is_rand_contract_row,
)

logger = logging.getLogger(__name__)

_TIPURI_LANT = ("Rez_Initiala", "Rez_Definitiva", "Rez_Influenta", "Rez_Zero")
_MAX_DETALII = 60
_EPS = 0.005

_ISTORIC_SQL = (
    "SELECT ID, DataFX, Descriere, Observatii, CodIndicator, CodAI, Val_AngLeg, "
    "       Rez_Ord, TipRand, Val_Rezervare_Ant, Val_Rezervare_Dif "
    "FROM FX_Istoric WHERE CodAngajament = %s AND Prelucrat = 1 ORDER BY ID"
)
_ORDINE_SQL = "SELECT CodAI, NrCrt FROM FX_Indicatori WHERE CodAngajament = %s"
_REZ_SQL = (
    "SELECT IDRZ, IDH, EInitiala, R_Anterioara, R_Valoare, EMarire, EMicsorare "
    "FROM FX_Rezervari WHERE CodAngajament = %s AND IDH IS NOT NULL"
)
_UPD_ISTORIC_SQL = (
    "UPDATE FX_Istoric SET Rez_Ord = %s, TipRand = %s, Val_Rezervare_Ant = %s, "
    "Val_Rezervare_Dif = %s WHERE ID = %s"
)
_UPD_REZ_SQL = (
    "UPDATE FX_Rezervari SET R_Anterioara = %s, R_Valoare = %s, "
    "EMarire = %s, EMicsorare = %s WHERE IDRZ = %s"
)


def _json_utf8(payload, status):
    body = json.dumps(payload, ensure_ascii=False)
    return current_app.response_class(body, status=status, mimetype="application/json")


def _diferit(a, b) -> bool:
    return abs(float(a or 0) - float(b or 0)) > _EPS


def _zi(v) -> str:
    try:
        return v.strftime("%d.%m.%Y")
    except AttributeError:
        return str(v or "")


def _rez_ord(rand: dict, cod: str, ordine: Dict[str, int], multi: int):
    """Ramurile lui 3a, in aceeasi ordine. Intoarce (rez_ord | None, multi nou)."""
    descr = (rand["Descriere"] or "").lower()
    obs = rand["Observatii"] or ""
    if "angajament nou" in descr:
        return 0, multi
    if "initial ->" in descr:
        return 100, 100
    if "definitivare ->" in descr:
        return 1000, 1000
    if obs[:14].upper() == "RAND CONTRACT:":
        ci = fx_extract_cod_indicator(obs)
        return ordine.get(cod_ai(cod, ci or ""), 0) + multi, multi
    return None, multi


def reanalizeaza_rezervari(cursor, cod: str, aplica: bool) -> dict:
    """Plimbarea propriu-zisa; cursorul e pe DICTIONAR. Scrie doar daca `aplica`."""
    avertismente: List[str] = []
    detalii: List[dict] = []
    rez = {
        "randuri_istoric": 0, "istoric_de_corectat": 0, "istoric_corectat": 0,
        "rezervari_de_corectat": 0, "rezervari_corectate": 0,
        "tip_schimbat": 0, "detalii": detalii, "avertismente": avertismente,
    }

    cursor.execute(_ISTORIC_SQL, (cod,))
    randuri = cursor.fetchall()
    if not randuri:
        return rez

    cursor.execute(_ORDINE_SQL, (cod,))
    ordine = {str(r["CodAI"]): int(r["NrCrt"] or 0) for r in cursor.fetchall()}

    # --- Rez_Ord de la zero, in ordinea ID ------------------------------------
    multi = 0
    candidate: List[dict] = []
    for r in randuri:
        ord_nou, multi = _rez_ord(r, cod, ordine, multi)
        if ord_nou is None:
            continue
        candidate.append(dict(r, RezOrdNou=ord_nou))
    rez["randuri_istoric"] = len(candidate)
    candidate.sort(key=lambda r: (r["DataFX"].date() if r["DataFX"] else date.min,
                                  r["RezOrdNou"], int(r["ID"])))

    # --- 3b de la zero ---------------------------------------------------------
    last_ang: Dict[str, float] = {}
    are_definitiva = set()
    r_init = False
    r_def = False
    noi: Dict[int, dict] = {}

    for r in candidate:
        rid = int(r["ID"])
        obs = r["Observatii"] or ""
        descr = r["Descriere"] or ""
        cod_ind = (r["CodIndicator"] or "").strip()
        tip, ant, dif = None, r["Val_Rezervare_Ant"], r["Val_Rezervare_Dif"]

        if descr == "sume nemodificate":
            tip = "Rez_Zero"
        elif is_rand_contract_row(obs) and cod and cod_ind:
            k = f"{cod}|{cod_ind}"
            val_curent = float(r["Val_AngLeg"] or 0)
            val_anterior = float(last_ang.get(k, 0))
            if r_init:
                tip, ant, dif = "Rez_Initiala", 0, 0
            elif r_def:
                tip, ant, dif = "Rez_Definitiva", 0, 0
                are_definitiva.add(k)
            elif k in are_definitiva:
                dif = round(val_curent - val_anterior, 2)
                tip = "Rez_Zero" if (val_curent == 0 and val_anterior == 0) else "Rez_Influenta"
                ant = val_anterior
            else:
                tip, ant, dif = "Rez_Influenta", 0, val_curent
                are_definitiva.add(k)
            last_ang[k] = val_curent
        else:
            if descr == "Angajament nou.":
                r_init, r_def = True, False
                tip = "Rez_Initiala+"
            elif "definitivare" in descr and "derulare" not in descr:
                r_init, r_def = False, True
                tip = "Rez_Definitiva+"
            elif "derulare" in descr:
                r_init, r_def = False, False
                tip = "Rez_Derulare+"
            else:
                tip = r["TipRand"]
        noi[rid] = {"rez_ord": r["RezOrdNou"], "tip": tip, "ant": ant, "dif": dif, "rand": r}

    # --- FX_Rezervari existente -----------------------------------------------
    cursor.execute(_REZ_SQL, (cod,))
    rez_dupa_idh = {int(x["IDH"]): x for x in cursor.fetchall()}

    for rid, n in noi.items():
        r = n["rand"]
        istoric_diferit = (
            (r["Rez_Ord"] is None or int(r["Rez_Ord"]) != n["rez_ord"])
            or (r["TipRand"] or "") != (n["tip"] or "")
            or _diferit(r["Val_Rezervare_Ant"], n["ant"])
            or _diferit(r["Val_Rezervare_Dif"], n["dif"])
        )
        if istoric_diferit:
            rez["istoric_de_corectat"] += 1
            if aplica:
                cursor.execute(_UPD_ISTORIC_SQL, (
                    n["rez_ord"], n["tip"], n["ant"], n["dif"], rid))
                rez["istoric_corectat"] += 1

        rz = rez_dupa_idh.get(rid)
        if rz is None:
            continue
        vechi_tip = r["TipRand"] or ""
        if n["tip"] in _TIPURI_LANT and vechi_tip in _TIPURI_LANT:
            era_initial = vechi_tip in ("Rez_Initiala", "Rez_Definitiva")
            e_initial = n["tip"] in ("Rez_Initiala", "Rez_Definitiva")
            if era_initial != e_initial:
                rez["tip_schimbat"] += 1
                avertismente.append(
                    f"Rândul din {_zi(r['DataFX'])} ({r['CodIndicator']}) trece de la "
                    f"«{vechi_tip}» la «{n['tip']}»; rezervarea lui nu s-a atins (ar cere "
                    "alt rând de rezervare)."
                )
                continue
        if bool(rz["EInitiala"]):
            continue
        val_noua = float(n["dif"] or 0)
        ant_noua = float(n["ant"] or 0)
        if _diferit(rz["R_Valoare"], val_noua) or _diferit(rz["R_Anterioara"], ant_noua):
            rez["rezervari_de_corectat"] += 1
            if len(detalii) < _MAX_DETALII:
                detalii.append({
                    "data": _zi(r["DataFX"]), "indicator": r["CodIndicator"],
                    "valoare_veche": float(rz["R_Valoare"] or 0), "valoare_noua": val_noua,
                    "anterioara_veche": float(rz["R_Anterioara"] or 0),
                    "anterioara_noua": ant_noua,
                })
            if aplica:
                cursor.execute(_UPD_REZ_SQL, (
                    ant_noua, val_noua,
                    1 if val_noua > 0 else 0, 1 if val_noua < 0 else 0, int(rz["IDRZ"])))
                rez["rezervari_corectate"] += 1

    if rez["rezervari_de_corectat"] > _MAX_DETALII:
        avertismente.append(
            f"Se arată primele {_MAX_DETALII} din {rez['rezervari_de_corectat']} rezervări de corectat.")
    return rez


@forexe_bp.route("/api/forexe/rezervari/reanaliza", methods=["POST"])
@require_session
def post_rezervari_reanaliza():
    """
    Reanalizeaza rezervarile unui angajament din istoric.

    Corp: { cod, aplica? } -- `aplica` lipsa/false = PROBA (nu scrie nimic).
    Raspuns 200: { cod, aplicat, randuri_istoric, istoric_de_corectat, istoric_corectat,
                   rezervari_de_corectat, rezervari_corectate, tip_schimbat,
                   detalii: [ {data, indicator, valoare_veche, valoare_noua,
                               anterioara_veche, anterioara_noua} ], avertismente }.
    """
    date = request.get_json(silent=True)
    if not isinstance(date, dict) or str(date.get("cod") or "").strip() == "":
        return _json_utf8({"error": "Câmp lipsă: cod"}, 400)
    cod = str(date["cod"]).strip()
    aplica = bool(date.get("aplica", False))

    conn = None
    try:
        conn = get_kbot_connection(g.session.db_name)
        cursor = conn.cursor(dictionary=True)
        rezultat = reanalizeaza_rezervari(cursor, cod, aplica)
        if aplica:
            conn.commit()
        else:
            conn.rollback()
        rezultat["cod"] = cod
        rezultat["aplicat"] = aplica
        logger.info("[forexe.rezervari.reanaliza] %s: cod=%s aplica=%s -> %s",
                    g.session.db_name, cod, aplica,
                    {k: v for k, v in rezultat.items() if k not in ("detalii", "avertismente")})
        return _json_utf8(rezultat, 200)
    except Exception as e:
        if conn is not None:
            conn.rollback()
        logger.error(f"[forexe.rezervari.reanaliza] {e}", exc_info=True)
        return _json_utf8({"error": f"Eroare la reanalizarea rezervărilor: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()
