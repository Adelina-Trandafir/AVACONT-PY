# routes/forexe/prelucrare.py
"""
Ingestia FOREXE -- POST /api/forexe/prelucrare (feliile 0048-02 si 0048-03).

Plan: docs/PLAN_ForexeIngest.md + PLAN_ForexeIngestSteps3to8 (felia 0048-03).
Fundament: docs/FUNDAMENT_Asociere_Receptii.md -- regulile F* si deciziile D-* de mai
jos vin de acolo si NU se re-deduc aici.

Scope: baza conectata ESTE unitatea (o baza MariaDB = o unitate), deci nu exista
parametru db_name / id_unitate -- baza vine din sesiune (g.session.db_name), exact ca
la toate celelalte rute /api/forexe/*. Un token nu poate tinti alta baza decat cea pe
care s-a logat.

===========================================================================
CONTRACTUL IN DOUA FAZE (D-B, si corectia C3 a deciziilor D1/D3 din planul parinte)
===========================================================================
O singura ruta, doua moduri. Motivul e in fundament, §1.4: istoricul FOREXE nu numeste
niciodata receptia (F4), trecerea automata poate aseza doar ULTIMUL instantaneu al unui
lant (F9), deci restul -- aproximativ (instantanee − receptii) per angajament (F10) --
ajung, prin constructie, la operator. Asta NU e o cale de exceptie: e rezultatul normal
al fiecarei descarcari. Nimic nu are voie sa ajunga in baza inainte ca omul sa fi
raspuns, fiindca o asociere gresita e tacuta si permanenta (F12).

FAZA UNU -- "mod": "propunere"
    Serverul ruleaza pasii 1..8 intr-o tranzactie, exact cum i-ar rula pe bune, si apoi
    DERULEAZA INAPOI NECONDITIONAT. Nu e o rulare pe uscat si nu e o ramura paralela
    strecurata prin codul pasilor: e acelasi drum, terminat cu rollback() in loc de
    commit(). Doua implementari ar aluneca una fata de alta si alunecarea nu s-ar vedea
    pana cand n-ar produce cifre gresite.

    Raspuns 200 cu tabloul construit: `receptii`, `instantanee`, `amprenta`.

FAZA DOI -- "mod": "salvare"
    ACELASI payload (clientul retrimite ce a pastrat in fisierul lui de decizii), plus
    `amprenta` inapoi si `decizii`. Serverul verifica amprenta, ruleaza aceiasi pasi,
    aplica deciziile la pasul 4c si COMMITE.

DE CE ACELASI PAYLOAD, OBLIGATORIU: `rand_istoric` din `decizii` este INDICELE randului
in `TabelIstoric` (F24), nu o cheie de baza de date. Id-urile atribuite in timpul
propunerii dispar la rollback si nu se intorc identice. Indicele e stabil prin
constructie -- dar numai daca ambele faze poarta acelasi payload. O re-descarcare intre
faze produce alt payload si trebuie sa porneasca o propunere noua.

MODUL IMPLICIT E "propunere". Un client care nu stie de faze primeste faza care NU
scrie nimic. Tacerea nu are voie sa insemne «salveaza».

===========================================================================
CELELALTE DOUA DRUMURI DUS-INTORS
===========================================================================
409 ALEGERE_UNITATE (felia 0048-02, neschimbat) -- o clasificatie se potriveste cu mai
multe unitati si serverului ii lipseste o informatie pe care doar un om o are. Se
declanseaza in faza intai. Un angajament poate deci avea nevoie de DOUA drumuri dus-intors
inainte ca operatorul sa vada formularul de asociere. Asa trebuie; nu se contopesc.

409 STARE_MODIFICATA (felia 0048-03) -- baza s-a schimbat intre propunere si salvare,
deci deciziile descriu un tablou care nu mai exista. Nimic nu se scrie.

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
from .prelucrare_pasi import (
    TABLE_ISTORIC,
    cere_lista,
    text_celula,
    TABLE_RECEPTII,
    read_indicatori,
    step3a_populeaza_istoric,
    step3b_prelucreaza_observatii,
    step3cd_populeaza_rezervari,
    step3e_asociaza_idrev,
    step4a_populeaza_receptii,
    step4b_receptii_prelucrare,
    step4d_calculeaza_dif,
    step5_plati_incasari,
    step7_actualizeaza_rezolvat,
    step8_actualizeaza_extrase,
)
from .prelucrare_asociere import (
    MSG_STARE_MODIFICATA,
    REASON_STARE_MODIFICATA,
    DecizieInvalida,
    amprenta,
    aplica_decizii,
    citeste_instantanee,
    citeste_receptii,
    marcheaza_reconstituirile_nesigure,
    normalizeaza_decizii,
    pas4c_automat,
)

logger = logging.getLogger(__name__)

TABLE_INDICATORI = "TabelIndicatori_results"

MOD_PROPUNERE = "propunere"
MOD_SALVARE = "salvare"


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
        unde = f"{TABLE_INDICATORI}[{i}]"
        cod_ind = text_celula(
            _field(row, "Indicator_ang", "Indicator ang"), unde, "Indicator_ang").strip()
        if cod_ind == "":
            continue
        raw = text_celula(
            _field(row, "Sector_Sursa_Indicator", "Sector - Sursa - Indicator"),
            unde, "Sector_Sursa_Indicator")
        if raw.strip() == "":
            raise ValueError(
                f"Indicatorul {cod_ind} nu are «Sector_Sursa_Indicator»; "
                f"unitatea și clasificația nu pot fi determinate."
            )
        # `BugetIndicator` e a DOUA coloana imbricata a sarcinii utile (prima e
        # `ListaReceptii.Detaliu`). Amandoua vin din acelasi tipar de workflow: un
        # `ForEachVar` al carui `collectFields` numeste un camp pe care un `ScrapeTable`
        # interior il scrie cu `saveTo` -- vezi «adlop - Prelucrare Completa.wfl».
        #
        # NIMIC NU O CITESTE, si asta e portat deliberat: VBA-ul pazeste
        # `dInd("BugetIndicator")` cu `Exists("BugetIndicatori")` -- cu «i» la coada --
        # deci testul e mereu fals (D18). Forma i se verifica totusi, fiindca daca ea
        # soseste ca text inseamna ca CLIENTUL aplatizeaza, iar clientul acela
        # aplatizeaza si `Detaliu`, pe care chiar il citim. E cel mai ieftin loc in care
        # se poate prinde regresia, si prinde intreaga sarcina utila deodata.
        if "BugetIndicator" in row and row["BugetIndicator"] is not None:
            cere_lista(row["BugetIndicator"],
                       f"{TABLE_INDICATORI}[{i}]", "BugetIndicator", gol_permis=True)

        ss, clsf_sal, clsf_e = split_sector_sursa_indicator(raw)
        out.append({
            "cod_indicator": cod_ind,
            "ss": ss,
            "clsf_sal": clsf_sal,
            "clsf_e": clsf_e,
            "clsf_raw": raw,
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

        # Cele patru celule de bani ale indicatorului. Scalare, si o cer explicit --
        # `parse_amount` peste o lista ar da tacut zero, adica un buget inventat.
        unde = f"{TABLE_INDICATORI}[{ind['cod_indicator']}]"
        prevedere = parse_amount(text_celula(
            _field(row, "Credit_bugetar", "Credit bugetar"), unde, "Credit_bugetar"))
        credit_init = parse_amount(text_celula(
            _field(row, "Total_credit_angajament", "Total credit angajament"),
            unde, "Total_credit_angajament"))
        ang_legal = parse_amount(text_celula(
            _field(row, "Angajament_legal", "Angajament legal"), unde, "Angajament_legal"))
        credit_def = parse_amount(text_celula(
            _field(row, "Credit_bugetar_rezervat_definitiv_an_curent",
                   "Credit bugetar rezervat definitiv an curent"),
            unde, "Credit_bugetar_rezervat_definitiv_an_curent"))

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
# Conducta -- IDENTICA in ambele faze
# ---------------------------------------------------------------------------
def _ruleaza_pasii(cursor, cod, scalari, tabele, db_name, un, supplied, warnings):
    """
    Pasii 1..5, 7 si 8, in ordinea impusa de cheile straine.

    Nu stie in ce faza e. Singurul lucru care difera intre faze -- pasul 4c -- se
    intampla la apelant, dupa ce functia asta se termina.

    Intoarce (scrise, are, index_la_id).
    """
    scrise = {}
    are = {}

    # --- pasul 1 -----------------------------------------------------------
    scrise["FX_Angajamente"] = _step1_angajament(cursor, cod, scalari, db_name)

    # --- pasul 2 -----------------------------------------------------------
    rows_indicatori = tabele.get(TABLE_INDICATORI) or []
    if not isinstance(rows_indicatori, list):
        raise ValueError(f"«{TABLE_INDICATORI}» trebuie să fie o listă.")
    indicators = _read_indicators(cod, rows_indicatori)
    # Rezolvarea TUTUROR unitatilor, inainte de orice scriere in FX_Indicatori.
    units = resolve_units(cursor, indicators, supplied, un, warnings)
    scrise["FX_Indicatori"] = _step2_indicatori(cursor, cod, indicators, units, warnings)
    are["Indicatori"] = scrise["FX_Indicatori"] > 0

    # Indicatorii se recitesc ACUM din baza: pasii 3-5 au nevoie de `IdClsf`,
    # `IdUnitate`, `Clsf` si `CodSSI` asa cum sunt ele DUPA pasul 2, nu cum au sosit.
    indicatori = read_indicatori(cursor, cod, warnings)

    # --- pasul 3 -----------------------------------------------------------
    randuri_istoric = tabele.get(TABLE_ISTORIC) or []
    if not isinstance(randuri_istoric, list):
        raise ValueError(f"«{TABLE_ISTORIC}» trebuie să fie o listă.")

    index_la_id, ids_noi = step3a_populeaza_istoric(cursor, cod, randuri_istoric,
                                                    indicatori)
    scrise["FX_Istoric"] = len(ids_noi)
    are["Istoric"] = len(ids_noi) > 0

    # VBA: daca nu e nimic nou in istoric, tot pasul 3 se opreste. NU e o eroare.
    # Pasul 4 merge insa mai departe -- `ListaReceptii` poate purta schimbari chiar
    # si cand istoricul nu are randuri noi, iar D-F cere ca instantaneele ramase
    # neasezate din rulari anterioare sa fie din nou in joc.
    if ids_noi:
        step3b_prelucreaza_observatii(cursor, cod, indicatori)
        rez = step3cd_populeaza_rezervari(cursor, cod, True, warnings)
        rez += step3cd_populeaza_rezervari(cursor, cod, False, warnings)
        step3e_asociaza_idrev(cursor, cod)
        scrise["FX_Rezervari"] = rez
        are["Rezervari"] = rez > 0
    else:
        scrise["FX_Rezervari"] = 0
        are["Rezervari"] = False

    # --- pasul 4a / 4b -----------------------------------------------------
    antete = step4a_populeaza_receptii(cursor, cod, indicatori)
    scrise["FX_Receptii_H"] = antete
    are["ReceptiiH"] = antete > 0

    randuri_receptii = tabele.get(TABLE_RECEPTII) or []
    if not isinstance(randuri_receptii, list):
        raise ValueError(f"«{TABLE_RECEPTII}» trebuie să fie o listă.")
    r_scrise, rhr_scrise = step4b_receptii_prelucrare(cursor, cod, randuri_receptii,
                                                      indicatori)
    scrise["FX_Receptii_R"] = r_scrise
    scrise["FX_Receptii_RHR"] = rhr_scrise
    are["Receptii"] = (r_scrise + rhr_scrise) > 0

    # --- pasul 5 -----------------------------------------------------------
    plati, are_p, are_i = step5_plati_incasari(cursor, cod, indicatori, warnings)
    scrise["FX_Plati"] = plati
    are["Plati"] = are_p
    are["Incasari"] = are_i

    # --- pasul 7 -----------------------------------------------------------
    # In faza «propunere» asta se deruleaza inapoi cu tot restul, deci o propunere nu
    # marcheaza NICIODATA istoricul ca prelucrat -- exact ce face rularea repetabila.
    step7_actualizeaza_rezolvat(cursor, ids_noi)

    # --- pasul 8 -----------------------------------------------------------
    # NECONDITIONAT, la coada, in aceeasi tranzactie -- exact ca originalul Access.
    # Nu se pazeste cu niciun steag `are` si nu se sare cand pasul 5 n-a scris nimic:
    # `FX_Extrase` poate purta randuri ramase in urma din rulari mai vechi, iar
    # filtrul `CodAI IS NULL` face ca fiecare trecere sa recupereze tot restantul.
    scrise["FX_Extrase"] = step8_actualizeaza_extrase(cursor)

    return scrise, are, index_la_id


def _pas4d_pe_receptiile_atinse(cursor, cod: str) -> None:
    """
    Pasul 4d peste fiecare receptie a angajamentului care are un lant.

    Access cheama `FX_CalculeazaDIF_Receptii_Tmp` exact aici -- DUPA asociere (4c) --
    fiindca inainte de ea un instantaneu nu apartine inca niciunei receptii si nu are
    lant in care sa i se calculeze diferenta.

    IESIREA LUI NU DECIDE NIMIC. `DIFH` se calculeaza LOCAL, de noi, dupa ce
    instantaneul a fost deja asezat; nu vine din payload si nu poate deci sa spuna daca
    o salvare a schimbat ceva. (Regula F20 a fundamentului, care propunea `DIFH = 0` ca
    marcaj de salvare-fara-schimbare, e RETRASA pe 26.08.2026 chiar din motivul asta.)
    Cifrele de aici hranesc eticheta plutitoare din Recepții si atat.
    """
    cursor.execute(
        "SELECT DISTINCT IDRR FROM FX_Receptii_H "
        "WHERE CodAngajament = %s AND IDRR IS NOT NULL", (cod,))
    for r in cursor.fetchall():
        step4d_calculeaza_dif(cursor, cod, int(r["IDRR"]))


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

    # Implicit «propunere»: un client care nu stie de faze primeste faza care NU scrie.
    mod = (data.get("mod") or MOD_PROPUNERE).strip()
    if mod not in (MOD_PROPUNERE, MOD_SALVARE):
        return _json_utf8(
            {"error": f"«mod» «{mod}» nu este cunoscut (permise: "
                      f"{MOD_PROPUNERE}, {MOD_SALVARE})."}, 400)

    try:
        supplied = normalize_supplied_choices(data.get("alegeri"))
        decizii = None
        if mod == MOD_SALVARE:
            if "decizii" not in data:
                return _json_utf8(
                    {"error": "Modul «salvare» cere «decizii»."}, 400)
            decizii = normalizeaza_decizii(data.get("decizii"))
            if not (data.get("amprenta") or "").strip():
                return _json_utf8(
                    {"error": "Modul «salvare» cere «amprenta» din propunere."}, 400)
    except DecizieInvalida as err:
        return _json_utf8({"error": str(err)}, 400)
    except ValueError as err:
        return _json_utf8({"error": str(err)}, 400)

    db_name = g.session.db_name
    un = g.session.username
    warnings = []

    conn = None
    cursor = None
    try:
        # get_kbot_connection deschide o conexiune catre O SINGURA baza de unitate.
        conn = get_kbot_connection(db_name)
        # start_transaction() opreste autocommit-ul. De aici incolo nimic nu e vizibil
        # altcuiva pana la conn.commit(); daca ridicam, conn.rollback() sterge tot.
        conn.start_transaction()
        cursor = conn.cursor(dictionary=True)

        # AMPRENTA SE IA INAINTE DE ORICE SCRIERE, in ambele faze. Luata la coada fazei
        # intai ar descrie starea scrisa -- care e apoi derulata inapoi -- si faza a doua
        # nu s-ar potrivi niciodata.
        amprenta_acum = amprenta(cursor, cod)
        if mod == MOD_SALVARE and data["amprenta"].strip() != amprenta_acum:
            conn.rollback()
            logger.info("PRELUCRARE_STARE_MODIFICATA dc=%s cod=%s", db_name, cod)
            return _json_utf8({
                "error": MSG_STARE_MODIFICATA,
                "reason": REASON_STARE_MODIFICATA,
                "cod": cod,
            }, 409)

        scrise, are, index_la_id = _ruleaza_pasii(
            cursor, cod, scalari, tabele, db_name, un, supplied, warnings)

        receptii = citeste_receptii(cursor, cod)
        instantanee = citeste_instantanee(cursor, cod, index_la_id, warnings)

        if mod == MOD_PROPUNERE:
            # --- PASUL 4c, FAZA UNU: sugestii, nicio scriere -------------------
            sugestii = pas4c_automat(cursor, cod, instantanee)
            nedecise = [i for i in instantanee if i["idrh"] not in sugestii]
            if nedecise:
                warnings.append(
                    f"{len(nedecise)} instantanee nu au primit nicio sugestie automată "
                    f"și trebuie așezate de operator."
                )

            # --- pasul 4d, si in faza intai --------------------------------
            # Ruleaza pe lanturile care EXISTA deja (rulari anterioare), fiindcă faza
            # intai nu aseaza nimic. Se deruleaza inapoi cu tot restul. E aici ca cele
            # doua faze sa parcurga acelasi drum: o ramura care ruleaza doar la salvare
            # e o ramura care nu se testeaza decat la salvare.
            _pas4d_pe_receptiile_atinse(cursor, cod)

            # `FX_Extrase` NU se raporteaza in propunere. Pasul 8 chiar a rulat -- si s-a
            # derulat inapoi cu restul -- dar el nu e o propunere despre care operatorul
            # are ceva de decis: e o legatura mecanica intre extrase si plati. Un contor
            # in tabloul de decizii ar cere un raspuns care nu i se cere.
            scrise_propuse = {k: v for k, v in scrise.items() if k != "FX_Extrase"}

            corp = {
                "cod": cod,
                "faza": MOD_PROPUNERE,
                "amprenta": amprenta_acum,
                "receptii": [
                    {k: v for k, v in r.items() if k != "nr_crt"} for r in receptii
                ],
                "instantanee": [{
                    "rand_istoric": i["rand_istoric"],
                    "data_h": i["data_h"],
                    "descriere": i["descriere"],
                    "total": i["total"],
                    "stergere": i["stergere"],
                    "sugestie_idrr": sugestii.get(i["idrh"]),
                    "sugestie_automata": i["idrh"] in sugestii,
                    "linii": i["linii"],
                } for i in instantanee],
                "are": are,
                # `scrise` raporteaza ce S-AR FI scris. Tranzactia se deruleaza inapoi
                # imediat dupa; contorul arata a rezultat, dar descrie o rulare anulata.
                "scrise": scrise_propuse,
                "avertismente": warnings,
            }
            # DERULARE INAPOI NECONDITIONATA. Nu e o cale de eroare: e chiar contractul.
            conn.rollback()
            return _json_utf8(corp, 200)

        # --- PASUL 4c, FAZA DOI: se aplica deciziile, se ignora automatul ------
        numarat = aplica_decizii(cursor, cod, decizii, instantanee, receptii, warnings)
        scrise["asocieri"] = numarat

        # F28: doua sau mai multe reconstituiri pe acelasi angajament fac gruparea
        # imposibil de verificat (F27). Se marcheaza TOATE, se numara si cele ramase din
        # rulari mai vechi, si marcajul nu se sterge niciodata.
        scrise["reconstituiri_nesigure"] = marcheaza_reconstituirile_nesigure(
            cursor, cod, warnings)

        # --- pasul 4d, per receptie atinsa ------------------------------------
        _pas4d_pe_receptiile_atinse(cursor, cod)

        conn.commit()
        return _json_utf8({
            "cod": cod,
            "faza": MOD_SALVARE,
            "are": are,
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

    except DecizieInvalida as err:
        # Deciziile nu descriu tabloul (un instantaneu lipsa, o data care nu se
        # potriveste, un lant care nu se inchide). Mesajul e deja romanesc si spune care.
        if conn is not None:
            conn.rollback()
        logger.warning("PRELUCRARE dc=%s cod=%s decizii respinse: %s", db_name, cod, err)
        return _json_utf8({"error": str(err)}, 400)

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
