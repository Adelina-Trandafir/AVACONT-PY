# routes/forexe/budget_on_day.py
"""
The budget of a classification ON A DAY (slice 0102) -- what the fundamentation document (DDF)
shows as «Buget» and measures «Valoare ramasa» against.

WHY IT EXISTS. `FX_Indicatori.Credit_Bugetar` (and `FX_Rezervari.R_CreditBug`, stamped from it
when the reservation rows are written) only hold what FOREXE says TODAY. After a budget
rectification an old reservation measured against today's smaller budget came out negative. The
DDF has to use the budget the classification had on the revision's date.

THE RULE (operator, 02.10.2026)
  1. Version: among the `Clasificatii_Buget` rows of the classification for the year of the day
     (`An = YEAR(day)`), the one with the greatest `DataInceput <= day`. None -> no budget.
  2. Rectifications: the `Clasificatii_Rectificari` rows of the classification dated from the
     version's `DataInceput` up to the day, both included. They are NOT budget; each one changes
     the quarter(s) whose `TrimN` it carries. A rectification older than the version is already
     inside the version and is left out.
  3. CUMULATIVE up to the quarter of the day: quarter q = (month - 1) // 3 + 1, and the result is
     the sum of Trim1..Trimq of the version plus the same quarters of its rectifications. There
     is no yearly total anywhere (operator: a total is not correct there).

`None` means the day has no budget version; the caller keeps today's `Credit_Bugetar` and tells
the operator so. This module never decides that fallback.
"""
import logging
from datetime import date, datetime
from typing import Optional

logger = logging.getLogger(__name__)

_SQL_VERSION = (
    "SELECT DataInceput, Trim1, Trim2, Trim3, Trim4 FROM Clasificatii_Buget "
    " WHERE IdClsf = %s AND An = %s AND DataInceput <= %s "
    " ORDER BY DataInceput DESC LIMIT 1"
)

_SQL_CORRECTIONS = (
    "SELECT Trim1, Trim2, Trim3, Trim4 FROM Clasificatii_Rectificari "
    " WHERE IdClsf = %s AND DATE(Data) >= %s AND DATE(Data) <= %s"
)


def as_day(value) -> Optional[date]:
    """A date, a datetime or an ISO 'YYYY-MM-DD...' text -> a date; anything else -> None."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def quarter_of(day: date) -> int:
    """1..4, the quarter the day falls in."""
    return (day.month - 1) // 3 + 1


def _amount(value) -> float:
    return 0.0 if value is None else float(value)


def cumulative_sum(version: dict, corrections: list, quarter: int) -> float:
    """Trim1..Trim`quarter` of the version plus the same quarters of every correction."""
    total = 0.0
    for k in range(1, quarter + 1):
        total += _amount(version.get(f"Trim{k}"))
        for c in corrections:
            total += _amount(c.get(f"Trim{k}"))
    return round(total, 2)


def budget_on_day(cursor, id_clsf: int, day) -> Optional[float]:
    """The cumulative budget of the classification on `day`, or None when no version covers it.

    `cursor` must return dict rows. Both queries are drained with `fetchall` so the connection is
    left clean for the caller's next statement.
    """
    day = as_day(day)
    if day is None or not id_clsf:
        return None

    cursor.execute(_SQL_VERSION, (int(id_clsf), day.year, day))
    versions = cursor.fetchall()
    if not versions:
        return None
    version = versions[0]

    start = as_day(version["DataInceput"])
    cursor.execute(_SQL_CORRECTIONS, (int(id_clsf), start, day))
    corrections = cursor.fetchall()
    return cumulative_sum(version, corrections, quarter_of(day))


class BudgetByDay:
    """`budget_on_day` with a memory: the lines of a document ask per (classification, day), and
    several lines share both. `missing` collects what had no version, for the operator's notice."""

    def __init__(self, cursor):
        self._cursor = cursor
        self._memory = {}
        self.missing = {}

    def value(self, id_clsf: int, day) -> Optional[float]:
        day = as_day(day)
        key = (int(id_clsf or 0), day)
        if key not in self._memory:
            self._memory[key] = budget_on_day(self._cursor, key[0], day)
        return self._memory[key]

    def note_missing(self, id_clsf: int, clsf: str, day) -> None:
        """Remembers that `clsf` had no budget version on `day` (one entry per pair)."""
        day = as_day(day)
        if day is not None:
            self.missing.setdefault((int(id_clsf or 0), day), clsf)

    def warnings(self) -> list:
        """One sentence per (classification, day) that fell back to today's credit."""
        messages = []
        for (_, day), clsf in sorted(self.missing.items(), key=lambda kv: (kv[1], kv[0][1])):
            messages.append(
                f"Clasificația {clsf} nu are un buget cu «Început» până la {day:%d.%m.%Y}; la "
                "«Buget» s-a folosit creditul bugetar de azi din FOREXE. Adăugați bugetul în "
                "«Clasificații bugetare» (o versiune cu data de început potrivită)."
            )
        return messages
