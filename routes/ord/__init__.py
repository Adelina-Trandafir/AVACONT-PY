# routes/ord/__init__.py
"""
Blueprint ORD — punctul de intrare al pachetului.

Expune `ord_bp`, `logger`, constantele de limita payload si resolverele FK
specifice ORD (_resolve_fk / _resolve_fk_opt). Rutele sunt definite in
core.py / patch.py si se inregistreaza pe ord_bp prin importul de la final.

IMPORTANT pentru ordinea de import:
  ord_bp, logger, constantele si resolverele trebuie definite INAINTE de
  `from . import core, patch`, pentru ca lantul de importuri declansat de
  acea linie (core → commit → part/tbl/att/doc/rec) face
  `from . import logger, _resolve_fk, _resolve_fk_opt`.

  Resolverele stau aici (nu in commit.py) tocmai ca sa evite ciclul
  commit ↔ entitati: commit.py importa entitatile, iar entitatile au nevoie
  de resolvere — daca ar sta in commit.py ar inchide un ciclu de import.

NOTA refactor (split monolit ord.py v7):
  - Parsarea generica (_strict_*, _opt_int, _opt_str) -> utils.parsing.
  - Conexiune + retry (_run_with_retry, _get_conn_cursor, _close) -> utils.db_retry.
  - _mariadb_pk ramane in validation.py (regula ADD/MOD).
  - _resolve_fk / _resolve_fk_opt raman aici (rezolva TmpID -> MariaDB PK).
"""
import logging

from flask import Blueprint

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Switch logging verbose (debug). Seteaza False in productie.
# ---------------------------------------------------------------------------
DEBUG_LOG: bool = True


def _dlog(msg: str) -> None:
    """Log verbose doar daca DEBUG_LOG este activ (utilitar optional)."""
    if DEBUG_LOG:
        logger.debug(msg)


# ---------------------------------------------------------------------------
# SECTIUNEA 1 — CONSTANTE (limite payload, specifice schemei ORD)
# Referite in validation.py (_validate_payload / _check_content_length).
# ---------------------------------------------------------------------------
MAX_PAYLOAD_BYTES = 2 * 1024 * 1024   # 2 MB
MAX_PARTS         = 50
MAX_TBLS          = 500
MAX_ATTS          = 100
MAX_DOCS          = 100
MAX_RECS          = 2000              # max randuri FX_ORD_TBL_REC per payload


# ---------------------------------------------------------------------------
# HELPERI SPECIFICI ORD — rezolvare FK TmpID → MariaDB PK
# Folositi de tbl/att/doc/rec la commit. Definiti aici pentru a evita
# ciclul de import commit ↔ entitati.
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Blueprint
# ---------------------------------------------------------------------------
ord_bp = Blueprint("ord", __name__)

# Inregistrarea rutelor (la final, dupa ce ord_bp/logger/constantele/resolverele exista).
# core.py  -> save_staging, update_staging, confirm, cleanup_staging, delete
# patch.py -> /api/ord/patch/{part,tbl,att,doc}
from . import core, patch   # noqa: E402,F401