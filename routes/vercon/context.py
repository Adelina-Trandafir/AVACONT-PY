"""SLICE-VC02-01: the working context DC -> subunit -> gestiune -> year, resolved on the server.

A provider supplies the tree the signed-in user may see:
    provider.tree(unit, email) -> [{id, name, gestiuni: [{id, name, type, years:[int], populated:bool,
                                                           occupied_by: str|None}]}]
Production gets the real provider with VC03 (tables VC_*); until then `EmptyProvider` answers "nothing yet".
The local preview supplies a fixture provider. `resolve` is a pure function and is the only place that
validates a selection, so a gestiune of another subunit or a year of another gestiune can never be accepted.
"""
import hashlib
import json

from . import capabilities

EXERCISE_TYPES = (capabilities.MATERIALE, capabilities.ALIMENTE)   # one tz_<year> exercise per year
HISTORY_TYPES = (capabilities.OI, capabilities.MF)                  # one multi-year history, year = a filter


class SelectionInvalid(Exception):
    def __init__(self, message):
        super().__init__(message)
        self.reason = "SELECTION_CHANGED"


class EmptyProvider:
    def tree(self, unit, email):
        return []

    def set_type(self, unit, subunit_id, gestiune_id, new_type):
        raise SelectionInvalid("Gestiunea nu exista.")


def _pick(items, wanted, label, key="id"):
    """The chosen item; with no choice and exactly one option, that option is selected on its own."""
    if wanted in (None, ""):
        return items[0] if len(items) == 1 else None
    found = next((i for i in items if str(i[key]) == str(wanted)), None)
    if found is None:
        raise SelectionInvalid("%s selectata nu mai este disponibila. Reincarcati pagina." % label)
    return found


def _digest(value):
    return hashlib.sha1(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:16]


def resolve(tree, subunit=None, gestiune=None, year=None, *, auto_subunit=True):
    """Validate the selection against the tree and return the context.

    The `version` changes whenever the selection OR the options it was resolved from change; the page throws
    away any response whose version is not the latest one it asked for."""
    # The menu starts with an explicit subunit choice, even when only one exists.
    chosen_sub = _pick(tree, subunit, "Subunitatea") if auto_subunit or subunit not in (None, "") else None
    gestiuni = chosen_sub["gestiuni"] if chosen_sub else []
    chosen_gest = _pick(gestiuni, gestiune, "Gestiunea") if chosen_sub else None
    if gestiune not in (None, "") and chosen_sub is None:
        raise SelectionInvalid("Alegeti mai intai subunitatea.")
    years = sorted(chosen_gest["years"]) if chosen_gest else []
    chosen_year = None
    if chosen_gest:
        if year in (None, ""):
            chosen_year = years[-1] if years else None          # the latest year is the default
        else:
            if not str(year).isdigit() or int(year) not in years:
                raise SelectionInvalid("Anul selectat nu apartine gestiunii. Reincarcati pagina.")
            chosen_year = int(year)
    selection = {
        "subunit": chosen_sub["id"] if chosen_sub else None,
        "gestiune": chosen_gest["id"] if chosen_gest else None,
        "year": chosen_year,
        "type": chosen_gest["type"] if chosen_gest else None,
    }
    options = {
        "subunits": [{"id": s["id"], "name": s["name"]} for s in tree],
        "gestiuni": [{"id": g["id"], "name": g["name"], "type": g["type"]} for g in gestiuni],
        "years": years,
    }
    return {
        "selection": selection,
        "options": options,
        "year_mode": ("exercise" if selection["type"] in EXERCISE_TYPES else "filter") if selection["type"] else None,
        "gestiune": chosen_gest,          # internal: the server uses it, the route strips it from the answer
        "version": _digest([selection, options]),
    }
