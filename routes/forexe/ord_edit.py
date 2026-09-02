# routes/forexe/ord_edit.py
"""
Rutele de SCRIERE ale ordonantarii (felia 0049) — portul lui `frmFX_ORD` + `mdl_FX_ORD`.

Rute (toate `@require_session`, baza vine din sesiune — o baza MariaDB = o unitate, deci
NU exista parametru `db_name` / `id_unitate`):

    POST   /api/forexe/ord/genereaza                     -> graful PROPUS, nimic scris
    GET    /api/forexe/ord/draft/<idordp>                -> graful unei ordonantari existente
    GET    /api/forexe/ord/zile                          -> zilele candidate pentru modul in lot
    POST   /api/forexe/ord/save                          -> scrie tot graful, o tranzactie
    DELETE /api/forexe/ord/<idordp>                      -> sterge ordonantarea (cascade)
    GET    /api/forexe/ord/att/<idordattp>/imagine       -> octetii atasamentului
    PUT    /api/forexe/ord/att/<idordattp>/imagine       -> inlocuieste-sau-insereaza octetii
    DELETE /api/forexe/ord/att/<idordattp>/imagine       -> sterge octetii

`routes/forexe/ord.py` (citirea vederii 0033) NU se modifica, iar `routes/ord/*` (clientul
Access/VBA legacy, pe X-Api-Key, port 5008) nu se atinge deloc — cele doua sisteme se
suprapun cat timp Access mai ruleaza.

=========================================================================================
CE INLOCUIESTE
=========================================================================================
Lantul VBA de salvare (`FX_Curatare_Staging_ORD` -> `FX_Adauga_Ord` -> proba locala ->
`FX_Confirma_ORD` -> `FX_Confirma_Local_ORD` -> commit real -> `FX_ActualizeazaAccessIds_ORD`)
NU se porteaza. In locul lui: UN singur POST, O singura tranzactie pe server, si cheile
reale intoarse clientului. Cele sase tabele `tmpFX_ORD*` din Access nu au succesor — rolul
lor il joaca obiecte in memoria clientului.

Id-urile ACCESS nu se scriu niciodata (`FX_ORD.IDORD`, `FX_ORD_PART.IDORDPART`,
`FX_ORD_TBL.IDORDTBL`, `FX_ORD_DOC.IDORDDOC`, `FX_ORD_ATT.IDORDATT`,
`FX_ORD_TBL_REC.IDORDREC`): toate sunt NULL-abile in MariaDB si Access se retrage.
`Incarca_MaxPKs` din VBA nu se porteaza — cheile vin din `AUTO_INCREMENT` la salvare.

=========================================================================================
CELE SASE CAPCANE ALE FAMILIEI FX_ORD (citite din DDL, nu deduse din nume)
=========================================================================================
1. TOATE legaturile merg pe cheia «...P» (`IDORDP`, `IDORDPARTP`, `IDORDTBLP`, `IDORDATTP`,
   `IDORDDOCP`). Omonimele fara «P» sunt id-uri Access pastrate. Un port literal al
   join-ului Access `FX_ORD_TBL.IDORDPART = FX_ORD_PART.IDORDPART` leaga cheia GRESITA.
2. `FX_ORD_TBL_REC` se leaga deja pe `IDORDTBLP` (FK real, ON DELETE CASCADE). Legatura
   `IDORDTBL` a existat numai in Access.
3. `FX_ORD.IDORD` si `FX_ORD.CUAL` sunt `varchar(255)`, in timp ce `FX_DDF.CUAL` e
   `int(11)` — CUAL-ul copiat din DDF se converteste la text.
4. `FX_ORD_TBL` are SASE chei straine, doua dintre ele in stare sa opreasca tranzactia pe
   date proaste: `IdClsf` -> `Clasificatii.IDClsf` (are `DEFAULT 0` SI cheie straina, deci
   o linie ajunsa la salvare cu `IdClsf = 0` cade pe constrangere) si `CodAI` ->
   `FX_Indicatori.CodAI`. Plus `IdPartener` -> `Parteneri` si `IdUnitate` (NOT NULL) ->
   `Unitati`. De aceea se valideaza pe NUME inainte de INSERT (vezi `valideaza_graf`).
5. INVERSIUNEA `IdClsf`: pe `FX_ORD_TBL`, MariaDB `IdClsf` e cheia straina catre
   `Clasificatii` (id-ul global) iar `IdClsfAcc` tine id-ul Access — INVERS fata de
   `FX_Indicatori`, unde `IdClsf` tine id-ul Access. In Access, cele doua se numeau
   `IdClsfPY` (global) si `IdClsf` (Access).
6. `ClasificatiiG` si `ParteneriG` NU EXISTA in MariaDB; `FX_ORD_ATT` nu are coloana `Nume`
   (numele fisierului sta in `FX_ORD_ATT_IMG.NumeFisier`, felia asta). Si nu exista
   `GROUP BY IdUnitate` pe tabelele `FX_`; `IdUnitate` e relicva acolo — dar
   `FX_ORD_TBL.IdUnitate` e NOT NULL cu cheie straina, deci TOT TREBUIE SCRIS. Se ia din
   `FX_Indicatori.IdUnitate`, exact ca in `qFX_ORD_BASE` (`I.IdUnitate`).

=========================================================================================
ID-URILE TEMPORARE
=========================================================================================
Randurile NOI poarta un `temp_id` NEGATIV, atribuit de client, cu inteles doar in interiorul
unei singure sarcini de salvare. Randurile EXISTENTE poarta cheia «...P» reala (pozitiva).
Copiii se leaga de parinti prin `..._temp_id` cand parintele e nou si prin cheia reala cand
nu e. Raspunsul lui `/save` intoarce harta `temp_id -> cheie reala` pe fiecare tabela;
clientul o foloseste ca sa incarce octetii atasamentelor (faza a doua, vezi mai jos).

=========================================================================================
DOUA FAZE, DIN NECESITATE, LA ATASAMENTE
=========================================================================================
Un `IDORDATTP` trebuie sa existe inainte ca octetii sa poata atarna de el. Deci: clientul
salveaza intai graful, citeste harta `att`, apoi incarca fiecare imagine noua sau schimbata.
Daca o incarcare cade DUPA o salvare reusita, ordonantarea RAMANE salvata si imaginea
lipseste — asta se spune pe sleau in romana si se ofera reluarea. NU se deruleaza inapoi:
un document pe jumatate derulat e mai rau decat unul caruia ii lipseste o poza.

=========================================================================================
LOCALIZARE
=========================================================================================
Fara `Format(..., "0.00")` nicaieri: sumele calatoresc ca numere JSON si se formateaza
doar la marginea interfetei, cu `ro-RO`. Toate raspunsurile folosesc `ensure_ascii=False`,
iar mesajele de eroare sunt in romana cu diacritice literale.
"""
import hashlib
import json
import logging
from datetime import date, datetime

from flask import request, g, current_app

from routes.auth.guard import require_session
from utils.database import get_kbot_connection, COMMON_DB

from . import forexe_bp

logger = logging.getLogger(__name__)


# =========================================================================================
# Constante portate din Access
# =========================================================================================

# CUI-ul care marcheaza «platitorul sunt eu, nu un tert» in `qFX_ORD_BASE`
# (`[FX_Extrase].[platitor_cui]='8609468'`). E o constanta LITERALA in interogarea Access,
# nu un parametru, si se porteaza ca atare. Cand se potriveste — sau cand IBAN-ul
# platitorului lipseste — beneficiarul devine numele UNITATII.
CUI_UNITATE_IN_EXTRAS = "8609468"

# Limita de parteneri per ordonantare, scrisa ca `TOP 25` CHIAR IN `qFX_ORD_BASE` si
# repetata in `Populeaza_PartSel`. Care 25 se aleg e regula de business (ordinea dupa
# numele platitorului), nu un accident, deci si `ORDER BY`-ul se porteaza.
LIMITA_PARTENERI = 25

# `Incarca_Explicatii_Incasari` filtreaza pe `FX_Extrase.CodContract='ERRRRRRRRRR'`.
# Niciun contract nu se numeste asa, deci dictionarul iese INTOTDEAUNA GOL, iar incasarile
# primesc «LIPSA EXPLICATIE» / «INCASARE». Arata a sentinela de depanare uitata in cod.
# Se porteaza FIDEL (aceeasi decizie ca D18 din felia 0048-01: defectul se porteaza si se
# consemneaza, nu se repara pe tacute) — dar sta aici, intr-o constanta, ca sa fie vizibil
# si reparabil cu o singura linie daca operatorul confirma ca e o greseala.
EXPLICATII_FILTRU_CODCONTRACT = "ERRRRRRRRRR"

# Plafonul practic al unei imagini de atasament. Coloana e LONGBLOB (nu se opreste aici),
# dar peste atat raspundem 413 cu un mesaj care SPUNE limita. nginx taie la 20 MB.
MAX_IMAGINE_BYTES = 16 * 1024 * 1024

# Antetele de integritate / concurenta, identice cu cele din routes/forexe/pdf.py.
H_SHA = "X-Sha256"
H_SHA_PREC = "X-Sha-Precedent"
NO_ROW = "-"

# Tipurile de imagine acceptate, dupa filtrul `SelectFile` din `frmFX_ORD_PRTSCR_S`:
# "*.jpg; *.jpeg; *.png; *.bmp; *.gif; *.tif; *.tiff".
SEMNATURI_IMAGINE = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"BM", "image/bmp"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"II*\x00", "image/tiff"),
    (b"MM\x00*", "image/tiff"),
)


class DateInvalide(Exception):
    """Sarcina utila e refuzata inainte de orice scriere. Mesajul e deja in romana."""


# =========================================================================================
# Utilitare de raspuns
# =========================================================================================
def _json_utf8(payload, status):
    """Raspuns JSON cu diacritice LITERALE (ensure_ascii=False)."""
    body = json.dumps(payload, ensure_ascii=False, default=_serializeaza)
    return current_app.response_class(body, status=status, mimetype="application/json")


def _serializeaza(v):
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return str(v)


def _iso_zi(v):
    """DateTime/Date -> 'YYYY-MM-DD', sau None."""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    return str(v)


def _iso_dt(v):
    """DateTime -> ISO cu ora, sau None."""
    if v is None:
        return None
    return v.isoformat() if hasattr(v, "isoformat") else str(v)


def _num(v):
    """Coloana de bani -> float; None devine 0.0 (server-side), ca grila sa arate «0,00»."""
    return float(v) if v is not None else 0.0


def _int_or_none(v):
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _txt(v):
    """Valoare -> text curatat, sau '' (niciodata None in campurile de text ale raspunsului)."""
    return "" if v is None else str(v)


def _sha256(data: bytes) -> str:
    """SHA-256 hex MINUSCULE peste octetii dati — acelasi format ca pe client."""
    return hashlib.sha256(data).hexdigest()


def _zi_ceruta(brut, nume_camp: str):
    """'YYYY-MM-DD' (sau ISO cu ora) -> date; None cand campul lipseste.

    Ridica `DateInvalide` cu mesaj romanesc pentru orice altceva — o data neinteleasa NU se
    inlocuieste tacut cu ziua de azi.
    """
    if brut is None or str(brut).strip() == "":
        return None
    if isinstance(brut, datetime):
        return brut.date()
    if isinstance(brut, date):
        return brut
    text = str(brut).strip()
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        pass
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date()
    except ValueError:
        raise DateInvalide(
            f"«{nume_camp}» nu este o dată validă: «{text}». Format așteptat: AAAA-LL-ZZ.")


# =========================================================================================
# GENERAREA — portul lui `Genereaza_ORD`
# =========================================================================================
#
# Dictionarele. VBA le incarca O SINGURA DATA inainte de bucla si le citeste cu
# `Exists`/index; aici sunt cinci SELECT-uri materializate in dicts, exact la fel.

# `Incarca_DicBanci` citeste tabela `BIC` (Cod -> Banca). Ea NU sta pe baza unitatii — nu e in
# MariaDB_Schema/000_DEMO.sql si nu e in FX_System_Export/TABLES — ci pe baza COMUNA,
# `AVACONT_COMUN` (spus de operator, 02.09.2026): nomenclatorul bancilor e acelasi pentru toate
# unitatile, deci o singura copie, nu una per baza. Se citeste cu numele bazei in fata, pe
# ACEEASI conexiune — tiparul pe care il foloseste deja `schema_sync/schema_common.py` pentru
# `AVACONT_COMUN`.`CAI`.
#
# NU se mai PROBEAZA intai in `information_schema` (03.09.2026). Proba spunea «tabela lipseste»
# desi tabela exista: `information_schema.TABLES` arata doar obiectele pe care contul are vreun
# drept, deci raspundea «0» de fiecare data cand dreptul pe `AVACONT_COMUN`.`BIC` nu era dat
# contului cu care merge serverul. O proba care poate raspunde gresit tocmai in cazul bun nu
# apara nimic: se incearca de-a dreptul SELECT-ul, iar esecul LUI e singurul raspuns de
# incredere. Tabela existenta dar goala da un dictionar gol si NICIUN avertisment — nu e o
# eroare, doar un nomenclator nepopulat.
def incarca_dic_banci(cursor, db_name: str, avertismente: list) -> dict:
    """Cod BIC (4 caractere) -> numele bancii, din `AVACONT_COMUN`.`BIC`."""
    try:
        cursor.execute(f"SELECT Cod, Banca FROM `{COMMON_DB}`.`BIC`")
        return {str(r["Cod"]): _txt(r["Banca"]) for r in cursor.fetchall() if r["Cod"] is not None}
    except Exception as e:
        # Tabela lipseste sau contul nu are drept pe ea: se SPUNE, nu se cade — generarea nu
        # depinde de numele bancii, e camp informativ.
        logger.warning("[forexe.ord_edit] %s: citirea lui `%s`.BIC a esuat (%s); se continua fara",
                       db_name, COMMON_DB, e)
        avertismente.append(
            f"Tabela «BIC» din baza comună «{COMMON_DB}» nu a putut fi citită, deci numele "
            "băncilor nu s-a putut completa automat. Completați-l manual, dacă e nevoie.")
        return {}


# `Incarca_DicPartInd`: CodIndicator -> (CodPartener, IdPartener), din sectiunea A a
# reviziilor DDF ale angajamentului. VBA pastreaza PRIMA aparitie (`If Not Exists`), deci
# ordinea conteaza: se ordoneaza dupa cheia primara ca raspunsul sa fie stabil.
_SQL_DIC_PART_IND = (
    "SELECT SA.CodIndicator, SA.CodPartener, SA.IdPartener "
    "  FROM FX_DDF_REV_SA SA "
    " WHERE SA.IDDF IN (SELECT IDDF FROM FX_DDF WHERE CodAngajament = %s) "
    " ORDER BY SA.IdSecA"
)


def incarca_dic_part_ind(cursor, cod: str) -> dict:
    cursor.execute(_SQL_DIC_PART_IND, (cod,))
    dic = {}
    for r in cursor.fetchall():
        cheie = _txt(r["CodIndicator"])
        if cheie not in dic:
            dic[cheie] = (_txt(r["CodPartener"]), _int_or_none(r["IdPartener"]))
    return dic


# `qFX_ORD_REC_ANT`: CodIndicator -> SUM(FX_Receptii.DIF) pana la DT inclusiv.
#
# `H.Sters = False` din Access: acolo un Yes/No nu poate fi Null, deci echivalentul fidel
# in MariaDB — unde coloana e nullabila si NULL inseamna «migrat fara valoare», nu «sters» —
# este `COALESCE(H.Sters, 0) = 0`. Un `H.Sters = 0` sec ar arunca tacut toate randurile
# vechi migrate cu NULL.
_SQL_REC_ANT = (
    "SELECT R.CodIndicator, SUM(R.DIF) AS Receptii "
    "  FROM FX_Receptii R "
    "  JOIN FX_Receptii_H H ON R.IDRH = H.IDRH "
    " WHERE H.CodAngajament = %s "
    "   AND COALESCE(H.Sters, 0) = 0 "
    "   AND DATE(R.Data) <= %s "
    " GROUP BY R.CodIndicator"
)


def incarca_dic_receptii(cursor, cod: str, dt: date) -> dict:
    cursor.execute(_SQL_REC_ANT, (cod, dt))
    return {_txt(r["CodIndicator"]): _num(r["Receptii"]) for r in cursor.fetchall()}


# `qFX_ORD_PLATI_ANT`: CodIndicator -> SUM(FX_ORD_TBL.Valoare) din ordonantarile cu
# DataORD <= DT. Legatura merge pe `IDORDP` (capcana 1) — Access lega pe `IDORD`.
_SQL_PLATI_ANT = (
    "SELECT T.CodIndicator, SUM(T.Valoare) AS PlatiAnterioare "
    "  FROM FX_ORD_TBL T "
    "  JOIN FX_ORD O ON T.IDORDP = O.IDORDP "
    " WHERE O.CodAngajament = %s "
    "   AND DATE(O.DataORD) <= %s "
    "   AND (O.Incarcat = 1 OR O.Preluat = 1) "
    " GROUP BY T.CodIndicator"
)


def incarca_dic_plati(cursor, cod: str, dt: date) -> dict:
    cursor.execute(_SQL_PLATI_ANT, (cod, dt))
    return {_txt(r["CodIndicator"]): _num(r["PlatiAnterioare"]) for r in cursor.fetchall()}


# `Incarca_Explicatii_Incasari`: NrDoc -> Explicatii. Vezi
# EXPLICATII_FILTRU_CODCONTRACT — al doilea predicat face rezultatul mereu gol.
_SQL_EXPLICATII = (
    "SELECT E.NrDoc, E.Explicatii "
    "  FROM FX_Extrase E "
    " WHERE E.NrDoc IN (SELECT NrDoc FROM FX_Extrase "
    "                    WHERE CodContract = %s AND LEFT(NrDoc, 5) = '00000') "
    "   AND E.CodContract = %s"
)


def incarca_explicatii_incasari(cursor, cod: str) -> dict:
    cursor.execute(_SQL_EXPLICATII, (cod, EXPLICATII_FILTRU_CODCONTRACT))
    return {_txt(r["NrDoc"]): _txt(r["Explicatii"]) for r in cursor.fetchall()}


# `Populeaza_PartSel` — cei (cel mult) 25 de parteneri ai zilei, in ordinea numelui.
# Access punea rezultatul intr-o tabela temporara; aici e o lista in memorie, folosita ca
# `IN (...)` de interogarea de baza.
#
# `platitor_cui & '-' & platitor_iban`: in Access, `&` cu Null da partea nenula; in MariaDB,
# `CONCAT` cu NULL da NULL. `COALESCE(..., '')` reproduce comportamentul Access. Aceeasi
# forma se foloseste si in interogarea de baza, ca cele doua chei sa se potriveasca.
_KMATCH = "CONCAT(COALESCE(E.platitor_cui, ''), '-', COALESCE(E.platitor_iban, ''))"

_SQL_PARTSEL = (
    f"SELECT {_KMATCH} AS KMatch "
    "  FROM FX_Plati P "
    "  JOIN FX_Extrase E ON P.Referinta_TREZOR = E.Referinta "
    " WHERE NOT EXISTS (SELECT 1 FROM FX_ORD_TBL_REC R WHERE R.IdPlataFX = P.IdPlataFX) "
    "   AND P.CodAngajament = %s "
    "   AND DATE(P.Data_plata) = %s "
    f" GROUP BY {_KMATCH} "
    "  ORDER BY MIN(E.platitor_nume) "
    f" LIMIT {LIMITA_PARTENERI}"
)


def populeaza_part_sel(cursor, cod: str, dt: date) -> list:
    cursor.execute(_SQL_PARTSEL, (cod, dt))
    return [r["KMatch"] for r in cursor.fetchall()]


# `qFX_ORD_BASE`, tradusa pe MariaDB. Ce s-a schimbat si de ce:
#
#  * `ClasificatiiG` nu exista (capcana 6). Clasificatia se ia prin SUBINTEROGARI SCALARE
#    cu LIMIT 1 pe `Clasificatii` (`IdClsfAcc` = id-ul Access din `FX_Plati.IdClsf`,
#    `IdUnitate` = al indicatorului) — nomenclatorul are duplicate reale pe
#    (IdClsfAcc, IdUnitate), iar un JOIN ar multiplica randurile (MAPARE_NOMENCLATOARE §3.2).
#    Acesta e tiparul folosit deja de routes/forexe/prelucrare_pasi.py.
#  * `CodSSI` nu e coloana pe MariaDB: se CALCULEAZA, `CONCAT(C.SS, C.ClsfSal)` — exact ce
#    face `read_indicatori` din prelucrare_pasi.py, care ruleaza azi.
#  * `IdClsfPY` din Access devine `Clasificatii.IDClsf` (capcana 5).
#  * ABATERE DELIBERATA, consemnata: Access folosea INNER JOIN pe `ClasificatiiG`, deci o
#    plata a carei clasificatie lipseste CADEA din recordset in tacere. Aici randul RAMANE,
#    cu clasificatia NULL, si se emite un avertisment; validarea salvarii il opreste apoi
#    pe nume (§7.4). Aceeasi alegere ca D19 din felia 0048-02 — regula casei: fara no-op-uri
#    tacute.
#  * `globNumeUnit()` -> numele unitatii din contextul sesiunii.
#  * `TOP 1` pe FX_DDF -> `ORDER BY IDDF, CUAL LIMIT 1`: PK-ul lui FX_DDF e COMPUS
#    (IDDF, CUAL), iar `TOP 1` fara `ORDER BY` e nedeterminist in Access. Ordinea stabila e
#    aceeasi ca in routes/forexe/pdf.py.
_SQL_BASE = (
    "SELECT "
    "  (E.platitor_iban IS NULL OR E.platitor_cui = %(cui_unit)s) AS NuAsta, "
    "  D.IDDF, D.CUAL, D.Comp, D.PartAng, D.ObiectDDF, D.NumePartener, "
    "  CASE WHEN (E.platitor_iban IS NULL OR E.platitor_cui = %(cui_unit)s) "
    "       THEN %(nume_unit)s ELSE E.platitor_nume END AS Beneficiar, "
    "  P.Suma AS Valoare, "
    "  DATE(P.Data_plata) AS Data, "
    "  P.IdPlataFX, P.IdClsf AS IdClsfAcc, P.CodAngajament, P.CodIndicator, "
    "  COALESCE(E.platitor_iban, H.CodIBAN) AS Beneficiar_IBAN, "
    "  E.platitor_cui AS Beneficiar_CUI, "
    "  SUBSTRING(E.Explicatii, 15) AS Descriere, "
    "  E.NrDoc, "
    "  H.CodIBAN AS IBAN_UNIT, "
    "  (CHAR_LENGTH(COALESCE(E.platitor_cui, '')) >= 13) AS PJ, "
    "  I.IdUnitate, "
    "  (SELECT C.IDClsf   FROM Clasificatii C "
    "    WHERE C.IdClsfAcc = P.IdClsf AND C.IdUnitate = I.IdUnitate LIMIT 1) AS IdClsf, "
    "  (SELECT C.Clsf     FROM Clasificatii C "
    "    WHERE C.IdClsfAcc = P.IdClsf AND C.IdUnitate = I.IdUnitate LIMIT 1) AS Clsf, "
    "  (SELECT C.Denumire FROM Clasificatii C "
    "    WHERE C.IdClsfAcc = P.IdClsf AND C.IdUnitate = I.IdUnitate LIMIT 1) AS Denumire, "
    "  (SELECT CONCAT(C.SS, C.ClsfSal) FROM Clasificatii C "
    "    WHERE C.IdClsfAcc = P.IdClsf AND C.IdUnitate = I.IdUnitate LIMIT 1) AS CodSSI "
    "FROM FX_Plati P "
    "JOIN FX_Extrase   E ON P.Referinta_TREZOR = E.Referinta "
    "JOIN FX_Extrase_H H ON H.IDEXH = E.IDFXH "
    "JOIN FX_Indicatori I ON I.CodAI = P.CodAI "
    "JOIN (SELECT IDDF, CUAL, Comp, PartAng, ObiectDDF, NumePartener "
    "        FROM FX_DDF WHERE CodAngajament = %(cod)s "
    "       ORDER BY IDDF, CUAL LIMIT 1) D "
    "  ON 1 = 1 "
    "WHERE NOT EXISTS (SELECT 1 FROM FX_ORD_TBL_REC R WHERE R.IdPlataFX = P.IdPlataFX) "
    "  AND P.CodAngajament = %(cod)s "
    "  AND DATE(P.Data_plata) = %(dt)s "
    "{filtru_plata}"
    "  AND {kmatch} IN ({locuri}) "
    "ORDER BY (E.platitor_iban IS NULL OR E.platitor_cui = %(cui_unit)s) DESC, "
    "         CASE WHEN (E.platitor_iban IS NULL OR E.platitor_cui = %(cui_unit)s) "
    "              THEN %(nume_unit)s ELSE E.platitor_nume END, "
    "         DATE(P.Data_plata), P.IdPlataFX"
)


def citeste_baza(cursor, cod: str, dt: date, nume_unitate: str,
                 id_plata_fx, kmatch: list) -> list:
    """Randurile-sursa ale ordonantarii: o plata neordonantata = un rand."""
    if not kmatch:
        return []
    locuri = ", ".join(["%(k{})s".format(i) for i in range(len(kmatch))])
    parametri = {
        "cod": cod, "dt": dt,
        "cui_unit": CUI_UNITATE_IN_EXTRAS,
        "nume_unit": nume_unitate,
    }
    for i, k in enumerate(kmatch):
        parametri[f"k{i}"] = k
    filtru = ""
    if id_plata_fx is not None:
        filtru = "  AND P.IdPlataFX = %(id_plata)s "
        parametri["id_plata"] = int(id_plata_fx)
    sql = _SQL_BASE.format(filtru_plata=filtru, kmatch=_KMATCH, locuri=locuri)
    cursor.execute(sql, parametri)
    return cursor.fetchall()


# `Contor_Parteneri_Zi` — CATE ORDONANTARI ar trebui ca sa acopere ziua. VBA porneste de la
# 1 si adauga cate una la fiecare al 25-lea rand (`If Cnt Mod 25 = 0`), deci raspunsul e un
# numar de PAGINI, nu de parteneri: `> 1` inseamna «ziua nu incape intr-o singura
# ordonantare de 25 de parteneri». Cheia de grup e `Platitor_CUI & Platitor_IBAN` (FARA
# liniuta, spre deosebire de `Populeaza_PartSel`) — se porteaza asa cum e scrisa.
_SQL_PARTENERI_ZI = (
    "SELECT COUNT(*) AS Grupuri FROM ( "
    "  SELECT CONCAT(COALESCE(E.platitor_cui, ''), COALESCE(E.platitor_iban, '')) AS G "
    "    FROM FX_Plati P "
    "    JOIN FX_Extrase E ON P.Referinta_TREZOR = E.Referinta "
    "   WHERE P.CodAngajament = %s "
    "     AND DATE(P.Data_plata) = %s "
    "     AND NOT EXISTS (SELECT 1 FROM FX_ORD_TBL_REC R WHERE R.IdPlataFX = P.IdPlataFX) "
    "   GROUP BY CONCAT(COALESCE(E.platitor_cui, ''), COALESCE(E.platitor_iban, '')) "
    ") AS sub"
)


def contor_parteneri_zi(cursor, cod: str, dt: date) -> int:
    """Numarul de ordonantari necesare pentru ziua data (1 = incape intr-una singura)."""
    cursor.execute(_SQL_PARTENERI_ZI, (cod, dt))
    row = cursor.fetchone()
    n = int(row["Grupuri"]) if row and row["Grupuri"] is not None else 0
    return 1 + (n // LIMITA_PARTENERI)


# `Contor_Zile_Luna` — zilele distincte cu plati neordonantate. Access filtra cu
# `Month & '/' & Year LIKE '<sablon>'`, unde `*` insemna «toate»; aici filtrul e explicit
# (`luna` / `an` optionale), fiindca `*` nu e metacaracter in MariaDB si un `LIKE` cu el ar
# fi tacut gresit.
_SQL_ZILE = (
    "SELECT DATE(P.Data_plata) AS DT, COUNT(*) AS Plati "
    "  FROM FX_Plati P "
    "  JOIN FX_Extrase E ON P.Referinta_TREZOR = E.Referinta "
    " WHERE P.CodAngajament = %s "
    "   AND NOT EXISTS (SELECT 1 FROM FX_ORD_TBL_REC R WHERE R.IdPlataFX = P.IdPlataFX) "
    "{filtru} "
    " GROUP BY DATE(P.Data_plata) "
    " ORDER BY DATE(P.Data_plata)"
)


def citeste_zile(cursor, cod: str, luna, an) -> list:
    parametri = [cod]
    filtru = ""
    if an is not None:
        filtru += "   AND YEAR(P.Data_plata) = %s "
        parametri.append(int(an))
    if luna is not None:
        filtru += "   AND MONTH(P.Data_plata) = %s "
        parametri.append(int(luna))
    cursor.execute(_SQL_ZILE.format(filtru=filtru), tuple(parametri))
    return cursor.fetchall()


# -----------------------------------------------------------------------------------------
# Constructorii — portul celor cinci `Adauga_Ord*`. VBA scria in tabele temporare; aici
# construiesc liste in memorie, cu ACELEASI campuri si ACELEASI socoteli.
# -----------------------------------------------------------------------------------------
def construieste_graf(randuri: list, dic_banci: dict, dic_part_ind: dict,
                      dic_receptii: dict, dic_plati: dict, dic_expl: dict,
                      cod: str, dt: date, avertismente: list) -> dict:
    """Graful PROPUS al ordonantarii: antet, beneficiari, linii, legaturi si documente.

    Nimic nu se scrie. Randurile noi poarta `temp_id` negative; cheile reale sunt 0.
    """
    antet = None
    parteneri = []           # `Adauga_Ord_Part`
    linii = []               # `Adauga_Ord_Tbl`
    rec = []                 # `Adauga_Ord_Rec`
    documente = []           # `Adauga_Ord_Doc`

    # `Adauga_Ord_Part` cauta beneficiarul dupa CodFiscal + ContIBAN si il reutilizeaza.
    index_part = {}
    urmator_part = -1
    urmator_linie = -1
    urmator_rec = -1
    urmator_doc = -1

    # Un document justificativ e UNIC la un beneficiar (cerinta operatorului, 02.09.2026).
    # `Adauga_Ord_Doc` scria cate unul pentru FIECARE rand-sursa, deci trei plati cu aceeasi
    # descriere la acelasi beneficiar dadeau trei randuri identice — nedeosebite pe ecran si
    # fara niciun inteles in plus in PDF. Se pastreaza primul si se numara restul.
    doc_vazute = set()
    doc_duplicate = 0

    # `DicPlatiAnt` se ACTUALIZEAZA in bucla (VBA: dupa fiecare linie adauga valoarea la
    # indicatorul ei), deci se lucreaza pe o copie ca dictionarul citit sa nu fie stricat.
    plati_curente = dict(dic_plati)

    for r in randuri:
        # ---- antetul (`Adauga_Ord`): unul singur, la primul rand ------------------------
        if antet is None:
            antet = {
                "idordp": 0,
                # NrORD se aloca IN TRANZACTIA DE SALVARE (§D8), nu aici: doi operatori care
                # salveaza in acelasi timp nu au voie sa primeasca acelasi numar.
                "nr_ord": 0,
                "data_ord": dt.isoformat(),
                "iddf": _int_or_none(r["IDDF"]),
                # capcana 3: FX_ORD.CUAL e varchar, FX_DDF.CUAL e int.
                "cual": _txt(r["CUAL"]),
                "comp": _txt(r["Comp"]),
                "cod_angajament": cod,
                "incarcat": False,
                "preluat": True,
                "obiect_ddf": _txt(r["ObiectDDF"]),
                "part_ang": bool(r["PartAng"]),
                "nume_partener": _txt(r["NumePartener"]),
            }

        # ---- beneficiarul (`Adauga_Ord_Part`) ------------------------------------------
        cui = _txt(r["Beneficiar_CUI"])
        iban = _txt(r["Beneficiar_IBAN"])
        cheie_part = (cui, iban)
        part = index_part.get(cheie_part)
        if part is None:
            contor = len(parteneri) + 1
            banca = ""
            if len(iban) >= 8:
                # `Mid(IBAN, 5, 4)` — codul bancii din IBAN-ul romanesc.
                banca = dic_banci.get(iban[4:8], "")
            part = {
                "temp_id": urmator_part,
                "idordpartp": 0,
                "counter": str(contor),
                "den_bene": _txt(r["Beneficiar"]),
                "cod_fiscal": cui,
                "cont_iban": iban,
                "banca": banca,
            }
            urmator_part -= 1
            parteneri.append(part)
            index_part[cheie_part] = part

        # ---- linia (`Adauga_Ord_Tbl`) --------------------------------------------------
        cod_indicator = _txt(r["CodIndicator"])
        # VBA: `sCodAi = CodAngajament & "-" & CodIndicator` — cheia lui FX_Indicatori.
        cod_ai = f"{_txt(r['CodAngajament'])}-{cod_indicator}"
        valoare = _num(r["Valoare"])
        receptii = dic_receptii.get(cod_indicator, 0.0)
        plati_ant = plati_curente.get(cod_indicator, 0.0)

        if r["IdClsf"] is None:
            avertismente.append(
                f"Plata {r['IdPlataFX']} ({cod_indicator}) nu are clasificație în "
                f"nomenclator (id Access {r['IdClsfAcc']}, unitatea {r['IdUnitate']}); "
                f"linia rămâne, dar ordonanțarea nu se poate salva până nu e completată.")
        if r["IdUnitate"] is None:
            avertismente.append(
                f"Indicatorul {cod_indicator} nu are unitate ({cod_ai}); linia rămâne, "
                f"dar ordonanțarea nu se poate salva până nu e completată.")

        # Explicatia: la valori NEGATIVE (incasari) se ia din dictionarul de explicatii,
        # taiat dupa prima liniuta; altfel e descrierea din extras. Vezi
        # EXPLICATII_FILTRU_CODCONTRACT — dictionarul e in practica gol.
        if valoare < 0:
            expl_bruta = dic_expl.get(_txt(r["NrDoc"]))
            if expl_bruta is not None:
                taietura = expl_bruta.find("-")
                explicatie = expl_bruta[taietura + 1:] if taietura >= 0 else expl_bruta
            else:
                explicatie = "LIPSA EXPLICATIE"
        else:
            explicatie = _txt(r["Descriere"])

        cod_partener, id_partener = "", None
        if r["PartAng"] and cod_indicator in dic_part_ind:
            cod_partener, id_partener = dic_part_ind[cod_indicator]

        linie = {
            "temp_id": urmator_linie,
            "idordtblp": 0,
            "part_temp_id": part["temp_id"],
            "idordpartp": 0,
            "cod_ai": cod_ai,
            "cod_angajament": _txt(r["CodAngajament"]),
            "cod_indicator": cod_indicator,
            "cod_ssi": _txt(r["CodSSI"]),
            # capcana 5: `IdClsf` = cheia MariaDB, `IdClsfAcc` = id-ul Access.
            "id_clsf": _int_or_none(r["IdClsf"]),
            "id_clsf_acc": _int_or_none(r["IdClsfAcc"]),
            "clsf": _txt(r["Clsf"]),
            "denumire": _txt(r["Denumire"]),
            "id_unitate": _int_or_none(r["IdUnitate"]),
            "total_receptii": receptii,
            "plati_ant": plati_ant,
            "valoare": valoare,
            "ramas": round(receptii - plati_ant - valoare, 2),
            "explicatie": explicatie,
            "cod_partener": cod_partener,
            "id_partener": id_partener,
        }
        urmator_linie -= 1
        linii.append(linie)

        # VBA aduna valoarea la platile anterioare ale indicatorului, deci a doua linie a
        # aceluiasi indicator vede prima. Se pastreaza.
        plati_curente[cod_indicator] = plati_ant + valoare

        # ---- legatura cu plata (`Adauga_Ord_Rec`): o plata = un rand -------------------
        rec.append({
            "temp_id": urmator_rec,
            "idordrecp": 0,
            "linie_temp_id": linie["temp_id"],
            "idordtblp": 0,
            "id_plata_fx": _int_or_none(r["IdPlataFX"]),
            "valoare": valoare,
        })
        urmator_rec -= 1

        # ---- documentul justificativ (`Adauga_Ord_Doc`): unul per rand-sursa ----------
        if valoare < 0:
            expl_bruta = dic_expl.get(_txt(r["NrDoc"]))
            if expl_bruta is not None:
                taietura = expl_bruta.find("-")
                doc_just = expl_bruta[taietura + 1:] if taietura >= 0 else expl_bruta
            else:
                doc_just = "INCASARE"
        else:
            doc_just = _txt(r["Descriere"]).upper()

        cheie_doc = (part["temp_id"], doc_just, None, "text")
        if cheie_doc in doc_vazute:
            doc_duplicate += 1
        else:
            doc_vazute.add(cheie_doc)
            documente.append({
                "temp_id": urmator_doc,
                "idorddocp": 0,
                "part_temp_id": part["temp_id"],
                "idordpartp": 0,
                "doc_just": doc_just,
                "nume_doc": None,
                "tip_doc": "text",
            })
            urmator_doc -= 1

    if doc_duplicate:
        # Nu e un avertisment pentru operator: se intampla la aproape orice zi cu mai multe
        # plati la fel, si nu-l pune sa faca nimic. Se scrie in jurnal ca sa se poata raspunde
        # la «de ce am mai putine documente decat linii».
        logger.info("[forexe.ord_edit] genereaza: %s document(e) justificativ(e) duplicat(e) "
                    "sarite (unic per beneficiar)", doc_duplicate)

    return {
        "antet": antet,
        "parteneri": parteneri,
        "linii": linii,
        "rec": rec,
        "documente": documente,
        "atasamente": [],
    }


@forexe_bp.route("/api/forexe/ord/genereaza", methods=["POST"])
@require_session
def post_ord_genereaza():
    """Graful PROPUS al unei ordonantari noi. NIMIC nu se scrie.

    Corp: { "cod": "...", "data": "2026-04-07", "id_plata_fx": null }
    `id_plata_fx` prezent = calea interactiva pentru O SINGURA plata; `null` = toate platile
    neordonantate ale zilei (VBA: `sIdPlataFX = "*"`).
    """
    date_in = request.get_json(silent=True)
    if not isinstance(date_in, dict):
        return _json_utf8({"error": "Corp JSON lipsă sau nevalid."}, 400)

    cod = str(date_in.get("cod") or "").strip()
    if cod == "":
        return _json_utf8({"error": "Câmp lipsă: cod"}, 400)

    db_name = g.session.db_name
    conn = None
    try:
        dt = _zi_ceruta(date_in.get("data"), "data")
        if dt is None:
            return _json_utf8({"error": "Câmp lipsă: data"}, 400)
        id_plata_fx = _int_or_none(date_in.get("id_plata_fx"))

        conn = get_kbot_connection(db_name)
        cursor = conn.cursor(dictionary=True)

        avertismente = []

        # Avertismentul de 25 de parteneri (`Contor_Parteneri_Zi > 1`) — doar pe calea
        # «toate platile zilei», exact ca in `FX_Adaugare_ORD_Din_Plati`.
        if id_plata_fx is None:
            pagini = contor_parteneri_zi(cursor, cod, dt)
            if pagini > 1:
                avertismente.append(
                    f"Data {dt.strftime('%d.%m.%Y')} conține mai mult de "
                    f"{LIMITA_PARTENERI} de parteneri distincți. Ordonanțarea se "
                    f"generează doar pentru primii {LIMITA_PARTENERI}; mai sunt nevoie "
                    f"de încă {pagini - 1} ordonanțări pentru restul.")

        dic_banci = incarca_dic_banci(cursor, db_name, avertismente)
        dic_part_ind = incarca_dic_part_ind(cursor, cod)
        dic_receptii = incarca_dic_receptii(cursor, cod, dt)
        dic_plati = incarca_dic_plati(cursor, cod, dt)
        dic_expl = incarca_explicatii_incasari(cursor, cod)

        kmatch = populeaza_part_sel(cursor, cod, dt)
        nume_unitate = _txt((g.session.ctx or {}).get("NumeUnitate"))
        randuri = citeste_baza(cursor, cod, dt, nume_unitate, id_plata_fx, kmatch)

        if not randuri:
            return _json_utf8(
                {"error": f"Nu există plăți neordonanțate pentru {cod} în data "
                          f"{dt.strftime('%d.%m.%Y')}."}, 404)

        graf = construieste_graf(randuri, dic_banci, dic_part_ind, dic_receptii,
                                 dic_plati, dic_expl, cod, dt, avertismente)
        graf["cod"] = cod
        graf["avertismente"] = avertismente

        logger.info("[forexe.ord_edit] %s: genereaza cod=%s data=%s -> parteneri=%s linii=%s",
                    db_name, cod, dt, len(graf["parteneri"]), len(graf["linii"]))
        return _json_utf8(graf, 200)
    except DateInvalide as e:
        return _json_utf8({"error": str(e)}, 400)
    except Exception as e:
        # Fara inghitire: o eroare de baza intoarce motivul, NU un graf gol — un graf gol ar
        # minti operatorul ca ziua nu are plati.
        logger.error(f"[forexe.ord_edit] genereaza: {e}", exc_info=True)
        return _json_utf8({"error": f"Eroare la generarea ordonanțării: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()


# =========================================================================================
# CITIREA UNEI ORDONANTARI EXISTENTE, IN FORMA EDITORULUI
# =========================================================================================
# De ce nu se refoloseste `GET /api/forexe/ord`: acea ruta e a VEDERII 0033 si isi alege
# coloanele deliberat — nu intoarce `CodAI`, `CodIndicator`, `IdClsf`, `CodSSI`,
# `Explicatie`, `CodPartener`/`IdPartener`, randurile de document una cate una (le aduna cu
# GROUP_CONCAT), legaturile `FX_ORD_TBL_REC` sau atasamentele. Editorul are nevoie de toate.
# `ord.py` NU se modifica (e partajata cu vederea); citirea editorului sta aici, ca sora a
# scrierii, si moare odata cu ea daca vreodata se retrage.
_SQL_DRAFT_ANTET = (
    "SELECT O.IDORDP, O.IDDF, O.NrORD, O.DataORD, O.Comp, O.CUAL, O.Incarcat, O.Preluat, "
    "       O.CodAngajament, "
    "       (SELECT D.ObiectDDF FROM FX_DDF D WHERE D.IDDF = O.IDDF "
    "         ORDER BY D.IDDF, D.CUAL LIMIT 1) AS ObiectDDF, "
    "       (SELECT D.PartAng FROM FX_DDF D WHERE D.IDDF = O.IDDF "
    "         ORDER BY D.IDDF, D.CUAL LIMIT 1) AS PartAng, "
    "       (SELECT D.NumePartener FROM FX_DDF D WHERE D.IDDF = O.IDDF "
    "         ORDER BY D.IDDF, D.CUAL LIMIT 1) AS NumePartener "
    "  FROM FX_ORD O WHERE O.IDORDP = %s"
)

_SQL_DRAFT_PART = (
    "SELECT IDORDPARTP, Counter, DenBene, CodFiscal, ContIBAN, Banca "
    "  FROM FX_ORD_PART WHERE IDORDP = %s ORDER BY IDORDPARTP"
)

# Clasificatia se rezolva prin DOUA drumuri, intr-un COALESCE, exact ca in
# routes/forexe/ord.py: direct (`Clasificatii.IDClsf = t.IdClsf`) si, cand acela e gol, prin
# `FX_Indicatori` (`IdClsfAcc + IdUnitate`) — drumul verificat live in felia 0011-03.
_SQL_DRAFT_TBL = (
    "SELECT T.IDORDTBLP, T.IDORDPARTP, T.CodAI, T.CodAngajament, T.CodIndicator, T.CodSSI, "
    "       T.TotalReceptii, T.PlatiAnt, T.Valoare, T.Ramas, T.Explicatie, "
    "       T.IdClsf, T.IdClsfAcc, T.IdUnitate, T.CodPartener, T.IdPartener, "
    "       COALESCE(NULLIF((SELECT C.Clsf FROM Clasificatii C "
    "                         WHERE C.IDClsf = T.IdClsf LIMIT 1), ''), "
    "                (SELECT C.Clsf FROM Clasificatii C "
    "                  WHERE C.IdClsfAcc = I.IdClsf AND C.IdUnitate = I.IdUnitate LIMIT 1)) AS Clsf, "
    "       COALESCE(NULLIF((SELECT C.Denumire FROM Clasificatii C "
    "                         WHERE C.IDClsf = T.IdClsf LIMIT 1), ''), "
    "                (SELECT C.Denumire FROM Clasificatii C "
    "                  WHERE C.IdClsfAcc = I.IdClsf AND C.IdUnitate = I.IdUnitate LIMIT 1)) AS Denumire "
    "  FROM FX_ORD_TBL T "
    "  LEFT JOIN FX_Indicatori I ON I.CodAI = T.CodAI "
    " WHERE T.IDORDP = %s ORDER BY T.IDORDTBLP"
)

_SQL_DRAFT_REC = (
    "SELECT R.IDORDRECP, R.IDORDTBLP, R.IdPlataFX, R.Valoare "
    "  FROM FX_ORD_TBL_REC R "
    " WHERE R.IDORDTBLP IN (SELECT IDORDTBLP FROM FX_ORD_TBL WHERE IDORDP = %s) "
    " ORDER BY R.IDORDRECP"
)

_SQL_DRAFT_DOC = (
    "SELECT IDORDDOCP, IDORDPARTP, DocJust, NumeDoc, TipDoc "
    "  FROM FX_ORD_DOC WHERE IDORDP = %s ORDER BY IDORDDOCP"
)

# Atasamentele: randul din FX_ORD_ATT plus METADATELE octetilor (fara octeti). `Imagine`
# (longtext base64) NU se citeste — vezi decizia D9 a feliei.
#
# `FX_ORD_ATT_IMG` e tabela NOUA a acestei felii (sql/0049_ord_att_img.sql). Pe o baza pe
# care fisierul acela n-a fost inca rulat, un SELECT peste ea ar da 500 pe TOATA citirea
# draftului — deci existenta ei se PROBEAZA o data per baza, exact ca proba `FX_ORD.CalePDF`
# din routes/forexe/ord.py, si atunci metadatele lipsesc in loc sa cada formularul.
_SQL_DRAFT_ATT = (
    "SELECT A.IDORDATTP, A.IDORDPARTP, "
    "       M.NumeFisier, M.TipMime, M.Dimensiune, M.Sha256, M.DataModif "
    "  FROM FX_ORD_ATT A "
    "  LEFT JOIN FX_ORD_ATT_IMG M ON M.IDORDATTP = A.IDORDATTP "
    " WHERE A.IDORDP = %s ORDER BY A.IDORDATTP"
)

_SQL_DRAFT_ATT_FARA_IMG = (
    "SELECT A.IDORDATTP, A.IDORDPARTP, "
    "       NULL AS NumeFisier, NULL AS TipMime, NULL AS Dimensiune, "
    "       NULL AS Sha256, NULL AS DataModif "
    "  FROM FX_ORD_ATT A WHERE A.IDORDP = %s ORDER BY A.IDORDATTP"
)

_SQL_ARE_ATT_IMG = (
    "SELECT COUNT(*) AS N FROM information_schema.TABLES "
    "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'FX_ORD_ATT_IMG'"
)
_are_att_img_cache: dict = {}


def _are_att_img(cursor, db_name: str) -> bool:
    """Exista `FX_ORD_ATT_IMG` pe baza asta? Proba se face O SINGURA DATA per baza."""
    if db_name in _are_att_img_cache:
        return _are_att_img_cache[db_name]
    try:
        cursor.execute(_SQL_ARE_ATT_IMG, (db_name,))
        row = cursor.fetchone()
        exista = bool(row and row["N"])
    except Exception as e:
        logger.warning("[forexe.ord_edit] %s: proba FX_ORD_ATT_IMG a esuat (%s); "
                       "se continua fara metadate de imagine", db_name, e)
        exista = False
    _are_att_img_cache[db_name] = exista
    return exista


@forexe_bp.route("/api/forexe/ord/draft/<int:idordp>", methods=["GET"])
@require_session
def get_ord_draft(idordp):
    """Graful complet al unei ordonantari existente, in forma pe care o editeaza formularul."""
    db_name = g.session.db_name
    conn = None
    try:
        conn = get_kbot_connection(db_name)
        cursor = conn.cursor(dictionary=True)

        cursor.execute(_SQL_DRAFT_ANTET, (idordp,))
        a = cursor.fetchone()
        if a is None:
            return _json_utf8({"error": "Ordonanțarea cerută nu există."}, 404)

        antet = {
            "idordp": int(a["IDORDP"]),
            "nr_ord": int(a["NrORD"]) if a["NrORD"] is not None else 0,
            "data_ord": _iso_zi(a["DataORD"]),
            "iddf": _int_or_none(a["IDDF"]),
            "cual": _txt(a["CUAL"]),
            "comp": _txt(a["Comp"]),
            "cod_angajament": _txt(a["CodAngajament"]),
            "incarcat": bool(a["Incarcat"]),
            "preluat": bool(a["Preluat"]),
            "obiect_ddf": _txt(a["ObiectDDF"]),
            "part_ang": bool(a["PartAng"]),
            "nume_partener": _txt(a["NumePartener"]),
        }

        cursor.execute(_SQL_DRAFT_PART, (idordp,))
        parteneri = [{
            "temp_id": 0,
            "idordpartp": int(r["IDORDPARTP"]),
            "counter": _txt(r["Counter"]),
            "den_bene": _txt(r["DenBene"]),
            "cod_fiscal": _txt(r["CodFiscal"]),
            "cont_iban": _txt(r["ContIBAN"]),
            "banca": _txt(r["Banca"]),
        } for r in cursor.fetchall()]

        cursor.execute(_SQL_DRAFT_TBL, (idordp,))
        linii = [{
            "temp_id": 0,
            "idordtblp": int(r["IDORDTBLP"]),
            "part_temp_id": 0,
            "idordpartp": _int_or_none(r["IDORDPARTP"]) or 0,
            "cod_ai": _txt(r["CodAI"]),
            "cod_angajament": _txt(r["CodAngajament"]),
            "cod_indicator": _txt(r["CodIndicator"]),
            "cod_ssi": _txt(r["CodSSI"]),
            "id_clsf": _int_or_none(r["IdClsf"]),
            "id_clsf_acc": _int_or_none(r["IdClsfAcc"]),
            "clsf": _txt(r["Clsf"]),
            "denumire": _txt(r["Denumire"]),
            "id_unitate": _int_or_none(r["IdUnitate"]),
            "total_receptii": _num(r["TotalReceptii"]),
            "plati_ant": _num(r["PlatiAnt"]),
            "valoare": _num(r["Valoare"]),
            "ramas": _num(r["Ramas"]),
            "explicatie": _txt(r["Explicatie"]),
            "cod_partener": _txt(r["CodPartener"]),
            "id_partener": _int_or_none(r["IdPartener"]),
        } for r in cursor.fetchall()]

        cursor.execute(_SQL_DRAFT_REC, (idordp,))
        rec = [{
            "temp_id": 0,
            "idordrecp": int(r["IDORDRECP"]),
            "linie_temp_id": 0,
            "idordtblp": _int_or_none(r["IDORDTBLP"]) or 0,
            "id_plata_fx": _int_or_none(r["IdPlataFX"]),
            "valoare": _num(r["Valoare"]),
        } for r in cursor.fetchall()]

        cursor.execute(_SQL_DRAFT_DOC, (idordp,))
        documente = [{
            "temp_id": 0,
            "idorddocp": int(r["IDORDDOCP"]),
            "part_temp_id": 0,
            "idordpartp": _int_or_none(r["IDORDPARTP"]) or 0,
            "doc_just": _txt(r["DocJust"]),
            "nume_doc": r["NumeDoc"],
            "tip_doc": _txt(r["TipDoc"]),
        } for r in cursor.fetchall()]

        are_img = _are_att_img(cursor, db_name)
        cursor.execute(_SQL_DRAFT_ATT if are_img else _SQL_DRAFT_ATT_FARA_IMG, (idordp,))
        atasamente = [{
            "temp_id": 0,
            "idordattp": int(r["IDORDATTP"]),
            "part_temp_id": 0,
            "idordpartp": _int_or_none(r["IDORDPARTP"]) or 0,
            "nume_fisier": _txt(r["NumeFisier"]),
            "tip_mime": _txt(r["TipMime"]),
            "dimensiune": _int_or_none(r["Dimensiune"]) or 0,
            "sha256": _txt(r["Sha256"]),
            "data_modif": _iso_dt(r["DataModif"]),
        } for r in cursor.fetchall()]

        logger.info("[forexe.ord_edit] %s: draft idordp=%s -> parteneri=%s linii=%s doc=%s "
                    "att=%s (imagini=%s)",
                    db_name, idordp, len(parteneri), len(linii), len(documente),
                    len(atasamente), are_img)
        return _json_utf8({
            "cod": antet["cod_angajament"],
            "antet": antet,
            "parteneri": parteneri,
            "linii": linii,
            "rec": rec,
            "documente": documente,
            "atasamente": atasamente,
            "avertismente": [],
        }, 200)
    except Exception as e:
        logger.error(f"[forexe.ord_edit] draft {idordp}: {e}", exc_info=True)
        return _json_utf8({"error": f"Eroare la citirea ordonanțării: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()


@forexe_bp.route("/api/forexe/ord/zile", methods=["GET"])
@require_session
def get_ord_zile():
    """Zilele cu plati neordonantate ale unui angajament — sursa modului in lot.

    Query: cod (obligatoriu), luna (1-12, optional), an (optional).
    Fiecare zi poarta cate ordonantari ii trebuie (`Contor_Parteneri_Zi`), iar totalul
    estimat e portul lui `Contor_Zile_Luna`.
    """
    cod = request.args.get("cod")
    if cod is None or str(cod).strip() == "":
        return _json_utf8({"error": "Parametru lipsă: cod"}, 400)
    cod = str(cod).strip()

    luna_brut = request.args.get("luna")
    an_brut = request.args.get("an")
    try:
        luna = int(luna_brut) if luna_brut not in (None, "") else None
        an = int(an_brut) if an_brut not in (None, "") else None
    except ValueError:
        return _json_utf8({"error": "Parametrii «luna» și «an» trebuie să fie numere."}, 400)
    if luna is not None and not 1 <= luna <= 12:
        return _json_utf8({"error": "Parametrul «luna» trebuie să fie între 1 și 12."}, 400)

    db_name = g.session.db_name
    conn = None
    try:
        conn = get_kbot_connection(db_name)
        cursor = conn.cursor(dictionary=True)

        zile = []
        total_estimat = 0
        for r in citeste_zile(cursor, cod, luna, an):
            dt = r["DT"]
            pagini = contor_parteneri_zi(cursor, cod, dt)
            total_estimat += pagini
            zile.append({
                "data": _iso_zi(dt),
                "plati": int(r["Plati"]),
                "ordonantari": pagini,
            })

        logger.info("[forexe.ord_edit] %s: zile cod=%s luna=%s an=%s -> %s zile, %s ordonantari estimate",
                    db_name, cod, luna, an, len(zile), total_estimat)
        return _json_utf8({"cod": cod, "zile": zile, "total_estimat": total_estimat}, 200)
    except Exception as e:
        logger.error(f"[forexe.ord_edit] zile: {e}", exc_info=True)
        return _json_utf8({"error": f"Eroare la citirea zilelor cu plăți: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()


@forexe_bp.route("/api/forexe/ord/nr-urmator", methods=["GET"])
@require_session
def get_ord_nr_urmator():
    """Numarul pe care l-ar primi ACUM o ordonantare noua. Raspuns: { "nr_ord": N }.

    E o PRESUPUNERE, nu o rezervare. Numarul adevarat se aloca tot in tranzactia de salvare
    (`_scrie_graf`, D8: `MAX(NrORD) + 1` cu `FOR UPDATE`), fiindca acolo e singurul loc in
    care doua salvari concurente se pot aseza la rand. Ruta asta raspunde la o intrebare pe
    care operatorul o punea oricum inainte de a incepe sa scrie — «al catelea va fi?» — si o
    raspunde fara sa incuie nimic: un `FOR UPDATE` aici ar tine lacatul pe toata durata
    editarii si ar opri restul casei din salvat.

    Consecinta, spusa pe fata in interfata: intre intrebare si salvare altcineva poate salva
    primul, si atunci numarul real va fi altul.
    """
    db_name = g.session.db_name
    conn = None
    try:
        conn = get_kbot_connection(db_name)
        cursor = conn.cursor(dictionary=True)
        # Fara `FOR UPDATE` — vezi mai sus. Predicatul Access `DC='…'` s-a retras si aici:
        # o baza = o unitate, deci selecta oricum tot.
        cursor.execute("SELECT COALESCE(MAX(NrORD), 0) + 1 AS Urmator FROM FX_ORD")
        nr_ord = int(cursor.fetchone()["Urmator"])
        logger.info("[forexe.ord_edit] %s: nr-urmator -> %s", db_name, nr_ord)
        return _json_utf8({"nr_ord": nr_ord}, 200)
    except Exception as e:
        logger.error(f"[forexe.ord_edit] nr-urmator: {e}", exc_info=True)
        return _json_utf8({"error": f"Eroare la citirea numărului următor: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()


# =========================================================================================
# SALVAREA
# =========================================================================================
def _lista(sarcina: dict, cheie: str) -> list:
    v = sarcina.get(cheie)
    if v is None:
        return []
    if not isinstance(v, list):
        raise DateInvalide(f"Câmpul «{cheie}» trebuie să fie o listă.")
    for i, item in enumerate(v):
        if not isinstance(item, dict):
            raise DateInvalide(f"«{cheie}»[{i}] nu este un obiect.")
    return v


def valideaza_graf(cursor, sarcina: dict) -> None:
    """Toate motivele de refuz, ADUNATE, inainte de orice scriere.

    Portul blocului de dinainte de salvare din `frmFX_ORD.btnSav_Click`, plus cele trei
    verificari pe care Access nu le avea nevoie fiindca nu avea chei straine (capcana 4).
    Se aduna TOATE problemele intr-un singur mesaj, nu se raporteaza prima — exact ca in
    Access, unde `msgEroare` se construia rand cu rand.
    """
    motive = []

    antet = sarcina.get("antet")
    if not isinstance(antet, dict):
        raise DateInvalide("Lipsesc datele de antet ale ordonanțării.")
    if not _txt(antet.get("data_ord")).strip():
        motive.append("Data ordonanțării lipsește.")
    if not _txt(antet.get("comp")).strip():
        motive.append("Compartimentul lipsește.")
    if _int_or_none(antet.get("iddf")) in (None, 0):
        motive.append("IDDF lipsește (ordonanțarea nu e legată de niciun document de fundamentare).")
    if not _txt(antet.get("cual")).strip():
        motive.append("CUAL lipsește.")
    if not _txt(antet.get("cod_angajament")).strip():
        motive.append("Codul angajamentului lipsește.")

    parteneri = _lista(sarcina, "parteneri")
    if not parteneri:
        motive.append("Lipsește cel puțin un beneficiar.")
    for p in parteneri:
        eticheta = _txt(p.get("counter")) or "?"
        if not _txt(p.get("den_bene")).strip():
            motive.append(f"Denumirea beneficiarului lipsește (beneficiar #{eticheta}).")
        if not _txt(p.get("cod_fiscal")).strip():
            motive.append(f"Codul fiscal lipsește (beneficiar #{eticheta}).")
        if not _txt(p.get("cont_iban")).strip():
            motive.append(f"Contul IBAN lipsește (beneficiar #{eticheta}).")

    linii = _lista(sarcina, "linii")
    if not linii:
        motive.append("Lipsește cel puțin un rând de plată.")

    # Cheile straine: se verifica pe NUME inainte de INSERT, ca refuzul sa spuna CE linie e
    # de vina. Altfel MariaDB opreste tranzactia cu un errno 1452 care nu numeste nimic.
    id_clsf_ceruti = set()
    cod_ai_ceruti = set()
    id_unitati_cerute = set()
    id_parteneri_ceruti = set()
    for i, l in enumerate(linii, start=1):
        if _num(l.get("valoare")) == 0.0:
            motive.append(f"Valoare = 0 pe rândul de plată #{i}.")
        if not _txt(l.get("cod_ssi")).strip():
            motive.append(f"Cod SSI lipsă pe rândul de plată #{i}.")
        id_clsf = _int_or_none(l.get("id_clsf"))
        if id_clsf in (None, 0):
            motive.append(f"Clasificația lipsește pe rândul de plată #{i}.")
        else:
            id_clsf_ceruti.add(id_clsf)
        cod_ai = _txt(l.get("cod_ai")).strip()
        if cod_ai == "":
            motive.append(f"CodAI lipsă pe rândul de plată #{i}.")
        else:
            cod_ai_ceruti.add(cod_ai)
        id_unitate = _int_or_none(l.get("id_unitate"))
        if id_unitate in (None, 0):
            motive.append(f"Unitatea lipsește pe rândul de plată #{i}.")
        else:
            id_unitati_cerute.add(id_unitate)
        id_partener = _int_or_none(l.get("id_partener"))
        if id_partener:
            id_parteneri_ceruti.add(id_partener)

    documente = _lista(sarcina, "documente")
    if not documente:
        motive.append("Lipsește cel puțin un rând în documentele justificative.")
    else:
        # Access: `IsNull(NumeDoc) And Not IsNull(DocJust)` — cel putin un rand TEXT.
        are_text = any(d.get("nume_doc") in (None, "") and _txt(d.get("doc_just")).strip() != ""
                       for d in documente)
        if not are_text:
            motive.append("Lipsește cel puțin un rând text în documentele justificative.")

    motive.extend(_verifica_existenta(cursor, "Clasificatii", "IDClsf", id_clsf_ceruti,
                                      "Clasificația {} nu există în nomenclator."))
    motive.extend(_verifica_existenta(cursor, "FX_Indicatori", "CodAI", cod_ai_ceruti,
                                      "Indicatorul «{}» nu există (CodAI)."))
    motive.extend(_verifica_existenta(cursor, "Unitati", "IdUnitate", id_unitati_cerute,
                                      "Unitatea {} nu există."))
    motive.extend(_verifica_existenta(cursor, "Parteneri", "IdPartener", id_parteneri_ceruti,
                                      "Partenerul {} nu există în nomenclator."))

    if motive:
        raise DateInvalide("Nu se poate salva din următoarele motive:\n- " + "\n- ".join(motive))


def _verifica_existenta(cursor, tabela: str, coloana: str, valori, sablon: str) -> list:
    """Care dintre valorile cerute NU exista in tabela-parinte? O singura interogare."""
    if not valori:
        return []
    locuri = ", ".join(["%s"] * len(valori))
    lista = list(valori)
    # Numele tabelei si al coloanei sunt LITERALE alese de server, nu date de la client.
    cursor.execute(f"SELECT {coloana} FROM {tabela} WHERE {coloana} IN ({locuri})", tuple(lista))
    gasite = {r[coloana] for r in cursor.fetchall()}
    return [sablon.format(v) for v in lista if v not in gasite]


_INSERT_ORD = (
    "INSERT INTO FX_ORD (IDORD, IDDF, NrORD, DataORD, Comp, CUAL, Incarcat, Preluat, "
    "                    CodAngajament) "
    "VALUES (NULL, %s, %s, %s, %s, %s, %s, %s, %s)"
)
_UPDATE_ORD = (
    "UPDATE FX_ORD SET IDDF = %s, DataORD = %s, Comp = %s, CUAL = %s, "
    "                  Incarcat = %s, Preluat = %s, CodAngajament = %s "
    " WHERE IDORDP = %s"
)

_INSERT_PART = (
    "INSERT INTO FX_ORD_PART (IDORDPART, IDORDP, Counter, DenBene, CodFiscal, ContIBAN, Banca) "
    "VALUES (NULL, %s, %s, %s, %s, %s, %s)"
)
_UPDATE_PART = (
    "UPDATE FX_ORD_PART SET Counter = %s, DenBene = %s, CodFiscal = %s, ContIBAN = %s, "
    "                       Banca = %s "
    " WHERE IDORDPARTP = %s AND IDORDP = %s"
)

_INSERT_TBL = (
    "INSERT INTO FX_ORD_TBL (IDORDTBL, IDORDP, IDORDPARTP, CodAI, CodAngajament, "
    "                        CodIndicator, CodSSI, TotalReceptii, PlatiAnt, Valoare, Ramas, "
    "                        IdClsfAcc, Explicatie, IdClsf, CodPartener, IdPartener, IdUnitate) "
    "VALUES (NULL, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
)
_UPDATE_TBL = (
    "UPDATE FX_ORD_TBL SET IDORDPARTP = %s, CodAI = %s, CodAngajament = %s, "
    "                      CodIndicator = %s, CodSSI = %s, TotalReceptii = %s, "
    "                      PlatiAnt = %s, Valoare = %s, Ramas = %s, IdClsfAcc = %s, "
    "                      Explicatie = %s, IdClsf = %s, CodPartener = %s, "
    "                      IdPartener = %s, IdUnitate = %s "
    " WHERE IDORDTBLP = %s AND IDORDP = %s"
)

_INSERT_REC = (
    "INSERT INTO FX_ORD_TBL_REC (IDORDTBLP, IDORDREC, IdPlataFX, Valoare) "
    "VALUES (%s, NULL, %s, %s)"
)

_INSERT_DOC = (
    "INSERT INTO FX_ORD_DOC (IDORDDOC, IDORDP, IDORDPARTP, DocJust, NumeDoc, TipDoc) "
    "VALUES (NULL, %s, %s, %s, %s, %s)"
)
_UPDATE_DOC = (
    "UPDATE FX_ORD_DOC SET IDORDPARTP = %s, DocJust = %s, NumeDoc = %s, TipDoc = %s "
    " WHERE IDORDDOCP = %s AND IDORDP = %s"
)

_INSERT_ATT = (
    "INSERT INTO FX_ORD_ATT (IDORDATT, IDORDP, IDORDPARTP, Imagine) "
    "VALUES (NULL, %s, %s, NULL)"
)
_UPDATE_ATT = (
    "UPDATE FX_ORD_ATT SET IDORDPARTP = %s WHERE IDORDATTP = %s AND IDORDP = %s"
)


def _cheie_parinte(item: dict, cheie_reala: str, cheie_temp: str, harta: dict, eticheta: str):
    """Cheia «...P» a parintelui: din harta cand parintele e nou, din sarcina cand nu e."""
    temp = _int_or_none(item.get(cheie_temp))
    if temp is not None and temp < 0:
        if temp not in harta:
            raise DateInvalide(
                f"{eticheta}: rândul trimite spre un părinte temporar ({temp}) "
                f"care nu există în sarcina de salvare.")
        return harta[temp]
    real = _int_or_none(item.get(cheie_reala))
    if real:
        return real
    raise DateInvalide(f"{eticheta}: rândul nu are părinte.")


def _fara_duplicate_doc(documente: list) -> list:
    """Un document justificativ ramane UNIC la un beneficiar (cerinta operatorului, 02.09.2026).

    Din mai multe randuri cu ACELEASI `IDORDPARTP` + `DocJust` + `NumeDoc` + `TipDoc` se scrie
    unul singur; restul se arunca. Generarea nu mai propune duplicate, iar editorul nu le mai
    lasa sa apara, dar ordonantarile scrise inainte de regula asta le au inca, si ele intra
    inapoi pe aici la prima salvare.

    Dintre doua randuri identice ramane cel care EXISTA deja in tabela: altfel un `IDORDDOCP`
    viu s-ar sterge doar ca sa se scrie altul la loc, cu exact acelasi continut.
    """
    vazute = {}          # cheia de valoare -> pozitia lui in `pastrate`
    pastrate = []
    for d in documente:
        real_part = _int_or_none(d.get("idordpartp")) or 0
        temp_part = _int_or_none(d.get("part_temp_id")) or 0
        nume_doc = d.get("nume_doc")
        cheie = (real_part if real_part > 0 else temp_part,
                 _txt(d.get("doc_just")),
                 None if nume_doc in (None, "") else str(nume_doc),
                 _txt(d.get("tip_doc")) or "text")
        poz = vazute.get(cheie)
        if poz is None:
            vazute[cheie] = len(pastrate)
            pastrate.append(d)
        elif ((_int_or_none(d.get("idorddocp")) or 0) > 0 and
              (_int_or_none(pastrate[poz].get("idorddocp")) or 0) <= 0):
            pastrate[poz] = d

    sarite = len(documente) - len(pastrate)
    if sarite:
        # Randurile REALE aruncate se sterg oricum prin `_sterge_absentii`, deci raspunsul le
        # numara deja in `sterse["documente"]`; aici se spune doar de ce.
        logger.info("[forexe.ord_edit] save: %s document(e) justificativ(e) duplicat(e) "
                    "aruncat(e) (unic per beneficiar)", sarite)
    return pastrate


def _sterge_absentii(cursor, tabela: str, cheie: str, idordp: int, pastrate: set) -> int:
    """Randurile din baza pentru acest IDORDP care LIPSESC din sarcina se sterg.

    Semantica pasului `colDelete` din `Save_FX_ORD_*_Update`, fara DAO. Tacerea inseamna
    «sterge-l», nu «las-o cum e» — asa se comporta si formularul Access.
    """
    if pastrate:
        locuri = ", ".join(["%s"] * len(pastrate))
        cursor.execute(
            f"DELETE FROM {tabela} WHERE IDORDP = %s AND {cheie} NOT IN ({locuri})",
            tuple([idordp] + list(pastrate)))
    else:
        cursor.execute(f"DELETE FROM {tabela} WHERE IDORDP = %s", (idordp,))
    return cursor.rowcount


@forexe_bp.route("/api/forexe/ord/save", methods=["POST"])
@require_session
def post_ord_save():
    """Scrie TOT graful ordonantarii intr-o singura tranzactie si intoarce cheile reale.

    Corp: { antet, parteneri[], linii[], rec[], documente[], atasamente[] }.
    Randurile noi poarta `temp_id` negative; cele existente poarta cheia «...P» reala.

    Raspuns: { idordp, nr_ord, harta: { parts, linii, rec, doc, att } } — `temp_id -> cheie`
    pe fiecare tabela. Clientul are nevoie de harta `att` ca sa incarce octetii (faza a doua).
    """
    sarcina = request.get_json(silent=True)
    if not isinstance(sarcina, dict):
        return _json_utf8({"error": "Corp JSON lipsă sau nevalid."}, 400)

    db_name = g.session.db_name

    # Reincercarea acopera TOATA tranzactia, niciodata o bucata din ea (regula casei).
    ultima_eroare = None
    for incercare in range(3):
        conn = None
        try:
            conn = get_kbot_connection(db_name)
            conn.autocommit = False
            cursor = conn.cursor(dictionary=True)
            # `start_transaction()` e API-ul conectorului; un `START TRANSACTION` scris de
            # mana ar comite tacit tranzactia implicita pe care `autocommit = False` a
            # deschis-o deja.
            if not conn.in_transaction:
                conn.start_transaction()

            rezultat = _scrie_graf(cursor, sarcina)
            conn.commit()

            logger.info("[forexe.ord_edit] %s: save idordp=%s nr_ord=%s (incercarea %s)",
                        db_name, rezultat["idordp"], rezultat["nr_ord"], incercare + 1)
            return _json_utf8(rezultat, 200)
        except DateInvalide as e:
            if conn is not None:
                conn.rollback()
            return _json_utf8({"error": str(e)}, 400)
        except Exception as e:
            if conn is not None:
                try:
                    conn.rollback()
                except Exception:
                    logger.warning("[forexe.ord_edit] rollback esuat dupa eroarea de mai jos",
                                   exc_info=True)
            ultima_eroare = e
            if _e_conflict(e) and incercare < 2:
                logger.warning("[forexe.ord_edit] %s: save a intalnit un conflict (%s); "
                               "se reia toata tranzactia", db_name, e)
                continue
            logger.error(f"[forexe.ord_edit] save: {e}", exc_info=True)
            return _json_utf8({"error": f"Eroare la salvarea ordonanțării: {e}"}, 500)
        finally:
            if conn is not None:
                conn.close()

    logger.error(f"[forexe.ord_edit] save: {ultima_eroare}", exc_info=True)
    return _json_utf8({"error": f"Eroare la salvarea ordonanțării: {ultima_eroare}"}, 500)


# Codurile MariaDB care merita reluarea INTREGII tranzactii: interblocare, expirarea
# asteptarii unui lacat, si cheia duplicata pe care o poate produce alocarea concurenta a
# lui NrORD. Se citesc din `errno`, nu din textul mesajului — textul e localizat de server
# si se schimba intre versiuni.
_ERORI_DE_RELUAT = frozenset({1213, 1205, 1062})


def _e_conflict(e) -> bool:
    """Eroarea merita reluarea intregii tranzactii? (conflict de integritate / interblocare)"""
    errno = getattr(e, "errno", None)
    return errno in _ERORI_DE_RELUAT


def _scrie_graf(cursor, sarcina: dict) -> dict:
    """Miezul tranzactiei de salvare. Ridica `DateInvalide` sau propaga eroarea de baza."""
    valideaza_graf(cursor, sarcina)

    antet = sarcina["antet"]
    idordp = _int_or_none(antet.get("idordp")) or 0
    data_ord = _zi_ceruta(antet.get("data_ord"), "data_ord")
    iddf = _int_or_none(antet.get("iddf"))
    comp = _txt(antet.get("comp"))
    cual = _txt(antet.get("cual"))
    incarcat = 1 if antet.get("incarcat") else 0
    preluat = 1 if antet.get("preluat") else 0
    cod = _txt(antet.get("cod_angajament"))

    # ---- 1. antetul --------------------------------------------------------------------
    if idordp > 0:
        cursor.execute(_UPDATE_ORD, (iddf, data_ord, comp, cual, incarcat, preluat, cod, idordp))
        cursor.execute("SELECT NrORD FROM FX_ORD WHERE IDORDP = %s", (idordp,))
        rand = cursor.fetchone()
        if rand is None:
            raise DateInvalide("Ordonanțarea pe care încercați să o salvați nu mai există.")
        nr_ord = int(rand["NrORD"] or 0)
    else:
        # D8: NrORD = MAX + 1, alocat IN TRANZACTIE. `FOR UPDATE` pune lacatul pe randurile
        # citite, deci doua salvari concurente se aseaza la rand in loc sa se ciocneasca.
        # Predicatul Access `DC='…'` s-a retras: o baza = o unitate, deci selecta oricum tot.
        cursor.execute("SELECT COALESCE(MAX(NrORD), 0) + 1 AS Urmator FROM FX_ORD FOR UPDATE")
        nr_ord = int(cursor.fetchone()["Urmator"])
        cursor.execute(_INSERT_ORD, (iddf, nr_ord, data_ord, comp, cual, incarcat, preluat, cod))
        idordp = int(cursor.lastrowid or 0)
        if idordp == 0:
            # Zgomotos, nu tacut: `lastrowid = 0` dupa un INSERT inseamna ca `FX_ORD.IDORDP`
            # si-a pierdut `AUTO_INCREMENT`. Tot ce ar urma s-ar lega de cheia 0.
            raise RuntimeError(
                "FX_ORD.IDORDP nu a întors o cheie nouă (AUTO_INCREMENT lipsă?). "
                "Nu s-a scris nimic.")

    harta_parts = {}
    harta_linii = {}
    harta_rec = {}
    harta_doc = {}
    harta_att = {}

    # ---- 2. beneficiarii ---------------------------------------------------------------
    parteneri = _lista(sarcina, "parteneri")
    pastrate_part = set()
    for p in parteneri:
        real = _int_or_none(p.get("idordpartp")) or 0
        valori = (_txt(p.get("counter")), _txt(p.get("den_bene")), _txt(p.get("cod_fiscal")),
                  _txt(p.get("cont_iban")), _txt(p.get("banca")))
        if real > 0:
            cursor.execute(_UPDATE_PART, valori + (real, idordp))
        else:
            cursor.execute(_INSERT_PART, (idordp,) + valori)
            real = int(cursor.lastrowid or 0)
            if real == 0:
                raise RuntimeError("FX_ORD_PART.IDORDPARTP nu a întors o cheie nouă "
                                   "(AUTO_INCREMENT lipsă?). Nu s-a scris nimic.")
            temp = _int_or_none(p.get("temp_id"))
            if temp is not None and temp < 0:
                harta_parts[temp] = real
        pastrate_part.add(real)

    # ---- 3. liniile --------------------------------------------------------------------
    linii = _lista(sarcina, "linii")
    pastrate_tbl = set()
    for l in linii:
        idordpartp = _cheie_parinte(l, "idordpartp", "part_temp_id", harta_parts,
                                    "Rând de plată")
        real = _int_or_none(l.get("idordtblp")) or 0
        valori = (
            idordpartp,
            _txt(l.get("cod_ai")), _txt(l.get("cod_angajament")), _txt(l.get("cod_indicator")),
            _txt(l.get("cod_ssi")),
            _num(l.get("total_receptii")), _num(l.get("plati_ant")),
            _num(l.get("valoare")), _num(l.get("ramas")),
            _int_or_none(l.get("id_clsf_acc")), _txt(l.get("explicatie")),
            _int_or_none(l.get("id_clsf")),
            _txt(l.get("cod_partener")) or None, _int_or_none(l.get("id_partener")),
            _int_or_none(l.get("id_unitate")),
        )
        if real > 0:
            cursor.execute(_UPDATE_TBL, valori + (real, idordp))
        else:
            cursor.execute(_INSERT_TBL, (idordp,) + valori)
            real = int(cursor.lastrowid or 0)
            if real == 0:
                raise RuntimeError("FX_ORD_TBL.IDORDTBLP nu a întors o cheie nouă "
                                   "(AUTO_INCREMENT lipsă?). Nu s-a scris nimic.")
            temp = _int_or_none(l.get("temp_id"))
            if temp is not None and temp < 0:
                harta_linii[temp] = real
        pastrate_tbl.add(real)

    # ---- 4. legaturile cu platile ------------------------------------------------------
    # STERGE + INSEREAZA, fara diferenta, exact ca `Save_FX_ORD_TBL_REC_Update`: randul nu
    # are camp editabil in afara celor doua chei si a valorii, deci o diferenta ar costa mai
    # mult decat rescrierea. Stergerea se face pe liniile ACESTEI ordonantari, prin IDORDTBLP.
    rec = _lista(sarcina, "rec")
    cursor.execute(
        "DELETE FROM FX_ORD_TBL_REC "
        " WHERE IDORDTBLP IN (SELECT IDORDTBLP FROM FX_ORD_TBL WHERE IDORDP = %s)", (idordp,))
    for r in rec:
        idordtblp = _cheie_parinte(r, "idordtblp", "linie_temp_id", harta_linii,
                                   "Legătură cu plata")
        cursor.execute(_INSERT_REC, (idordtblp, _int_or_none(r.get("id_plata_fx")),
                                     _num(r.get("valoare"))))
        real = int(cursor.lastrowid or 0)
        temp = _int_or_none(r.get("temp_id"))
        if temp is not None and temp < 0 and real:
            harta_rec[temp] = real

    # ---- 5. documentele justificative ---------------------------------------------------
    documente = _fara_duplicate_doc(_lista(sarcina, "documente"))
    pastrate_doc = set()
    for d in documente:
        # Un document poate apartine INTREGII ordonantari, nu unui beneficiar: atunci
        # `IDORDPARTP` ramane NULL. Asa mapa Access randul sintetic «toti beneficiarii».
        idordpartp = None
        temp_part = _int_or_none(d.get("part_temp_id"))
        real_part = _int_or_none(d.get("idordpartp")) or 0
        if temp_part is not None and temp_part < 0:
            idordpartp = _cheie_parinte(d, "idordpartp", "part_temp_id", harta_parts,
                                        "Document justificativ")
        elif real_part > 0:
            idordpartp = real_part

        real = _int_or_none(d.get("idorddocp")) or 0
        nume_doc = d.get("nume_doc")
        valori = (idordpartp, _txt(d.get("doc_just")),
                  None if nume_doc in (None, "") else str(nume_doc),
                  _txt(d.get("tip_doc")) or "text")
        if real > 0:
            cursor.execute(_UPDATE_DOC, valori + (real, idordp))
        else:
            cursor.execute(_INSERT_DOC, (idordp,) + valori)
            real = int(cursor.lastrowid or 0)
            if real == 0:
                raise RuntimeError("FX_ORD_DOC.IDORDDOCP nu a întors o cheie nouă "
                                   "(AUTO_INCREMENT lipsă?). Nu s-a scris nimic.")
            temp = _int_or_none(d.get("temp_id"))
            if temp is not None and temp < 0:
                harta_doc[temp] = real
        pastrate_doc.add(real)

    # ---- 6. atasamentele (doar randurile; octetii vin in faza a doua) -------------------
    atasamente = _lista(sarcina, "atasamente")
    pastrate_att = set()
    for a in atasamente:
        idordpartp = None
        temp_part = _int_or_none(a.get("part_temp_id"))
        real_part = _int_or_none(a.get("idordpartp")) or 0
        if temp_part is not None and temp_part < 0:
            idordpartp = _cheie_parinte(a, "idordpartp", "part_temp_id", harta_parts,
                                        "Atașament")
        elif real_part > 0:
            idordpartp = real_part

        real = _int_or_none(a.get("idordattp")) or 0
        if real > 0:
            cursor.execute(_UPDATE_ATT, (idordpartp, real, idordp))
        else:
            cursor.execute(_INSERT_ATT, (idordp, idordpartp))
            real = int(cursor.lastrowid or 0)
            if real == 0:
                raise RuntimeError("FX_ORD_ATT.IDORDATTP nu a întors o cheie nouă "
                                   "(AUTO_INCREMENT lipsă?). Nu s-a scris nimic.")
            temp = _int_or_none(a.get("temp_id"))
            if temp is not None and temp < 0:
                harta_att[temp] = real
        pastrate_att.add(real)

    # ---- 7. randurile disparute din sarcina se sterg ------------------------------------
    # Ordinea conteaza: intai copiii (DOC / ATT / TBL), apoi parintii (PART). Cascada ar
    # face-o oricum, dar un DELETE explicit spune ce s-a intamplat si numara.
    sterse = {
        "documente": _sterge_absentii(cursor, "FX_ORD_DOC", "IDORDDOCP", idordp, pastrate_doc),
        "atasamente": _sterge_absentii(cursor, "FX_ORD_ATT", "IDORDATTP", idordp, pastrate_att),
        "linii": _sterge_absentii(cursor, "FX_ORD_TBL", "IDORDTBLP", idordp, pastrate_tbl),
        "parteneri": _sterge_absentii(cursor, "FX_ORD_PART", "IDORDPARTP", idordp, pastrate_part),
    }

    return {
        "idordp": idordp,
        "nr_ord": nr_ord,
        "harta": {
            "parts": {str(k): v for k, v in harta_parts.items()},
            "linii": {str(k): v for k, v in harta_linii.items()},
            "rec": {str(k): v for k, v in harta_rec.items()},
            "doc": {str(k): v for k, v in harta_doc.items()},
            "att": {str(k): v for k, v in harta_att.items()},
        },
        "sterse": sterse,
    }


# =========================================================================================
# STERGEREA
# =========================================================================================
@forexe_bp.route("/api/forexe/ord/<int:idordp>", methods=["DELETE"])
@require_session
def delete_ord(idordp):
    """Sterge o ordonantare cu tot ce atarna de ea.

    Portul lui `FX_Sterge_Ordonandare`, care in Access stergea manual, in ordine,
    `FX_ORD_TBL_REC` -> `FX_ORD_ATT` -> `FX_ORD_DOC` -> `FX_ORD_TBL` -> `FX_ORD_PART` ->
    `FX_ORD`, fiindca Access nu avea cascade. Aici toate cele cinci legaturi sunt
    `ON DELETE CASCADE` REALE (plus `FX_ORD_TBL` -> `FX_ORD_TBL_REC` si `FX_ORD_PDF`), deci
    un singur DELETE pe antet face tot. NU se scriu stergeri manuale de copii: ele ar fi o a
    doua definitie a cascadei, care se poate desincroniza de prima.
    Access nu facea NIMIC in plus (niciun steag intors, nicio plata resetata) — verificat.

    Efectul de partea platilor e automat: «plata asta e deja ordonantata?» se raspunde prin
    `IdPlataFX IN (SELECT IdPlataFX FROM FX_ORD_TBL_REC)`, iar cascada goleste acele randuri,
    deci platile se intorc singure in rezerva de neordonantate.

    Se numara CE se sterge, INAINTE, ca raspunsul sa poata spune un numar adevarat in loc
    de «gata».
    """
    db_name = g.session.db_name
    conn = None
    try:
        conn = get_kbot_connection(db_name)
        conn.autocommit = False
        cursor = conn.cursor(dictionary=True)

        cursor.execute("SELECT NrORD, DataORD, CodAngajament FROM FX_ORD WHERE IDORDP = %s",
                       (idordp,))
        antet = cursor.fetchone()
        if antet is None:
            return _json_utf8({"error": "Ordonanțarea de șters nu există."}, 404)

        numarate = {}
        for eticheta, sql in (
            ("parteneri", "SELECT COUNT(*) AS N FROM FX_ORD_PART WHERE IDORDP = %s"),
            ("linii", "SELECT COUNT(*) AS N FROM FX_ORD_TBL WHERE IDORDP = %s"),
            ("documente", "SELECT COUNT(*) AS N FROM FX_ORD_DOC WHERE IDORDP = %s"),
            ("atasamente", "SELECT COUNT(*) AS N FROM FX_ORD_ATT WHERE IDORDP = %s"),
            ("pdf", "SELECT COUNT(*) AS N FROM FX_ORD_PDF WHERE IDORDP = %s"),
            ("plati_eliberate",
             "SELECT COUNT(*) AS N FROM FX_ORD_TBL_REC "
             " WHERE IDORDTBLP IN (SELECT IDORDTBLP FROM FX_ORD_TBL WHERE IDORDP = %s)"),
        ):
            cursor.execute(sql, (idordp,))
            numarate[eticheta] = int(cursor.fetchone()["N"])

        cursor.execute("DELETE FROM FX_ORD WHERE IDORDP = %s", (idordp,))
        numarate["ordonantari"] = cursor.rowcount
        conn.commit()

        logger.info("[forexe.ord_edit] %s: sters idordp=%s (nr=%s) -> %s",
                    db_name, idordp, antet["NrORD"], numarate)
        return _json_utf8({
            "idordp": idordp,
            "nr_ord": int(antet["NrORD"] or 0),
            "data_ord": _iso_zi(antet["DataORD"]),
            "cod": _txt(antet["CodAngajament"]),
            "sterse": numarate,
        }, 200)
    except Exception as e:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                logger.warning("[forexe.ord_edit] rollback esuat dupa eroarea de mai jos",
                               exc_info=True)
        logger.error(f"[forexe.ord_edit] delete {idordp}: {e}", exc_info=True)
        return _json_utf8({"error": f"Eroare la ștergerea ordonanțării: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()


# =========================================================================================
# OCTETII ATASAMENTELOR
# =========================================================================================
# Acelasi contract ca la PDF-urile semnate (routes/forexe/pdf.py): octeti bruti pe fir,
# SHA-256 verificat la amandoua capetele si in amandoua sensurile, concurenta optimista
# prin `X-Sha-Precedent`.
def _tip_imagine(octeti: bytes):
    """Tipul MIME dedus din primii octeti, sau None cand nu e o imagine cunoscuta.

    Se deduce pe SERVER, nu se ia de la client: acelasi principiu ca numele fisierului PDF.
    Access facea aceeasi deductie, dar peste base64 (`DetectMimeType`).
    """
    for semnatura, mime in SEMNATURI_IMAGINE:
        if octeti.startswith(semnatura):
            return mime
    return None


def _att_exista(cursor, idordattp: int) -> bool:
    cursor.execute("SELECT 1 FROM FX_ORD_ATT WHERE IDORDATTP = %s LIMIT 1", (idordattp,))
    return cursor.fetchone() is not None


def _att_sha(cursor, idordattp: int):
    cursor.execute("SELECT Sha256 FROM FX_ORD_ATT_IMG WHERE IDORDATTP = %s LIMIT 1",
                   (idordattp,))
    row = cursor.fetchone()
    return row[0] if row else None


@forexe_bp.route("/api/forexe/ord/att/<int:idordattp>/imagine", methods=["GET"])
@require_session
def get_ord_att_imagine(idordattp):
    """Octetii imaginii unui atasament, VERBATIM. `If-None-Match` egal cu suma -> 304."""
    db_name = g.session.db_name
    conn = None
    try:
        conn = get_kbot_connection(db_name)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT Sha256, TipMime, Dimensiune, Continut FROM FX_ORD_ATT_IMG "
            " WHERE IDORDATTP = %s LIMIT 1", (idordattp,))
        row = cursor.fetchone()
        if row is None:
            return _json_utf8({"error": "Nu există imagine pentru acest atașament."}, 404)

        sha, mime, dimensiune, continut = row
        if (request.headers.get("If-None-Match", "").strip().strip('"')) == sha:
            resp = current_app.response_class(b"", status=304)
            resp.headers["ETag"] = f'"{sha}"'
            return resp

        octeti = bytes(continut)
        resp = current_app.response_class(octeti, status=200,
                                          mimetype=mime or "application/octet-stream")
        resp.headers["Content-Length"] = str(len(octeti))
        resp.headers["ETag"] = f'"{sha}"'
        logger.info("[forexe.ord_edit] %s: imagine att=%s -> 200 (%s octeti)",
                    db_name, idordattp, dimensiune)
        return resp
    except Exception as e:
        # Fara inghitire: un 404 ar minti clientul ca atasamentul nu are imagine.
        logger.error(f"[forexe.ord_edit] imagine GET {idordattp}: {e}", exc_info=True)
        return _json_utf8({"error": f"Eroare la citirea imaginii: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()


@forexe_bp.route("/api/forexe/ord/att/<int:idordattp>/imagine", methods=["PUT"])
@require_session
def put_ord_att_imagine(idordattp):
    """Inlocuieste-sau-insereaza octetii imaginii. Ordinea pasilor: parinte -> imagine
    valida -> suma de control -> concurenta -> scriere. Fiecare pas care esueaza NU scrie."""
    db_name = g.session.db_name

    octeti = request.get_data()
    if not octeti:
        return _json_utf8({"error": "Corpul cererii este gol: nu s-a primit niciun fișier."}, 400)
    if len(octeti) > MAX_IMAGINE_BYTES:
        return _json_utf8(
            {"error": f"Fișierul depășește limita de {MAX_IMAGINE_BYTES // (1024 * 1024)} MB "
                      f"acceptată de server ({len(octeti)} octeți)."}, 413)

    nume = (request.headers.get("X-Nume-Fisier") or "").strip()
    if not nume:
        return _json_utf8({"error": "Antet lipsă: X-Nume-Fisier."}, 400)
    sha_client = (request.headers.get(H_SHA) or "").strip().lower()
    if not sha_client:
        return _json_utf8({"error": f"Antet lipsă: {H_SHA}."}, 400)
    sha_precedent = (request.headers.get(H_SHA_PREC) or "").strip().lower()
    if not sha_precedent:
        return _json_utf8({"error": f"Antet lipsă: {H_SHA_PREC}."}, 400)

    conn = None
    try:
        conn = get_kbot_connection(db_name)
        conn.autocommit = False
        cursor = conn.cursor()

        if not _att_exista(cursor, idordattp):
            return _json_utf8({"error": "Atașamentul pentru care s-a trimis imaginea nu există."}, 404)

        mime = _tip_imagine(octeti)
        if mime is None:
            return _json_utf8(
                {"error": "Conținutul trimis nu este o imagine recunoscută "
                          "(acceptate: JPEG, PNG, BMP, GIF, TIFF)."}, 400)

        sha_server = _sha256(octeti)
        if sha_server != sha_client:
            logger.warning("[forexe.ord_edit] %s: att=%s suma nepotrivita (client=%s… server=%s…)",
                           db_name, idordattp, sha_client[:8], sha_server[:8])
            return _json_utf8({"error": "Fișierul a sosit corupt: suma de control nu corespunde."}, 400)

        sha_stocat = _att_sha(cursor, idordattp)
        asteptat = NO_ROW if sha_stocat is None else sha_stocat
        if sha_precedent != asteptat:
            return _json_utf8({"error": "Imaginea a fost modificată de altcineva între timp."}, 409)

        cursor.execute(
            "INSERT INTO FX_ORD_ATT_IMG "
            "       (IDORDATTP, NumeFisier, TipMime, Dimensiune, Sha256, Continut, DataModif) "
            "VALUES (%s, %s, %s, %s, %s, %s, NOW()) "
            "ON DUPLICATE KEY UPDATE "
            "       NumeFisier = VALUES(NumeFisier), TipMime = VALUES(TipMime), "
            "       Dimensiune = VALUES(Dimensiune), Sha256 = VALUES(Sha256), "
            "       Continut = VALUES(Continut), DataModif = NOW()",
            (idordattp, nume[:255], mime, len(octeti), sha_server, octeti))
        conn.commit()

        logger.info("[forexe.ord_edit] %s: imagine att=%s salvata (%s octeti, sha=%s…, %s)",
                    db_name, idordattp, len(octeti), sha_server[:8], mime)
        return _json_utf8({"idordattp": idordattp, "sha256": sha_server,
                           "nume_fisier": nume[:255], "tip_mime": mime,
                           "dimensiune": len(octeti)}, 200)
    except Exception as e:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                logger.warning("[forexe.ord_edit] rollback esuat dupa eroarea de mai jos",
                               exc_info=True)
        logger.error(f"[forexe.ord_edit] imagine PUT {idordattp}: {e}", exc_info=True)
        return _json_utf8({"error": f"Eroare la salvarea imaginii: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()


@forexe_bp.route("/api/forexe/ord/att/<int:idordattp>/imagine", methods=["DELETE"])
@require_session
def delete_ord_att_imagine(idordattp):
    """Sterge octetii imaginii, lasand randul de atasament pe loc."""
    db_name = g.session.db_name
    conn = None
    try:
        conn = get_kbot_connection(db_name)
        conn.autocommit = False
        cursor = conn.cursor()
        cursor.execute("DELETE FROM FX_ORD_ATT_IMG WHERE IDORDATTP = %s", (idordattp,))
        sterse = cursor.rowcount
        conn.commit()
        logger.info("[forexe.ord_edit] %s: imagine att=%s stearsa (%s randuri)",
                    db_name, idordattp, sterse)
        return _json_utf8({"idordattp": idordattp, "sterse": sterse}, 200)
    except Exception as e:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                logger.warning("[forexe.ord_edit] rollback esuat dupa eroarea de mai jos",
                               exc_info=True)
        logger.error(f"[forexe.ord_edit] imagine DELETE {idordattp}: {e}", exc_info=True)
        return _json_utf8({"error": f"Eroare la ștergerea imaginii: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()
