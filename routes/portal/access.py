"""K-BOT portal visibility, independent of desktop and other application rights."""
import logging
from functools import wraps

import mysql.connector
from flask import g

from .portal import _fail, _user_units, require_portal_session

logger = logging.getLogger(__name__)

DOCUMENT_ROLES = frozenset({"administrator", "director", "secretar"})
DOCUMENT_READERS = frozenset({"tree", "ddf", "ord", "ddf-pdf", "ord-pdf"})
TREE_FIELDS = ("CodAngajament", "Descriere", "Stare", "DataAngajamentNou", "Surse")


def is_accountant(role):
    return str(role or "").strip().casefold() == "contabil"


def require_portal_data(reader):
    """Check current unit membership and role before invoking any business reader.

    Only accountants can read accounting data. Document roles get DDF/ORD and
    the navigation tree; CAB notes belong to the accountant, including their PDFs.
    Re-read the role so existing sessions also obey a changed assignment.
    """
    def decorate(fn):
        @wraps(fn)
        @require_portal_session
        def wrapper(*args, **kwargs):
            try:
                units = _user_units(g.portal["email"])
            except mysql.connector.Error:
                logger.exception("portal data: cannot read current unit role")
                return _fail("DB_ERROR", "Drepturile contului nu au putut fi citite.", 500)
            unit = next((u for u in units if u["DC"] == g.portal["db_name"]), None)
            role = str((unit or {}).get("Rol") or "").strip().casefold()
            if not is_accountant(role) and not (
                    role in DOCUMENT_ROLES and reader in DOCUMENT_READERS):
                return _fail("ROL_INTERZIS", "Nu aveți dreptul de a consulta aceste informații.", 403)
            g.portal["role"] = role
            return fn(*args, **kwargs)
        return wrapper
    return decorate
