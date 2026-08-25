# routes/forexe/prelucrare_helpers.py
"""
Pure helpers for the FOREXE ingest pipeline (slice 0048, plan docs/PLAN_ForexeIngest.md).

These are line-by-line ports of the Access VBA helpers in `mdl_FX_Helpers`. They
touch NO database and NO Flask request state, so every one of them is unit
testable offline -- that is the whole reason they live in their own module
instead of inside prelucrare.py.

HOW TO READ THIS FILE (the operator asked for this explicitly -- comments say
WHAT a line does, not only why; the reader knows SQL and VB.NET, not Python):

  * `def name(arg):`            declares a function. No `Function`/`End Function`;
                                the indentation IS the block, like VB's line
                                continuation but mandatory.
  * `-> str` / `: str`          type hints. Documentation only -- Python does not
                                enforce them at runtime the way `Option Strict On`
                                does in VB.NET.
  * `Optional[X]`               "X or None". `None` is VB's `Nothing` / `Null`.
  * a triple-quoted string  a docstring: the comment block attached to the
                                function, readable at runtime via help().
  * `s[-2]`                     indexing from the END of a string. `s[-2]` is the
                                second character from the right -- exactly VBA's
                                `Left(Right(S, 2), 1)`.
  * `s[a:b]`                    a "slice": characters from a up to (not incl.) b.
  * `raise ValueError(...)`     VBA's `Err.Raise`. Never swallowed here.

FIDELITY NOTE. Where the VBA has a defect, this module reproduces the VBA's
OBSERVABLE BEHAVIOUR and says so in a comment, because the data already in
MariaDB was produced by that behaviour. Deviations are called out one by one.
"""
import hashlib
from datetime import date, datetime, time, timedelta
from typing import Optional

# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------
# The Access side computed:  BytesToHex(Base64Bytes(BCrypt.HASH(key, bcSha256)))
# Base64Bytes(base64(x)) == x, so the whole expression is just hex(sha256(key)).
#
# VBA's `Hex$` yields UPPER case and BytesToHex pads each byte to two chars
# (Right$("0" & Hex$(b), 2)), so stored hashes are upper-case hex. VERIFIED by
# reading BytesToHex; see plan section 8.
#
# What is NOT verified: which byte encoding BCrypt.HASH applied to the VBA
# UTF-16 string before hashing. UTF-8 is the assumption here. It matters only
# for text carrying diacritics. This is EXACTLY why deduplication runs on the
# natural key and not on this string (decision D9) -- a wrong guess here can
# never duplicate a row, it can only make the stored HASH column disagree with
# an Access-era value.
_HASH_ENCODING = "utf-8"

# VBA `CStr(<Double>)` is LOCALE dependent: on the Romanian machines the Access
# clients ran on, CStr(1.5) is "1,5". Integral values carry no separator at all
# (CStr(510.0) is "510"), which covers most FOREXE amounts. Recorded as
# unverified in the worklog; only affects the HASH string we WRITE, never
# deduplication (D9).
_VBA_DECIMAL_SEPARATOR = ","


def bytes_to_hex(data: bytes) -> str:
    """Port of BytesToHex: upper-case hex, two characters per byte."""
    # .hex() gives lower case; .upper() matches VBA's Hex$.
    return data.hex().upper()


def _sha256_hex(hash_key: str) -> str:
    """hex(sha256(key)) in the Access byte order and letter case."""
    # .encode() turns the text into bytes -- hashing is defined over bytes, not
    # characters. digest() returns the raw 32 bytes.
    return bytes_to_hex(hashlib.sha256(hash_key.encode(_HASH_ENCODING)).digest())


def _vba_cstr(value) -> str:
    """
    Reproduce VBA `CStr(x)` for the value kinds that reach a hash key.

    Strings pass through. Floats that are whole numbers lose the decimal part
    entirely (CStr(510.0) -> "510"); fractional ones use the locale separator.
    """
    if value is None:
        # VBA Nz(...) collapses Null to "" before it reaches the hash.
        return ""
    if isinstance(value, bool):
        # Checked BEFORE int: in Python `bool` IS a subclass of `int`, so a bare
        # isinstance(value, int) would also catch True/False.
        return "True" if value else "False"
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return str(value).replace(".", _VBA_DECIMAL_SEPARATOR)
    return str(value)


def _hash_key_from_pairs(pairs) -> str:
    """
    Build the `name=len:value|` key shared by every Access hash helper.

    `pairs` is a sequence of (name, value) two-element tuples. A tuple is an
    immutable list written with parentheses; a list of them preserves insertion
    order, which is what the VBA relied on when it walked a Dictionary by index.

    The trailing "|" is stripped, matching `Left$(hashKey, Len(hashKey) - 1)`.
    """
    parts = []
    for name, value in pairs:
        text = _vba_cstr(value)
        # Len() in VBA counts UTF-16 characters; len() in Python counts code
        # points. They agree for everything outside the astral planes, which
        # FOREXE text never reaches.
        parts.append(f"{name}={len(text)}:{text}|")
    # "".join(list) concatenates -- the fast idiom; building with += in a loop
    # re-allocates the whole string every time.
    key = "".join(parts)
    return key[:-1] if key else key


def get_hash_from_dict(row: dict) -> str:
    """Port of GetHashFromDict -- every key of the dict, in insertion order."""
    # .items() yields (key, value) pairs. Python dicts keep insertion order.
    return _sha256_hex(_hash_key_from_pairs(row.items()))


def get_hash_for_row_istoric(row: dict) -> str:
    """
    Port of GetHashForRow_Istoric.

    Fixed four-field order, and -- unlike the dict-driven helpers -- the VBA
    builds this one WITHOUT a trailing "|" on the last field, so there is
    nothing to strip. Same resulting string either way; kept explicit so the
    two recipes can be compared side by side.
    """
    return _sha256_hex(_hash_key_from_pairs([
        ("Timp", row.get("Timp")),
        ("Utilizator", row.get("Utilizator")),
        ("Descriere", row.get("Descriere")),
        ("Observatii", row.get("Observatii")),
    ]))


def get_hash_for_row_receptie(row: dict) -> str:
    """Port of GetHashForRow_Receptie / GetHashForRow_ReceptieH (identical shape)."""
    return _sha256_hex(_hash_key_from_pairs(row.items()))


def fx_receptii_num_key(v: float) -> str:
    """
    Port of FX_Receptii_NumKey: Format$(Round(V, 4), "0.0000") with "," -> ".".

    Always four decimals, always a dot, so the value is locale-proof. Note the
    VBA rounds to 4 BEFORE formatting to 4 -- the round is redundant but kept.
    """
    # Python's round() uses banker's rounding on .5 ties, VBA's Round() does too
    # (both round-half-to-even), so this matches.
    return f"{round(v, 4):.4f}"


def fx_receptii_h_get_hash_ident(cod_angajament: str, data_h,
                                 tip_receptie: Optional[str],
                                 descriere_receptie: Optional[str]) -> str:
    """Port of FX_Receptii_H_GetHashIdent -- the reception-header identity."""
    return get_hash_for_row_receptie({
        "CodAngajament": cod_angajament,
        "DataH": _format_ymd(data_h),
        "TipReceptie": tip_receptie or "",
        "DescriereReceptie": descriere_receptie or "",
    })


def fx_receptii_istoric_get_indent(cod_ang: str, cod_ind: str, data,
                                   cod_ssi: str, valoare: float) -> str:
    """
    Port of FX_Receptii_Istoric_GetIndent.

    NOTE the VBA adds `Valoare` as a raw Double, NOT through FX_Receptii_NumKey
    -- so this one hash IS locale sensitive on fractional amounts. Faithful port;
    see _vba_cstr and the worklog.
    """
    return get_hash_for_row_receptie({
        "CodAng": cod_ang,
        "CodInd": cod_ind,
        "Data": _format_ymd(data),
        "CodSSI": cod_ssi,
        "Valoare": float(valoare),
    })


def _format_ymd(value) -> str:
    """Format$(d, "yyyy-mm-dd") for a date or datetime."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        value = value.date()
    return value.strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Text extraction from Observatii
# ---------------------------------------------------------------------------
# Every VBA InStr here passes vbTextCompare, i.e. CASE INSENSITIVE. The Python
# equivalent is to search in a lower-cased copy while slicing the original, so
# the returned text keeps its original casing.
def _find_ci(haystack: str, needle: str, start: int = 0) -> int:
    """Case-insensitive index search. Returns -1 when absent (VBA returned 0)."""
    return haystack.lower().find(needle.lower(), start)


def extract_text_between(txt: Optional[str], start_label: str,
                         end_label: str) -> str:
    """
    Port of ExtractTextBetween.

    Missing start label -> "" (the VBA falls out of the function, leaving the
    return value at its String default). Missing end label -> everything after
    the start label. Result is trimmed.
    """
    if not txt:
        return ""
    p1 = _find_ci(txt, start_label)
    if p1 < 0:
        return ""
    p1 += len(start_label)
    p2 = _find_ci(txt, end_label, p1)
    if p2 < 0:
        return txt[p1:].strip()
    return txt[p1:p2].strip()


def extract_text_after_label(txt: Optional[str], label: str) -> str:
    """Port of ExtractTextAfterLabel -- everything after the label, trimmed."""
    if not txt:
        return ""
    p1 = _find_ci(txt, label)
    if p1 < 0:
        return ""
    return txt[p1 + len(label):].strip()


def extract_obs_value(obs: Optional[str], start_key: str,
                      end_key: str = ",") -> str:
    """
    Port of ExtractObsValue -- the workhorse for payment Observatii.

    Finds start_key, trims what follows, then cuts at the FIRST end_key in that
    remainder. An empty end_key returns the whole remainder.
    """
    if not obs:
        return ""
    pos = _find_ci(obs, start_key)
    if pos < 0:
        return ""
    rest = obs[pos + len(start_key):].strip()
    if end_key == "":
        return rest.strip()
    end_pos = _find_ci(rest, end_key)
    # VBA: IIf(endPos > 0, Left$(rest, endPos - 1), rest) -- endPos is 1-based
    # there, so "> 0" means "found". Here -1 means "not found", and a hit at
    # index 0 (end_key first character) correctly yields "".
    return rest[:end_pos].strip() if end_pos >= 0 else rest.strip()


def extract_number_after_label(txt: Optional[str], label: str) -> float:
    """
    Port of ExtractNumberAfterLabel.

    FAITHFUL DEFECT: the VBA guards with `If P2 < P1 Then Exit Function`, and
    when there is no comma after the label P2 is 0, which is always < P1. So a
    label with NO trailing comma yields 0, and the `IIf(P2 = 0, ...)` branch
    below it is unreachable dead code. Reproduced exactly -- callers such as
    ExtractRezervareDefinitiva sum five of these and the totals in MariaDB were
    produced under this rule.
    """
    if not txt:
        return 0.0
    p1 = _find_ci(txt, label)
    if p1 < 0:
        return 0.0
    p1 += len(label)
    p2 = _find_ci(txt, ",", p1)
    if p2 < 0:
        # No comma after the label -> the VBA's `P2 < P1` guard fires.
        return 0.0
    raw = txt[p1:p2].strip()
    # Replace(..., "RON", "", vbTextCompare) -- case-insensitive removal.
    raw = _replace_ci(raw, "RON", "").strip()
    if raw == "" or raw.lower() == "n/a":
        return 0.0
    return parse_loose_number(raw)


def _replace_ci(text: str, old: str, new: str) -> str:
    """Case-insensitive str.replace (VBA Replace with vbTextCompare)."""
    if not old:
        return text
    out = []
    low_text, low_old = text.lower(), old.lower()
    i = 0
    while True:
        j = low_text.find(low_old, i)
        if j < 0:
            out.append(text[i:])
            return "".join(out)
        out.append(text[i:j])
        out.append(new)
        i = j + len(old)


def extract_rezervare_definitiva(obs: Optional[str]) -> float:
    """Port of ExtractRezervareDefinitiva -- the five horizon buckets, summed."""
    return (extract_number_after_label(obs, "an curent:")
            + extract_number_after_label(obs, "an+1:")
            + extract_number_after_label(obs, "an+2:")
            + extract_number_after_label(obs, "an+3:")
            + extract_number_after_label(obs, "alti ani:"))


def extract_numar_rev(s: Optional[str]) -> Optional[int]:
    """
    Port of ExtractNumarREV -- the revision number inside "(REV:nn)".

    Returns None (VBA Null) when absent, empty or non-numeric.
    """
    if not s:
        return None
    p1 = _find_ci(s, "(REV:")
    if p1 < 0:
        return None
    p1 += len("(REV:")
    p2 = s.find(")", p1)
    v = (s[p1:] if p2 < 0 else s[p1:p2]).strip()
    if v == "":
        return None
    try:
        # VBA IsNumeric + CLng. int() raises on anything non-integral, which is
        # the same rejection, just spelled with an exception.
        return int(v)
    except ValueError:
        return None


def fx_extract_cod_indicator(obs: Optional[str]) -> Optional[str]:
    """
    Port of FX_ExtractCodIndicator -- three labels tried in order.

    NOTE the third branch ("Plata: Rand:") is unreachable: the second branch
    searches for "Rand:", which also matches inside "Plata: Rand:". Kept for
    fidelity; it costs nothing and documents the original intent.
    """
    for label in ("Rand contract:", "Rand:", "Plata: Rand:"):
        s = extract_text_between(obs, label, ",")
        if len(s) > 0:
            return s
    return None


# ---------------------------------------------------------------------------
# Row classification
# ---------------------------------------------------------------------------
def get_tip_rand(obs: Optional[str]) -> str:
    """
    Port of GetTipRand.

    Order matters and is preserved: "suma receptie:" wins over "(activ:true)",
    so a row carrying both is a "Receptie", not a "Receptie_T".
    """
    s = (obs or "").strip().lower()
    if "suma receptie:" in s:
        return "Receptie"
    if "(activ:true)" in s:
        return "Receptie_T"
    if "plata:" in s:
        return "Plata"
    return ""


def is_rand_contract_row(obs: Optional[str]) -> bool:
    """Port of IsRandContractRow -- first 14 characters are 'rand contract:'."""
    return (obs or "").strip()[:14].lower() == "rand contract:"


def is_initiala_descriere(descr: Optional[str]) -> bool:
    """Port of IsInitialaDescriere -- exact match on either fixed phrase."""
    s = (descr or "").strip()
    return s in ("Initializare angajament", "Adaugare rand.")


def null_if_empty(s):
    """Port of NullIfEmpty -- blank or whitespace-only becomes None."""
    if s is None:
        return None
    return None if str(s).strip() == "" else s


# ---------------------------------------------------------------------------
# Numbers
# ---------------------------------------------------------------------------
def parse_loose_number(txt) -> float:
    """
    Port of ParseLooseNumber -- FOREXE amounts, which arrive in several shapes.

    The VBA comment is worth repeating verbatim: this ALL rests on FOREXE never
    emitting more than two decimals. It decides whether a "." or "," is a
    decimal point or a thousands separator by looking at WHERE it sits:

        Z = the 2nd character from the right
        if Z is a digit: Z = the 3rd character from the right

    so Z lands on the separator for both "510,00" (2 decimals) and "123.4"
    (1 decimal). If Z is still a digit there is no separator at all, and any
    "." or "," present must be a thousands separator.

    Worked examples, all from the real payload:
        "819.500,00" -> 819500.0     (dot = thousands, comma = decimal)
        "3.587"      -> 3587.0       (dot = thousands, NOT 3.587)
        "510,00"     -> 510.0
        "123.4"      -> 123.4        (one decimal -- the case the comment curses)
        "210"        -> 210.0
        "-5"         -> -5.0         (Z is "-": short negative, no separators)
    """
    if txt is None:
        return 0.0
    s = str(txt).strip()
    # Replace(S, "RON", "") in the VBA is case SENSITIVE here (no vbTextCompare
    # argument), unlike the one in ExtractNumberAfterLabel. Kept as-is.
    s = s.replace("RON", "").strip()
    if s == "" or s == "---":
        return 0.0

    # VBA Left(Right(S, 2), 1) is the second character from the right. On a
    # 1-character string Right(S,2) is the whole string, so guard the index.
    z = s[-2] if len(s) >= 2 else s[-1]
    if z.isdigit():
        # Maybe the decimals have two digits -- look one further left.
        z = s[-3] if len(s) >= 3 else s[0]

    if z.isdigit() and z != "-":
        # No decimal separator anywhere: a "." or "," can only be thousands.
        # The VBA removes ALL occurrences of whichever it finds first.
        if "," in s:
            s = s.replace(",", "")
        elif "." in s:
            s = s.replace(".", "")
        return _to_float(s)

    if z != "-":
        # Z IS the decimal separator; the other character is the thousands one.
        thousands = "." if z == "," else ","
        s = s.replace(thousands, "")
        # Normalise to a dot so Python's float() accepts it. The VBA instead
        # rewrote it to the machine's TVars.SepDeci -- same intent, no locale.
        s = s.replace(z, ".")
        return _to_float(s)

    # Z == "-": a short negative number, at most two digits, no decimals.
    return _to_float(s)


def _to_float(s: str) -> float:
    """CDbl with an explicit failure -- never a silent 0 (house rule)."""
    try:
        return float(s)
    except ValueError:
        raise ValueError(f"Valoare numerica invalida dupa normalizare: '{s}'")


def parse_amount(txt):
    """
    Port of ParseAmount.

    Returns None for None (VBA Null in, Null out) and 0 for "" / "---";
    everything else defers to parse_loose_number.
    """
    if txt is None:
        return None
    s = str(txt).replace("RON", "").strip()
    if s == "" or s == "---":
        return 0.0
    return parse_loose_number(s)


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------
_MONTHS_EN = ("jan", "feb", "mar", "apr", "may", "jun",
              "jul", "aug", "sep", "oct", "nov", "dec")


def parse_data_zzllaaaa(s: Optional[str]) -> Optional[date]:
    """
    ZZ.LL.AAAA -- zi.luna.an, i.e. day.month.year.

    UNVERIFIED SOURCE: the original ParseDataZZLLAAAA is USED in
    mdl_FX_Tasks_Receive_DWN and mdl_FX_Istoric but DEFINED in neither -- it
    lives in a module outside FX_System_Export. The contract below is taken
    from the two call sites, which both pass a dd/MM/yyyy string with the
    slashes rewritten to dots:

        ParseDataZZLLAAAA(Replace(sTitr(0), "/", "."))

    Recorded in the worklog under "left unverified".
    """
    if s is None:
        return None
    t = str(s).strip().replace("/", ".")
    if t == "":
        return None
    parts = t.split(".")
    if len(parts) != 3:
        raise ValueError(f"Data invalida (asteptat zz.ll.aaaa): '{s}'")
    try:
        zi, luna, an = int(parts[0]), int(parts[1]), int(parts[2])
        return date(an, luna, zi)
    except ValueError:
        raise ValueError(f"Data invalida (asteptat zz.ll.aaaa): '{s}'")


def parse_timp_istoric(timp: Optional[str]) -> Optional[datetime]:
    """
    The `Timp` column of TabelIstoric -> FX_Istoric.DataFX.

    Mirrors the VBA exactly:

        sTitr = Split(Timp, " ")
        DataFX = ParseDataZZLLAAAA(Replace(sTitr(0), "/", ".")) + TimeValue(sTitr(1))

    -- the date and the time are parsed SEPARATELY and added, and a value with
    no time part is a plain date. "10/02/2026 22:45:23" -> 2026-02-10 22:45:23.
    """
    if timp is None:
        return None
    t = str(timp).strip()
    if t == "":
        return None
    bits = t.split(" ")
    d = parse_data_zzllaaaa(bits[0])
    if d is None:
        return None
    if len(bits) < 2 or bits[1].strip() == "":
        # datetime.combine glues a date and a time together; time() is midnight.
        return datetime.combine(d, time())
    hms = bits[1].split(":")
    try:
        hh = int(hms[0])
        mm = int(hms[1]) if len(hms) > 1 else 0
        ss = int(hms[2]) if len(hms) > 2 else 0
    except ValueError:
        raise ValueError(f"Ora invalida in Timp: '{timp}'")
    # VBA adds a TimeValue to a Date, so an hour of 24+ would roll over. Python
    # rejects hour>23, so add it as a duration to keep the same tolerance.
    return datetime.combine(d, time()) + timedelta(hours=hh, minutes=mm, seconds=ss)


def parse_english_date(s: Optional[str]) -> Optional[datetime]:
    """
    Port of ParseEnglishDate -- the format inside PAYMENT Observatii.

        "Feb 16, 2026 12:00:00 AM" -> 2026-02-16 00:00:00

    Parsed with an EXPLICIT month table, never the machine locale (plan 5.6):
    the server's locale has nothing to do with what FOREXE emits.

    Returns None for blank input, for fewer than three space-separated parts,
    or for an unknown month name -- all three are VBA Null exits.
    """
    if s is None or str(s).strip() == "":
        return None
    parts = str(s).strip().split(" ")
    if len(parts) < 3:
        return None

    month_num = 0
    for i, name in enumerate(_MONTHS_EN):
        if name == parts[0].lower():
            month_num = i + 1
            break
    if month_num == 0:
        return None

    try:
        an = int(parts[2])
        zi = int(parts[1].replace(",", ""))
        result = datetime(an, month_num, zi)
    except ValueError:
        raise ValueError(f"Data engleza invalida: '{s}'")

    # Time is optional: the VBA only reads it when there are >= 5 parts
    # (date, day, year, time, AM/PM) AND the time splits into three pieces.
    if len(parts) >= 5:
        tp = parts[3].split(":")
        if len(tp) >= 3:
            try:
                hh, mm, ss = int(tp[0]), int(tp[1]), int(tp[2])
            except ValueError:
                raise ValueError(f"Ora invalida in data engleza: '{s}'")
            marker = parts[4].strip().upper()
            if marker == "PM" and hh != 12:
                hh += 12
            elif marker == "AM" and hh == 12:
                hh = 0
            result = result + timedelta(hours=hh, minutes=mm, seconds=ss)

    return result


# ---------------------------------------------------------------------------
# Classification codes
# ---------------------------------------------------------------------------
def fx_receptii_normalize_ssi(s: Optional[str]) -> str:
    """
    Port of FX_Receptii_NormalizeSSI -- strip spaces, tabs, dots and dashes.

    "02E- 65. 03. 01. 20. 03. 01" -> "02E650301200301".
    """
    t = (s or "").strip()
    if t == "":
        return ""
    for ch in (" ", "\t"):
        t = t.replace(ch, "")
    # The " ." / ". " / " -" / "- " passes in the VBA are already covered by
    # removing every space above; kept implicit rather than replayed verbatim
    # because the result is identical and the intermediate steps are unreachable.
    return t.replace("-", "").replace(".", "")


def split_sector_sursa_indicator(raw: Optional[str]):
    """
    Split "02E- 65. 03. 01. 20. 03. 01" into (SS, ClsfSal, ClsfE).

    This is the port of the three lines at the top of Prelucrare_Indicatori:

        clsfRaw = Replace(Replace(Split(raw, "-")(1), " ", ""), ".", "")
        SS      = Split(raw, "-")(0)
        ...and Obtine_IdUnitate_Din receives Replace(raw, " ", "") and uses
           Replace(Right(ClsfE, 8), ".", "") as its lookup key.

    IMPORTANT -- there is NO zero-padding. The plan (5.2) inferred `Format(x,
    "00")` from the sibling Angajament_Incarcat_Prelucrare_Initiala, but
    Prelucrare_Indicatori does no such thing: it only strips spaces and dots.
    Decision recorded in the worklog; the real function wins.

    Returns a 3-tuple:
        SS       "02E"            -- Clasificatii.SS
        ClsfSal  "650301200301"   -- Clasificatii.ClsfSal   (finds IdClsfAcc)
        ClsfE    "200301"         -- Clasificatii.ClsfE     (finds IdUnitate, D17)

    ClsfE is the LAST SIX digits, matching both the VBA's Right(...,8)-minus-dots
    and the MariaDB generated column
        concat(replace(Articol,'.',''), Alineat).
    """
    if raw is None or str(raw).strip() == "":
        raise ValueError("Sector_Sursa_Indicator lipseste sau este gol")
    text = str(raw)
    if "-" not in text:
        raise ValueError(f"Sector_Sursa_Indicator fara '-': '{raw}'")
    # split("-", 1) splits on the FIRST dash only and yields exactly two pieces,
    # which is what Split(x, "-")(0) / (1) read in the VBA.
    head, tail = text.split("-", 1)
    ss = head.strip()
    clsf_sal = tail.replace(" ", "").replace(".", "")
    # Right(ClsfE, 8) over the space-stripped WHOLE string, then dots removed.
    no_spaces = text.replace(" ", "")
    clsf_e = no_spaces[-8:].replace(".", "")
    return ss, clsf_sal, clsf_e


def cod_ai(cod_angajament: str, cod_indicator: str) -> str:
    """CodAI = CodAngajament & "-" & CodIndicator (the FX_Indicatori key)."""
    return f"{cod_angajament}-{cod_indicator}"
