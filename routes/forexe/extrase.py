# routes/forexe/extrase.py
"""
Importul extraselor de cont (SNM) -- felia 0057. Portul lui `FX_Extrase_Prelucrare`
din `mdl_FX_Extrase`, mutat de pe partea de Access pe server.

DE CE AICI SI NU IN ROBOT
-------------------------
Robotul (KBot.Forexe.ForexeSNM) descarca PDF-urile si desface XML-ul din ele. Atat.
Citirea XML-ului cere nomenclatoare -- Clasificatii, Parteneri, Unitati, FX_Indicatori --
care traiesc in baza unitatii, iar clientul nu are acces la ele. Exact aceeasi impartire
o avea si sistemul vechi: robotul impingea pe pipe trei campuri (`PdfFisier`,
`DataFisier`, `XmlContent`), iar Access facea toata prelucrarea.

CONTRACT
--------
POST /api/forexe/extrase/import
    { "extrase": [ { "PdfFisier": "...", "DataFisier": "dd.MM.yyyy HH:mm:ss",
                     "XmlContent": "<?xml ...", "CaleLocala": "C:\\..." }, ... ] }
    -> 200 { "primite", "importate", "sarite", "randuri", "avertismente": [...] }

GET /api/forexe/extrase/ultima
    -> 200 { "data_extras": "YYYY-MM-DD" | null }
    Cea mai recenta `FX_Extrase_F.DataExtras`. Clientul o foloseste ca punct de oprire
    al paginarii, ca sa nu descarce toata cutia de mesaje la fiecare apasare.

FORMA XML-ULUI (din portul Access, nu ghicita)
---------------------------------------------
    /extras                       @Data_extras
      cont_ext*
        cont                      text = Cont, @Cod_IBAN
        Sold_precedent            Sumad / Sumac   -> SID / SIC
        Rulaj_zi                  Sumad / Sumac   -> RPD / RPC
        Total_sume                Sumad / Sumac   -> TSD / TSC
        Sold_final                Sumad / Sumac   -> SFD / SFC
        cont_misc*                un rand de extras (databan, datadoc, nrdoc, ...)

TREI NIVELE, TREI TABELE
------------------------
    /extras   -> FX_Extrase_F   (fisierul: numele, calea, data, XML-ul brut, HASH)
    cont_ext  -> FX_Extrase_H   (contul: IBAN, soldurile, unitatea, clasificatia)
    cont_misc -> FX_Extrase     (operatiunea: suma, platitorul, referinta, explicatiile)

DEDUPLICARE -- pe CHEIA NATURALA, nu pe sirul HASH
--------------------------------------------------
Aceeasi decizie ca la ingestia angajamentelor (D9, felia 0048): hash-urile deja aflate
in MariaDB au fost calculate de invelisul BCrypt din Access, iar codificarea pe octeti a
sirului UTF-16 nu se poate reconstitui din export. Daca hash-ul calculat aici difera cu
un octet, FIECARE extras vazut inainte ar parea nou. Asa ca se compara ce se poate
compara sigur:

  * fisierul     (NumeFisier, DataExtras)
  * operatiunea  (DataDoc, NrDoc, platitor_cui, suma_debit, suma_credit)

Si mai concret decat atat: pe randurile scrise de Access, `FX_Extrase.HASH` este GOL.
`Extrase_Add` calcula hash-ul, cauta dupa el cu `FindFirst`... si apoi NU il scria in
`rsEx!HASH`, asa ca fiecare cautare cadea pe gol. Adica deduplicarea randurilor de extras
nu a functionat niciodata in sistemul vechi -- se vede in exportul tabelei, unde coloana
e goala pe toate randurile. Aici hash-ul SE SCRIE (formula lui Access, neschimbata), dar
decizia ramane pe cheia naturala: coloana are nevoie de o rulare completa inainte sa se
poata sprijini cineva pe ea.

`DataDoc` este TEXT in `FX_Extrase`, nu data -- Access scria acolo o data printr-un camp
Text, deci a iesit in formatul scurt al masinii: `dd.MM.yyyy` (confirmat in exportul
tabelei). Se scrie in acelasi format, altfel randurile noi nu s-ar mai compara cu cele
vechi si ar arata altfel in vederea Plati, care afiseaza coloana ca sir brut.

UNDE STAU NOMENCLATOARELE (si de ce nu se ghicesc)
--------------------------------------------------
  * `DefaSS` / `DefaSSS` -- traduc primele 3 caractere ale contului ("21E", "23A") in
    sursa-sector ("02A", "02E"). Fara ele nu se poate afla unitatea contului. Stau in
    `AVACONT_COMUN` (spus de operator, 08.09.2026) si se citesc CALIFICAT, ca
    DefaClsfF / DefaArticol din istoric.py: conexiunea e deschisa pe baza unitatii, deci
    un nume necalificat n-ar fi gasit niciodata tabela. Nu sunt in
    `MariaDB_Schema/000_DEMO.sql` fiindca acela e dump-ul unei baze de UNITATE.
  * `Clasificatii_Venituri` -- fostul `ClasificatiiV` din Access, redenumit si adus pe
    MariaDB (operator, 09.09.2026; e in `sql/AVACONT_SURSA.sql`). Da `IdClsfV`, si se
    citeste NECALIFICAT, din baza unitatii, fiindca acolo sta: e o tabela pe baza, nu una
    comuna ca DefaSS. Aceleasi coloane ca in Access -- Capitol, SubCapitol, Paragraf,
    IdClsfV -- deci interogarea lui FX_DicClsfV se poarta verbatim.
    FARA filtru pe unitate: tabela n-are `IdUnitate` nici in Access, nici aici, fiindca
    tine clasificatiile de venituri ale intregii DIRECTII. E singurul nomenclator din set
    care nu e cautat pe unitate, si asta e o proprietate a lui, nu o scapare.

Cand tabela lipseste, campul ramane NULL -- exact ce facea si Access cand cautarea lui
nu gasea nimic (`IdUnitate = -1` inseamna coloana nescrisa) -- DAR se pune un avertisment
cu numele tabelei in raspuns, ca lipsa sa nu treaca tacut. Randurile de bani
(FX_Extrase) se scriu oricum: ele nu depind de unitate.
"""
import json
import logging
import math
from datetime import datetime
from xml.etree import ElementTree

import mysql.connector
from flask import request, g, current_app

from routes.auth.guard import require_session
from utils.database import COMMON_DB, get_kbot_connection
# Cronometrul, in `forexe_timing.log` (vezi utils/timing.py). Ruta asta e a doua
# jumatate a ingestiei: parsare XML plus scrieri rand cu rand, deci despartirea
# «sql» / «propriu» chiar spune care din doua tine timpul.
from utils import timing

from .prelucrare_helpers import (
    extract_obs_value,
    get_hash_from_dict,
    null_if_empty,
    parse_loose_number,
)

from . import forexe_bp

logger = logging.getLogger(__name__)

# Numarul de eroare MariaDB pentru "tabela nu exista". Doua nomenclatoare se opresc aici,
# nu in blocul general de eroare: `AVACONT_COMUN.DefaSS`/`DefaSSS`, care pot lipsi de pe
# server, si `Clasificatii_Venituri`, care e in AVACONT_SURSA abia din 09.09.2026, deci
# lipseste din orice baza nesincronizata de atunci. `Clasificatii` nu are nevoie de
# tratarea asta: e in schema de baza de la inceput.
_ER_NO_SUCH_TABLE = 1146

# Lunile pe care le stia MonthNameToNumber: RO + EN, abrevieri si nume intregi.
_LUNI = {
    "IAN": 1, "IANUARIE": 1, "JAN": 1, "JANUARY": 1,
    "FEB": 2, "FEBRUARIE": 2, "FEBRUARY": 2,
    "MAR": 3, "MARTIE": 3, "MARCH": 3,
    "APR": 4, "APRILIE": 4, "APRIL": 4,
    "MAI": 5, "MAY": 5,
    "IUN": 6, "IUNIE": 6, "JUN": 6, "JUNE": 6,
    "IUL": 7, "IULIE": 7, "JUL": 7, "JULY": 7,
    "AUG": 8, "AUGUST": 8,
    "SEP": 9, "SEPT": 9, "SEPTEMBRIE": 9, "SEPTEMBER": 9,
    "OCT": 10, "OCTOMBRIE": 10, "OCTOBER": 10,
    "NOI": 11, "NOIEMBRIE": 11, "NOV": 11, "NOVEMBER": 11,
    "DEC": 12, "DECEMBRIE": 12, "DECEMBER": 12,
}


# ---------------------------------------------------------------------------
# Raspuns
# ---------------------------------------------------------------------------
def _json_utf8(payload, status):
    """JSON cu diacritice LITERALE: avertismentele sunt text romanesc."""
    body = json.dumps(payload, ensure_ascii=False)
    return current_app.response_class(body, status=status, mimetype="application/json")


# ---------------------------------------------------------------------------
# Parsare
# ---------------------------------------------------------------------------
def _text(node):
    """Textul unui nod, trimmed, sau "" cand nodul lipseste."""
    if node is None:
        return ""
    return (node.text or "").strip()


def _text_copil(parinte, nume: str) -> str:
    """Port de GetXmlNodeText: textul copilului `nume`, sau "" daca lipseste."""
    if parinte is None:
        return ""
    return _text(parinte.find(nume))


def parse_data_extras(valoare) -> "datetime | None":
    """
    Port de FX_ParseDataExtraseF -- atributul `Data_extras` al nodului /extras.

    Detecteaza separatorul (/ . -), apoi citeste dd<sep>mm<sep>yyyy. A doua parte poate
    fi si un nume de luna (RO sau EN, abreviat sau intreg). Orice altceva -> None, ca in
    original: o data pe care nu o intelegem nu se inventeaza.
    """
    if valoare is None:
        return None
    text = str(valoare).strip()
    if text == "":
        return None

    sep = None
    for candidat in ("/", ".", "-"):
        if candidat in text:
            sep = candidat
            break
    if sep is None:
        return None

    parti = [p.strip() for p in text.split(sep)]
    if len(parti) != 3:
        return None

    try:
        zi = int(parti[0])
        an = int(parti[2])
    except ValueError:
        return None

    try:
        luna = int(parti[1])
    except ValueError:
        luna = _LUNI.get(parti[1].upper(), 0)

    if an < 100 or not (1 <= luna <= 12) or not (1 <= zi <= 31):
        return None
    try:
        return datetime(an, luna, zi)
    except ValueError:
        # 31 februarie trece de validarea de mai sus, ca in VBA; DateSerial l-ar fi
        # rostogolit in martie, datetime ridica. Ridicarea e raspunsul mai bun.
        return None


def parse_data_yyyymmdd(valoare):
    """Port de ParseXmlDate_YYYYMMDD: 8 cifre lipite, altfel None."""
    text = "" if valoare is None else str(valoare).strip()
    if len(text) != 8 or not text.isdigit():
        return None
    try:
        return datetime(int(text[0:4]), int(text[4:6]), int(text[6:8])).date()
    except ValueError:
        return None


def parse_cont_misc(nod) -> dict:
    """
    Port de ParseContMiscNodeToDict: atributele nodului, apoi copiii, apoi atributele
    copiilor sub cheia "copil.atribut". Numele raman EXACT cum sunt in XML.

    Un copil cu descendenti (nu doar text) intra ca XML brut, ca in original -- nimeni
    nu citeste asa ceva azi, dar a-l pierde ar ascunde o schimbare de format.
    """
    d = {}
    for nume, valoare in nod.attrib.items():
        d[nume] = valoare

    for copil in nod:
        if len(copil) == 0:
            text = copil.text
            d[copil.tag] = text if text is not None and text.strip() != "" else None
        else:
            d[copil.tag] = ElementTree.tostring(copil, encoding="unicode")
        for nume, valoare in copil.attrib.items():
            d[f"{copil.tag}.{nume}"] = valoare
    return d


# ---------------------------------------------------------------------------
# Hash-uri (formula Access, scrisa dar nefolosita la decizii -- vezi antetul)
# ---------------------------------------------------------------------------
def hash_fisier(pdf_fisier: str, data_fisier: str) -> str:
    """HASH-ul de FX_Extrase_F: {PdfFisier, DataFisier}, in ordinea din Access."""
    return get_hash_from_dict({"PdfFisier": pdf_fisier, "DataFisier": data_fisier})


def _cstr_double_vba(v: float) -> str:
    """
    `CStr(<Double>)` asa cum il tipareste VBA: cel mult 15 cifre semnificative.

    Conteaza aici si nicaieri altundeva. `100 * Round(0.07, 2)` da in virgula mobila
    7.000000000000001; VBA taie la 15 cifre semnificative si scrie "7", pe cand `str()`
    din Python scrie sirul cel mai scurt care se citeste inapoi identic, adica
    "7.000000000000001". Din 600 de valori de bani incercate, 48 ies diferit -- destul cat
    sa conteze. `%.15g` reproduce taietura lui VBA; separatorul zecimal ramane cel al
    masinii romanesti pe care rula Access.

    Nu se atinge `_vba_cstr` din prelucrare_helpers: acela e folosit de hash-urile de
    receptii, unde forma actuala e deja in date.
    """
    return ("%.15g" % v).replace(".", ",")


def _cheie_suma(v: float) -> str:
    """
    Port de `Int(v) & "_" & 100 * Round(v - Int(v), 2)` din Extrase_Add.

    `Int` in VBA e floor (spre minus infinit), nu trunchiere -- conteaza pe sumele
    negative. Partea zecimala se inmulteste cu 100 DUPA rotunjire, iar rezultatul e tot un
    Double, deci se tipareste ca in VBA (vezi _cstr_double_vba).
    """
    intreg = math.floor(v)
    return f"{intreg}_{_cstr_double_vba(100 * round(v - intreg, 2))}"


def hash_operatiune(data_doc, nr_doc, cui, suma_debit: float, suma_credit: float) -> str:
    """
    HASH-ul unui rand de FX_Extrase, in ordinea exacta a dictionarului din Extrase_Add.

    ATENTIE la cheia "IBAN": in Access ea se alimenta din `vIbanPlat`, o variabila care
    NU E ATRIBUITA NICIODATA in acea procedura (IBAN-ul platitorului sta in `vPlatitorIBAN`).
    Deci a intrat mereu goala in hash. Se reproduce ca atare -- un hash "reparat" aici ar
    fi un sir nou, care nu se mai potriveste cu niciun rand scris de Access.
    """
    return get_hash_from_dict({
        "DataDoc": data_doc,
        "NrDoc": nr_doc,
        "CUI": cui,
        "IBAN": None,
        "SumaDebit": _cheie_suma(suma_debit),
        "SumaCredit": _cheie_suma(suma_credit),
    })


# ---------------------------------------------------------------------------
# Nomenclatoare
# ---------------------------------------------------------------------------
class _Nomenclatoare:
    """
    Cautarile de care are nevoie un antet de cont, citite O SINGURA DATA per import.

    Access le tinea in dictionare globale (FX_DicClsf, FX_DicClsfV, FX_DicPart, FX_DicInd)
    pe care le construia la prima folosire. Acelasi lucru, doar ca durata de viata e
    cererea: un import nu are voie sa vada nomenclatorul asa cum era acum o ora.

    O tabela lipsa NU opreste importul: cautarea ei intoarce None si numele tabelei se
    aduna in `avertismente`, ca lipsa sa fie vizibila in raspuns.
    """

    def __init__(self, cursor):
        self._cursor = cursor
        self.avertismente = []
        self._sursa_pentru_prefix = self._incarca_surse()
        self._unitate_pentru_sursa = self._incarca_unitati()
        self._clsf = {}      # (id_unitate) -> {ClsfSal: IdClsfAcc}
        self._clsf_v = None  # toata baza, fara unitate: {Capitol+SubCapitol+Paragraf: IdClsfV}
        self._parteneri = {}  # (id_unitate) -> {CodFiscal: CodPartener}
        self._indicatori = None  # {f"{CodAngajament}!{IdClsf}": CodAI}

    # -- surse ------------------------------------------------------------
    def _incarca_surse(self):
        """
        SSS (primele 3 caractere ale contului) -> SS, din DefaSS INNER JOIN DefaSSS.
        Interogarea e cea din Extrase_H_Add, cu numele CALIFICATE.

        Cele doua tabele stau in AVACONT_COMUN, nu in baza unitatii -- sunt nomenclator
        comun, ca DefaClsfF / DefaArticol din istoric.py sau BIC / CAI din ord_edit.py --
        iar conexiunea deschisa aici e pe baza unitatii. Nescrise asa, MariaDB raspundea
        1146 pe ORICE baza si ruta cadea de fiecare data pe ramura "nomenclatorul
        lipseste", deci antetele ar fi ramas fara unitate si dupa crearea tabelelor
        (operator, 08.09.2026: "defass si defasss sunt amandoua in avacont_comun").
        """
        try:
            self._cursor.execute(
                f"SELECT SSS, SS FROM {COMMON_DB}.DefaSS "
                f"INNER JOIN {COMMON_DB}.DefaSSS "
                f"ON {COMMON_DB}.DefaSS.IDSS = {COMMON_DB}.DefaSSS.IDSS"
            )
            return {str(sss): str(ss) for (sss, ss) in self._cursor.fetchall()}
        except mysql.connector.Error as err:
            if err.errno != _ER_NO_SUCH_TABLE:
                raise
            self.avertismente.append(
                f"Nomenclatorul «{COMMON_DB}.DefaSS / {COMMON_DB}.DefaSSS» nu există pe "
                "acest server, deci unitatea și clasificația conturilor nu au putut fi "
                "stabilite. Extrasele s-au importat, dar antetele lor rămân fără unitate."
            )
            return None

    def _incarca_unitati(self):
        """SursaSector -> IdUnitate. Portul lui gUnitati.IdUnitatePentru(Sursa)."""
        self._cursor.execute("SELECT SursaSector, IdUnitate FROM Unitati")
        return {str(ss): int(idu) for (ss, idu) in self._cursor.fetchall()}

    @property
    def are_surse(self) -> bool:
        """Exista tabela DefaSS/DefaSSS in baza asta? False = avertismentul e deja pus."""
        return self._sursa_pentru_prefix is not None

    def sursa_pentru_cont(self, cont: str):
        """SS-ul contului, din primele lui 3 caractere. None = nu s-a putut afla."""
        if self._sursa_pentru_prefix is None or not cont:
            return None
        return self._sursa_pentru_prefix.get(cont[:3])

    def unitate_pentru_sursa(self, sursa):
        """IdUnitate pentru un SS, sau None. Access folosea -1 pentru «negasit»."""
        if not sursa:
            return None
        return self._unitate_pentru_sursa.get(sursa)

    # -- clasificatii -----------------------------------------------------
    def clsf_pentru(self, id_unitate: int, clsf_sal: str):
        """
        ClsfSal -> id de clasificatie, pentru unitatea data.

        Se intoarce `IdClsfAcc`, NU `IDClsf`. Motivul e decizia blocata din STATUS:
        `FX_Indicatori.IdClsf` tine id-ul ACCESS, verificat pe date reale in 0011-03,
        si toate tabelele FX_ urmeaza aceeasi conventie. `FX_Extrase_H.IdClsf` e citit
        astazi doar de rapoarte care il compara cu FX_Indicatori, deci trebuie sa fie
        din aceeasi familie.

        Predicatul IdUnitate RAMANE: `Clasificatii` e nomenclator comun si tine randuri
        pentru mai multe unitati in aceeasi baza.
        """
        harta = self._clsf.get(id_unitate)
        if harta is None:
            self._cursor.execute(
                "SELECT ClsfSal, IdClsfAcc FROM Clasificatii WHERE IdUnitate = %s",
                (id_unitate,),
            )
            harta = {}
            for (cs, ida) in self._cursor.fetchall():
                # Primul castiga, ca `FindFirst` din Access: nomenclatorul are
                # duplicate reale pe (IdClsfAcc, IdUnitate) -- vezi 0011-03.
                if cs is not None and str(cs) not in harta:
                    harta[str(cs)] = int(ida)
            self._clsf[id_unitate] = harta
        return harta.get(clsf_sal)

    def clsf_venituri_pentru(self, clsf_sal: str):
        """
        Acelasi ClsfSal pe clasificatia de VENITURI, pentru `IdClsfV`.

        Portul lui FX_DicClsfV, si acum are unde sa se duca: `ClasificatiiV` din Access a
        devenit `Clasificatii_Venituri` in fiecare baza (operator, 09.09.2026), cu aceleasi
        coloane -- Capitol, SubCapitol, Paragraf, IdClsfV -- deci interogarea din Access se
        poarta verbatim, nu se reinventeaza.

        FARA filtru pe unitate, ca in Access: tabela n-are `IdUnitate` nici acolo, nici pe
        MariaDB. Tinea clasificatiile de venituri ale intregii DIRECTII, si le tine si
        acum; de asta e si singurul nomenclator din set care nu e cautat pe unitate.

        `CONCAT_WS('', ...)` si nu `CONCAT(...)`: cele trei coloane sunt nulabile, iar
        `CONCAT` intoarce NULL daca oricare argument e NULL, pe cand `&` din Access trata
        Null ca sir gol. CONCAT_WS sare peste NULL-uri, deci se poarta ca `&`.
        """
        harta = self._clsf_v
        if harta is None:
            try:
                self._cursor.execute(
                    "SELECT CONCAT_WS('', Capitol, SubCapitol, Paragraf) AS Clsf, IdClsfV "
                    "FROM Clasificatii_Venituri"
                )
                harta = {}
                for (clsf, idv) in self._cursor.fetchall():
                    # Primul castiga, ca la `clsf_pentru`.
                    if clsf is not None and str(clsf) not in harta:
                        harta[str(clsf)] = int(idv)
            except mysql.connector.Error as err:
                if err.errno != _ER_NO_SUCH_TABLE:
                    raise
                harta = {}
                # Tabela e in AVACONT_SURSA din 09.09.2026, deci o baza creata sau
                # sincronizata dupa acea data o are. Una migrata inainte, nu.
                self.avertismente.append(
                    "Nomenclatorul «Clasificatii_Venituri» nu există în această bază, deci "
                    "clasificația de venituri (IdClsfV) rămâne necompletată. Baza nu a mai "
                    "fost sincronizată cu AVACONT_SURSA."
                )
            self._clsf_v = harta
        return harta.get(clsf_sal)

    # -- parteneri / indicatori -------------------------------------------
    def partener_pentru(self, id_unitate: int, cod_fiscal):
        """CodFiscal -> CodPartener, in unitatea data. Port de FX_DicPart."""
        if cod_fiscal is None:
            return None
        harta = self._parteneri.get(id_unitate)
        if harta is None:
            self._cursor.execute(
                "SELECT CodFiscal, CodPartener FROM Parteneri WHERE IdUnitate = %s",
                (id_unitate,),
            )
            harta = {}
            for (cf, cp) in self._cursor.fetchall():
                if cf is not None and str(cf) not in harta:
                    harta[str(cf)] = cp
            self._parteneri[id_unitate] = harta
        return harta.get(str(cod_fiscal))

    def indicator_pentru(self, cod_contract, id_clsf):
        """
        "CodAngajament!IdClsf" -> CodAI. Port de FX_DicInd, folosit doar cand extrasul
        poarta codul de contract dar nu si randul lui.
        """
        if self._indicatori is None:
            self._cursor.execute(
                "SELECT CodAngajament, IdClsf, CodAI FROM FX_Indicatori "
                "WHERE CodAngajament IS NOT NULL"
            )
            self._indicatori = {}
            for (cod_ang, idc, cod_ai) in self._cursor.fetchall():
                cheie = f"{cod_ang}!{idc}"
                if cheie not in self._indicatori:
                    self._indicatori[cheie] = cod_ai
        return self._indicatori.get(f"{cod_contract}!{id_clsf}")


# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------
_F_EXISTENTE_SQL = "SELECT NumeFisier, DataExtras FROM FX_Extrase_F"
_F_INSERT_SQL = (
    "INSERT INTO FX_Extrase_F (NumeFisier, CaleFisier, DataExtras, XML, HASH) "
    "VALUES (%s, %s, %s, %s, %s)"
)
_H_INSERT_SQL = (
    "INSERT INTO FX_Extrase_H "
    "(IDEXF, IdClsf, IdUnitate, CodIBAN, Cont, SID, SIC, RPD, RPC, TSD, TSC, "
    " SFD, SFC, IdClsfV) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
)
_E_EXISTENTE_SQL = (
    "SELECT DataDoc, NrDoc, platitor_cui, suma_debit, suma_credit FROM FX_Extrase"
)
_E_INSERT_SQL = (
    "INSERT INTO FX_Extrase "
    "(IDFXH, CodAI, DataBanca, DataDoc, NrDoc, Referinta, ReferintaDest, "
    " platitor_nume, platitor_cui, platitor_iban, suma_debit, suma_credit, "
    " Explicatii, CodContract, RandContract, CodProgram, HASH, CodPartener, IdUnitate) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
)
_ULTIMA_SQL = "SELECT MAX(DataExtras) AS Ultima FROM FX_Extrase_F"


def _id_nou(cursor, tabela: str) -> int:
    """
    Cheia AUTO_INCREMENT tocmai atribuita, refuzata cand e 0.

    Un `lastrowid` de 0 dupa un INSERT reusit inseamna ca respectiva coloana NU este
    AUTO_INCREMENT in baza asta -- caz in care randurile copil s-ar lega toate de zero,
    tacut. Se opreste in loc.
    """
    cheie = int(cursor.lastrowid or 0)
    if cheie == 0:
        raise RuntimeError(
            f"{tabela}: INSERT-ul nu a întors o cheie (lastrowid = 0). "
            "Coloana de cheie primară nu pare a fi AUTO_INCREMENT în această bază."
        )
    return cheie


# ---------------------------------------------------------------------------
# Rute
# ---------------------------------------------------------------------------
@forexe_bp.route("/api/forexe/extrase/ultima", methods=["GET"])
@require_session
def get_ultima_data_extras():
    """
    Data celui mai recent extras deja importat, sau null cand nu exista niciunul.

    Clientul o trimite robotului ca punct de oprire al paginarii prin cutia de mesaje
    FOREXE. Nu e un filtru de corectitudine -- importul respinge oricum ce a mai vazut --
    ci unul de viteza: fara ea, fiecare apasare ar descarca toata cutia.
    """
    db_name = g.session.db_name
    conn = None
    try:
        conn = get_kbot_connection(db_name)
        cursor = conn.cursor()
        cursor.execute(_ULTIMA_SQL)
        rand = cursor.fetchone()
        ultima = rand[0] if rand is not None else None
        return _json_utf8(
            {"data_extras": ultima.date().isoformat() if ultima is not None else None},
            200,
        )
    except Exception as e:
        logger.error(f"[forexe.extrase.ultima] {e}", exc_info=True)
        return _json_utf8({"error": str(e)}, 500)
    finally:
        if conn is not None:
            conn.close()


@forexe_bp.route("/api/forexe/extrase/import", methods=["POST"])
@require_session
@timing.timed("extrase-import")
def import_extrase():
    """
    Importa extrasele descarcate de robot. Portul lui FX_Extrase_Prelucrare.

    Totul intr-o SINGURA tranzactie, ca in original: un extras pe jumatate scris (fisier
    fara antete, antet fara randuri) nu e o stare din care sa se poata continua, iar
    reluarea e ieftina -- deduplicarea face a doua rulare aproape goala.
    """
    data = request.json or {}
    extrase = data.get("extrase")
    if extrase is None:
        return _json_utf8({"error": "Parametru lipsă: extrase"}, 400)
    if not isinstance(extrase, list):
        return _json_utf8({"error": "«extrase» trebuie să fie listă"}, 400)

    db_name = g.session.db_name
    timing.note(dc=db_name, user=g.session.username, octeti=request.content_length)
    timing.count(extrase=len(extrase))
    conn = None
    try:
        with timing.stage("conectare MariaDB"):
            conn = get_kbot_connection(db_name)
            cursor = timing.watch(conn.cursor())
            conn.start_transaction()

        with timing.stage("nomenclatoare"):
            nom = _Nomenclatoare(cursor)
        avertismente = list(nom.avertismente)

        # Ce stim deja, pe cheia naturala (vezi antetul: NU pe sirul HASH).
        with timing.stage("citeste ce e deja importat"):
            cursor.execute(_F_EXISTENTE_SQL)
            fisiere_vazute = {
                (str(nume), data_ex.date() if data_ex is not None else None)
                for (nume, data_ex) in cursor.fetchall()
            }
            cursor.execute(_E_EXISTENTE_SQL)
            # DataDoc e TEXT in tabela; `str(...) if not None` o pastreaza asa si de
            # partea asta, ca perechea (citit, scris) sa se compare intre ele si nu o
            # data cu un sir.
            operatiuni_vazute = {
                (None if dd is None else str(dd), nd, cui, _f(sd), _f(sc))
                for (dd, nd, cui, sd, sc) in cursor.fetchall()
            }

        importate = 0
        sarite = 0
        randuri_scrise = 0

        for extras in extrase:
            xml_text = extras.get("XmlContent")
            if not xml_text:
                sarite += 1
                continue

            pdf_fisier = extras.get("PdfFisier") or ""
            data_fisier = extras.get("DataFisier") or ""
            cale_fisier = extras.get("CaleLocala") or ""

            try:
                # Etapa se deschide o data pe extras si se aduna intr-un singur rand
                # de raport, cu `n` = cate extrase si maximul lor. Un XML de cateva
                # megaocteti se vede aici, si NU in coloana «sql».
                with timing.stage("parsare XML"):
                    radacina = ElementTree.fromstring(xml_text)
            except ElementTree.ParseError as err:
                # Access ridica «XML invalid (Fisier=...)» si oprea tot. Aici se
                # numeste fisierul si se merge mai departe: un extras stricat nu are
                # voie sa tina pe loc restul descarcarii.
                avertismente.append(f"XML invalid în «{pdf_fisier}»: {err}")
                sarite += 1
                continue

            data_extras = parse_data_extras(radacina.get("Data_extras"))
            cheie_fisier = (pdf_fisier, data_extras.date() if data_extras else None)
            if cheie_fisier in fisiere_vazute:
                sarite += 1
                continue
            fisiere_vazute.add(cheie_fisier)

            with timing.stage("scrie FX_Extrase_F"):
                cursor.execute(_F_INSERT_SQL, (
                    pdf_fisier,
                    cale_fisier,
                    data_extras,
                    xml_text,
                    hash_fisier(pdf_fisier, data_fisier),
                ))
                idexf = _id_nou(cursor, "FX_Extrase_F")
            importate += 1

            with timing.stage("scrie antete + operatiuni"):
                for cont_ext in radacina.findall("cont_ext"):
                    idexh, id_unitate, id_clsf = _scrie_antet(
                        cursor, nom, cont_ext, idexf, avertismente)
                    if idexh is None:
                        continue
                    for cont_misc in cont_ext.findall("cont_misc"):
                        if _scrie_operatiune(cursor, nom, cont_misc, idexh, id_unitate,
                                             id_clsf, operatiuni_vazute):
                            randuri_scrise += 1

        with timing.stage("commit"):
            conn.commit()
        logger.info(
            f"[forexe.extrase.import] {db_name}: primite={len(extrase)} "
            f"importate={importate} sarite={sarite} randuri={randuri_scrise}"
        )
        return _json_utf8({
            "primite": len(extrase),
            "importate": importate,
            "sarite": sarite,
            "randuri": randuri_scrise,
            "avertismente": avertismente,
        }, 200)

    except Exception as e:
        if conn is not None:
            conn.rollback()
        logger.error(f"[forexe.extrase.import] {e}", exc_info=True)
        return _json_utf8({"error": str(e)}, 500)
    finally:
        if conn is not None:
            conn.close()


def _f(v) -> float:
    """DOUBLE (sau None) -> float, ca sumele sa se compare intre ele."""
    return float(v) if v is not None else 0.0


def _data_doc_text(d):
    """
    Data documentului in forma in care traieste coloana: TEXT `dd.MM.yyyy`, sau None.

    Nu e o alegere de stil. `FX_Extrase.DataDoc` e `varchar`, iar randurile scrise de
    Access poarta formatul scurt al masinii romanesti. Orice alt format ar rupe atat
    compararea cu ele (deduplicarea), cat si afisarea din vederea Plati.
    """
    return None if d is None else d.strftime("%d.%m.%Y")


def _scrie_antet(cursor, nom: _Nomenclatoare, cont_ext, idexf: int, avertismente):
    """
    Un `cont_ext` -> un rand de FX_Extrase_H. Port de Extrase_H_Add.

    Intoarce (IDEXH, IdUnitate, IdClsf). IDEXH None inseamna «nu am ce scrie»: nodul nu
    are `cont`, si atunci Access sarea peste tot antetul (GoTo Iesire) -- randurile lui
    de bani nu au unde sa se lege.
    """
    nod_cont = cont_ext.find("cont")
    if nod_cont is None:
        return None, None, None

    cont = _text(nod_cont)
    cod_iban = nod_cont.get("Cod_IBAN")

    # Sursa = primele 3 caractere ale contului, traduse prin DefaSSS. Cand tabela
    # lipseste cu totul, avertismentul s-a pus o singura data, la incarcarea
    # nomenclatoarelor -- nu se repeta pentru fiecare cont.
    sursa = nom.sursa_pentru_cont(cont)
    if sursa is None and nom.are_surse:
        avertismente.append(f"Sursa «{cont[:3]}» nu a fost găsită pentru contul {cont}.")

    # ClsfSal din cont, dar numai cand exista IBAN -- conditia din Access.
    clsf_sal = ""
    if cod_iban:
        if len(cont) >= 4 and cont[3] == "6":
            clsf_sal = cont[3:15]
        elif len(cont) >= 4 and cont[3] == "3":
            clsf_sal = cont[3:9]

    id_unitate = nom.unitate_pentru_sursa(sursa)
    id_clsf = None
    id_clsf_v = None
    if id_unitate is not None and clsf_sal:
        id_clsf = nom.clsf_pentru(id_unitate, clsf_sal)
        if id_clsf is None:
            # «cauta si in clasifi venituri» -- ramura din Extrase_H_Add, netinsa.
            id_clsf_v = nom.clsf_venituri_pentru(clsf_sal)

    def suma(grup: str, camp: str) -> float:
        return parse_loose_number(_text_copil(cont_ext.find(grup), camp))

    cursor.execute(_H_INSERT_SQL, (
        idexf, id_clsf, id_unitate, cod_iban, cont,
        suma("Sold_precedent", "Sumad"), suma("Sold_precedent", "Sumac"),
        suma("Rulaj_zi", "Sumad"), suma("Rulaj_zi", "Sumac"),
        suma("Total_sume", "Sumad"), suma("Total_sume", "Sumac"),
        suma("Sold_final", "Sumad"), suma("Sold_final", "Sumac"),
        id_clsf_v,
    ))
    return _id_nou(cursor, "FX_Extrase_H"), id_unitate, id_clsf


def _scrie_operatiune(cursor, nom: _Nomenclatoare, cont_misc, idexh: int,
                      id_unitate, id_clsf, vazute) -> bool:
    """
    Un `cont_misc` -> un rand de FX_Extrase. Port de Extrase_Add.
    True daca s-a scris, False daca era deja acolo.
    """
    d = parse_cont_misc(cont_misc)

    data_banca = parse_data_yyyymmdd(d.get("databan"))
    # DataDoc pleaca spre baza ca TEXT `dd.MM.yyyy` -- vezi antetul fisierului.
    data_doc = _data_doc_text(parse_data_yyyymmdd(d.get("datadoc")))
    nr_doc = null_if_empty(d.get("nrdoc"))

    # Referinta = ce sta inaintea primei liniute din explicatii.
    explicatii = d.get("explicatii")
    referinta = extract_obs_value(explicatii, "", "-")
    referinta_dest = null_if_empty(d.get("nrrefdest"))

    platitor_nume = null_if_empty(d.get("numepb"))
    platitor_cui = null_if_empty(d.get("platitor"))
    platitor_iban = null_if_empty(d.get("ibanbfpl"))

    suma_debit = parse_loose_number(d.get("sumad"))
    suma_credit = parse_loose_number(d.get("sumac"))

    cod_contract = null_if_empty(d.get("codcontract"))    # CodAngajament
    rand_contract = null_if_empty(d.get("randcontract"))  # CodIndicator
    cod_program = null_if_empty(d.get("codprogram"))

    # CodAI: direct cand extrasul poarta si randul, altfel cautat prin clasificatie.
    # «ERRRRRRRRRR» e santinela FOREXE pentru «fara contract» -- vezi ord_edit.py.
    cod_ai = None
    if cod_contract is not None and rand_contract is not None:
        cod_ai = f"{cod_contract}-{rand_contract}"
    elif cod_contract is not None and id_clsf is not None and cod_contract != "ERRRRRRRRRR":
        cod_ai = nom.indicator_pentru(cod_contract, id_clsf)

    cheie = (data_doc, nr_doc, platitor_cui, suma_debit, suma_credit)
    if cheie in vazute:
        return False
    vazute.add(cheie)

    cod_partener = None
    if id_unitate is not None:
        cod_partener = nom.partener_pentru(id_unitate, platitor_cui)

    cursor.execute(_E_INSERT_SQL, (
        idexh, cod_ai, data_banca, data_doc, nr_doc, referinta, referinta_dest,
        platitor_nume, platitor_cui, platitor_iban, suma_debit, suma_credit,
        null_if_empty(explicatii), cod_contract, rand_contract, cod_program,
        hash_operatiune(data_doc, nr_doc, platitor_cui, suma_debit, suma_credit),
        cod_partener, id_unitate,
    ))
    return True
