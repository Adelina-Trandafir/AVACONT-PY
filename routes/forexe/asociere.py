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

CE BLOCHEAZA ACUM: doar o ordonantare -- construita chiar pe instantaneu (`FX_ORD.IDRH`)
sau pe receptia lui, cu data ulterioara instantaneului (`FX_ORD.IDRR` + `DataORD`).

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
"""
import json
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
    ACTIUNE_IGNORAT,
    ACTIUNE_RECONSTITUIRE,
    ACTIUNE_STERGERE,
    DecizieInvalida,
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

# A cincea actiune, care exista DOAR aici: contractul in doua faze nu o are fiindca acolo
# nimic nu e inca atasat, deci nu e nimic de desprins. E `btnDel_Click` din
# `frmFX_DUBII_LISTA_HA`.
ACTIUNE_DESPRINS = "desprins"
ACTIUNI = (ACTIUNE_ASOCIAT, ACTIUNE_DESPRINS, ACTIUNE_IGNORAT,
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
    "       COALESCE(H.Sters, 0) AS Sters, COALESCE(H.EsteStergere, 0) AS EsteStergere "
    "FROM FX_Receptii_H H WHERE H.CodAngajament = %s AND " + SNAPSHOT_COUNTS_SQL + " "
    "ORDER BY H.DataH, H.IDRH"
)
_LINII_SQL = (
    "SELECT IDRH, CodIndicator, CodAI, CodSSI, IdClsf, Valoare "
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
# `H.IDRR IS NOT NULL` in WHERE: un instantaneu neasezat nu are legatura, deci nu are ce
# sa fie blocat (vezi nota «SI PE CE NU SE APLICA» din antet).
#
# Nu e nevoie de `O.IDRR <> 0` / `O.IDRH <> 0` ca sa scapam de santinela 0 a randurilor
# vechi: `FX_Receptii_R.IDRR` si `FX_Receptii_H.IDRH` sunt chei primare Access, deci
# incep de la 1 si nu pot fi niciodata 0. Un `FX_ORD` neancorat pur si simplu nu se
# potriveste cu nimic.
#
# `O.DataORD IS NULL OR O.DataORD >= H.DataH`: o ordonantare fara data nu poate fi
# dovedita anterioara, deci se considera ulterioara. Conservator, si e ramura care
# blocheaza -- nu una care lasa sa treaca ceva nedovedit.
#
# NU se mai numara platile (18.09.2026): doar ordonantarile blocheaza. Vezi antetul.
# ---------------------------------------------------------------------------
_BLOCAJE_SQL = (
    "SELECT H.IDRH, "
    " (SELECT COUNT(*) FROM FX_ORD O WHERE O.IDRH = H.IDRH) AS ord_h, "
    " (SELECT GROUP_CONCAT(DISTINCT O.NrORD ORDER BY O.NrORD SEPARATOR ', ') "
    "    FROM FX_ORD O WHERE O.IDRH = H.IDRH) AS ord_h_nr, "
    " (SELECT COUNT(*) FROM FX_ORD O WHERE O.IDRR = H.IDRR "
    "    AND (O.DataORD IS NULL OR O.DataORD >= H.DataH)) AS ord_r, "
    " (SELECT MIN(O.DataORD) FROM FX_ORD O WHERE O.IDRR = H.IDRR "
    "    AND (O.DataORD IS NULL OR O.DataORD >= H.DataH)) AS ord_r_data "
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

    Lista goala inseamna «editabil». Ordinea e de la cel mai specific la cel mai general:
    o ordonantare construita CHIAR pe acest instantaneu spune mai mult operatorului decat
    «receptia are o ordonantare ulterioara», iar mesajul cel mai de sus e cel pe care il
    vede intai. Doar ordonantarile blocheaza; platile nu (18.09.2026).
    """
    motive = []

    if int(rand.get("ord_h") or 0) > 0:
        nr = (rand.get("ord_h_nr") or "").strip()
        motive.append(
            "Pe acest instantaneu s-a construit ordonanțarea nr. " + nr + "."
            if nr else
            "Pe acest instantaneu s-a construit o ordonanțare."
        )

    if int(rand.get("ord_r") or 0) > 0:
        data = _zi(rand.get("ord_r_data"))
        motive.append(
            "Recepția are o ordonanțare din " + data + ", ulterioară acestui instantaneu."
            if data else
            "Recepția are o ordonanțare fără dată, care nu poate fi dovedită anterioară."
        )

    # Platile NU mai blocheaza (18.09.2026): un rand vechi care mai poarta `plati` /
    # `plati_data` e ignorat aici, nu tradus in motiv.
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
            "cod_indicator": r["CodIndicator"] or "",
            "cod_ai": r["CodAI"] or "",
            "cod_ssi": r["CodSSI"] or "",
            "id_clsf": r["IdClsf"],
            "valoare": float(r["Valoare"] or 0),
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
_R_DEMARCHEAZA_SQL = (
    "UPDATE FX_Receptii_R SET Sters = 0 WHERE IDRR = %s AND Reconstituit = 0 "
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
                lant.append(inst)
        for idrh, nou in mutari.items():
            if nou == idrr and dupa_idrh[idrh]["idrr"] != idrr:
                lant.append(dupa_idrh[idrh])
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
