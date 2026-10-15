"""SLICE-VC02-02: what each gestiune type may open, and which columns it shows.

One table drives BOTH the menu the page paints and the refusal of a direct API call, so the two cannot
disagree. Source: VERCON/MAPARE_ACCESS_WEB.md (ribbon + Form_Open rules of the Access system) and the
PLAN rules (BC/BR/ListaAlimente gating, no annual close for OI and MF, ListaAlimente only for Alimente).
Pure functions, no database.
"""
MATERIALE, ALIMENTE, OI, MF = "MATERIALE", "ALIMENTE", "OI", "MF"
TYPES = (MATERIALE, ALIMENTE, OI, MF)
ALL = frozenset(TYPES)

# key -> (label, group, types, needs) ; needs = the VR operation the form requires (routes/vercon/operations.py)
FORMS = {
    "stocuri":        ("Stocuri",                 "Stocuri",      ALL,                          "raport"),
    "produse":        ("Produse",                 "Configurari",  ALL,                          "nomenclatoare"),
    "baza_date":      ("Baza de date",           "Configurari",  ALL,                          "nomenclatoare"),
    "comisii":        ("Comisii",                "Configurari",  ALL,                          "nomenclatoare"),
    "parteneri":      ("Furnizori (Parteneri)",   "Configurari",  ALL,                          "read"),
    "gestionari":     ("Gestionari",              "Configurari",  ALL,                          "nomenclatoare"),
    "setari":         ("Unitate / setari",        "Configurari",  ALL,                          "configure"),
    "inchidere_an":   ("Inchidere an",            "Configurari",  frozenset({MATERIALE, ALIMENTE}), "close"),
    "nir":            ("Factură Cumpărare (NIR)",  "Intrari",      ALL,                          "receptie"),
    "storno":         ("Factura stornare (FCS)",   "Intrari",      ALL,                          "storno"),
    "bc":             ("Bon de consum",           "Iesiri",       frozenset({MATERIALE, ALIMENTE}), "consum"),
    "br":             ("Retur",                   "Iesiri",       frozenset({MATERIALE, ALIMENTE}), "retur"),
    "lista_alimente": ("Lista de alimente",       "Iesiri",       frozenset({ALIMENTE}),        "alimente"),
    "mf_registru":    ("Registru MF",             "OI / MF",      frozenset({MF}),              "mf"),
    "bp":             ("Bon de predare",          "OI / MF",      frozenset({OI, MF}),          "oi_predare"),
    "bt":             ("Bon de transfer",         "OI / MF",      frozenset({OI, MF}),          "oi_transfer"),
    "bs":             ("Bon de casare",           "OI / MF",      frozenset({OI, MF}),          "oi_casare"),
    "rapoarte":       ("Rapoarte",                "Rapoarte",     frozenset({MATERIALE, ALIMENTE}), "raport"),
}

# Documents that have a header + lines editor (the others are lists / settings handled in later slices).
DOCUMENT_FORMS = ("nir", "storno", "bc", "br", "lista_alimente", "bp", "bt", "bs")

_COMMON_LINE = [
    {"key": "produs", "label": "Produs", "type": "text", "required": True, "max": 100},
    {"key": "cant", "label": "Cantitate", "type": "decimal", "required": True},
    {"key": "pret", "label": "Pret", "type": "decimal", "required": True, "min": 0},
]
_MF_EXTRA = [   # fixed-asset extension of NIR: only the MF gestiune carries it
    {"key": "nr_inventar", "label": "Nr. inventar", "type": "text", "required": True, "max": 30},
    {"key": "data_intrarii", "label": "Data intrarii", "type": "date", "required": True},
    {"key": "durata", "label": "Durata (luni)", "type": "int", "required": True, "min": 1},
]
_HEADER = [
    {"key": "numar", "label": "Numar", "type": "int", "required": False, "min": 1},   # empty = automatic numbering
    {"key": "data", "label": "Data", "type": "date", "required": True},
    {"key": "explicatie", "label": "Explicatie", "type": "text", "required": False, "max": 255},
]


class FormUnavailable(Exception):
    def __init__(self, reason, message, status):
        super().__init__(message)
        self.reason, self.status = reason, status


def allowed(form, gestiune_type):
    """True when this gestiune type may open this form."""
    spec = FORMS.get(form)
    return bool(spec) and gestiune_type in spec[2]


def menu_for(gestiune_type, operations, occupied=False):
    """Menu entries for a gestiune: [{key,label,group,enabled,reason}]. Forms the type may NOT open are left out
    (they do not appear); forms it may open but the user cannot right now appear disabled with the reason."""
    entries = []
    for key, (label, group, types, needs) in FORMS.items():
        if gestiune_type not in types:
            continue
        reason = None
        if needs not in operations:
            reason = "Nu aveti dreptul necesar."
        elif _is_write(needs) and occupied:
            reason = "Gestiunea este ocupata de alt utilizator."
        entries.append({"key": key, "label": label, "group": group, "enabled": reason is None, "reason": reason, "write": _is_write(needs)})
    return entries


def check_open(form, gestiune_type, operations, occupied=False):
    """Server-side gate of a direct call. Raises FormUnavailable; the menu is only a convenience."""
    if form not in FORMS:
        raise FormUnavailable("FORM_UNKNOWN", "Formularul nu exista.", 404)
    if not allowed(form, gestiune_type):
        raise FormUnavailable("FORM_NOT_AVAILABLE", "Formularul nu este disponibil pentru acest tip de gestiune.", 403)
    needs = FORMS[form][3]
    if needs not in operations:
        raise FormUnavailable("FORBIDDEN", "Nu aveti dreptul necesar pentru aceasta operatie VERCON.", 403)
    if _is_write(needs) and occupied:
        raise FormUnavailable("GESTIUNE_OCCUPIED", "Gestiunea este ocupata de alt utilizator.", 409)


def columns_for(form, gestiune_type):
    """Header and line columns of a document form. MF-only columns are never returned for another type, so a
    preference saved on an MF gestiune cannot be applied to an Alimente one (the page keys preferences by type)."""
    if form not in DOCUMENT_FORMS:
        return {"header": [], "lines": []}
    lines = list(_COMMON_LINE)
    if form == "nir" and gestiune_type == MF:
        lines += _MF_EXTRA
    return {"header": list(_HEADER), "lines": lines}


def _is_write(operation):
    """Operations that change data need the gestiune not to be held by somebody else (same list as operations.py;
    kept here so the two modules do not import each other)."""
    return operation not in ("read", "raport")


def form_writes(form):
    """True when opening this form means working on data (the page then takes the exclusive access)."""
    return _is_write(FORMS[form][3])
