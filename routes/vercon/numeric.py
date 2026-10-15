"""VERCON numeric contract (PLAN rule 2, CONTRACT_CALCULE section 1). Pure functions, no database.

ZEC   = displayed/calculated decimals of a gestiune (NR.ZEC, fallback 4).
SCARA = fixed storage precision 4, independent of ZEC.
Values are unscaled DECIMAL(20,4); display and calculation retain their own rounding rules.
"""
from decimal import Decimal, ROUND_HALF_UP, ROUND_HALF_EVEN

DECIMAL_LIMIT = Decimal('10000000000000000')
DEFAULT_ZEC = 4
MAX_SCALE = 4
VAT_SCALE = 4
# Access stores Double; anything closer than this to a short decimal is float noise, not data.
FLOAT_TOLERANCE = Decimal("0.000000001")
HEADER_TOLERANCE = Decimal("1")


class NumericError(ValueError):
    def __init__(self, reason, message):
        super().__init__(message)
        self.reason = reason


def exact_decimal(value):
    """Decimal of a source value. Floats go through repr() (shortest round-trip text)."""
    if value is None:
        raise NumericError("NULL", "Valoare lipsa.")
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        raise NumericError("TYPE", "Valoare invalida.")
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise NumericError("TYPE", "Valoare invalida.")
        return Decimal(repr(value))
    return Decimal(str(value).strip())


def needed_scale(value, max_scale=MAX_SCALE):
    """Fewest decimals (0..max_scale) that represent value exactly (within float noise), else None."""
    d = exact_decimal(value)
    for scale in range(0, max_scale + 1):
        if abs(d - d.quantize(Decimal(1).scaleb(-scale), rounding=ROUND_HALF_UP)) < FLOAT_TOLERANCE:
            return scale
    return None


def storage_scale(values, zec=DEFAULT_ZEC, max_scale=MAX_SCALE):
    """Validate source precision; storage is always four decimal places."""
    zec = check_zec(zec)
    scale = zec
    for v in values:
        if v is None:
            continue
        n = needed_scale(v, max_scale)
        if n is None:
            raise NumericError("PRECISION", "Valoare cu prea multe zecimale pentru scara maxima.")
        scale = max(scale, n)
    return MAX_SCALE


def check_zec(zec):
    if zec is None:
        return DEFAULT_ZEC
    if not isinstance(zec, int) or isinstance(zec, bool) or not 0 <= zec <= MAX_SCALE:
        raise NumericError("ZEC", "Numarul de zecimale trebuie sa fie intre 0 si %d." % MAX_SCALE)
    return zec


def to_stored(value, scale=MAX_SCALE):
    """Direct DECIMAL(20,4). Only floating point noise is normalised; no scaling."""
    d = exact_decimal(value)
    if not d.is_finite():
        raise NumericError("TYPE", "Valoare numerică invalidă.")
    if abs(d) >= DECIMAL_LIMIT:
        raise NumericError("OVERFLOW", "Valoarea depășește capacitatea DECIMAL(20,4).")
    rounded = d.quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)
    if abs(d - rounded) >= FLOAT_TOLERANCE:
        raise NumericError("PRECISION", "Valoarea are mai mult de 4 zecimale.")
    if abs(rounded) >= DECIMAL_LIMIT:
        raise NumericError("OVERFLOW", "Valoarea depășește capacitatea DECIMAL(20,4).")
    return rounded


def from_stored(stored, scale=MAX_SCALE):
    return exact_decimal(stored)


def display(stored, scale, zec):
    """Value shown on screen: exact stored value rounded half up to zec decimals."""
    return from_stored(stored, scale).quantize(Decimal(1).scaleb(-zec), rounding=ROUND_HALF_UP)


def vat_to_stored(rate):
    """Direct fractional rate: e.g. 0.19 for 19 percent."""
    return to_stored(rate, VAT_SCALE)


def calc_round(value, zec):
    """Rounding of a NEW document calculation (proposal: half even, to verify at VC08-05)."""
    return exact_decimal(value).quantize(Decimal(1).scaleb(-zec), rounding=ROUND_HALF_EVEN)


def header_total(header, lines_sum):
    """N01/N02, the only calculation at migration: header 0/NULL/|H-S|>1 leu -> sum of lines.
    Returns (total, replaced)."""
    s = exact_decimal(lines_sum)
    if header is None:
        return s, True
    h = exact_decimal(header)
    if h == 0 or abs(h - s) > HEADER_TOLERANCE:
        return s, True
    return h, False
