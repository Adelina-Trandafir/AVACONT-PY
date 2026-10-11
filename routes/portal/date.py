"""
The READ-ONLY data routes of the web area (slice 0110-06).

    GET /api/portal/date/tree?an=&ss=&include_hidden=
    GET /api/portal/date/sumar?cod=
    GET /api/portal/date/rezervari?cod=
    GET /api/portal/date/receptii?cod=
    GET /api/portal/date/plati?cod=
    GET /api/portal/date/ddf?cod=            lists (slice 0110-08): the DDF revisions,
    GET /api/portal/date/ord?cod=                the ORD documents and
    GET /api/portal/date/note?cod=               the CAB correction notes of one angajament
    GET /api/portal/date/ddf-pdf/<idrev>     the SIGNED PDF bytes (slice 0110-08),
    GET /api/portal/date/ord-pdf/<idordp>        exactly as the desktop route sends them
    GET /api/portal/date/nc-pdf/<idnc>
    GET /api/portal/date/istoric?cod=        the FX_Istoric rows of one angajament (tab Istoric)
    GET /api/portal/date/extrase[?cod=]      bank statements: headers + operations (menu Extrase)
    GET /api/portal/date/clasificatii?an=            the classification tree + level names (menu Clasificatii)
    GET /api/portal/date/clasificatii-sumar?an=      last budget + corrections total per classification
    GET /api/portal/date/clasificatii-buget/<id>?an= budget versions + corrections of one classification
    GET /api/portal/date/parteneri                   the partners of the unit with their codes (menu Parteneri)
    GET /api/portal/date/clasificatii-verificare[?data=]  FOREXE credit against the K-BOT budget

THE QUERIES ARE NOT COPIED. Each route calls the function the desktop app's route already
runs (routes/forexe/{tree,sumar,rezervari,receptii,plati}.py) -- the undecorated one, reached
through `__wrapped__`, so the bearer guard of the desktop app is skipped and ours
(`require_portal_session`) stands in front instead. Those functions read exactly two things
from the outside: the query string, which is the portal request's own, and
`g.session.db_name`, which is set here from the portal session -- never from the request. So a
portal token reaches the unit it opened and no other, and the answer has exactly the shape the
desktop views already consume.

Only these eighteen readers are reachable, and all of them only SELECT. Adding a route to this
module means reading the function it wraps first: it must not write, and it must not read any
other part of `g.session`.
"""
import logging
from types import SimpleNamespace

from flask import g

from routes.forexe.clasificatii_edit import (
    get_clasificatie_buget,
    get_clasificatii_sumar_buget,
    get_clasificatii_tree,
    get_verificare_buget,
)
from routes.forexe.ddf import get_ddf
from routes.forexe.extrase_lista import get_extrase_lista
from routes.forexe.istoric import get_istoric
from routes.forexe.note_cab import get_note_cab
from routes.forexe.ord import get_ord
from routes.forexe.pdf import get_ddf_pdf, get_nc_pdf, get_ord_pdf
from routes.forexe.parteneri_edit import get_parteneri
from routes.forexe.plati import get_plati
from routes.forexe.receptii import get_receptii
from routes.forexe.rezervari import get_rezervari
from routes.forexe.sumar import get_sumar
from routes.forexe.tree import get_tree

from .portal import portal_bp, require_portal_session

logger = logging.getLogger(__name__)


def _reader(view):
    """The route function without the desktop guard. Fails at import, not at the first request."""
    inner = getattr(view, "__wrapped__", None)
    if inner is None:
        raise RuntimeError(f"{view.__name__} has no __wrapped__: cannot reuse it for the portal")
    return inner


_READERS = {
    "tree": _reader(get_tree),
    "sumar": _reader(get_sumar),
    "rezervari": _reader(get_rezervari),
    "receptii": _reader(get_receptii),
    "plati": _reader(get_plati),
    "ddf": _reader(get_ddf),
    "ord": _reader(get_ord),
    "note": _reader(get_note_cab),
    "istoric": _reader(get_istoric),
    "extrase": _reader(get_extrase_lista),
    "parteneri": _reader(get_parteneri),
    "clasificatii": _reader(get_clasificatii_tree),
    "clasificatii-sumar": _reader(get_clasificatii_sumar_buget),
    "clasificatii-verificare": _reader(get_verificare_buget),
}

# The signed PDFs: the desktop function takes the document id as an argument.
_PDF_READERS = {
    "ddf-pdf": _reader(get_ddf_pdf),
    "ord-pdf": _reader(get_ord_pdf),
    "nc-pdf": _reader(get_nc_pdf),
}


# Readers that take one id in the path.
_ID_READERS = {
    "clasificatii-buget": _reader(get_clasificatie_buget),
}


def _run(name):
    # The one thing the wrapped functions read from the session: the unit. It comes from the
    # portal session note (set by /api/portal/unit after checking the user owns it).
    g.session = SimpleNamespace(db_name=g.portal["db_name"], username=g.portal["email"])
    return _READERS[name]()


def _make(name):
    @require_portal_session
    def view():
        return _run(name)
    view.__name__ = f"portal_date_{name.replace('-', '_')}"
    portal_bp.add_url_rule(f"/api/portal/date/{name}", view_func=view, methods=["GET"])


def _make_pdf(name):
    @require_portal_session
    def view(doc_id):
        g.session = SimpleNamespace(db_name=g.portal["db_name"], username=g.portal["email"])
        response = _PDF_READERS[name](doc_id)
        # The bytes are a private document: never keep a copy in a shared cache, and show it inline.
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        if response.status_code == 200:
            response.headers["Content-Disposition"] = "inline"
        return response
    view.__name__ = f"portal_date_{name.replace('-', '_')}"
    portal_bp.add_url_rule(f"/api/portal/date/{name}/<int:doc_id>", view_func=view, methods=["GET"])


def _make_id(name):
    @require_portal_session
    def view(item_id):
        g.session = SimpleNamespace(db_name=g.portal["db_name"], username=g.portal["email"])
        return _ID_READERS[name](item_id)
    view.__name__ = f"portal_date_{name.replace('-', '_')}"
    portal_bp.add_url_rule(f"/api/portal/date/{name}/<int:item_id>", view_func=view, methods=["GET"])


for _name in _READERS:
    _make(_name)
for _name in _ID_READERS:
    _make_id(_name)
for _name in _PDF_READERS:
    _make_pdf(_name)
