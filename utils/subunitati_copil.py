# utils/subunitati_copil.py
"""
Child subunits per sector+source (slice AV01-11), for the NOTICE the classification pages show.

A child subunit (TZ_Subunitati.IdSubunitateParinte filled, as the transfer from Access leaves it) has the SAME sector+source as its parent, so the classifications and the budget it
works on are the parent's: they are the data of one unit (the row of `Unitati` named by that sector+source and the year). Nothing is blocked and nothing is copied; the pages only say,
for such a source, that the work goes straight into the parent's data. A child never has classifications or a budget of its own.

The notice is advice, so a database that does not have TZ_Subunitati / IdSubunitateParinte yet (not synchronised since AV01-04 / AV01_04d) simply gets no notice: logged, never an error.
"""
import logging

import mysql.connector

logger = logging.getLogger(__name__)

_SQL_CHILDREN = (
    "SELECT C.SursaSector AS Ss, C.Denumire AS Copil, P.Denumire AS Parinte "
    "  FROM TZ_Subunitati C "
    "  JOIN TZ_Subunitati P ON P.IdSubunitate = C.IdSubunitateParinte "
    " WHERE C.IdSubunitateParinte IS NOT NULL "
    " ORDER BY C.SursaSector, C.Denumire"
)

_ER_BAD_FIELD = 1054
_ER_NO_SUCH_TABLE = 1146


def child_sources(cursor) -> dict:
    """
    {sector+source: {"parinte": the parent's name (several names joined by ", "), "copii": [the children's names]}} for every source that has a child
    subunit. `cursor` is a dictionary cursor on the unit database.
    """
    try:
        cursor.execute(_SQL_CHILDREN)
        rows = cursor.fetchall()
    except mysql.connector.Error as e:
        if e.errno in (_ER_BAD_FIELD, _ER_NO_SUCH_TABLE):
            logger.warning("[subunitati_copil] nu exista TZ_Subunitati / IdSubunitateParinte in aceasta baza (%s): fara mesaj despre subunitati copil", e)
            return {}
        raise
    result = {}
    for r in rows:
        ss = str(r["Ss"] or "").strip().upper()
        if not ss:
            continue
        entry = result.setdefault(ss, {"parinte": [], "copii": []})
        parent = str(r["Parinte"] or "").strip()
        child = str(r["Copil"] or "").strip()
        if parent and parent not in entry["parinte"]:
            entry["parinte"].append(parent)
        if child and child not in entry["copii"]:
            entry["copii"].append(child)
    return {ss: {"parinte": ", ".join(e["parinte"]), "copii": e["copii"]} for ss, e in result.items()}
