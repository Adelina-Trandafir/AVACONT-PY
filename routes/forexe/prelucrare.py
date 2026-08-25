# routes/forexe/prelucrare.py
"""
Ingestia FOREXE -- POST /api/forexe/prelucrare (felia 0048, plan docs/PLAN_ForexeIngest.md).

!!! CONDUCTA ESTE PARTIALA IN FELIA 0048-02 !!!
------------------------------------------------------------------------------------
Portati si activi: pasul 0 (tranzactia), pasul 1 (FX_Angajamente) si pasul 2
(FX_Indicatori), plus drumul dus-intors prin care operatorul alege unitatea cand o
clasificatie se potriveste cu mai multe.

NEPORTATI: pasii 3-8 -- istoric, rezervari, receptii, plati/incasari, marcarea
`Prelucrat` si `FX_Extrase`. Nu se scrie NIMIC in tabelele lor. Raspunsul spune asta
in `avertismente`, in romana, ca sa nu existe nicio rulare care sa para completa fara
sa fie. Clientul NU cheama inca ruta din fluxul de descarcare; se exercita din
DevHarness. Cand pasii 3-8 sosesc, avertismentul si nota asta pleaca impreuna cu ei.
------------------------------------------------------------------------------------

Scope: baza conectata ESTE unitatea (o baza MariaDB = o unitate), deci nu exista
parametru db_name / id_unitate -- baza vine din sesiune (g.session.db_name), exact ca
la toate celelalte rute /api/forexe/*. Un token nu poate tinti alta baza decat cea pe
care s-a logat.

CONTRACT
--------
Cerere:
    {
      "cod": "AAB37CNBK95",
      "workflow": "adlop - Prelucrare Completa.wfl",
      "moment": "2026-08-25T10:12:00",
      "scalari": { "DataAngajament": "10/02/2026", ... },
      "tabele":  { "TabelIndicatori_results": [ {...} ], ... },
      "alegeri": [ { "ss": "02E", "clsfe": "200101", "id_unitate": 76,
                     "retine": false } ]        <- optional; vezi mai jos
    }

Raspuns 200:
    { "cod": ..., "are": {...}, "scrise": {...}, "avertismente": [...] }

Raspuns 409 (o clasificatie se potriveste cu mai multe unitati):
    { "error": "<mesaj romanesc>", "reason": "ALEGERE_UNITATE", "cod": ...,
      "alegeri_necesare": [ { "ss", "clsfe", "clsf", "cod_indicator",
                              "indicatori": [...], "unitati": [ {...} ] } ] }
    TRANZACTIA E DERULATA INAPOI. Nu s-a scris nimic -- nici angajamentul din pasul 1.
    Clientul intreaba operatorul si trimite ACEEASI sarcina utila cu `alegeri` completat.

DE CE 409 SI NU 400: cererea nu e gresita. Serverului ii lipseste o informatie pe care
doar un om o are, exact ca in Access, unde `Obtine_IdUnitate_Din` deschidea formularul
modal `FX_Unitate`. `reason` e un cod-motiv stabil, acelasi tipar ca la 401-urile din
routes/auth/guard.py, si KBot.Api.ApiException il poarta deja ca `Reason`.

O SINGURA TRANZACTIE (D10): orice esec deruleaza inapoi tot. Nimic pe jumatate scris.
"""
import json
import logging

import mysql.connector
from flask import request, g, current_app

from routes.auth.guard import require_session
from utils.database import get_kbot_connection

from . import forexe_bp
from .prelucrare_helpers import (
    cod_ai,
    parse_amount,
    parse_data_zzllaaaa,
    split_sector_sursa_indicator,
)
from .prelucrare_unitate import (
    MSG_UNIT_CHOICE,
    REASON_UNIT_CHOICE,
    UnitChoiceRequired,
    UnitChoiceTableMissing,
    find_id_clsf_acc,
    normalize_supplied_choices,
    resolve_units,
)

logger = logging.getLogger(__name__)

# Numele tabelului din `tabele` care hraneste pasul 2.
TABLE_INDICATORI = "TabelIndicatori_results"

# Avertismentul care spune, in fiecare raspuns, ce NU s-a facut. Pleaca odata cu pasii.
WARNING_PARTIAL = (
    "Pașii 3–8 ai ingestiei nu sunt încă portați: istoricul, rezervările, recepțiile, "
    "plățile și încasările NU s-au scris. S-au scris doar angajamentul și indicatorii."
)


def _json_utf8(payload, status):
    """Raspuns JSON cu diacritice LITERALE UTF-8 (ensure_ascii=False), nu \\uXXXX.
    Acelasi ajutor ca in routes/forexe/pdf.py -- mesajele ajung la operator."""
    body = json.dumps(payload, ensure_ascii=False, default=str)
    return current_app.response_class(body, status=status, mimetype="application/json")


def _field(row: dict, *names):
    """
    Prima cheie care exista in `row`, dintre numele date.

    ScrapeTable normalizeaza numele coloanelor ("Indicator_ang"), FindInTable nu
    ("Indicator ang"). VBA-ul alege intre cele doua ortografii testand daca prima
    cheie a dictionarului contine un spatiu; aici incercam pur si simplu ambele,
    ceea ce da acelasi rezultat fara sa depinda de ordinea cheilor.
    """
    for n in names:
        if n in row:
            return row[n]
    return None


# ---------------------------------------------------------------------------
# Pasul 1 -- FX_Angajamente (Prelucrare_Angajament)
# ---------------------------------------------------------------------------
_ANG_EXISTS_SQL = "SELECT 1 FROM FX_Angajamente WHERE CodAngajament = %s"
_ANG_INSERT_SQL = (
    "INSERT INTO FX_Angajamente "
    "(CodAngajament, Descriere, Stare, DataCreare, DataDefinitivare, DC, Preluat) "
    "VALUES (%s, %s, %s, %s, %s, %s, 1)"
)


def _step1_angajament(cursor, cod: str, scalari: dict, db_name: str) -> int:
    """
    Port rand cu rand al lui `Prelucrare_Angajament`. Intoarce numarul de randuri scrise.

    Doua lucruri portate ca INTENTIE, nu litera:

      * Defectul `StareAngajament`. VBA-ul testeaza `Exists("StareAngajament")` dar
        citeste apoi `ExtraObject("Stare")` pe AMANDOUA ramurile, deci cheia
        `StareAngajament` nu ajunge niciodata in coloana. Planul (5.1) cere intentia.
      * `DC` si `Preluat` se pun DOAR la insert, si nu se ating la update -- exact ce
        face deja routes/forexe/angajamente.py. VBA-ul nu le atinge deloc, fiindca in
        Access baza deschisa era unitatea; aici `DC` spune din ce baza a venit randul.

    Datele: `DataAngajament` -> `DataCreare`, `DataInceputDerulare` ->
    `DataDefinitivare`. Confirmat citind functia (intrebarea deschisa 13.2 din plan).
    Formatul e ZZ/LL/AAAA, iar VBA-ul inlocuieste "/" cu "." inainte de parsare.
    """
    descriere = _field(scalari, "DescriereAngajament", "Descriere") or ""
    stare = _field(scalari, "StareAngajament", "Stare") or ""

    s_data_ang = _field(scalari, "DataAngajament") or ""
    s_data_der = _field(scalari, "DataInceputDerulare") or ""
    data_ang = parse_data_zzllaaaa(s_data_ang.replace("/", ".")) if s_data_ang else None
    data_der = parse_data_zzllaaaa(s_data_der.replace("/", ".")) if s_data_der else None

    cursor.execute(_ANG_EXISTS_SQL, (cod,))
    exists = cursor.fetchone() is not None

    if not exists:
        cursor.execute(_ANG_INSERT_SQL,
                       (cod, descriere, stare, data_ang, data_der, db_name))
        return 1

    # Update: VBA-ul scrie o coloana doar daca are ce pune in ea. Un camp gol NU
    # sterge ce e deja acolo -- de aceea nu e un simplu UPDATE cu toate coloanele.
    sets = []
    params = []
    if descriere != "":
        sets.append("Descriere = %s")
        params.append(descriere)
    if stare != "":
        sets.append("Stare = %s")
        params.append(stare)
    if data_ang is not None:
        sets.append("DataCreare = %s")
        params.append(data_ang)
    if data_der is not None:
        sets.append("DataDefinitivare = %s")
        params.append(data_der)
    if not sets:
        return 0
    params.append(cod)
    cursor.execute(
        "UPDATE FX_Angajamente SET " + ", ".join(sets) + " WHERE CodAngajament = %s",
        params,
    )
    return 1


# ---------------------------------------------------------------------------
# Pasul 2 -- FX_Indicatori (Prelucrare_Indicatori)
# ---------------------------------------------------------------------------
_IND_EXISTS_SQL = (
    "SELECT 1 FROM FX_Indicatori WHERE CodAngajament = %s AND CodIndicator = %s"
)
_IND_INSERT_SQL = (
    "INSERT INTO FX_Indicatori "
    "(CodAI, CodAngajament, CodIndicator, IdClsf, IndicatorFX, IdUnitate, SS, "
    " Prevedere_Bugetara_Initiala, Credit_Bugetar_Initial, Angajament_Legal, "
    " Credit_Bugetar_Definitiv, NrCrt) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
)
# Ramura Edit din VBA atinge EXACT aceste patru coloane si nimic altceva.
_IND_UPDATE_SQL = (
    "UPDATE FX_Indicatori SET Prevedere_Bugetara_Initiala = %s, "
    "Credit_Bugetar_Initial = %s, Angajament_Legal = %s, Credit_Bugetar_Definitiv = %s "
    "WHERE CodAngajament = %s AND CodIndicator = %s"
)


def _read_indicators(cod: str, rows) -> list:
    """
    Sparge fiecare rand de indicator in ce are nevoie rezolvarea unitatii.

    Randurile fara `Indicator_ang` se SAR -- `If CodInd = "" Then GoTo Next_Ind` in
    VBA. Un rand fara `Sector_Sursa_Indicator` insa nu se poate sari in tacere: fara
    el nu exista nici unitate, nici clasificatie, deci ridica.
    """
    out = []
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"{TABLE_INDICATORI}[{i}] nu este un obiect.")
        cod_ind = (_field(row, "Indicator_ang", "Indicator ang") or "").strip()
        if cod_ind == "":
            continue
        raw = _field(row, "Sector_Sursa_Indicator", "Sector - Sursa - Indicator")
        if raw is None or str(raw).strip() == "":
            raise ValueError(
                f"Indicatorul {cod_ind} nu are «Sector_Sursa_Indicator»; "
                f"unitatea și clasificația nu pot fi determinate."
            )
        ss, clsf_sal, clsf_e = split_sector_sursa_indicator(raw)
        out.append({
            "cod_indicator": cod_ind,
            "ss": ss,
            "clsf_sal": clsf_sal,
            "clsf_e": clsf_e,
            "clsf_raw": str(raw),
            "row": row,
        })
    return out


def _step2_indicatori(cursor, cod: str, indicators: list, units: dict,
                      warnings: list) -> int:
    """
    Scrie FX_Indicatori. Unitatile sunt DEJA rezolvate (`units`), deci aici nu se mai
    poate pune nicio intrebare -- de-asta rezolvarea e un pas separat, inaintea
    oricarei scrieri.

    `NrCrt` este un contor LOCAL care porneste de la 0 la fiecare apel si creste doar
    pentru randurile NOI -- exact ca variabila din VBA. Nu e un numar de ordine global
    si nu se recalculeaza pentru randurile existente.

    D18 (defect portat): `Receptii` si `Plati` nu se scriu NICIODATA pe calea asta.
    VBA-ul pazeste `dInd("BugetIndicator")` cu `Exists("BugetIndicatori")` -- cu «i»
    la coada -- deci testul e mereu fals si vRec/vPlati raman -1. Se pastreaza:
    coloanele au azi valorile puse de altcineva, iar o scriere «reparata» aici le-ar
    schimba tacut peste tot istoricul migrat.
    """
    written = 0
    nr_crt = 0
    for ind in indicators:
        row = ind["row"]
        key = (ind["ss"], ind["clsf_e"])
        id_unitate = units[key]

        id_clsf = find_id_clsf_acc(cursor, id_unitate, ind["clsf_sal"],
                                   ind["clsf_raw"], ind["cod_indicator"], warnings)

        prevedere = parse_amount(_field(row, "Credit_bugetar", "Credit bugetar"))
        credit_init = parse_amount(
            _field(row, "Total_credit_angajament", "Total credit angajament"))
        ang_legal = parse_amount(_field(row, "Angajament_legal", "Angajament legal"))
        credit_def = parse_amount(_field(
            row,
            "Credit_bugetar_rezervat_definitiv_an_curent",
            "Credit bugetar rezervat definitiv an curent"))

        cursor.execute(_IND_EXISTS_SQL, (cod, ind["cod_indicator"]))
        exists = cursor.fetchone() is not None

        if not exists:
            nr_crt += 1
            cursor.execute(_IND_INSERT_SQL, (
                cod_ai(cod, ind["cod_indicator"]),
                cod,
                ind["cod_indicator"],
                id_clsf,                 # None -> NULL, D19
                ind["clsf_sal"],         # IndicatorFX = clsfRaw din VBA
                id_unitate,
                ind["ss"],
                prevedere, credit_init, ang_legal, credit_def,
                nr_crt,
            ))
        else:
            # ATENTIE, comportament portat fidel si CONTRAINTUITIV: ramura Edit NU
            # rescrie `IdUnitate`, `IdClsf`, `SS` sau `NrCrt`. Deci daca indicatorul
            # exista deja, alegerea de unitate pe care tocmai a facut-o operatorul NU
            # ajunge in rand -- se aplica abia la un insert. Access facea exact la
            # fel: intreba prin `FX_Unitate` si apoi arunca raspunsul pe aceasta
            # ramura. Consemnat in worklog ca de decis; nu se schimba pe nevazute.
            cursor.execute(_IND_UPDATE_SQL, (
                prevedere, credit_init, ang_legal, credit_def,
                cod, ind["cod_indicator"],
            ))
        written += 1
    return written


# ---------------------------------------------------------------------------
# Ruta
# ---------------------------------------------------------------------------
@forexe_bp.route("/api/forexe/prelucrare", methods=["POST"])
@require_session
def post_prelucrare():
    data = request.get_json(silent=True) or {}
    cod = (data.get("cod") or "").strip()
    if cod == "":
        return _json_utf8({"error": "Lipsește «cod» (codul angajamentului)."}, 400)

    scalari = data.get("scalari") or {}
    tabele = data.get("tabele") or {}
    if not isinstance(scalari, dict) or not isinstance(tabele, dict):
        return _json_utf8({"error": "«scalari» și «tabele» trebuie să fie obiecte."}, 400)
    rows_indicatori = tabele.get(TABLE_INDICATORI) or []
    if not isinstance(rows_indicatori, list):
        return _json_utf8({"error": f"«{TABLE_INDICATORI}» trebuie să fie o listă."}, 400)

    try:
        supplied = normalize_supplied_choices(data.get("alegeri"))
    except ValueError as err:
        return _json_utf8({"error": str(err)}, 400)

    db_name = g.session.db_name
    un = g.session.username
    warnings = [WARNING_PARTIAL]

    conn = None
    cursor = None
    try:
        # get_kbot_connection deschide o conexiune catre O SINGURA baza de unitate.
        conn = get_kbot_connection(db_name)
        # start_transaction() opreste autocommit-ul. De aici incolo nimic nu e vizibil
        # altcuiva pana la conn.commit(); daca ridicam, conn.rollback() sterge tot.
        conn.start_transaction()
        cursor = conn.cursor(dictionary=True)

        scrise = {}
        scrise["FX_Angajamente"] = _step1_angajament(cursor, cod, scalari, db_name)

        indicators = _read_indicators(cod, rows_indicatori)
        # Rezolvarea TUTUROR unitatilor, inainte de orice scriere in FX_Indicatori.
        units = resolve_units(cursor, indicators, supplied, un, warnings)
        scrise["FX_Indicatori"] = _step2_indicatori(cursor, cod, indicators, units,
                                                    warnings)

        conn.commit()

        return _json_utf8({
            "cod": cod,
            # `are` poarta DOAR steagul pe care pasii portati il pot pune. Restul
            # steagurilor din plan (Istoric, Rezervari, Receptii, ReceptiiH, Plati,
            # Incasari) lipsesc INTENTIONAT: un `false` ar arata ca «s-a verificat si
            # nu era nimic», cand adevarul e «nu s-a rulat pasul».
            "are": {"Indicatori": scrise["FX_Indicatori"] > 0},
            "scrise": scrise,
            "avertismente": warnings,
        }, 200)

    except UnitChoiceRequired as err:
        # Intrebarea, nu o eroare. Tranzactia se deruleaza inapoi INTAI, ca sa nu ramana
        # nici angajamentul scris la pasul 1.
        if conn is not None:
            conn.rollback()
        logger.info("PRELUCRARE_ALEGERE_UNITATE dc=%s cod=%s perechi=%s",
                    db_name, cod, len(err.pending))
        return _json_utf8({
            "error": MSG_UNIT_CHOICE,
            "reason": REASON_UNIT_CHOICE,
            "cod": cod,
            "alegeri_necesare": err.pending,
        }, 409)

    except UnitChoiceTableMissing as err:
        if conn is not None:
            conn.rollback()
        logger.error("PRELUCRARE dc=%s cod=%s: %s", db_name, cod, err)
        return _json_utf8({"error": str(err)}, 500)

    except ValueError as err:
        # Sarcina utila e de nefolosit (clasificatie fara unitate, camp lipsa, alegere
        # imposibila). Mesajul e deja romanesc si spune care indicator.
        if conn is not None:
            conn.rollback()
        logger.warning("PRELUCRARE dc=%s cod=%s respins: %s", db_name, cod, err)
        return _json_utf8({"error": str(err)}, 400)

    except mysql.connector.Error as err:
        if conn is not None:
            conn.rollback()
        logger.error("PRELUCRARE dc=%s cod=%s eroare MariaDB: %s", db_name, cod, err,
                     exc_info=True)
        return _json_utf8({"error": "Eroare la scrierea în baza de date."}, 500)

    except Exception:
        # Fara `except: pass` nicaieri -- regula casei. Derulam inapoi, logam intreg
        # traseul si RIDICAM mai departe; Flask raspunde 500.
        if conn is not None:
            conn.rollback()
        logger.error("PRELUCRARE dc=%s cod=%s a esuat", db_name, cod, exc_info=True)
        raise

    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None and conn.is_connected():
            conn.close()
