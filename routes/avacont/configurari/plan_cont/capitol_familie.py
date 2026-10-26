# routes/avacont/configurari/plan_cont/capitol_familie.py
"""
The chapter rule of the subunits (slice AV01-04): the budget chapter (Capitol, 65.01 ...) is shared ONLY inside one family, a subunit
and its children (the subunits that read the classifications and the budget of the parent, TZ_Subunitati.IdSubunitateParinte).
Two subunits that are not of the same family must have different chapters.

The database does not enforce it (the unique index on Capitol was dropped by sql/AVACONT/Configurari/PlanCont/AV01_04e): only the
window's save route (subunitati.py) does. The migrator writes straight to the database and does not use this rule.

TO SWITCH THE RULE OFF: set CAPITOL_DOAR_IN_FAMILIE = False. Then any subunit may have any chapter. Nothing else changes in the
server. The texts that mention the rule say so too, so change them with it:
    src/KBot.App/Avacont/Configurari/PlanCont/Views/SubunitateEditForm.Designer.vb  (tooltip of the chapter field)
    src/KBot.App/HelpContent/contabil/nomenclatoare/plan-conturi.md                 (the line about the chapter)
"""
import mysql.connector
from mysql.connector import errorcode

# THE SWITCH. True = a chapter may repeat only inside one parent/children family. False = no rule.
CAPITOL_DOAR_IN_FAMILIE = True

_PASI_MAX = 50  # a parent loop (never expected) is cut after this many steps


def _incarca(cursor) -> dict:
    """{IdSubunitate: (parent id or 0, chapter, denomination)} for every subunit. A database without the parent column has no children."""
    try:
        cursor.execute("SELECT IdSubunitate, IdSubunitateParinte, Capitol, Denumire FROM TZ_Subunitati")
    except mysql.connector.Error as e:
        if e.errno != errorcode.ER_BAD_FIELD_ERROR:
            raise
        cursor.execute("SELECT IdSubunitate, NULL, Capitol, Denumire FROM TZ_Subunitati")
    return {int(r[0]): (int(r[1]) if r[1] else 0, (r[2] or "").strip(), r[3] or "") for r in cursor.fetchall()}


def _radacina(rows: dict, idsub: int) -> int:
    """The top of the family: follows the parent links as far as they lead."""
    curent = idsub
    for _ in range(_PASI_MAX):
        parinte = rows[curent][0] if curent in rows else 0
        if not parinte or parinte not in rows:
            return curent
        curent = parinte
    return curent


def conflict_capitol(cursor, idsub, capitol: str):
    """
    The Romanian reason the chapter is refused, or None. `idsub` = the subunit being saved (None = a new one). A new subunit has no
    parent (the window cannot set one), so it is a family of its own. Call it inside the save's transaction, after the table lock.
    """
    if not CAPITOL_DOAR_IN_FAMILIE:
        return None
    rows = _incarca(cursor)
    propria = _radacina(rows, idsub) if idsub in rows else None
    cerut = capitol.strip().lower()
    for alt_id, (_parinte, alt_capitol, alt_denumire) in rows.items():
        if alt_id == idsub or alt_capitol.lower() != cerut:
            continue
        if _radacina(rows, alt_id) != propria:
            return (f"Capitolul {capitol.strip()} îl are deja subunitatea «{alt_denumire}». Același capitol îl pot avea doar o "
                    "subunitate și copiii ei (cele care citesc clasificațiile din părinte); celelalte au capitol diferit.")
    return None
