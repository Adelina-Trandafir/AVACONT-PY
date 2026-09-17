# routes/forexe/receptii_refacere.py
"""
Refacerea instantaneelor (FX_Receptii_H) si a liniilor lor (FX_Receptii) din istoric,
pentru un angajament -- felia 0062.

DE CE EXISTA
============
Operatorul a constatat (15.09.2026) ca in Access `FX_Receptii_H` isi pierde IDRH / IDRR,
iar la migrare randurile ajung pe MariaDB cu `IDRR IS NULL` -- sau nu ajung deloc. La fel
liniile din `FX_Receptii`, care raman fara `IDRH`. Sursa de adevar care NU se pierde e
`FX_Istoric`: fiecare instantaneu si fiecare linie poarta `IDH` = `FX_Istoric.ID`, iar
pasul 4a al ingestiei (`prelucrare_pasi.step4a_populeaza_receptii`) construieste H si
liniile chiar din randurile de istoric. Ruta de aici re-joaca ACELASI algoritm, dar peste
randurile DEJA prelucrate, si scrie numai ce lipseste.

CE FACE, EXACT
==============
Merge peste randurile de istoric de receptie ale angajamentului (`Prelucrat = 1`,
`TipRand` contine «Receptie»), in ordinea `ID`, cu acelasi tampon ca 4a:

  * un rand cu `(activ:true)` in Observatii e un ANTET. Daca exista deja un
    `FX_Receptii_H` cu acest `IDH`, se refoloseste `IDRH`-ul lui; altfel se INSEREAZA
    (cu `IDRR NULL` -- asezarea pe o receptie e treaba editorului de legaturi, nu a
    refacerii). `NrCrt` continua de la maximul angajamentului.
  * a row that is a line by `este_linie_receptie` (names an indicator, or has
    `Val_Receptie <> 0` -- F31: zero lines are kept) waits for its header. At the header,
    each buffered line:
      - lipseste din `FX_Receptii` (dupa `IDH`)      -> se INSEREAZA sub antet;
      - exista, dar cu `IDRH IS NULL` (orfana)         -> se RELEAGA la antet (UPDATE);
      - exista si e legata                             -> se lasa in pace.

  Because the criterion is shared with 4a, this route is also the BACKFILL for F31: the
  zero lines that earlier ingests dropped are "missing by IDH" and get inserted under
  their existing header, with the chain's DIF recomputed.

Randurile cu `Prelucrat = 0` NU se ating: ele apartin urmatoarei descarcari, iar pasul 4a
le va construi atunci. Daca le-am construi aici, 4a le-ar insera a doua oara (nu verifica
IDH-ul), deci doua H pentru acelasi rand de istoric.

UN ANGAJAMENT SAU TOATA BAZA
============================
Cu `cod` se reface un singur angajament. Cu `toate: true` (si fara `cod`) se trec TOATE
angajamentele bazei care au randuri de istoric de receptie, pe rand, fiecare in TRANZACTIA
LUI: un angajament care cade (indicator lipsa la FK, orice) se consemneaza in `erori` si nu
trage dupa el nici ce s-a scris inaintea lui, nici ce vine dupa. Raspunsul e un sumar
(totaluri peste toate) plus `detalii` DOAR pentru angajamentele la care s-a gasit ceva de
facut sau de spus -- o baza cu 900 de angajamente curate nu intoarce 900 de zerouri.

DOUA MODURI, ACEEASI PLIMBARE
=============================
`aplica = false` (implicit) e o PROBA: numara ce s-ar scrie, fara sa scrie nimic. Clientul
o arata operatorului si cere confirmarea. `aplica = true` scrie, intr-o singura
tranzactie. Acelasi cod merge in ambele moduri, ca proba sa nu poata minti despre ce va
face aplicarea.

DIF-URILE
=========
O linie adaugata sau relegata sub un instantaneu care e DEJA asezat pe o receptie
schimba lantul acelei receptii, deci `DIF`/`DIFC` pe linii si `DIFH`/`DIFHC` pe antete se
recalculeaza cu `step4d_calculeaza_dif`, pentru fiecare IDRR atins. Un instantaneu nou
nu are IDRR, deci nu are lant de recalculat -- il va avea cand operatorul il aseaza.

CE SE SEMNALEAZA, NU SE INGHITE
===============================
  * linii ramase fara antet la sfarsit (tamponul nevidat);
  * o linie al carei indicator nu e in `FX_Indicatori` (4a RIDICA aici; refacerea o sare
    si o spune, ca restul angajamentului sa se poata reface);
  * un instantaneu existent care are linii cu `IDH IS NULL`: nu se poate judeca ce ii
    lipseste, deci liniile lui nu se ating si se spune de ce;
  * un antet FARA nicio linie care nu e stergere (F32, `is_header_only_snapshot`): e o
    eroare a vechii aplicatii Access, nu un instantaneu. Nu se insereaza (4a nu il
    insereaza nici el); daca exista deja in baza, ramane acolo dar niciun cititor nu il
    mai vede (`SNAPSHOT_COUNTS_SQL`), si se spune ca a fost gasit.
Toate ajung in `avertismente`, in romana, pentru operator.
"""
import json
import logging
from typing import Dict, List, Optional, Set

from flask import request, g, current_app

from routes.auth.guard import require_session
from utils.database import get_kbot_connection

from . import forexe_bp
from .prelucrare_helpers import (
    este_linie_receptie,
    extract_text_between,
    fx_receptii_istoric_get_indent,
    is_header_only_snapshot,
    is_stergere_receptie,
)
from .prelucrare_pasi import (
    _H_INSERT_SQL,
    _INDICATORI_VAZUTI_SQL,
    _MAX_NRCRT_H_SQL,
    _REC_INSERT_SQL,
    read_indicatori,
    step4d_calculeaza_dif,
)

logger = logging.getLogger(__name__)

# Aceleasi coloane ca `_RECEPTII_ISTORIC_SQL` din pasul 4a, dar peste randurile DEJA
# prelucrate: refacerea nu e o ingestie, e o completare a ce ar fi trebuit sa existe.
_ISTORIC_PRELUCRAT_SQL = (
    "SELECT ID, HASH, CodAI, CodAngajament, CodIndicator, IdClsf, DataFX, TipRand, "
    "       Descriere, Observatii, Val_Receptie "
    "FROM FX_Istoric "
    "WHERE CodAngajament = %s AND Prelucrat = 1 "
    "  AND INSTR(COALESCE(TipRand,''), 'Receptie') <> 0 "
    "ORDER BY ID"
)
# Instantaneele care exista deja, cu ancora lor de istoric si receptia pe care stau.
_H_EXISTENTE_SQL = (
    "SELECT IDRH, IDRR, IDH FROM FX_Receptii_H WHERE CodAngajament = %s"
)
# Liniile care exista deja: `IDH` spune ce rand de istoric le-a nascut, `IDRH` daca mai
# au un antet. Se cer TOATE liniile angajamentului, si cele orfane (IDRH NULL).
_LINII_EXISTENTE_SQL = (
    "SELECT IDR, IDRH, IDH FROM FX_Receptii WHERE CodAngajament = %s"
)
_RELEAGA_LINIE_SQL = "UPDATE FX_Receptii SET IDRH = %s WHERE IDR = %s"


def _json_utf8(payload, status):
    """Raspuns JSON cu diacritice LITERALE (ensure_ascii=False)."""
    body = json.dumps(payload, ensure_ascii=False)
    return current_app.response_class(body, status=status, mimetype="application/json")


def _zi(v) -> str:
    """DataFX -> 'zz.ll.aaaa' pentru mesajele operatorului. None -> gol."""
    if v is None:
        return ""
    try:
        return v.strftime("%d.%m.%Y")
    except AttributeError:
        return str(v)


def refa_receptii(cursor, cod: str, aplica: bool) -> dict:
    """
    Plimbarea propriu-zisa. Intoarce numaratoarea si avertismentele; scrie numai daca
    `aplica` e True. Cursorul trebuie sa fie pe DICTIONAR (r["ID"]), ca in 4a.

    Cheile rezultatului (contract cu clientul VB, ASCII pe ambele parti ale firului):
      antete_lipsa, linii_lipsa, linii_orfane  -- ce s-a gasit de facut;
      antete_scrise, linii_scrise, linii_relegate -- ce s-a si scris (0 la proba);
      linii_fara_antet, linii_sarite -- ce nu s-a putut aseza;
      avertismente -- lista de propozitii pentru operator.
    """
    avertismente: List[str] = []
    rezultat = {
        "antete_lipsa": 0, "linii_lipsa": 0, "linii_orfane": 0,
        "antete_scrise": 0, "linii_scrise": 0, "linii_relegate": 0,
        "linii_fara_antet": 0, "linii_sarite": 0,
        "receptii_recalculate": [],
        "avertismente": avertismente,
    }

    cursor.execute(_ISTORIC_PRELUCRAT_SQL, (cod,))
    randuri = cursor.fetchall()
    if not randuri:
        return rezultat

    indicatori = read_indicatori(cursor, cod, avertismente)

    # --- ce exista deja -----------------------------------------------------
    cursor.execute(_H_EXISTENTE_SQL, (cod,))
    h_dupa_idh: Dict[int, dict] = {}
    for r in cursor.fetchall():
        if r["IDH"] is not None:
            h_dupa_idh[int(r["IDH"])] = {
                "idrh": int(r["IDRH"]),
                "idrr": int(r["IDRR"]) if r["IDRR"] is not None else None,
            }

    cursor.execute(_LINII_EXISTENTE_SQL, (cod,))
    linie_dupa_idh: Dict[int, dict] = {}
    # Antetele care au linii fara `IDH`: la ele nu se poate spune ce lipseste.
    h_cu_linii_fara_idh: Set[int] = set()
    for r in cursor.fetchall():
        if r["IDH"] is None:
            if r["IDRH"] is not None:
                h_cu_linii_fara_idh.add(int(r["IDRH"]))
            continue
        linie_dupa_idh[int(r["IDH"])] = {
            "idr": int(r["IDR"]),
            "idrh": int(r["IDRH"]) if r["IDRH"] is not None else None,
        }

    cursor.execute(_INDICATORI_VAZUTI_SQL, (cod,))
    vazuti = {str(r["CodIndicator"]) for r in cursor.fetchall()
              if r["CodIndicator"] is not None}

    cursor.execute(_MAX_NRCRT_H_SQL, (cod,))
    row = cursor.fetchone()
    nr_crt = int((row or {}).get("MaxNr") or 0) + 1

    # Receptiile ale caror lanturi se schimba (linii adaugate / relegate sub un H asezat).
    idrr_atinse: Set[int] = set()
    # Antetele deja semnalate pentru linii fara IDH, ca mesajul sa apara o singura data.
    semnalate: Set[int] = set()

    tampon: List[dict] = []
    # F32: line rows seen since the previous header, INCLUDING the ones skipped for an
    # unknown indicator -- those still make the header a real snapshot.
    linii_vazute = 0

    for r in randuri:
        obs = r["Observatii"] or ""
        idh = int(r["ID"])

        if "(activ:true)" in obs:
            # --- ANTET -----------------------------------------------------
            este_stergere = is_stergere_receptie(r["Descriere"])
            existent = h_dupa_idh.get(idh)
            antet_gol = is_header_only_snapshot(este_stergere, linii_vazute)
            linii_vazute = 0
            if antet_gol:
                # F32: the total row alone. Never inserted; an existing one is left in
                # place (every reader filters it out) and named, so the operator knows
                # the old app left it there.
                avertismente.append(
                    f"Antetul de recepție din {_zi(r['DataFX'])} (rând de istoric {idh}, "
                    f"total {float(r['Val_Receptie'] or 0):.2f}) nu are nicio linie pe "
                    "indicator; este o eroare a vechii aplicații și se ignoră peste tot."
                    + (f" Instantaneul IDRH {existent['idrh']} rămâne în bază, nevăzut."
                       if existent is not None else "")
                )
                continue
            if existent is not None:
                idrh: Optional[int] = existent["idrh"]
                idrr = existent["idrr"]
            else:
                rezultat["antete_lipsa"] += 1
                idrr = None
                idrh = None
                if aplica:
                    descriere = extract_text_between(obs, "Receptie: ", ",")
                    cursor.execute(_H_INSERT_SQL, (
                        idh, nr_crt, str(r["CodAngajament"]), r["DataFX"],
                        float(r["Val_Receptie"] or 0), descriere,
                        1 if este_stergere else 0,
                    ))
                    idrh = int(cursor.lastrowid)
                    rezultat["antete_scrise"] += 1
                nr_crt += 1

            if idrh is not None and idrh in h_cu_linii_fara_idh:
                # Nu se poate judeca ce ii lipseste: liniile lui se lasa in pace.
                if idrh not in semnalate:
                    semnalate.add(idrh)
                    avertismente.append(
                        f"Instantaneul din {_zi(r['DataFX'])} (IDRH {idrh}) are linii fără "
                        "ancoră de istoric (IDH gol); liniile lui nu s-au atins."
                    )
                tampon = []
                continue

            for linie in tampon:
                stare = linie_dupa_idh.get(linie["IDH"])
                if stare is None:
                    rezultat["linii_lipsa"] += 1
                    if aplica:
                        ci = linie["CodIndicator"]
                        cursor.execute(_REC_INSERT_SQL, (
                            idrh, linie["IDH"], linie["IdClsf"], linie["CodSSI"],
                            linie["Clsf"], linie["IdUnitate"], linie["CodAI"],
                            linie["CodAngajament"], ci, linie["Data"], linie["Valoare"],
                            linie["ValoareOrig"], linie["HASH"],
                            "VECHI" if ci in vazuti else "NOU",
                        ))
                        vazuti.add(ci)
                        rezultat["linii_scrise"] += 1
                        if idrr is not None:
                            idrr_atinse.add(idrr)
                elif stare["idrh"] is None:
                    rezultat["linii_orfane"] += 1
                    if aplica:
                        cursor.execute(_RELEAGA_LINIE_SQL, (idrh, stare["idr"]))
                        rezultat["linii_relegate"] += 1
                        if idrr is not None:
                            idrr_atinse.add(idrr)
                # altfel: linia exista si e legata -- nu se atinge.
            tampon = []

        elif este_linie_receptie(r):
            # --- LINIE (F31: a zero value is still a line) ---------------------
            linii_vazute += 1
            ind = indicatori.get(str(r["CodAI"] or ""))
            if ind is None:
                # 4a RIDICA aici. La refacere, o linie fara indicator nu are voie sa
                # opreasca tot angajamentul: se sare si se spune.
                rezultat["linii_sarite"] += 1
                avertismente.append(
                    f"Rândul de istoric {idh} ({_zi(r['DataFX'])}, indicator "
                    f"{r['CodIndicator']}) nu are indicatorul în FX_Indicatori; linia lui "
                    "nu s-a putut reface."
                )
                continue
            tampon.append({
                "IDH": idh,
                "IdClsf": r["IdClsf"],
                "CodSSI": ind["CodSSI"],
                "Clsf": ind["Clsf"],
                "IdUnitate": ind["IdUnitate"],
                "CodAI": str(r["CodAI"]),
                "CodAngajament": str(r["CodAngajament"]),
                "CodIndicator": str(r["CodIndicator"]),
                "Data": r["DataFX"],
                "Valoare": float(r["Val_Receptie"] or 0),
                "ValoareOrig": float(r["Val_Receptie"] or 0),
                # Acelasi hash ca in 4a: al patrulea argument e `Clsf`, nu `CodSSI`.
                "HASH": fx_receptii_istoric_get_indent(
                    str(r["CodAngajament"]), str(r["CodIndicator"]), r["DataFX"],
                    ind["Clsf"] or "", float(r["Val_Receptie"] or 0)),
            })

    if tampon:
        rezultat["linii_fara_antet"] = len(tampon)
        avertismente.append(
            f"{len(tampon)} linii de recepție de la sfârșitul istoricului nu au încă un "
            "antet; rămân în istoric până sosește el."
        )

    if aplica:
        for idrr in sorted(idrr_atinse):
            step4d_calculeaza_dif(cursor, cod, idrr)
        rezultat["receptii_recalculate"] = sorted(idrr_atinse)

    return rezultat


# Angajamentele care AU ce sa li se refaca: cele cu randuri de istoric de receptie deja
# prelucrate. Un angajament fara astfel de randuri n-ar produce decat zerouri.
_ANGAJAMENTE_CU_ISTORIC_SQL = (
    "SELECT DISTINCT CodAngajament FROM FX_Istoric "
    "WHERE CodAngajament IS NOT NULL AND Prelucrat = 1 "
    "  AND INSTR(COALESCE(TipRand,''), 'Receptie') <> 0 "
    "ORDER BY CodAngajament"
)

# Cheile numarate care se aduna in totalurile sumarului.
_CHEI_NUMARATE = ("antete_lipsa", "linii_lipsa", "linii_orfane",
                  "antete_scrise", "linii_scrise", "linii_relegate",
                  "linii_fara_antet", "linii_sarite")


def _are_ceva_de_spus(rez: dict) -> bool:
    """Un angajament intra in `detalii` daca a gasit ceva de facut sau a avut ce semnala."""
    return any(rez[k] for k in _CHEI_NUMARATE) or bool(rez["avertismente"])


def refa_toate(conn, cursor, aplica: bool) -> dict:
    """
    Refacerea peste TOATE angajamentele bazei, fiecare in tranzactia lui.

    `conn` e conexiunea (pentru commit / rollback per angajament), `cursor` cursorul ei pe
    dictionar. Intoarce:
      angajamente      -- cate s-au parcurs;
      cu_lipsuri       -- la cate s-a gasit ceva de facut (lipsa sau orfane);
      totaluri         -- suma pe cheile numarate, peste toate;
      detalii          -- [ {cod, ...rezultat} ] doar pentru cele cu ceva de facut / de spus;
      erori            -- [ {cod, eroare} ] pentru cele care au cazut (rollback, se merge mai
                          departe).
    """
    cursor.execute(_ANGAJAMENTE_CU_ISTORIC_SQL)
    coduri = [str(r["CodAngajament"]) for r in cursor.fetchall()]

    totaluri = {k: 0 for k in _CHEI_NUMARATE}
    detalii: List[dict] = []
    erori: List[dict] = []
    cu_lipsuri = 0

    for cod in coduri:
        try:
            rez = refa_receptii(cursor, cod, aplica)
            if aplica:
                conn.commit()
            else:
                conn.rollback()
        except Exception as e:  # noqa: BLE001 -- consemnat per angajament, nu inghitit
            conn.rollback()
            logger.error("[forexe.receptii.refacere] cod=%s a căzut: %s", cod, e,
                         exc_info=True)
            erori.append({"cod": cod, "eroare": str(e)})
            continue

        for k in _CHEI_NUMARATE:
            totaluri[k] += rez[k]
        if rez["antete_lipsa"] or rez["linii_lipsa"] or rez["linii_orfane"]:
            cu_lipsuri += 1
        if _are_ceva_de_spus(rez):
            detalii.append(dict(rez, cod=cod))

    return {
        "angajamente": len(coduri),
        "cu_lipsuri": cu_lipsuri,
        "totaluri": totaluri,
        "detalii": detalii,
        "erori": erori,
    }


class CerereInvalida(ValueError):
    """Corpul cererii nu spune ce sa se refaca. Devine 400 in oricare dintre cele doua rute."""


def citeste_cererea(date) -> tuple:
    """
    (cod, toate, aplica) din corpul JSON, sau `CerereInvalida`. Comuna celor doua rute
    (sesiune / cheie de administrare), ca regulile sa nu alunece una fata de alta.
    """
    if not isinstance(date, dict):
        raise CerereInvalida("Corp JSON lipsă sau nevalid.")
    cod = str(date.get("cod") or "").strip()
    toate = bool(date.get("toate", False))
    if cod == "" and not toate:
        raise CerereInvalida("Câmp lipsă: cod (sau toate: true)")
    if cod != "" and toate:
        raise CerereInvalida("Fie «cod», fie «toate: true» — nu amândouă.")
    return cod, toate, bool(date.get("aplica", False))


def executa_refacerea(db_name: str, cod: str, toate: bool, aplica: bool) -> dict:
    """
    Deschide baza K-BOT `db_name`, ruleaza refacerea (un angajament sau toata baza) si
    intoarce corpul raspunsului. Tranzactiile: una pe angajament la `toate`, una singura
    la `cod`. Ridica mai departe orice eroare de baza; apelantul o transforma in 500.

    Comuna celor doua rute: cea de sesiune (baza vine din token) si cea de administrare
    (baza vine in corp, sub X-Api-Key). Aceeasi plimbare, acelasi raspuns.
    """
    conn = None
    try:
        conn = get_kbot_connection(db_name)
        # Cursor pe DICTIONAR: plimbarea citeste randurile pe nume de coloana, ca 4a.
        cursor = conn.cursor(dictionary=True)

        if toate:
            sumar = refa_toate(conn, cursor, aplica)
            sumar["toate"] = True
            sumar["aplicat"] = aplica
            logger.info("[forexe.receptii.refacere] %s: TOATE aplica=%s -> %s angajamente, "
                        "%s cu lipsuri, %s erori, totaluri=%s",
                        db_name, aplica, sumar["angajamente"], sumar["cu_lipsuri"],
                        len(sumar["erori"]), sumar["totaluri"])
            return sumar

        rezultat = refa_receptii(cursor, cod, aplica)
        if aplica:
            conn.commit()
        else:
            conn.rollback()

        rezultat["cod"] = cod
        rezultat["aplicat"] = aplica
        logger.info("[forexe.receptii.refacere] %s: cod=%s aplica=%s -> %s",
                    db_name, cod, aplica, {k: v for k, v in rezultat.items()
                                           if k != "avertismente"})
        return rezultat
    except Exception:
        if conn is not None:
            conn.rollback()
        raise
    finally:
        if conn is not None:
            conn.close()


@forexe_bp.route("/api/forexe/receptii/refacere", methods=["POST"])
@require_session
def post_receptii_refacere():
    """
    Reface instantaneele si liniile lipsa, din istoric -- pentru un angajament sau
    pentru toata baza sesiunii.

    Corp: { cod, aplica? }  sau  { toate: true, aplica? }.
    `aplica` lipsa sau false = PROBA (nu scrie nimic).
    Raspuns 200, un angajament:
        { cod, aplicat, antete_lipsa, linii_lipsa, linii_orfane, antete_scrise,
          linii_scrise, linii_relegate, linii_fara_antet, linii_sarite,
          receptii_recalculate, avertismente }.
    Raspuns 200, toata baza:
        { toate: true, aplicat, angajamente, cu_lipsuri, totaluri: {...}, detalii: [...],
          erori: [ {cod, eroare} ] }  -- vezi `refa_toate`.

    Sora ei fara sesiune, pentru intretinere: POST /api/admin/receptii/refacere
    (X-Api-Key + `db_name` in corp), in routes/admin.py.
    """
    try:
        cod, toate, aplica = citeste_cererea(request.get_json(silent=True))
    except CerereInvalida as e:
        return _json_utf8({"error": str(e)}, 400)

    db_name = g.session.db_name
    try:
        return _json_utf8(executa_refacerea(db_name, cod, toate, aplica), 200)
    except Exception as e:
        logger.error(f"[forexe.receptii.refacere] {e}", exc_info=True)
        return _json_utf8({"error": f"Eroare la refacerea recepțiilor: {e}"}, 500)
