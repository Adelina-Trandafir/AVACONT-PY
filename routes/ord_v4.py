# routes/ord.py — v4
# Schema: AVACONT_SURSA.sql (17/04/2026)
#
# v4 vs v3:
#   - MariaDB PKs ca discriminator ADD/MOD (nu Access PKs)
#       ...P < 0  → INSERT nou
#       ...P > 0  → UPDATE existent
#   - Access PKs pre-calculate vin in payload, se scriu direct la INSERT/UPDATE
#   - TmpID folosit exclusiv pentru legaturi parent-child in payload
#   - Eliminat complet: update_access_ids + tot codul aferent (Sectiunea 9 v3)
#   - Staging stocheaza acum si ...P (MariaDB PK) pentru fiecare entitate
#   - _stg_insert_all simplificat — fara access_map
#   - _commit_add: INSERT cu IDORD/IDORDPART/etc. din stg, return TmpID→MariaDB PK map
#   - _commit_mod: diff sync pe semnul ...P, nu pe Access ID
#   - patch/part: endpoint nou pentru PART-uri adaugate punctual

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
MAX_ATTS             = 150
MAX_DOCS             = 150
MAX_DEADLOCK_RETRIES = 3
DEADLOCK_RETRY_SLEEP = 0.2               # seconds (se inmulteste cu attempt)

ERRNO_DEADLOCK       = 1213
ERRNO_LOCK_TIMEOUT   = 1205


# ===========================================================================
# SECTIUNEA 2 — PARSARE STRICTA
# ===========================================================================
def _strict_bool(v, field: str) -> int:
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
    if v is None or (isinstance(v, str) and v.strip() == ""):
        raise ValueError(f"Camp '{field}': null sau gol (int obligatoriu)")
    try:
        return int(v)
    except (TypeError, ValueError):
        raise ValueError(f"Camp '{field}': '{v}' nu este int valid")


def _strict_pos_int(v, field: str) -> int:
    result = _strict_int(v, field)
    if result <= 0:
        raise ValueError(f"Camp '{field}': {result} trebuie sa fie > 0")
    return result


def _strict_float(v, field: str) -> float:
    if v is None or (isinstance(v, str) and v.strip() == ""):
        raise ValueError(f"Camp '{field}': null sau gol (float obligatoriu)")
    try:
        return float(v)
    except (TypeError, ValueError):
        raise ValueError(f"Camp '{field}': '{v}' nu este float valid")


def _strict_str(v, field: str) -> str:
    if v is None:
        raise ValueError(f"Camp '{field}': null (string obligatoriu)")
    return str(v)


def _strict_str_nonempty(v, field: str) -> str:
    s = _strict_str(v, field)
    if s.strip() == "":
        raise ValueError(f"Camp '{field}': string gol (valoare obligatorie)")
    return s


def _opt_int(v, field: str) -> Optional[int]:
    """None → NULL. 0 → NULL (FK neset). String gol → raise. Non-numeric → raise."""
    if v is None:
        return None
    if isinstance(v, str) and v.strip() == "":
        raise ValueError(f"Camp optional '{field}': string gol invalid — trimite null sau int")
    try:
        result = int(v)
        return result if result != 0 else None
    except (TypeError, ValueError):
        raise ValueError(f"Camp optional '{field}': '{v}' nu este int valid")


def _opt_str(v) -> Optional[str]:
    return None if v is None else str(v)


def _mariadb_pk(v, field: str, tip: str) -> int:
    """
    Valideaza un camp MariaDB PK conform regulii:
      ADD: trebuie < 0  (rand nou — niciodata pozitiv la adaugare)
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

# HELPER NOU — pentru ATT/DOC unde TmpID_OrdPart e nullable
def _resolve_fk_opt(tmp_id_part: int, tmp_to_real: dict, entity: str) -> int:
    """
    Identic cu _resolve_fk dar cu if idordpartp is None (nu 'not idordpartp').
    Apelat doar cand tmp_id_part is not None.
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
    if request.content_length and request.content_length > MAX_PAYLOAD_BYTES:
        raise ValueError(
            f"Payload prea mare: {request.content_length:,} bytes "
            f"(maxim {MAX_PAYLOAD_BYTES:,} bytes = 2 MB)"
        )


def _validate_ord_header(ord_: dict, tip: str):
    if not isinstance(ord_, dict):
        raise ValueError("'ord' trebuie sa fie dict")

    # IDORDP: MariaDB PK — ADD < 0, MOD > 0
    idordp = _strict_int(ord_.get("IDORDP"), "ord.IDORDP")
    if tip == "ADD" and idordp >= 0:
        raise ValueError(f"ord.IDORDP={idordp}: la ADD trebuie sa fie negativ")
    if tip == "MOD" and idordp <= 0:
        raise ValueError(f"ord.IDORDP={idordp}: la MOD trebuie sa fie > 0")

    # IDORD: Access PK, mereu > 0
    _strict_pos_int(ord_.get("IDORD"), "ord.IDORD")

    # Campuri obligatorii
    _strict_int(ord_.get("NrORD"), "ord.NrORD")
    _strict_str_nonempty(ord_.get("DataORD"), "ord.DataORD")
    _strict_str_nonempty(ord_.get("Comp"),    "ord.Comp")
    _strict_bool(ord_.get("Incarcat"),           "ord.Incarcat")
    _strict_bool(ord_.get("Preluat"),            "ord.Preluat")


def _validate_part(p: dict, idx: int, tip: str):
    pfx = f"parts[{idx}]"

    _strict_pos_int(p.get("TmpID"), f"{pfx}.TmpID")

    # IDORDPARTP: MariaDB PK
    _mariadb_pk(p.get("IDORDPARTP"), f"{pfx}.IDORDPARTP", tip)

    # IDORDPART: Access PK, mereu > 0
    _strict_pos_int(p.get("IDORDPART"), f"{pfx}.IDORDPART")

    _strict_str_nonempty(p.get("DenBene"),    f"{pfx}.DenBene")
    _strict_str(p.get("Counter"),             f"{pfx}.Counter")
    _strict_str(p.get("CodFiscal"),           f"{pfx}.CodFiscal")
    _strict_str(p.get("ContIBAN"),            f"{pfx}.ContIBAN")
    _strict_str(p.get("Banca"),               f"{pfx}.Banca")
    _opt_str(p.get("CodPartener"))
    _opt_int(p.get("IdPartener"),             f"{pfx}.IdPartener")

def _validate_tbl(t: dict, idx: int, valid_tmpid_set: set, tip: str):
    pfx = f"tbls[{idx}]"

    _strict_pos_int(t.get("TmpID"), f"{pfx}.TmpID")

    tmp_id_part = _strict_pos_int(t.get("TmpID_OrdPart"), f"{pfx}.TmpID_OrdPart")
    if tmp_id_part not in valid_tmpid_set:
        raise ValueError(
            f"{pfx}.TmpID_OrdPart={tmp_id_part} nu corespunde niciunui TmpID din parts"
        )

    # IDORDTBLP: MariaDB PK
    _mariadb_pk(t.get("IDORDTBLP"), f"{pfx}.IDORDTBLP", tip)

    # IDORDTBL: Access PK, mereu > 0
    _strict_pos_int(t.get("IDORDTBL"), f"{pfx}.IDORDTBL")

    _strict_str_nonempty(t.get("CodAI"),        f"{pfx}.CodAI")
    _strict_str(t.get("CodAngajament"),          f"{pfx}.CodAngajament")
    _strict_str(t.get("CodIndicator"),           f"{pfx}.CodIndicator")
    _strict_str(t.get("CodSSI"),                 f"{pfx}.CodSSI")
    _strict_float(t.get("TotalReceptii"),        f"{pfx}.TotalReceptii")
    _strict_float(t.get("PlatiAnt"),             f"{pfx}.PlatiAnt")
    _strict_float(t.get("Valoare"),              f"{pfx}.Valoare")
    _strict_float(t.get("Ramas"),                f"{pfx}.Ramas")
    _opt_int(t.get("IdClsf"),                    f"{pfx}.IdClsf")
    _opt_int(t.get("IdClsfAcc"),                 f"{pfx}.IdClsfAcc")
    _opt_int(t.get("IDRD"),                      f"{pfx}.IDRD")

def _validate_att(a: dict, idx: int, valid_tmpid_set: set, tip: str):
    pfx = f"atts[{idx}]"

    _strict_pos_int(a.get("TmpID"), f"{pfx}.TmpID")

    tmp_id_part = _opt_int(a.get("TmpID_OrdPart"), f"{pfx}.TmpID_OrdPart")
    if tmp_id_part is not None and tmp_id_part not in valid_tmpid_set:
        raise ValueError(
            f"{pfx}.TmpID_OrdPart={tmp_id_part} nu corespunde niciunui TmpID din parts"
        )

    # IDORDATTP: MariaDB PK
    _mariadb_pk(a.get("IDORDATTP"), f"{pfx}.IDORDATTP", tip)

    # IDORDATT: Access PK, mereu > 0
    _strict_pos_int(a.get("IDORDATT"), f"{pfx}.IDORDATT")

    _strict_str_nonempty(a.get("Imagine"), f"{pfx}.Imagine")


def _validate_doc(d: dict, idx: int, valid_tmpid_set: set, tip: str):
    logger.debug(f"Validating DOC index={idx} data={d}")

    pfx = f"docs[{idx}]"

    _strict_pos_int(d.get("TmpID"), f"{pfx}.TmpID")

    tmp_id_part = _opt_int(d.get("TmpID_OrdPart"), f"{pfx}.TmpID_OrdPart")
    if tmp_id_part is not None and tmp_id_part not in valid_tmpid_set:
        raise ValueError(
            f"{pfx}.TmpID_OrdPart={tmp_id_part} nu corespunde niciunui TmpID din parts"
        )

    # IDORDDOCP: MariaDB PK
    _mariadb_pk(d.get("IDORDDOCP"), f"{pfx}.IDORDDOCP", tip)

    # IDORDDOC: Access PK, mereu > 0
    _strict_pos_int(d.get("IDORDDOC"), f"{pfx}.IDORDDOC")

    tip_doc = _strict_str_nonempty(d.get("TipDoc"), f"{pfx}.TipDoc")
    if tip_doc != "text":
        _strict_str_nonempty(d.get("NumeDoc"), f"{pfx}.NumeDoc")
    else:
        _opt_str(d.get("NumeDoc"))

    _strict_str_nonempty(d.get("TipDoc"),  f"{pfx}.TipDoc")
    _opt_str(d.get("DocJust"))



def _validate_payload(data: dict, tip: str):
    if not isinstance(data, dict):
        raise ValueError("Payload trebuie sa fie dict")
    if "ord" not in data:
        raise ValueError("Cheie 'ord' lipsa din payload")

    _validate_ord_header(data["ord"], tip)

    for key in ("parts", "tbls", "atts", "docs"):
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

    for i, p in enumerate(parts):
        _validate_part(p, i, tip)

    valid_tmpid_set = {
        _strict_pos_int(p["TmpID"], f"parts[{i}].TmpID")
        for i, p in enumerate(parts)
    }

    tmp_ids = [_strict_pos_int(p["TmpID"], "TmpID") for p in parts]
    if len(tmp_ids) != len(set(tmp_ids)):
        raise ValueError("TmpID duplicat in 'parts'")

    for i, t in enumerate(tbls):
        _validate_tbl(t, i, valid_tmpid_set, tip)
    for i, a in enumerate(atts):
        _validate_att(a, i, valid_tmpid_set, tip)
    for i, d in enumerate(docs):
        _validate_doc(d, i, valid_tmpid_set, tip)


# ===========================================================================
# SECTIUNEA 4 — DB HELPERS (conexiune + retry)
# ===========================================================================

def _get_conn_cursor(data: dict):
    conn   = get_db_connection(data.get("db_name"))
    cursor = conn.cursor(dictionary=True)
    return conn, cursor


def _close(conn, cursor):
    for obj in (cursor, conn):
        if obj:
            try:
                obj.close()
            except Exception:
                pass


def _run_with_retry(operation, data: dict):
    """
    Executa operation(cursor) → result in tranzactie explicita.
    Retry automat la deadlock (errno 1213) sau lock timeout (errno 1205).
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
                    f"Deadlock/lock timeout dupa {MAX_DEADLOCK_RETRIES} incercari: {e.msg}"
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

def _stg_insert_ord(cursor, token: str, tip: str, data: dict):
    ord_ = data["ord"]
    cursor.execute("""
        INSERT INTO stg_Ord
            (Token, TipOperatie, IDORD, IDORDP, IDDF, NrORD, DataORD, Comp, CUAL, IdUnitate, Incarcat, Preluat, CodAngajament)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (
        token, tip,
        _strict_pos_int(ord_["IDORD"],  "ord.IDORD"),
        _strict_int(ord_["IDORDP"],     "ord.IDORDP"),
        _opt_int(ord_.get("IDDF"),      "ord.IDDF"),
        _strict_int(ord_["NrORD"],      "ord.NrORD"),
        _strict_str_nonempty(ord_["DataORD"], "ord.DataORD"),
        _strict_str_nonempty(ord_["Comp"],    "ord.Comp"),
        _opt_str(ord_.get("CUAL")),
        _opt_int(ord_.get("IdUnitate"), "ord.IdUnitate"),
        _strict_bool(ord_.get("Incarcat"), "ord.Incarcat"),
        _strict_bool(ord_.get("Preluat"),  "ord.Preluat"),
        _strict_str_nonempty(ord_.get("CodAngajament"), "ord.CodAngajament"),
    ))

    logger.debug(
        f"[STG][ORD] token={token} tip={tip} "
        f"IDORD={ord_['IDORD']} IDORDP={ord_['IDORDP']}"
    )


def _stg_insert_parts(cursor, token: str, parts: list):
    for p in parts:
        cursor.execute("""
            INSERT INTO stg_OrdPart
                (Token, TmpID, IDORDPART, IDORDPARTP,
                 Counter, DenBene, CodFiscal, ContIBAN, Banca, CodPartener, IdPartener)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            token,
            _strict_pos_int(p["TmpID"],     "TmpID"),
            _strict_pos_int(p["IDORDPART"], "IDORDPART"),
            _strict_int(p["IDORDPARTP"],    "IDORDPARTP"),
            _strict_str(p["Counter"],       "Counter"),
            _strict_str_nonempty(p["DenBene"], "DenBene"),
            _strict_str(p["CodFiscal"],     "CodFiscal"),
            _strict_str(p["ContIBAN"],      "ContIBAN"),
            _strict_str(p["Banca"],         "Banca"),
            _opt_str(p.get("CodPartener")),
            _opt_int(p.get("IdPartener"),   "IdPartener"),
        ))
    logger.debug(f"[STG][PART] token={token} count={len(parts)}")


def _stg_insert_tbls(cursor, token: str, tbls: list):
    for t in tbls:
        cursor.execute("""
            INSERT INTO stg_OrdTbl
                (Token, TmpID, TmpID_OrdPart, IDORDTBL, IDORDTBLP,
                 CodAI, CodAngajament, CodIndicator, CodSSI,
                 TotalReceptii, PlatiAnt, Valoare, Ramas,
                 IdClsf, IdClsfAcc, Explicatie, IDRD)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            token,
            _strict_pos_int(t["TmpID"],         "TmpID"),
            _strict_pos_int(t["TmpID_OrdPart"],  "TmpID_OrdPart"),
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
            _opt_int(t.get("IDRD"),             "IDRD"),
        ))
    logger.debug(f"[STG][TBL] token={token} count={len(tbls)}")


def _stg_insert_atts(cursor, token: str, atts: list):
    for a in atts:
        cursor.execute("""
            INSERT INTO stg_OrdAtt
                (Token, TmpID, TmpID_OrdPart, IDORDATT, IDORDATTP, Imagine)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (
            token,
            _strict_pos_int(a["TmpID"],                    "TmpID"),
            _opt_int(a.get("TmpID_OrdPart"),                "TmpID_OrdPart"),
            _strict_pos_int(a["IDORDATT"],                  "IDORDATT"),
            _strict_int(a["IDORDATTP"],                     "IDORDATTP"),
            _strict_str_nonempty(a["Imagine"],              "Imagine"),
        ))
    logger.debug(f"[STG][ATT] token={token} count={len(atts)}")

def _stg_insert_docs(cursor, token: str, docs: list):
    for d in docs:
        cursor.execute("""
            INSERT INTO stg_OrdDoc
                (Token, TmpID, TmpID_OrdPart, IDORDDOC, IDORDDOCP, DocJust, NumeDoc, TipDoc)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            token,
            _strict_pos_int(d["TmpID"],                    "TmpID"),
            _opt_int(d.get("TmpID_OrdPart"),                "TmpID_OrdPart"),
            _strict_pos_int(d["IDORDDOC"],                  "IDORDDOC"),
            _strict_int(d["IDORDDOCP"],                     "IDORDDOCP"),
            _strict_str_nonempty(d.get("DocJust"),          "DocJust"),
            _opt_str(d.get("NumeDoc")),
            _strict_str_nonempty(d["TipDoc"],               "TipDoc"),
        ))
    logger.debug(f"[STG][DOC] token={token} count={len(docs)}")


def _stg_insert_all(cursor, token: str, tip: str, data: dict):
    _stg_insert_ord(cursor,   token, tip, data)
    _stg_insert_parts(cursor, token, data.get("parts", []))
    _stg_insert_tbls(cursor,  token, data.get("tbls",  []))
    _stg_insert_atts(cursor,  token, data.get("atts",  []))
    _stg_insert_docs(cursor,  token, data.get("docs",  []))


# ===========================================================================
# SECTIUNEA 6 — COMMIT ADD
# ===========================================================================

def _resolve_fk(tmp_id_part: int, tmp_to_real: dict, entity: str) -> int:
    """
    Rezolva TmpID_OrdPart → IDORDPARTP real (MariaDB auto-increment).
    Raise daca TmpID lipseste din map.
    """
    logger.debug(f"[RESOLVE_FK] tmp_to_real={tmp_to_real}")

    idordpartp = tmp_to_real.get(tmp_id_part)
    if not idordpartp:
        raise ValueError(
            f"{entity}: TmpID_OrdPart={tmp_id_part} nu exista in map PART. "
            "Inconsistenta intre staging si commit."
        )
    return idordpartp


def _commit_add(cursor, token: str) -> dict:
    logger.info(f"[ADD] START token={token}")

    cursor.execute("SELECT * FROM stg_Ord WHERE Token=%s", (token,))
    stg_ord = cursor.fetchone()
    if not stg_ord:
        raise ValueError(f"stg_Ord negasit pentru token={token}")

    # INSERT FX_ORD — IDORD vine pre-calculat din Access
    cursor.execute("""
        INSERT INTO FX_ORD (IDORD, IDDF, NrORD, DataORD, Comp, CUAL, Incarcat, Preluat, CodAngajament)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (
        _strict_pos_int(stg_ord["IDORD"], "stg_Ord.IDORD"),
        _opt_int(stg_ord.get("IDDF"),     "IDDF"),
        _strict_int(stg_ord["NrORD"],     "NrORD"),
        stg_ord["DataORD"],
        _strict_str_nonempty(stg_ord["Comp"], "Comp"),
        _opt_str(stg_ord.get("CUAL")),
        _strict_bool(stg_ord.get("Incarcat"), "Incarcat"),
        _strict_bool(stg_ord.get("Preluat"),  "Preluat"),
        _strict_str_nonempty(stg_ord.get("CodAngajament"), "CodAngajament"),
    ))
    idordp = cursor.lastrowid
    logger.debug(f"[ADD][ORD] IDORDP={idordp} IDORD={stg_ord['IDORD']}")

    # PARTS — toate noi la ADD
    cursor.execute(
        "SELECT * FROM stg_OrdPart WHERE Token=%s ORDER BY TmpID", (token,)
    )
    stg_parts   = cursor.fetchall()
    tmp_to_real = {}   # TmpID → IDORDPARTP real
    part_map    = []

    for p in stg_parts:
        tmp_id = _strict_pos_int(p["TmpID"], "TmpID")
        cursor.execute("""
            INSERT INTO FX_ORD_PART
                (IDORDP, IDORDPART, Counter, DenBene, CodPartener,
                 IdPartener, CodFiscal, ContIBAN, Banca)
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

    logger.debug(f"[ADD][PART] inserted={len(part_map)}")

    # TBLS — toate noi la ADD
    cursor.execute(
        "SELECT * FROM stg_OrdTbl WHERE Token=%s ORDER BY TmpID", (token,)
    )
    tbl_map = []
    for t in cursor.fetchall():
        logger.debug(f"Processing TBL TmpID={t['TmpID']} TmpID_OrdPart={t['TmpID_OrdPart']}")
        tmp_id_part = _strict_pos_int(t["TmpID_OrdPart"], "TmpID_OrdPart")
        idordpartp  = _resolve_fk(tmp_id_part, tmp_to_real, f"TBL TmpID={t['TmpID']}")
        cursor.execute("""
            INSERT INTO FX_ORD_TBL
                (IDORDP, IDORDPARTP, IDORDTBL,
                 CodAI, CodAngajament, CodIndicator, CodSSI,
                 TotalReceptii, PlatiAnt, Valoare, Ramas,
                 IdClsf, IdClsfAcc, Explicatie, IDRD)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
            _opt_int(t.get("IDRD"),           "IDRD"),
        ))
        tbl_map.append({
            "TmpID":     _strict_pos_int(t["TmpID"], "TmpID"),
            "IDORDTBLP": cursor.lastrowid,
        })

    logger.debug(f"[ADD][TBL] inserted={len(tbl_map)}")

    # ATTS — toate noi la ADD
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
            INSERT INTO FX_ORD_ATT (IDORDP, IDORDPARTP, IDORDATT, Imagine)
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

    # DOCS — toate noi la ADD
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
            INSERT INTO FX_ORD_DOC (IDORDP, IDORDPARTP, IDORDDOC, DocJust, NumeDoc, TipDoc)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (
            idordp, idordpartp,
            _strict_pos_int(d["IDORDDOC"],     "IDORDDOC"),
            _strict_str_nonempty(d.get("DocJust"), "DocJust"),
            _opt_str(d.get("NumeDoc")),
            _strict_str_nonempty(d["TipDoc"],  "TipDoc"),
        ))
        doc_map.append({
            "TmpID":     _strict_pos_int(d["TmpID"], "TmpID"),
            "IDORDDOCP": cursor.lastrowid,
        })

    logger.debug(f"[ADD][DOC] inserted={len(doc_map)}")
    
    logger.info(
        f"[ADD] DONE IDORDP={idordp} "
        f"parts={len(part_map)} tbls={len(tbl_map)} "
        f"atts={len(att_map)} docs={len(doc_map)}"
    )
    return {
        "IDORDP":   idordp,
        "Part_Map": part_map,
        "TBL_Map":  tbl_map,
        "ATT_Map":  att_map,
        "DOC_Map":  doc_map,
    }


# ===========================================================================
# SECTIUNEA 7 — COMMIT MOD (DIFF SYNC PE MARIADB PK)
# ===========================================================================

def _sync_part(cursor, token: str, idordp: int) -> Tuple[dict, list]:
    """
    Diff sync PART pe IDORDPARTP (MariaDB PK):
      IDORDPARTP > 0 → UPDATE rand existent
      IDORDPARTP < 0 → INSERT rand nou
      DELETE randuri din DB cu IDORDPARTP absent din payload
    Returneaza:
      tmp_to_real: TmpID → IDORDPARTP real (pentru rezolvare FK copii)
      part_map:    [{TmpID, IDORDPARTP}] — doar randuri NOI
    """
    logger.debug(f"[MOD][PART] START idordp={idordp}")

    cursor.execute(
        "SELECT * FROM stg_OrdPart WHERE Token=%s ORDER BY TmpID", (token,)
    )
    stg_parts      = cursor.fetchall()
    tmp_to_real    = {}
    part_map       = []
    incoming_partp = set()   # PK-uri existente pastrate (>0)

    for p in stg_parts:
        tmp_id     = _strict_pos_int(p["TmpID"],    "TmpID")
        idordpartp = _strict_int(p["IDORDPARTP"],   "IDORDPARTP")

        if idordpartp > 0:
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
            cursor.execute("""
                INSERT INTO FX_ORD_PART
                    (IDORDP, IDORDPART, Counter, DenBene, CodPartener,
                     IdPartener, CodFiscal, ContIBAN, Banca)
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

    # DELETE: randuri existente in DB care lipsesc din payload
    if incoming_partp:
        ph = ",".join(["%s"] * len(incoming_partp))
        cursor.execute(
            f"DELETE FROM FX_ORD_PART WHERE IDORDP=%s AND IDORDPARTP NOT IN ({ph})",
            [idordp] + list(incoming_partp),
        )
    else:
        cursor.execute("DELETE FROM FX_ORD_PART WHERE IDORDP=%s", (idordp,))

    logger.debug(
        f"[MOD][PART] DONE upd={len(incoming_partp)} ins={len(part_map)} "
        f"del={cursor.rowcount}"
    )
    return tmp_to_real, part_map


def _sync_tbl(cursor, token: str, idordp: int, tmp_to_real: dict) -> list:
    """
    Diff sync TBL pe IDORDTBLP (MariaDB PK).
    IDORDTBLP > 0 → UPDATE. IDORDTBLP < 0 → INSERT.
    DELETE randuri absente din payload.
    """
    logger.debug(f"[MOD][TBL] START idordp={idordp}")

    cursor.execute(
        "SELECT * FROM stg_OrdTbl WHERE Token=%s ORDER BY TmpID", (token,)
    )
    stg_tbls      = cursor.fetchall()
    tbl_map       = []
    incoming_tblp = set()

    for t in stg_tbls:
        idordtblp   = _strict_int(t["IDORDTBLP"], "IDORDTBLP")
        tmp_id_part = _strict_pos_int(t["TmpID_OrdPart"], "TmpID_OrdPart")
        idordpartp  = _resolve_fk(tmp_id_part, tmp_to_real, f"TBL TmpID={t['TmpID']}")

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
            _opt_int(t.get("IDRD"),           "IDRD"),
        )

        if idordtblp > 0:
            cursor.execute("""
                UPDATE FX_ORD_TBL
                SET IDORDPARTP=%s, IDORDTBL=%s,
                    CodAI=%s, CodAngajament=%s, CodIndicator=%s, CodSSI=%s,
                    TotalReceptii=%s, PlatiAnt=%s, Valoare=%s, Ramas=%s,
                    IdClsf=%s, IdClsfAcc=%s, Explicatie=%s, IDRD=%s
                WHERE IDORDTBLP=%s AND IDORDP=%s
            """, valori_date + (idordtblp, idordp))
            if cursor.rowcount == 0:
                raise ValueError(
                    f"[MOD][TBL] IDORDTBLP={idordtblp} nu exista in FX_ORD_TBL "
                    f"sau nu apartine IDORDP={idordp}"
                )
            incoming_tblp.add(idordtblp)
        else:
            cursor.execute("""
                INSERT INTO FX_ORD_TBL
                    (IDORDP, IDORDPARTP, IDORDTBL,
                     CodAI, CodAngajament, CodIndicator, CodSSI,
                     TotalReceptii, PlatiAnt, Valoare, Ramas,
                     IdClsf, IdClsfAcc, Explicatie, IDRD    )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (idordp,) + valori_date)
            tbl_map.append({
                "TmpID":     _strict_pos_int(t["TmpID"], "TmpID"),
                "IDORDTBLP": cursor.lastrowid,
            })

    if incoming_tblp:
        ph = ",".join(["%s"] * len(incoming_tblp))
        cursor.execute(
            f"DELETE FROM FX_ORD_TBL WHERE IDORDP=%s AND IDORDTBLP NOT IN ({ph})",
            [idordp] + list(incoming_tblp),
        )
    else:
        cursor.execute("DELETE FROM FX_ORD_TBL WHERE IDORDP=%s", (idordp,))

    logger.debug(
        f"[MOD][TBL] DONE upd={len(incoming_tblp)} ins={len(tbl_map)} "
        f"del={cursor.rowcount}"
    )
    return tbl_map

def _sync_att(cursor, token: str, idordp: int, tmp_to_real: dict) -> list:
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
        imagine     = _strict_str_nonempty(a["Imagine"], "Imagine")

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
                INSERT INTO FX_ORD_ATT (IDORDP, IDORDPARTP, IDORDATT, Imagine)
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
            f"DELETE FROM FX_ORD_ATT WHERE IDORDP=%s AND IDORDATTP NOT IN ({ph})",
            [idordp] + list(incoming_attp),
        )
    else:
        cursor.execute("DELETE FROM FX_ORD_ATT WHERE IDORDP=%s", (idordp,))

    logger.debug(
        f"[MOD][ATT] DONE upd={len(incoming_attp)} ins={len(att_map)} "
        f"del={cursor.rowcount}"
    )
    return att_map

def _sync_doc(cursor, token: str, idordp: int, tmp_to_real: dict) -> list:
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
                SET IDORDPARTP=%s, IDORDDOC=%s, DocJust=%s, NumeDoc=%s, TipDoc=%s
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
                INSERT INTO FX_ORD_DOC (IDORDP, IDORDPARTP, IDORDDOC, DocJust, NumeDoc, TipDoc)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (
                idordp, idordpartp,
                _strict_pos_int(d["IDORDDOC"],     "IDORDDOC"),
                _opt_str(d.get("DocJust")),
                _strict_str_nonempty(d["NumeDoc"], "NumeDoc"),
                _strict_str_nonempty(d["TipDoc"],  "TipDoc"),
            ))
            doc_map.append({
                "TmpID":     _strict_pos_int(d["TmpID"], "TmpID"),
                "IDORDDOCP": cursor.lastrowid,
            })

    if incoming_docp:
        ph = ",".join(["%s"] * len(incoming_docp))
        cursor.execute(
            f"DELETE FROM FX_ORD_DOC WHERE IDORDP=%s AND IDORDDOCP NOT IN ({ph})",
            [idordp] + list(incoming_docp),
        )
    else:
        cursor.execute("DELETE FROM FX_ORD_DOC WHERE IDORDP=%s", (idordp,))

    logger.debug(
        f"[MOD][DOC] DONE upd={len(incoming_docp)} ins={len(doc_map)} "
        f"del={cursor.rowcount}"
    )
    return doc_map

def _commit_mod(cursor, token: str) -> dict:
    """
    Commit MOD.
    Lock FX_ORD (SELECT FOR UPDATE) inainte de orice write.
    Diff sync pe MariaDB PK pentru toate entitatile.
    """
    logger.info(f"[MOD] START token={token}")

    cursor.execute("SELECT * FROM stg_Ord WHERE Token=%s", (token,))
    stg_ord = cursor.fetchone()
    if not stg_ord:
        raise ValueError(f"stg_Ord negasit pentru token={token}")

    idordp = _strict_pos_int(stg_ord["IDORDP"], "stg_Ord.IDORDP")
    logger.info(f"[MOD] IDORDP={idordp}")

    # Lock pe parent — previne write concurent pe acelasi ORD
    cursor.execute("SELECT IDORDP FROM FX_ORD WHERE IDORDP=%s FOR UPDATE", (idordp,))
    if not cursor.fetchone():
        raise ValueError(f"[MOD] FX_ORD cu IDORDP={idordp} nu exista in DB")

    # UPDATE header ORD — include si IDORD (Access PK, pre-calculat)
    cursor.execute("""
        UPDATE FX_ORD
        SET IDORD=%s, NrORD=%s, DataORD=%s, Comp=%s, CUAL=%s, Incarcat=%s, Preluat=%s, IDRD=%s
        WHERE IDORDP=%s
    """, (
        _strict_pos_int(stg_ord["IDORD"], "IDORD"),
        _strict_int(stg_ord["NrORD"],     "NrORD"),
        stg_ord["DataORD"],
        _strict_str_nonempty(stg_ord["Comp"], "Comp"),
        _opt_str(stg_ord.get("CUAL")),
        _strict_bool(stg_ord.get("Incarcat"), "Incarcat"),
        _strict_bool(stg_ord.get("Preluat"),  "Preluat"),
        _opt_int(stg_ord.get("IDRD"), "IDRD"),
        idordp,
    ))
    logger.debug(f"[MOD] FX_ORD UPDATE rows={cursor.rowcount}")

    tmp_to_real, part_map = _sync_part(cursor, token, idordp)
    tbl_map               = _sync_tbl(cursor,  token, idordp, tmp_to_real)
    att_map               = _sync_att(cursor,  token, idordp, tmp_to_real)
    doc_map               = _sync_doc(cursor,  token, idordp, tmp_to_real)

    logger.info(
        f"[MOD] DONE IDORDP={idordp} "
        f"parts_new={len(part_map)} tbls_new={len(tbl_map)} "
        f"atts_new={len(att_map)} docs_new={len(doc_map)}"
    )
    return {
        "IDORDP":   idordp,
        "Part_Map": part_map,
        "TBL_Map":  tbl_map,
        "ATT_Map":  att_map,
        "DOC_Map":  doc_map,
    }


# ===========================================================================
# SECTIUNEA 8 — CLEANUP STAGING
# ===========================================================================

def _cleanup_stg_children(cursor, token: str):
    """
    Sterge randurile copil din stg dupa commit (CONFIRMED sau FAIL).
    Guard: nu sterge daca Status=PENDING.
    stg_Ord ramane pentru audit.
    """
    cursor.execute("SELECT Status FROM stg_Ord WHERE Token=%s", (token,))
    row = cursor.fetchone()
    if not row:
        raise ValueError(f"_cleanup_stg_children: token={token} negasit in stg_Ord")
    if row["Status"] == "PENDING":
        raise ValueError(
            f"_cleanup_stg_children: token={token} are Status=PENDING. "
            "Nu se sterge staging activ. Bug logic."
        )
    for tabel in ("stg_OrdPart", "stg_OrdTbl", "stg_OrdAtt", "stg_OrdDoc"):
        cursor.execute(f"DELETE FROM {tabel} WHERE Token=%s", (token,))
        logger.debug(f"[CLEANUP_STG] {tabel} rows={cursor.rowcount}")


# ===========================================================================
# SECTIUNEA 9 — PATCH (INSERT DIRECT, FARA STAGING)
# ===========================================================================
#
# Patch este folosit pentru adaugari punctuale dupa salvarea initiala.
# IDORDPARTP trebuie sa fie mereu > 0 (PART existent confirmat in DB).
# Daca e necesar un PART nou, se apeleaza intai patch/part, se obtine
# IDORDPARTP din response, apoi se foloseste la patch/tbl|att|doc.

def _assert_ord_exists(cursor, idordp: int):
    cursor.execute("SELECT 1 FROM FX_ORD WHERE IDORDP=%s", (idordp,))
    if not cursor.fetchone():
        raise ValueError(f"FX_ORD cu IDORDP={idordp} nu exista")


def _assert_part_belongs_to_ord(cursor, idordpartp: int, idordp: int):
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
    Insereaza PART-uri noi intr-un ORD existent.
    Returneaza [{TmpID, IDORDPARTP}].
    """
    _assert_ord_exists(cursor, idordp)
    part_map = []
    for r in rows:
        cursor.execute("""
            INSERT INTO FX_ORD_PART
                (IDORDP, IDORDPART, Counter, DenBene, CodPartener,
                 IdPartener, CodFiscal, ContIBAN, Banca)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            idordp,
            _strict_pos_int(r.get("IDORDPART"), "IDORDPART"),
            _strict_str(r.get("Counter"),       "Counter"),
            _strict_str_nonempty(r.get("DenBene"), "DenBene"),
            _opt_str(r.get("CodPartener")),
            _opt_int(r.get("IdPartener"),        "IdPartener"),
            _strict_str(r.get("CodFiscal"),     "CodFiscal"),
            _strict_str(r.get("ContIBAN"),       "ContIBAN"),
            _strict_str(r.get("Banca"),          "Banca"),
        ))
        part_map.append({
            "TmpID":      _strict_pos_int(r.get("TmpID"), "TmpID"),
            "IDORDPARTP": cursor.lastrowid,
        })
    logger.debug(f"[PATCH][PART] IDORDP={idordp} inserted={len(rows)}")
    return part_map


def _patch_tbl(cursor, idordp: int, rows: list) -> list:
    """Insereaza TBL-uri noi. Returneaza [{TmpID, IDORDTBLP}]."""
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
                 IdClsf, IdClsfAcc, Explicatie, IDRD)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
            _opt_int(r.get("IDRD"),               "IDRD"),
        ))
        tbl_map.append({
            "TmpID":     _strict_pos_int(r.get("TmpID"), "TmpID"),
            "IDORDTBLP": cursor.lastrowid,
        })
    logger.debug(f"[PATCH][TBL] IDORDP={idordp} inserted={len(rows)}")
    return tbl_map


def _patch_att(cursor, idordp: int, rows: list) -> list:
    """Insereaza ATT-uri noi. Returneaza [{TmpID, IDORDATTP}]."""
    _assert_ord_exists(cursor, idordp)
    att_map = []
    for r in rows:
        idordpartp = _strict_pos_int(r.get("IDORDPARTP"), "IDORDPARTP")
        _assert_part_belongs_to_ord(cursor, idordpartp, idordp)
        cursor.execute("""
            INSERT INTO FX_ORD_ATT (IDORDP, IDORDPARTP, IDORDATT, Imagine)
            VALUES (%s, %s, %s, %s)
        """, (
            idordp, idordpartp,
            _strict_pos_int(r.get("IDORDATT"),    "IDORDATT"),
            _strict_str_nonempty(r.get("Imagine"), "Imagine"),
        ))
        att_map.append({
            "TmpID":     _strict_pos_int(r.get("TmpID"), "TmpID"),
            "IDORDATTP": cursor.lastrowid,
        })
    logger.debug(f"[PATCH][ATT] IDORDP={idordp} inserted={len(rows)}")
    return att_map


def _patch_doc(cursor, idordp: int, rows: list) -> list:
    """Insereaza DOC-uri noi. Returneaza [{TmpID, IDORDDOCP}]."""
    _assert_ord_exists(cursor, idordp)
    doc_map = []
    for r in rows:
        idordpartp = _strict_pos_int(r.get("IDORDPARTP"), "IDORDPARTP")
        _assert_part_belongs_to_ord(cursor, idordpartp, idordp)
        cursor.execute("""
            INSERT INTO FX_ORD_DOC (IDORDP, IDORDPARTP, IDORDDOC, DocJust, NumeDoc, TipDoc)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (
            idordp, idordpartp,
            _strict_pos_int(r.get("IDORDDOC"),    "IDORDDOC"),
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
    Obligatoriu: ord.IDORDP < 0, ord.IDORD > 0, toate ...P < 0.
    """
    data = request.json
    try:
        _check_content_length()
        _validate_payload(data, "ADD")
        token = str(uuid.uuid4())

        logger.info(
            f"[save_staging] START token={token} "
            f"IDORD={data['ord']['IDORD']} IDORDP={data['ord']['IDORDP']}"
        )

        def operation(cursor):
            _stg_insert_all(cursor, token, "ADD", data)
            return {"token": token}

        result = _run_with_retry(operation, data)
        logger.info(f"[save_staging] OK token={token}")
        return jsonify(result), 200

    except Exception as e:
        logger.error(f"[save_staging] ERROR: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@ord_bp.route("/api/ord/update_staging", methods=["POST"])
@require_api_key
def update_staging():
    """
    Staging MOD (ordonantare existenta).
    Obligatoriu: ord.IDORDP > 0, ord.IDORD > 0.
    Copii: ...P > 0 (existent) sau < 0 (nou adaugat in MOD).
    """
    data = request.json
    try:
        _check_content_length()
        _validate_payload(data, "MOD")
        token = str(uuid.uuid4())

        logger.info(
            f"[update_staging] START token={token} "
            f"IDORD={data['ord']['IDORD']} IDORDP={data['ord']['IDORDP']}"
        )

        def operation(cursor):
            _stg_insert_all(cursor, token, "MOD", data)
            return {"token": token}

        result = _run_with_retry(operation, data)
        logger.info(f"[update_staging] OK token={token}")
        return jsonify(result), 200

    except Exception as e:
        logger.error(f"[update_staging] ERROR: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@ord_bp.route("/api/ord/confirm", methods=["POST"])
@require_api_key
def confirm():
    """
    Confirma (OK) sau anuleaza (FAIL) o operatie staging.

    status=OK:
      - FOR UPDATE pe stg_Ord (race condition protection)
      - Idempotency: eroare clara daca Status != PENDING
      - Roteaza pe TipOperatie: ADD | MOD
      - Cleanup stg copii dupa commit
    status=FAIL:
      - Marcheaza Status=FAIL + cleanup stg copii
    """
    data   = request.json
    token  = data.get("token")
    status = data.get("status")

    if not token:
        return jsonify({"error": "token lipsa"}), 400
    if status not in ("OK", "FAIL"):
        return jsonify({"error": "status invalid; valori acceptate: OK, FAIL"}), 400

    try:
        if status == "FAIL":
            def operation_fail(cursor):
                cursor.execute(
                    "UPDATE stg_Ord SET Status='FAIL' WHERE Token=%s AND Status='PENDING'",
                    (token,),
                )
                if cursor.rowcount > 0:
                    _cleanup_stg_children(cursor, token)
                else:
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
                "SELECT TipOperatie, Status FROM stg_Ord WHERE Token=%s FOR UPDATE",
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
                raise ValueError(f"TipOperatie necunoscut in stg_Ord: '{tip}'")

            cursor.execute(
                "UPDATE stg_Ord SET Status='CONFIRMED', DataConfirm=NOW() WHERE Token=%s",
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
    """Sterge tokenuri PENDING > 60 minute (si copiii lor). Returneaza {"deleted": N}."""
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
                for tabel in ("stg_OrdPart", "stg_OrdTbl", "stg_OrdAtt", "stg_OrdDoc"):
                    cursor.execute(f"DELETE FROM {tabel} WHERE Token IN ({ph})", tokens)
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
    Insereaza PART-uri noi intr-un ORD existent.
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
    Insereaza TBL-uri noi. IDORDPARTP trebuie sa fie > 0.
    Payload:  {db_name, IDORDP, rows: [{TmpID, IDORDPARTP, IDORDTBL, CodAI, ...}]}
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
    Insereaza ATT-uri noi. IDORDPARTP trebuie sa fie > 0.
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
    Insereaza DOC-uri noi. IDORDPARTP trebuie sa fie > 0.
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
    """Cascade FK sterge PART, TBL, ATT, DOC. Payload: {db_name, IDORDP}."""
    data = request.json
    try:
        idordp = _strict_pos_int(data.get("IDORDP"), "IDORDP")

        def operation(cursor):
            cursor.execute(
                "SELECT IDORDP FROM FX_ORD WHERE IDORDP=%s FOR UPDATE", (idordp,)
            )
            if not cursor.fetchone():
                raise ValueError(f"FX_ORD cu IDORDP={idordp} nu exista")
            cursor.execute("DELETE FROM FX_ORD WHERE IDORDP=%s", (idordp,))
            if cursor.rowcount == 0:
                raise ValueError(f"DELETE FX_ORD IDORDP={idordp}: 0 randuri afectate")
            return {"ok": True, "IDORDP": idordp}

        result = _run_with_retry(operation, data)
        logger.info(f"[delete] OK IDORDP={idordp}")
        return jsonify(result), 200

    except Exception as e:
        logger.error(f"[delete] ERROR: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500