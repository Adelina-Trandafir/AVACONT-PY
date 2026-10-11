# routes/efactura/__init__.py
"""
Blueprint E-FACTURA (slice 00EF) -- entry point of the package.

Exposes `efactura_bp`. The routes live in submodules and register on it through the import at
the bottom, so `efactura_bp` must exist BEFORE that import (same pattern as routes/forexe).

    ef_config.py     the application's client data, read from the server environment
    cripto.py        encryption of the two tokens stored in AVACONT_COMUN.EF_Token
    oauth.py         the calls to ANAF's token address (code exchange, refresh)
    token_store.py   EF_Token / EF_TokenStart in AVACONT_COMUN
    tokens.py        the steps: start, spend the code, state, a live access token
    token_routes.py  POST /api/efactura/token/start, POST .../cod, GET .../stare   (slice 00EF-04)
    ubl.py           the UBL 2.1 / CIUS-RO XML of an issued invoice (pure)          (slice 00EF-06)
    validare.py      our own checks of an invoice + ANAF's public validation service (slice 00EF-06)
    facturi_store.py SQL of Unitati_Date + Unitati_Conturi (COMUN, 00EF-13) / EF_Clienti / EF_Facturi / EF_FacturiLinii (00EF-06)
    facturi.py       the rules: states, numbering, correction, storno, XML, validation (slice 00EF-06)
    factura_routes.py /api/efactura/furnizor, /um, /clienti, /facturi, ...         (slice 00EF-06)
    anaf_api.py      the calls to ANAF with the access token: upload, state, download, messages (slice 00EF-07)
    trimitere.py     send (check, validate, upload), state, download, messages        (slice 00EF-07)
    trimitere_routes.py /api/efactura/facturi/<id>/trimite|verifica|descarca, /mesaje (slice 00EF-07)
    primite_ubl.py   reads the UBL XML of a received invoice (pure)                       (slice 00EF-17)
    primite.py       sync from ANAF, list, detail, files, link to a DDF                    (slice 00EF-17)
    primite_routes.py /api/efactura/primite/...                                            (slice 00EF-17)

SECRETS: the client secret, the encryption key and both tokens exist on this server only. No route
returns them and no log line prints them.
"""
from flask import Blueprint

efactura_bp = Blueprint("efactura", __name__)

from . import token_routes  # noqa: E402,F401  registers the token routes on efactura_bp
from . import factura_routes  # noqa: E402,F401  registers the invoice routes on efactura_bp (slice 00EF-06)
from . import trimitere_routes  # noqa: E402,F401  registers the send / state / download routes (slice 00EF-07)
from . import primite_routes  # noqa: E402,F401  registers the received-invoice routes (slice 00EF-17)
