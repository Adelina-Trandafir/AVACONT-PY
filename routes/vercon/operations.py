"""SLICE-VC04-01: the operations of section VR, the gestiune types each one is valid for, and the common role that
grants it. The role rows live in AVACONT_COMUN (sql/VC04_01_roluri_vr.sql) and the operations a user holds are read
from Roluri_Operatii through routes/portal/drepturi.py -- never assumed from a Roluri field. A right is only half of
the answer: the TYPE limits the operation even when the role allows it (`allowed`).
No role grants everything by default: every grant is made by hand, role by role.
"""
from .capabilities import ALIMENTE, ALL, MATERIALE, MF, OI

_GA = frozenset({MATERIALE, ALIMENTE})
_OM = frozenset({OI, MF})

# operation -> (role code, label, gestiune types it is valid for, writes data)
OPERATIONS = {
    "read":          ("VR_CITIRE",        "citire",                         ALL, False),
    # SLICE-VC06-26: limited ANAF permission available to everyone with VR read, without new role grants.
    "parteneri_anaf": (None,               "parteneri: ANAF / cod fiscal negăsit", ALL, True),
    "nomenclatoare": ("VR_NOMENCLATOARE", "nomenclatoare (produse, categorii, gestionari)", ALL, True),
    "receptie":      ("VR_RECEPTIE",      "receptie (NIR)",                 ALL, True),
    "storno":        ("VR_STORNO",        "storno intrari",                 ALL, True),
    "consum":        ("VR_CONSUM",        "bon de consum",                  _GA, True),
    "retur":         ("VR_RETUR",         "retur",                          _GA, True),
    "alimente":      ("VR_ALIMENTE",      "lista de alimente",              frozenset({ALIMENTE}), True),
    "oi_predare":    ("VR_OI_PREDARE",    "OI/MF - bon de predare",         _OM, True),
    "oi_transfer":   ("VR_OI_TRANSFER",   "OI/MF - bon de transfer",        _OM, True),
    "oi_casare":     ("VR_OI_CASARE",     "OI/MF - bon de casare",          _OM, True),
    "mf":            ("VR_MF",            "mijloace fixe (evidenta, amortizare)", frozenset({MF}), True),
    "raport":        ("VR_RAPORT",        "rapoarte si export",             ALL, False),
    "import":        ("VR_IMPORT",        "import, reconciliere, activare", ALL, True),
    "configure":     ("VR_SETARI",        "setari si tipul gestiunii",      ALL, True),
    "close":         ("VR_INCHIDERE",     "inchidere an",                   _GA, True),
    "reopen":        ("VR_REDESCHIDERE",  "redeschidere an",                _GA, True),
}

WRITE_OPERATIONS = frozenset(k for k, v in OPERATIONS.items() if v[3])


def type_allows(operation, gestiune_type):
    """The type limits the operation even when the role allows it."""
    spec = OPERATIONS.get(operation)
    return bool(spec) and gestiune_type in spec[2]


def effective(operations, gestiune_type):
    """The operations the user may actually do in a gestiune of this type: right AND type."""
    return {op for op in operations if type_allows(op, gestiune_type)}


def is_write(operation):
    return operation in WRITE_OPERATIONS
