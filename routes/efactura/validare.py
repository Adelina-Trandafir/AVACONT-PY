# routes/efactura/validare.py
"""
Validation of an issued invoice (slice 00EF-06). Two independent steps:

1. `verifica(...)`  -- OUR OWN checks, pure and offline: what must be filled in before the XML makes sense and
   what ANAF is known to refuse. Answers a list of findings; the invoice may be sent only when none of them
   has level `eroare`.
2. `anaf_valideaza(xml)` -- the public validation service of ANAF.

WHAT `ValideazaXML_Local` OF ACCESS REALLY DID (read in `Surse/RawExport/Modules/mdl_EFactura.bas.txt`, 06.10.2026):
the name says «local», the code is not. It POSTs the XML (without `xsi:schemaLocation`) to
`https://webservicesp.anaf.ro/prod/FCTEL/rest/validare/FACT1` with no token, and accepts the invoice when the
answer contains `"stare":"ok"`; otherwise it shows the whole answer. Step 2 does the same call. The plan's
«local validation» is therefore step 1 (new, written here) plus that service.

The invoice data go to ANAF in step 2 exactly as they will when the invoice is uploaded; no token and no
secret are involved. Nothing is logged except the HTTP status.

LEVELS: `eroare` blocks sending; `avertisment` is shown but does not block (a difference ANAF does not check,
or something this server cannot judge). Codes are stable ASCII; messages are Romanian for the operator.
"""
import json
import logging
import re
import urllib.error
import urllib.request
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

logger = logging.getLogger(__name__)

ANAF_VALIDATE_URL = "https://webservicesp.anaf.ro/prod/FCTEL/rest/validare/FACT1"
TIMEOUT_SECONDS = 30
MAX_ANSWER = 64 * 1024
MAX_MESSAGES = 20
MAX_MESSAGE_LENGTH = 500

ERROR = "eroare"
WARNING = "avertisment"

# ISO 3166-2:RO: the 41 counties and Bucharest. CIUS-RO writes them after «RO-» in CountrySubentity.
COUNTIES = frozenset(
    "AB AR AG BC BH BN BT BV BR BZ CS CL CJ CT CV DB DJ GL GR GJ HR HD IL IS IF MM MH MS NT OT PH SM SJ SB SV TR TM TL VS VL VN B".split()
)
_IBAN_RO = re.compile(r"^RO\d{2}[A-Z]{4}[A-Z0-9]{16}$")
_SECTOR = re.compile(r"^SECTOR[1-6]$")
_TWO = Decimal("0.01")


def _finding(level, code, message):
    return {"nivel": level, "cod": code, "mesaj": message}


def _blank(value):
    return value is None or not str(value).strip()


def verifica(furnizor, client, invoice, lines, known_units, bank_name):
    """The findings for one invoice; an empty list means nothing was found.

    furnizor   Unitati_Date row or None (the unit has not filled in its issuer data yet)
    client     EF_Clienti row         invoice  EF_Facturi row         lines  EF_FacturiLinii rows
    known_units  set of the codes in AVACONT_COMUN.EF_UM, or None when that table could not be read
    bank_name  `BIC.Banca` of the IBAN's bank code, or "" when it is not in the list
    """
    found = []

    def err(code, message):
        found.append(_finding(ERROR, code, message))

    def warn(code, message):
        found.append(_finding(WARNING, code, message))

    # ----- issuer
    if furnizor is None:
        err("FURNIZOR_LIPSA", "Datele unității care emite factura nu sunt completate (denumire, cod fiscal, adresă).")
    else:
        for column, label in (("Denumire", "denumirea"), ("CodFiscal", "codul fiscal"), ("Adresa", "adresa"),
                              ("Orasul", "localitatea"), ("Judetul", "județul")):
            if _blank(furnizor.get(column)):
                err("FURNIZOR_" + column.upper(), f"Unitatea emitentă: {label} nu este completat(ă).")
        _check_county(furnizor.get("Judetul"), furnizor.get("Orasul"), "Unitatea emitentă", "FURNIZOR", err, warn)

    # ----- customer
    if client is None:
        err("CLIENT_LIPSA", "Factura nu are cumpărător.")
    else:
        for column, label in (("DenumireClient", "denumirea"), ("CodFiscal", "codul fiscal"), ("Adresa", "adresa"),
                              ("Orasul", "localitatea"), ("Judetul", "județul")):
            if _blank(client.get(column)):
                err("CLIENT_" + column.upper(), f"Cumpărătorul: {label} nu este completat(ă).")
        _check_county(client.get("Judetul"), client.get("Orasul"), "Cumpărătorul", "CLIENT", err, warn)

    # ----- header
    if invoice.get("DataFactura") is None:
        err("DATA_LIPSA", "Factura nu are dată.")
    elif invoice["DataFactura"] > date.today():
        warn("DATA_VIITOR", "Data facturii este în viitor.")
    if int(invoice.get("NumarFactura") or 0) <= 0:
        err("NUMAR_INVALID", "Numărul facturii trebuie să fie pozitiv.")
    if str(invoice.get("TipFactura") or "") not in ("380", "384"):
        err("TIP_INVALID", "Tipul facturii trebuie să fie 380 (factură) sau 384 (factură corectată).")
    iban = str(invoice.get("ContPlata") or "").replace(" ", "").upper()
    if not iban:
        err("IBAN_LIPSA", "Factura nu are contul în care se face plata (IBAN).")
    elif not _IBAN_RO.match(iban):
        warn("IBAN_FORMAT", "Contul de plată nu are forma unui IBAN românesc (RO + 2 cifre + 4 litere + 16 caractere).")
    elif not bank_name:
        warn("BANCA_NECUNOSCUTA", "Codul băncii din IBAN nu este în lista de bănci; numele băncii va rămâne gol în XML.")

    # ----- lines
    if not lines:
        err("LINII_LIPSA", "Factura nu are nicio linie.")
    seen = set()
    for index, line in enumerate(lines, start=1):
        where = f"Linia {index}"
        nr = str(line.get("NrCrt") or "").strip()
        if not nr:
            err("LINIE_NR", f"{where}: numărul liniei lipsește.")
        elif nr in seen:
            err("LINIE_NR_DUBLU", f"{where}: numărul liniei «{nr}» se repetă.")
        seen.add(nr)
        if _blank(line.get("Continut")):
            err("LINIE_CONTINUT", f"{where}: denumirea lipsește.")
        unit = str(line.get("Um") or "").strip()
        if not unit:
            err("LINIE_UM", f"{where}: unitatea de măsură lipsește.")
        elif known_units is not None and unit not in known_units:
            err("LINIE_UM_NECUNOSCUTA", f"{where}: unitatea de măsură «{unit}» nu este în lista UN/ECE a sistemului.")
        cant, pu, valoare = Decimal(line["Cant"]), Decimal(line["PU"]), Decimal(line["Valoare"])
        if cant == 0:
            err("LINIE_CANTITATE", f"{where}: cantitatea nu poate fi zero.")
        expected = (cant * pu).quantize(_TWO, ROUND_HALF_UP)
        if abs(expected - valoare) > _TWO:
            warn("LINIE_VALOARE", f"{where}: valoarea ({valoare}) nu este cantitatea × prețul ({expected}).")
    if known_units is None and lines:
        warn("UM_NECITITA", "Lista unităților de măsură nu a putut fi citită; unitățile nu au fost verificate.")
    return found


def _check_county(county, city, who, prefix, err, warn):
    """The county is the code written after «RO-»; Bucharest asks for the sector as the city."""
    code = str(county or "").strip().upper()
    if not code:
        return                                              # already reported as missing
    if code not in COUNTIES:
        err(prefix + "_JUDET_INVALID",
            f"{who}: județul «{county}» nu este un cod de județ valid (de exemplu PH, CJ, B pentru București).")
    elif code == "B" and not _SECTOR.match(str(city or "").strip().upper().replace(" ", "")):
        warn(prefix + "_SECTOR",
             f"{who}: pentru București localitatea ar trebui să fie SECTOR1 … SECTOR6; ANAF poate refuza altfel.")


def has_errors(findings):
    return any(item["nivel"] == ERROR for item in findings)


# ---------------------------------------------------------------------------------------------
# the service of ANAF
# ---------------------------------------------------------------------------------------------
class ValidareIndisponibila(RuntimeError):
    """ANAF's validation service could not be reached or answered with a server error."""


def anaf_valideaza(xml_bytes):
    """Sends the XML to ANAF's validation service. Answers `{"ok": bool, "mesaje": [text, ...]}`.

    Raises ValidareIndisponibila when the service cannot be reached (network, timeout, 5xx): that says
    nothing about the invoice. Nothing but the HTTP status reaches the log.
    """
    request = urllib.request.Request(
        ANAF_VALIDATE_URL, data=xml_bytes, method="POST",
        headers={"Content-Type": "text/plain; charset=UTF-8", "Accept": "*/*", "User-Agent": "K-BOT"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            status, body = response.status, response.read(MAX_ANSWER)
    except urllib.error.HTTPError as err:
        status, body = err.code, err.read(MAX_ANSWER)
    except (urllib.error.URLError, TimeoutError, OSError) as err:
        logger.error("[efactura] validare ANAF: %s", type(err).__name__)
        raise ValidareIndisponibila("Serviciul de validare ANAF nu a putut fi contactat.") from err

    if status >= 500 or status in (408, 429):
        logger.error("[efactura] validare ANAF: HTTP %s", status)
        raise ValidareIndisponibila(f"Serviciul de validare ANAF nu răspunde (HTTP {status}).")
    return interpret_answer(body)


def interpret_answer(body):
    """ANAF's answer -> `{"ok", "mesaje"}`. `"stare":"ok"` is the only success, as in Access.

    The service answers JSON like `{"stare":"ok", ...}` or `{"stare":"nok", "Messages":[{"message":"..."}]}`;
    when the body is not JSON the (cut) text is the message, as Access showed `responseText`.
    """
    text = body.decode("utf-8", errors="replace") if isinstance(body, (bytes, bytearray)) else str(body)
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if isinstance(data, dict):
        ok = str(data.get("stare", "")).strip().lower() == "ok"
        messages = []
        for item in data.get("Messages") or data.get("messages") or []:
            piece = item.get("message") if isinstance(item, dict) else item
            if isinstance(piece, str) and piece.strip():
                messages.append(piece.strip()[:MAX_MESSAGE_LENGTH])
        if not ok and not messages:
            messages.append(text.strip()[:MAX_MESSAGE_LENGTH])
        return {"ok": ok, "mesaje": messages[:MAX_MESSAGES]}
    ok = '"stare":"ok"' in text.replace(" ", "")
    return {"ok": ok, "mesaje": [] if ok else [text.strip()[:MAX_MESSAGE_LENGTH]]}
