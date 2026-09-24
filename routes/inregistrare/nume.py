# routes/inregistrare/nume.py
r"""
The database name for a new unit (slice 0075-02, plan 5.4 and decisions D3 / D12).

`1nn_SSSS`, uppercase: a three-digit number starting with 1, an underscore, and four
letters squeezed out of the unit's name.

  AVATAR SOFT SRL  ->  111_VTRS

WHY THE LETTERS ARE MOSTLY CONSONANTS (D12). Four letters have to stand for a name
that can be sixty characters long, and Romanian institution names are full of the
same vowels in the same places (`DIRECTIA`, `PRIMARIA`, `LICEUL`). Dropping the
vowels keeps what tells two names apart. When a name does not HAVE four consonants
the vowels come back, in the order they appear, to fill the gap -- and a name with
fewer than four letters altogether is refused rather than padded with invented ones.

WHY THE DIGITS NEVER CONTAIN A ZERO (D3, 5.4). Every existing database is `NNN_XXXX`
and the old numbers are handed out from `000` upward (`000_DEMO`, `001_GR23`,
`053_LTTR`, and `101_CCDP`, which predates the rule). Starting at `1` and forbidding
a zero digit carves out a block -- `111` to `199` -- that the old numbering cannot
reach, so a self-service name can never land on one an operator is about to type.

NO DIACRITICS ARE WRITTEN IN THIS FILE, and none are needed. Folding goes through
`unicodedata`: NFD splits a letter into its base and its marks, and dropping the
marks leaves the base. That covers both spellings Romanian is written in -- the
comma-below letters (S-comma, T-comma) and the cedilla ones ANAF actually sends --
plus A-breve, A-circumflex and I-circumflex, without a table of special cases and
without a single accented character in the source.

WHAT A NAME MAY CONTAIN (operator, 22.09.2026): word characters, whitespace and commas,
nothing else -- `[\w\s,]`. Python's `\w` is Unicode-aware, so letters with diacritics
count as word characters; the page uses `[\p{L}\p{N}_\s,]` for the same set (a JS `\w`
is ASCII-only and would take every diacritic out). Runs of whitespace become one space.
The page REMOVES anything else before the applicant sees it, including from the ANAF
name; this side only REFUSES, because by the time a name gets here nothing should be left
to remove.

Nothing here writes to the database. `used_prefixes` reads two lists; everything else
is pure.
"""
import re
import unicodedata

# The four letters of the suffix, and the vowels that get dropped first.
NAME_LENGTH = 4
VOWELS = "AEIOU"

# An existing database name: three digits, an underscore, then the letters.
_EXISTING_NAME = re.compile(r"^\d{3}_")

# Everything a unit name may NOT contain, and the whitespace runs folded to one space.
_NAME_FORBIDDEN = re.compile(r"[^\w\s,]")
_WHITESPACE_RUN = re.compile(r"\s+")

# The sentence the applicant reads when a name breaks the rule above.
NAME_CHARACTERS_MESSAGE = (
    "Denumirea unității poate conține doar litere, cifre, spații și virgule."
)


class NameTooShort(ValueError):
    """The unit's name has fewer than four letters, so no suffix can be built."""


class NoFreeNumber(RuntimeError):
    """All 81 numbers from 111 to 199 are taken. Eighty-one units would be a nice
    problem to have; it is raised rather than wrapped around into the old block."""


def normalize_name(denumire) -> str:
    """
    The name as it is checked and stored: NFC, every whitespace run one space, trimmed.

    NFC first, so a letter that arrives as a base plus a combining mark is judged as the
    one letter it is, not as a letter followed by a forbidden character.
    """
    text = "" if denumire is None else str(denumire)
    text = unicodedata.normalize("NFC", text)
    return _WHITESPACE_RUN.sub(" ", text).strip()


def has_forbidden_characters(denumire) -> bool:
    r"""True when the (normalized) name holds anything outside `[\w\s,]`."""
    return _NAME_FORBIDDEN.search(normalize_name(denumire)) is not None


def letter_key(denumire) -> str:
    """
    The `SSSS` half: four uppercase letters, consonants first.

    Raises NameTooShort when the name has fewer than four letters at all -- refused
    with a Romanian sentence by the caller rather than padded with something invented.
    """
    letters = _letters(denumire)
    if len(letters) < NAME_LENGTH:
        raise NameTooShort(denumire)

    key = [ch for ch in letters if ch not in VOWELS][:NAME_LENGTH]
    if len(key) < NAME_LENGTH:
        # D12: the vowels come back, in the order they appear in the name.
        for ch in letters:
            if ch in VOWELS:
                key.append(ch)
                if len(key) == NAME_LENGTH:
                    break
    return "".join(key)


def candidate_numbers():
    """`111`, `112` ... `119`, `121` ... `199` -- lowest first, never a zero digit."""
    for tens in range(1, 10):
        for units in range(1, 10):
            yield f"1{tens}{units}"


def first_free_number(used) -> str:
    """The lowest number in that sequence which nothing has taken yet."""
    for number in candidate_numbers():
        if number not in used:
            return number
    raise NoFreeNumber()


def db_name(number, key) -> str:
    """`1nn_SSSS`. Uppercase always: the server runs `lower_case_table_names = 0`,
    so `111_VTRS` and `111_vtrs` would be two different databases."""
    return f"{number}_{key}".upper()


def used_prefixes(conn) -> set:
    """
    Every three-digit number already spoken for, from BOTH places a name can live.

    `information_schema.SCHEMATA` is what exists on the server; `CAI.DbName` is what
    the registry believes exists. Finding F0 showed the two lists are not the same,
    so a number is free only when it is missing from both.

    The NUMBER is what is compared, not the whole name. Existing rows carry a
    distinct `NNN` each, which reads as a unit number rather than a disambiguator,
    and two units sharing `111` with different letters would break that.
    """
    used = set()
    cur = conn.cursor()
    cur.execute("SELECT SCHEMA_NAME FROM information_schema.SCHEMATA")
    for (name,) in cur.fetchall():
        _collect(used, name)
    cur.execute("SELECT DbName FROM CAI WHERE DbName IS NOT NULL")
    for (name,) in cur.fetchall():
        _collect(used, name)
    return used


def _collect(used, name):
    text = "" if name is None else str(name)
    if _EXISTING_NAME.match(text):
        used.add(text[:3])


def _letters(denumire) -> str:
    """The name folded to bare uppercase A-Z: marks stripped, everything else dropped."""
    text = "" if denumire is None else str(denumire)
    stripped = "".join(
        ch for ch in unicodedata.normalize("NFD", text) if not unicodedata.combining(ch)
    )
    return "".join(ch for ch in stripped.upper() if "A" <= ch <= "Z")
