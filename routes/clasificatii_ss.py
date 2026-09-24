# routes/clasificatii_ss.py
"""
The one rule for `Clasificatii.Sector`, `Sursa` and `SS` (slice 0075-00).

Until 0075-00 the three were GENERATED columns and nobody had to think about them: the
server computed them from `right(Capitol, 2)`. They are written now, because MariaDB
refuses a stored generated column built out of another column (error 1901, proven on the
live server), and because the source letter was never readable from the capitol in the
first place -- `01A`, `01D`, `01F` and `01G` all end in `01`.

`SS` is NOT NULL, has NO default, and carries a foreign key into
`AVACONT_COMUN.DefaSursaSector`. An INSERT that omits it fails with 1364; one that invents
a value fails with 1452. Both are loud, which is the point: a silently wrong sector-source
is worse than a refused row.

WHAT THIS MODULE IS FOR. Every existing writer only ever had Capitol/Subcapitol/Articol/
Alineat to work with, and every one of them must keep behaving exactly as it did. So
`derive_ss(capitol)` reproduces the OLD generated expression, letter for letter. New code
that actually knows the source (the self-service provisioning of slice 0075, the Migrator
reading Access `Sursa`) passes it explicitly and reaches the eleven values the old
expression could never produce.

Nothing here touches the database. Every function is pure and takes what the caller has.
"""

# The sector CASE of the old generated column, extended with the four sectors
# DefaSursaSector knows and the old expression did not (03, 04, 05, 08). The historical
# four keep exactly the mapping they had.
_SECTOR_BY_ENDING = {
    "00": "01", "01": "01", "02": "02", "10": "02",
    "03": "03", "04": "04", "05": "05", "08": "08",
}

# The source letter the OLD expression produced. It knew only these two answers.
_LEGACY_SOURCE_BY_ENDING = {"00": "A", "01": "A", "02": "A", "10": "E"}

DEFAULT_SOURCE = "A"


def sector_of(capitol) -> str:
    """`Sector` for a capitol: the last two characters, mapped. Unknown ending -> ''."""
    return _SECTOR_BY_ENDING.get(_ending(capitol), "")


def source_of(capitol, sursa=None) -> str:
    """
    `Sursa` for a row.

    A `xx10` capitol is `E` whatever the caller says -- that is what the generated column
    produced, and the capitol is what the rest of the row is built on. Otherwise an
    explicit value wins (trimmed, uppercased, first character, since the column is
    `char(1)`). With nothing explicit, the answer the old expression gave: `A`.
    """
    ending = _ending(capitol)
    if ending == "10":
        return "E"
    text = (sursa or "").strip().upper()
    if text:
        return text[0]
    return _LEGACY_SOURCE_BY_ENDING.get(ending, DEFAULT_SOURCE)


def derive_ss(capitol, sursa=None) -> str:
    """
    `SS` for a row: sector + source, the value the generated column used to hold.

    Called with one argument it reproduces the old behaviour exactly, which is what every
    pre-0075 writer needs. Called with a source letter it produces any of the fourteen
    DefaSursaSector values.

    An unknown capitol ending yields a one-character string, which the foreign key
    refuses -- a near-blank, never a plausible wrong value.
    """
    return sector_of(capitol) + source_of(capitol, sursa)


def ss_values(capitol, sursa=None) -> tuple:
    """
    `(Sector, Sursa, SS)` for one row, in the order the INSERT lists them.

    The shape every write site uses, so that adding three columns to a statement costs one
    line at the call site instead of three derivations that could drift apart.
    """
    sector = sector_of(capitol)
    source = source_of(capitol, sursa)
    return sector, source, sector + source


def _ending(capitol) -> str:
    text = "" if capitol is None else str(capitol)
    return text[-2:] if len(text) >= 2 else text
