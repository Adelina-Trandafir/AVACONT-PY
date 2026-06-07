# routes/ddf/__init__.py
"""
Blueprint DDF — punctul de intrare al pachetului.

Expune `ddf_bp` (folosit la register_blueprint in app) si utilitarele de
logging comune submodulelor. Rutele sunt definite in core.py / prt.py / att.py
si se inregistreaza pe ddf_bp prin importul de la finalul fisierului.

IMPORTANT pentru ordinea de import:
  ddf_bp si _dlog trebuie definite INAINTE de `from . import core, prt, att`,
  pentru ca acele module fac `from . import ddf_bp, _dlog`.
"""
import logging

from flask import Blueprint

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Switch logging verbose (debug). Seteaza False in productie.
# ---------------------------------------------------------------------------
DEBUG_LOG: bool = True


def _dlog(msg: str) -> None:
    """Log verbose doar daca DEBUG_LOG este activ."""
    if DEBUG_LOG:
        logger.debug(msg)


# ---------------------------------------------------------------------------
# CONSTANTE — limite payload.
# Pastrate din monolit. Momentan NU sunt referite nicaieri in cod (nu inventez
# o folosire noua); le tin aici ca sa nu se piarda daca vrei sa le activezi.
# ---------------------------------------------------------------------------
MAX_PAYLOAD_BYTES = 2 * 1024 * 1024   # 2 MB
MAX_PARTS         = 50
MAX_TBLS          = 500
MAX_ATTS          = 100
MAX_DOCS          = 100

# ---------------------------------------------------------------------------
# Blueprint
# ---------------------------------------------------------------------------
ddf_bp = Blueprint("ddf", __name__)

# Inregistrarea rutelor (la final, dupa ce ddf_bp/_dlog exista)
from . import core, prt, att   # noqa: E402,F401