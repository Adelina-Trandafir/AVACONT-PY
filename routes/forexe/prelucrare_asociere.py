# routes/forexe/prelucrare_asociere.py
"""
Pasul 4c al ingestiei FOREXE -- ASOCIEREA instantaneelor cu receptiile -- plus tot ce
tine de contractul in doua faze: amprenta, forma deciziilor, validarile si
reconstituirea receptiilor disparute. Felia 0048-03.

DE CE EXISTA FISIERUL ASTA SEPARAT. Tot restul conductei (prelucrare_pasi.py) ruleaza
IDENTIC in ambele faze. Aici e singurul loc in care ele difera:

  * faza «propunere» ruleaza trecerea AUTOMATA si o raporteaza ca SUGESTIE, fara sa
    scrie vreun `IDRR`;
  * faza «salvare» aplica `decizii` si IGNORA complet trecerea automata -- operatorul
    i-a vazut sugestiile si fie le-a acceptat, fie le-a suprascris; a o rula din nou ar
    insemna sa ne batem cu propriul om.

PROBLEMA DE FOND, in doua propozitii (docs/FUNDAMENT_Asociere_Receptii.md, partea 1).
`FX_Receptii_R` stie CARE receptie, dar nu are axa timpului. `FX_Receptii_H` are axa
timpului, dar nu stie care receptie -- istoricul FOREXE nu numeste niciodata receptia
(F4). Legatura dintre ele nu exista in date si nu poate fi dedusa: valoarea nu e cheie
(F5), data nu e cheie (F6), iar o salvare care nu schimba nimic produce oricum un
instantaneu complet (F7). Trecerea automata poate aseza doar ULTIMUL instantaneu al
unui lant (F9), deci restul ajung, prin constructie, la operator (F10).

O ASOCIERE GRESITA E TACUTA SI PERMANENTA (F12): strica `TotalReceptii` / `PlatiAnt` /
`Ramas` pentru fiecare plata de dupa acea data, si nimic nu compara cifrele cu nimic.
De-asta fiecare validare de mai jos RIDICA in loc sa corecteze.

CUM SE CITESTE FISIERUL (comentariile spun CE face un rand; cititorul stie SQL si
VB.NET, nu idiomuri Python):

  * `@dataclass`            o clasa cu campuri, fara sa scrii constructorul. Un Type
                            din VB6, cu nume.
  * `set(a) <= set(b)`      «a e submultime a lui b». Testul F14 intr-un singur operator.
  * `sorted(x, key=...)`    sortare dupa o cheie calculata, ca ORDER BY.
  * `dict.setdefault(k, [])` «ia lista de la cheia k, sau pune una goala si ia-o».
  * `f"..."`                interpolare de sir. Buna pentru mesaje, NICIODATA pentru SQL.
"""
import hashlib
import logging
from typing import Dict, List, Optional, Set, Tuple

from utils import asociere_log as journal

from .prelucrare_helpers import SNAPSHOT_COUNTS_SQL, fx_receptii_h_get_hash_ident

logger = logging.getLogger(__name__)

# Codul-motiv pe care clientul il recunoaste cand baza s-a schimbat intre cele doua faze.
# Acelasi tipar ca 401-urile din routes/auth/guard.py si ca ALEGERE_UNITATE din 0048-02.
REASON_STARE_MODIFICATA = "STARE_MODIFICATA"
MSG_STARE_MODIFICATA = (
    "Angajamentul s-a modificat de când a fost făcută propunerea. "
    "Descărcați-l din nou și reluați asocierea; nu s-a scris nimic."
)

# Cele patru actiuni, si nicio alta.
ACTIUNE_ASOCIAT = "asociat"
ACTIUNE_IGNORAT = "ignorat"
ACTIUNE_STERGERE = "stergere"
ACTIUNE_RECONSTITUIRE = "reconstituire"
ACTIUNI = (ACTIUNE_ASOCIAT, ACTIUNE_IGNORAT, ACTIUNE_STERGERE, ACTIUNE_RECONSTITUIRE)


class DecizieInvalida(ValueError):
    """Cererea de salvare e de nefolosit. Devine 400; nimic nu se scrie."""


class StareModificata(Exception):
    """Amprenta nu se potriveste. Devine 409 STARE_MODIFICATA; nimic nu se scrie."""


# ===========================================================================
# ANCORA unui instantaneu intre cele doua faze (F24 + F34)
# ===========================================================================
# Numele sub care un instantaneu de asezat calatoreste de la propunere la salvare. Doua
# forme, si NUMAI doua:
#
#   ("rand", n)  indicele randului lui de istoric in `TabelIstoric` (F24). Pentru orice
#                instantaneu al carui rand de istoric E in sarcina utila -- inclusiv cele
#                nascute chiar acum, al caror `IDRH` nu supravietuieste derularii inapoi.
#   ("idh", n)   `FX_Istoric.ID` al randului lui de istoric (F34, 18.09.2026). Pentru un
#                instantaneu al carui rand NU e in sarcina utila. ID-ul e stabil intre
#                faze fiindca randul exista DINAINTE de rularea asta: amprenta garanteaza
#                ca `FX_Istoric` nu s-a miscat, iar un rand vechi nu se deruleaza inapoi.
#
# DE CE A DOUA FORMA (F34). Fluxul REVERSE aduce doar randurile de istoric mai noi decat
# ultimul de acasa, deci `TabelIstoric` e o DIFERENTA, nu istoricul intreg -- presupunerea
# «FOREXE trimite istoricul intreg la fiecare descarcare» era adevarata doar la prima
# descarcare. Un instantaneu ramas neasezat dintr-o rulare mai veche nu-si mai gasea
# indicele si ramanea, prin constructie, de nerezolvat din descarcare. Acum are un nume.
#
# Ancora se CALCULEAZA din dictionar, nu se stocheaza: editorul de oricand
# (routes/forexe/asociere.py) pune `rand_istoric = idrh` ca alias pe ambele laturi si
# imprumuta functiile de aici neschimbat.
def ancora(x: dict) -> Tuple[str, int]:
    """Ancora unui instantaneu sau a unei decizii: («rand», indice) sau («idh», ID)."""
    rand = x.get("rand_istoric")
    if rand is not None:
        return ("rand", int(rand))
    idh = x.get("idh")
    if idh is None:
        raise DecizieInvalida(
            "Un instantaneu fără «rand_istoric» și fără «idh» nu poate fi numit.")
    return ("idh", int(idh))


def ancora_text(a: Tuple[str, int]) -> str:
    """Cum se numeste ancora in mesaje si in jurnal: «rândul 3» / «istoric 5786»."""
    return f"rândul {a[1]}" if a[0] == "rand" else f"istoric {a[1]}"


def _ancora_jurnal(x: dict):
    """Coloana «rand» din tabelele jurnalului, pentru orice dictionar de instantaneu."""
    try:
        return ancora_text(ancora(x))
    except DecizieInvalida:
        return "(deja legat)"


# ===========================================================================
# AMPRENTA (2.3)
# ===========================================================================
# Faza a doua trebuie sa vada aceeasi baza pe care a vazut-o faza intai, altfel deciziile
# descriu un tablou care nu mai exista.
#
# CONTINUT, si e documentat aici fiindca planul cere sa fie scris undeva anume:
#   FX_Istoric      -- COUNT(*), MAX(ID), MAX(DataFX)
#   FX_Receptii_R   -- COUNT(*), MAX(IDRR)
#   FX_Receptii_H   -- COUNT(*), MAX(IDRH), si COUNT-ul celor inca neasociate
# toate filtrate pe `CodAngajament`, toate concatenate si trecute prin SHA-256.
#
# De ce si COUNT si MAX: MAX singur nu se misca la o STERGERE de rand, COUNT singur nu
# se misca la o stergere urmata de o inserare. Impreuna, prind ambele cazuri.
# COUNT-ul celor neasociate e adaugat fiindca EL e chiar multimea despre care operatorul
# ia decizii: daca alta sesiune a asociat ceva intre timp, deciziile din fisierul local
# nu mai descriu aceeasi lista.
#
# Nimic din ce se schimba la CITIRE nu intra aici (fara DTQ, fara ceasuri).
_AMPRENTA_SQL = (
    "SELECT "
    " (SELECT COUNT(*) FROM FX_Istoric WHERE CodAngajament = %s) AS ic, "
    " (SELECT COALESCE(MAX(ID), 0) FROM FX_Istoric WHERE CodAngajament = %s) AS im, "
    " (SELECT COALESCE(MAX(DataFX), '1900-01-01') FROM FX_Istoric "
    "   WHERE CodAngajament = %s) AS id_, "
    " (SELECT COUNT(*) FROM FX_Receptii_R WHERE CodAngajament = %s) AS rc, "
    " (SELECT COALESCE(MAX(IDRR), 0) FROM FX_Receptii_R WHERE CodAngajament = %s) AS rm, "
    " (SELECT COUNT(*) FROM FX_Receptii_H WHERE CodAngajament = %s) AS hc, "
    " (SELECT COALESCE(MAX(IDRH), 0) FROM FX_Receptii_H WHERE CodAngajament = %s) AS hm, "
    " (SELECT COUNT(*) FROM FX_Receptii_H WHERE CodAngajament = %s "
    "    AND IDRR IS NULL AND COALESCE(Sters, 0) = 0) AS hn"
)


def amprenta(cursor, cod: str) -> str:
    """
    Amprenta starii curente a angajamentului, ca sir hex scurt.

    SE CALCULEAZA INAINTE DE ORICE SCRIERE, in ambele faze. Daca s-ar calcula la coada
    fazei intai, ar descrie starea SCRISA -- care e apoi derulata inapoi -- si faza a
    doua nu s-ar potrivi niciodata.
    """
    cursor.execute(_AMPRENTA_SQL, (cod,) * 8)
    r = cursor.fetchone()
    brut = "|".join([
        f"ic={r['ic']}", f"im={r['im']}", f"id={r['id_']}",
        f"rc={r['rc']}", f"rm={r['rm']}",
        f"hc={r['hc']}", f"hm={r['hm']}", f"hn={r['hn']}",
    ])
    semnatura = hashlib.sha256(brut.encode("utf-8")).hexdigest()[:32]
    # The components, not just the hash: when two phases disagree, the only useful
    # question is WHICH of the eight numbers moved between them.
    journal.line("amprenta %s din %s", semnatura, brut)
    return semnatura


# ===========================================================================
# Citirea tabloului: receptii si instantanee
# ===========================================================================
_RECEPTII_SQL = (
    "SELECT IDRR, NRCRT, DataR, SumaAntet, Descriere, Sters, Reconstituit, "
    "       ReconstituitNesigur "
    "FROM FX_Receptii_R WHERE CodAngajament = %s ORDER BY DataR, IDRR"
)
_RHR_SQL = (
    "SELECT IDRR, CodIndicator, CodAI, CodSSI, CreditBugetar, Valoare, ValoareN "
    "FROM FX_Receptii_RHR WHERE CodAngajament = %s ORDER BY IDRR, CodIndicator"
)
# D-F: TOATE instantaneele neasezate intra in joc, si cele din rularea asta si cele
# ramase din rulari anterioare. Selectia e `IDRR IS NULL AND Sters = 0`, NU «inserate de
# rularea curenta». Marcajul Access `OrigIDRH IS NULL` nu are echivalent aici si nu se
# reconstruieste.
#
# F32: un antet fara nicio linie (si care nu e stergere) NU e instantaneu -- e o eroare a
# vechii aplicatii Access -- si nu intra nici in tabloul de hotarat, nici in context.
# `SNAPSHOT_COUNTS_SQL` il lasa afara pe TOATE citirile din fisierul asta.
_INSTANTANEE_SQL = (
    "SELECT H.IDRH, H.IDH, H.DataH, H.Total, H.Descriere, H.EsteStergere "
    "FROM FX_Receptii_H H "
    "WHERE H.CodAngajament = %s AND H.IDRR IS NULL AND COALESCE(H.Sters, 0) = 0 "
    "  AND " + SNAPSHOT_COUNTS_SQL + " "
    "ORDER BY H.DataH, H.IDRH"
)
_LINII_SQL = (
    "SELECT IDRH, CodIndicator, CodAI, CodSSI, IdClsf, Valoare "
    "FROM FX_Receptii WHERE CodAngajament = %s ORDER BY IDRH, CodIndicator"
)
# Toate instantaneele angajamentului, pentru CONTEXTUL propunerii. Aceleasi coloane ca in
# routes/forexe/asociere.py, ca sa poata calatori in aceeasi forma pe fir.
_TOATE_INSTANTANEELE_SQL = (
    "SELECT H.IDRH, H.IDRR, H.IDH, H.DataH, H.Total, H.Descriere, H.TipReceptie, "
    "       COALESCE(H.Sters, 0) AS Sters, COALESCE(H.EsteStergere, 0) AS EsteStergere "
    "FROM FX_Receptii_H H WHERE H.CodAngajament = %s AND " + SNAPSHOT_COUNTS_SQL + " "
    "ORDER BY H.DataH, H.IDRH"
)


def citeste_receptii(cursor, cod: str,
                     ancore: Optional[Dict[int, int]] = None) -> List[dict]:
    """
    Fiecare receptie a angajamentului, cu liniile ei -- INCLUSIV cele pe care rularea
    asta nu le-a atins si inclusiv cele sterse.

    Formularul are nevoie de toate ca tinte de plasare: o receptie stearsa poate primi
    in continuare un instantaneu ANTERIOR stergerii ei. Steagul `Sters` nu o scoate din
    joc -- si cu atat mai putin acum, de cand F13 nu mai refuza nimic pe data (31.08.2026).

    `ancore` sunt cele intoarse de `step4b_receptii_prelucrare`: {indice in ListaReceptii
    -> IDRR}, doar pentru receptiile NASCUTE in rularea curenta. Fiecare astfel de
    receptie pleaca spre client si cu `rand_receptie`, indicele ei -- singurul nume al ei
    care supravietuieste derularii inapoi a propunerii. Pentru toate celelalte campul e
    `None`, iar numele care conteaza ramane `idrr`.

    Editorul de ORICAND (routes/forexe/asociere.py) cheama fara `ancore`: acolo nu exista
    sarcina utila, nu se deruleaza nimic inapoi, si fiecare `IDRR` e deja real.
    """
    rand_dupa_idrr: Dict[int, int] = {}
    for rand, idrr in (ancore or {}).items():
        # Doua randuri de payload din aceeasi zi pot ajunge pe aceeasi receptie; se tine
        # PRIMUL indice, ca sa fie o singura ancora pentru o singura receptie.
        if idrr not in rand_dupa_idrr:
            rand_dupa_idrr[idrr] = rand
    cursor.execute(_RHR_SQL, (cod,))
    linii: Dict[int, List[dict]] = {}
    for r in cursor.fetchall():
        linii.setdefault(int(r["IDRR"]), []).append({
            "cod_indicator": r["CodIndicator"] or "",
            "cod_ai": r["CodAI"] or "",
            "cod_ssi": r["CodSSI"] or "",
            "credit_bugetar": float(r["CreditBugetar"] or 0),
            "valoare": float(r["Valoare"] or 0),
            "valoare_n": float(r["ValoareN"] or 0),
        })

    cursor.execute(_RECEPTII_SQL, (cod,))
    out = []
    for r in cursor.fetchall():
        idrr = int(r["IDRR"])
        out.append({
            "idrr": idrr,
            "nr_crt": r["NRCRT"],
            "data_r": r["DataR"],
            "suma_antet": float(r["SumaAntet"] or 0),
            "descriere": r["Descriere"] or "",
            "sters": bool(r["Sters"]),
            "reconstituit": bool(r["Reconstituit"]),
            # F28. Formularul (0048-04) o foloseste ca sa avertizeze operatorul IN CLIPA
            # in care porneste o a doua reconstituire pe acelasi angajament -- inainte de
            # drag-uri, cand avertismentul inca valoreaza ceva -- si ca sa puna un semn pe
            # randurile deja marcate.
            "reconstituit_nesigur": bool(r["ReconstituitNesigur"]),
            # Vezi docstring-ul: numele care supravietuieste fazei intai, sau None.
            "rand_receptie": rand_dupa_idrr.get(idrr),
            "rhr": linii.get(idrr, []),
        })

    # The left-hand side of every F15 comparison, written out once: the sums the
    # rule will compare against are exactly these, so a refusal can be read
    # against them without opening the database.
    journal.section("recepțiile angajamentului (%d)" % len(out))
    journal.table(
        ("IDRR", "DataR", "SumaAntet", "sters", "reconst.", "rand", "linii RHR"),
        [(r["idrr"], r["data_r"], r["suma_antet"], r["sters"], r["reconstituit"],
          r["rand_receptie"], journal.sume_pe_indicator(r["rhr"])) for r in out])
    return out


def citeste_instantanee(cursor, cod: str, index_la_id: Dict[int, int],
                        warnings: List[str]) -> List[dict]:
    """
    Instantaneele inca neasezate, fiecare cu ancora lui (vezi `ancora`).

    `rand_istoric` (F24) e INDICELE de la zero al randului in `TabelIstoric`, nu o cheie
    de baza de date. Ancorarea pe indice e ce face contractul in doua faze sa functioneze:
    id-urile atribuite in timpul propunerii dispar la rollback si nu se intorc identice,
    dar indicele e stabil prin constructie, fiindca AMBELE faze poarta acelasi payload.

    UN INSTANTANEU AL CARUI RAND DE ISTORIC NU E IN PAYLOAD nu primeste indice
    (`rand_istoric = None`) si se ancoreaza pe `idh` (F34): fluxul REVERSE aduce doar
    diferenta de istoric, deci cazul e cel NORMAL pentru orice rand ramas neasezat dintr-o
    rulare mai veche, nu o exceptie. `FX_Istoric.ID` al unui rand vechi e stabil intre
    faze -- amprenta garanteaza ca tabelul nu s-a miscat.

    RAMAN AFARA, numarate intr-un avertisment (nu strecurate tacut, nu inventate):
      * un instantaneu fara `IDH` deloc -- nu are nume;
      * doua sau mai multe instantanee neasezate pe ACELASI `IDH` -- dublurile lasate de
        defectul F33 inainte de a fi inchis; ancora ar fi ambigua, deci le desface
        editorul de oricand, care le vede pe fiecare cu `IDRH`-ul lui.
    """
    # Harta inversa: FX_Istoric.ID -> indicele in payload. Doua randuri identice din
    # payload arata catre acelasi ID; se pastreaza PRIMUL indice, care e si cel pe care
    # l-ar trimite clientul.
    id_la_index: Dict[int, int] = {}
    for idx in sorted(index_la_id.keys()):
        id_la_index.setdefault(index_la_id[idx], idx)

    cursor.execute(_LINII_SQL, (cod,))
    linii: Dict[int, List[dict]] = {}
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
    randuri = cursor.fetchall()

    # F33 leftovers: the same history row under two or more unplaced snapshots. Counted
    # BEFORE the loop, so both copies are treated alike -- neither gets a name.
    cate_pe_idh: Dict[int, int] = {}
    for r in randuri:
        if r["IDH"] is not None:
            cate_pe_idh[int(r["IDH"])] = cate_pe_idh.get(int(r["IDH"]), 0) + 1

    out = []
    fara_idh = 0
    dublate = 0
    for r in randuri:
        idrh = int(r["IDRH"])
        if r["IDH"] is None:
            fara_idh += 1
            continue
        idh = int(r["IDH"])
        if cate_pe_idh[idh] > 1:
            dublate += 1
            continue
        out.append({
            "idrh": idrh,
            # None when the row is not in this download: the anchor is then `idh`.
            "rand_istoric": id_la_index.get(idh),
            "idh": idh,
            "data_h": r["DataH"],
            "descriere": r["Descriere"] or "",
            "total": float(r["Total"] or 0),
            "stergere": bool(r["EsteStergere"]),
            "linii": linii.get(idrh, []),
        })

    journal.section("instantaneele de hotărât în rularea asta (%d)" % len(out))
    journal.table(
        ("IDRH", "ancora", "IDH", "DataH", "Total", "stergere", "linii"),
        [(i["idrh"], _ancora_jurnal(i), i["idh"], i["data_h"], i["total"],
          i["stergere"], journal.sume_pe_indicator(i["linii"])) for i in out])
    prin_idh = sum(1 for i in out if i["rand_istoric"] is None)
    if prin_idh:
        journal.line("%d instantanee ancorate pe IDH: rândul lor de istoric nu e în "
                     "descărcarea asta (F34)", prin_idh)
    if fara_idh:
        journal.line("%d instantanee lăsate afară: nu au IDH deloc", fara_idh)
        warnings.append(
            f"{fara_idh} instantanee neasociate nu au niciun rând de istoric și nu pot "
            f"fi așezate din descărcare. Se rezolvă în editorul de asociere."
        )
    if dublate:
        journal.line("%d instantanee lăsate afară: dubluri pe același rând de istoric "
                     "(F33)", dublate)
        warnings.append(
            f"{dublate} instantanee neasociate sunt dubluri ale aceluiași rând de "
            f"istoric și nu pot fi așezate din descărcare. Se rezolvă în editorul de "
            f"asociere."
        )
    return out


# Frazele puse pe un instantaneu care nu e blocat de nicio ordonantare si de nicio plata,
# dar nici nu se poate misca DE AICI: in ingestie deciziile acopera doar randurile de
# asezat, si nimic altceva. Doua cazuri, si nu se confunda -- unul are o legatura scrisa,
# celalalt n-are cu ce sa fie rezolvat acum.
MOTIV_CONTEXT = (
    "Legătura este deja scrisă. Se corectează în editorul de asociere, nu în timpul "
    "descărcării."
)
MOTIV_FARA_ISTORIC = (
    "Nu poate fi numit din descărcare: fie nu are rând de istoric, fie împarte rândul "
    "cu alt instantaneu neașezat. Se așază în editorul de asociere."
)


def citeste_instantanee_context(cursor, cod: str, de_decis: Set[int],
                                blocaje: Dict[int, List[str]]) -> List[dict]:
    """
    RESTUL instantaneelor angajamentului: tot ce nu e de decis in rularea asta.

    DE CE EXISTA (08.09.2026, cerinta operatorului dupa prima rulare adevarata). Tabloul
    propunerii continea doar randurile de asezat -- `IDRR IS NULL AND Sters = 0` --, deci
    o receptie al carei lant era deja legat sosea pe ecran GOALA: fara linie in grafic
    (formularul sare peste lanturile de lungime zero), fara marcaje pe banda, fara nimic
    sub ea in arbore. Vazut de la locul operatorului, «recepțiile vechi nu mai vin».
    Erau acolo; povestea lor nu era.

    Nu e doar aspect: serverul JUDECA deja pe lantul intreg -- `aplica_decizii` aduce
    instantaneele deja asociate ale acelorasi receptii inainte de `valideaza_plasarile`,
    fiindca F15 si F16 se refera la lant, nu la ce se adauga acum. Pana la felia asta,
    formularul era singurul care nu vedea ce vede vetoul.

    SE ARATA, NU SE MISCA. Toate ies cu `blocat = True`: acoperirea ceruta de
    `verifica_acoperirea` e exact multimea de decis, iar o decizie pentru un rand din
    afara ei e respinsa (si pe drept -- ar rescrie tacut legaturi vechi la fiecare
    descarcare). Corectarea unei legaturi vechi ramane treaba editorului de oricand.

    `de_decis` sunt IDRH-urile intoarse de `citeste_instantanee`. Complementul lor, nu un
    filtru SQL paralel: asa nu poate exista un rand pe care sa nu-l ia niciuna dintre cele
    doua liste -- nici macar cel lasat afara fiindca nu si-a gasit randul de istoric in
    descarcarea asta.
    """
    cursor.execute(_LINII_SQL, (cod,))
    linii: Dict[int, List[dict]] = {}
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

    cursor.execute(_TOATE_INSTANTANEELE_SQL, (cod,))
    out = []
    for r in cursor.fetchall():
        idrh = int(r["IDRH"])
        if idrh in de_decis:
            continue
        idrr = int(r["IDRR"]) if r["IDRR"] is not None else 0
        motive = list(blocaje.get(idrh, []))
        if not motive:
            motive = [MOTIV_CONTEXT if idrr else MOTIV_FARA_ISTORIC]
        out.append({
            "idrh": idrh,
            "idrr": idrr,
            "idh": int(r["IDH"]) if r["IDH"] is not None else 0,
            "data_h": r["DataH"],
            "descriere": r["Descriere"] or "",
            "total": float(r["Total"] or 0),
            "tip_receptie": r["TipReceptie"] or "",
            "stergere": bool(r["EsteStergere"]),
            # F17: marcat de operator ca «nu consemneaza nicio schimbare».
            "ignorat": bool(r["Sters"]),
            "blocat": True,
            "motive": motive,
            "linii": linii.get(idrh, []),
        })

    journal.section("instantaneele deja așezate, doar arătate (%d)" % len(out))
    journal.table(
        ("IDRH", "IDRR", "DataH", "Total", "tip", "stergere", "ignorat", "linii"),
        [(i["idrh"], i["idrr"], i["data_h"], i["total"], i["tip_receptie"],
          i["stergere"], i["ignorat"], journal.sume_pe_indicator(i["linii"]))
         for i in out])
    return out


# ===========================================================================
# PASUL 4c, FAZA UNU -- trecerea automata (sugestii, fara scriere)
# ===========================================================================
# Port al lui TMP_Asociaza_Receptii_Istoric: doua treceri, LIFO, fiecare receptie
# consumata cel mult o data.
#
# DOUA ABATERI, amandoua deliberate:
#
# 1. ORDINEA. Access ordona dupa `ID`, cu comentariul «autonumber = ordine cronologica
#    in tmp». Aici se ordoneaza dupa `DataH` (apoi `IDRH` ca departajare stabila).
#    `DataH` ESTE axa timpului (F2); sub plasare manuala ordinea de inserare inceteaza sa
#    mai fie cea cronologica. Cand totul e automat, cele doua coincid.
#
# 2. RECEPTIILE STERSE NU SUNT CANDIDATE. Nimic nu se mai poate adauga pe site unei
#    receptii sterse, deci o potrivire automata pe ea ar fi mereu o coliziune -- acelasi
#    rationament ca F25 la pasul 4b. Plasarea MANUALA pe o receptie stearsa ramane
#    permisa (un instantaneu dinaintea stergerii ii apartine pe drept); doar SUGESTIA
#    automata se abtine.
_R_CANDIDATI_AUTO_SQL = (
    "SELECT IDRR, SumaAntet FROM FX_Receptii_R "
    "WHERE CodAngajament = %s AND Sters = 0 ORDER BY DataR DESC, IDRR DESC"
)


def _cheie_suma(valoare) -> str:
    """
    Cheia de potrivire pe suma.

    Access folosea `CStr(<Double>)`, care e dependent de locale. Aici e rotunjire la doi
    zecimali, formatata cu punct: acelasi rezultat pentru orice suma reala, fara sa
    depinda de ce limba are serverul.
    """
    return f"{round(float(valoare or 0), 2):.2f}"


def pas4c_automat(cursor, cod: str, instantanee: List[dict]) -> Dict[int, int]:
    """
    Trecerea automata. Intoarce {IDRH: IDRR} -- SUGESTII, nu scrieri.

    Nu scrie nimic. Faza intai raporteaza rezultatul ca `sugestie_idrr` /
    `sugestie_automata`, ca sa poata fi aratat drept propunere, nu drept fapt (F18):
    sub F11, trecerea automata poate fi GRESITA, nu doar incompleta.
    """
    cursor.execute(_R_CANDIDATI_AUTO_SQL, (cod,))
    # {suma: [IDRR, ...]} cu cea mai recenta receptie prima (LIFO).
    dic_r: Dict[str, List[int]] = {}
    for r in cursor.fetchall():
        dic_r.setdefault(_cheie_suma(r["SumaAntet"]), []).append(int(r["IDRR"]))

    journal.section("trecerea automată (doar sugestii, nu scrie nimic)")
    journal.line("candidați după sumă: %s",
                 "  ".join("%s -> %s" % (k, v) for k, v in sorted(dic_r.items()))
                 or "(nicio recepție neștearsă)")

    folosite: Set[int] = set()
    sugestii: Dict[int, int] = {}

    def _incearca(lista: List[dict], tura: int) -> None:
        for inst in lista:
            if inst["idrh"] in sugestii:
                continue
            cheie = _cheie_suma(inst["total"])
            candidati = dic_r.get(cheie)
            if not candidati:
                journal.line("tura %d: IDRH %s (%s, %s) -- nicio recepție cu suma asta",
                             tura, inst["idrh"], journal.moment(inst["data_h"]), cheie)
                continue
            while candidati:
                idrr = candidati.pop(0)
                if idrr not in folosite:
                    folosite.add(idrr)
                    sugestii[inst["idrh"]] = idrr
                    journal.line("tura %d: IDRH %s (%s, %s) -> recepția %s",
                                 tura, inst["idrh"], journal.moment(inst["data_h"]),
                                 cheie, idrr)
                    break

    # RUN 1 -- instantanee de la cel mai nou catre cel mai vechi.
    _incearca(sorted(instantanee, key=lambda x: (x["data_h"], x["idrh"]), reverse=True), 1)
    # RUN 2 -- ce a ramas, de la cel mai vechi catre cel mai nou.
    _incearca(sorted(instantanee, key=lambda x: (x["data_h"], x["idrh"])), 2)
    journal.line("%d sugestii, %d instantanee rămân pentru operator",
                 len(sugestii), len(instantanee) - len(sugestii))
    return sugestii


# ===========================================================================
# Forma deciziilor (2.2)
# ===========================================================================
def normalizeaza_decizii(brut) -> List[dict]:
    """
    Verifica forma lui `decizii` si o intoarce curatata.

    Nimic nu se corecteaza aici -- se ridica. O decizie pe care nu o putem citi nu e
    acelasi lucru cu absenta unei decizii, iar tacerea NU are voie sa fie interpretabila
    drept alegere.
    """
    if not isinstance(brut, list):
        raise DecizieInvalida("Câmpul «decizii» trebuie să fie o listă.")
    out: List[dict] = []
    for i, item in enumerate(brut):
        if not isinstance(item, dict):
            raise DecizieInvalida(f"«decizii»[{i}] nu este un obiect.")
        # Ancora: EXACT una dintre `rand_istoric` (F24) si `idh` (F34). `null` e absenta,
        # nu o valoare -- clientul .NET scrie null pe campul pe care nu l-a pus.
        rand = item.get("rand_istoric")
        idh = item.get("idh")
        if (rand is None) == (idh is None):
            raise DecizieInvalida(
                f"«decizii»[{i}]: trebuie exact una dintre «rand_istoric» și «idh»."
            )
        try:
            rand = None if rand is None else int(rand)
            idh = None if idh is None else int(idh)
        except (TypeError, ValueError) as err:
            raise DecizieInvalida(
                f"«decizii»[{i}]: «rand_istoric» / «idh» nu este un număr."
            ) from err
        actiune = str(item.get("actiune") or "").strip()
        if actiune not in ACTIUNI:
            raise DecizieInvalida(
                f"«decizii»[{i}]: «actiune» «{actiune}» nu este cunoscută "
                f"(permise: {', '.join(ACTIUNI)})."
            )
        data_h = item.get("data_h")
        if not data_h:
            raise DecizieInvalida(f"«decizii»[{i}]: «data_h» este obligatorie.")

        idrr = item.get("idrr")
        rand_receptie = item.get("rand_receptie")
        eticheta = item.get("receptie_noua")
        if idrr is not None:
            try:
                idrr = int(idrr)
            except (TypeError, ValueError) as err:
                raise DecizieInvalida(
                    f"«decizii»[{i}]: «idrr» nu este un număr.") from err
        if rand_receptie is not None:
            try:
                rand_receptie = int(rand_receptie)
            except (TypeError, ValueError) as err:
                raise DecizieInvalida(
                    f"«decizii»[{i}]: «rand_receptie» nu este un număr.") from err
        if eticheta is not None:
            eticheta = str(eticheta).strip()
            if eticheta == "":
                raise DecizieInvalida(
                    f"«decizii»[{i}]: «receptie_noua» nu poate fi șir gol.")

        # `asociat` si `stergere` cer EXACT una dintre cele TREI tinte. A treia,
        # `rand_receptie`, e indicele randului in `ListaReceptii` si numeste o receptie
        # pe care o naste CHIAR RULAREA ASTA: `IDRR`-ul ei din propunere nu supravietuieste
        # derularii inapoi (vezi `step4b_receptii_prelucrare`), deci nu are voie sa fie
        # numele pe care il poarta decizia.
        tinte = [x is not None for x in (idrr, rand_receptie, eticheta)]
        if actiune in (ACTIUNE_ASOCIAT, ACTIUNE_STERGERE):
            if sum(tinte) != 1:
                raise DecizieInvalida(
                    f"«decizii»[{i}] ({actiune}): trebuie exact una dintre «idrr», "
                    f"«rand_receptie» și «receptie_noua»."
                )
        elif actiune == ACTIUNE_RECONSTITUIRE:
            if eticheta is None:
                raise DecizieInvalida(
                    f"«decizii»[{i}] (reconstituire): «receptie_noua» este obligatorie.")
            if idrr is not None or rand_receptie is not None:
                raise DecizieInvalida(
                    f"«decizii»[{i}] (reconstituire): «idrr» / «rand_receptie» nu au "
                    f"sens — recepția încă nu există.")
        else:   # ignorat
            if any(tinte):
                raise DecizieInvalida(
                    f"«decizii»[{i}] (ignorat): nu poate purta o recepție.")

        out.append({
            "rand_istoric": rand,
            "idh": idh,
            "actiune": actiune,
            "data_h": str(data_h),
            "idrr": idrr,
            "rand_receptie": rand_receptie,
            "receptie_noua": eticheta,
        })
    return out


def _acelasi_moment(a, b) -> bool:
    """
    Compara `data_h` sosita ca text cu `DataH` din baza, ca DATETIME COMPLET.

    Clientul trimite ISO ("2026-05-20T00:36:12" sau cu spatiu). Se normalizeaza ambele
    la «YYYY-MM-DD HH:MM:SS» si se compara ca siruri: aceeasi rezolutie ca datetime-ul
    din MariaDB, fara sa depindem de parsarea unui fus orar care nu exista in date.
    """
    def _norm(v) -> str:
        if v is None:
            return ""
        s = str(v).strip().replace("T", " ")
        if "." in s:                 # taie fractiunile de secunda, daca sosesc
            s = s.split(".", 1)[0]
        if len(s) == 10:             # doar data -> miezul noptii
            s += " 00:00:00"
        return s
    return _norm(a) == _norm(b)


def verifica_acoperirea(decizii: List[dict], instantanee: List[dict]) -> Dict[int, dict]:
    """
    Fiecare instantaneu trebuie sa apara in `decizii`, o singura data, cu `data_h` potrivit.

    Intoarce {ancora: decizie} -- cheia e tuplul dat de `ancora()`.

    Un instantaneu LIPSA e 400, nu o valoare implicita: tacerea nu are voie sa insemne
    «ignora-l». O `data_h` care nu se potriveste cu randul de la acea ancora e tot 400 --
    asa un fisier de decizii invechit cade zgomotos in loc sa asocieze tacut alt rand.
    """
    dupa_ancora = {ancora(i): i for i in instantanee}

    vazute: Dict[Tuple[str, int], dict] = {}
    for d in decizii:
        a = ancora(d)
        inst = dupa_ancora.get(a)
        if inst is None:
            raise DecizieInvalida(
                f"Decizia pentru {ancora_text(a)} nu corespunde niciunui instantaneu "
                f"de rezolvat."
            )
        if a in vazute:
            raise DecizieInvalida(
                f"Instantaneul de la {ancora_text(a)} apare de două ori în «decizii».")
        if not _acelasi_moment(d["data_h"], inst["data_h"]):
            raise DecizieInvalida(
                f"Instantaneul de la {ancora_text(a)}: «data_h» trimisă ({d['data_h']}) "
                f"nu se potrivește cu cea din descărcare ({inst['data_h']}). Fișierul de "
                f"decizii este învechit — reluați descărcarea."
            )
        vazute[a] = d

    lipsa = [ancora(i) for i in instantanee if ancora(i) not in vazute]
    journal.line("acoperire: %d decizii pentru %d instantanee, %d fără hotărâre",
                 len(decizii), len(instantanee), len(lipsa))
    if lipsa:
        raise DecizieInvalida(
            f"Lipsesc deciziile pentru {len(lipsa)} instantanee "
            f"({', '.join(ancora_text(x) for x in sorted(lipsa)[:20])}"
            f"{'…' if len(lipsa) > 20 else ''})."
        )
    return vazute


def verifica_etichetele(decizii: List[dict]) -> Dict[str, dict]:
    """
    Regulile etichetelor `receptie_noua` (§4c-bis).

    * fiecare eticheta e DECLARATA de exact o `reconstituire`;
    * fiecare eticheta folosita altundeva e declarata;
    * fiecare lant reconstituit contine EXACT o `stergere`.

    Ultima regula nu e pedanterie. O receptie reconstituita exista TOCMAI fiindca a fost
    stearsa (F26); un lant fara stergere inseamna ca operatorul a grupat gresit, iar
    rezultatul ar fi o receptie care nu apare niciodata in `ListaReceptii` si nu se poate
    reconcilia cu nimic. Mai bine se opreste rularea.
    """
    declarate: Dict[str, dict] = {}
    for d in decizii:
        if d["actiune"] != ACTIUNE_RECONSTITUIRE:
            continue
        et = d["receptie_noua"]
        if et in declarate:
            raise DecizieInvalida(
                f"Eticheta «{et}» este declarată de două ori prin «reconstituire»; "
                f"o recepție reconstituită începe într-un singur loc."
            )
        declarate[et] = d

    folosite = {d["receptie_noua"] for d in decizii if d["receptie_noua"]}
    nedeclarate = folosite - set(declarate)
    if nedeclarate:
        raise DecizieInvalida(
            f"Etichetele {', '.join('«%s»' % x for x in sorted(nedeclarate))} sunt "
            f"folosite dar nu sunt declarate prin «reconstituire»."
        )

    for et in declarate:
        stergeri = [d for d in decizii
                    if d["receptie_noua"] == et and d["actiune"] == ACTIUNE_STERGERE]
        if len(stergeri) != 1:
            raise DecizieInvalida(
                f"Lanțul reconstituit «{et}» are {len(stergeri)} rânduri de ștergere; "
                f"trebuie exact unul. O recepție reconstituită există tocmai pentru că "
                f"a fost ștearsă."
            )
    return declarate


# ===========================================================================
# PASUL 4c-bis -- materializarea receptiilor reconstituite
# ===========================================================================
_MAX_NRCRT_R_SQL = "SELECT MAX(NRCRT) AS MaxNr FROM FX_Receptii_R WHERE CodAngajament = %s"
_R_INSERT_RECONST_SQL = (
    "INSERT INTO FX_Receptii_R "
    "(NRCRT, CodAngajament, Tip, DataR, SumaAntet, Descriere, TipReceptie, HASH, "
    " Preluat, Sters, Reconstituit) "
    "VALUES (%s, %s, NULL, %s, %s, %s, 'NOU', NULL, 1, 1, 1)"
)
_RHR_INSERT_SQL = (
    "INSERT INTO FX_Receptii_RHR "
    "(IDRR, CodAngajament, CodIndicator, CodAI, IdClsf, IdUnitate, CodSSI, "
    " CreditBugetar, Valoare, ValoareN, TipIntern) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 0, 'NOU')"
)
# CreditBugetar e creditul bugetar AL INDICATORULUI, nu o cifra per receptie: datele de
# proba il arata constant pe indicator, pe toate receptiile unui angajament (AAB =
# 10502,19 pe TmpID 268-272; AA2 = 1263,39 pe toate cinci). Se ia dintr-un rand RHR
# existent cu acelasi CodAI. DACA NU EXISTA NICIUNUL, SE RIDICA -- un zero aici nu se
# deosebeste de un zero real si ar fi citit ca fapt pe veci.
_CREDIT_BUGETAR_SQL = (
    "SELECT CreditBugetar FROM FX_Receptii_RHR "
    "WHERE CodAngajament = %s AND CodAI = %s AND CreditBugetar IS NOT NULL "
    "ORDER BY IDRHR DESC LIMIT 1"
)
_IND_SQL = (
    "SELECT CodAI, IdClsf, IdUnitate FROM FX_Indicatori WHERE CodAngajament = %s"
)


def materializeaza_reconstituite(cursor, cod: str, decizii: List[dict],
                                 instantanee: List[dict],
                                 etichete: Dict[str, dict],
                                 warnings: List[str]) -> Dict[str, int]:
    """
    Creeaza receptiile pe care operatorul le-a declarat prin «reconstituire» (F26).

    DE CE EXISTA. «O receptie trebuie sa existe inainte sa poata fi stearsa» e adevarat
    PE SITE. Local e adevarat doar daca K-BOT a descarcat INAINTE de stergere. Un
    angajament descarcat prima oara pe 26/08, a carui receptie a fost creata in ianuarie
    si stearsa in martie, livreaza crearea, modificarile si stergerea intr-un singur
    istoric -- iar `ListaReceptii` nu o contine, deci nu exista niciun rand
    `FX_Receptii_R` de care lantul sa se agate. Cum tot restantul se ingereaza retroactiv
    chiar acum, cazul e de asteptat sa fie frecvent, nu rar.

    NIMIC NU SE INVENTEAZA. Fiecare camp are o sursa in istoric:
      DataR      <- DataH al CELUI MAI VECHI instantaneu din lant
      SumaAntet  <- `Total` al randului de stergere (cat valora cand a plecat)
      Descriere  <- `Receptie: <text>` al randului de stergere
      NrCrt      <- MAX+1 pe angajament, ca peste tot
      Sters = 1, Reconstituit = 1, HASH = NULL (nu exista bloc de payload de hasuit)
      RHR        <- liniile pe indicator ale ULTIMULUI instantaneu DINAINTEA stergerii
      CreditBugetar <- un rand RHR existent cu acelasi CodAI

    `Preluat`, `Incarcat` si `TipReceptie` se pun EXACT ca la o receptie nou inserata de
    ingestie (pasul 4b): TipReceptie = 'NOU', Preluat = 1, Incarcat NEATINS (ramane NULL).
    Consemnat in worklog, fiindca randurile astea nu au trecut niciodata prin
    `ListaReceptii` si acolo valorile inseamna ceva usor diferit.

    Se ruleaza INAINTE de aplicarea deciziilor, ca fiecare eticheta sa fie deja un `IDRR`
    real cand se ajunge la `asociat` / `stergere` -- si restul pasului 4c sa nu fie nevoit
    sa stie ca receptiile astea sunt speciale.
    """
    if not etichete:
        return {}

    journal.section("recepții reconstituite de creat (%d)" % len(etichete))
    dupa_ancora = {ancora(i): i for i in instantanee}
    cursor.execute(_IND_SQL, (cod,))
    indicatori = {str(r["CodAI"]): r for r in cursor.fetchall()}

    cursor.execute(_MAX_NRCRT_R_SQL, (cod,))
    row = cursor.fetchone()
    nr_crt = int((row or {}).get("MaxNr") or 0) + 1

    rezolvate: Dict[str, int] = {}

    for eticheta in sorted(etichete):
        lant = [dupa_ancora[ancora(d)] for d in decizii
                if d["receptie_noua"] == eticheta]
        lant.sort(key=lambda x: (x["data_h"], x["idrh"]))

        stergerea = next(d for d in decizii
                         if d["receptie_noua"] == eticheta
                         and d["actiune"] == ACTIUNE_STERGERE)
        inst_stergere = dupa_ancora[ancora(stergerea)]

        # Stergerea trebuie sa fie ULTIMUL instantaneu al lantului. Daca nu e, gruparea
        # e gresita: nimic nu se poate intampla cu o receptie dupa ce a fost stearsa.
        if lant[-1]["idrh"] != inst_stergere["idrh"]:
            raise DecizieInvalida(
                f"Lanțul reconstituit «{eticheta}»: rândul de ștergere "
                f"({inst_stergere['data_h']}) nu este ultimul din lanț. O recepție nu "
                f"mai poate fi modificată după ce a fost ștearsă."
            )

        cursor.execute(_R_INSERT_RECONST_SQL, (
            nr_crt, cod, lant[0]["data_h"], inst_stergere["total"],
            inst_stergere["descriere"],
        ))
        idrr = int(cursor.lastrowid)
        nr_crt += 1
        rezolvate[eticheta] = idrr
        journal.line("reconstituire «%s» -> recepția %s: NrCrt %s, DataR %s (primul "
                     "instantaneu), SumaAntet %.2f (rândul de ștergere), %d "
                     "instantanee în lanț",
                     eticheta, idrr, nr_crt - 1, journal.moment(lant[0]["data_h"]),
                     inst_stergere["total"], len(lant))

        # Liniile: ultimul instantaneu DINAINTEA stergerii care are linii. Randul de
        # stergere nu are (F21), deci se merge inapoi pana la primul care are.
        sursa = None
        for inst in reversed(lant[:-1]):
            if inst["linii"]:
                sursa = inst
                break
        if sursa is None:
            warnings.append(
                f"Recepția reconstituită «{eticheta}» nu are niciun instantaneu cu "
                f"linii pe indicator; se creează fără rânduri RHR."
            )
            continue

        for linie in sursa["linii"]:
            cheie_ai = linie["cod_ai"]
            cursor.execute(_CREDIT_BUGETAR_SQL, (cod, cheie_ai))
            cb = cursor.fetchone()
            if cb is None:
                raise DecizieInvalida(
                    f"Recepția reconstituită «{eticheta}»: indicatorul {cheie_ai} nu "
                    f"are niciun rând RHR existent pe acest angajament, deci creditul "
                    f"bugetar nu poate fi preluat. Nu se scrie zero — zero ar fi citit "
                    f"ca valoare reală."
                )
            ind = indicatori.get(cheie_ai)
            cursor.execute(_RHR_INSERT_SQL, (
                idrr, cod, linie["cod_indicator"], cheie_ai,
                None if ind is None else ind["IdClsf"],
                None if ind is None else ind["IdUnitate"],
                linie["cod_ssi"], float(cb["CreditBugetar"]), linie["valoare"],
            ))

    return rezolvate


# ===========================================================================
# F28 -- reconstituirea neverificabila
# ===========================================================================
# F27 spune limita: cand DOUA receptii ale aceluiasi angajament au fost si create, si
# sterse inainte de prima descarcare, instantaneele lor sunt de nedeosebit unele de
# altele altfel decat dupa suma si indicator. F14, F16 si regula «exact o stergere pe
# lant» ingradesc gruparea operatorului; NU o demonstreaza.
#
# F28 (26.08.2026) consemneaza cazul in date. Un total care peste luni nu se inchide
# poate atunci fi urmarit inapoi pana la gruparea care a fost o judecata, nu o
# verificare. Fara steag, ambiguitatea traieste doar in capul omului care a facut-o.
_RECONSTITUITE_SQL = (
    "SELECT IDRR FROM FX_Receptii_R "
    "WHERE CodAngajament = %s AND Reconstituit = 1 ORDER BY IDRR"
)
_MARCHEAZA_NESIGUR_SQL = (
    "UPDATE FX_Receptii_R SET ReconstituitNesigur = 1 WHERE IDRR IN ({})"
)


def f28_de_marcat(reconstituite: List[int]) -> List[int]:
    """
    Care receptii reconstituite ale unui angajament devin `ReconstituitNesigur`.

    Functie PURA, peste lista de `IDRR` reconstituite ale angajamentului dupa rularea
    curenta -- cele de acum SI cele din rulari mai vechi. Regula e exact conditia lui
    F27 si nimic mai larg:

      * una singura  ▸ lista goala. Instantaneele ei nu concureaza cu nimic, deci
                       gruparea e ingradita de F14/F16 si atat -- ceea ce e destul.
      * doua sau mai ▸ TOATE. Ambiguitatea e INTRE ele, deci nu apartine niciuneia
                       singure, si nici macar celei adaugate ultima: fiecare instantaneu
                       al oricareia dintre ele ar fi putut sta pe cealalta.

    NU SE STERGE NICIODATA, si de-aia functia asta spune doar pe cine sa marchezi, nu si
    pe cine sa demarchezi. O rulare de mai tarziu care vede o singura reconstituire nu
    face gruparea de atunci mai verificabila decat era in clipa in care s-a facut.
    """
    return list(reconstituite) if len(reconstituite) >= 2 else []


def marcheaza_reconstituirile_nesigure(cursor, cod: str,
                                       warnings: List[str]) -> int:
    """
    Aplica F28 dupa ce toate deciziile au fost scrise. Intoarce cate randuri s-au marcat.

    Se cheama in faza de SALVARE, la coada pasului 4c, fiindca abia atunci exista
    receptiile reconstituite in tabel. Se recitesc din baza -- nu se numara deciziile --
    ca sa intre in socoteala si reconstituirile ramase din rulari mai vechi: doua
    reconstituiri facute in doua sesiuni diferite sunt exact la fel de imposibil de
    deosebit ca doua facute in aceeasi sesiune.
    """
    cursor.execute(_RECONSTITUITE_SQL, (cod,))
    reconstituite = [int(r["IDRR"]) for r in cursor.fetchall()]

    de_marcat = f28_de_marcat(reconstituite)
    journal.line("F28: %d recepții reconstituite pe angajament %s, de marcat %s",
                 len(reconstituite), reconstituite or "(niciuna)",
                 de_marcat or "(niciuna)")
    if not de_marcat:
        return 0

    locuri = ", ".join(["%s"] * len(de_marcat))
    cursor.execute(_MARCHEAZA_NESIGUR_SQL.format(locuri), tuple(de_marcat))

    warnings.append(
        "Pe acest angajament sunt acum " + str(len(de_marcat)) + " recepții "
        "reconstituite (nr. " + ", ".join(str(x) for x in de_marcat) + "). "
        "Instantaneele lor nu se pot deosebi între ele decât după sumă și indicator, "
        "deci gruparea nu a putut fi verificată de program — a fost o judecată a "
        "operatorului (F27). Toate au fost marcate «reconstituire nesigură»."
    )
    return len(de_marcat)

# ===========================================================================
# Validarile de plasare (F14, F15, F16 -- si F13, retras ca veto, ramas semn)
# ===========================================================================
def _indicatori_receptie(rec: dict) -> Set[str]:
    return {l["cod_indicator"] for l in rec["rhr"] if l["cod_indicator"]}


def _indicatori_instantaneu(inst: dict) -> Set[str]:
    return {l["cod_indicator"] for l in inst["linii"] if l["cod_indicator"]}


def valideaza_plasarile(lanturi: Dict[int, List[dict]],
                        receptii: Dict[int, dict],
                        f15_ca_avertisment: bool = False,
                        avertismente: Optional[List[str]] = None,
                        id_stabil: bool = True) -> None:
    """
    Toate regulile care pot spune «nu acolo», rulate pe tabloul REZULTAT.

    Clientul face veto la momentul plasarii; serverul NU se increde in el. Fiecare
    verificare RIDICA -- nu corecteaza, nu avertizeaza si merge mai departe -- fiindca o
    asociere gresita e tacuta si permanenta (F12).

    `f15_ca_avertisment` (implicit False = purtarea de pana acum, calea de INGESTIE)
    coboara DOAR F15, capatul de lant, de la veto la semnalare. Il foloseste editorul de
    asociere de oricand (routes/forexe/asociere.py, felia 0048-04), si nu ca sa fie
    ingaduitor: acolo se DESPRIND legaturi, iar desprinderea ultimului instantaneu lasa,
    prin definitie, un lant care nu se mai inchide. Un veto acolo ar face imposibil tocmai
    lucrul pentru care exista editorul. Fundamentul insusi descrie F15 ca pe un SEMN
    aratat per recepție (§1.5), iar Access nu il verifica deloc la desprindere.

    F14 si F16 raman vetouri in AMANDOUA cazurile: sunt absolute. Un instantaneu nu poate
    numi indicatori pe care recepția nu ii are, iar un indicator nu poate disparea din lant.

    F13 A FOST RETRAS (31.08.2026) SI APOI STERS CU TOTUL (09.09.2026)
    -----------------------------------------------------------------
    Vetoul de data se sprijinea pe premisa ca `FX_Receptii_R.DataR` spune cand a aparut
    receptia. Operatorul a corectat premisa: `DataR` e un camp OBISNUIT, pe care omul il
    tasteaza pe site si il poate schimba dupa aceea, iar `FX_Receptii_R` nu are NICIO
    coloana cu momentul crearii (F29 -- verificat in `000_DEMO.sql` si in
    `FX_System_Export/TABLES/FX_Receptii_R.md`).

    Prima treapta l-a coborat de la veto la semn. A doua l-a scos si de acolo, la cererea
    explicita a operatorului: semnul se aprindea pe date perfect corecte -- o data tastata
    soseste la miezul noptii, iar un instantaneu din chiar ziua receptiei iesea deci
    «inainte de ea» -- deci nu spunea nimic si aparea pe rand dupa rand. AICI NU SE MAI
    SCRIE NICIUN AVERTISMENT DESPRE `DataR`. Perechea din client (`AsociereForm`) a fost
    curatata in aceeasi zi si in acelasi fel.

    `avertismente` este OPTIONAL, si un apelant care nu-l da renunta la semnul F15.
    Nu e un no-op tacut: o regula care prin definitie nu refuza nu are cum sa se faca
    auzita altfel, iar apelantul din productie care coboara F15 (`aplica_comenzi` din
    `asociere.py`) trece o lista adevarata, care ajunge in raspuns.

    `id_stabil` spune daca `IDRR`-urile din `lanturi` sunt numere pe care operatorul le
    poate cauta. In editorul de oricand sunt (False nu se trimite de acolo). Pe calea de
    INGESTIE nu sunt: receptiile nascute in rularea de fata au primit `IDRR` inauntrul
    tranzactiei, deci mesajul «Recepția 235: ...» numea o receptie pe care operatorul nu
    o gaseste nicaieri in lista -- exact plangerea din 09.09.2026, si acelasi defect ca
    «Recepția 188 nu există pe acest angajament» din felia 0056. Cu False, receptia se
    numeste prin data si valoarea ei, adica prin chiar textul randului din formular.
    """
    if f15_ca_avertisment and avertismente is None:
        raise ValueError(
            "f15_ca_avertisment cere o listă «avertismente» în care să scrie; "
            "altfel semnalarea s-ar pierde în tăcere.")
    journal.section("verificarea lanțurilor (%d recepții atinse; F15 = %s)"
                    % (len(lanturi), "semn" if f15_ca_avertisment else "veto"))
    for idrr, lant in lanturi.items():
        rec = receptii[idrr]
        nume = _numeste_receptia(idrr, rec, id_stabil)
        lant = sorted(lant, key=lambda x: (x["data_h"], x["idrh"]))

        ind_rec = _indicatori_receptie(rec)

        # The whole chain as the rules below see it, plus the reception it has to
        # close on. Everything a refusal talks about is on these lines.
        journal.line("recepția %s (%s) SumaAntet=%.2f  linii=%s",
                     idrr, nume, float(rec.get("suma_antet") or 0),
                     journal.sume_pe_indicator(rec["rhr"]))
        journal.table(
            ("IDRH", "ancora", "DataH", "Total", "stergere", "linii"),
            [(i["idrh"], _ancora_jurnal(i), i["data_h"], i["total"], i["stergere"],
              journal.sume_pe_indicator(i["linii"])) for i in lant])

        precedente: Set[str] = set()
        for inst in lant:
            # NIMIC despre `DataR` aici. Vezi docstring-ul: semnul F13 a fost sters cu
            # totul pe 09.09.2026, fiindca se aprindea pe date corecte.
            ind_inst = _indicatori_instantaneu(inst)

            # --- F14, submultimea de indicatori. Slab (majoritatea angajamentelor au un
            # singur indicator), dar corect: indicatorii se adauga la o receptie in timp
            # si cad la zero, insa nu dispar din bloc.
            if ind_inst and not ind_inst <= ind_rec:
                lipsa = ", ".join(sorted(ind_inst - ind_rec))
                journal.line("F14 CADE pe IDRH %s: instantaneul are {%s}, recepția "
                             "are {%s}", inst["idrh"], ", ".join(sorted(ind_inst)),
                             ", ".join(sorted(ind_rec)))
                raise DecizieInvalida(
                    f"Instantaneul de la {inst['data_h']} numește indicatorii {lipsa}, "
                    f"pe care {nume.lower()} nu îi are."
                )

            # --- F16, multimile doar cresc, de-a lungul lantului ordonat dupa DataH.
            if ind_inst and not precedente <= ind_inst:
                pierduti = ", ".join(sorted(precedente - ind_inst))
                journal.line("F16 CADE pe IDRH %s: până aici {%s}, acum {%s}",
                             inst["idrh"], ", ".join(sorted(precedente)),
                             ", ".join(sorted(ind_inst)))
                raise DecizieInvalida(
                    f"Instantaneul de la {inst['data_h']} pierde indicatorii "
                    f"{pierduti}, prezenți mai devreme în lanțul recepției "
                    f"({nume.lower()}). Un indicator poate cădea la zero, dar nu poate "
                    f"dispărea."
                )
            if ind_inst:
                precedente = precedente | ind_inst

        journal.line("F14 și F16 trec pe toate cele %d instantanee ale lanțului",
                     len(lant))

        # --- F15, capatul lantului --------------------------------------------
        if not lant:
            journal.line("lanț gol, nimic de închis")
            continue
        ultimul = lant[-1]
        if ultimul["stergere"]:
            journal.line("F15 sărit: ultimul instantaneu (IDRH %s) e rândul de "
                         "ștergere", ultimul["idrh"])
            # SARIT DELIBERAT. Ultimul instantaneu al unei receptii sterse E randul de
            # stergere; a-l compara cu starea de ACUM nu inseamna nimic. Receptiile
            # reconstituite sunt mereu in categoria asta.
            continue
        def _f15(mesaj: str) -> None:
            """Veto in ingestie, semnalare in editorul de oricand. Vezi docstring-ul."""
            if f15_ca_avertisment:
                avertismente.append(mesaj)
            else:
                raise DecizieInvalida(mesaj)

        if round(ultimul["total"], 2) != round(rec["suma_antet"], 2):
            journal.line("F15 CADE pe total: ultimul IDRH %s (%s) are %.2f, "
                         "SumaAntet e %.2f, diferența %.2f",
                         ultimul["idrh"], journal.moment(ultimul["data_h"]),
                         ultimul["total"], rec["suma_antet"],
                         round(ultimul["total"] - rec["suma_antet"], 2))
            _f15(
                f"{nume}: ultimul instantaneu ({ultimul['data_h']}) are "
                f"totalul {ultimul['total']:.2f}, dar recepția valorează acum "
                f"{rec['suma_antet']:.2f}. Lanțul nu se închide."
            )
            # In modul VETO randul de sus a ridicat deja. In modul AVERTISMENT se iese
            # aici in mod deliberat: daca totalul nu se potriveste, nici liniile nu au
            # cum, iar a doua semnalare ar fi aceeasi veste spusa de doua ori.
            continue
        val_inst = _valori_pe_indicator(ultimul["linii"])
        val_rec = _valori_pe_indicator(rec["rhr"])
        # BOTH sides, as the rule sees them -- summed per indicator and with the
        # zeroes already dropped. The raw lines are in the tables above; these two
        # lines are what the comparison actually runs on, and the difference
        # between the two is the whole answer when a right-looking placement is
        # refused.
        journal.line("F15 pe linii: instantaneu %s", _ca_text(val_inst))
        journal.line("              recepție    %s", _ca_text(val_rec))
        if val_inst and val_inst != val_rec:
            journal.line("F15 CADE pe linii: tablourile de mai sus nu sunt egale")
            dif = ", ".join(
                f"{cod}: instantaneu {val_inst.get(cod, 0):.2f} / recepție "
                f"{val_rec.get(cod, 0):.2f}"
                for cod in sorted(set(val_inst) | set(val_rec))
                if round(val_inst.get(cod, 0), 2) != round(val_rec.get(cod, 0), 2))
            _f15(
                f"{nume}: liniile ultimului instantaneu nu se potrivesc cu "
                f"cele ale recepției ({dif}). Lanțul nu se închide."
            )
        else:
            journal.line("F15 trece: lanțul se închide pe recepția %s", idrr)


def _ca_text(valori: Dict[str, float]) -> str:
    """A {indicator: value} map on one line, for the journal. `(gol)` when empty."""
    if not valori:
        return "(gol)"
    return "  ".join("%s=%.2f" % (c, v) for c, v in sorted(valori.items()))


def _valori_pe_indicator(linii) -> Dict[str, float]:
    """
    Valoarea pe INDICATOR, adunata -- si fara indicatorii ramasi pe zero.

    Doua defecte reparate deodata (09.09.2026, plangerea «pun corect recepțiile la locul
    lor si tot imi apare mesajul»):

    1. Varianta veche era o dictionar-comprehensiune pe `cod_indicator`, deci o receptie
       cu MAI MULTE linii pe acelasi indicator (alt `CodAI`, alt `CodSSI`) pastra doar
       ULTIMA linie -- si cele doua laturi, citite din tabele diferite cu ordini diferite,
       puteau pastra linii diferite. Comparatia refuza atunci o plasare perfect corecta,
       si o refuza tacut de fiecare data. Suma pe indicator e bine definita indiferent de
       ordine, iar totalul pe antet a fost deja verificat mai sus.

    2. Un indicator cazut la ZERO (F16 spune limpede ca poate) apare pe o latura si
       lipseste de pe cealalta, dupa cum tabela pastreaza sau nu randul gol. Zerourile ies
       din amandoua, deci cele doua tablouri se compara pe ce inseamna bani.
    """
    out: Dict[str, float] = {}
    for l in linii or []:
        cod = l.get("cod_indicator") or ""
        out[cod] = out.get(cod, 0.0) + float(l.get("valoare") or 0)
    return {cod: round(v, 2) for cod, v in out.items() if round(v, 2) != 0}


def _numeste_receptia(idrr: int, rec: dict, id_stabil: bool = True) -> str:
    """
    Cum se numeste o receptie INTR-UN MESAJ CITIT DE OPERATOR.

    Cu `id_stabil=False` numarul NU se scrie deloc: pe calea de ingestie `IDRR`-ul unei
    receptii nascute in rularea curenta s-a dat inauntrul tranzactiei si nu supravietuieste
    derularii inapoi, deci operatorul cauta in zadar «Recepția 235». Data si valoarea sunt
    chiar textul randului din formular (`AsociereForm.CaptionReceptie`), deci se gasesc din
    prima privire.
    """
    bucati = []
    d = rec.get("data_r")
    if d is not None:
        m = _ca_datetime(d)
        bucati.append(m.strftime("%d.%m.%Y") if hasattr(m, "strftime") else str(d))
    bucati.append(f"{float(rec.get('suma_antet') or 0):.2f}")
    coada = " · ".join(bucati)
    if id_stabil:
        return f"Recepția {idrr} ({coada})"
    return f"Recepția din {coada}"


def _ca_datetime(v):
    """Datele vin din driver ca `datetime` sau `date`; se compara pe acelasi teren."""
    from datetime import date, datetime
    if isinstance(v, datetime):
        return v
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day)
    return v


# ===========================================================================
# PASUL 4c, FAZA DOUA -- aplicarea deciziilor
# ===========================================================================
_H_ASOCIAZA_SQL = (
    "UPDATE FX_Receptii_H SET IDRR = %s, Sters = 0, EsteStergere = %s WHERE IDRH = %s"
)
_H_IGNORA_SQL = (
    "UPDATE FX_Receptii_H SET IDRR = NULL, Sters = 1 WHERE IDRH = %s"
)
_R_MARCHEAZA_STEARSA_SQL = "UPDATE FX_Receptii_R SET Sters = 1 WHERE IDRR = %s"
_H_TIP_SQL = "UPDATE FX_Receptii_H SET TipReceptie = %s, HASH = %s WHERE IDRH = %s"
# F32: un antet gol nu e in lant, deci nu poate fi «ultimul» si nu ia `Final`.
_H_LANT_SQL = (
    "SELECT H.IDRH, H.DataH, H.Descriere, H.TipReceptie, H.CodAngajament "
    "FROM FX_Receptii_H H "
    "WHERE H.IDRR = %s AND " + SNAPSHOT_COUNTS_SQL + " ORDER BY H.DataH, H.IDRH"
)
# Membrii deja asezati ai unui lant atins de deciziile de acum (F15 / F16 judeca lantul
# intreg). Acelasi filtru F32: un antet gol in mijlocul lantului ar avea multimea de
# indicatori VIDA, si F16 («multimile doar cresc») ar cadea pe un rand pe care operatorul
# nu il vede si nu l-a atins.
_H_MEMBRI_LANT_SQL = (
    "SELECT H.IDRH, H.DataH, H.Total, H.EsteStergere FROM FX_Receptii_H H "
    "WHERE H.IDRR = %s AND " + SNAPSHOT_COUNTS_SQL
)


def aplica_decizii(cursor, cod: str, decizii: List[dict], instantanee: List[dict],
                   receptii: List[dict], warnings: List[str],
                   ancore: Optional[Dict[int, int]] = None) -> Dict[str, int]:
    """
    Faza a doua a pasului 4c. Aplica `decizii` si ignora complet trecerea automata.

    `ancore` = {indice in ListaReceptii -> IDRR}, cele intoarse de
    `step4b_receptii_prelucrare` DIN RULAREA ASTA. Prin ele se rezolva deciziile care
    numesc o receptie prin `rand_receptie`; vezi docstring-ul pasului 4b pentru de ce
    `idrr`-ul din propunere nu are voie sa fie numele.

    Intoarce numaratorile scrise.
    """
    dupa_ancora = {ancora(i): i for i in instantanee}

    journal.section("hotărârile operatorului (%d)" % len(decizii))
    journal.table(
        ("ancora", "acțiune", "IDRR", "rand_receptie", "receptie_noua", "data_h"),
        [(_ancora_jurnal(d), d["actiune"], d["idrr"], d["rand_receptie"],
          d["receptie_noua"], d["data_h"]) for d in decizii])
    journal.line("ancore din pasul 4b (rând ListaReceptii -> IDRR): %s",
                 dict(sorted((ancore or {}).items())) or "(niciuna)")

    verifica_acoperirea(decizii, instantanee)
    etichete = verifica_etichetele(decizii)

    # 4c-bis mai intai: dupa asta fiecare eticheta e un IDRR real.
    noi = materializeaza_reconstituite(cursor, cod, decizii, instantanee,
                                       etichete, warnings)

    # Tabloul receptiilor se reciteste, ca sa contina si cele tocmai create.
    toate = {r["idrr"]: r for r in citeste_receptii(cursor, cod)}

    ancore = ancore or {}

    def _tinta(d: dict) -> int:
        if d["rand_receptie"] is not None:
            idrr = ancore.get(d["rand_receptie"])
            if idrr is None:
                # Randul acela nu a nascut nicio receptie in rularea de fata. Ori sarcina
                # utila s-a schimbat intre faze, ori deciziile vin dintr-un dosar mai
                # vechi. In ambele cazuri tabloul descris nu mai exista.
                raise DecizieInvalida(
                    f"Rândul {d['rand_receptie']} din «ListaReceptii» nu a creat nicio "
                    f"recepție în această salvare. Descărcarea nu mai este aceeași — "
                    f"reluați-o."
                )
            return idrr
        if d["idrr"] is not None:
            if d["idrr"] not in toate:
                raise DecizieInvalida(
                    f"Recepția {d['idrr']} nu există pe acest angajament.")
            return d["idrr"]
        return noi[d["receptie_noua"]]

    # --- se construiesc lanturile REZULTATE si se valideaza INAINTE de a scrie ------
    journal.section("ce vrea să facă rularea")
    lanturi: Dict[int, List[dict]] = {}
    for d in decizii:
        if d["actiune"] == ACTIUNE_IGNORAT:
            journal.line("%s (IDRH %s): IGNORAT -- IDRR gol, Sters = 1",
                         _ancora_jurnal(d), dupa_ancora[ancora(d)]["idrh"])
            continue
        inst = dupa_ancora[ancora(d)]
        idrr_tinta = _tinta(d)
        journal.line("%s (IDRH %s, %s, total %.2f): %s -> recepția %s",
                     _ancora_jurnal(d), inst["idrh"], journal.moment(inst["data_h"]),
                     inst["total"], d["actiune"].upper(), idrr_tinta)
        lanturi.setdefault(idrr_tinta, []).append(inst)

    # Instantaneele DEJA asociate ale acelorasi receptii fac parte din lant si ele --
    # F15 si F16 se refera la lantul intreg, nu doar la ce se adauga acum.
    for idrr in list(lanturi):
        cursor.execute(_H_MEMBRI_LANT_SQL, (idrr,))
        for r in cursor.fetchall():
            idrh = int(r["IDRH"])
            if any(x["idrh"] == idrh for x in lanturi[idrr]):
                continue
            cursor.execute(
                "SELECT CodIndicator, CodAI, CodSSI, IdClsf, Valoare FROM FX_Receptii "
                "WHERE IDRH = %s", (idrh,))
            linii = [{"cod_indicator": x["CodIndicator"] or "",
                      "cod_ai": x["CodAI"] or "", "cod_ssi": x["CodSSI"] or "",
                      "id_clsf": x["IdClsf"], "valoare": float(x["Valoare"] or 0)}
                     for x in cursor.fetchall()]
            lanturi[idrr].append({
                # No anchor: not decided in this run (see the note below).
                "idrh": idrh, "rand_istoric": None, "data_h": r["DataH"],
                "descriere": "", "total": float(r["Total"] or 0),
                "stergere": bool(r["EsteStergere"]), "linii": linii,
            })
            # These are NOT decided in this run: they are already written, and they
            # join the chain because F15 and F16 speak about the whole chain. A
            # refusal can therefore come from a row the operator never touched.
            journal.line("recepția %s primește în lanț și IDRH %s (%s, total %.2f), "
                         "deja legat dinainte", idrr, idrh,
                         journal.moment(r["DataH"]), float(r["Total"] or 0))

    # `id_stabil=False`: receptiile nascute in rularea de fata au primit `IDRR` inauntrul
    # tranzactiei asteia, deci numarul nu e un nume pe care operatorul sa-l poata cauta --
    # «Recepția 235» nu exista nicaieri in lista lui (plangere, 09.09.2026). Mesajele o
    # numesc prin data si valoare, adica prin chiar textul randului din formular.
    # `warnings` ramane trecut ca F15 sa aiba unde scrie daca vreodata coboara si aici;
    # acum e veto (`f15_ca_avertisment` implicit False), deci lista nu se atinge.
    valideaza_plasarile(lanturi, toate, avertismente=warnings, id_stabil=False)

    # --- scrierea ---------------------------------------------------------------
    numarat = {"asociat": 0, "ignorat": 0, "stergere": 0, "reconstituit": len(noi)}
    for d in decizii:
        inst = dupa_ancora[ancora(d)]
        if d["actiune"] == ACTIUNE_IGNORAT:
            # F17 / 1.6: o salvare care nu a consemnat nicio schimbare. `Sters = 1` pe
            # instantaneu, `IDRR` lasat gol. A ignora nu pierde nimic; a forta pe o
            # receptie injecteaza o valoare falsa in cronologia ei, la acea data, si
            # verificarea de capat de lant NU o prinde daca aterizeaza la mijloc.
            cursor.execute(_H_IGNORA_SQL, (inst["idrh"],))
            numarat["ignorat"] += 1
            continue

        if d["actiune"] == ACTIUNE_RECONSTITUIRE:
            # Randul care PORNESTE lantul. Se ataseaza ca orice altul; receptia lui
            # tocmai a fost creata mai sus.
            cursor.execute(_H_ASOCIAZA_SQL, (_tinta(d), 0, inst["idrh"]))
            numarat["asociat"] += 1
            continue

        idrr = _tinta(d)
        este_stergere = 1 if d["actiune"] == ACTIUNE_STERGERE else 0
        cursor.execute(_H_ASOCIAZA_SQL, (idrr, este_stergere, inst["idrh"]))
        if este_stergere:
            cursor.execute(_R_MARCHEAZA_STEARSA_SQL, (idrr,))
            numarat["stergere"] += 1
        else:
            numarat["asociat"] += 1

    # --- Final / Partial, o singura data per receptie ---------------------------
    journal.section("ce s-a scris")
    journal.line("legături: %s", numarat)
    for idrr in sorted(lanturi):
        recalculeaza_final(cursor, idrr)

    return numarat


def recalculeaza_final(cursor, idrr: int) -> None:
    """
    `Final` e cel mai TARZIU instantaneu dupa `DataH` din lant; tot ce e mai devreme e
    `Partial`.

    ASTA E O SCHIMBARE FATA DE ACCESS SI TREBUIE CITITA CA ATARE. `AsociazaFinal` facea
    `Final` orice tocmai atasase si retrograda restul. Cu plasare MANUALA regula aceea ar
    lasa un instantaneu din ianuarie sa devina `Final` pe o receptie care are deja unul
    din mai -- fiindca «tocmai atasat» nu mai inseamna «cel mai nou». Se recalculeaza per
    receptie, o data, dupa aplicarea TUTUROR deciziilor, nu la fiecare atasare.

    `HASH` se rescrie pe orice instantaneu al carui `TipReceptie` se schimba, exact ca in
    VBA -- hash-ul de identitate al antetului contine tipul.
    """
    cursor.execute(_H_LANT_SQL, (idrr,))
    lant = cursor.fetchall()
    if not lant:
        return
    ultimul_idrh = int(lant[-1]["IDRH"])
    for r in lant:
        dorit = "Final" if int(r["IDRH"]) == ultimul_idrh else "Partial"
        if (r["TipReceptie"] or "") == dorit:
            continue
        h = fx_receptii_h_get_hash_ident(
            r["CodAngajament"] or "", r["DataH"], dorit, r["Descriere"] or "")
        cursor.execute(_H_TIP_SQL, (dorit, h, int(r["IDRH"])))
        journal.line("recepția %s: IDRH %s trece din «%s» în «%s»",
                     idrr, int(r["IDRH"]), r["TipReceptie"] or "(gol)", dorit)
