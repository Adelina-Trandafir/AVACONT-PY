# routes/forexe/prelucrare_pasi.py
"""
Pasii 3, 4a, 4b, 4d, 5, 7 si 8 ai ingestiei FOREXE (feliile 0048-03 si 0048-03-completare).

Pasii 0-2 (tranzactia, FX_Angajamente, FX_Indicatori) stau in prelucrare.py, de unde
au venit in felia 0048-02. Pasul 4c (asocierea) sta in prelucrare_asociere.py, fiindca
e SINGURUL loc in care cele doua faze -- propunere si salvare -- se comporta diferit.
Tot ce e aici ruleaza IDENTIC in ambele faze; propunerea nu are nicio ramura proprie.

DE CE CONTEAZA CA E ACELASI COD. Faza «propunere» ruleaza pipeline-ul intreg intr-o
tranzactie si apoi o DERULEAZA INAPOI neconditionat. Nu e o rulare pe uscat, nu e o
ramura paralela: e exact acelasi drum, terminat cu rollback() in loc de commit().
Doua implementari ar aluneca una fata de alta, iar alunecarea nu s-ar vedea pana cand
n-ar produce cifre gresite pe care nimeni nu le mai compara cu nimic.

CUM SE CITESTE FISIERUL (operatorul a cerut explicit: comentariile spun CE face un
rand, nu doar de ce; cititorul stie SQL si VB.NET, nu idiomuri Python):

  * `cursor.execute(SQL, (a, b))`  ruleaza SQL cu parametri. `%s` e locul in care
                                   driverul pune valoarea, deja escapata. Niciodata
                                   f-string in SQL.
  * `cursor.lastrowid`             cheia AUTO_INCREMENT tocmai atribuita -- echivalentul
                                   lui `rs.Bookmark = rs.LastModified` urmat de `rs!ID`.
  * `dict` / `set`                 Scripting.Dictionary / un dictionar fara valori.
  * `list.append(x)`               Collection.Add.
  * `for x in lista:`              For Each.
  * `enumerate(lista)`             For Each care da si indexul, pornind de la 0.
  * `raise ValueError(...)`        Err.Raise. Niciodata inghitit aici.

FIDELITATE. Unde VBA-ul are un defect, se reproduce COMPORTAMENTUL OBSERVABIL si se
spune in comentariu, fiindca datele deja aflate in MariaDB au fost produse de acel
comportament. Abaterile sunt numite una cate una, la fata locului.
"""
import json
import logging
from typing import Dict, List, Optional, Tuple

from .marcaj import LOCK_IDR, LOCK_IDRH, consuma_lacatul, id_marcaj_utilizabil
from .prelucrare_helpers import (
    cod_ai,
    este_linie_receptie,
    extract_number_after_label,
    extract_numar_rev,
    extract_obs_value,
    extract_rezervare_definitiva,
    extract_text_after_label,
    extract_marcaj,
    extract_text_between,
    fara_marcaj,
    fx_extract_cod_indicator,
    fx_receptii_istoric_get_indent,
    get_hash_for_row_istoric,
    get_tip_rand,
    is_header_only_snapshot,
    is_rand_contract_row,
    is_stergere_receptie,
    SNAPSHOT_COUNTS_SQL,
    null_if_empty,
    parse_amount,
    parse_english_date,
    parse_timp_istoric,
)

logger = logging.getLogger(__name__)

# Numele tabelului de istoric din `tabele`. Fara sufixul `_results`, spre deosebire de
# celelalte doua -- asa il trimite executorul (planul parinte 5.4).
TABLE_ISTORIC = "TabelIstoric"
TABLE_RECEPTII = "ListaReceptii_results"


# ===========================================================================
# Indicatorii angajamentului, cititi o singura data
# ===========================================================================
# Access: ClasificatiiG INNER JOIN FX_Indicatori ON (IdUnitate) AND (IdClsf).
#
# Pe MariaDB clasificatia se ia prin SUBINTEROGARE SCALARA cu LIMIT 1, nu prin JOIN:
# nomenclatorul are duplicate reale pe (IdClsfAcc, IdUnitate) -- MAPARE_NOMENCLATOARE.md
# 3.2 -- si un JOIN ar multiplica randurile de indicator. E tiparul pe care rutele de
# CITIRE il folosesc deja (routes/forexe/receptii.py); aici e la fel de sigur, fiindca
# duplicatele difera doar prin `IDClsf`, iar `IDClsf` nu se scrie nicaieri (D7).
#
# `CodSSI` NU exista ca si coloana pe MariaDB (MAPARE_NOMENCLATOARE.md rand 261: «Memo,
# 01A650402100101. No column on the target»). E `SS` lipit de `ClsfSal`, exact ce produce
# si FX_Receptii_NormalizeSSI din textul brut al payload-ului. Se calculeaza, nu se citeste.
#
# ABATERE DE LA VBA, deliberata si consemnata: Access folosea INNER JOIN, deci un
# indicator a carui clasificatie lipseste CADEA din recordset si randurile lui de istoric
# ramaneau tacut fara CodAI. Aici indicatorul RAMANE, cu `Clsf`/`CodSSI` NULL, si se emite
# un avertisment. E aceeasi alegere ca D19 din felia 0048-02 (o clasificatie neredusa
# lasa coloana goala si avertizeaza, nu sterge randul), si respecta regula casei: fara
# no-op-uri tacute.
_INDICATORI_SQL = (
    "SELECT I.CodAI, I.CodIndicator, I.IdClsf, I.IdUnitate, I.SS, I.IndicatorFX, "
    "  (SELECT C.Clsf FROM Clasificatii C "
    "    WHERE C.IdUnitate = I.IdUnitate AND C.IdClsfAcc = I.IdClsf LIMIT 1) AS Clsf, "
    "  (SELECT CONCAT(C.SS, C.ClsfSal) FROM Clasificatii C "
    "    WHERE C.IdUnitate = I.IdUnitate AND C.IdClsfAcc = I.IdClsf LIMIT 1) AS CodSSI, "
    "  I.NrCrt "
    "FROM FX_Indicatori I WHERE I.CodAngajament = %s ORDER BY I.NrCrt"
)


def read_indicatori(cursor, cod: str, warnings: List[str]) -> Dict[str, dict]:
    """
    Indicatorii angajamentului, indexati dupa CodAI.

    Se citesc O SINGURA DATA si se tin in memorie: VBA-ul deschidea un snapshot si facea
    FindFirst pe el la fiecare rand de istoric, ceea ce aici ar fi un SELECT per rand.
    Un dictionar da acelasi raspuns fara drumul la server.
    """
    cursor.execute(_INDICATORI_SQL, (cod,))
    out: Dict[str, dict] = {}
    for row in cursor.fetchall():
        out[str(row["CodAI"])] = dict(row)
        if row["Clsf"] is None:
            warnings.append(
                f"Indicatorul {row['CodIndicator']} nu are clasificație în nomenclator "
                f"(IdClsf {row['IdClsf']}, unitatea {row['IdUnitate']}); rândurile lui "
                f"de istoric rămân fără «Clsf» și fără «CodSSI»."
            )
    return out


# ===========================================================================
# PASUL 3a -- FX_Istoric_Populeaza_Istoric
# ===========================================================================
_ISTORIC_EXISTENTE_SQL = (
    "SELECT ID, DataFX, Utilizator, Descriere, Observatii "
    "FROM FX_Istoric WHERE CodAngajament = %s"
)
_ISTORIC_MAXORD_SQL = (
    "SELECT MAX(Rez_Ord) AS MaxOrd FROM FX_Istoric WHERE CodAngajament = %s"
)
_ISTORIC_INSERT_SQL = (
    "INSERT INTO FX_Istoric "
    "(CodAngajament, Descriere, Observatii, Utilizator, HASH, Rez_Ord, DataFX, Prelucrat) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, 0)"
)


def _cheie_istoric(data_fx, utilizator, descriere, observatii) -> tuple:
    """
    Identitatea unui rand de istoric: cele PATRU campuri din care Access construia hash-ul.

    D9 din planul parinte: deduplicarea merge pe cheia naturala, NU pe sirul de hash.
    Motivul e ca hash-urile deja aflate in MariaDB au fost calculate de invelisul BCrypt
    din Access, si doua lucruri despre acel sir sunt necunoscute din export -- codificarea
    pe octeti (ANSI vs UTF-8, conteaza in clipa in care o Observatie poarta «a») si daca
    hexul iesea cu majuscule. Daca hash-ul Python difera cu un octet, FIECARE rand vazut
    inainte pare nou si angajamentul se dubleaza singur la prima re-descarcare. Tacut.

    Un `tuple` e o lista imutabila; Python poate folosi tuple drept cheie de dictionar sau
    membru de `set`, ceea ce face testul «l-am mai vazut?» o cautare in tabela de dispersie.
    """
    return (
        data_fx,
        (utilizator or "").strip(),
        (descriere or "").strip(),
        (observatii or "").strip(),
    )


def step3a_populeaza_istoric(cursor, cod: str, randuri: List[dict],
                             indicatori: Dict[str, dict]) -> Tuple[Dict[int, int], List[int]]:
    """
    Scrie randurile NOI de istoric si intoarce doua lucruri.

    Intoarce:
      * `index_la_id`  -- {indicele randului in TabelIstoric: FX_Istoric.ID}, pentru
                          FIECARE rand din payload, si cele gasite si cele inserate.
                          Asta e ancora contractului in doua faze pentru randurile din
                          sarcina utila: `rand_istoric` din `decizii` e chiar acest
                          indice (F24). Randurile VECHI care sunt in payload intra si ele
                          in harta. DAR fluxul REVERSE aduce doar diferenta de istoric,
                          deci un instantaneu ramas neasezat dintr-o rulare mai veche de
                          regula NU isi gaseste randul aici -- acela se ancoreaza pe
                          `FX_Istoric.ID` (F34, `prelucrare_asociere.ancora`).
      * `ids_inserate` -- doar cele scrise ACUM. Pasul 7 marcheaza exact pe acestea.

    `Rez_Ord` e siretlicul de ordonare si e usor de gresit. Multiplicatorul 0/100/1000 se
    alege din MAX(Rez_Ord) existent, apoi se MUTA pe parcursul buclei cand apare un rand
    de «Initial ->» sau «definitivare ->». Un rand ale carui Observatii incep cu
    «RAND CONTRACT:» primeste ordinalul indicatorului plus multiplicatorul curent.
    """
    cursor.execute(_ISTORIC_MAXORD_SQL, (cod,))
    rand = cursor.fetchone()
    max_ord = (rand or {}).get("MaxOrd") or 0
    # Access: If lMaxOrd >= 1000 Then Multi = 1000 ElseIf >= 100 Then Multi = 100.
    multi = 1000 if max_ord >= 1000 else (100 if max_ord >= 100 else 0)

    # Ordinea indicatorilor -- portul lui FX_DicInd_Ordine: {CodAI: NrCrt}.
    ordine = {k: int(v["NrCrt"] or 0) for k, v in indicatori.items()}

    cursor.execute(_ISTORIC_EXISTENTE_SQL, (cod,))
    existente: Dict[tuple, int] = {}
    for r in cursor.fetchall():
        existente[_cheie_istoric(r["DataFX"], r["Utilizator"],
                                 r["Descriere"], r["Observatii"])] = int(r["ID"])

    index_la_id: Dict[int, int] = {}
    ids_inserate: List[int] = []

    for i, row in enumerate(randuri):
        if not isinstance(row, dict):
            raise ValueError(f"{TABLE_ISTORIC}[{i}] nu este un obiect.")
        # Cele patru celule de payload ale randului de istoric. `TabelIstoric` e razuit
        # de un `ScrapeTable` simplu, fara `ForEachVar`, deci NICIUNA nu poate fi
        # imbricata -- spus aici ca asteptare, nu presupus: daca vreuna devine o lista,
        # ne oprim cu numele ei in loc sa crapam mai incolo cu `AttributeError`.
        unde = f"{TABLE_ISTORIC}[{i}]"
        descriere = text_celula(row.get("Descriere"), unde, "Descriere")
        observatii = text_celula(row.get("Observatii"), unde, "Observatii")
        utilizator = text_celula(row.get("Utilizator"), unde, "Utilizator")
        data_fx = parse_timp_istoric(text_celula(row.get("Timp"), unde, "Timp"))
        if data_fx is None:
            raise ValueError(
                f"{TABLE_ISTORIC}[{i}] nu are «Timp»; rândul nu poate fi datat."
            )

        cheie = _cheie_istoric(data_fx, utilizator, descriere, observatii)
        gasit = existente.get(cheie)
        if gasit is not None:
            # Deja in baza -- din aceasta rulare (un rand identic mai devreme in acelasi
            # payload) sau dintr-una anterioara. In ambele cazuri NU se rescrie nimic.
            index_la_id[i] = gasit
            continue

        # --- Rez_Ord, exact ramurile din VBA, in exact aceeasi ordine -------------
        rez_ord: Optional[int] = None
        d_low = descriere.lower()
        if "angajament nou" in d_low:
            # Primul rand de creare, chiar daca in FOREXE apare al doilea.
            rez_ord = 0
        elif "initial ->" in d_low:
            rez_ord = 100
            multi = 100
        elif "definitivare ->" in d_low:
            rez_ord = 1000
            multi = 1000
        elif observatii[:14].upper() == "RAND CONTRACT:":
            cod_ind = fx_extract_cod_indicator(observatii)
            cheie_ai = cod_ai(cod, cod_ind or "")
            # VBA: dInd(CodAI) + Multi. Un CodAI absent dintr-un Scripting.Dictionary
            # da Empty, adica 0 la adunare -- deci un indicator necunoscut nu ridica
            # acolo. Se pastreaza: `.get(..., 0)` face acelasi lucru, explicit.
            rez_ord = ordine.get(cheie_ai, 0) + multi

        # HASH se scrie in continuare, chiar daca deduplicarea nu se sprijina pe el:
        # coloana ramane populata si o comparatie viitoare ramane posibila (D9).
        # Cheia hash-ului e construita din TEXTUL brut al payload-ului, nu din data
        # parsata -- asa o construia si Access (GetHashForRow_Istoric peste `Timp`).
        row_hash = get_hash_for_row_istoric({
            "Timp": row.get("Timp"),
            "Utilizator": row.get("Utilizator"),
            "Descriere": row.get("Descriere"),
            "Observatii": row.get("Observatii"),
        })

        cursor.execute(_ISTORIC_INSERT_SQL,
                       (cod, descriere, observatii, utilizator, row_hash,
                        rez_ord, data_fx))
        new_id = int(cursor.lastrowid)
        index_la_id[i] = new_id
        ids_inserate.append(new_id)
        # Adaugarea in `existente` NU e o optimizare: doua randuri identice in acelasi
        # payload trebuie sa se colapseze intr-unul, exact cum se colapsau in Access
        # (rcHis.FindFirst mergea peste un recordset care crestea cu fiecare AddNew).
        # Fara ea, al doilea ar lovi indexul UNIQUE de pe FX_Istoric.HASH.
        existente[cheie] = new_id

    return index_la_id, ids_inserate


# ===========================================================================
# PASUL 3b -- FX_Istoric_Prelucreaza_Observatii
# ===========================================================================
_NEPRELUCRATE_SQL = (
    "SELECT ID, Descriere, Observatii, CodAngajament "
    "FROM FX_Istoric WHERE Prelucrat = 0 AND CodAngajament = %s ORDER BY ID"
)
# Ramura Edit din VBA reseteaza INTAI totul, apoi scrie. Se pastreaza intocmai: un rand
# reprocesat nu are voie sa pastreze o valoare dintr-o parsare anterioara.
_OBS_UPDATE_SQL = (
    "UPDATE FX_Istoric SET CodIndicator = %s, CodAI = %s, IdClsf = %s, Clsf = %s, "
    "Val_Receptie = %s, Val_Rezervare_I = %s, Val_Plata = %s, Val_Rezervare_D = %s, "
    "Val_AngLeg = %s, IdTrezor = %s, Doc = %s, Val_Rezervare_Dif = %s, "
    "Val_Rezervare_Ant = %s, IDREV = %s, TipRand = %s "
    "WHERE ID = %s"
)
_IDREV_LOOKUP_SQL = (
    "SELECT IDREV FROM FX_DDF_REV WHERE CodAngajament = %s AND NumarRev = %s LIMIT 1"
)


def _idrev_din_descriere(cursor, descriere: str, cod: str) -> Optional[int]:
    """Port al lui GetIDREV_FromDescriere: «(REV:nn)» -> FX_DDF_REV.IDREV, sau None."""
    nr = extract_numar_rev(descriere)
    if nr is None or (cod or "").strip() == "":
        return None
    cursor.execute(_IDREV_LOOKUP_SQL, (cod, int(nr)))
    row = cursor.fetchone()
    return None if row is None else int(row["IDREV"])


def step3b_prelucreaza_observatii(cursor, cod: str,
                                  indicatori: Dict[str, dict]) -> None:
    """
    Umple coloanele derivate din textul liber al Observatiilor.

    Functia asta e parsare pura si e cel mai dens lucru din toata conducta. E portata
    rand cu rand; NU e restructurata. Ordinea testelor conteaza si e pastrata.
    """
    cursor.execute(_NEPRELUCRATE_SQL, (cod,))
    randuri = cursor.fetchall()

    for r in randuri:
        obs = r["Observatii"] or ""
        descr = r["Descriere"] or ""
        contract = r["CodAngajament"] or ""

        # --- resetare, exact ca in VBA -------------------------------------
        cod_indicator = None
        cheie_ai = None
        id_clsf = None
        clsf = None
        val_receptie = 0.0
        val_rez_i = 0.0
        val_plata = 0.0
        val_rez_d = 0.0
        val_angleg = 0.0
        id_trezor = None
        doc = None
        val_rez_dif = 0.0
        val_rez_ant = 0.0
        idrev = None
        tip_rand = None

        # --- indicatorul -----------------------------------------------------
        ci = fx_extract_cod_indicator(obs)
        if ci is not None:
            ind = indicatori.get(cod_ai(contract, ci))
            if ind is not None:
                cod_indicator = ci
                if contract != "":
                    cheie_ai = cod_ai(contract, ci)
                    id_clsf = ind["IdClsf"]
                    clsf = ind["Clsf"]

        # --- rand de contract: cele trei valori de rezervare -----------------
        if "rand contract:" in obs.lower():
            val_angleg = extract_number_after_label(obs, "angajament legal:")
            val_rez_i = extract_number_after_label(
                obs, "credit de angajament rezervat intial:")
            val_rez_d = extract_rezervare_definitiva(obs)

        # --- receptie, linie ---------------------------------------------------
        if "suma receptie:" in obs.lower():
            val_receptie = parse_amount(
                extract_text_between(obs, "Suma receptie:", "RON")) or 0.0
            tip_rand = get_tip_rand(obs)

        # --- receptie, antet ---------------------------------------------------
        # Testul e SEPARAT, nu `elif`: GetTipRand da prioritate lui «suma receptie:»,
        # deci un rand care le poarta pe amandoua ramane «Receptie», dar Val_Receptie
        # se rescrie aici din «valoare:». Fidel VBA-ului.
        if "(activ:true)" in obs.lower():
            val_receptie = parse_amount(
                extract_text_between(obs, "valoare:", ", (activ:true)")) or 0.0
            tip_rand = get_tip_rand(obs)

        # --- plata ------------------------------------------------------------
        if "plata:" in obs.lower():
            val_plata = extract_number_after_label(obs, "valoare:")
            id_trezor = null_if_empty(extract_text_after_label(obs, "IdTrezor:"))
            doc = null_if_empty(extract_text_between(obs, "document:", ", data:"))
            # Cautarea in Descriere e SENSIBILA la litere mari/mici in VBA (InStr fara
            # vbTextCompare) si asa ramane. «ncasare» prinde si «Incasare» si «Incasare»
            # -- de-asta era taiat prefixul acolo, si de-asta nu se «repara» aici.
            if "ncasare" in descr:
                tip_rand = "PLATA_INCASARE"
            elif "Retur" in descr:
                tip_rand = "PLATA_RETUR"
            else:
                tip_rand = "PLATA_PLATA"

        # Slice 0076: the K-BOT marker «(IDREV: n)» the page appended to the reservation's
        # motive names the revision directly, and it wins over the old «(REV:nn)». It is
        # stored even when FX_DDF_REV has no such row YET: the revision is written later,
        # from the DDF form, and the reservation is linked then (ddf_edit.py, the IDREV
        # lock). FX_Istoric.IDREV has no foreign key; FX_Rezervari.IDREV has one, and step
        # 3c/3d already writes NULL there, with a warning, while the revision is missing.
        idrev = extract_marcaj(descr).get("IDREV") or extract_marcaj(obs).get("IDREV")
        if idrev is None:
            idrev = _idrev_din_descriere(cursor, descr, contract)

        cursor.execute(_OBS_UPDATE_SQL, (
            cod_indicator, cheie_ai, id_clsf, clsf,
            val_receptie, val_rez_i, val_plata, val_rez_d, val_angleg,
            id_trezor, doc, val_rez_dif, val_rez_ant, idrev, tip_rand,
            int(r["ID"]),
        ))

    _calculeaza_val_rezervare_dif(cursor, cod)


# ---------------------------------------------------------------------------
# CalculeazaValRezervareDif -- apelata la coada lui 3b, ca in VBA
# ---------------------------------------------------------------------------
# Planul nu o numeste, dar FX_Istoric_Prelucreaza_Observatii se termina cu ea si TOATE
# valorile `TipRand` de rezervare («Rez_Initiala», «Rez_Definitiva», «Rez_Influenta»,
# «Rez_Zero» si cele trei cu «+») sunt puse AICI, nu mai sus. Pasii 3c/3d filtreaza pe
# ele, deci fara functia asta rezervarile ar fi mereu zero.
_SEED_LAST_ANG_SQL = (
    "SELECT T.CodIndicator, T.Val_AngLeg FROM FX_Istoric AS T "
    "INNER JOIN (SELECT CodIndicator, MAX(ID) AS MaxID FROM FX_Istoric "
    "            WHERE CodAngajament = %s AND Prelucrat = 1 "
    "              AND COALESCE(CodIndicator, '') <> '' "
    "              AND TipRand IN ('Rez_Initiala','Rez_Definitiva','Rez_Influenta','Rez_Zero') "
    "            GROUP BY CodIndicator) AS Q "
    "  ON T.CodIndicator = Q.CodIndicator AND T.ID = Q.MaxID"
)
_SEED_TIPRAND_SQL = (
    "SELECT DISTINCT CodIndicator FROM FX_Istoric "
    "WHERE CodAngajament = %s AND Prelucrat = 1 "
    "  AND COALESCE(CodIndicator, '') <> '' AND TipRand = %s"
)
# Portul lui qFX_ISTORIC_REZERVARI_TIPRAND. `CDate(Format(DataFX,'Short Date'))` =
# trunchiere la zi; in MariaDB, DATE(DataFX).
_TIPRAND_RANDURI_SQL = (
    "SELECT FIS.ID, FIS.Observatii, FIS.Descriere, FIS.CodIndicator, FIS.Val_AngLeg "
    "FROM FX_Istoric FIS "
    "WHERE FIS.Rez_Ord IS NOT NULL AND FIS.CodAngajament = %s AND FIS.Prelucrat = 0 "
    "  AND COALESCE(FIS.TipRand, '') = '' "
    "ORDER BY DATE(FIS.DataFX), FIS.Rez_Ord, FIS.ID"
)
_SET_TIPRAND_SQL = "UPDATE FX_Istoric SET TipRand = %s WHERE ID = %s"
_SET_REZ_SQL = (
    "UPDATE FX_Istoric SET TipRand = %s, Val_Rezervare_Ant = %s, "
    "Val_Rezervare_Dif = %s WHERE ID = %s"
)


def _calculeaza_val_rezervare_dif(cursor, cod: str) -> None:
    """Port al lui CalculeazaValRezervareDif. Se citeste alaturi de sursa VBA."""
    cursor.execute(_SEED_LAST_ANG_SQL, (cod,))
    last_ang: Dict[str, float] = {}
    for r in cursor.fetchall():
        ci = (r["CodIndicator"] or "").strip()
        if ci:
            last_ang[f"{cod}|{ci}"] = float(r["Val_AngLeg"] or 0)

    cursor.execute(_SEED_TIPRAND_SQL, (cod, "Rez_Initiala"))
    are_initiala = {f"{cod}|{(r['CodIndicator'] or '').strip()}"
                    for r in cursor.fetchall()}
    cursor.execute(_SEED_TIPRAND_SQL, (cod, "Rez_Definitiva"))
    are_definitiva = {f"{cod}|{(r['CodIndicator'] or '').strip()}"
                      for r in cursor.fetchall()}

    cursor.execute(_TIPRAND_RANDURI_SQL, (cod,))
    randuri = cursor.fetchall()

    # Cele trei stari care se muta pe masura ce bucla inainteaza. In VBA sunt variabile
    # locale NEinitializate, deci False la intrare -- si asta conteaza: primul rand de
    # «Rand contract:» dinaintea oricarui «Angajament nou.» cade pe ramura ELSE.
    r_init = False
    r_def = False

    for r in randuri:
        obs = r["Observatii"] or ""
        descr = r["Descriere"] or ""
        cod_ind = (r["CodIndicator"] or "").strip()
        rid = int(r["ID"])

        if descr == "sume nemodificate":
            cursor.execute(_SET_TIPRAND_SQL, ("Rez_Zero", rid))
            continue

        if is_rand_contract_row(obs) and cod and cod_ind:
            k = f"{cod}|{cod_ind}"
            val_curent = float(r["Val_AngLeg"] or 0)
            val_anterior = float(last_ang.get(k, 0))

            if r_init:
                # Primul rand -- initializare angajament sau adaugare rand.
                cursor.execute(_SET_REZ_SQL, ("Rez_Initiala", 0, 0, rid))
                are_initiala.add(k)
            elif r_def:
                # Primul rand dupa Initiala. VBA-ul scrie ZERO in amandoua coloanele
                # aici, cu valorile reale comentate alaturi -- se pastreaza zero.
                cursor.execute(_SET_REZ_SQL, ("Rez_Definitiva", 0, 0, rid))
                are_definitiva.add(k)
            elif k in are_definitiva:
                dif = round(val_curent - val_anterior, 2)
                tip = "Rez_Zero" if (val_curent == 0 and val_anterior == 0) \
                    else "Rez_Influenta"
                cursor.execute(_SET_REZ_SQL, (tip, val_anterior, dif, rid))
            else:
                # Indicator adaugat fara Initiala -- direct Influenta.
                cursor.execute(_SET_REZ_SQL, ("Rez_Influenta", 0, val_curent, rid))
                are_definitiva.add(k)

            last_ang[k] = val_curent
        else:
            # Randurile de STARE. Ele muta cele trei steaguri pentru randurile care
            # urmeaza; de-asta ordinea interogarii (zi, Rez_Ord, ID) nu e decorativa.
            if descr == "Angajament nou.":
                r_init, r_def = True, False
                cursor.execute(_SET_TIPRAND_SQL, ("Rez_Initiala+", rid))
            elif "definitivare" in descr and "derulare" not in descr:
                r_init, r_def = False, True
                cursor.execute(_SET_TIPRAND_SQL, ("Rez_Definitiva+", rid))
            elif "derulare" in descr:
                r_init, r_def = False, False
                cursor.execute(_SET_TIPRAND_SQL, ("Rez_Derulare+", rid))


# ===========================================================================
# PASII 3c / 3d -- FX_Istoric_Populeaza_Rezervari (initiale, apoi influente)
# ===========================================================================
# Portul lui qFX_ISTORIC_REZ_INIT si qFX_ISTORIC_REZ. Doua note de traducere:
#
#   * `CDate(Format([DataFX],"Short Date"))` = trunchiere la zi -> `DATE(DataFX)`.
#   * Ambele interogari Access faceau JOIN cu ClasificatiiG DOAR pentru cheia de sortare
#     `Clsf`. Aici e subinterogare scalara cu LIMIT 1, din acelasi motiv ca mai sus:
#     un JOIN peste un nomenclator cu duplicate ar multiplica randurile de rezervare.
#
#   * `FX_Istoric.ID NOT IN (SELECT IDH FROM FX_Rezervari)` e ce face pasul repetabil.
#     Se pastreaza intocmai. (FX_Rezervari.IDH are si un index UNIQUE pe MariaDB, deci
#     o a doua scriere ar esua zgomotos oricum -- dar filtrul e cel care o previne.)
_REZ_SELECT = (
    "SELECT H.ID, I.CodAI, I.CodAngajament, I.CodIndicator, I.IdClsf, "
    "  DATE(H.DataFX) AS DataRezervare, I.Prevedere_Bugetara_Initiala AS R_CreditBug, "
    "  H.Val_Rezervare_I AS R_Initiala, H.Val_AngLeg AS R_Definitiva, "
    "  {valoare} AS R_Valoare, H.Val_Rezervare_Ant, H.IDREV, "
    "  (SELECT C.Clsf FROM Clasificatii C "
    "    WHERE C.IdUnitate = I.IdUnitate AND C.IdClsfAcc = I.IdClsf LIMIT 1) AS ClsfSort "
    "FROM FX_Indicatori I INNER JOIN FX_Istoric H ON I.CodAI = H.CodAI "
    "WHERE H.ID NOT IN (SELECT IDH FROM FX_Rezervari WHERE IDH IS NOT NULL) "
    "  AND I.CodAngajament = %s AND H.TipRand = %s "
    "ORDER BY DATE(H.DataFX), ClsfSort"
)
_REZ_INSERT_SQL = (
    "INSERT INTO FX_Rezervari "
    "(IDH, CodAI, CodAngajament, CodIndicator, IdClsf, DataRezervare, R_CreditBug, "
    " R_Initiala, R_Definitiva, R_Valoare, IDREV, EInitiala, R_Anterioara, "
    " EMicsorare, EMarire) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
)
_DDF_REV_EXISTA_SQL = "SELECT 1 FROM FX_DDF_REV WHERE IDREV = %s"


def step3cd_populeaza_rezervari(cursor, cod: str, initiala: bool,
                                warnings: List[str]) -> int:
    """
    Scrie FX_Rezervari. Apelata de doua ori: `initiala=True`, apoi `initiala=False`.

    D-E: un `IDREV` care arata catre o revizie absenta din FX_DDF_REV se scrie NULL, se
    numara si se intoarce ca avertisment. Nu ar trebui sa se intample; daca se intampla,
    avertismentul e singurul mod in care cineva afla. Alternativa -- sa lasam INSERT-ul
    sa cada -- ar lua toata tranzactia cu el (eroare 1452) pentru o legatura pe care
    pasul 3e o poate umple mai tarziu oricum.
    """
    valoare = "H.Val_AngLeg" if initiala else "H.Val_Rezervare_Dif"
    tip_rand = "Rez_Definitiva" if initiala else "Rez_Influenta"
    cursor.execute(_REZ_SELECT.format(valoare=valoare), (cod, tip_rand))
    randuri = cursor.fetchall()

    scrise = 0
    for r in randuri:
        idrev = r["IDREV"]
        if idrev is not None:
            cursor.execute(_DDF_REV_EXISTA_SQL, (int(idrev),))
            if cursor.fetchone() is None:
                warnings.append(
                    f"FX_Rezervari: revizia {idrev} nu există în FX_DDF_REV — "
                    f"legătura rămâne goală (indicator {r['CodIndicator']})."
                )
                idrev = None

        r_valoare = float(r["R_Valoare"] or 0)
        cursor.execute(_REZ_INSERT_SQL, (
            int(r["ID"]), r["CodAI"], r["CodAngajament"], r["CodIndicator"],
            r["IdClsf"], r["DataRezervare"], r["R_CreditBug"],
            r["R_Initiala"], r["R_Definitiva"], r_valoare, idrev,
            # EInitiala pe ramura initiala; pe cealalta, cele trei coloane de influenta.
            1 if initiala else 0,
            None if initiala else r["Val_Rezervare_Ant"],
            0 if initiala else (1 if r_valoare < 0 else 0),
            0 if initiala else (1 if r_valoare > 0 else 0),
        ))
        scrise += 1
    return scrise


# ===========================================================================
# PASUL 3e -- FX_Rezervari_Asocieaza_IDREV
# ===========================================================================
_REZ_CU_IDREV_SQL = (
    "SELECT IDRZ, IDREV FROM FX_Rezervari "
    "WHERE CodAngajament = %s AND IDREV IS NOT NULL AND AreDDF = 0"
)
_REZ_FARA_IDREV_SQL = (
    "SELECT IDRZ, CodIndicator FROM FX_Rezervari "
    "WHERE CodAngajament = %s AND IDREV IS NULL AND AreDDF = 0"
)
_MIN_IDREV_SA_SQL = (
    "SELECT MIN(SA.IDREV) AS MinIDREV FROM FX_DDF_REV_SA SA "
    "WHERE SA.CodAngajament = %s AND SA.CodIndicator = %s "
    "  AND SA.IDREV NOT IN (SELECT IDREV FROM FX_Rezervari "
    "                       WHERE CodAngajament = %s AND IDREV IS NOT NULL)"
)


def step3e_asociaza_idrev(cursor, cod: str) -> None:
    """
    Doua cazuri, exact ca in VBA.

    Cazul 1 -- randul poarta deja un IDREV explicit din parser («(REV:x)»): primeste
    `AreDDF = 1`, iar revizia numita primeste `Incarcat = 1`.

    Cazul 2 -- randul nu are IDREV: se cauta cea mai mica revizie disponibila din
    FX_DDF_REV_SA pentru acelasi indicator, care nu e deja folosita de o alta rezervare.
    Primeste `AreDDF = 1`, dar NU si `Incarcat` pe FX_DDF_REV -- diferenta e in VBA si e
    intentionata acolo: legatura e dedusa, nu declarata de operator.
    """
    cursor.execute(_REZ_CU_IDREV_SQL, (cod,))
    for r in cursor.fetchall():
        cursor.execute("UPDATE FX_Rezervari SET AreDDF = 1 WHERE IDRZ = %s",
                       (int(r["IDRZ"]),))
        cursor.execute(
            "UPDATE FX_DDF_REV SET Incarcat = 1 WHERE IDREV = %s AND CodAngajament = %s",
            (int(r["IDREV"]), cod))

    cursor.execute(_REZ_FARA_IDREV_SQL, (cod,))
    for r in cursor.fetchall():
        # Interogarea se re-ruleaza pentru FIECARE rand, fiindca `NOT IN (...)` de
        # deasupra trebuie sa vada rezervarile actualizate la pasii anteriori ai
        # aceleiasi bucle. VBA-ul face la fel, si aici chiar conteaza: altfel doua
        # rezervari ale aceluiasi indicator ar primi aceeasi revizie.
        cursor.execute(_MIN_IDREV_SA_SQL, (cod, r["CodIndicator"] or "", cod))
        row = cursor.fetchone()
        min_idrev = None if row is None else row["MinIDREV"]
        if min_idrev is not None:
            cursor.execute(
                "UPDATE FX_Rezervari SET IDREV = %s, AreDDF = 1 WHERE IDRZ = %s",
                (int(min_idrev), int(r["IDRZ"])))


# ===========================================================================
# PASUL 4a -- FX_Istoric_Populeaza_Receptii
# ===========================================================================
_RECEPTII_ISTORIC_SQL = (
    "SELECT ID, HASH, CodAI, CodAngajament, CodIndicator, IdClsf, DataFX, TipRand, "
    "       Descriere, Observatii, Val_Receptie "
    "FROM FX_Istoric "
    "WHERE CodAngajament = %s AND Prelucrat = 0 AND INSTR(COALESCE(TipRand,''), 'Receptie') <> 0 "
    "ORDER BY ID"
)
_MAX_NRCRT_H_SQL = (
    "SELECT MAX(NrCrt) AS MaxNr FROM FX_Receptii_H WHERE CodAngajament = %s"
)
_H_INSERT_SQL = (
    "INSERT INTO FX_Receptii_H "
    "(IDH, NrCrt, CodAngajament, DataH, Total, Descriere, EsteStergere, Sters) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, 0)"
)
# Slice 0076: the same row, with the IDRH the page reserved and wrote into the description.
_H_INSERT_CU_ID_SQL = (
    "INSERT INTO FX_Receptii_H "
    "(IDRH, IDH, NrCrt, CodAngajament, DataH, Total, Descriere, EsteStergere, Sters) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 0)"
)
_REC_INSERT_SQL = (
    "INSERT INTO FX_Receptii "
    "(IDRH, IDH, IdClsf, CodSSI, Clsf, IdUnitate, CodAI, CodAngajament, CodIndicator, "
    " Data, Valoare, ValoareOrig, HASH, TipIntern) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
)
# Slice 0076: the first line of a marked snapshot, with the IDR the page reserved.
_REC_INSERT_CU_ID_SQL = (
    "INSERT INTO FX_Receptii "
    "(IDR, IDRH, IDH, IdClsf, CodSSI, Clsf, IdUnitate, CodAI, CodAngajament, CodIndicator, "
    " Data, Valoare, ValoareOrig, HASH, TipIntern) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
)
_INDICATORI_VAZUTI_SQL = (
    "SELECT CodIndicator FROM FX_Receptii WHERE CodAngajament = %s GROUP BY CodIndicator"
)
# A header row that ALREADY has its snapshot. See the F33 note in the docstring below.
_H_EXISTA_IDH_SQL = "SELECT IDRH FROM FX_Receptii_H WHERE IDH = %s LIMIT 1"


def step4a_populeaza_receptii(cursor, cod: str,
                              indicatori: Dict[str, dict]) -> Tuple[int, List[int]]:
    """
    Construieste instantaneele (FX_Receptii_H) si liniile lor (FX_Receptii) din istoric.

    Intoarce `(antete scrise, ID-urile de istoric CONSUMATE)`. Al doilea e lista pe care
    pasul 7 o marcheaza `Prelucrat = 1` pe langa randurile inserate in rularea asta --
    vezi F33 mai jos.

    F33 -- A HISTORY ROW IS CONSUMED ONCE (18.09.2026). The step reads `Prelucrat = 0`,
    and step 7 used to mark only the rows THIS run inserted (`ids_noi`). A row that was
    already in the table at `Prelucrat = 0` -- migrated from Access with the flag unset,
    or left behind by anything older than this pipeline -- was therefore read on every
    download, and every download built a NEW `FX_Receptii_H` for it, unplaced (the
    27.07.2026 reception of AAB2KPRT2EB, history row 5786, is the case that surfaced it).
    Two guards, and both are needed:
      * a header whose `IDH` already has an `FX_Receptii_H` is NOT written again; its
        buffered lines are dropped, because they were poured under that snapshot when it
        was born (same check `receptii_refacere.py` makes);
      * every history row the step consumed -- headers written, headers skipped as
        duplicates or under F32, and the lines poured under a header -- is returned so
        step 7 marks it. Lines still waiting for a header at the end are NOT in the list:
        they belong to the next run, as the comment at the bottom has always said.

    Merge peste randurile NEPRELUCRATE de receptie, in ordinea `ID`, si acumuleaza:

      * un rand ale carui Observatii contin `(activ:true)` e un ANTET -> FX_Receptii_H,
        iar liniile stranse pana atunci se varsa sub el;
      * a row that names an indicator (`CodAI`) or has `Val_Receptie <> 0` is a LINE
        -> set aside (`este_linie_receptie`).

    F31 -- ZERO LINES ARE KEPT (17.09.2026). The VBA kept only `Val_Receptie <> 0`, so
    an indicator that did not move or fell to zero DISAPPEARED from the snapshot while
    the reception still carries it in `RHR`. F14/F16 ("an indicator can fall to zero
    but never disappears") then refused correct placements, and `SUM(DIF)` no longer
    came down with the indicator. A zero row that names an indicator is a line with
    `Valoare = 0`; a zero row naming none is skipped, as before.

    F21 -- STERGEREA. Un rand cu `Descriere = "Stergere receptie"` poarta si el
    `(activ:true)`, deci devine antet pe calea NORMALA. NU e deviat, NU e filtrat, NU e
    tratat special aici in afara de steagul `EsteStergere`. Nu are randuri pe indicator,
    deci nu produce nicio linie -- si asta iese de la sine, fiindca liniile lui nu exista
    in istoric, nu fiindca le-am opri noi. E ultimul instantaneu al lantului lui, si
    formularul trebuie sa il poata vedea si fixa.

    F32 -- HEADER-ONLY SNAPSHOTS ARE SKIPPED (17.09.2026). The old Access app left
    headers in `FX_Istoric` with a total and NO indicator row before them. That is not a
    state a reception can be in; it is an error of the old app, and no `FX_Receptii_H`
    is written for it (`is_header_only_snapshot`). The deletion row (F21) is the one
    line-less header that IS a snapshot, and it is exempt. The history row still gets
    `Prelucrat = 1` with the rest of the run (step 7), so it is not re-read.

    D3 -- fara tabele temporare. Access scria in tmpFX_Receptii_H / tmpFX_Receptii si le
    salva la pasul 6; aici se scrie direct in tabelele vii, inauntrul tranzactiei.
    `IDRR` ramane NULL: cine este receptia se hotaraste la pasul 4c.
    """
    cursor.execute(_RECEPTII_ISTORIC_SQL, (cod,))
    randuri = cursor.fetchall()
    if not randuri:
        return 0, []

    # Indicatorii care APAR DEJA in receptiile acestui angajament. Decid `TipIntern`
    # ('VECHI' / 'NOU') la scrierea liniilor.
    cursor.execute(_INDICATORI_VAZUTI_SQL, (cod,))
    vazuti = {str(r["CodIndicator"]) for r in cursor.fetchall()
              if r["CodIndicator"] is not None}

    cursor.execute(_MAX_NRCRT_H_SQL, (cod,))
    row = cursor.fetchone()
    nr_crt = int((row or {}).get("MaxNr") or 0) + 1

    tampon: List[dict] = []      # liniile stranse pana la urmatorul antet
    consumate: List[int] = []    # F33: history rows this pass has used up
    antete = 0
    antete_goale = 0             # F32: header-only snapshots, not written
    antete_dublate = 0           # F33: headers whose snapshot already exists

    for r in randuri:
        obs = r["Observatii"] or ""

        if "(activ:true)" in obs:
            # --- ANTET -----------------------------------------------------
            # The header and everything buffered before it are consumed here, on
            # every branch below: written, skipped as a duplicate, or skipped under
            # F32. None of them may be read again by a later run.
            consumate.append(int(r["ID"]))
            consumate.extend(linie["IDH"] for linie in tampon)

            este_stergere = is_stergere_receptie(r["Descriere"])
            if is_header_only_snapshot(este_stergere, tampon):
                # F32: the total row alone, no indicator line. Not a snapshot.
                antete_goale += 1
                logger.info("PRELUCRARE cod=%s: antet de receptie fara nicio linie "
                            "(rand de istoric %s, DataFX %s, Total %s) -- ignorat (F32)",
                            cod, r["ID"], r["DataFX"], r["Val_Receptie"])
                continue
            cursor.execute(_H_EXISTA_IDH_SQL, (int(r["ID"]),))
            if cursor.fetchone() is not None:
                # F33: built by an earlier run that never marked the row. Its lines
                # were poured then; the buffer is dropped, not poured a second time.
                antete_dublate += 1
                logger.info("PRELUCRARE cod=%s: antetul de receptie din randul de "
                            "istoric %s are deja instantaneu -- nu se rescrie (F33)",
                            cod, r["ID"])
                tampon = []
                continue
            descriere = extract_text_between(obs, "Receptie: ", ",")
            # Slice 0076 -- THE MARKER. The page appended «(IDRH: n; IDR: m)» to the
            # reception's description before the save, and FOREXE copied the description
            # into this total row. The ids were reserved on the server (marcaj.py), so the
            # snapshot is born WITH them and nobody has to guess which history row became
            # which snapshot. A marker whose id cannot be used (already in the table: the
            # same history row read again; or never handed out by the counter) is ignored
            # and said in the log -- the row is then written exactly as before.
            marcaj = extract_marcaj(descriere) or extract_marcaj(obs)
            descriere = fara_marcaj(descriere)
            idrh_marcat = marcaj.get("IDRH")
            if idrh_marcat is not None and not id_marcaj_utilizabil(cursor, LOCK_IDRH, idrh_marcat):
                logger.warning("PRELUCRARE cod=%s: marcajul IDRH %s din randul de istoric %s "
                               "nu se poate folosi (exista deja sau nu a fost rezervat) -- "
                               "instantaneul primeste un id nou", cod, idrh_marcat, r["ID"])
                idrh_marcat = None
            if idrh_marcat is not None:
                cursor.execute(_H_INSERT_CU_ID_SQL, (
                    int(idrh_marcat), int(r["ID"]), nr_crt, str(r["CodAngajament"]),
                    r["DataFX"], float(r["Val_Receptie"] or 0), descriere,
                    1 if este_stergere else 0,
                ))
                idrh = int(idrh_marcat)
                consuma_lacatul(cursor, LOCK_IDRH, idrh, cod)
            else:
                cursor.execute(_H_INSERT_SQL, (
                    int(r["ID"]), nr_crt, str(r["CodAngajament"]), r["DataFX"],
                    float(r["Val_Receptie"] or 0), descriere,
                    1 if este_stergere else 0,
                ))
                idrh = int(cursor.lastrowid)
            nr_crt += 1
            antete += 1

            # The FIRST line takes the marked IDR (the rest follow the counter, which the
            # reservation already moved past it). Only under a snapshot that took its own
            # marked IDRH: an IDR without its IDRH would name a line of a snapshot that is
            # not the one the operator saved.
            idr_marcat = marcaj.get("IDR") if idrh_marcat is not None else None
            if idr_marcat is not None and not id_marcaj_utilizabil(cursor, LOCK_IDR, idr_marcat):
                logger.warning("PRELUCRARE cod=%s: marcajul IDR %s nu se poate folosi -- "
                               "prima linie primeste un id nou", cod, idr_marcat)
                idr_marcat = None
            for linie in tampon:
                ci = linie["CodIndicator"]
                valori = (
                    idrh, linie["IDH"], linie["IdClsf"], linie["CodSSI"],
                    linie["Clsf"], linie["IdUnitate"], linie["CodAI"],
                    linie["CodAngajament"], ci, linie["Data"], linie["Valoare"],
                    linie["ValoareOrig"], linie["HASH"],
                    "VECHI" if ci in vazuti else "NOU",
                )
                if idr_marcat is not None:
                    cursor.execute(_REC_INSERT_CU_ID_SQL, (int(idr_marcat),) + valori)
                    consuma_lacatul(cursor, LOCK_IDR, idr_marcat, cod)
                    idr_marcat = None
                else:
                    cursor.execute(_REC_INSERT_SQL, valori)
                vazuti.add(ci)
            tampon = []

        elif este_linie_receptie(r):
            # --- LINIE (F31: a zero value is still a line) ---------------------
            ind = indicatori.get(str(r["CodAI"] or ""))
            if ind is None:
                # Se pastreaza ca EROARE, ca in VBA. O linie de receptie al carei
                # indicator nu e in FX_Indicatori nu are unde sa se duca, iar FK-ul de
                # pe CodAI ar refuza-o oricum -- mai bine cu un mesaj care il numeste.
                raise ValueError(
                    f"Nu am găsit indicatorul {r['CodIndicator']} în FX_Indicatori "
                    f"(rând de istoric {r['ID']})."
                )
            tampon.append({
                "IDH": int(r["ID"]),
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
                # VBA: FX_Receptii_Istoric_GetIndent(CodAng, CodInd, DataFX, rcInd!Clsf,
                # Val_Receptie). Al patrulea argument e `Clsf`, NU `CodSSI`, desi numele
                # parametrului din helper e `CodSSI`. Fidel: hash-urile deja migrate au
                # fost calculate asa.
                "HASH": fx_receptii_istoric_get_indent(
                    str(r["CodAngajament"]), str(r["CodIndicator"]), r["DataFX"],
                    ind["Clsf"] or "", float(r["Val_Receptie"] or 0)),
            })

    # Un tampon nevidat la sfarsit inseamna linii fara antet. Access le pierdea tacut
    # (colectia se arunca la iesirea din functie). Aici nu se pierde nimic tacut, dar
    # nici nu se inventeaza un antet: raman in istoric, nemarcate, si vor fi prinse de
    # urmatoarea rulare cand antetul lor soseste.
    if tampon:
        logger.info("PRELUCRARE cod=%s: %s linii de receptie fara antet, amanate",
                    cod, len(tampon))
    if antete_goale:
        logger.info("PRELUCRARE cod=%s: %s antete de receptie fara linii ignorate (F32)",
                    cod, antete_goale)
    if antete_dublate:
        logger.info("PRELUCRARE cod=%s: %s antete de receptie cu instantaneu deja "
                    "existent, sarite (F33)", cod, antete_dublate)

    return antete, consumate


# ===========================================================================
# PASUL 4b -- Receptii_Prelucrare
# ===========================================================================
# Compara `ListaReceptii_results` cu ce e DEJA stocat, in tabelele vii (D3).
#
# F25 -- REGULA ADAUGATA, si motivul ei merita citit inainte de a o sterge din greseala.
# Potrivirea Access e `CLng(DataR) = CLng(dtDataR)`: GRANULARITATE DE ZI. O receptie
# stearsa nu mai poate aparea in `ListaReceptii`, deci orice potrivire aparenta cu ea e o
# CIOCNIRE, nu o identificare. Fara `Sters = 0` mai jos, o receptie creata azi in aceeasi
# zi calendaristica in care fusese creata una stearsa in martie s-ar potrivi peste aceea
# si i-ar suprascrie tacut valorile.
_R_CANDIDATI_SQL = (
    "SELECT IDRR, DataR, SumaAntet FROM FX_Receptii_R "
    "WHERE CodAngajament = %s AND Sters = 0 AND DATE(DataR) = %s"
)
_RHR_SNAP_SQL = (
    "SELECT IDRHR, CodIndicator, Valoare FROM FX_Receptii_RHR WHERE IDRR = %s"
)
_MAX_NRCRT_R_SQL = (
    "SELECT MAX(NRCRT) AS MaxNr FROM FX_Receptii_R WHERE CodAngajament = %s"
)
_R_INSERT_SQL = (
    "INSERT INTO FX_Receptii_R "
    "(NRCRT, CodAngajament, Tip, DataR, SumaAntet, Descriere, TipReceptie, HASH, "
    " Preluat, Sters, Reconstituit) "
    "VALUES (%s, %s, %s, %s, %s, %s, 'NOU', %s, 1, 0, 0)"
)
_R_UPDATE_SUMA_SQL = (
    "UPDATE FX_Receptii_R SET SumaAntet = %s, TipReceptie = 'EDIT' WHERE IDRR = %s"
)
_RHR_INSERT_SQL = (
    "INSERT INTO FX_Receptii_RHR "
    "(IDRR, CodAngajament, CodIndicator, CodAI, IdClsf, IdUnitate, CodSSI, "
    " CreditBugetar, Valoare, ValoareN, TipIntern) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
)
_RHR_UPDATE_SQL = (
    "UPDATE FX_Receptii_RHR SET CreditBugetar = %s, Valoare = %s, ValoareN = %s, "
    "TipIntern = 'EDIT' WHERE IDRHR = %s"
)
_RHR_INDICATORI_VAZUTI_SQL = (
    "SELECT CodIndicator FROM FX_Receptii_RHR WHERE CodAngajament = %s "
    "GROUP BY CodIndicator"
)


def _detaliu(det: dict, cod: str, indicatori: Dict[str, dict],
             unde: str = TABLE_RECEPTII) -> dict:
    """
    Port al lui ObtineDateDetaliu -- o linie din `Detaliu`.

    `IdClsf` si `IdUnitate` vin din FX_Indicatori, nu din payload; un indicator care nu
    se gaseste lasa amandoua goale, exact ca `rsInd.NoMatch` din VBA (care lasa vIdClsf =
    Null si lIdUnitate = 0). Aici `IdUnitate` ramane None in loc de 0: coloana e
    nulabila, iar zero ar fi un id de unitate care nu exista, adica o minciuna.
    """
    from .prelucrare_helpers import fx_receptii_normalize_ssi, parse_loose_number

    # Celulele-frunza din interiorul lui `Detaliu`. `CelulaTabel` promite imbricarea
    # RECURSIV, deci nici la nivelul asta «e text» nu e o certitudine -- se cere.
    ci = text_celula(det.get("Cod"), unde, "Detaliu.Cod").strip()
    cheie = cod_ai(cod, ci)
    ind = indicatori.get(cheie)
    return {
        "CodIndicator": ci,
        "CodAI": cheie,
        "CodSSI": fx_receptii_normalize_ssi(
            text_celula(det.get("Sector_Sursa_Indicator"), unde,
                        "Detaliu.Sector_Sursa_Indicator")),
        "CreditBugetar": parse_loose_number(
            text_celula(det.get("Credit_bugetar_rezervat_definitiv"), unde,
                        "Detaliu.Credit_bugetar_rezervat_definitiv")),
        "Valoare": parse_loose_number(
            text_celula(det.get("Valoare"), unde, "Detaliu.Valoare")),
        "ValoareN": parse_loose_number(
            text_celula(det.get("Valoare_nereceptionata"), unde,
                        "Detaliu.Valoare_nereceptionata")),
        "IdClsf": None if ind is None else ind["IdClsf"],
        "IdUnitate": None if ind is None else ind["IdUnitate"],
    }


def text_celula(valoare, unde: str, coloana: str) -> str:
    """
    O celula de payload citita ca TEXT, cu asteptarea spusa pe fata.

    De ce exista (decizia D-N, 26.08.2026). De cand structura calatoreste end-to-end, o
    celula poate sosi ca lista sau ca obiect, nu doar ca text. Un `.strip()` peste o lista
    ar crapa cu `AttributeError` la prima rulare reala, iar un `str()` peste ea ar fi si
    mai rau: ar scrie linistit «[{'Cod': 'AAB'}]» intr-o coloana de baza de date si nimeni
    nu ar afla niciodata.

    Deci fiecare loc care citeste o celula ca text o cere prin functia asta, si o coloana
    care s-a facut imbricata se opreste pe loc, cu numele ei in mesaj.

    `None` devine sirul gol -- pentru un tabel razuit o valoare lipsa si una goala sunt
    acelasi lucru, si asa se comporta si codul dinaintea acestei felii.
    """
    if valoare is None:
        return ""
    if isinstance(valoare, str):
        return valoare
    if isinstance(valoare, (list, dict)):
        fel = "o listă" if isinstance(valoare, list) else "un obiect"
        raise ValueError(
            f"{unde}: «{coloana}» a sosit ca {fel}, dar aici se citește ca text. "
            f"Coloana s-a schimbat în FOREXE, ori se citește greșit."
        )
    # Numar sau boolean: le producea deja `str()` inainte, si asa le si trimite clientul
    # (celulele vin din HTML, deci sunt text). Se pastreaza, ca sa nu se schimbe nimic
    # pentru sarcinile utile care chiar poarta asa ceva.
    return str(valoare)


def cere_lista(valoare, unde: str, coloana: str, gol_permis: bool = False):
    """
    O coloana IMBRICATA trebuie sa soseasca drept LISTA. Nimic altceva nu se accepta.

    DE CE E STRICT (decizia D-N, 26.08.2026). Pana atunci coloana asta sosea in DOUA
    forme: ca lista imbricata din sarcina utila produsa de FOREXE/Access, si ca SIR care
    contine JSON de la clientul K-BOT -- fiindca `ForexeRunner.TryParseTable` aplatiza
    fiecare celula cu `prop.Value.ToString()`, iar pentru un JArray asta da chiar textul
    JSON. Serverul le accepta pe amandoua, si tocmai asta era problema: cat timp calea
    toleranta traieste, clientul care pierde structura traieste cu ea. Clientul s-a
    reparat (structura calatoreste acum end-to-end), deci calea toleranta a plecat odata
    cu el.

    NU EXISTA SARCINI UTILE STOCATE IN FORMA VECHE de recuperat: D2 spune ca Access e
    scos din uz si nu exista un al doilea scriitor, iar conducta asta nu a rulat inca
    niciodata pe o baza reala. Nu se pierde nimic; se inchide o usa.

    Un sir NEGOL e deci un client care aplatizeaza, si mesajul spune exact asta -- nu
    «JSON nevalid», care ar trimite pe cine il citeste sa caute o virgula lipsa.

    `gol_permis` acopera cazul in care tabelul interior chiar nu a avut randuri:
    `BuildCollectedRow` scrie "" cand variabila iteratiei a ramas goala, deci un sir GOL
    inseamna «nu a fost nimic de citit», nu «s-a aplatizat ceva».
    """
    if isinstance(valoare, list):
        return valoare

    if isinstance(valoare, str):
        if valoare.strip() == "":
            if gol_permis:
                return []
            raise ValueError(f"{unde}: «{coloana}» este gol.")
        raise ValueError(
            f"{unde}: «{coloana}» a sosit ca text, dar trebuie să fie o listă. "
            f"Clientul trimite valoarea aplatizată — structura se pierde pe drum. "
            f"Trimite coloana ca listă JSON, nu ca șir care conține JSON."
        )

    raise ValueError(
        f"{unde}: «{coloana}» nu este o listă (a sosit ca {type(valoare).__name__})."
    )


def step4b_receptii_prelucrare(cursor, cod: str, randuri: List[dict],
                               indicatori: Dict[str, dict]) -> Tuple[int, int, Dict[int, int]]:
    """
    Aduce `FX_Receptii_R` / `FX_Receptii_RHR` la zi din payload.

    Trei cazuri, exact ca in VBA:
      * receptie gasita si suma identica  -> nu se atinge `R`, dar `RHR` SE VERIFICA
        oricum (suma poate fi aceeasi cu distributia schimbata: AAB -100, AAC +100);
      * receptie gasita si suma diferita  -> UPDATE `R` + `RHR`;
      * receptie negasita                 -> INSERT `R` + toate `RHR`.

    Intoarce (receptii scrise, linii RHR scrise, ANCORE).

    ANCORELE, si de ce exista (08.09.2026, dupa prima rulare adevarata a contractului in
    doua faze). `IDRR`-ul unei receptii pe care O CREEAZA RULAREA ASTA nu supravietuieste
    fazei intai: propunerea deruleaza tranzactia inapoi neconditionat, iar contorul
    AUTO_INCREMENT al InnoDB NU se deruleaza odata cu ea -- la salvare aceeasi receptie
    primeste alt numar. O decizie care numea numarul din propunere cadea deci cu
    «Recepția N nu există pe acest angajament», si asa a si cazut.

    Ancora e INDICELE randului in `ListaReceptii`, exact acelasi rationament ca
    `rand_istoric` pentru instantanee (F24): indicele e stabil prin constructie, fiindca
    AMANDOUA fazele poarta acelasi payload. `{indice: IDRR}`, doar pentru randurile a
    caror receptie s-a NASCUT in rularea asta -- restul au un `IDRR` real, dinainte, care
    nu se misca.

    Se numara receptia ca «nascuta acum» dupa IDRR, nu dupa ramura care a rulat: doua
    randuri de payload din aceeasi zi se potrivesc pe acelasi rand, deci al doilea poate
    ajunge, prin potrivire, pe o receptie pe care tocmai a inserat-o primul.
    """
    from .prelucrare_helpers import (
        fx_receptii_h_get_hash_ident, fx_receptii_parse_ro_date, parse_loose_number,
    )

    if not randuri:
        return 0, 0, {}

    cursor.execute(_RHR_INDICATORI_VAZUTI_SQL, (cod,))
    vazuti = {str(r["CodIndicator"]) for r in cursor.fetchall()
              if r["CodIndicator"] is not None}

    cursor.execute(_MAX_NRCRT_R_SQL, (cod,))
    row = cursor.fetchone()
    nr_crt = int((row or {}).get("MaxNr") or 0) + 1

    r_scrise = 0
    rhr_scrise = 0
    # IDRR-urile nascute in rularea asta, si ancorele randurilor care ajung pe ele.
    nascute = set()
    ancore: Dict[int, int] = {}

    for i, rec in enumerate(randuri):
        if not isinstance(rec, dict):
            raise ValueError(f"{TABLE_RECEPTII}[{i}] nu este un obiect.")
        if "Detaliu" not in rec or rec["Detaliu"] is None:
            raise ValueError(f"{TABLE_RECEPTII}[{i}]: lipsește colecția «Detaliu».")

        unde = f"{TABLE_RECEPTII}[{i}]"
        # `Detaliu` E imbricat, prin constructie: `ForEachVar` peste `ListaReceptii` il
        # numeste in `collectFields`, iar un `ScrapeTable` interior il scrie cu `saveTo`.
        detalii = cere_lista(rec["Detaliu"], unde, "Detaliu")

        # Restul coloanelor sunt scalare, si o cer explicit.
        data_r = fx_receptii_parse_ro_date(text_celula(rec.get("Data"), unde, "Data"))
        if data_r is None:
            raise ValueError(f"{unde}: «Data» lipsește sau e invalidă.")
        suma = parse_loose_number(text_celula(rec.get("Suma"), unde, "Suma"))
        # Hash-ul de identitate foloseste `Tip`, NU `TipReceptie` -- verificat citind
        # ObtineDateHeader.
        #
        # `TipReceptie` RAMANE in `collectFields` din «adlop - Prelucrare Completa.wfl»
        # (decizia D11 cere ca cele doua fisiere de workflow sa se potriveasca, si o
        # coloana necitita in plus nu costa nimic), dar NIMIC nu o consuma: in Access ea
        # exista ca sa se decida, dupa umplerea tabelelor temporare, daca un rand real
        # trebuie inserat sau actualizat -- o decizie care aici nu exista. Cerinta
        # planului parinte 5.3 (400 la lipsa ei) e RETRASA pe 26.08.2026 din motivul asta.
        # Slice 0076: the K-BOT marker «(IDRH: n; IDR: m)» the page appended to the
        # description is a key, not part of what the operator wrote -- it is taken out
        # before the identity hash and before the stored description, so a reception
        # edited from K-BOT keeps the hash and the text it had.
        descriere_r = fara_marcaj(
            text_celula(rec.get("DescriereReceptie"), unde, "DescriereReceptie"))
        hash_ident = fx_receptii_h_get_hash_ident(
            cod, data_r,
            text_celula(rec.get("Tip"), unde, "Tip"),
            descriere_r)

        cursor.execute(_R_CANDIDATI_SQL, (cod, data_r))
        candidati = cursor.fetchall()
        gasit = candidati[0] if candidati else None

        if gasit is not None:
            idrr = int(gasit["IDRR"])
            if round(float(gasit["SumaAntet"] or 0), 2) != round(suma, 2):
                cursor.execute(_R_UPDATE_SUMA_SQL, (suma, idrr))
                r_scrise += 1

            cursor.execute(_RHR_SNAP_SQL, (idrr,))
            existente = {str(x["CodIndicator"]): x for x in cursor.fetchall()}

            for det in detalii:
                d = _detaliu(det, cod, indicatori, f"{TABLE_RECEPTII}[{i}]")
                vechi = existente.get(d["CodIndicator"])
                if vechi is not None:
                    if round(float(vechi["Valoare"] or 0), 2) != round(d["Valoare"], 2):
                        cursor.execute(_RHR_UPDATE_SQL, (
                            d["CreditBugetar"], d["Valoare"], d["ValoareN"],
                            int(vechi["IDRHR"])))
                        rhr_scrise += 1
                else:
                    cursor.execute(_RHR_INSERT_SQL, (
                        idrr, cod, d["CodIndicator"], d["CodAI"], d["IdClsf"],
                        d["IdUnitate"], d["CodSSI"], d["CreditBugetar"],
                        d["Valoare"], d["ValoareN"],
                        "VECHI" if d["CodIndicator"] in vazuti else "NOU"))
                    vazuti.add(d["CodIndicator"])
                    rhr_scrise += 1
        else:
            cursor.execute(_R_INSERT_SQL, (
                nr_crt, cod, rec.get("Tip"), data_r, suma,
                None if rec.get("DescriereReceptie") is None else descriere_r,
                hash_ident))
            idrr = int(cursor.lastrowid)
            nascute.add(idrr)
            nr_crt += 1
            r_scrise += 1

            for det in detalii:
                d = _detaliu(det, cod, indicatori, f"{TABLE_RECEPTII}[{i}]")
                cursor.execute(_RHR_INSERT_SQL, (
                    idrr, cod, d["CodIndicator"], d["CodAI"], d["IdClsf"],
                    d["IdUnitate"], d["CodSSI"], d["CreditBugetar"],
                    d["Valoare"], d["ValoareN"],
                    "VECHI" if d["CodIndicator"] in vazuti else "NOU"))
                vazuti.add(d["CodIndicator"])
                rhr_scrise += 1

        if idrr in nascute:
            ancore[i] = idrr

    return r_scrise, rhr_scrise, ancore


# ===========================================================================
# PASUL 4d -- FX_CalculeazaDIF_Receptii_Tmp
# ===========================================================================
# Port al functiei din mdl_FX_Helpers, rulata per receptie asociata. Regulile DIF
# hranesc eticheta plutitoare din Receptii, iar o greseala aici ramane invizibila pana
# cand cineva citeste un total.
#
# Access ordona instantaneele dupa `ID` («autonumber = ordine cronologica in tmp»). Aici
# ordinea e dupa `DataH`, apoi `IDRH`: sub plasare MANUALA, ordinea de inserare nu mai e
# ordinea cronologica -- operatorul poate atasa un instantaneu din ianuarie dupa unul din
# mai. `DataH` E axa timpului (F2), deci ea ordoneaza. Cu plasare pur automata cele doua
# ordini coincid, deci nimic din datele deja produse nu se schimba.
#
# LANTUL UNEI RECEPTII STERSE SE TERMINA LA INSTANTANEUL DE STERGERE. Nu exista randuri
# dupa el, deci nu exista o regula de inventat pentru ele.
# F32: a header-only snapshot (no lines, not a deletion) is not part of the chain, so it
# must not become the `precedent` of the real snapshot after it.
_DIF_H_SQL = (
    "SELECT H.IDRH, H.Total FROM FX_Receptii_H H "
    "WHERE H.CodAngajament = %s AND H.IDRR = %s AND " + SNAPSHOT_COUNTS_SQL + " "
    "ORDER BY H.DataH, H.IDRH"
)
_DIF_H_UPDATE_SQL = "UPDATE FX_Receptii_H SET DIFH = %s, DIFHC = %s WHERE IDRH = %s"
_DIF_R_SQL = (
    "SELECT R.IDR, R.CodAI, R.IdClsf, R.Valoare FROM FX_Receptii R "
    "INNER JOIN FX_Receptii_H H ON R.IDRH = H.IDRH "
    "WHERE R.CodAngajament = %s AND H.IDRR = %s "
    "ORDER BY R.CodAI, R.IdClsf, H.DataH, H.IDRH"
)
_DIF_R_UPDATE_SQL = "UPDATE FX_Receptii SET DIF = %s, DIFC = %s WHERE IDR = %s"


def step4d_calculeaza_dif(cursor, cod: str, idrr: int) -> None:
    """Recalculeaza DIFH/DIFHC pe antete si DIF/DIFC pe linii, pentru o receptie."""
    # --- antete ---------------------------------------------------------
    cursor.execute(_DIF_H_SQL, (cod, idrr))
    precedent = None      # valoarea instantaneului anterior
    primul = None         # valoarea PRIMULUI instantaneu al lantului
    for r in cursor.fetchall():
        curent = float(r["Total"] or 0)
        if precedent is None:
            dif, difc = curent, 0.0
            primul = curent
        else:
            dif, difc = curent - precedent, curent - primul
        precedent = curent
        cursor.execute(_DIF_H_UPDATE_SQL, (round(dif, 2), round(difc, 2),
                                           int(r["IDRH"])))

    # --- linii, grupate pe (CodAI, IdClsf) -------------------------------
    cursor.execute(_DIF_R_SQL, (cod, idrr))
    prec: Dict[str, float] = {}
    prim: Dict[str, float] = {}
    for r in cursor.fetchall():
        cheie = f"{r['CodAI'] or ''}|{r['IdClsf'] or 0}"
        curent = float(r["Valoare"] or 0)
        if cheie in prec:
            dif, difc = curent - prec[cheie], curent - prim[cheie]
        else:
            dif, difc = curent, 0.0
            prim[cheie] = curent
        prec[cheie] = curent
        cursor.execute(_DIF_R_UPDATE_SQL, (round(dif, 2), round(difc, 2),
                                           int(r["IDR"])))


# ===========================================================================
# PASUL 5 -- FX_Istoric_Populeaza_Plati_Incasari
# ===========================================================================
_PLATI_ISTORIC_SQL = (
    "SELECT ID, Observatii, DataFX FROM FX_Istoric "
    "WHERE CodAngajament = %s AND Prelucrat = 0 "
    "  AND INSTR(COALESCE(TipRand,''), 'PLATA_') <> 0 ORDER BY ID"
)
_PLATI_DEDUP_SQL = (
    "SELECT Referinta_TREZOR FROM FX_Plati WHERE CodAngajament = %s "
    "GROUP BY Referinta_TREZOR"
)
_PLATI_INSERT_SQL = (
    "INSERT INTO FX_Plati "
    "(IDH, IdClsf, IdUnitate, CodAI, NrOP, CodAngajament, CodIndicator, Data_plata, "
    " Indicator_IBAN, Clsf, Program, Referinta_TREZOR, Suma, Tip, Preluat) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1)"
)
_PROGRAM_SQL = "SELECT CodProgram FROM Unitati WHERE IdUnitate = %s"


def _format_clsf_iban(indicator_fx: Optional[str]) -> str:
    """
    Port al lui Format(IndicatorFX, "@@.@@.@@.@@.@@.@@").

    Masca `@` din VBA umple de la DREAPTA la STANGA si pastreaza caracterele literale.
    Pentru "650301200301" (12 cifre) da "65.03.01.20.03.01". Un sir mai scurt se
    alinieaza tot la dreapta, cu grupurile din stanga incomplete -- de-asta se lucreaza
    de la coada, nu de la cap.
    """
    s = (indicator_fx or "").strip()
    if s == "":
        return ""
    grupuri = []
    i = len(s)
    while i > 0:
        grupuri.append(s[max(0, i - 2):i])
        i -= 2
    grupuri.reverse()
    return ".".join(grupuri)


def step5_plati_incasari(cursor, cod: str, indicatori: Dict[str, dict],
                         warnings: List[str]) -> Tuple[int, bool, bool]:
    """
    Scrie FX_Plati din randurile de istoric `PLATA_*`.

    Intoarce (scrise, are_plati, are_incasari).

    Regulile pastrate exact:
      * deduplicare pe `Referinta_TREZOR`, pornita din platile deja stocate;
      * `Rand:` sau `IdTrezor:` lipsa -> se SARE randul si se logheaza (asta chiar e o
        sarire, nu o eroare -- e ce face VBA-ul);
      * data neparsabila -> se sare;
      * `Tip` = INCASARE cand suma e negativa, altfel PLATA;
      * un indicator negasit in FX_Indicatori RIDICA.

    `Program` venea din globala `globCodProgram`. Aici e `Unitati.CodProgram` pentru
    unitatea INDICATORULUI -- planul parinte 5.2a: fiecare plata isi rezolva deja
    indicatorul, deci `IdUnitate` se ia din randul acela, nu din clasificatie a doua oara.
    """
    cursor.execute(_PLATI_ISTORIC_SQL, (cod,))
    randuri = cursor.fetchall()
    if not randuri:
        return 0, False, False

    cursor.execute(_PLATI_DEDUP_SQL, (cod,))
    dedup = {str(r["Referinta_TREZOR"]) for r in cursor.fetchall()
             if r["Referinta_TREZOR"]}

    # CodProgram per unitate, citit o data si tinut minte.
    programe: Dict[int, Optional[str]] = {}

    scrise = 0
    are_plati = False
    are_incasari = False

    for r in randuri:
        obs = r["Observatii"] or ""
        if obs == "":
            continue

        cod_ind = extract_obs_value(obs, "Rand:")
        nr_op = extract_obs_value(obs, "document:")
        s_data = extract_obs_value(obs, "data:", "valoare:")
        s_suma = extract_obs_value(obs, "valoare:")
        id_trezor = extract_obs_value(obs, "IdTrezor:", "")

        if cod_ind == "" or id_trezor == "":
            logger.info("PRELUCRARE cod=%s: plata fara CodInd/IdTrezor, sarita "
                        "(doc=%s data=%s suma=%s)", cod, nr_op, s_data, s_suma)
            continue
        if id_trezor in dedup:
            logger.info("PRELUCRARE cod=%s: plata duplicat %s, sarita", cod, id_trezor)
            continue

        data_plata = parse_english_date(s_data.strip())
        if data_plata is None:
            logger.info("PRELUCRARE cod=%s: data platii neparsabila «%s», sarita",
                        cod, s_data)
            continue

        cheie = cod_ai(cod, cod_ind)
        ind = indicatori.get(cheie)
        if ind is None:
            raise ValueError(f"Nu am găsit indicatorul {cheie} în FX_Indicatori.")

        id_unitate = ind["IdUnitate"]
        if id_unitate is not None and id_unitate not in programe:
            cursor.execute(_PROGRAM_SQL, (int(id_unitate),))
            row = cursor.fetchone()
            programe[id_unitate] = None if row is None else row["CodProgram"]
        program = programe.get(id_unitate) if id_unitate is not None else None
        if program is None:
            warnings.append(
                f"Unitatea {id_unitate} nu are «CodProgram»; plata {nr_op} "
                f"({cod_ind}) se scrie fără program."
            )

        clsf_fmt = _format_clsf_iban(ind["IndicatorFX"])
        ind_iban = f"{ind['SS']}-{clsf_fmt}" if clsf_fmt else ""

        suma = parse_amount(s_suma) or 0.0
        tip = "INCASARE" if suma < 0 else "PLATA"
        if tip == "INCASARE":
            are_incasari = True
        else:
            are_plati = True

        cursor.execute(_PLATI_INSERT_SQL, (
            int(r["ID"]), ind["IdClsf"], id_unitate, cheie, nr_op, cod, cod_ind,
            # VBA: rcPlati!Data_Plata = rcHis!DataFX -- data randului de ISTORIC, nu
            # cea din Observatii. `data:` e parsata doar ca sa fie VALIDATA (o data
            # neparsabila sare randul). Contraintuitiv, fidel, verificat in mdl_FX_Plati.
            r["DataFX"], ind_iban, clsf_fmt, program, id_trezor, suma, tip,
        ))
        dedup.add(id_trezor)
        scrise += 1

    return scrise, are_plati, are_incasari


# ===========================================================================
# PASUL 7 -- FX_Istoric_Actualizeaza_Rezolvat
# ===========================================================================
def step7_actualizeaza_rezolvat(cursor, ids: List[int]) -> int:
    """
    `Prelucrat = 1` pe randurile date: cele pe care le-a inserat rularea asta, plus
    (F33) cele pe care pasul 4a le-a consumat din stocul mai vechi de `Prelucrat = 0`.

    Restul randurilor care existau deja isi pastreaza ce aveau. VBA-ul marca doar
    randurile al caror `IDH` fusese scris in timpul acelei treceri; adaosul F33 exista
    fiindca un rand ajuns in tabel cu steagul jos PE ALTA CALE (migrare) nu era marcat
    niciodata si isi nastea cate un instantaneu la fiecare descarcare.

    In faza «propunere» asta se deruleaza inapoi cu tot restul, deci o propunere nu
    marcheaza NICIODATA istoricul ca prelucrat. Exact asta face rularea repetabila.
    """
    if not ids:
        return 0
    # `IN (%s, %s, ...)` construit din numarul de id-uri. Nu e f-string peste VALORI:
    # sunt tot atatea `%s` cate id-uri, iar valorile trec prin driver ca oriunde.
    locuri = ", ".join(["%s"] * len(ids))
    cursor.execute(
        f"UPDATE FX_Istoric SET Prelucrat = 1 WHERE ID IN ({locuri})",
        tuple(int(x) for x in ids))
    return len(ids)


# ===========================================================================
# PASUL 8 -- FX_Indicatori_Actualizare_Extrase
# ===========================================================================
# Port al functiei din mdl_FX_Tasks_Receive_DWN. Are exact doua instructiuni, si
# amandoua leaga `FX_Extrase` de platile scrise la pasul 5 prin referinta de trezorerie.
#
# Access:
#   UPDATE FX_Extrase INNER JOIN FX_Plati ON FX_Extrase.Referinta     = FX_Plati.Referinta_TREZOR
#      SET FX_Extrase.CodAI = [FX_Plati]![CodAI] WHERE FX_Extrase.CodAI Is Null
#   UPDATE FX_Extrase INNER JOIN FX_Plati ON FX_Extrase.ReferintaDest = FX_Plati.Referinta_TREZOR
#      SET FX_Extrase.CodAI = [FX_Plati]![CodAI] WHERE FX_Extrase.CodAI Is Null
#
# ORDINEA CELOR DOUA NU E O INTAMPLARE, si de-aia NU se contopesc intr-una singura cu
# `OR`. A doua trebuie sa VADA randurile pe care prima le-a completat deja: filtrul e
# `CodAI IS NULL` pe amandoua, deci un rand legat pe `Referinta` iese din multimea celei
# de-a doua. Contopite cu `OR`, `Referinta` si `ReferintaDest` ar concura pe acelasi rand
# si ar castiga oricare, tacut.
#
# NECONDITIONAT. Nu se pazeste cu niciun steag `are` si nu se sare cand pasul 5 n-a
# scris nimic -- exact ca originalul Access, care il cheama la coada fiecarei rulari.
# `FX_Extrase` poate purta randuri ramase in urma din rulari mai vechi, iar filtrul
# `CodAI IS NULL` inseamna ca fiecare trecere recupereaza tot restantul.
#
# NUMELE COLOANELOR sunt verificate in `MariaDB_Schema/000_DEMO.sql`, nu presupuse:
# `FX_Extrase`.`Referinta`, `.ReferintaDest`, `.CodAI`; `FX_Plati`.`Referinta_TREZOR`,
# `.CodAI`.
#
# DACA DOUA RANDURI `FX_Plati` IMPART O `Referinta_TREZOR`, MariaDB alege unul dintre ele
# fara sa spuna care -- exact comportamentul pe care il are si Access. Pasul 5
# deduplica platile chiar pe `Referinta_TREZOR` (`_PLATI_DEDUP_SQL`), deci situatia nu ar
# trebui sa apara; nota exista ca cine vine dupa sa stie ca a fost cantarita, nu ratata.
#
# `FX_Extrase` NU e filtrat pe angajament: nu poarta `CodAngajament`. Legatura se face
# prin `FX_Plati`, care e. Un extras al altui angajament nu se poate potrivi decat daca
# imparte referinta de trezorerie cu o plata a acestuia, adica in cazul de mai sus.
_PAS8_SQL = (
    "UPDATE FX_Extrase E INNER JOIN FX_Plati P ON E.Referinta = P.Referinta_TREZOR "
    "SET E.CodAI = P.CodAI WHERE E.CodAI IS NULL",
    "UPDATE FX_Extrase E INNER JOIN FX_Plati P ON E.ReferintaDest = P.Referinta_TREZOR "
    "SET E.CodAI = P.CodAI WHERE E.CodAI IS NULL",
)


def pas8_instructiuni() -> Tuple[str, ...]:
    """
    Cele doua instructiuni ale pasului 8, in ordinea in care se executa.

    Exista ca functie -- si nu doar ca o constanta citita direct din test -- fiindca
    testele care pinuiesc forma SQL-ului trebuie sa treaca prin ACELASI drum pe care il
    ia ruta. O constanta copiata in test ar ramane verde dupa ce ruta ar inceta sa o mai
    foloseasca.
    """
    return _PAS8_SQL


def step8_actualizeaza_extrase(cursor) -> int:
    """
    Completeaza `FX_Extrase.CodAI` din `FX_Plati`, pe referinta de trezorerie.

    Intoarce numarul TOTAL de randuri atinse de cele doua instructiuni. Ruleaza in
    aceeasi tranzactie cu pasii 1-7 si se deruleaza inapoi cu ei in faza «propunere»,
    ca orice altceva: nu are nicio ramura proprie de faza.
    """
    atinse = 0
    for sql in pas8_instructiuni():
        cursor.execute(sql)
        # `rowcount` dupa un UPDATE = randurile CHIAR schimbate. Un -1 (driverul nu
        # stie) nu se numara ca zero tacut: se trateaza ca zero si atat, fiindca
        # singurul lui consumator e contorul raportat operatorului.
        atinse += max(int(cursor.rowcount or 0), 0)
    return atinse
