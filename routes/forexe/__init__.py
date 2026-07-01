# routes/forexe/__init__.py
"""
Blueprint FOREXE — punctul de intrare al pachetului.

Expune `forexe_bp`, `logger` si `_dlog`. Rutele sunt definite in submodule
(angajamente.py ...) si se inregistreaza pe forexe_bp prin importul de la final.

IMPORTANT (ordine import): forexe_bp / logger / _dlog trebuie definite INAINTE de
`from . import angajamente`, pentru ca submodulul face `from . import forexe_bp`.
(Acelasi tipar ca routes/ord/__init__.py si routes/ddf/__init__.py.)
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
# Blueprint
# ---------------------------------------------------------------------------
forexe_bp = Blueprint("forexe", __name__)

# Inregistrarea rutelor (la final, dupa ce forexe_bp/logger/_dlog exista).
# angajamente.py -> POST /api/forexe/angajamente/upsert
from . import angajamente  # noqa: E402,F401
