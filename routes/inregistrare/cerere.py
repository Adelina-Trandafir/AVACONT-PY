# routes/inregistrare/cerere.py
"""
Checking and recording a finished registration (slice 0075-02, plan 5.5).

The page has walked the applicant through six screens. Nothing it sends is trusted:
every value is checked here again, against the same dictionaries the page was fed
from, and only then written to `AVACONT_COMUN.FX_Inregistrari` as one row waiting for
the operator.

WHY THE RE-CHECK IS NOT PARANOIA. These routes are public, and the browser is not
the only thing that can post to them. But the more ordinary case is slower and
likelier: the wizard has a thirty-minute window, and a dictionary can change inside
it. A code the page offered at minute two can be gone by minute twenty. Re-reading
the dictionaries at submission is what turns that into a clear refusal now instead of
a foreign-key error during provisioning, after the database has already been built.

WHAT COUNTS AS A SELECTABLE CODE (plan 4.1). The six-digit codes form three levels:

    xx0000   a root        -- never selectable on its own
    xxyy00   a level-1 node -- selectable ONLY when nothing hangs under it
    xxyyzz   a leaf         -- always selectable

The middle case is the one worth stating: real data has `650500` and `590100`, which
are level-1 codes that nobody subdivided, and they are legitimate rows. So "is this a
leaf" cannot be answered from the code alone -- it needs the whole dictionary, which
is why the check happens here and not in the page.

THE CEILING ON ROW COUNT IS NOT IN THE PLAN. One request becomes one
`Clasificatii` row per F leaf x E leaf x sector-source, and the plan's own example is
20 x 40 x 2 = 1600. The arithmetic does not stop there: every leaf of both trees and
all fourteen sources would be 531 x 686 x 14, five million rows, queued by an
anonymous form. `MAX_ROWS` below is a guard against that, added deliberately rather
than found in the plan -- it is one constant, and the operator can move it.
"""
import json
import logging
from datetime import date

from utils.database import COMMON_DB, get_kbot_connection

from . import nomenclatoare, nume

logger = logging.getLogger(__name__)

# `Unitati.NumeUnitate` and `CAI.NumeUnitate` are varchar(255), and sql_mode has
# STRICT_TRANS_TABLES -- a longer value is error 1406, not a silent truncation.
DENUMIRE_MAX_LENGTH = 255

# See the note at the top of the file. Not from the plan.
MAX_ROWS = 50_000

# The states one request moves through. ASCII identifiers, not sentences: the operator
# page translates them for the screen.
STARE_IN_ASTEPTARE = "InAsteptare"
STARE_APROBATA = "Aprobata"
STARE_RESPINSA = "Respinsa"
STARE_ESUATA = "Esuata"


class CerereInvalid(ValueError):
    """A value the page sent did not survive the re-check. Carries the Romanian
    sentence the applicant reads and the ASCII reason code the page branches on."""

    def __init__(self, reason, message):
        super().__init__(message)
        self.reason = reason
        self.message = message


def allowed_years(today=None):
    """
    The years a request may be filed for (D6): this one and the one before it.

    Never the future -- a unit cannot budget for a year the dictionaries do not cover
    yet, and `Unitati_Ani` would carry a row nothing can be entered against.
    """
    current = (today or date.today()).year
    return (current - 1, current)


def validate(conn, registration, body):
    """
    Checks one submitted request and answers the values to store.

    `registration` is the note behind the token; `body` is what the page posted.
    Raises CerereInvalid on the first thing that does not hold.
    """
    if not registration.get("verified"):
        raise CerereInvalid(
            "EMAIL_NEVERIFICAT",
            "Adresa de e-mail nu a fost confirmată. Reluați pasul cu codul.",
        )

    denumire = nume.normalize_name(body.get("denumire"))
    if not denumire:
        raise CerereInvalid("DENUMIRE_ABSENTA", "Introduceți denumirea unității.")
    if nume.has_forbidden_characters(denumire):
        raise CerereInvalid("DENUMIRE_CARACTERE_INTERZISE", nume.NAME_CHARACTERS_MESSAGE)
    if len(denumire) > DENUMIRE_MAX_LENGTH:
        raise CerereInvalid(
            "DENUMIRE_PREA_LUNGA",
            f"Denumirea unității nu poate depăși {DENUMIRE_MAX_LENGTH} de caractere.",
        )

    an = _year(body.get("an"))

    ss = _distinct(body.get("sursasector"))
    if not ss:
        raise CerereInvalid("SS_ABSENT", "Alegeți cel puțin o sursă-sector.")
    known_ss = nomenclatoare.read_sursasector_codes(conn)
    _all_known(ss, known_ss, "SS_NECUNOSCUT",
               "Una dintre sursele-sector alese nu mai există. Reîmprospătați pagina.")

    f_codes = _leaves(conn, body.get("clsf_f"), "F",
                      "Alegeți cel puțin o poziție din clasificația funcțională.")
    e_codes = _leaves(conn, body.get("clsf_e"), "E",
                      "Alegeți cel puțin o poziție din clasificația economică.")

    rows = len(f_codes) * len(e_codes) * len(ss)
    if rows > MAX_ROWS:
        raise CerereInvalid(
            "PREA_MULTE_RANDURI",
            f"Selecția ar produce {rows} de clasificații, peste limita de {MAX_ROWS}. "
            "Restrângeți selecția.",
        )

    return {
        "denumire": denumire,
        "an": an,
        "ss": ss,
        "clsf_f": f_codes,
        "clsf_e": e_codes,
        "randuri": rows,
    }


def insert(checked, registration, ip_address):
    """
    Writes the request as one row and answers its `IdCerere`.

    Its own connection and its own transaction: this is the only write of the whole
    pass, and it either lands whole or not at all.
    """
    payload = json.dumps(
        {"ss": checked["ss"], "f": checked["clsf_f"], "e": checked["clsf_e"]},
        ensure_ascii=False,
    )
    anaf = registration.get("anaf") or {}

    conn = get_kbot_connection(COMMON_DB)
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO FX_Inregistrari "
            "(Email, CF, DenumireAnaf, Denumire, An, Payload, Stare, IpAddress) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (
                registration.get("email"),
                registration.get("cf"),
                (anaf.get("denumire") or "")[:DENUMIRE_MAX_LENGTH],
                checked["denumire"],
                checked["an"],
                payload,
                STARE_IN_ASTEPTARE,
                ip_address,
            ),
        )
        id_cerere = cur.lastrowid
        conn.commit()
        return id_cerere
    except Exception:
        conn.rollback()
        raise
    finally:
        if conn.is_connected():
            conn.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _year(value):
    try:
        an = int(value)
    except (TypeError, ValueError):
        raise CerereInvalid("AN_INVALID", "Alegeți anul.") from None
    low, high = allowed_years()
    if not low <= an <= high:
        raise CerereInvalid(
            "AN_IN_AFARA_INTERVALULUI",
            f"Anul trebuie să fie {low} sau {high}.",
        )
    return an


def _distinct(values):
    """The list as sent, de-duplicated, order kept, blanks dropped."""
    if not isinstance(values, list):
        return []
    seen = []
    for value in values:
        text = ("" if value is None else str(value)).strip()
        if text and text not in seen:
            seen.append(text)
    return seen


def _all_known(chosen, known, reason, message):
    missing = [code for code in chosen if code not in known]
    if missing:
        logger.warning("registration sent unknown codes: %s", ", ".join(missing[:10]))
        raise CerereInvalid(reason, message)


def _leaves(conn, values, tip, empty_message):
    """
    The chosen codes of one tree, proven to exist and to be selectable.

    Both questions are answered from the SAME list the page was given, read now --
    see the note at the top of the file about the thirty-minute window.
    """
    chosen = _distinct(values)
    if not chosen:
        raise CerereInvalid(f"CLSF_{tip}_ABSENT", empty_message)

    known = nomenclatoare.read_codes(conn, tip)
    _all_known(
        chosen, known, f"CLSF_{tip}_NECUNOSCUT",
        "Una dintre pozițiile alese nu mai există în nomenclator. "
        "Reîmprospătați pagina.",
    )

    # A stem that something hangs under. Built once, from the whole dictionary.
    stems_with_children = {code[:4] for code in known if not code.endswith("00")}
    for code in chosen:
        if not _is_selectable(code, stems_with_children):
            raise CerereInvalid(
                f"CLSF_{tip}_NU_E_FRUNZA",
                "Una dintre pozițiile alese are poziții sub ea și nu poate fi aleasă "
                "direct. Alegeți pozițiile de pe ultimul nivel.",
            )
    return chosen


def _is_selectable(code, stems_with_children):
    if code.endswith("0000"):          # a root, never on its own
        return False
    if not code.endswith("00"):        # a leaf, always
        return True
    return code[:4] not in stems_with_children   # level 1, only if childless
