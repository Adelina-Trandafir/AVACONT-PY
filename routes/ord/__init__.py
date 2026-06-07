# routes/ord/__init__.py
"""
Blueprint ORD — punctul de intrare al pachetului.

Expune `ord_bp` (folosit la register_blueprint in app), `logger` si constantele
de limita payload. Rutele sunt definite in core.py / patch.py si se inregistreaza
pe ord_bp prin importul de la finalul fisierului.

IMPORTANT pentru ordinea de import:
  ord_bp, logger si constantele trebuie definite INAINTE de
  `from . import core, patch`, pentru ca acele module fac
  `from . import ord_bp, logger, MAX_PARTS, ...`.

NOTA refactor (split monolit ord.py v7):
  - Helperii de parsing (_strict_*, _opt_int, _opt_str) -> utils.parsing.
  - Conexiune + retry (_run_with_retry, _get_conn_cursor, _close) -> utils.db_retry.
  - Helperii specifici ORD (_mariadb_pk, _resolve_fk, _resolve_fk_opt) raman in
    pachet (validation.py / commit.py) pentru ca depind de logica ADD/MOD si de
    rezolvarea TmpID -> MariaDB PK, specifice ORD.
  - Comportamentul de logging din monolit (logger.debug / logger.info) se pastreaza
    neschimbat in module. _dlog este oferit doar ca utilitar optional, paritate DDF.
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
# SECTIUNEA 1 — CONSTANTE (limite payload)
# Referite in validation.py (_validate_payload / _check_content_length).
# ---------------------------------------------------------------------------
MAX_PAYLOAD_BYTES = 2 * 1024 * 1024   # 2 MB
MAX_PARTS         = 50
MAX_TBLS          = 500
MAX_ATTS          = 100
MAX_DOCS          = 100
MAX_RECS          = 2000              # max randuri FX_ORD_TBL_REC per payload

# ---------------------------------------------------------------------------
# Blueprint
# ---------------------------------------------------------------------------
ord_bp = Blueprint("ord", __name__)

# Inregistrarea rutelor (la final, dupa ce ord_bp/logger/constantele exista).
# core.py  -> save_staging, update_staging, confirm, cleanup_staging
# patch.py -> /api/ord/patch/{part,tbl,att,doc}
from . import core, patch   # noqa: E402,F401