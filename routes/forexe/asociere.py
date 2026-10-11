# routes/forexe/asociere.py
"""
Editorul de asociere R <-> H, disponibil ORICAND -- felia 0048-04.

DE CE EXISTA, SI DE CE E ALT FISIER DECAT prelucrare_asociere.py
================================================================
`prelucrare_asociere.py` rezolva asocierea IN TIMPUL unei descarcari: are o sarcina utila
in mana, ancoreaza fiecare instantaneu pe INDICELE randului lui in `TabelIstoric` (F24),
cere acoperire COMPLETA (tacerea nu are voie sa insemne «ignora-l») si nu scrie nimic pana
cand operatorul nu a raspuns pentru fiecare rand.

Fisierul asta rezolva alta problema: operatorul vrea sa se uite la legaturile deja facute
si sa le corecteze, fara sa fi descarcat nimic. Nu exista sarcina utila, deci nu exista
indice de rand -- ancora e `FX_Receptii_H.IDRH`, cheia reala din baza. Si nu exista
obligatia de acoperire: aici tacerea inseamna «lasa-l cum e», care e un raspuns adevarat
si sigur, spre deosebire de faza de ingestie unde ar fi fost o alegere ascunsa.

ACCESS AVEA EXACT DOUA GAZDE PENTRU ACELEASI PATRU PANOURI, si a doua e chiar asta.
`frmFX_DUBII_LISTA_HA.Form_Open` si `frmFX_DUBII_LISTA_RH.Form_Open` se ramifica pe
`isLoaded("frmFX_ASOC")`: gazda de ingestie e `frmFX_DUBII`, gazda de oricand e
`frmFX_ASOC`, cu `frmFX_ASOC_SUB` ca lista de recepții. `frmFX_ASOC` NU e in
`FX_System_Export/FORMS` -- codul ei nu poate fi citit. Cele patru subformulare SUNT
exportate, si ele poarta regulile; pierderea e aspectul gazdei, nu logica.

BLOCAREA (decizia operatorului, 29.08.2026; ingustata pe 18.09.2026)
====================================================================
«Daca exista ordonantari construite pe platile din R sau H, sau plati in acele date,
legaturile NU vor mai fi editabile, dar raman VIZIBILE.»

18.09.2026, operatorul a INGUSTAT regula: legaturile se editeaza cat timp NU EXISTA
ORDONANTARE; o simpla plata a angajamentului NU mai blocheaza nimic. Jumatatea «plati» de
mai jos e pastrata ca istoric al deciziei, dar `_BLOCAJE_SQL` si `motive_blocare` nu o
mai citesc. Motivul: platile se inregistreaza inaintea ordonantarii, iar pe un angajament
viu aproape fiecare instantaneu istoric are plati dupa el -- editorul ajungea sa nu poata
corecta tocmai legaturile pentru care exista.

Access avea chiar verificarea asta, si e COMENTATA in `frmFX_DUBII_LISTA_HA.btnDel_Click`:

    'If Nz(Me!origidrh, 0) > 0 Then
    '    If DCount("IDRP", "FX_Receptii_Plati", "IDRH=" & Me!origidrh) <> 0 Then
    '        MsgBox "Acest rand are Plati / Incasari asociate! Nu mai poate fi dez-asociat!"

Cheiata pe `IDRH` -- INSTANTANEUL, nu recepția -- prin `FX_Receptii_Plati`, tabel pe care
corectia C2 din fundament il declara GOL si scos complet din migrare. Regula supravietuieste,
sursa ei de date nu, deci se re-cheiaza pe tabelele vii. Verificat in `MariaDB_Schema/000_DEMO.sql`:

  * `FX_ORD.IDRR` si `FX_ORD.IDRH` exista pe MariaDB, amandoua nullable, comentate
    «PK ACCESS FX_Receptii_R / _H». `FX_Receptii_R.IDRR` si `FX_Receptii_H.IDRH` sunt
    `int(11) NOT NULL PRIMARY KEY` -- cheile Access pastrate -- deci se leaga direct.
    Scrise de `mdl_FX_ORD_Salvare` (liniile 284 si 362, marcate «v5» / «v6»).
  * platile pe care le-a consumat o ordonantare:
    `FX_ORD -> FX_ORD_TBL (IDORDP) -> FX_ORD_TBL_REC (IDORDTBLP) -> FX_Plati (IdPlataFX)`,
    amandoua salturile fiind constrangeri FK reale.
  * ATENTIE: `FX_ORD_TBL.IDRR` NU EXISTA pe MariaDB. Access il are; nu s-a migrat. Deci
    legatura ordonantare -> recepție traieste doar la nivel de CAP de ordonantare
    (`FX_ORD`), niciodata pe linie.
  * ATENTIE: in exportul Access, TOATE randurile `FX_ORD` poarta `IDRR = 0, IDRH = 0`.
    Pe date de vechimea aceea jumatatea «ordonantare» a regulii nu gaseste nimic si tot
    blocajul se sprijina pe jumatatea «plati». De-asta jumatatea aia trebuie sa fie buna.

CELE DOUA JUMATATI DE REGULA, asa cum le-a fixat operatorul:
  * fereastra (RETRASA pe 18.09.2026): ORICE plata a angajamentului cu
    `Data_plata >= DataH` a instantaneului. Motivul era §1.3 din fundament -- fiecare
    plata de dupa acea data citeste totalul receptiei asa cum statea atunci. Nu se mai
    aplica: platile raman pe ecran ca repere, dar nu mai ingheata nicio legatura.
  * granularitate: SE BLOCHEAZA DOAR INSTANTANEUL ATINS. Restul lantului aceleiasi
    recepții ramane editabil. Nu se inghetă recepția intreaga.

CE BLOCHEAZA ACUM (operator, 20.09.2026): doar o ordonantare -- angajamentul are cel putin
o LINIE de ordonantare (`FX_ORD_TBL`) al carei cap (`FX_ORD`, prin `IDORDP`) are `DataORD`
in ziua instantaneului sau dupa ea. Legatura e pe `FX_ORD.CodAngajament`, NU pe
`FX_ORD.IDRR` / `IDRH`: pe acelea nu scrie nimeni (exportul Access le are 0 peste tot,
`ord_edit.py` nu le pune), asa ca regula cheiata pe ele nu bloca NIMIC pe o baza reala --
defectul din 20.09.2026. Vezi `_BLOCAJE_SQL`.

SI PE CE NU SE APLICA, DELIBERAT
--------------------------------
Blocajul pazeste EDITAREA unei legaturi existente -- desprinderea sau re-tintirea unui
instantaneu care are deja `IDRR`. NU pazeste ATASAREA unui instantaneu inca neasezat.

Nu e o scapare, e necesar si e ce facea si Access: verificarea traia in `btnDel_Click` --
butonul de DESPRINDERE -- si nicaieri altundeva. Sub F10, rezultatul normal al fiecarei
descarcari e un teanc de instantanee istorice neasezate, toate cu plati dupa ele; daca
blocajul le-ar opri asezarea, formularul de ingestie s-ar bloca in prima zi si nimic nu
s-ar mai putea ingera. Asimetria e consemnata aici ca sa nu fie «reparata» din greseala.

CE SE VERIFICA SI CE DOAR SE SEMNALEAZA
=======================================
F14 (submultimea de indicatori) si F16 (multimile doar cresc) RIDICA, la fel ca in
ingestie: sunt absolute. Un instantaneu nu poate numi indicatori pe care recepția nu ii
are; un indicator nu poate disparea din lant.

F13 (data) NU MAI EXISTA DELOC -- retras ca veto pe 31.08.2026, sters si ca semn pe
09.09.2026. Se sprijinea pe premisa ca `FX_Receptii_R.DataR` spune cand a aparut receptia;
operatorul a corectat premisa: e un camp obisnuit, tastat pe site si schimbabil dupa
aceea, iar tabelul nu are nicio coloana cu momentul crearii (F29). Ca semn se aprindea pe
date perfect corecte, deci nu mai apare NICAIERI -- nici in `avertismente`, nici in
formular. Detaliile: docstring-ul lui `valideaza_plasarile`.

F15 (capatul lantului) doar AVERTIZEAZA aici. Fundamentul §1.5 chiar asa il descrie --
«aratat per recepție ca un SEMN» -- iar un editor in care nu poti desprinde ultimul
instantaneu (fiindca dupa desprindere lantul nu se mai inchide) nu poate face tocmai
lucrul pentru care exista. Access nu il verifica deloc la desprindere: `btnDel_Click` doar
re-promova ultimul rand ramas la `Final`, fara sa compare vreo suma.

VALUE CORRECTION (slice 0111, 06.10.2026)
=========================================
FOREXE's own history sometimes writes a wrong total on a header (`valoare: 0` over a line of
1635). The same file now also holds the route that lets the operator correct the WORKING values
(`FX_Receptii_H.Total`, `FX_Receptii.Valoare`) while `TotalOrig` / `ValoareOrig` keep what FOREXE
said: `POST /api/forexe/asociere/corectie`, at the end of the file. The reasoning and the rules
are in `docs/FUNDAMENT_Asociere_Receptii.md`, §1.8 and Part 6 (F35).
"""
import json
import math
import logging

from flask import request, g, current_app

from routes.auth.guard import require_session
from utils.database import get_kbot_connection
from utils import asociere_log as journal

from . import forexe_bp
from .prelucrare_helpers import SNAPSHOT_COUNTS_SQL
from .prelucrare_pasi import step4d_calculeaza_dif
from .prelucrare_asociere import (
    ACTIUNE_ASOCIAT,
    ACTIUNE_DESPRINS,
    ACTIUNE_IGNORAT,
    ACTIUNE_RECONSTITUIRE,
    ACTIUNE_STERGERE,
    DecizieInvalida,
    ancora,
    ancora_text,
    MSG_STARE_MODIFICATA,
    REASON_STARE_MODIFICATA,
    amprenta,
    citeste_receptii,
    marcheaza_reconstituirile_nesigure,
    materializeaza_reconstituite,
    recalculeaza_final,
    valideaza_plasarile,
    verifica_etichetele,
)

logger = logging.getLogger(__name__)

# A cincea actiune, `ACTIUNE_DESPRINS`: `btnDel_Click` din `frmFX_DUBII_LISTA_HA`. Its
# name lives in `prelucrare_asociere` since 01.10.2026, because the two-phase contract
# now accepts it too -- only on a correction, a snapshot that already has a link.
ACTIUNI =(ACTIUNE_ASOCIAT, ACTIUNE_DESPRINS, ACTIUNE_IGNORAT,
           ACTIUNE_STERGERE, ACTIUNE_RECONSTITUIRE)

# Codul-motiv cand clientul cere o schimbare pe un instantaneu blocat. NU e 400: cererea
# nu e gresita ca forma, doar clientul are un tablou invechit (sau o ordonantare a aparut
# intre citire si salvare). Acelasi tipar ca STARE_MODIFICATA si ALEGERE_UNITATE.
REASON_INSTANTANEU_BLOCAT = "INSTANTANEU_BLOCAT"


class InstantaneuBlocat(Exception):
    """O comanda atinge o legatura pe care regula de blocare o inghetă. Devine 409."""


# ===========================================================================
# Citirea tabloului
# ===========================================================================
# TOATE instantaneele angajamentului, nu doar cele neasezate: aici se editeaza legaturile
# EXISTENTE, deci cele asociate sunt chiar subiectul. Vin si cele marcate `Sters` (F17,
# «nu consemneaza nicio schimbare»), ca operatorul sa le poata rasgandi.
#
# F32: un antet fara nicio linie, care nu e stergere, NU e instantaneu -- e o eroare a
# vechii aplicatii Access -- si nu apare in editor. `SNAPSHOT_COUNTS_SQL` il lasa afara;
# o comanda care l-ar numi cade in `verifica_blocajele` cu «nu exista pe acest angajament».
_INSTANTANEE_SQL = (
    "SELECT H.IDRH, H.IDRR, H.IDH, H.DataH, H.Total, H.Descriere, H.TipReceptie, "
    "       COALESCE(H.Sters, 0) AS Sters, COALESCE(H.EsteStergere, 0) AS EsteStergere, "
    "       H.TotalOrig, H.CorectatDe, H.CorectatLa, H.CorectatMotiv "
    "FROM FX_Receptii_H H WHERE H.CodAngajament = %s AND " + SNAPSHOT_COUNTS_SQL + " "
    "ORDER BY H.DataH, H.IDRH"
)
_LINII_SQL = (
    "SELECT IDR, IDRH, CodIndicator, CodAI, CodSSI, IdClsf, Valoare, ValoareOrig "
    "FROM FX_Receptii WHERE CodAngajament = %s ORDER BY IDRH, CodIndicator"
)
# Platile angajamentului, pentru contextul din formular (aceeasi interogare ca la
# /api/forexe/receptii: fara alt filtru, confirmat in qFX_MAIN_REC_TT_PLATI).
_PLATI_SQL = (
    "SELECT Data_plata, Suma, NrOP FROM FX_Plati "
    "WHERE CodAngajament = %s ORDER BY Data_plata"
)

# ---------------------------------------------------------------------------
# Blocajele, o singura interogare pentru tot angajamentul.
#
# REGULA (operator, 20.09.2026): un instantaneu e blocat cand angajamentul are CEL PUTIN O
# LINIE DE ORDONANTARE (`FX_ORD_TBL`) cu data mai noua decat el. Data liniei e data capului
# ei, `FX_ORD.DataORD` -- `FX_ORD_TBL` nu are nicio coloana de data si nici `IDRR` pe
# MariaDB (vezi antet). Angajamentul se ia de pe CAP (`FX_ORD.CodAngajament`, NOT NULL),
# exact cum leaga `ord.py` liniile de angajament; `FX_ORD_TBL.CodAngajament` e nullable.
#
# DE CE NU MAI E PE `FX_ORD.IDRR` / `IDRH`: prima varianta (0048-04) cauta o ordonantare
# construita CHIAR pe instantaneu sau pe receptia lui, prin cele doua coloane. Nimeni nu le
# scrie: exportul Access le are 0 peste tot, iar editorul K-BOT (`ord_edit.py`, `_INSERT_ORD`)
# nu le pune deloc. Deci pe orice baza reala jumatatea aia nu gasea nimic si, dupa retragerea
# platilor din 18.09.2026, NIMIC nu mai era blocat. Defectul raportat pe 20.09.2026.
#
# Comparatia e pe ZI (`DATE(...)`), nu pe datetime: o ordonantare din aceeasi zi cu
# instantaneul i-a citit totalul (§1.3, «DataH <= data platii»), deci il ingheata; ora din
# `DataH` nu are voie sa o lase sa treaca. `>=`, aceeasi fereastra pe care o fixase
# operatorul pentru plati in 0048-04.
#
# `H.IDRR IS NOT NULL` in WHERE: un instantaneu neasezat nu are legatura, deci nu are ce
# sa fie blocat (vezi nota «SI PE CE NU SE APLICA» din antet).
#
# `O.DataORD IS NULL OR H.DataH IS NULL`: o data lipsa nu poate fi dovedita anterioara,
# deci se considera ulterioara. Conservator, si e ramura care blocheaza -- nu una care
# lasa sa treaca ceva nedovedit.
#
# NU se mai numara platile (18.09.2026): doar ordonantarile blocheaza. Vezi antetul.
# ---------------------------------------------------------------------------
_ORD_ULTERIOARE_SQL = (
    "FROM FX_ORD_TBL T JOIN FX_ORD O ON O.IDORDP = T.IDORDP "
    "WHERE O.CodAngajament = H.CodAngajament "
    "  AND (O.DataORD IS NULL OR H.DataH IS NULL OR DATE(O.DataORD) >= DATE(H.DataH))"
)
_BLOCAJE_SQL = (
    "SELECT H.IDRH, "
    " (SELECT COUNT(*) " + _ORD_ULTERIOARE_SQL + ") AS ord_n, "
    " (SELECT GROUP_CONCAT(DISTINCT O.NrORD ORDER BY O.NrORD SEPARATOR ', ') "
    + _ORD_ULTERIOARE_SQL + ") AS ord_nr, "
    " (SELECT MIN(O.DataORD) " + _ORD_ULTERIOARE_SQL + ") AS ord_data "
    "FROM FX_Receptii_H H "
    "WHERE H.CodAngajament = %s AND H.IDRR IS NOT NULL"
)


def _zi(v) -> str:
    """DateTime -> 'zz.ll.aaaa' pentru mesajele operatorului. Gol daca lipseste."""
    if v is None:
        return ""
    try:
        return v.strftime("%d.%m.%Y")
    except AttributeError:
        return str(v)


def motive_blocare(rand: dict) -> list:
    """
    Motivele pentru care legatura unui instantaneu nu mai poate fi editata.

    FUNCTIE PURA peste un rand de `_BLOCAJE_SQL` -- de-asta se poate testa fara baza de
    date, si de-asta regula se citeste intr-un singur loc in loc sa fie imprastiata prin
    SQL.

    Lista goala inseamna «editabil». Un singur motiv posibil: angajamentul are linii de
    ordonantare cu data mai noua decat instantaneul (`ord_n`), numite prin numerele
    capetelor lor (`ord_nr`) si prin cea mai veche data dintre ele (`ord_data`). Doar
    ordonantarile blocheaza; platile nu (18.09.2026).
    """
    motive = []

    if int(rand.get("ord_n") or 0) > 0:
        nr = (rand.get("ord_nr") or "").strip()
        data = _zi(rand.get("ord_data"))
        cap = ("Angajamentul are ordonanțarea nr. " + nr if nr
               else "Angajamentul are o ordonanțare")
        motive.append(
            cap + " din " + data + ", ulterioară acestui instantaneu."
            if data else
            cap + " fără dată, care nu poate fi dovedită anterioară."
        )

    # Platile NU mai blocheaza (18.09.2026): un rand vechi care mai poarta `plati` /
    # `plati_data` e ignorat aici, nu tradus in motiv. La fel `ord_h` / `ord_r` din prima
    # varianta a regulii (0048-04).
    return motive


def citeste_plati(cursor, cod: str) -> list:
    """
    Platile angajamentului, contextul in care se citeste orice lant.

    Publica fiindca o cheama SI ingestia (routes/forexe/prelucrare.py): reperele de plata
    sunt chiar rostul asezarii -- §1.3 din fundament, fiecare ordonantare citeste totalul
    receptiei ASA CUM STATEA la data platii -- deci ele trebuie sa fie pe ecran cand se
    aseaza, nu abia dupa.
    """
    cursor.execute(_PLATI_SQL, (cod,))
    return [{"data_plata": r["Data_plata"], "suma": float(r["Suma"] or 0),
             "nr_op": r["NrOP"] or ""} for r in cursor.fetchall()]


def citeste_blocaje(cursor, cod: str) -> dict:
    """{IDRH: [motiv, ...]} pentru instantaneele asociate ale angajamentului."""
    cursor.execute(_BLOCAJE_SQL, (cod,))
    out = {}
    for r in cursor.fetchall():
        motive = motive_blocare(r)
        if motive:
            out[int(r["IDRH"])] = motive
    return out


def citeste_instantanee(cursor, cod: str, blocaje: dict) -> list:
    """
    Toate instantaneele angajamentului, cu liniile lor si cu starea de blocare.

    Ancora e `idrh`, nu `rand_istoric`: nu exista sarcina utila din care sa vina un
    indice, si nici nu e nevoie -- randurile sunt deja in baza.

    `rand_istoric` se pune totusi, ca ALIAS al lui `idrh` -- exact ca in
    `normalizeaza_comenzi`. Functiile imprumutate din `prelucrare_asociere`
    (`valideaza_plasarile`, `materializeaza_reconstituite`) cheiaza instantaneele pe
    `rand_istoric`, si fara alias FIECARE salvare cadea cu `KeyError: 'rand_istoric'`
    la prima verificare de lant (defectul din 17.09.2026).
    """
    cursor.execute(_LINII_SQL, (cod,))
    linii = {}
    for r in cursor.fetchall():
        if r["IDRH"] is None:
            continue
        linii.setdefault(int(r["IDRH"]), []).append({
            "idr": int(r["IDR"]),
            "cod_indicator": r["CodIndicator"] or "",
            "cod_ai": r["CodAI"] or "",
            "cod_ssi": r["CodSSI"] or "",
            "id_clsf": r["IdClsf"],
            "valoare": float(r["Valoare"] or 0),
            # Slice 0111: what FOREXE said. None until the one-time query has filled it.
            "valoare_orig": None if r["ValoareOrig"] is None else float(r["ValoareOrig"]),
        })

    cursor.execute(_INSTANTANEE_SQL, (cod,))
    out = []
    for r in cursor.fetchall():
        idrh = int(r["IDRH"])
        motive = blocaje.get(idrh, [])
        out.append({
            "idrh": idrh,
            "rand_istoric": idrh,
            "idrr": int(r["IDRR"]) if r["IDRR"] is not None else 0,
            "idh": int(r["IDH"]) if r["IDH"] is not None else 0,
            "data_h": r["DataH"],
            "descriere": r["Descriere"] or "",
            "total": float(r["Total"] or 0),
            # Slice 0111: what FOREXE said, and who corrected the working value, when, why.
            "total_orig": None if r["TotalOrig"] is None else float(r["TotalOrig"]),
            "corectat_de": r["CorectatDe"] or "",
            "corectat_la": r["CorectatLa"],
            "corectat_motiv": r["CorectatMotiv"] or "",
            "tip_receptie": r["TipReceptie"] or "",
            "stergere": bool(r["EsteStergere"]),
            # F17: marcat de operator ca «nu consemneaza nicio schimbare».
            "ignorat": bool(r["Sters"]),
            "blocat": bool(motive),
            "motive": motive,
            "linii": linii.get(idrh, []),
        })

    journal.section("instantaneele angajamentului (%d)" % len(out))
    journal.table(
        ("IDRH", "IDRR", "DataH", "Total", "tip", "stergere", "ignorat", "blocat",
         "linii"),
        [(i["idrh"], i["idrr"], i["data_h"], i["total"], i["tip_receptie"],
          i["stergere"], i["ignorat"], i["blocat"],
          journal.sume_pe_indicator(i["linii"])) for i in out])
    for i in out:
        if i["motive"]:
            journal.line("IDRH %s blocat: %s", i["idrh"], " ".join(i["motive"]))
    return out


# ===========================================================================
# Forma comenzilor
# ===========================================================================
def normalizeaza_comenzi(brut) -> list:
    """
    Verifica forma lui `comenzi` si o intoarce curatata.

    Nimic nu se corecteaza -- se ridica. Aceeasi disciplina ca `normalizeaza_decizii`:
    o comanda pe care nu o putem citi nu e acelasi lucru cu absenta unei comenzi.

    DIFERENTA FATA DE INGESTIE: cheia e `idrh`, si lista poate fi PARTIALA. Aici tacerea
    inseamna «lasa legatura cum e», ceea ce e un raspuns adevarat; in ingestie ar fi fost
    o alegere ascunsa, de-asta acolo acoperirea e obligatorie.
    """
    if not isinstance(brut, list):
        raise DecizieInvalida("Câmpul «comenzi» trebuie să fie o listă.")
    if not brut:
        raise DecizieInvalida("Nu s-a trimis nicio comandă.")

    out = []
    vazute = set()
    for i, item in enumerate(brut):
        if not isinstance(item, dict):
            raise DecizieInvalida(f"«comenzi»[{i}] nu este un obiect.")
        try:
            idrh = int(item.get("idrh"))
        except (TypeError, ValueError) as err:
            raise DecizieInvalida(
                f"«comenzi»[{i}]: «idrh» lipsește sau nu este un număr.") from err
        if idrh in vazute:
            raise DecizieInvalida(
                f"Instantaneul {idrh} apare de două ori în «comenzi».")
        vazute.add(idrh)

        actiune = str(item.get("actiune") or "").strip()
        if actiune not in ACTIUNI:
            raise DecizieInvalida(
                f"«comenzi»[{i}]: «actiune» «{actiune}» nu este cunoscută "
                f"(permise: {', '.join(ACTIUNI)})."
            )

        idrr = item.get("idrr")
        eticheta = item.get("receptie_noua")
        if idrr is not None:
            try:
                idrr = int(idrr)
            except (TypeError, ValueError) as err:
                raise DecizieInvalida(
                    f"«comenzi»[{i}]: «idrr» nu este un număr.") from err
            if idrr == 0:
                idrr = None
        if eticheta is not None:
            eticheta = str(eticheta).strip()
            if eticheta == "":
                raise DecizieInvalida(
                    f"«comenzi»[{i}]: «receptie_noua» nu poate fi șir gol.")

        if actiune in (ACTIUNE_ASOCIAT, ACTIUNE_STERGERE):
            if (idrr is None) == (eticheta is None):
                raise DecizieInvalida(
                    f"«comenzi»[{i}] ({actiune}): trebuie exact una dintre «idrr» și "
                    f"«receptie_noua», nu ambele și nu niciuna."
                )
        elif actiune == ACTIUNE_RECONSTITUIRE:
            if eticheta is None:
                raise DecizieInvalida(
                    f"«comenzi»[{i}] (reconstituire): «receptie_noua» este obligatorie.")
            if idrr is not None:
                raise DecizieInvalida(
                    f"«comenzi»[{i}] (reconstituire): «idrr» nu are sens — recepția "
                    f"încă nu există.")
        else:   # desprins, ignorat
            if idrr is not None or eticheta is not None:
                raise DecizieInvalida(
                    f"«comenzi»[{i}] ({actiune}): nu poate purta o recepție.")

        out.append({
            "idrh": idrh,
            # Alias, ca sa putem refolosi neschimbate functiile din prelucrare_asociere
            # care cheiaza pe `rand_istoric`. Aici ancora ESTE `idrh` (F24 nu se aplica:
            # nu exista sarcina utila).
            "rand_istoric": idrh,
            "actiune": actiune,
            "idrr": idrr,
            "receptie_noua": eticheta,
        })
    return out


def verifica_blocajele(comenzi: list, instantanee: list, blocaje: dict) -> None:
    """
    Nicio comanda nu are voie sa atinga o legatura blocata.

    Se ruleaza pe SERVER chiar daca formularul stie deja blocajele: intre citire si
    salvare poate aparea o ordonantare sau o plata, iar clientul nu are de unde sa afle.
    """
    dupa_idrh = {i["idrh"]: i for i in instantanee}
    lovite = []
    for c in comenzi:
        inst = dupa_idrh.get(c["idrh"])
        if inst is None:
            raise DecizieInvalida(
                f"Instantaneul {c['idrh']} nu există pe acest angajament.")
        # Un instantaneu inca neasezat nu are legatura, deci nu are ce sa fie blocat.
        if not inst["idrr"]:
            continue
        motive = blocaje.get(c["idrh"])
        if motive:
            lovite.append((c["idrh"], inst["data_h"], motive))

    if lovite:
        bucati = []
        for idrh, data_h, motive in lovite:
            bucati.append(
                "instantaneul din " + _zi(data_h) + ": " + " ".join(motive))
        raise InstantaneuBlocat(
            "Legăturile următoare nu mai pot fi modificate — " + " | ".join(bucati)
        )


# ===========================================================================
# Aplicarea
# ===========================================================================
_H_ASOCIAZA_SQL = (
    "UPDATE FX_Receptii_H SET IDRR = %s, Sters = 0, EsteStergere = %s WHERE IDRH = %s"
)
_H_DESPRINDE_SQL = (
    "UPDATE FX_Receptii_H SET IDRR = NULL, Sters = 0, EsteStergere = 0 WHERE IDRH = %s"
)
_H_IGNORA_SQL = (
    "UPDATE FX_Receptii_H SET IDRR = NULL, Sters = 1, EsteStergere = 0 WHERE IDRH = %s"
)
_R_MARCHEAZA_STEARSA_SQL = "UPDATE FX_Receptii_R SET Sters = 1 WHERE IDRR = %s"
# Desprinderea randului de stergere lasa recepția «nestearsa» din nou: steagul de pe R nu
# e o parere, e umbra unui instantaneu anume, iar daca acela pleaca umbra pleaca cu el.
# Reconstructed receptions too (operator, 25.09.2026): starting one no longer means it was
# deleted, so its Sters follows its deletion row like everyone else's.
_R_DEMARCHEAZA_SQL = (
    "UPDATE FX_Receptii_R SET Sters = 0 WHERE IDRR = %s "
    "  AND NOT EXISTS (SELECT 1 FROM (SELECT IDRH FROM FX_Receptii_H "
    "                                 WHERE IDRR = %s AND EsteStergere = 1) X)"
)


def _lanturi_rezultate(comenzi: list, instantanee: list, tinta) -> dict:
    """
    Lanturile asa cum vor arata DUPA aplicarea comenzilor, calculate in memorie.

    Se valideaza inainte de a scrie ceva, exact ca in ingestie: o asociere gresita e
    tacuta si permanenta (F12), deci nu se scrie si apoi se verifica.

    Intoarce {IDRR: [instantaneu, ...]} DOAR pentru recepțiile al caror lant se schimba.
    O recepție neatinsa nu se valideaza -- altfel o incalcare veche, care exista deja in
    baza, ar bloca o corectie care nu are nicio legatura cu ea.
    """
    dupa_idrh = {i["idrh"]: i for i in instantanee}
    # Starea de acum: fiecare recepție cu instantaneele ei.
    acum = {}
    for inst in instantanee:
        if inst["idrr"]:
            acum.setdefault(inst["idrr"], []).append(inst)

    afectate = set()
    mutari = {}     # idrh -> noul IDRR (0 = niciunul)
    # The deletion flag AFTER the commands: a row un-marked (or marked) in this save must be
    # seen with its new flag, or a former deletion row moved to a new reception would still
    # be read as one.
    stergere_noua = {c["idrh"]: c["actiune"] == ACTIUNE_STERGERE for c in comenzi}

    def _cu_steag(inst):
        if inst["idrh"] not in stergere_noua:
            return inst
        return dict(inst, stergere=stergere_noua[inst["idrh"]])

    for c in comenzi:
        inst = dupa_idrh[c["idrh"]]
        if inst["idrr"]:
            afectate.add(inst["idrr"])
        if c["actiune"] in (ACTIUNE_DESPRINS, ACTIUNE_IGNORAT):
            mutari[c["idrh"]] = 0
        else:
            nou = tinta(c)
            mutari[c["idrh"]] = nou
            afectate.add(nou)

    lanturi = {}
    for idrr in afectate:
        lant = []
        for inst in acum.get(idrr, []):
            if mutari.get(inst["idrh"], idrr) == idrr:
                lant.append(_cu_steag(inst))
        for idrh, nou in mutari.items():
            if nou == idrr and dupa_idrh[idrh]["idrr"] != idrr:
                lant.append(_cu_steag(dupa_idrh[idrh]))
        lanturi[idrr] = lant
    return lanturi


def aplica_comenzi(cursor, cod: str, comenzi: list, instantanee: list,
                   avertismente: list) -> dict:
    """
    Aplica un set PARTIAL de comenzi peste legaturile existente. Intoarce numaratorile.

    Ordinea e aceeasi ca in ingestie, si din aceleasi motive:
      1. etichetele si reconstituirile, ca fiecare eticheta sa fie un `IDRR` real;
      2. lanturile REZULTATE, calculate in memorie si validate;
      3. scrierea;
      4. `Final` / `Partial`, o singura data per recepție atinsa;
      5. F28.
    """
    journal.section("comenzile operatorului (%d)" % len(comenzi))
    dupa_idrh = {i["idrh"]: i for i in instantanee}
    journal.table(
        ("IDRH", "acțiune", "IDRR cerut", "receptie_noua", "IDRR de acum", "DataH",
         "Total"),
        [(c["idrh"], c["actiune"], c["idrr"], c["receptie_noua"],
          dupa_idrh.get(c["idrh"], {}).get("idrr"),
          dupa_idrh.get(c["idrh"], {}).get("data_h"),
          dupa_idrh.get(c["idrh"], {}).get("total")) for c in comenzi])

    etichete = verifica_etichetele(comenzi)
    noi = materializeaza_reconstituite(cursor, cod, comenzi, instantanee,
                                       etichete, avertismente)

    toate = {r["idrr"]: r for r in citeste_receptii(cursor, cod)}

    def _tinta(c: dict) -> int:
        if c["idrr"] is not None:
            if c["idrr"] not in toate:
                raise DecizieInvalida(
                    f"Recepția {c['idrr']} nu există pe acest angajament.")
            return c["idrr"]
        return noi[c["receptie_noua"]]

    lanturi = _lanturi_rezultate(comenzi, instantanee, _tinta)

    # F14 / F16 ridica; F13 si F15 doar avertizeaza -- vezi nota din antet.
    valideaza_plasarile(lanturi, toate, f15_ca_avertisment=True,
                        avertismente=avertismente)

    numarat = {"asociat": 0, "desprins": 0, "ignorat": 0, "stergere": 0,
               "reconstituit": len(noi)}
    de_recalculat = set(lanturi)

    for c in comenzi:
        idrh = c["idrh"]
        if c["actiune"] == ACTIUNE_DESPRINS:
            cursor.execute(_H_DESPRINDE_SQL, (idrh,))
            numarat["desprins"] += 1
        elif c["actiune"] == ACTIUNE_IGNORAT:
            cursor.execute(_H_IGNORA_SQL, (idrh,))
            numarat["ignorat"] += 1
        elif c["actiune"] == ACTIUNE_RECONSTITUIRE:
            cursor.execute(_H_ASOCIAZA_SQL, (_tinta(c), 0, idrh))
            numarat["asociat"] += 1
        else:
            idrr = _tinta(c)
            este_stergere = 1 if c["actiune"] == ACTIUNE_STERGERE else 0
            cursor.execute(_H_ASOCIAZA_SQL, (idrr, este_stergere, idrh))
            if este_stergere:
                cursor.execute(_R_MARCHEAZA_STEARSA_SQL, (idrr,))
                numarat["stergere"] += 1
            else:
                numarat["asociat"] += 1

    # Steagul `Sters` de pe recepțiile care si-au pierdut randul de stergere.
    for idrr in sorted(de_recalculat):
        cursor.execute(_R_DEMARCHEAZA_SQL, (idrr, idrr))
        if cursor.rowcount:
            journal.line("recepția %s nu mai are rând de ștergere: Sters = 0", idrr)

    for idrr in sorted(de_recalculat):
        recalculeaza_final(cursor, idrr)

    # Pasul 4d, pe fiecare lant atins (felia 0065). Ingestia il ruleaza dupa 4c si
    # refacerea dupa ce a scris; editorul de oricand NU il rula, deci un instantaneu
    # asezat de aici ramanea cu `DIFH`/`DIF` NULL -- iar `SUM(DIF)` din ordonantare
    # (`qFX_ORD_REC_ANT`) si eticheta din Receptii nu il vedeau. Operatorul a gasit o
    # receptie lipsa din total: 25.410 in loc de 29.645.
    for idrr in sorted(de_recalculat):
        step4d_calculeaza_dif(cursor, cod, idrr)

    marcheaza_reconstituirile_nesigure(cursor, cod, avertismente)
    journal.section("ce s-a scris")
    journal.line("legături: %s", numarat)
    for a in avertismente:
        journal.line("avertisment: %s", a)
    return numarat


# ===========================================================================
# Rutele
# ===========================================================================
def _json_utf8(payload, status):
    """JSON cu diacritice LITERALE: motivele de blocare sunt text romanesc."""
    body = json.dumps(payload, ensure_ascii=False, default=_serializeaza)
    return current_app.response_class(body, status=status,
                                      mimetype="application/json")


def _serializeaza(v):
    """`datetime` -> ISO. Restul ridica, ca sa nu plece tacut un `str(obiect)`."""
    if hasattr(v, "isoformat"):
        return v.isoformat()
    raise TypeError(f"Tip neserializabil în răspuns: {type(v).__name__}")


@forexe_bp.route("/api/forexe/asociere", methods=["GET"])
@require_session
@journal.traced("asociere GET")
def get_asociere():
    """
    Tabloul de asociere al unui angajament, citit DIRECT din baza.

    Query: cod (obligatoriu) = CodAngajament.
    Raspuns: { cod, amprenta, receptii: [...], instantanee: [...], plati: [...] }.

    Un angajament fara recepții NU e 404 — e 200 cu liste goale, ca la
    /api/forexe/receptii: «nu are» e un raspuns, nu o eroare.
    """
    cod = request.args.get("cod")
    if cod is None or str(cod).strip() == "":
        return _json_utf8({"error": "Parametru lipsă: cod"}, 400)
    cod = str(cod).strip()

    db_name = g.session.db_name
    journal.note(dc=db_name, user=g.session.username, cod=cod)
    conn = None
    try:
        conn = get_kbot_connection(db_name)
        # Cursor pe DICTIONAR: tot fisierul asta -- si tot ce imprumuta din
        # prelucrare_asociere -- citeste randurile pe NUME de coloana (r["IDRH"]).
        # Un cursor obisnuit intoarce tupluri, si prima citire moare cu
        # «tuple indices must be integers»; exact drumul pe care a cazut GET-ul.
        cursor = conn.cursor(dictionary=True)

        amp = amprenta(cursor, cod)
        receptii = citeste_receptii(cursor, cod)
        blocaje = citeste_blocaje(cursor, cod)
        instantanee = citeste_instantanee(cursor, cod, blocaje)

        plati = citeste_plati(cursor, cod)

        logger.info(
            "[forexe.asociere] %s: cod=%s -> %s recepții, %s instantanee "
            "(%s blocate), %s plăți",
            db_name, cod, len(receptii), len(instantanee), len(blocaje), len(plati))
        return _json_utf8({
            "cod": cod,
            "amprenta": amp,
            "receptii": receptii,
            "instantanee": instantanee,
            "plati": plati,
        }, 200)
    except Exception as e:
        # Fara inghitire: o lista goala ar minti operatorul ca nu are ce edita.
        journal.refusal("citirea tabloului a căzut: %s", e)
        logger.error(f"[forexe.asociere] {e}", exc_info=True)
        return _json_utf8(
            {"error": f"Eroare la citirea asocierii: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()


@forexe_bp.route("/api/forexe/asociere", methods=["POST"])
@require_session
@journal.traced("asociere POST")
def post_asociere():
    """
    Aplica un set PARTIAL de modificari peste legaturile R <-> H.

    Corp: { cod, amprenta, comenzi: [ {idrh, actiune, idrr?, receptie_noua?} ] }.

    O SINGURA TRANZACTIE, si nicio faza de propunere: aici nu exista sarcina utila de
    re-trimis, deci nu exista nimic de derulat inapoi si nimic de re-rulat. Amprenta
    ramane, fiindca doua sesiuni pot edita acelasi angajament in acelasi timp.
    """
    date = request.get_json(silent=True)
    if not isinstance(date, dict):
        return _json_utf8({"error": "Corp JSON lipsă sau nevalid."}, 400)

    cod = str(date.get("cod") or "").strip()
    if cod == "":
        return _json_utf8({"error": "Câmp lipsă: cod"}, 400)
    amp_client = str(date.get("amprenta") or "").strip()
    if amp_client == "":
        return _json_utf8({"error": "Câmp lipsă: amprenta"}, 400)

    db_name = g.session.db_name
    journal.note(dc=db_name, user=g.session.username, cod=cod)
    conn = None
    try:
        comenzi = normalizeaza_comenzi(date.get("comenzi"))

        conn = get_kbot_connection(db_name)
        # Cursor pe DICTIONAR: tot fisierul asta -- si tot ce imprumuta din
        # prelucrare_asociere -- citeste randurile pe NUME de coloana (r["IDRH"]).
        # Un cursor obisnuit intoarce tupluri, si prima citire moare cu
        # «tuple indices must be integers»; exact drumul pe care a cazut GET-ul.
        cursor = conn.cursor(dictionary=True)

        # Amprenta INAINTE de orice scriere; altfel ar descrie starea scrisa.
        amp_server = amprenta(cursor, cod)
        if amp_server != amp_client:
            conn.rollback()
            journal.refusal("amprenta nu se potrivește: formularul are %s, baza are "
                            "%s. Nu s-a scris nimic.", amp_client, amp_server)
            return _json_utf8({"error": MSG_STARE_MODIFICATA,
                               "reason": REASON_STARE_MODIFICATA}, 409)

        blocaje = citeste_blocaje(cursor, cod)
        instantanee = citeste_instantanee(cursor, cod, blocaje)
        verifica_blocajele(comenzi, instantanee, blocaje)

        avertismente = []
        numarat = aplica_comenzi(cursor, cod, comenzi, instantanee, avertismente)
        journal.line("commit")
        conn.commit()

        # Amprenta noua, ca formularul sa poata continua fara sa reincarce tot.
        amp_nou = amprenta(cursor, cod)

        logger.info("[forexe.asociere] %s: cod=%s -> %s", db_name, cod, numarat)
        return _json_utf8({"cod": cod, "amprenta": amp_nou,
                           "scrise": numarat, "avertismente": avertismente}, 200)
    except InstantaneuBlocat as e:
        if conn is not None:
            conn.rollback()
        journal.refusal("%s -- nu s-a scris nimic (rollback)", e)
        return _json_utf8({"error": str(e),
                           "reason": REASON_INSTANTANEU_BLOCAT}, 409)
    except DecizieInvalida as e:
        if conn is not None:
            conn.rollback()
        journal.refusal("%s -- nu s-a scris nimic (rollback)", e)
        return _json_utf8({"error": str(e)}, 400)
    except Exception as e:
        if conn is not None:
            conn.rollback()
        journal.refusal("salvarea a căzut: %s -- rollback", e)
        logger.error(f"[forexe.asociere] {e}", exc_info=True)
        return _json_utf8({"error": f"Eroare la salvarea asocierii: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()


# ===========================================================================
# Value correction (slice 0111)
# ===========================================================================
# WHY. FOREXE's own history sometimes writes a WRONG total on a reception header: the row
# «Receptie: ..., valoare: 0, (activ:true)» of AAB3MEF2MG2 (reception of 12.06.2026) says 0
# while the line under it says 1635. `FX_Receptii_H.Total` copies that figure, so the chain of
# the reception does not close, the snapshot cannot be matched to its reception by value, and
# DIFH / DIF -- what the ordonantare sums -- start from a false number.
#
# WHAT. The operator corrects the WORKING columns -- `FX_Receptii_H.Total` and
# `FX_Receptii.Valoare`, the ones every reader already uses -- and the ORIGINAL columns
# (`TotalOrig`, `ValoareOrig`) keep what FOREXE said. A row is «corrected» when its working
# value differs from its original. The audit (who / when / why) lives on the header:
# `CorectatDe`, `CorectatLa`, `CorectatMotiv`; one save is one header + its lines.
#
# WHAT IS NEVER TOUCHED. `FX_Istoric` -- the evidence of what FOREXE said -- and the HASH of
# the lines (computed at birth, read by nobody, see the worklog of slice 0111). A download or a
# rebuild cannot undo a correction: the history is read only once per row (`Prelucrat`), the
# download stops at the newest known `DataFX`, and the rebuild from history writes only what is
# missing.
#
# THE RULES, all enforced HERE, not in the form:
#   * a reason is required;
#   * the corrected total must equal the sum of the corrected lines (to two decimals) -- a
#     BLOCKING error, not a warning: the correction must not leave a header and its lines
#     telling different stories. Header and lines are saved together, all or nothing;
#   * a placed snapshot that the ordonantare rule freezes (`citeste_blocaje`) cannot be
#     corrected: an ordonantare read the total of that snapshot (§1.3 of the fundament). An
#     unplaced one can: nothing has read it yet;
#   * the deletion row has no value of its own to correct;
#   * the client sends the values it SAW; if the base holds others, nothing is written (409,
#     the same code as a stale fingerprint) -- two sessions cannot overwrite each other silently;
#   * a placed snapshot gets step 4d re-run in the same transaction, because DIF is a stored
#     column and the ordonantare sums DIF, not Valoare.
REASON_VALUE_CHANGED = REASON_STARE_MODIFICATA
MAX_REASON_LENGTH = 500

_H_FILL_ORIG_SQL = (
    "UPDATE FX_Receptii_H SET TotalOrig = Total WHERE IDRH = %s AND TotalOrig IS NULL"
)
_LINES_FILL_ORIG_SQL = (
    "UPDATE FX_Receptii SET ValoareOrig = Valoare WHERE IDRH = %s AND ValoareOrig IS NULL"
)
_H_CORRECT_SQL = (
    "UPDATE FX_Receptii_H SET Total = %s, CorectatDe = %s, CorectatLa = NOW(), "
    "CorectatMotiv = %s WHERE IDRH = %s"
)
_LINE_CORRECT_SQL = "UPDATE FX_Receptii SET Valoare = %s WHERE IDR = %s AND IDRH = %s"
# A line born in this download has no key the client knows yet; it is named by its indicator.
_LINE_CORRECT_BY_INDICATOR_SQL = (
    "UPDATE FX_Receptii SET Valoare = %s WHERE IDRH = %s AND CodIndicator = %s"
)


class ValueChanged(Exception):
    """The base holds other values than the ones the client saw. Becomes 409."""


def _number(value, name: str) -> float:
    """A JSON number, finite. A string or a boolean is refused, never converted."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DecizieInvalida(f"Câmpul «{name}» lipsește sau nu este un număr.")
    if not math.isfinite(value):
        raise DecizieInvalida(f"Câmpul «{name}» nu este un număr finit.")
    return float(value)


def normalize_correction(raw) -> dict:
    """The shape of the request, cleaned. Nothing is fixed silently -- a bad field is refused."""
    if not isinstance(raw, dict):
        raise DecizieInvalida("Corp JSON lipsă sau nevalid.")
    try:
        idrh = int(raw.get("idrh"))
    except (TypeError, ValueError) as err:
        raise DecizieInvalida("Câmpul «idrh» lipsește sau nu este un număr.") from err

    reason = str(raw.get("motiv") or "").strip()
    if reason == "":
        raise DecizieInvalida("Motivul corecției este obligatoriu.")
    if len(reason) > MAX_REASON_LENGTH:
        raise DecizieInvalida(
            f"Motivul are {len(reason)} caractere; cel mult {MAX_REASON_LENGTH}.")

    raw_lines = raw.get("linii")
    if not isinstance(raw_lines, list):
        raise DecizieInvalida("Câmpul «linii» trebuie să fie o listă.")
    lines = []
    seen = set()
    for i, item in enumerate(raw_lines):
        if not isinstance(item, dict):
            raise DecizieInvalida(f"«linii»[{i}] nu este un obiect.")
        try:
            idr = int(item.get("idr"))
        except (TypeError, ValueError) as err:
            raise DecizieInvalida(
                f"«linii»[{i}]: «idr» lipsește sau nu este un număr.") from err
        if idr in seen:
            raise DecizieInvalida(f"Linia {idr} apare de două ori în «linii».")
        seen.add(idr)
        lines.append({
            "idr": idr,
            "valoare": _number(item.get("valoare"), f"linii[{i}].valoare"),
            "valoare_veche": _number(item.get("valoare_veche"), f"linii[{i}].valoare_veche"),
        })

    return {
        "idrh": idrh,
        "total": _number(raw.get("total"), "total"),
        "total_vechi": _number(raw.get("total_vechi"), "total_vechi"),
        "motiv": reason,
        "linii": lines,
    }


def plan_correction(snapshots: list, blocks: dict, request: dict) -> dict:
    """
    Checks the correction against the snapshot as it is in the base and returns what to write:
    {snapshot, total, changes: [(idr, value)], sum_lines}.

    A PURE function over what `citeste_instantanee` / `citeste_blocaje` read, so the rules can
    be read in one place and checked without a database.
    """
    by_idrh = {s["idrh"]: s for s in snapshots}
    snap = by_idrh.get(request["idrh"])
    if snap is None:
        raise DecizieInvalida(f"Instantaneul {request['idrh']} nu există pe acest angajament.")
    if snap["stergere"]:
        raise DecizieInvalida("Instantaneul de ștergere nu are o valoare de corectat.")

    reasons = blocks.get(snap["idrh"])
    if snap["idrr"] and reasons:
        raise InstantaneuBlocat(
            "Valoarea nu mai poate fi corectată — instantaneul din " + _zi(snap["data_h"]) +
            ": " + " ".join(reasons))

    if round(snap["total"], 2) != round(request["total_vechi"], 2):
        raise ValueChanged("Totalul instantaneului s-a schimbat între citire și salvare.")

    db_lines = {l["idr"]: l for l in snap["linii"]}
    final = {idr: l["valoare"] for idr, l in db_lines.items()}
    changes = []
    for item in request["linii"]:
        line = db_lines.get(item["idr"])
        if line is None:
            raise DecizieInvalida(f"Linia {item['idr']} nu aparține acestui instantaneu.")
        if round(line["valoare"], 2) != round(item["valoare_veche"], 2):
            raise ValueChanged("O linie a instantaneului s-a schimbat între citire și salvare.")
        if round(item["valoare"], 2) != round(line["valoare"], 2):
            changes.append((item["idr"], item["valoare"]))
        final[item["idr"]] = item["valoare"]

    sum_lines = round(sum(final.values()), 2)
    total = request["total"]
    if abs(round(total, 2) - sum_lines) >= 0.005:
        raise DecizieInvalida(
            f"Totalul ({round(total, 2):.2f}) nu este egal cu suma liniilor ({sum_lines:.2f}). "
            f"Corectați totalul și liniile împreună, apoi salvați.")

    if not changes and round(total, 2) == round(snap["total"], 2):
        raise DecizieInvalida("Nu s-a modificat nicio valoare.")

    return {"snapshot": snap, "total": total, "changes": changes, "sum_lines": sum_lines}


def apply_correction(cursor, cod: str, plan: dict, reason: str, user: str) -> dict:
    """
    Writes a checked correction. The originals are filled FIRST (only where still NULL), so
    the value FOREXE gave is never lost, whatever the one-time query has or has not done yet.
    """
    snap = plan["snapshot"]
    idrh = snap["idrh"]
    journal.section("value correction, snapshot %s" % idrh)
    journal.line("total %s -> %s, %s linii schimbate, motiv: %s",
                 snap["total"], plan["total"], len(plan["changes"]), reason)

    cursor.execute(_H_FILL_ORIG_SQL, (idrh,))
    cursor.execute(_LINES_FILL_ORIG_SQL, (idrh,))
    for idr, value in plan["changes"]:
        cursor.execute(_LINE_CORRECT_SQL, (value, idr, idrh))
    cursor.execute(_H_CORRECT_SQL, (plan["total"], user, reason, idrh))

    dif_recomputed = False
    if snap["idrr"]:
        # DIF is a stored column and the ordonantare sums DIF (qFX_ORD_REC_ANT), not Valoare.
        step4d_calculeaza_dif(cursor, cod, snap["idrr"])
        dif_recomputed = True
    return {"total": 1, "linii": len(plan["changes"]), "dif_recalculat": dif_recomputed}


def apply_download_corrections(cursor, corrections: list, snapshots: list, user: str) -> int:
    """
    The corrections an operator made IN THE DOWNLOAD WINDOW, applied in phase two (slice 0111).

    `corrections` = the decisions that carry a `corectie`; `snapshots` = what
    `prelucrare_asociere.citeste_instantanee` read inside THIS transaction. A snapshot born in
    this run has no key the client could know (its IDRH dies with phase one's rollback), so a
    correction travels with the decision that names it -- by `rand_istoric` or `idh` -- and its
    lines are named by INDICATOR. Only the snapshots the download brought: an older, already
    written one is corrected in the anytime editor (`post_correction`).

    The rules are the anytime editor's (`plan_correction`): reason, total = sum of the lines,
    no deletion row. No frozen-link check and no stale-value check are needed here: the snapshot
    is still unplaced (nothing read it), and phase two already verified the fingerprint.

    Step 4d is not re-run here: nothing is placed yet, and the route runs it on every touched
    reception after the decisions. Returns how many snapshots were corrected.
    """
    by_anchor = {}
    for snap in snapshots:
        try:
            by_anchor[ancora(snap)] = snap
        except DecizieInvalida:
            continue

    done = 0
    for decision in corrections:
        if decision.get("ancora_idrh") is not None:
            raise DecizieInvalida(
                "Corecția de valoare se poate face aici doar pe instantaneele aduse de "
                "descărcare; unul deja scris se corectează în editorul de asociere.")
        anchor = ancora(decision)
        snap = by_anchor.get(anchor)
        if snap is None:
            raise DecizieInvalida(
                "Corecția de valoare: " + ancora_text(anchor) +
                " nu se află printre instantaneele descărcării.")
        correction = decision["corectie"]

        # The lines get a position as key for the shared rule check, and are mapped back to
        # their indicator for the write. Two lines of one indicator cannot be told apart.
        indicator_of = {}
        lines = []
        for k, line in enumerate(snap["linii"], start=1):
            if line["cod_indicator"] in indicator_of.values():
                raise DecizieInvalida(
                    "Instantaneul are două linii pe indicatorul " + line["cod_indicator"] +
                    "; valoarea nu se poate corecta din descărcare.")
            indicator_of[k] = line["cod_indicator"]
            lines.append(dict(line, idr=k))
        key_of = {ind: k for k, ind in indicator_of.items()}

        request_lines = []
        for item in correction["linii"]:
            k = key_of.get(item["cod_indicator"])
            if k is None:
                raise DecizieInvalida(
                    "Corecția numește indicatorul " + item["cod_indicator"] +
                    ", pe care instantaneul nu îl are.")
            request_lines.append({
                "idr": k, "valoare": item["valoare"],
                "valoare_veche": lines[k - 1]["valoare"]})

        view = {"idrh": snap["idrh"], "idrr": 0, "stergere": snap["stergere"],
                "total": snap["total"], "data_h": snap["data_h"], "linii": lines}
        plan = plan_correction([view], {}, {
            "idrh": snap["idrh"], "total": correction["total"],
            "total_vechi": snap["total"], "motiv": correction["motiv"],
            "linii": request_lines})

        journal.section("download correction, snapshot %s" % ancora_text(anchor))
        journal.line("total %s -> %s, %s lines changed, reason: %s",
                     snap["total"], plan["total"], len(plan["changes"]), correction["motiv"])
        cursor.execute(_H_FILL_ORIG_SQL, (snap["idrh"],))
        cursor.execute(_LINES_FILL_ORIG_SQL, (snap["idrh"],))
        for k, value in plan["changes"]:
            cursor.execute(_LINE_CORRECT_BY_INDICATOR_SQL,
                           (value, snap["idrh"], indicator_of[k]))
        cursor.execute(_H_CORRECT_SQL,
                       (plan["total"], user, correction["motiv"], snap["idrh"]))
        done += 1
    return done


@forexe_bp.route("/api/forexe/asociere/corectie", methods=["POST"])
@require_session
@journal.traced("asociere corectie POST")
def post_correction():
    """
    Corrects the value of ONE snapshot (header total + lines), all or nothing.

    Body: { cod, idrh, total_vechi, total, linii: [ {idr, valoare_veche, valoare} ], motiv }.
    `*_vechi` are the values the client saw; the answer is 409 when the base holds others.
    Answer 200: { cod, idrh, scrise: {total, linii, dif_recalculat} }.
    """
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _json_utf8({"error": "Corp JSON lipsă sau nevalid."}, 400)
    cod = str(data.get("cod") or "").strip()
    if cod == "":
        return _json_utf8({"error": "Câmp lipsă: cod"}, 400)

    db_name = g.session.db_name
    user = str(g.session.username or "")[:255]
    journal.note(dc=db_name, user=user, cod=cod)
    conn = None
    try:
        req = normalize_correction(data)

        conn = get_kbot_connection(db_name)
        cursor = conn.cursor(dictionary=True)

        blocks = citeste_blocaje(cursor, cod)
        snapshots = citeste_instantanee(cursor, cod, blocks)
        plan = plan_correction(snapshots, blocks, req)
        written = apply_correction(cursor, cod, plan, req["motiv"], user)
        journal.line("commit")
        conn.commit()

        logger.info("[forexe.asociere] %s: cod=%s correction idrh=%s -> %s",
                    db_name, cod, req["idrh"], written)
        return _json_utf8({"cod": cod, "idrh": req["idrh"], "scrise": written}, 200)
    except ValueChanged as e:
        if conn is not None:
            conn.rollback()
        journal.refusal("%s -- nu s-a scris nimic (rollback)", e)
        return _json_utf8({"error": MSG_STARE_MODIFICATA,
                           "reason": REASON_VALUE_CHANGED}, 409)
    except InstantaneuBlocat as e:
        if conn is not None:
            conn.rollback()
        journal.refusal("%s -- nu s-a scris nimic (rollback)", e)
        return _json_utf8({"error": str(e),
                           "reason": REASON_INSTANTANEU_BLOCAT}, 409)
    except DecizieInvalida as e:
        if conn is not None:
            conn.rollback()
        journal.refusal("%s -- nu s-a scris nimic (rollback)", e)
        return _json_utf8({"error": str(e)}, 400)
    except Exception as e:
        if conn is not None:
            conn.rollback()
        journal.refusal("correction failed: %s -- rollback", e)
        logger.error(f"[forexe.asociere] {e}", exc_info=True)
        return _json_utf8({"error": f"Eroare la corectarea valorii: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()
