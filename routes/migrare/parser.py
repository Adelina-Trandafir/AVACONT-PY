# routes/migrare/parser.py
# -----------------------------------------------------------------------------
# Access says it one way, MariaDB accepts it another. This is the one place that
# translates, and it translates ONCE: the analysis measures the parsed value and
# the write sends the parsed value, so what was measured is what travels.
#
# That was the whole defect class. `validate._DATE_FORMATS` already ACCEPTED
# «04/28/26 15:28:03» when checking, but the write sent the original string and
# MariaDB answered:
#
#   1292 (22007): Incorrect datetime value: '04/28/26 15:28:03' for column
#   `000_DEMO`.`FX_Angajamente`.`DTQ` at row 1
#
# A checker that coerces in order to judge, next to a writer that does not
# coerce, is a checker that lies.
#
# The TARGET decides, always. The MariaDB column type says what shape the value
# has to take; Access is only where it came from. Nothing here guesses from the
# Access type.
#
# WHAT IT WILL NOT DO: invent a value. A thing it cannot read is handed on
# UNCHANGED, so that `validate.check_value` reports it as the blocking TIP
# finding it is. Silently substituting a zero or a NULL for a value nobody could
# read is worse than refusing the run.
# -----------------------------------------------------------------------------

import datetime
import decimal
import logging
import re

logger = logging.getLogger(__name__)

# --- how an ambiguous date is read -------------------------------------------
# «04/28/26» is not ambiguous: 28 cannot be a month. «04/05/26» is, and someone
# has to decide. The decision, and the reason for it:
#
#   * TWO-DIGIT year with «/» -> MONTH first. That is mdbtools' own default
#     output format (%m/%d/%y), and mdbtools is what produces our rows.
#   * FOUR-DIGIT year with «/» -> DAY first. Those come from Access TEXT columns
#     typed by a person on a Romanian system, not from mdbtools.
#   * «.» or «-» as separator -> DAY first. European notation, always.
#
# EVERY ambiguous date is written into the parse log with the reading that was
# chosen, so the operator can audit the choice instead of trusting it.
SLASH_TWO_DIGIT_YEAR_IS_MONTH_FIRST = True
SLASH_FOUR_DIGIT_YEAR_IS_MONTH_FIRST = False

# A two-digit year below this belongs to the 2000s, at or above it to the 1900s.
# Same pivot POSIX and MariaDB use.
TWO_DIGIT_YEAR_PIVOT = 70

_TEXT_TYPES = ("char", "varchar", "text", "tinytext", "mediumtext", "longtext",
               "enum", "set")
_INT_TYPES = ("tinyint", "smallint", "mediumint", "int", "integer", "bigint")
_DECIMAL_TYPES = ("decimal", "numeric")
_FLOAT_TYPES = ("float", "double", "real")
_DATETIME_TYPES = ("datetime", "timestamp")
_BINARY_TYPES = ("blob", "tinyblob", "mediumblob", "longblob", "varbinary",
                 "binary")
_BOOL_TYPES = ("bit", "bool", "boolean")

# Whitespace INSIDE the digits of a number. Access never writes one, so meeting
# one means the value is not a plain number -- see `_number_from_text`.
_INNER_SPACE = re.compile(r"\S[\s  ]\S")

# The three numbers of a date, the separator that joined them, and whatever tail
# is left (the time of day, usually). The separator is CAPTURED rather than
# searched for afterwards: «.» and «/» are read differently, so guessing which
# one was used would be guessing about the day and the month.
_DATE_PART = re.compile(
    r"^\s*(?P<a>\d{1,4})\s*(?P<sep>[/.\-])\s*(?P<b>\d{1,2})\s*(?P=sep)\s*"
    r"(?P<c>\d{1,4})(?P<tail>.*)$")

_TIME_PART = re.compile(
    r"^[\sT]*(?P<h>\d{1,2}):(?P<m>\d{2})(?::(?P<s>\d{2}))?"
    r"(?:\.(?P<frac>\d+))?\s*(?P<ampm>[AaPp][Mm])?\s*$")

_TIME_ONLY = re.compile(
    r"^\s*(?P<h>\d{1,3}):(?P<m>\d{2})(?::(?P<s>\d{2}))?\s*(?P<ampm>[AaPp][Mm])?\s*$")

# Yes/No the way Access, Excel and a Romanian operator write it.
_TRUE_WORDS = ("true", "yes", "da", "on", "y", "t", "adevarat", "adevărat")
_FALSE_WORDS = ("false", "no", "nu", "off", "n", "f", "fals")


class Conversion(object):
    """One value the parser changed. Everything here goes into the parse log."""

    __slots__ = ("column", "before", "after", "note", "ambiguous")

    def __init__(self, column, before, after, note, ambiguous=False):
        self.column = column
        self.before = before
        self.after = after
        self.note = note
        self.ambiguous = ambiguous


def parse_row(row, target_columns):
    """
    A copy of `row` with every value shaped the way its TARGET column wants it,
    plus the list of `Conversion`s that were needed.

    `row` is already keyed by the target's exact column names (see
    `validate.with_target_names`). A key with no target column is left alone --
    it is reported as missing elsewhere, and inventing a type for it here would
    only hide that.
    """
    out = {}
    changes = []
    for name, value in row.items():
        meta = target_columns.get(name)
        if meta is None:
            out[name] = value
            continue
        new_value, note, ambiguous = parse_value(meta, value)
        out[name] = new_value
        if note is not None:
            changes.append(Conversion(name, value, new_value, note, ambiguous))
    return out, changes


def parse_value(meta, value):
    """
    One value, shaped for one target column. Returns (value, note, ambiguous);
    `note` is None when nothing had to change.

    Never raises. A value this cannot read comes back untouched, with no note,
    and `validate.check_value` then reports it as a TIP finding -- which is a
    blocking one, so nothing is written on a guess.
    """
    try:
        return _parse_value(meta, value)
    except Exception:
        # Cannot happen by design; if it ever does, the migration must not die
        # for it and the trace must not be lost.
        logger.exception("migrare/parsare: «%s» (%s) nu a putut fi prelucrată",
                         meta.get("nume"), type(value).__name__)
        return value, None, False


def _parse_value(meta, value):
    tip = (meta.get("tip") or "").lower()

    if value is None:
        return None, None, False

    # An empty string is not a value. In a text column it is a legitimate one; in
    # any other column MariaDB either refuses it or silently reads it as zero,
    # depending on sql_mode -- and a silent zero is the worse of the two.
    if isinstance(value, str) and not value.strip() and tip not in _TEXT_TYPES:
        return None, "text gol → NULL (coloana nu e de tip text)", False

    if tip in _TEXT_TYPES:
        return _parse_text(value)
    if tip in _BOOL_TYPES or _is_boolean_tinyint(meta):
        return _parse_boolean(value)
    if tip in _INT_TYPES:
        return _parse_int(value)
    if tip in _DECIMAL_TYPES:
        return _parse_decimal(value)
    if tip in _FLOAT_TYPES:
        return _parse_float(value)
    if tip == "date":
        return _parse_date(value)
    if tip in _DATETIME_TYPES:
        return _parse_datetime(value)
    if tip == "year":
        return _parse_int(value)
    if tip == "time":
        return _parse_time(value)
    if tip in _BINARY_TYPES:
        return value, None, False

    # A type we do not know: hand it over as it came and let MariaDB be the one
    # that refuses it, with its own error. Guessing here would be inventing.
    return value, None, False


def _is_boolean_tinyint(meta):
    """
    `tinyint(1)` is MySQL's own spelling of a boolean, and it is what a schema
    generated from an Access Yes/No column looks like. A PLAIN `tinyint` is NOT
    treated as a boolean: -1 is a perfectly good tinyint, and turning it into 1
    on a column that counts things would be corruption, not conversion.
    """
    if (meta.get("tip") or "").lower() != "tinyint":
        return False
    return (meta.get("tip_complet") or "").lower().replace(" ", "").startswith("tinyint(1)")


# --- text --------------------------------------------------------------------

def _parse_text(value):
    if isinstance(value, str):
        return value, None, False
    if isinstance(value, bool):
        return ("1" if value else "0"), "boolean → text", False
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat(sep=" ") if isinstance(value, datetime.datetime) \
            else value.isoformat(), "dată → text", False
    if isinstance(value, (bytes, bytearray)):
        return value, None, False
    return str(value), "%s → text" % type(value).__name__, False


# --- boolean -----------------------------------------------------------------

def _parse_boolean(value):
    """
    Access stores Yes/No as 0 and -1. MariaDB wants 0 and 1. -1 written straight
    into a `tinyint(1)` is stored as -1, which is truthy but is not what any
    later `= 1` will find.
    """
    if isinstance(value, bool):
        out = 1 if value else 0
        return out, "boolean → %d" % out, False

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        out = 0 if value == 0 else 1
        if out == value:
            return value, None, False
        return out, "%s → %d (Access folosește -1 pentru «da»)" % (value, out), False

    if isinstance(value, str):
        text = value.strip().lower()
        if text in _TRUE_WORDS:
            return 1, "«%s» → 1" % value.strip(), False
        if text in _FALSE_WORDS:
            return 0, "«%s» → 0" % value.strip(), False
        number = _number_from_text(text)
        if number is not None:
            out = 0 if number == 0 else 1
            return out, "«%s» → %d" % (value.strip(), out), False

    return value, None, False


# --- numbers -----------------------------------------------------------------

def _number_from_text(text):
    """
    The number inside a piece of text, as a Decimal, or None.

    ONE rule, because there is only one real case: «,» is a decimal point.
    Access does not write a thousands separator -- not even when the column
    carries a display format, because the format is how the value is SHOWN, not
    how it is stored (confirmed by the operator, 2026-08-21). So «1234,56» is
    what arrives, and it means 1234.56.

    Deliberately NOT handled: a string carrying BOTH «.» and «,», or spaces
    inside the digits. Those cannot come out of Access, so meeting one means the
    value is not what we think it is -- and there is no reading of «1.234,56»
    that is safe to guess. It comes back None, stays unchanged, and
    `validate.check_value` reports it as the blocking TIP finding it is. Better a
    stopped run than a number quietly off by a thousand.
    """
    text = str(text).strip()
    if not text:
        return None
    if "." in text and "," in text:
        return None
    if _INNER_SPACE.search(text):
        return None

    try:
        return decimal.Decimal(text.replace(",", "."))
    except (decimal.InvalidOperation, ValueError, TypeError):
        return None


def _parse_int(value):
    if isinstance(value, bool):
        return (1 if value else 0), "boolean → %d" % (1 if value else 0), False
    if isinstance(value, int):
        return value, None, False
    if isinstance(value, float):
        if float(value).is_integer():
            return int(value), "%s → %d" % (value, int(value)), False
        return value, None, False

    number = _number_from_text(value) if isinstance(value, str) else None
    if number is None:
        return value, None, False
    if number == number.to_integral_value():
        out = int(number)
        return out, "«%s» → %d" % (str(value).strip(), out), False
    # A fraction headed for an integer column: MariaDB would round it silently.
    # Hand it over unchanged so check_value reports it instead.
    return value, None, False


def _parse_decimal(value):
    if isinstance(value, bool):
        return (1 if value else 0), "boolean → %d" % (1 if value else 0), False
    if isinstance(value, decimal.Decimal):
        return value, None, False
    if isinstance(value, int):
        return value, None, False
    if isinstance(value, float):
        out = decimal.Decimal(repr(value))
        return out, "%s → %s" % (value, out), False

    number = _number_from_text(value) if isinstance(value, str) else None
    if number is None:
        return value, None, False
    return number, "«%s» → %s" % (str(value).strip(), number), False


def _parse_float(value):
    if isinstance(value, bool):
        return (1 if value else 0), "boolean → %d" % (1 if value else 0), False
    if isinstance(value, (int, float)):
        return value, None, False

    number = _number_from_text(value) if isinstance(value, str) else None
    if number is None:
        return value, None, False
    out = float(number)
    return out, "«%s» → %s" % (str(value).strip(), out), False


# --- dates and times ---------------------------------------------------------

def _split_datetime_text(text):
    """(date_part_match, time_text) for a piece of text, or (None, None)."""
    m = _DATE_PART.match(text)
    if m is None:
        return None, None
    return m, m.group("tail") or ""


def _read_ymd(a, b, c, separator):
    """
    (year, month, day, ambiguous) from the three numbers of a date, or None.

    ISO first: a four-digit first number is the year and there is nothing to
    guess. Otherwise the third number is the year, and the order of the other two
    is decided by the rules at the top of this module.
    """
    a, b, c = int(a), int(b), int(c)

    if a > 31:                                   # yyyy-mm-dd, unambiguous
        return _valid(a, b, c, False)

    # The third number is the YEAR. It is not tested for being "big enough to be
    # a year" -- «04/28/26» carries the year 26, and a test like `c > 31` throws
    # away every two-digit year there is, which is the format mdbtools actually
    # emits.
    if c < 100:
        year = 2000 + c if c < TWO_DIGIT_YEAR_PIVOT else 1900 + c
        month_first_default = SLASH_TWO_DIGIT_YEAR_IS_MONTH_FIRST
    else:
        year = c
        month_first_default = SLASH_FOUR_DIGIT_YEAR_IS_MONTH_FIRST
    if separator != "/":
        month_first_default = False              # «.» and «-» are European

    if a > 12 and b <= 12:
        return _valid(year, b, a, False)         # day first, proven
    if b > 12 and a <= 12:
        return _valid(year, a, b, False)         # month first, proven
    if a > 12 and b > 12:
        return None

    if month_first_default:
        return _valid(year, a, b, True)
    return _valid(year, b, a, True)


def _valid(year, month, day, ambiguous):
    try:
        datetime.date(year, month, day)
    except ValueError:
        return None
    return year, month, day, ambiguous


def _read_time(text):
    """(hour, minute, second, matched) from the tail of a datetime string."""
    if not text or not text.strip():
        return 0, 0, 0, True
    m = _TIME_PART.match(text)
    if m is None:
        return 0, 0, 0, False
    hour = int(m.group("h"))
    ampm = (m.group("ampm") or "").lower()
    if ampm == "pm" and hour < 12:
        hour += 12
    elif ampm == "am" and hour == 12:
        hour = 0
    if hour > 23:
        return 0, 0, 0, False
    return hour, int(m.group("m")), int(m.group("s") or 0), True


def _read_datetime(value):
    """A datetime out of anything readable, or None. Shared by date/datetime."""
    if isinstance(value, datetime.datetime):
        return value, False
    if isinstance(value, datetime.date):
        return datetime.datetime(value.year, value.month, value.day), False
    if not isinstance(value, str):
        return None

    text = value.strip()
    if not text:
        return None

    m, tail = _split_datetime_text(text)
    if m is None:
        return None
    ymd = _read_ymd(m.group("a"), m.group("b"), m.group("c"), m.group("sep"))
    if ymd is None:
        return None
    year, month, day, ambiguous = ymd
    hour, minute, second, ok = _read_time(tail)
    if not ok:
        return None
    return datetime.datetime(year, month, day, hour, minute, second), ambiguous


def _parse_datetime(value):
    if isinstance(value, datetime.datetime):
        return value, None, False
    got = _read_datetime(value)
    if got is None:
        return value, None, False
    out, ambiguous = got
    note = "«%s» → %s" % (str(value).strip(), out.strftime("%Y-%m-%d %H:%M:%S"))
    if ambiguous:
        note += " (zi/lună ambiguu — citit după regula din parser.py)"
    return out, note, ambiguous


def _parse_date(value):
    if isinstance(value, datetime.date) and not isinstance(value, datetime.datetime):
        return value, None, False
    got = _read_datetime(value)
    if got is None:
        return value, None, False
    out, ambiguous = got
    note = "«%s» → %s" % (str(value).strip(), out.date().isoformat())
    if ambiguous:
        note += " (zi/lună ambiguu — citit după regula din parser.py)"
    return out.date(), note, ambiguous


def _parse_time(value):
    if isinstance(value, datetime.time):
        return value.strftime("%H:%M:%S"), "oră → text", False
    if isinstance(value, datetime.datetime):
        return value.strftime("%H:%M:%S"), "dată → oră", False
    if not isinstance(value, str):
        return value, None, False

    m = _TIME_ONLY.match(value)
    if m is None:
        # «28/04/26 15:28:03» headed for a TIME column: keep the time of day.
        got = _read_datetime(value)
        if got is None:
            return value, None, False
        out = got[0].strftime("%H:%M:%S")
        return out, "«%s» → %s" % (value.strip(), out), False

    hour = int(m.group("h"))
    ampm = (m.group("ampm") or "").lower()
    if ampm == "pm" and hour < 12:
        hour += 12
    elif ampm == "am" and hour == 12:
        hour = 0
    out = "%02d:%02d:%02d" % (hour, int(m.group("m")), int(m.group("s") or 0))
    if out == value.strip():
        return value, None, False
    return out, "«%s» → %s" % (value.strip(), out), False
