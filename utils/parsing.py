# utils/parsing.py
"""
Parsare stricta a campurilor venite din Access (JSON).

Functii relocate verbatim din ddf.py (semantica neschimbata).
Pot fi refolosite si de alte blueprint-uri (ex. ord.py), dar
ATENTIE: _opt_int de aici converteste 0 -> None (conventia DDF/Access).
Daca un alt modul are nevoie de alta semantica pentru 0, NU refolositi
aceasta functie acolo fara verificare.
"""
from typing import Optional


def _strict_bool(v, field: str) -> int:
    """
    Parseaza un boolean Access (0/1/-1).
    Access trimite -1 pentru True, 0 pentru False.
    Returneaza 1 sau 0. Raise daca null/gol/invalid.
    """
    if v is None or (isinstance(v, str) and v.strip() == ""):
        raise ValueError(f"Camp '{field}': null sau gol (0/1 obligatoriu)")
    try:
        result = int(v)
    except (TypeError, ValueError):
        raise ValueError(f"Camp '{field}': '{v}' nu este 0/1 valid")
    if result == -1:   # Access TRUE
        return 1
    if result not in (0, 1):
        raise ValueError(f"Camp '{field}': {result} trebuie sa fie 0 sau 1")
    return result


def _strict_int(v, field: str) -> int:
    """Parseaza int, reject null/gol/non-numeric."""
    if v is None or (isinstance(v, str) and v.strip() == ""):
        raise ValueError(f"Camp '{field}': null sau gol (int obligatoriu)")
    try:
        return int(v)
    except (TypeError, ValueError):
        raise ValueError(f"Camp '{field}': '{v}' nu este int valid")


def _strict_pos_int(v, field: str) -> int:
    """Parseaza int strict pozitiv (> 0). Reject 0, negativ, null."""
    result = _strict_int(v, field)
    if result <= 0:
        raise ValueError(f"Camp '{field}': {result} trebuie sa fie > 0")
    return result


def _strict_float(v, field: str) -> float:
    """Parseaza float, reject null/gol/non-numeric."""
    if v is None or (isinstance(v, str) and v.strip() == ""):
        raise ValueError(f"Camp '{field}': null sau gol (float obligatoriu)")
    try:
        return float(v)
    except (TypeError, ValueError):
        raise ValueError(f"Camp '{field}': '{v}' nu este float valid")


def _strict_str(v, field: str) -> str:
    """Parseaza string, reject null. String gol admis."""
    if v is None:
        raise ValueError(f"Camp '{field}': null (string obligatoriu)")
    return str(v)


def _strict_str_nonempty(v, field: str) -> str:
    """Parseaza string non-gol (dupa strip). Reject null si whitespace-only."""
    s = _strict_str(v, field)
    if s.strip() == "":
        raise ValueError(f"Camp '{field}': string gol (valoare obligatorie)")
    return s


def _opt_int(v, field: str) -> Optional[int]:
    """
    Camp int optional (FK nullable):
      None        -> NULL (FK neset)
      0           -> NULL (FK neset — conventia Access pentru camp gol)
      int         -> int
      string gol  -> raise (trimite null explicit, nu string gol)
      non-numeric -> raise
    """
    if v is None:
        return None
    if isinstance(v, str) and v.strip() == "":
        raise ValueError(
            f"Camp optional '{field}': string gol invalid — trimite null sau int"
        )
    try:
        result = int(v)
        return result if result != 0 else None
    except (TypeError, ValueError):
        raise ValueError(f"Camp optional '{field}': '{v}' nu este int valid")


def _opt_str(v) -> Optional[str]:
    """None ramane None. Orice altceva devine str."""
    return None if v is None else str(v)