# routes/inregistrare/anaf.py
"""
The ANAF lookup behind the first screen of the registration page (slice 0075-01,
plan 5.2).

A browser cannot call ANAF itself (no CORS headers on their side), so the server
does it. Stdlib `urllib` only: the virtual environment has no `requests` and this
one call is not worth a dependency (finding F10).

PORTED FROM THE ACCESS SIDE. The request is the one `InformatiiFirmaOnline2` has
been making for years (module `Module__EFactura`, read 22.09.2026): POST, header
`Content-Type: application/json`, body

    [{"cui": <digits, unquoted>, "data": "yyyy-mm-dd"}]

and a reply that begins `<html>` means ANAF itself broke, not that the code is
unknown. That check is kept letter for letter.

ONE THING DID NOT PORT, and it matters. The Access function decides "not found" by
reading `cod <> "200"` off the reply. **Version 9 has no `cod` field at all** --
verified against a real v9 answer on 22.09.2026, which carried exactly two top-level
keys, `found` and `notFound`. So the test here is `found` being empty. Anyone
comparing this module against the VBA should know the difference is deliberate.

THE ANSWER, as seen on 22.09.2026 (CUI 21015411), trimmed to what is used:

    {"found": [{"date_generale": {"cui": 21015411,
                                  "denumire": "AVATAR SOFT SRL",
                                  "adresa": "JUD. PRAHOVA, MUN. PLOIESTI, ...",
                                  "nrRegCom": "J2007000325294", ...},
                "adresa_sediu_social": {...}, ...}],
     "notFound": []}

Only `date_generale` is read. The structured `adresa_sediu_social` is left alone --
the flat `adresa` is what the applicant recognises, and the operator sees the same
string on the approval page.

ANAF returns CEDILLA diacritics (PLOIESTI with S-cedilla, Administratia with
T-cedilla), not the comma-below letters Romanian typography wants. Nothing here
rewrites them: what ANAF said is what gets stored and shown, and the applicant may
retype the name anyway.

UNVERIFIED: ANAF's rate limit on v9. One call per registration is far below any
published figure, but the number itself was never confirmed.
"""
import json
import logging
import urllib.error
import urllib.request
from datetime import date

logger = logging.getLogger(__name__)

# The v9 endpoint. Overridable from config.py because the path SHAPE moved between
# versions -- Access still points at `/PlatitorTvaRest/api/v6/ws/tva`, this is
# `/api/PlatitorTvaRest/v9/tva` -- so if ANAF moves it again it is one line on the
# VPS and no code change.
DEFAULT_URL = "https://webservicesp.anaf.ro/api/PlatitorTvaRest/v9/tva"
DEFAULT_TIMEOUT = 15

# A Romanian fiscal code is at most ten digits. Two is the shortest that exists.
MIN_CF_DIGITS = 2
MAX_CF_DIGITS = 10


class AnafError(RuntimeError):
    """Base for everything this module refuses on."""


class AnafUnavailable(AnafError):
    """ANAF could not be reached, or answered with something that is not an answer."""


class AnafNotFound(AnafError):
    """ANAF was reached and does not know this fiscal code."""


class AnafIncomplete(AnafError):
    """ANAF found the code but gave no name, so there is nothing to pre-fill."""


def normalize_cf(raw) -> str:
    """
    The digits of a fiscal code, and nothing else.

    Applicants type `RO 21015411`, `RO-21015411`, `21.015.411`. The prefix and every
    separator go; what is left must be digits. An empty answer means the caller has
    to refuse -- this function never guesses.
    """
    text = ("" if raw is None else str(raw)).strip().upper()
    if text.startswith("RO"):
        text = text[2:]
    return "".join(ch for ch in text if ch.isdigit())


def is_valid_cf(cf) -> bool:
    """True for a digits-only code of a plausible length. Shape only, not a checksum."""
    return bool(cf) and MIN_CF_DIGITS <= len(cf) <= MAX_CF_DIGITS


def lookup(cf, when=None, url=None, timeout=None) -> dict:
    """
    Asks ANAF about one fiscal code and answers the fields the page shows.

    `cf` is already normalized (digits only). Answers
    `{"cui", "denumire", "adresa", "nr_reg_com"}`.

    Raises AnafNotFound when ANAF does not know the code, AnafIncomplete when it
    knows it but names nothing, and AnafUnavailable for every transport failure, for
    an `<html>` page and for a body that is not the JSON object v9 promises. Nothing
    is swallowed: each one keeps the original error chained to it and is logged here
    before it leaves.
    """
    target = url or _configured("ANAF_TVA_URL", DEFAULT_URL)
    seconds = int(timeout or _configured("ANAF_TIMEOUT", DEFAULT_TIMEOUT))
    asked_on = (when or date.today()).strftime("%Y-%m-%d")

    # `cui` unquoted, exactly as the Access function builds it.
    body = json.dumps([{"cui": int(cf), "data": asked_on}]).encode("utf-8")
    request = urllib.request.Request(
        target,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "K-BOT"},
    )

    try:
        with urllib.request.urlopen(request, timeout=seconds) as response:
            text = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as err:
        logger.error("ANAF %s answered HTTP %s for CF %s", target, err.code, cf)
        raise AnafUnavailable(f"ANAF HTTP {err.code}") from err
    except Exception as err:            # URLError, socket timeout, DNS, TLS
        logger.error("ANAF %s unreachable for CF %s: %s", target, cf, err)
        raise AnafUnavailable(str(err)) from err

    # The Access check, kept: an error page, not a reply.
    if text.lstrip()[:6].lower() == "<html>":
        logger.error("ANAF returned an HTML error page for CF %s", cf)
        raise AnafUnavailable("ANAF a raspuns cu o pagina HTML")

    try:
        payload = json.loads(text)
    except ValueError as err:
        logger.error("ANAF answer for CF %s is not JSON: %s", cf, text[:200])
        raise AnafUnavailable("Raspuns ANAF ilizibil") from err

    if not isinstance(payload, dict):
        logger.error("ANAF answer for CF %s is not an object: %s", cf, text[:200])
        raise AnafUnavailable("Raspuns ANAF neasteptat")

    found = payload.get("found") or []
    if not found:
        raise AnafNotFound(cf)

    fields = _fields(found[0])
    if not fields["denumire"]:
        logger.error("ANAF found CF %s but gave no denumire", cf)
        raise AnafIncomplete(cf)
    return fields


def _fields(entry) -> dict:
    """The four values the page and the approval screen use, from `date_generale`."""
    general = entry.get("date_generale") if isinstance(entry, dict) else None
    general = general if isinstance(general, dict) else {}
    return {
        "cui": _text(general.get("cui")),
        "denumire": _text(general.get("denumire")),
        "adresa": _text(general.get("adresa")),
        "nr_reg_com": _text(general.get("nrRegCom")),
    }


def _text(value) -> str:
    """`cui` arrives as a number and the rest as strings; everything leaves as text."""
    return "" if value is None else str(value).strip()


def _configured(name, default):
    """config.py if it is importable, the default otherwise (dev machines have none)."""
    try:
        import config as _config
    except Exception:
        return default
    value = getattr(_config, name, None)
    return default if value in (None, "") else value
