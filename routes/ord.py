# routes/ord.py — v7
# Schema: AVACONT 
#
# v7 vs v6: (19/05/2026)
#   - _sync_tbl_rec: rec_map returna {TmpID, IDORDREC} — incorect.
#       Corectat la {TmpID, IDORDRECP} (MariaDB AUTO_INCREMENT PK,
#       capturat cu cursor.lastrowid dupa fiecare INSERT).
#       rec_map acumuleaza TOATE randurile inserate (nu doar cele cu
#       IDORDREC generat de server), consistent cu pattern-ul celorlalte
#       entitati (part_map, tbl_map, att_map, doc_map la ADD).
#   - stg_OrdTblRec: PRIMARY KEY (Token) permitea o singura linie per
#       token — bug care facea INSERT sa esueze de la al doilea rand.
#       Rezolvat prin adaugarea StgRecID INT NOT NULL AUTO_INCREMENT
#       PRIMARY KEY, consistent cu stg_OrdPart.StgPartID.
#       SQL aplicat pe MariaDB:
#           ALTER TABLE stg_OrdTblRec
#               DROP PRIMARY KEY,
#               ADD COLUMN StgRecID INT NOT NULL AUTO_INCREMENT PRIMARY KEY FIRST,
#               ADD INDEX idx_token (Token);
#
# v6 vs v5: (11/05/2026)
#   - FX_ORD.IDRH (nullable): link la FX_Receptii_H (istoricul receptiei).
#       Stocat pentru trasabilitate — permite legatura retroactiva la intrarea
#       din registrul istoric, independent de IDRR (receptia reala).
#       Flux 1: IDRH pozitiv (intrarea din FX_Receptii_H existenta).
#       Flux 2: IDRH = NULL (nu exista inca intrare in istoric).
#       Prezent in: stg_Ord, FX_ORD (MariaDB + Access), tmpFX_ORD.
#       NU apare in PART / TBL / ATT / DOC.
#   - _validate_ord_header: validare IDRH (opt_int).
#   - _stg_insert_ord: INSERT include IDRH.
#   - _commit_add INSERT FX_ORD: include IDRH.
#   - _commit_mod UPDATE FX_ORD: include IDRH.
#   - save_staging / update_staging: logger.info logheaza IDRH.
#
# v5 vs v4:
#   - FX_ORD.IDRR  (nullable): link la FX_Receptii_R (receptia reala parinte).
#       Flux 1 (date existente FOREXE) → IDRR pozitiv.
#       Flux 2 (creat in AVACONT, receptia nu exista inca) → IDRR = NULL.
#   - FX_ORD_TBL.IDRP (nullable): link la FX_Receptii_Plati (randul de plata).
#       Flux 1 → IDRP pozitiv (referinta read-only, ORD nu modifica RecPl).
#       Flux 2 → IDRP = NULL.
#       Un IDRP trebuie sa fie unic per ORD — constrangere gestionata de Access.
#   - FX_ORD_TBL.IDRD eliminat (coloana stearsa din Access si MariaDB).
#   - stg_Ord.IDRR adaugat (corespunde FX_ORD.IDRR).
#   - stg_OrdTbl.IDRP adaugat, stg_OrdTbl.IDRD eliminat.
#   - _validate_ord_header: validare IDRR (opt_int).
#   - _validate_tbl: validare IDRP (opt_int), eliminata validarea IDRD.
#   - _stg_insert_ord: INSERT include IDRR.
#   - _stg_insert_tbls: INSERT include IDRP, exclude IDRD.
#   - _commit_add INSERT FX_ORD: include IDRR.
#   - _commit_add INSERT FX_ORD_TBL: include IDRP, exclude IDRD.
#   - _commit_mod UPDATE FX_ORD: IDRD inlocuit cu IDRR.
#   - _sync_tbl UPDATE/INSERT FX_ORD_TBL: include IDRP, exclude IDRD.
#   - _patch_tbl INSERT FX_ORD_TBL: include IDRP, exclude IDRD.
#
# NOTA ARHITECTURA — relatia ORD - Receptii:
#   ORD (header) → FX_Receptii_R  prin IDRR (receptia reala, nullable).
#   ORD (header) → FX_Receptii_H  prin IDRH (istoricul receptiei, nullable).
#     IDRR si IDRH sunt FK independente — pot fi ambele prezente (flux 1),
#     ambele NULL (flux 2), sau oricare din cele doua singur.
#   ORD_TBL (rand indicator) → FX_Receptii_Plati prin IDRP (randul de plata
#     asociat indicatorului, nullable). FX_ORD_TBL.Valoare = ValoareAsociata
#     din RecPl pentru acel rand. ORD NU scrie in FX_Receptii_Plati —
#     IDRP este exclusiv referinta de citire.

import logging
import time
import uuid
from typing import Optional, Tuple

import mysql.connector
import mysql.connector.errors

from flask import Blueprint, jsonify, request
from utils.database import get_db_connection
from utils.security import require_api_key

ord_bp = Blueprint("ord", __name__)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SECTIUNEA 1 — CONSTANTE
# ---------------------------------------------------------------------------

MAX_PAYLOAD_BYTES    = 2 * 1024 * 1024   # 2 MB
MAX_PARTS            = 50
MAX_TBLS             = 500
MAX_ATTS             = 100
MAX_DOCS             = 100
MAX_RECS             = 2000   # max randuri FX_ORD_TBL_REC per payload
MAX_DEADLOCK_RETRIES = 3
DEADLOCK_RETRY_SLEEP = 0.2               # seconds (se inmulteste cu attempt)

ERRNO_DEADLOCK       = 1213
ERRNO_LOCK_TIMEOUT   = 1205

# ===========================================================================
# SECTIUNEA 2 — PARSARE STRICTA
# ===========================================================================

def _strict_bool(v, field: str) -> int:
    """
    Parseaza un boolean Access (0/1/-1).
    Access trimite -1 pentru True, 0 pentru False.
    Returneaza 1 sau 0. Raise daca null/gol/invalid.
    """
    if v is None or (isinstance(v, str) and v.strip() == ""):
        raise ValueError(f"Camp '{field}': null sau gol (0/1 obligatoriu)")
    try:
        result = int(v)
    except (TypeError, ValueError):
        raise ValueError(f"Camp '{field}': '{v}' nu este 0/1 valid")
    if result == -1:   # Access TRUE
        return 1
    if result not in (0, 1):
        raise ValueError(f"Camp '{field}': {result} trebuie sa fie 0 sau 1")
    return result


def _strict_int(v, field: str) -> int:
    """Parseaza int, reject null/gol/non-numeric."""
    if v is None or (isinstance(v, str) and v.strip() == ""):
        raise ValueError(f"Camp '{field}': null sau gol (int obligatoriu)")
    try:
        return int(v)
    except (TypeError, ValueError):
        raise ValueError(f"Camp '{field}': '{v}' nu este int valid")


def _strict_pos_int(v, field: str) -> int:
    """Parseaza int strict pozitiv (> 0). Reject 0, negativ, null."""
    result = _strict_int(v, field)
    if result <= 0:
        raise ValueError(f"Camp '{field}': {result} trebuie sa fie > 0")
    return result


def _strict_float(v, field: str) -> float:
    """Parseaza float, reject null/gol/non-numeric."""
    if v is None or (isinstance(v, str) and v.strip() == ""):
        raise ValueError(f"Camp '{field}': null sau gol (float obligatoriu)")
    try:
        return float(v)
    except (TypeError, ValueError):
        raise ValueError(f"Camp '{field}': '{v}' nu este float valid")


def _strict_str(v, field: str) -> str:
    """Parseaza string, reject null. String gol admis."""
    if v is None:
        raise ValueError(f"Camp '{field}': null (string obligatoriu)")
    return str(v)


def _strict_str_nonempty(v, field: str) -> str:
    """Parseaza string non-gol (dupa strip). Reject null si whitespace-only."""
    s = _strict_str(v, field)
    if s.strip() == "":
        raise ValueError(f"Camp '{field}': string gol (valoare obligatorie)")
    return s


def _opt_int(v, field: str) -> Optional[int]:
    """
    Camp int optional (FK nullable):
      None   → NULL (FK neset)
      0      → NULL (FK neset — conventia Access pentru camp gol)
      int    → int
      string gol → raise (trimite null explicit, nu string gol)
      non-numeric → raise
    """
    if v is None:
        return None
    if isinstance(v, str) and v.strip() == "":
        raise ValueError(
            f"Camp optional '{field}': string gol invalid — trimite null sau int"
        )
    try:
        result = int(v)
        return result if result != 0 else None
    except (TypeError, ValueError):
        raise ValueError(f"Camp optional '{field}': '{v}' nu este int valid")


def _opt_str(v) -> Optional[str]:
    """None ramane None. Orice altceva devine str."""
    return None if v is None else str(v)


def _mariadb_pk(v, field: str, tip: str) -> int:
    """
    Valideaza un camp MariaDB PK conform regulii ADD/MOD:
      ADD: trebuie < 0 (rand nou — niciodata pozitiv la adaugare initiala)
      MOD: poate fi < 0 (rand nou adaugat in MOD) sau > 0 (rand existent)
      Niciodata 0.
    """
    result = _strict_int(v, field)
    if result == 0:
        raise ValueError(
            f"Camp '{field}': 0 invalid — trebuie < 0 (nou) sau > 0 (existent)"
        )
    if tip == "ADD" and result > 0:
        raise ValueError(
            f"Camp '{field}': {result} > 0 invalid la ADD — "
            "la adaugare MariaDB PK trebuie sa fie negativ"
        )
    return result


def _resolve_fk(tmp_id_part: int, tmp_to_real: dict, entity: str) -> int:
    """
    Rezolva TmpID_OrdPart → IDORDPARTP real (MariaDB auto-increment).
    Folosit pentru TBL unde TmpID_OrdPart este obligatoriu (NOT NULL).
    Raise daca TmpID lipseste din map — indica inconsistenta staging vs commit.
    """
    logger.debug(
        f"[RESOLVE_FK] entity={entity} tmp_id_part={tmp_id_part} "
        f"map_keys={list(tmp_to_real.keys())}"
    )
    idordpartp = tmp_to_real.get(tmp_id_part)
    if not idordpartp:
        raise ValueError(
            f"{entity}: TmpID_OrdPart={tmp_id_part} nu exista in map PART. "
            "Inconsistenta intre staging si commit."
        )
    return idordpartp


def _resolve_fk_opt(tmp_id_part: int, tmp_to_real: dict, entity: str) -> int:
    """
    Identic cu _resolve_fk dar verifica `is None` in loc de `not`.
    Apelat exclusiv cand tmp_id_part is not None (ATT/DOC cu TmpID_OrdPart optional).
    IDORDPARTP = 0 ar fi invalid, deci `is None` e verificarea corecta.
    """
    idordpartp = tmp_to_real.get(tmp_id_part)
    if idordpartp is None:
        raise ValueError(
            f"{entity}: TmpID_OrdPart={tmp_id_part} nu exista in map PART. "
            "Inconsistenta intre staging si commit."
        )
    return idordpartp


# ===========================================================================
# SECTIUNEA 3 — VALIDARE PAYLOAD
# ===========================================================================

def _check_content_length():
    """Reject payload > 2 MB inainte de parsare JSON."""
    if request.content_length and request.content_length > MAX_PAYLOAD_BYTES:
        raise ValueError(
            f"Payload prea mare: {request.content_length:,} bytes "
            f"(maxim {MAX_PAYLOAD_BYTES:,} bytes = 2 MB)"
        )


def _validate_ord_header(ord_: dict, tip: str):
    """
    Valideaza header-ul ORD (campurile din FX_ORD / stg_Ord).

    IDRR (nullable):
      Flux 1 (date FOREXE existente): IDRR > 0 (receptia reala).
      Flux 2 (creat in AVACONT): IDRR = NULL (receptia nu exista inca).
      Serverul nu valideaza existenta IDRR in FX_Receptii_R —
      responsabilitatea consistentei apartine Access/VBA.

    IDRH (nullable):
      FK catre FX_Receptii_H (istoricul receptiei).
      Flux 1: IDRH > 0 (intrarea din registrul istoric existenta).
      Flux 2: IDRH = NULL (nu exista inca intrare in istoric).
      Independent de IDRR — pot coexista, pot fi ambele NULL, sau oricare singur.
      Serverul nu valideaza existenta IDRH in FX_Receptii_H —
      responsabilitatea consistentei apartine Access/VBA.
    """
    if not isinstance(ord_, dict):
        raise ValueError("'ord' trebuie sa fie dict")

    # IDORDP: MariaDB PK — ADD < 0 (rand nou), MOD > 0 (rand existent)
    idordp = _strict_int(ord_.get("IDORDP"), "ord.IDORDP")
    if tip == "ADD" and idordp >= 0:
        raise ValueError(f"ord.IDORDP={idordp}: la ADD trebuie sa fie negativ")
    if tip == "MOD" and idordp <= 0:
        raise ValueError(f"ord.IDORDP={idordp}: la MOD trebuie sa fie > 0")

    # IDORD: Access PK, mereu > 0 (pre-calculat de VBA)
    _strict_pos_int(ord_.get("IDORD"), "ord.IDORD")

    # Campuri obligatorii header
    _strict_int(ord_.get("NrORD"),             "ord.NrORD")
    _strict_str_nonempty(ord_.get("DataORD"),  "ord.DataORD")
    _strict_str_nonempty(ord_.get("Comp"),     "ord.Comp")
    _strict_bool(ord_.get("Incarcat"),         "ord.Incarcat")
    _strict_bool(ord_.get("Preluat"),          "ord.Preluat")

    # IDRR: nullable — NULL flux 2, pozitiv flux 1
    _opt_int(ord_.get("IDRR"), "ord.IDRR")

    # IDRH: nullable — FK catre FX_Receptii_H (istoricul receptiei, v6)
    _opt_int(ord_.get("IDRH"), "ord.IDRH")


def _validate_part(p: dict, idx: int, tip: str):
    """Valideaza un rand PART (beneficiar plata)."""
    pfx = f"parts[{idx}]"

    _strict_pos_int(p.get("TmpID"),     f"{pfx}.TmpID")

    # IDORDPARTP: MariaDB PK — ADD < 0, MOD < 0 (nou) sau > 0 (existent)
    _mariadb_pk(p.get("IDORDPARTP"),    f"{pfx}.IDORDPARTP", tip)

    # IDORDPART: Access PK, mereu > 0
    _strict_pos_int(p.get("IDORDPART"), f"{pfx}.IDORDPART")

    _strict_str_nonempty(p.get("DenBene"),  f"{pfx}.DenBene")
    _strict_str(p.get("Counter"),           f"{pfx}.Counter")
    _strict_str(p.get("CodFiscal"),         f"{pfx}.CodFiscal")
    _strict_str(p.get("ContIBAN"),          f"{pfx}.ContIBAN")
    _strict_str(p.get("Banca"),             f"{pfx}.Banca")
    _opt_str(p.get("CodPartener"))
    _opt_int(p.get("IdPartener"),           f"{pfx}.IdPartener")


def _validate_tbl(t: dict, idx: int, valid_tmpid_set: set, tip: str):
    """
    Valideaza un rand TBL (indicator/clasificatie per beneficiar).

    TmpID_OrdPart: obligatoriu, trebuie sa corespunda unui TmpID din parts.
    IDRP (nullable):
      Flux 1: IDRP > 0 (referinta read-only la FX_Receptii_Plati).
      Flux 2: IDRP = NULL.
      Serverul NU valideaza existenta IDRP in FX_Receptii_Plati —
      responsabilitatea consistentei apartine Access/VBA.
      Unicitatea IDRP per ORD este gestionata de Access, nu de MariaDB.
    IDRD: eliminat din v5 — nu se mai trimite si nu se mai valideaza.
    """
    pfx = f"tbls[{idx}]"

    _strict_pos_int(t.get("TmpID"), f"{pfx}.TmpID")

    # TmpID_OrdPart: FK catre PART parinte — obligatoriu in TBL
    tmp_id_part = _strict_pos_int(t.get("TmpID_OrdPart"), f"{pfx}.TmpID_OrdPart")
    if tmp_id_part not in valid_tmpid_set:
        raise ValueError(
            f"{pfx}.TmpID_OrdPart={tmp_id_part} nu corespunde "
            "niciunui TmpID din lista parts"
        )

    # IDORDTBLP: MariaDB PK
    _mariadb_pk(t.get("IDORDTBLP"), f"{pfx}.IDORDTBLP", tip)

    # IDORDTBL: Access PK, mereu > 0
    _strict_pos_int(t.get("IDORDTBL"), f"{pfx}.IDORDTBL")

    _strict_str_nonempty(t.get("CodAI"),       f"{pfx}.CodAI")
    _strict_str(t.get("CodAngajament"),         f"{pfx}.CodAngajament")
    _strict_str(t.get("CodIndicator"),          f"{pfx}.CodIndicator")
    _strict_str(t.get("CodSSI"),                f"{pfx}.CodSSI")
    _strict_float(t.get("TotalReceptii"),       f"{pfx}.TotalReceptii")
    _strict_float(t.get("PlatiAnt"),            f"{pfx}.PlatiAnt")
    _strict_float(t.get("Valoare"),             f"{pfx}.Valoare")
    _strict_float(t.get("Ramas"),               f"{pfx}.Ramas")
    _opt_int(t.get("IdClsf"),                   f"{pfx}.IdClsf")
    _opt_int(t.get("IdClsfAcc"),                f"{pfx}.IdClsfAcc")
    # IDRP: nullable — NULL flux 2, pozitiv flux 1
    _opt_int(t.get("IDRP"),                     f"{pfx}.IDRP")
    # IDRD: eliminat din v5 — nu se mai valideaza


def _validate_tbl_rec(r: dict, idx: int, valid_tbl_tmpid_set: set):
    pfx = f"tbl_recs[{idx}]"

    _strict_pos_int(r.get("TmpID"),        f"{pfx}.TmpID")

    tmp_id_tbl = _strict_pos_int(r.get("TmpID_OrdTbl"), f"{pfx}.TmpID_OrdTbl")
    if tmp_id_tbl not in valid_tbl_tmpid_set:
        raise ValueError(
            f"{pfx}.TmpID_OrdTbl={tmp_id_tbl} nu corespunde niciunui TmpID din tbls"
        )

    _strict_int(r.get("IDORDREC"),  f"{pfx}.IDORDREC")   # negativ=ADD, pozitiv=EDIT
    _strict_int(r.get("IDORDRECP"), f"{pfx}.IDORDRECP")   # negativ=ADD, pozitiv=EDIT
    _strict_pos_int(r.get("IDRP"),  f"{pfx}.IDRP")
    _strict_float(r.get("Valoare"), f"{pfx}.Valoare")


def _validate_att(a: dict, idx: int, valid_tmpid_set: set, tip: str):
    """
    Valideaza un rand ATT (atasament imagine).
    TmpID_OrdPart: optional — ATT poate fi global (fara PART parinte).
    """
    pfx = f"atts[{idx}]"

    _strict_pos_int(a.get("TmpID"), f"{pfx}.TmpID")

    # TmpID_OrdPart: optional in ATT (poate fi atasat la nivel ORD, nu PART)
    tmp_id_part = _opt_int(a.get("TmpID_OrdPart"), f"{pfx}.TmpID_OrdPart")
    if tmp_id_part is not None and tmp_id_part not in valid_tmpid_set:
        raise ValueError(
            f"{pfx}.TmpID_OrdPart={tmp_id_part} nu corespunde "
            "niciunui TmpID din lista parts"
        )

    # IDORDATTP: MariaDB PK
    _mariadb_pk(a.get("IDORDATTP"), f"{pfx}.IDORDATTP", tip)

    # IDORDATT: Access PK, mereu > 0
    _strict_pos_int(a.get("IDORDATT"), f"{pfx}.IDORDATT")

    _strict_str_nonempty(a.get("Imagine"), f"{pfx}.Imagine")


def _validate_doc(d: dict, idx: int, valid_tmpid_set: set, tip: str):
    """
    Valideaza un rand DOC (document justificativ).
    TmpID_OrdPart: optional — DOC poate fi global (fara PART parinte).
    TipDoc == 'text' → NumeDoc poate fi null (text liber fara fisier).
    TipDoc != 'text' → NumeDoc obligatoriu (nume fisier).
    """
    logger.debug(f"Validating DOC index={idx} data={d}")
    pfx = f"docs[{idx}]"

    _strict_pos_int(d.get("TmpID"), f"{pfx}.TmpID")

    # TmpID_OrdPart: optional in DOC
    tmp_id_part = _opt_int(d.get("TmpID_OrdPart"), f"{pfx}.TmpID_OrdPart")
    if tmp_id_part is not None and tmp_id_part not in valid_tmpid_set:
        raise ValueError(
            f"{pfx}.TmpID_OrdPart={tmp_id_part} nu corespunde "
            "niciunui TmpID din lista parts"
        )

    # IDORDDOCP: MariaDB PK
    _mariadb_pk(d.get("IDORDDOCP"), f"{pfx}.IDORDDOCP", tip)

    # IDORDDOC: Access PK, mereu > 0
    _strict_pos_int(d.get("IDORDDOC"), f"{pfx}.IDORDDOC")

    tip_doc = _strict_str_nonempty(d.get("TipDoc"), f"{pfx}.TipDoc")
    if tip_doc != "text":
        # Document real — nume fisier obligatoriu
        _strict_str_nonempty(d.get("NumeDoc"), f"{pfx}.NumeDoc")
    else:
        # Text liber — NumeDoc poate lipsi
        _opt_str(d.get("NumeDoc"))

    # DocJust: optional pe ambele tipuri
    _opt_str(d.get("DocJust"))


def _validate_payload(data: dict, tip: str):
    """
    Valideaza complet payload-ul primit de la VBA.
    Ordinea: header → limits → parts → valid_tmpid_set → tbls/atts/docs → tbl_recs.
    valid_tmpid_set este construit din TmpID-urile PART validate,
    si folosit pentru cross-validarea TmpID_OrdPart din TBL/ATT/DOC.
    valid_tbl_tmpid_set este construit din TmpID-urile TBL validate,
    si folosit pentru cross-validarea TmpID_OrdTbl din TBL_REC.
    """
    if not isinstance(data, dict):
        raise ValueError("Payload trebuie sa fie dict")
    if "ord" not in data:
        raise ValueError("Cheie 'ord' lipsa din payload")

    _validate_ord_header(data["ord"], tip)

    for key in ("parts", "tbls", "atts", "docs", "tbl_recs"):
        if not isinstance(data.get(key, []), list):
            raise ValueError(f"'{key}' trebuie sa fie list")

    parts = data.get("parts", [])
    tbls  = data.get("tbls",  [])
    atts  = data.get("atts",  [])
    docs  = data.get("docs",  [])

    if len(parts) > MAX_PARTS:
        raise ValueError(f"Prea multe parts: {len(parts)} (maxim {MAX_PARTS})")
    if len(tbls)  > MAX_TBLS:
        raise ValueError(f"Prea multe tbls: {len(tbls)} (maxim {MAX_TBLS})")
    if len(atts)  > MAX_ATTS:
        raise ValueError(f"Prea multe atts: {len(atts)} (maxim {MAX_ATTS})")
    if len(docs)  > MAX_DOCS:
        raise ValueError(f"Prea multe docs: {len(docs)} (maxim {MAX_DOCS})")

    tbl_recs = data.get("tbl_recs", [])
    if len(tbl_recs) > MAX_RECS:
        raise ValueError(f"Prea multe tbl_recs: {len(tbl_recs)} (maxim {MAX_RECS})")

    for i, p in enumerate(parts):
        _validate_part(p, i, tip)

    # Construieste set TmpID PART pentru cross-validare copii
    valid_tmpid_set = {
        _strict_pos_int(p["TmpID"], f"parts[{i}].TmpID")
        for i, p in enumerate(parts)
    }

    # Verifica unicitate TmpID in parts (fiecare rand tmp are ID unic)
    tmp_ids = [_strict_pos_int(p["TmpID"], "TmpID") for p in parts]
    if len(tmp_ids) != len(set(tmp_ids)):
        raise ValueError("TmpID duplicat in 'parts'")

    for i, t in enumerate(tbls):
        _validate_tbl(t, i, valid_tmpid_set, tip)

    for i, a in enumerate(atts):
        _validate_att(a, i, valid_tmpid_set, tip)

    for i, d in enumerate(docs):
        _validate_doc(d, i, valid_tmpid_set, tip)

    valid_tbl_tmpid_set = {
        _strict_pos_int(t["TmpID"], f"tbls[{i}].TmpID")
        for i, t in enumerate(tbls)
    }
    for i, r in enumerate(tbl_recs):
        _validate_tbl_rec(r, i, valid_tbl_tmpid_set)


# ===========================================================================
# SECTIUNEA 4 — DB HELPERS (conexiune + retry)
# ===========================================================================

def _get_conn_cursor(data: dict):
    """Deschide conexiune si cursor dictionary pentru db_name din payload."""
    conn   = get_db_connection(data.get("db_name"))
    cursor = conn.cursor(dictionary=True)
    return conn, cursor


def _close(conn, cursor):
    """Inchide cursor si conexiune silentios (ignore erori la close)."""
    for obj in (cursor, conn):
        if obj:
            try:
                obj.close()
            except Exception:
                pass


def _run_with_retry(operation, data: dict):
    """
    Executa operation(cursor) → result in tranzactie explicita.
    Retry automat la deadlock (errno 1213) sau lock timeout (errno 1205),
    cu sleep progresiv: attempt * DEADLOCK_RETRY_SLEEP secunde.

    IntegrityError (FK violation, duplicate key) → raise imediat, fara retry
    (sunt erori de date, nu de concurenta).

    Orice alta exceptie → rollback + raise.
    """
    last_err = None
    for attempt in range(1, MAX_DEADLOCK_RETRIES + 1):
        conn = cursor = None
        try:
            conn, cursor = _get_conn_cursor(data)
            result = operation(cursor)
            conn.commit()
            return result

        except mysql.connector.errors.IntegrityError as e:
            # Eroare de integritate (FK, UNIQUE) — nu are sens sa reincerci
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
            raise ValueError(f"Eroare integritate DB: {e.msg}") from e

        except mysql.connector.errors.DatabaseError as e:
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass

            if e.errno in (ERRNO_DEADLOCK, ERRNO_LOCK_TIMEOUT):
                last_err = e
                if attempt < MAX_DEADLOCK_RETRIES:
                    sleep_time = DEADLOCK_RETRY_SLEEP * attempt
                    logger.warning(
                        f"[RETRY] errno={e.errno} attempt={attempt}/{MAX_DEADLOCK_RETRIES} "
                        f"sleep={sleep_time:.2f}s"
                    )
                    time.sleep(sleep_time)
                    continue
                raise ValueError(
                    f"Deadlock/lock timeout dupa {MAX_DEADLOCK_RETRIES} "
                    f"incercari: {e.msg}"
                ) from e

            raise

        except Exception:
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
            raise

        finally:
            _close(conn, cursor)

    raise last_err


# ===========================================================================
# SECTIUNEA 5 — STAGING INSERT
# ===========================================================================
#
# Toate functiile _stg_insert_* scriu datele din payload in tabelele stg_*.
# Apelate in ordinea: Ord → Parts → Tbls → Atts → Docs → TblRecs
# (ordinea impusa de FK-urile din staging).
# Toate ruleaza in aceeasi tranzactie prin _run_with_retry.

def _stg_insert_ord(cursor, token: str, tip: str, data: dict):
    """
    Insereaza header-ul ORD in stg_Ord.

    IDRR: nullable — NULL pentru flux 2 (receptia nu exista inca),
    pozitiv pentru flux 1 (receptia deja existenta in FOREXE).

    IDRH: nullable — FK catre FX_Receptii_H (istoricul receptiei, v6).
    NULL pentru flux 2, pozitiv pentru flux 1.
    Independent de IDRR.

    _opt_int converteste 0 → None pentru ambele, deci Access poate trimite
    0 sau null pentru campurile nesetate.
    """
    ord_ = data["ord"]
    cursor.execute("""
        INSERT INTO stg_Ord
            (Token, TipOperatie, IDORD, IDORDP, IDDF, IDRR, IDRH,
             NrORD, DataORD, Comp, CUAL, IdUnitate,
             Incarcat, Preluat, CodAngajament)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (
        token, tip,
        _strict_pos_int(ord_["IDORD"],        "ord.IDORD"),
        _strict_int(ord_["IDORDP"],           "ord.IDORDP"),
        _opt_int(ord_.get("IDDF"),            "ord.IDDF"),
        _opt_int(ord_.get("IDRR"),            "ord.IDRR"),   # v5: IDRR
        _opt_int(ord_.get("IDRH"),            "ord.IDRH"),   # v6: IDRH adaugat
        _strict_int(ord_["NrORD"],            "ord.NrORD"),
        _strict_str_nonempty(ord_["DataORD"], "ord.DataORD"),
        _strict_str_nonempty(ord_["Comp"],    "ord.Comp"),
        _opt_str(ord_.get("CUAL")),
        _opt_int(ord_.get("IdUnitate"),       "ord.IdUnitate"),
        _strict_bool(ord_.get("Incarcat"),    "ord.Incarcat"),
        _strict_bool(ord_.get("Preluat"),     "ord.Preluat"),
        _strict_str_nonempty(ord_.get("CodAngajament"), "ord.CodAngajament"),
    ))

    logger.debug(
        f"[STG][ORD] token={token} tip={tip} "
        f"IDORD={ord_['IDORD']} IDORDP={ord_['IDORDP']} "
        f"IDRR={ord_.get('IDRR')} IDRH={ord_.get('IDRH')}"
    )


def _stg_insert_parts(cursor, token: str, parts: list) -> dict:
    """
    Insereaza randurile PART (beneficiari) in stg_OrdPart.

    Returneaza map {TmpID: StgPartID} unde StgPartID este autoincrement-ul
    generat de MariaDB (lastrowid) la fiecare INSERT.
    Map-ul este propagat de _stg_insert_all catre _stg_insert_tbls /
    _stg_insert_atts / _stg_insert_docs pentru a popula coloana FK
    StgPartID din tabelele copil (NOT NULL, fara default value).
    """
    tmpid_to_stgpartid = {}   # TmpID (int) → StgPartID (int, lastrowid)

    for p in parts:
        tmp_id = _strict_pos_int(p["TmpID"], "TmpID")
        cursor.execute("""
            INSERT INTO stg_OrdPart
                (Token, TmpID, IDORDPART, IDORDPARTP,
                 Counter, DenBene, CodFiscal, ContIBAN,
                 Banca, CodPartener, IdPartener)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            token,
            tmp_id,
            _strict_pos_int(p["IDORDPART"],    "IDORDPART"),
            _strict_int(p["IDORDPARTP"],       "IDORDPARTP"),
            _strict_str(p["Counter"],          "Counter"),
            _strict_str_nonempty(p["DenBene"], "DenBene"),
            _strict_str(p["CodFiscal"],        "CodFiscal"),
            _strict_str(p["ContIBAN"],         "ContIBAN"),
            _strict_str(p["Banca"],            "Banca"),
            _opt_str(p.get("CodPartener")),
            _opt_int(p.get("IdPartener"),      "IdPartener"),
        ))
        tmpid_to_stgpartid[tmp_id] = cursor.lastrowid

    logger.debug(
        f"[STG][PART] token={token} count={len(parts)} "
        f"stgpart_map={tmpid_to_stgpartid}"
    )
    return tmpid_to_stgpartid


def _stg_insert_tbls(cursor, token: str, tbls: list, tmpid_to_stgpartid: dict):
    """
    Insereaza randurile TBL (indicatori/clasificatii) in stg_OrdTbl.

    tmpid_to_stgpartid: map {TmpID: StgPartID} returnat de _stg_insert_parts.
    StgPartID este FK NOT NULL catre stg_OrdPart.StgPartID — obligatoriu
    pentru fiecare rand TBL. Raise daca TmpID_OrdPart lipseste din map
    (indica inconsistenta intre payload parts si tbls).

    IDRP: nullable — FK read-only catre FX_Receptii_Plati.
      NULL = flux 2 (receptia nu exista inca).
      > 0  = flux 1 (randul RecPl existent, referinta de citire).
    IDRD: eliminat din v5 — coloana stearsa din FX_ORD_TBL si stg_OrdTbl.
    TmpID_OrdPart: obligatoriu in TBL (FK catre PART parinte din staging).
    """
    for t in tbls:
        tmp_id_part = _strict_pos_int(t["TmpID_OrdPart"], "TmpID_OrdPart")
        stg_part_id = tmpid_to_stgpartid.get(tmp_id_part)
        if stg_part_id is None:
            raise ValueError(
                f"stg_insert_tbls: TmpID_OrdPart={tmp_id_part} absent din "
                "map stg_OrdPart. Inconsistenta intre parts si tbls din payload."
            )
        cursor.execute("""
            INSERT INTO stg_OrdTbl
                (Token, TmpID, TmpID_OrdPart, StgPartID,
                 IDORDTBL, IDORDTBLP,
                 CodAI, CodAngajament, CodIndicator, CodSSI,
                 TotalReceptii, PlatiAnt, Valoare, Ramas,
                 IdClsf, IdClsfAcc, Explicatie, IDRP)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            token,
            _strict_pos_int(t["TmpID"],          "TmpID"),
            tmp_id_part,
            stg_part_id,                              # FK → stg_OrdPart.StgPartID
            _strict_pos_int(t["IDORDTBL"],       "IDORDTBL"),
            _strict_int(t["IDORDTBLP"],          "IDORDTBLP"),
            _strict_str_nonempty(t["CodAI"],     "CodAI"),
            _strict_str(t["CodAngajament"],      "CodAngajament"),
            _strict_str(t["CodIndicator"],       "CodIndicator"),
            _strict_str(t["CodSSI"],             "CodSSI"),
            _strict_float(t["TotalReceptii"],    "TotalReceptii"),
            _strict_float(t["PlatiAnt"],         "PlatiAnt"),
            _strict_float(t["Valoare"],          "Valoare"),
            _strict_float(t["Ramas"],            "Ramas"),
            _opt_int(t.get("IdClsf"),            "IdClsf"),
            _opt_int(t.get("IdClsfAcc"),         "IdClsfAcc"),
            _opt_str(t.get("Explicatie")),
            _opt_int(t.get("IDRP"),              "IDRP"),    # v5: IDRP adaugat, IDRD eliminat
        ))
    logger.debug(f"[STG][TBL] token={token} count={len(tbls)}")


def _stg_insert_atts(cursor, token: str, atts: list, tmpid_to_stgpartid: dict):
    """
    Insereaza randurile ATT (atasamente imagini) in stg_OrdAtt.
    TmpID_OrdPart: optional — None inseamna ATT global (nivel ORD, nu PART).
    StgPartID: NULL daca ATT e global, altfel FK → stg_OrdPart.StgPartID.
    """
    for a in atts:
        tmp_id_part = _opt_int(a.get("TmpID_OrdPart"), "TmpID_OrdPart")
        if tmp_id_part is not None:
            stg_part_id = tmpid_to_stgpartid.get(tmp_id_part)
            if stg_part_id is None:
                raise ValueError(
                    f"stg_insert_atts: TmpID_OrdPart={tmp_id_part} absent din "
                    "map stg_OrdPart. Inconsistenta intre parts si atts din payload."
                )
        else:
            stg_part_id = None
        cursor.execute("""
            INSERT INTO stg_OrdAtt
                (Token, TmpID, TmpID_OrdPart, StgPartID,
                 IDORDATT, IDORDATTP, Imagine)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (
            token,
            _strict_pos_int(a["TmpID"],        "TmpID"),
            tmp_id_part,
            stg_part_id,                           # NULL daca ATT global
            _strict_pos_int(a["IDORDATT"],     "IDORDATT"),
            _strict_int(a["IDORDATTP"],        "IDORDATTP"),
            _strict_str_nonempty(a["Imagine"], "Imagine"),
        ))
    logger.debug(f"[STG][ATT] token={token} count={len(atts)}")


def _stg_insert_docs(cursor, token: str, docs: list, tmpid_to_stgpartid: dict):
    """
    Insereaza randurile DOC (documente justificative) in stg_OrdDoc.
    TmpID_OrdPart: optional — None inseamna DOC global (nivel ORD, nu PART).
    StgPartID: NULL daca DOC e global, altfel FK → stg_OrdPart.StgPartID.
    TipDoc == 'text' → NumeDoc poate fi null.
    """
    for d in docs:
        tmp_id_part = _opt_int(d.get("TmpID_OrdPart"), "TmpID_OrdPart")
        if tmp_id_part is not None:
            stg_part_id = tmpid_to_stgpartid.get(tmp_id_part)
            if stg_part_id is None:
                raise ValueError(
                    f"stg_insert_docs: TmpID_OrdPart={tmp_id_part} absent din "
                    "map stg_OrdPart. Inconsistenta intre parts si docs din payload."
                )
        else:
            stg_part_id = None
        cursor.execute("""
            INSERT INTO stg_OrdDoc
                (Token, TmpID, TmpID_OrdPart, StgPartID,
                 IDORDDOC, IDORDDOCP,
                 DocJust, NumeDoc, TipDoc)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            token,
            _strict_pos_int(d["TmpID"],            "TmpID"),
            tmp_id_part,
            stg_part_id,                               # NULL daca DOC global
            _strict_pos_int(d["IDORDDOC"],         "IDORDDOC"),
            _strict_int(d["IDORDDOCP"],            "IDORDDOCP"),
            _strict_str_nonempty(d.get("DocJust"), "DocJust"),
            _opt_str(d.get("NumeDoc")),
            _strict_str_nonempty(d["TipDoc"],      "TipDoc"),
        ))
    logger.debug(f"[STG][DOC] token={token} count={len(docs)}")


def _stg_insert_tbl_recs(cursor, token: str, tbl_recs: list):
    """
    Insereaza randurile TBL_REC (plati individuale per TBL) in stg_OrdTblRec.
    TmpID_OrdTbl: FK catre tmpFX_ORD_TBL.ID (parintele TBL din Access).
    IDORDREC: Access PK (pozitiv = pre-calculat de VBA, negativ = generat de server).
    IDRP: FK catre FX_Receptii_Plati (obligatoriu pozitiv).
    """
    for r in tbl_recs:
        cursor.execute("""
            INSERT INTO stg_OrdTblRec
                (Token, TmpID, TmpID_OrdTbl, IDORDREC, IDORDRECP, IDRP, Valoare)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (
            token,
            _strict_pos_int(r["TmpID"],         "TmpID"),
            _strict_pos_int(r["TmpID_OrdTbl"],  "TmpID_OrdTbl"),
            _strict_int(r["IDORDREC"],          "IDORDREC"),
            _strict_int(r["IDORDRECP"],         "IDORDRECP"),
            _strict_pos_int(r["IDRP"],          "IDRP"),
            _strict_float(r["Valoare"],         "Valoare"),
        ))
    logger.debug(f"[STG][REC] token={token} count={len(tbl_recs)}")


def _stg_insert_all(cursor, token: str, tip: str, data: dict):
    """
    Insereaza complet un payload in staging (toate tabelele stg_Ord*).
    Ordinea este impusa de FK: Ord → Parts → Tbls → Atts → Docs → TblRecs.
    Totul ruleaza in aceeasi tranzactie (apelant: _run_with_retry).

    _stg_insert_parts returneaza map {TmpID: StgPartID} care este
    propagat explicit catre TBL/ATT/DOC pentru popularea coloanei
    FK StgPartID (NOT NULL in stg_OrdTbl, nullable in stg_OrdAtt/Doc).
    """
    _stg_insert_ord(cursor,   token, tip, data)
    tmpid_to_stgpartid = _stg_insert_parts(cursor, token, data.get("parts", []))
    _stg_insert_tbls(cursor,  token, data.get("tbls",  []), tmpid_to_stgpartid)
    _stg_insert_atts(cursor,  token, data.get("atts",  []), tmpid_to_stgpartid)
    _stg_insert_docs(cursor,  token, data.get("docs",  []), tmpid_to_stgpartid)
    _stg_insert_tbl_recs(cursor, token, data.get("tbl_recs", []))
    logger.info(f"[STG][ALL] token={token} tip={tip} payload inserted in staging")


# ===========================================================================
# SECTIUNEA 6 — COMMIT ADD
# ===========================================================================
#
# _commit_add: executa INSERT-urile in tabelele reale FX_ORD*.
# Apelat din confirm dupa validarea Status=PENDING si TipOperatie=ADD.
# Returneaza dict cu IDORDP real + Map-urile TmpID → MariaDB PK
# pentru toate entitatile (Part_Map, TBL_Map, ATT_Map, DOC_Map, REC_Map).
# Aceste Map-uri sunt trimise inapoi la VBA prin /ord/confirm,
# si folosite de Confirma_Local_ORD pentru a scrie PK-urile MariaDB
# in tabelele tmp* din Access.

def _commit_add(cursor, token: str) -> dict:
    """
    Commit ADD complet: INSERT FX_ORD → PART → TBL → ATT → DOC → TBL_REC.

    FX_ORD.IDRR: scris direct din stg_Ord (nullable, v5).
    FX_ORD.IDRH: scris direct din stg_Ord (nullable, v6).
    FX_ORD_TBL.IDRP: scris direct din stg_OrdTbl (nullable, v5).
    FK PART → ORD: IDORDPARTP rezolvat prin tmp_to_real (TmpID → lastrowid).
    FK TBL/ATT/DOC → PART: rezolvat prin acelasi tmp_to_real.
    FK TBL_REC → TBL: rezolvat prin tbl_to_real (TmpID → IDORDTBLP).
    """
    logger.info(f"[ADD] START token={token}")

    # Citeste header-ul ORD din staging
    cursor.execute("SELECT * FROM stg_Ord WHERE Token=%s", (token,))
    stg_ord = cursor.fetchone()
    if not stg_ord:
        raise ValueError(f"stg_Ord negasit pentru token={token}")

    # -----------------------------------------------------------------------
    # INSERT FX_ORD
    # IDORD: pre-calculat de VBA (Access autonumber predictibil).
    # IDORDP: generat de MariaDB autoincrement → capturat cu lastrowid.
    # IDRR: nullable (v5) — NULL = flux 2, pozitiv = flux 1.
    # IDRH: nullable (v6) — FK catre FX_Receptii_H, independent de IDRR.
    # -----------------------------------------------------------------------
    cursor.execute("""
        INSERT INTO FX_ORD
            (IDORD, IDDF, IDRR, IDRH,
             NrORD, DataORD, Comp, CUAL,
             Incarcat, Preluat, CodAngajament)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (
        _strict_pos_int(stg_ord["IDORD"],            "stg_Ord.IDORD"),
        _opt_int(stg_ord.get("IDDF"),                "IDDF"),
        _opt_int(stg_ord.get("IDRR"),                "IDRR"),   # v5: IDRR
        _opt_int(stg_ord.get("IDRH"),                "IDRH"),   # v6: IDRH
        _strict_int(stg_ord["NrORD"],                "NrORD"),
        stg_ord["DataORD"],
        _strict_str_nonempty(stg_ord["Comp"],        "Comp"),
        _opt_str(stg_ord.get("CUAL")),
        _strict_bool(stg_ord.get("Incarcat"),        "Incarcat"),
        _strict_bool(stg_ord.get("Preluat"),         "Preluat"),
        _strict_str_nonempty(stg_ord.get("CodAngajament"), "CodAngajament"),
    ))
    idordp = cursor.lastrowid
    logger.debug(
        f"[ADD][ORD] IDORDP={idordp} IDORD={stg_ord['IDORD']} "
        f"IDRR={stg_ord.get('IDRR')} IDRH={stg_ord.get('IDRH')}"
    )

    # -----------------------------------------------------------------------
    # INSERT FX_ORD_PART (toate noi la ADD)
    # tmp_to_real: TmpID (Access autonumber din tmpFX_ORD_PART.ID)
    #              → IDORDPARTP (MariaDB autoincrement generat la INSERT)
    # Folosit pentru a rezolva FK PART parinte la copiii TBL/ATT/DOC.
    # -----------------------------------------------------------------------
    cursor.execute(
        "SELECT * FROM stg_OrdPart WHERE Token=%s ORDER BY TmpID", (token,)
    )
    stg_parts   = cursor.fetchall()
    tmp_to_real = {}   # TmpID → IDORDPARTP real (MariaDB)
    part_map    = []

    for p in stg_parts:
        tmp_id = _strict_pos_int(p["TmpID"], "TmpID")
        cursor.execute("""
            INSERT INTO FX_ORD_PART
                (IDORDP, IDORDPART, Counter, DenBene,
                 CodPartener, IdPartener, CodFiscal, ContIBAN, Banca)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            idordp,
            _strict_pos_int(p["IDORDPART"],    "IDORDPART"),
            _strict_str(p["Counter"],          "Counter"),
            _strict_str_nonempty(p["DenBene"], "DenBene"),
            _opt_str(p.get("CodPartener")),
            _opt_int(p.get("IdPartener"),      "IdPartener"),
            _strict_str(p["CodFiscal"],        "CodFiscal"),
            _strict_str(p["ContIBAN"],         "ContIBAN"),
            _strict_str(p["Banca"],            "Banca"),
        ))
        new_idordpartp      = cursor.lastrowid
        tmp_to_real[tmp_id] = new_idordpartp
        part_map.append({"TmpID": tmp_id, "IDORDPARTP": new_idordpartp})

    logger.debug(
        f"[ADD][PART] inserted={len(part_map)} "
        f"tmp_to_real={tmp_to_real}"
    )

    # -----------------------------------------------------------------------
    # INSERT FX_ORD_TBL (toate noi la ADD)
    # IDORDPARTP: rezolvat din tmp_to_real via TmpID_OrdPart.
    # IDRP: nullable — read-only FK catre FX_Receptii_Plati (v5).
    # IDRD: eliminat din v5.
    # -----------------------------------------------------------------------
    cursor.execute(
        "SELECT * FROM stg_OrdTbl WHERE Token=%s ORDER BY TmpID", (token,)
    )
    tbl_map = []
    for t in cursor.fetchall():
        logger.debug(
            f"[ADD][TBL] TmpID={t['TmpID']} "
            f"TmpID_OrdPart={t['TmpID_OrdPart']} "
            f"IDRP={t.get('IDRP')}"
        )
        tmp_id_part = _strict_pos_int(t["TmpID_OrdPart"], "TmpID_OrdPart")
        idordpartp  = _resolve_fk(
            tmp_id_part, tmp_to_real, f"TBL TmpID={t['TmpID']}"
        )
        cursor.execute("""
            INSERT INTO FX_ORD_TBL
                (IDORDP, IDORDPARTP, IDORDTBL,
                 CodAI, CodAngajament, CodIndicator, CodSSI,
                 TotalReceptii, PlatiAnt, Valoare, Ramas,
                 IdClsf, IdClsfAcc, Explicatie, IDRP)
            VALUES (%s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            idordp, idordpartp,
            _strict_pos_int(t["IDORDTBL"],    "IDORDTBL"),
            _strict_str_nonempty(t["CodAI"],  "CodAI"),
            _strict_str(t["CodAngajament"],   "CodAngajament"),
            _strict_str(t["CodIndicator"],    "CodIndicator"),
            _strict_str(t["CodSSI"],          "CodSSI"),
            _strict_float(t["TotalReceptii"], "TotalReceptii"),
            _strict_float(t["PlatiAnt"],      "PlatiAnt"),
            _strict_float(t["Valoare"],       "Valoare"),
            _strict_float(t["Ramas"],         "Ramas"),
            _opt_int(t.get("IdClsf"),         "IdClsf"),
            _opt_int(t.get("IdClsfAcc"),      "IdClsfAcc"),
            _opt_str(t.get("Explicatie")),
            _opt_int(t.get("IDRP"),           "IDRP"),       # v5: IDRP, fara IDRD
        ))
        tbl_map.append({
            "TmpID":     _strict_pos_int(t["TmpID"], "TmpID"),
            "IDORDTBLP": cursor.lastrowid,
        })

    logger.debug(f"[ADD][TBL] inserted={len(tbl_map)}")

    # -----------------------------------------------------------------------
    # INSERT FX_ORD_ATT (toate noi la ADD)
    # TmpID_OrdPart: optional — None = ATT global (nivel ORD).
    # -----------------------------------------------------------------------
    cursor.execute(
        "SELECT * FROM stg_OrdAtt WHERE Token=%s ORDER BY TmpID", (token,)
    )
    att_map = []
    for a in cursor.fetchall():
        tmp_id_part = a["TmpID_OrdPart"]   # None sau int
        idordpartp  = (
            _resolve_fk_opt(tmp_id_part, tmp_to_real, f"ATT TmpID={a['TmpID']}")
            if tmp_id_part is not None
            else None
        )
        cursor.execute("""
            INSERT INTO FX_ORD_ATT
                (IDORDP, IDORDPARTP, IDORDATT, Imagine)
            VALUES (%s, %s, %s, %s)
        """, (
            idordp, idordpartp,
            _strict_pos_int(a["IDORDATT"],     "IDORDATT"),
            _strict_str_nonempty(a["Imagine"], "Imagine"),
        ))
        att_map.append({
            "TmpID":     _strict_pos_int(a["TmpID"], "TmpID"),
            "IDORDATTP": cursor.lastrowid,
        })

    logger.debug(f"[ADD][ATT] inserted={len(att_map)}")

    # -----------------------------------------------------------------------
    # INSERT FX_ORD_DOC (toate noi la ADD)
    # TmpID_OrdPart: optional — None = DOC global (nivel ORD).
    # -----------------------------------------------------------------------
    cursor.execute(
        "SELECT * FROM stg_OrdDoc WHERE Token=%s ORDER BY TmpID", (token,)
    )
    doc_map = []
    for d in cursor.fetchall():
        tmp_id_part = d["TmpID_OrdPart"]   # None sau int
        idordpartp  = (
            _resolve_fk_opt(tmp_id_part, tmp_to_real, f"DOC TmpID={d['TmpID']}")
            if tmp_id_part is not None
            else None
        )
        cursor.execute("""
            INSERT INTO FX_ORD_DOC
                (IDORDP, IDORDPARTP, IDORDDOC,
                 DocJust, NumeDoc, TipDoc)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (
            idordp, idordpartp,
            _strict_pos_int(d["IDORDDOC"],         "IDORDDOC"),
            _strict_str_nonempty(d.get("DocJust"), "DocJust"),
            _opt_str(d.get("NumeDoc")),
            _strict_str_nonempty(d["TipDoc"],      "TipDoc"),
        ))
        doc_map.append({
            "TmpID":     _strict_pos_int(d["TmpID"], "TmpID"),
            "IDORDDOCP": cursor.lastrowid,
        })

    logger.debug(f"[ADD][DOC] inserted={len(doc_map)}")

    # -----------------------------------------------------------------------
    # INSERT FX_ORD_TBL_REC (toate noi la ADD)
    # tbl_to_real: TmpID (din stg_OrdTblRec.TmpID_OrdTbl)
    #              → IDORDTBLP real (capturat la INSERT TBL mai sus)
    # DELETE e no-op la ADD (toate IDORDTBLP sunt noi, FX_ORD_TBL_REC e gol).
    # -----------------------------------------------------------------------
    tbl_to_real = {entry["TmpID"]: entry["IDORDTBLP"] for entry in tbl_map}
    rec_map     = _sync_tbl_rec(cursor, token, tbl_to_real)
    logger.debug(f"[ADD][REC] new={len(rec_map)}")

    logger.info(
        f"[ADD] DONE IDORDP={idordp} "
        f"parts={len(part_map)} tbls={len(tbl_map)} "
        f"atts={len(att_map)} docs={len(doc_map)} recs={len(rec_map)}"
    )
    return {
        "IDORDP":   idordp,
        "Part_Map": part_map,
        "TBL_Map":  tbl_map,
        "ATT_Map":  att_map,
        "DOC_Map":  doc_map,
        "REC_Map":  rec_map,
    }


# ===========================================================================
# SECTIUNEA 7 — COMMIT MOD (DIFF SYNC PE MARIADB PK)
# ===========================================================================
#
# _commit_mod: sync differential pe tabelele reale FX_ORD*.
# Discriminatorul ADD/UPDATE pentru fiecare rand este semnul MariaDB PK:
#   ...P > 0 → rand existent → UPDATE
#   ...P < 0 → rand nou adaugat in MOD → INSERT
# DELETE: randuri din DB cu PK absent din payload (sterse de user in Access).
# Returneaza acelasi format ca _commit_add (Map-uri doar pentru randuri NOI).

def _sync_part(cursor, token: str, idordp: int) -> Tuple[dict, list]:
    """
    Diff sync FX_ORD_PART.

    IDORDPARTP > 0 → UPDATE rand existent (rowcount=0 = eroare: rand disparut din DB).
    IDORDPARTP < 0 → INSERT rand nou, captureaza lastrowid in tmp_to_real.
    DELETE: PART-uri din DB cu IDORDPARTP absent din payload (sterse in Access).
      Cascade FK MariaDB sterge automat TBL/ATT/DOC orfane.

    Returneaza:
      tmp_to_real: {TmpID → IDORDPARTP real} — folosit pentru FK copii
      part_map:    [{TmpID, IDORDPARTP}] — doar randuri NOI (pentru Part_Map)
    """
    logger.debug(f"[MOD][PART] START idordp={idordp}")

    cursor.execute(
        "SELECT * FROM stg_OrdPart WHERE Token=%s ORDER BY TmpID", (token,)
    )
    stg_parts      = cursor.fetchall()
    tmp_to_real    = {}
    part_map       = []
    incoming_partp = set()   # IDORDPARTP > 0 pastrate in payload

    for p in stg_parts:
        tmp_id     = _strict_pos_int(p["TmpID"],    "TmpID")
        idordpartp = _strict_int(p["IDORDPARTP"],   "IDORDPARTP")

        if idordpartp > 0:
            # Rand existent — UPDATE
            cursor.execute("""
                UPDATE FX_ORD_PART
                SET IDORDPART=%s, Counter=%s, DenBene=%s, CodFiscal=%s,
                    ContIBAN=%s, Banca=%s, CodPartener=%s, IdPartener=%s
                WHERE IDORDPARTP=%s AND IDORDP=%s
            """, (
                _strict_pos_int(p["IDORDPART"], "IDORDPART"),
                _strict_str(p["Counter"],       "Counter"),
                _strict_str_nonempty(p["DenBene"], "DenBene"),
                _strict_str(p["CodFiscal"],     "CodFiscal"),
                _strict_str(p["ContIBAN"],      "ContIBAN"),
                _strict_str(p["Banca"],         "Banca"),
                _opt_str(p.get("CodPartener")),
                _opt_int(p.get("IdPartener"),   "IdPartener"),
                idordpartp, idordp,
            ))
            if cursor.rowcount == 0:
                raise ValueError(
                    f"[MOD][PART] IDORDPARTP={idordpartp} nu exista in FX_ORD_PART "
                    f"sau nu apartine IDORDP={idordp}"
                )
            tmp_to_real[tmp_id] = idordpartp
            incoming_partp.add(idordpartp)

        else:
            # Rand nou adaugat in MOD — INSERT
            cursor.execute("""
                INSERT INTO FX_ORD_PART
                    (IDORDP, IDORDPART, Counter, DenBene,
                     CodPartener, IdPartener, CodFiscal, ContIBAN, Banca)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                idordp,
                _strict_pos_int(p["IDORDPART"], "IDORDPART"),
                _strict_str(p["Counter"],       "Counter"),
                _strict_str_nonempty(p["DenBene"], "DenBene"),
                _opt_str(p.get("CodPartener")),
                _opt_int(p.get("IdPartener"),   "IdPartener"),
                _strict_str(p["CodFiscal"],     "CodFiscal"),
                _strict_str(p["ContIBAN"],      "ContIBAN"),
                _strict_str(p["Banca"],         "Banca"),
            ))
            new_idordpartp      = cursor.lastrowid
            tmp_to_real[tmp_id] = new_idordpartp
            part_map.append({"TmpID": tmp_id, "IDORDPARTP": new_idordpartp})

    # DELETE PART-uri absente din payload
    # Cascade FK sterge automat TBL/ATT/DOC asociate PART-ului sters.
    if incoming_partp:
        ph = ",".join(["%s"] * len(incoming_partp))
        cursor.execute(
            f"DELETE FROM FX_ORD_PART "
            f"WHERE IDORDP=%s AND IDORDPARTP NOT IN ({ph})",
            [idordp] + list(incoming_partp),
        )
    else:
        # Niciun PART existent pastrat → sterge tot
        cursor.execute("DELETE FROM FX_ORD_PART WHERE IDORDP=%s", (idordp,))

    logger.debug(
        f"[MOD][PART] DONE upd={len(incoming_partp)} "
        f"ins={len(part_map)} del={cursor.rowcount}"
    )
    return tmp_to_real, part_map


def _sync_tbl(cursor, token: str, idordp: int, tmp_to_real: dict) -> Tuple[dict, list]:
    """
    Diff sync FX_ORD_TBL.

    IDORDTBLP > 0 → UPDATE rand existent.
    IDORDTBLP < 0 → INSERT rand nou.
    DELETE: TBL-uri cu IDORDTBLP absent din payload.

    IDRP: nullable — inclus in UPDATE si INSERT (v5).
    IDRD: eliminat din v5 — nu mai apare in nicio operatie SQL pe FX_ORD_TBL.

    valori_date: tuplu cu campurile comune UPDATE si INSERT,
    construit o singura data per rand pentru consistenta.

    Returneaza:
      tbl_to_real: {TmpID → IDORDTBLP real} — folosit pentru FK copii REC
      tbl_map:     [{TmpID, IDORDTBLP}] — doar randuri NOI (pentru TBL_Map)
    """
    logger.debug(f"[MOD][TBL] START idordp={idordp}")

    cursor.execute(
        "SELECT * FROM stg_OrdTbl WHERE Token=%s ORDER BY TmpID", (token,)
    )
    stg_tbls      = cursor.fetchall()
    tbl_map       = []
    tbl_to_real   = {}          # TmpID → IDORDTBLP (toate, noi + existente)
    incoming_tblp = set()

    for t in stg_tbls:
        idordtblp   = _strict_int(t["IDORDTBLP"], "IDORDTBLP")
        tmp_id_part = _strict_pos_int(t["TmpID_OrdPart"], "TmpID_OrdPart")
        idordpartp  = _resolve_fk(
            tmp_id_part, tmp_to_real, f"TBL TmpID={t['TmpID']}"
        )

        # Campuri comune UPDATE si INSERT — construite o singura data per rand
        valori_date = (
            idordpartp,
            _strict_pos_int(t["IDORDTBL"],    "IDORDTBL"),
            _strict_str_nonempty(t["CodAI"],  "CodAI"),
            _strict_str(t["CodAngajament"],   "CodAngajament"),
            _strict_str(t["CodIndicator"],    "CodIndicator"),
            _strict_str(t["CodSSI"],          "CodSSI"),
            _strict_float(t["TotalReceptii"], "TotalReceptii"),
            _strict_float(t["PlatiAnt"],      "PlatiAnt"),
            _strict_float(t["Valoare"],       "Valoare"),
            _strict_float(t["Ramas"],         "Ramas"),
            _opt_int(t.get("IdClsf"),         "IdClsf"),
            _opt_int(t.get("IdClsfAcc"),      "IdClsfAcc"),
            _opt_str(t.get("Explicatie")),
            _opt_int(t.get("IDRP"),           "IDRP"),    # v5: IDRP, fara IDRD
        )

        if idordtblp > 0:
            # Rand existent — UPDATE
            cursor.execute("""
                UPDATE FX_ORD_TBL
                SET IDORDPARTP=%s, IDORDTBL=%s,
                    CodAI=%s, CodAngajament=%s, CodIndicator=%s, CodSSI=%s,
                    TotalReceptii=%s, PlatiAnt=%s, Valoare=%s, Ramas=%s,
                    IdClsf=%s, IdClsfAcc=%s, Explicatie=%s, IDRP=%s
                WHERE IDORDTBLP=%s AND IDORDP=%s
            """, valori_date + (idordtblp, idordp))
            if cursor.rowcount == 0:
                raise ValueError(
                    f"[MOD][TBL] IDORDTBLP={idordtblp} nu exista in FX_ORD_TBL "
                    f"sau nu apartine IDORDP={idordp}"
                )
            tmp_id = _strict_pos_int(t["TmpID"], "TmpID")
            tbl_to_real[tmp_id] = idordtblp
            incoming_tblp.add(idordtblp)

        else:
            # Rand nou — INSERT
            cursor.execute("""
                INSERT INTO FX_ORD_TBL
                    (IDORDP, IDORDPARTP, IDORDTBL,
                     CodAI, CodAngajament, CodIndicator, CodSSI,
                     TotalReceptii, PlatiAnt, Valoare, Ramas,
                     IdClsf, IdClsfAcc, Explicatie, IDRP)
                VALUES (%s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s)
            """, (idordp,) + valori_date)
            new_idordtblp = cursor.lastrowid
            tmp_id        = _strict_pos_int(t["TmpID"], "TmpID")
            tbl_to_real[tmp_id] = new_idordtblp
            tbl_map.append({
                "TmpID":     tmp_id,
                "IDORDTBLP": new_idordtblp,
            })

    # DELETE TBL-uri absente din payload
    if incoming_tblp:
        ph = ",".join(["%s"] * len(incoming_tblp))
        cursor.execute(
            f"DELETE FROM FX_ORD_TBL "
            f"WHERE IDORDP=%s AND IDORDTBLP NOT IN ({ph})",
            [idordp] + list(incoming_tblp),
        )
    else:
        cursor.execute("DELETE FROM FX_ORD_TBL WHERE IDORDP=%s", (idordp,))

    logger.debug(
        f"[MOD][TBL] DONE upd={len(incoming_tblp)} "
        f"ins={len(tbl_map)} del={cursor.rowcount}"
    )
    return tbl_to_real, tbl_map


def _sync_tbl_rec(cursor, token: str, tbl_to_real: dict) -> list:
    """
    Sync FX_ORD_TBL_REC: DELETE+INSERT per IDORDTBLP.

    Strategie DELETE+INSERT (nu diff) — IDORDRECP (MariaDB AutoInc) nu este
    tracked in Access, deci nu exista un discriminator de UPDATE/INSERT per rand.

    IDORDREC < 0 → Python genereaza MAX(IDORDREC pozitiv din grup)+1 per grup.
    IDORDREC > 0 → se pastreaza valoarea trimisa din VBA.

    DELETE ALL pentru TOATE IDORDTBLP din tbl_to_real, inclusiv cele fara
    randuri in stg (acopera stergerea completa a REC-urilor unui TBL).

    Returneaza REC_Map: [{TmpID, IDORDREC}] — doar randuri cu IDORDREC nou generat.
    """
    logger.debug(f"[SYNC][REC] START token={token}")

    cursor.execute(
        "SELECT * FROM stg_OrdTblRec WHERE Token=%s ORDER BY TmpID_OrdTbl, TmpID",
        (token,)
    )
    stg_recs = cursor.fetchall()
    rec_map  = []

    # Grupeaza randurile pe IDORDTBLP (rezolva TmpID_OrdTbl → IDORDTBLP)
    groups = {}   # {idordtblp: [row, ...]}
    for r in stg_recs:
        tmp_id_tbl = _strict_pos_int(r["TmpID_OrdTbl"], "TmpID_OrdTbl")
        idordtblp  = _resolve_fk(tmp_id_tbl, tbl_to_real, f"REC TmpID={r['TmpID']}")
        if idordtblp not in groups:
            groups[idordtblp] = []
        groups[idordtblp].append(r)

    # DELETE ALL pentru TOATE IDORDTBLP din tbl_to_real
    # (acopera si cazul cand toate REC-urile unui TBL au fost sterse din Access)
    all_idordtblp = list(tbl_to_real.values())
    if all_idordtblp:
        ph = ",".join(["%s"] * len(all_idordtblp))
        cursor.execute(
            f"DELETE FROM FX_ORD_TBL_REC WHERE IDORDTBLP IN ({ph})",
            all_idordtblp,
        )
        logger.debug(f"[SYNC][REC] DELETE rows={cursor.rowcount}")

    # INSERT din stg, grup cu grup
    for idordtblp, rows in groups.items():
        # Dupa DELETE, baza pentru generarea IDORDREC nou este maximul
        # valorilor pozitive existente in payload pentru acest grup.
        # Randurile cu IDORDREC > 0 (existente anterior) isi pastreaza valoarea.
        existing_max = max(
            (
                _strict_int(r["IDORDREC"], "IDORDREC")
                for r in rows
                if _strict_int(r["IDORDREC"], "IDORDREC") > 0
            ),
            default=0,
        )
        counter = existing_max   # incrementat pentru fiecare rand cu IDORDREC < 0

        for r in rows:
            idordrec = _strict_int(r["IDORDREC"], "IDORDREC")
            if idordrec < 0:
                counter  += 1
                idordrec  = counter
            cursor.execute("""
                INSERT INTO FX_ORD_TBL_REC (IDORDTBLP, IDORDREC, IDRP, Valoare)
                VALUES (%s, %s, %s, %s)
            """, (
                idordtblp,
                idordrec,
                _strict_pos_int(r["IDRP"],  "IDRP"),
                _strict_float(r["Valoare"], "Valoare"),
            ))
            rec_map.append({
                "TmpID":     _strict_pos_int(r["TmpID"], "TmpID"),
                "IDORDRECP": cursor.lastrowid,    # MariaDB AUTO_INCREMENT — capturat dupa INSERT
            })

    logger.debug(
        f"[SYNC][REC] DONE groups={len(groups)} new={len(rec_map)}"
    )
    return rec_map


def _sync_att(cursor, token: str, idordp: int, tmp_to_real: dict) -> list:
    """
    Diff sync FX_ORD_ATT.

    IDORDATTP > 0 → UPDATE. IDORDATTP < 0 → INSERT.
    DELETE: ATT-uri cu IDORDATTP absent din payload.
    TmpID_OrdPart: optional — None = ATT global (nivel ORD).
    """
    logger.debug(f"[MOD][ATT] START idordp={idordp}")

    cursor.execute(
        "SELECT * FROM stg_OrdAtt WHERE Token=%s ORDER BY TmpID", (token,)
    )
    stg_atts      = cursor.fetchall()
    att_map       = []
    incoming_attp = set()

    for a in stg_atts:
        idordattp   = _strict_int(a["IDORDATTP"], "IDORDATTP")
        tmp_id_part = a["TmpID_OrdPart"]   # None sau int
        idordpartp  = (
            _resolve_fk_opt(tmp_id_part, tmp_to_real, f"ATT TmpID={a['TmpID']}")
            if tmp_id_part is not None
            else None
        )
        imagine = _strict_str_nonempty(a["Imagine"], "Imagine")

        if idordattp > 0:
            cursor.execute("""
                UPDATE FX_ORD_ATT
                SET IDORDPARTP=%s, IDORDATT=%s, Imagine=%s
                WHERE IDORDATTP=%s AND IDORDP=%s
            """, (
                idordpartp,
                _strict_pos_int(a["IDORDATT"], "IDORDATT"),
                imagine,
                idordattp, idordp,
            ))
            if cursor.rowcount == 0:
                raise ValueError(
                    f"[MOD][ATT] IDORDATTP={idordattp} nu exista in FX_ORD_ATT "
                    f"sau nu apartine IDORDP={idordp}"
                )
            incoming_attp.add(idordattp)

        else:
            cursor.execute("""
                INSERT INTO FX_ORD_ATT
                    (IDORDP, IDORDPARTP, IDORDATT, Imagine)
                VALUES (%s, %s, %s, %s)
            """, (
                idordp, idordpartp,
                _strict_pos_int(a["IDORDATT"], "IDORDATT"),
                imagine,
            ))
            att_map.append({
                "TmpID":     _strict_pos_int(a["TmpID"], "TmpID"),
                "IDORDATTP": cursor.lastrowid,
            })

    if incoming_attp:
        ph = ",".join(["%s"] * len(incoming_attp))
        cursor.execute(
            f"DELETE FROM FX_ORD_ATT "
            f"WHERE IDORDP=%s AND IDORDATTP NOT IN ({ph})",
            [idordp] + list(incoming_attp),
        )
    else:
        cursor.execute("DELETE FROM FX_ORD_ATT WHERE IDORDP=%s", (idordp,))

    logger.debug(
        f"[MOD][ATT] DONE upd={len(incoming_attp)} "
        f"ins={len(att_map)} del={cursor.rowcount}"
    )
    return att_map


def _sync_doc(cursor, token: str, idordp: int, tmp_to_real: dict) -> list:
    """
    Diff sync FX_ORD_DOC.

    IDORDDOCP > 0 → UPDATE. IDORDDOCP < 0 → INSERT.
    DELETE: DOC-uri cu IDORDDOCP absent din payload.
    TmpID_OrdPart: optional — None = DOC global (nivel ORD).
    TipDoc == 'text' → NumeDoc poate fi null (text liber fara fisier).
    """
    logger.debug(f"[MOD][DOC] START idordp={idordp}")

    cursor.execute(
        "SELECT * FROM stg_OrdDoc WHERE Token=%s ORDER BY TmpID", (token,)
    )
    stg_docs      = cursor.fetchall()
    doc_map       = []
    incoming_docp = set()

    for d in stg_docs:
        idorddocp   = _strict_int(d["IDORDDOCP"], "IDORDDOCP")
        tmp_id_part = d["TmpID_OrdPart"]   # None sau int
        idordpartp  = (
            _resolve_fk_opt(tmp_id_part, tmp_to_real, f"DOC TmpID={d['TmpID']}")
            if tmp_id_part is not None
            else None
        )

        if idorddocp > 0:
            cursor.execute("""
                UPDATE FX_ORD_DOC
                SET IDORDPARTP=%s, IDORDDOC=%s,
                    DocJust=%s, NumeDoc=%s, TipDoc=%s
                WHERE IDORDDOCP=%s AND IDORDP=%s
            """, (
                idordpartp,
                _strict_pos_int(d["IDORDDOC"],     "IDORDDOC"),
                _opt_str(d.get("DocJust")),
                _strict_str_nonempty(d["NumeDoc"], "NumeDoc"),
                _strict_str_nonempty(d["TipDoc"],  "TipDoc"),
                idorddocp, idordp,
            ))
            if cursor.rowcount == 0:
                raise ValueError(
                    f"[MOD][DOC] IDORDDOCP={idorddocp} nu exista in FX_ORD_DOC "
                    f"sau nu apartine IDORDP={idordp}"
                )
            incoming_docp.add(idorddocp)

        else:
            cursor.execute("""
                INSERT INTO FX_ORD_DOC
                    (IDORDP, IDORDPARTP, IDORDDOC,
                     DocJust, NumeDoc, TipDoc)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (
                idordp, idordpartp,
                _strict_pos_int(d["IDORDDOC"],         "IDORDDOC"),
                _opt_str(d.get("DocJust")),
                _strict_str_nonempty(d["NumeDoc"],     "NumeDoc"),
                _strict_str_nonempty(d["TipDoc"],      "TipDoc"),
            ))
            doc_map.append({
                "TmpID":     _strict_pos_int(d["TmpID"], "TmpID"),
                "IDORDDOCP": cursor.lastrowid,
            })

    if incoming_docp:
        ph = ",".join(["%s"] * len(incoming_docp))
        cursor.execute(
            f"DELETE FROM FX_ORD_DOC "
            f"WHERE IDORDP=%s AND IDORDDOCP NOT IN ({ph})",
            [idordp] + list(incoming_docp),
        )
    else:
        cursor.execute("DELETE FROM FX_ORD_DOC WHERE IDORDP=%s", (idordp,))

    logger.debug(
        f"[MOD][DOC] DONE upd={len(incoming_docp)} "
        f"ins={len(doc_map)} del={cursor.rowcount}"
    )
    return doc_map


def _commit_mod(cursor, token: str) -> dict:
    """
    Commit MOD complet: UPDATE FX_ORD → diff sync PART → TBL → TBL_REC → ATT → DOC.

    Lock FX_ORD (SELECT FOR UPDATE) inainte de orice write —
    previne write concurent pe acelasi ORD.

    FX_ORD.IDRR: actualizat la UPDATE (nullable, v5).
      Permite modificarea legaturii la FX_Receptii_R (ex: flux 2 devine flux 1
      dupa ce receptia a fost creata in FOREXE si adusa inapoi).
    FX_ORD.IDRH: actualizat la UPDATE (nullable, v6).
    IDRD: eliminat din UPDATE FX_ORD (v5) — coloana nu mai exista in stg_Ord.
    """
    logger.info(f"[MOD] START token={token}")

    cursor.execute("SELECT * FROM stg_Ord WHERE Token=%s", (token,))
    stg_ord = cursor.fetchone()
    if not stg_ord:
        raise ValueError(f"stg_Ord negasit pentru token={token}")

    idordp = _strict_pos_int(stg_ord["IDORDP"], "stg_Ord.IDORDP")
    logger.info(f"[MOD] IDORDP={idordp}")

    # Lock pe ORD parinte — previne concurenta la MOD simultan pe acelasi ORD
    cursor.execute(
        "SELECT IDORDP FROM FX_ORD WHERE IDORDP=%s FOR UPDATE", (idordp,)
    )
    if not cursor.fetchone():
        raise ValueError(f"[MOD] FX_ORD cu IDORDP={idordp} nu exista in DB")

    # UPDATE header ORD
    # IDRR: nullable — permite actualizarea legaturii la receptie (v5).
    # IDRH: nullable — permite actualizarea legaturii la istoricul receptiei (v6).
    #   Ambele pot fi actualizate independent: flux 2 → flux 1 inseamna
    #   setarea IDRR si/sau IDRH dupa ce entitatile din Receptii au fost create.
    # IDRD: eliminat din v5 (coloana stearsa, nu mai exista in stg_Ord).
    cursor.execute("""
        UPDATE FX_ORD
        SET IDORD=%s, NrORD=%s, DataORD=%s,
            Comp=%s, CUAL=%s,
            Incarcat=%s, Preluat=%s,
            IDRR=%s, IDRH=%s
        WHERE IDORDP=%s
    """, (
        _strict_pos_int(stg_ord["IDORD"],      "IDORD"),
        _strict_int(stg_ord["NrORD"],          "NrORD"),
        stg_ord["DataORD"],
        _strict_str_nonempty(stg_ord["Comp"],  "Comp"),
        _opt_str(stg_ord.get("CUAL")),
        _strict_bool(stg_ord.get("Incarcat"),  "Incarcat"),
        _strict_bool(stg_ord.get("Preluat"),   "Preluat"),
        _opt_int(stg_ord.get("IDRR"),          "IDRR"),    # v5: IDRR
        _opt_int(stg_ord.get("IDRH"),          "IDRH"),    # v6: IDRH
        idordp,
    ))
    logger.debug(
        f"[MOD] FX_ORD UPDATE rows={cursor.rowcount} "
        f"IDRR={stg_ord.get('IDRR')} IDRH={stg_ord.get('IDRH')}"
    )

    tmp_to_real, part_map = _sync_part(cursor, token, idordp)
    tbl_to_real, tbl_map  = _sync_tbl(cursor,  token, idordp, tmp_to_real)
    rec_map               = _sync_tbl_rec(cursor, token, tbl_to_real)
    att_map               = _sync_att(cursor,  token, idordp, tmp_to_real)
    doc_map               = _sync_doc(cursor,  token, idordp, tmp_to_real)

    logger.info(
        f"[MOD] DONE IDORDP={idordp} "
        f"parts_new={len(part_map)} tbls_new={len(tbl_map)} "
        f"atts_new={len(att_map)} docs_new={len(doc_map)} recs_new={len(rec_map)}"
    )
    return {
        "IDORDP":   idordp,
        "Part_Map": part_map,
        "TBL_Map":  tbl_map,
        "ATT_Map":  att_map,
        "DOC_Map":  doc_map,
        "REC_Map":  rec_map,
    }


# ===========================================================================
# SECTIUNEA 8 — CLEANUP STAGING
# ===========================================================================

def _cleanup_stg_children(cursor, token: str):
    """
    Sterge randurile copil din stg_Ord* dupa commit (CONFIRMED sau FAIL).
    stg_Ord insusi ramane pentru audit (Status + DataConfirm).

    Guard: refuza stergerea daca Status=PENDING — ar indica un bug logic
    (cleanup apelat inainte de confirm sau in paralel cu alta sesiune).
    """
    cursor.execute("SELECT Status FROM stg_Ord WHERE Token=%s", (token,))
    row = cursor.fetchone()
    if not row:
        raise ValueError(
            f"_cleanup_stg_children: token={token} negasit in stg_Ord"
        )
    if row["Status"] == "PENDING":
        raise ValueError(
            f"_cleanup_stg_children: token={token} are Status=PENDING. "
            "Nu se sterge staging activ. Bug logic."
        )
    for tabel in ("stg_OrdPart", "stg_OrdTbl", "stg_OrdAtt", "stg_OrdDoc", "stg_OrdTblRec"):
        cursor.execute(f"DELETE FROM {tabel} WHERE Token=%s", (token,))
        logger.debug(f"[CLEANUP_STG] {tabel} rows={cursor.rowcount}")


# ===========================================================================
# SECTIUNEA 9 — PATCH (INSERT DIRECT, FARA STAGING)
# ===========================================================================
#
# Patch: adaugari punctuale dupa salvarea initiala, fara circuit complet
# de staging. IDORDPARTP trebuie sa fie mereu > 0 (PART confirmat in DB).
# Daca e necesar un PART nou, se apeleaza intai patch/part → se obtine
# IDORDPARTP real din response → se foloseste la patch/tbl|att|doc.

def _assert_ord_exists(cursor, idordp: int):
    """Raise daca FX_ORD cu IDORDP nu exista. Folosit ca guard in patch."""
    cursor.execute("SELECT 1 FROM FX_ORD WHERE IDORDP=%s", (idordp,))
    if not cursor.fetchone():
        raise ValueError(f"FX_ORD cu IDORDP={idordp} nu exista")


def _assert_part_belongs_to_ord(cursor, idordpartp: int, idordp: int):
    """
    Raise daca IDORDPARTP nu apartine IDORDP.
    Previne inserarea unui TBL/ATT/DOC sub un PART din alt ORD.
    """
    cursor.execute(
        "SELECT 1 FROM FX_ORD_PART WHERE IDORDPARTP=%s AND IDORDP=%s",
        (idordpartp, idordp),
    )
    if not cursor.fetchone():
        raise ValueError(
            f"IDORDPARTP={idordpartp} nu apartine IDORDP={idordp} "
            "sau nu exista in FX_ORD_PART"
        )


def _patch_part(cursor, idordp: int, rows: list) -> list:
    """
    Insereaza PART-uri noi intr-un ORD existent (fara staging).
    Returneaza [{TmpID, IDORDPARTP}].
    """
    _assert_ord_exists(cursor, idordp)
    part_map = []
    for r in rows:
        cursor.execute("""
            INSERT INTO FX_ORD_PART
                (IDORDP, IDORDPART, Counter, DenBene,
                 CodPartener, IdPartener, CodFiscal, ContIBAN, Banca)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            idordp,
            _strict_pos_int(r.get("IDORDPART"),    "IDORDPART"),
            _strict_str(r.get("Counter"),          "Counter"),
            _strict_str_nonempty(r.get("DenBene"), "DenBene"),
            _opt_str(r.get("CodPartener")),
            _opt_int(r.get("IdPartener"),          "IdPartener"),
            _strict_str(r.get("CodFiscal"),        "CodFiscal"),
            _strict_str(r.get("ContIBAN"),         "ContIBAN"),
            _strict_str(r.get("Banca"),            "Banca"),
        ))
        part_map.append({
            "TmpID":      _strict_pos_int(r.get("TmpID"), "TmpID"),
            "IDORDPARTP": cursor.lastrowid,
        })
    logger.debug(f"[PATCH][PART] IDORDP={idordp} inserted={len(rows)}")
    return part_map


def _patch_tbl(cursor, idordp: int, rows: list) -> list:
    """
    Insereaza TBL-uri noi intr-un ORD existent (fara staging).
    IDORDPARTP trebuie sa fie > 0 (PART confirmat).
    IDRP: nullable — FK read-only catre FX_Receptii_Plati (v5).
    IDRD: eliminat din v5.
    Returneaza [{TmpID, IDORDTBLP}].
    """
    _assert_ord_exists(cursor, idordp)
    tbl_map = []
    for r in rows:
        idordpartp = _strict_pos_int(r.get("IDORDPARTP"), "IDORDPARTP")
        _assert_part_belongs_to_ord(cursor, idordpartp, idordp)
        cursor.execute("""
            INSERT INTO FX_ORD_TBL
                (IDORDP, IDORDPARTP, IDORDTBL,
                 CodAI, CodAngajament, CodIndicator, CodSSI,
                 TotalReceptii, PlatiAnt, Valoare, Ramas,
                 IdClsf, IdClsfAcc, Explicatie, IDRP)
            VALUES (%s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            idordp, idordpartp,
            _strict_pos_int(r.get("IDORDTBL"),    "IDORDTBL"),
            _strict_str_nonempty(r.get("CodAI"),  "CodAI"),
            _strict_str(r.get("CodAngajament"),   "CodAngajament"),
            _strict_str(r.get("CodIndicator"),    "CodIndicator"),
            _strict_str(r.get("CodSSI"),          "CodSSI"),
            _strict_float(r.get("TotalReceptii"), "TotalReceptii"),
            _strict_float(r.get("PlatiAnt"),      "PlatiAnt"),
            _strict_float(r.get("Valoare"),       "Valoare"),
            _strict_float(r.get("Ramas"),         "Ramas"),
            _opt_int(r.get("IdClsf"),             "IdClsf"),
            _opt_int(r.get("IdClsfAcc"),          "IdClsfAcc"),
            _opt_str(r.get("Explicatie")),
            _opt_int(r.get("IDRP"),               "IDRP"),    # v5: IDRP, fara IDRD
        ))
        tbl_map.append({
            "TmpID":     _strict_pos_int(r.get("TmpID"), "TmpID"),
            "IDORDTBLP": cursor.lastrowid,
        })
    logger.debug(f"[PATCH][TBL] IDORDP={idordp} inserted={len(rows)}")
    return tbl_map


def _patch_att(cursor, idordp: int, rows: list) -> list:
    """
    Insereaza ATT-uri noi intr-un ORD existent (fara staging).
    IDORDPARTP trebuie sa fie > 0 (PART confirmat).
    Returneaza [{TmpID, IDORDATTP}].
    """
    _assert_ord_exists(cursor, idordp)
    att_map = []
    for r in rows:
        idordpartp = _strict_pos_int(r.get("IDORDPARTP"), "IDORDPARTP")
        _assert_part_belongs_to_ord(cursor, idordpartp, idordp)
        cursor.execute("""
            INSERT INTO FX_ORD_ATT
                (IDORDP, IDORDPARTP, IDORDATT, Imagine)
            VALUES (%s, %s, %s, %s)
        """, (
            idordp, idordpartp,
            _strict_pos_int(r.get("IDORDATT"),     "IDORDATT"),
            _strict_str_nonempty(r.get("Imagine"), "Imagine"),
        ))
        att_map.append({
            "TmpID":     _strict_pos_int(r.get("TmpID"), "TmpID"),
            "IDORDATTP": cursor.lastrowid,
        })
    logger.debug(f"[PATCH][ATT] IDORDP={idordp} inserted={len(rows)}")
    return att_map


def _patch_doc(cursor, idordp: int, rows: list) -> list:
    """
    Insereaza DOC-uri noi intr-un ORD existent (fara staging).
    IDORDPARTP trebuie sa fie > 0 (PART confirmat).
    Returneaza [{TmpID, IDORDDOCP}].
    """
    _assert_ord_exists(cursor, idordp)
    doc_map = []
    for r in rows:
        idordpartp = _strict_pos_int(r.get("IDORDPARTP"), "IDORDPARTP")
        _assert_part_belongs_to_ord(cursor, idordpartp, idordp)
        cursor.execute("""
            INSERT INTO FX_ORD_DOC
                (IDORDP, IDORDPARTP, IDORDDOC,
                 DocJust, NumeDoc, TipDoc)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (
            idordp, idordpartp,
            _strict_pos_int(r.get("IDORDDOC"),     "IDORDDOC"),
            _opt_str(r.get("DocJust")),
            _strict_str_nonempty(r.get("NumeDoc"), "NumeDoc"),
            _strict_str_nonempty(r.get("TipDoc"),  "TipDoc"),
        ))
        doc_map.append({
            "TmpID":     _strict_pos_int(r.get("TmpID"), "TmpID"),
            "IDORDDOCP": cursor.lastrowid,
        })
    logger.debug(f"[PATCH][DOC] IDORDP={idordp} inserted={len(rows)}")
    return doc_map


# ===========================================================================
# SECTIUNEA 10 — ENDPOINTS
# ===========================================================================

@ord_bp.route("/api/ord/save_staging", methods=["POST"])
@require_api_key
def save_staging():
    """
    Staging ADD (ordonantare noua).

    Validari:
      ord.IDORDP < 0 (rand nou — nu exista inca in DB)
      ord.IDORD  > 0 (pre-calculat de VBA)
      toate ...P < 0 (toti copiii sunt noi)
      ord.IDRR   nullable (NULL = flux 2)
      ord.IDRH   nullable (NULL = flux 2, v6)

    Returneaza: {"token": "<uuid>"}
    """
    data = request.json
    try:
        _check_content_length()
        _validate_payload(data, "ADD")

        token = str(uuid.uuid4())
        logger.info(
            f"[save_staging] ADD token={token} "
            f"IDORD={data['ord'].get('IDORD')} "
            f"IDRR={data['ord'].get('IDRR')} "
            f"IDRH={data['ord'].get('IDRH')}"
        )

        def operation(cursor):
            _stg_insert_all(cursor, token, "ADD", data)
            return {"token": token}

        return jsonify(_run_with_retry(operation, data)), 200

    except ValueError as e:
        logger.warning(f"[save_staging] Validare eronata: {e}")
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"[save_staging] ERROR: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@ord_bp.route("/api/ord/update_staging", methods=["POST"])
@require_api_key
def update_staging():
    """
    Staging MOD (ordonantare existenta).

    Validari:
      ord.IDORDP > 0 (ORD existent in DB)
      ord.IDORD  > 0
      ...P < 0 = rand nou in MOD, ...P > 0 = rand existent
      ord.IDRR   nullable
      ord.IDRH   nullable (v6)

    Returneaza: {"token": "<uuid>"}
    """
    data = request.json
    try:
        _check_content_length()
        _validate_payload(data, "MOD")

        token = str(uuid.uuid4())
        logger.info(
            f"[update_staging] MOD token={token} "
            f"IDORDP={data['ord'].get('IDORDP')} "
            f"IDRR={data['ord'].get('IDRR')} "
            f"IDRH={data['ord'].get('IDRH')}"
        )

        def operation(cursor):
            _stg_insert_all(cursor, token, "MOD", data)
            return {"token": token}

        return jsonify(_run_with_retry(operation, data)), 200

    except ValueError as e:
        logger.warning(f"[update_staging] Validare eronata: {e}")
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"[update_staging] ERROR: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@ord_bp.route("/api/ord/confirm", methods=["POST"])
@require_api_key
def confirm():
    """
    Confirma sau anuleaza o operatie staged.

    Payload: {db_name, token, status: "OK" | "FAIL"}

    status="OK":
      - Verifica Status=PENDING (dublu submit = eroare, nu re-executa).
      - Executa _commit_add sau _commit_mod in functie de TipOperatie.
      - Seteaza Status=CONFIRMED + DataConfirm.
      - Sterge copiii din staging (stg_Ord ramane pentru audit).
      - Returneaza {IDORDP, Part_Map, TBL_Map, ATT_Map, DOC_Map, REC_Map}.

    status="FAIL":
      - Seteaza Status=FAIL pe stg_Ord.
      - Sterge copiii din staging.
      - Returneaza {ok: True}.
    """
    data  = request.json
    token = None
    try:
        token  = _strict_str_nonempty(data.get("token"),  "token")
        status = _strict_str_nonempty(data.get("status"), "status")

        if status not in ("OK", "FAIL"):
            return jsonify(
                {"error": f"status='{status}' invalid — acceptat: OK | FAIL"}
            ), 400

        if status == "FAIL":
            def operation_fail(cursor):
                cursor.execute("""
                    UPDATE stg_Ord
                    SET Status='FAIL', DataConfirm=NOW()
                    WHERE Token=%s AND Status='PENDING'
                """, (token,))
                if cursor.rowcount > 0:
                    _cleanup_stg_children(cursor, token)
                else:
                    # Token deja procesat (CONFIRMED/FAIL) sau inexistent
                    logger.warning(
                        f"[confirm] FAIL pe token={token} care nu mai e PENDING "
                        f"(rowcount=0) — ignorat"
                    )
                return {"ok": True}

            result = _run_with_retry(operation_fail, data)
            logger.info(f"[confirm] FAIL token={token}")
            return jsonify(result), 200

        # status == OK
        def operation_ok(cursor):
            cursor.execute(
                "SELECT TipOperatie, Status FROM stg_Ord "
                "WHERE Token=%s FOR UPDATE",
                (token,),
            )
            row = cursor.fetchone()

            if not row:
                raise ValueError(f"Token necunoscut: {token}")

            if row["Status"] != "PENDING":
                raise ValueError(
                    f"Token {token} are Status={row['Status']} (nu PENDING). "
                    "Dublu submit detectat — operatia nu va fi reexecutata."
                )

            tip = row["TipOperatie"]
            logger.info(f"[confirm] token={token} TipOperatie={tip}")

            if tip == "ADD":
                result = _commit_add(cursor, token)
            elif tip == "MOD":
                result = _commit_mod(cursor, token)
            else:
                raise ValueError(
                    f"TipOperatie necunoscut in stg_Ord: '{tip}'"
                )

            cursor.execute(
                "UPDATE stg_Ord SET Status='CONFIRMED', DataConfirm=NOW() "
                "WHERE Token=%s",
                (token,),
            )
            _cleanup_stg_children(cursor, token)
            return result

        result = _run_with_retry(operation_ok, data)
        logger.info(f"[confirm] DONE token={token} IDORDP={result['IDORDP']}")
        return jsonify(result), 200

    except Exception as e:
        logger.error(f"[confirm] ERROR token={token}: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@ord_bp.route("/api/ord/cleanup_staging", methods=["POST"])
@require_api_key
def cleanup_staging():
    """
    Sterge tokenuri PENDING mai vechi de 60 minute (si copiii lor).
    Apelat de VBA la inceputul fiecarui flux de salvare.
    Returneaza {"deleted": N} — numarul de tokenuri expirate sterse.
    """
    data = request.json
    try:
        def operation(cursor):
            cursor.execute("""
                SELECT Token FROM stg_Ord
                WHERE Status = 'PENDING'
                  AND DataInsert < DATE_SUB(NOW(), INTERVAL 60 MINUTE)
            """)
            tokens = [r["Token"] for r in cursor.fetchall()]
            if tokens:
                ph = ",".join(["%s"] * len(tokens))
                # Sterge copiii mai intai (explicit, nu numai prin CASCADE)
                for tabel in ("stg_OrdPart", "stg_OrdTbl",
                              "stg_OrdAtt",  "stg_OrdDoc", "stg_OrdTblRec"):
                    cursor.execute(
                        f"DELETE FROM {tabel} WHERE Token IN ({ph})", tokens
                    )
                cursor.execute(
                    f"DELETE FROM stg_Ord WHERE Token IN ({ph})", tokens
                )
            return {"deleted": len(tokens)}

        result = _run_with_retry(operation, data)
        logger.info(f"[cleanup_staging] deleted={result['deleted']}")
        return jsonify(result), 200

    except Exception as e:
        logger.error(f"[cleanup_staging] ERROR: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# PATCH ENDPOINTS
# ---------------------------------------------------------------------------

@ord_bp.route("/api/ord/patch/part", methods=["POST"])
@require_api_key
def patch_part():
    """
    Insereaza PART-uri noi intr-un ORD existent (fara staging).
    Payload:  {db_name, IDORDP, rows: [{TmpID, IDORDPART, DenBene, ...}]}
    Response: {Part_Map: [{TmpID, IDORDPARTP}]}
    """
    data = request.json
    try:
        idordp = _strict_pos_int(data.get("IDORDP"), "IDORDP")
        rows   = data.get("rows", [])
        if not isinstance(rows, list):
            return jsonify({"error": "'rows' trebuie sa fie list"}), 400

        def operation(cursor):
            return {"Part_Map": _patch_part(cursor, idordp, rows)}

        return jsonify(_run_with_retry(operation, data)), 200

    except Exception as e:
        logger.error(f"[patch/part] ERROR: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@ord_bp.route("/api/ord/patch/tbl", methods=["POST"])
@require_api_key
def patch_tbl():
    """
    Insereaza TBL-uri noi intr-un ORD existent (fara staging).
    IDORDPARTP trebuie sa fie > 0 (PART confirmat).
    IDRP: optional (nullable — v5).
    Payload:  {db_name, IDORDP, rows: [{TmpID, IDORDPARTP, IDORDTBL, CodAI, ..., IDRP?}]}
    Response: {TBL_Map: [{TmpID, IDORDTBLP}]}
    """
    data = request.json
    try:
        idordp = _strict_pos_int(data.get("IDORDP"), "IDORDP")
        rows   = data.get("rows", [])
        if not isinstance(rows, list):
            return jsonify({"error": "'rows' trebuie sa fie list"}), 400

        def operation(cursor):
            return {"TBL_Map": _patch_tbl(cursor, idordp, rows)}

        return jsonify(_run_with_retry(operation, data)), 200

    except Exception as e:
        logger.error(f"[patch/tbl] ERROR: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@ord_bp.route("/api/ord/patch/att", methods=["POST"])
@require_api_key
def patch_att():
    """
    Insereaza ATT-uri noi intr-un ORD existent (fara staging).
    IDORDPARTP trebuie sa fie > 0 (PART confirmat).
    Payload:  {db_name, IDORDP, rows: [{TmpID, IDORDPARTP, IDORDATT, Imagine}]}
    Response: {ATT_Map: [{TmpID, IDORDATTP}]}
    """
    data = request.json
    try:
        idordp = _strict_pos_int(data.get("IDORDP"), "IDORDP")
        rows   = data.get("rows", [])
        if not isinstance(rows, list):
            return jsonify({"error": "'rows' trebuie sa fie list"}), 400

        def operation(cursor):
            return {"ATT_Map": _patch_att(cursor, idordp, rows)}

        return jsonify(_run_with_retry(operation, data)), 200

    except Exception as e:
        logger.error(f"[patch/att] ERROR: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@ord_bp.route("/api/ord/patch/doc", methods=["POST"])
@require_api_key
def patch_doc():
    """
    Insereaza DOC-uri noi intr-un ORD existent (fara staging).
    IDORDPARTP trebuie sa fie > 0 (PART confirmat).
    Payload:  {db_name, IDORDP, rows: [{TmpID, IDORDPARTP, IDORDDOC, NumeDoc, TipDoc, DocJust?}]}
    Response: {DOC_Map: [{TmpID, IDORDDOCP}]}
    """
    data = request.json
    try:
        idordp = _strict_pos_int(data.get("IDORDP"), "IDORDP")
        rows   = data.get("rows", [])
        if not isinstance(rows, list):
            return jsonify({"error": "'rows' trebuie sa fie list"}), 400

        def operation(cursor):
            return {"DOC_Map": _patch_doc(cursor, idordp, rows)}

        return jsonify(_run_with_retry(operation, data)), 200

    except Exception as e:
        logger.error(f"[patch/doc] ERROR: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# DELETE
# ---------------------------------------------------------------------------

@ord_bp.route("/api/ord/delete", methods=["POST"])
@require_api_key
def delete_ord():
    """
    Sterge un ORD complet din DB.
    Cascade FK MariaDB sterge automat: PART → TBL / ATT / DOC.
    Payload:  {db_name, IDORDP}
    Response: {ok: True, IDORDP: N}

    NOTA: ORD NU modifica FX_Receptii_Plati la stergere —
    IDRP din TBL este read-only, RecPl ramane intact.
    """
    data = request.json

    try:
        idordp = _strict_pos_int(data.get("IDORDP"), "IDORDP")

        def operation(cursor):
            cursor.execute(
                "SELECT IDORDP FROM FX_ORD WHERE IDORDP=%s FOR UPDATE",
                (idordp,)
            )

            if not cursor.fetchone():
                raise LookupError(f"FX_ORD cu IDORDP={idordp} nu exista")

            cursor.execute(
                "DELETE FROM FX_ORD WHERE IDORDP=%s",
                (idordp,)
            )

            return {"ok": True, "IDORDP": idordp}

        result = _run_with_retry(operation, data)
        return jsonify(result), 200

    except LookupError as e:
        return jsonify({"error": str(e)}), 404

    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    except Exception as e:
        logger.error(f"[delete] ERROR: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500