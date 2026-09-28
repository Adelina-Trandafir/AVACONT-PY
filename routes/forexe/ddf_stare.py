# routes/forexe/ddf_stare.py
"""
The send stage of a DDF revision (slice 0081-01): `FX_DDF_REV.StareTrimitere`.

    0 = not sent by K-BOT (also every revision written before slice 0081)
    1 = send started and interrupted (forexecab may be half changed)
    2 = sent, final PDF not generated yet (a new angajament's Rev 0)
    3 = final PDF generated

The column comes from sql/0081_ddf_rev_stare_trimitere.sql, run by hand per unit database.
A database where it has not been run yet must still show its DDFs, so the read route asks
`are_stare_trimitere` and reads 0 instead; the write routes refuse with a message that names
the script. Probed once per database, like `_are_att_img` in ddf_edit.py.

The state S0-S4 itself is derived on the CLIENT (KBot.Domain DdfRevisionStates), from this
stage, `Semnatura` and whether forexecab already has the revision. The server only stores.
"""

STAGE_NOT_SENT = 0
STAGE_INTERRUPTED = 1
STAGE_SENT_IN_PROGRESS = 2
STAGE_FINAL_PDF = 3
STAGES = (STAGE_NOT_SENT, STAGE_INTERRUPTED, STAGE_SENT_IN_PROGRESS, STAGE_FINAL_PDF)

MISSING_COLUMN_MESSAGE = (
    "Baza de date nu are încă coloana FX_DDF_REV.StareTrimitere. "
    "Rulați întâi scriptul sql/0081_ddf_rev_stare_trimitere.sql pe această bază."
)

_SQL_ARE_COLOANA = (
    "SELECT COUNT(*) FROM information_schema.COLUMNS "
    " WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'FX_DDF_REV' AND COLUMN_NAME = 'StareTrimitere'"
)

_PREZENTA = {}


def are_stare_trimitere(cursor, db_name: str) -> bool:
    """Does this database have FX_DDF_REV.StareTrimitere? Probed once, then remembered.

    Only a True answer is remembered: a database where the script is run later must start
    working without a server restart.
    """
    if _PREZENTA.get(db_name):
        return True
    cursor.execute(_SQL_ARE_COLOANA, (db_name,))
    row = cursor.fetchone()
    # Works for both cursor kinds used in this package (tuple and dictionary).
    value = next(iter(row.values())) if isinstance(row, dict) else (row[0] if row else 0)
    present = bool(value)
    if present:
        _PREZENTA[db_name] = True
    return present
