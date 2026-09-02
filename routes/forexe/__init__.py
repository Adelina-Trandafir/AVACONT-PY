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
# angajamente.py -> POST /api/forexe/angajamente/upsert, GET /api/forexe/angajamente
# tree.py        -> GET /api/forexe/tree
# sumar.py       -> GET /api/forexe/sumar
# rezervari.py   -> GET /api/forexe/rezervari
# receptii.py    -> GET /api/forexe/receptii
# plati.py       -> GET /api/forexe/plati
# ddf.py         -> GET /api/forexe/ddf
# istoric.py     -> GET /api/forexe/istoric
# ord.py         -> GET /api/forexe/ord
# ord_edit.py    -> POST /api/forexe/ord/genereaza, GET /api/forexe/ord/draft/<idordp>,
#                   GET /api/forexe/ord/zile, POST /api/forexe/ord/save,
#                   DELETE /api/forexe/ord/<idordp>,
#                   GET/PUT/DELETE /api/forexe/ord/att/<idordattp>/imagine  (felia 0049)
# pdf.py         -> GET/PUT /api/forexe/ddf/pdf/<idrev>, GET/PUT /api/forexe/ord/pdf/<idordp>
# prelucrare.py  -> POST /api/forexe/prelucrare (ingestia FOREXE; pasii 1-2 in 0048-02)
# asociere.py    -> GET/POST /api/forexe/asociere (editorul R<->H de ORICAND, 0048-04)
from . import angajamente  # noqa: E402,F401
from . import tree  # noqa: E402,F401
from . import sumar  # noqa: E402,F401
from . import rezervari  # noqa: E402,F401
from . import receptii  # noqa: E402,F401
from . import plati  # noqa: E402,F401
from . import ddf  # noqa: E402,F401
from . import istoric  # noqa: E402,F401
# `as ord_route`: importat simplu, numele `ord` ar umbri built-in-ul `ord()` in spatiul
# de nume al pachetului. Fisierul ramane `ord.py` (numele rutei), legarea nu.
from . import ord as ord_route  # noqa: E402,F401
# ord_edit.py = jumatatea de SCRIERE a ordonantarii (felia 0049). Fisier separat, ca ord.py
# (citirea vederii 0033) sa ramana neatins; `routes/ord/*` — clientul Access legacy pe
# X-Api-Key — nu se atinge deloc.
from . import ord_edit  # noqa: E402,F401
from . import pdf  # noqa: E402,F401
from . import prelucrare  # noqa: E402,F401
from . import asociere  # noqa: E402,F401
