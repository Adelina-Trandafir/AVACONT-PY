# routes/efactura/anaf_api.py
"""
The calls this server makes to ANAF's e-Factura service with the unit's access token (slice 00EF-07). Stdlib `urllib`
only (same choice as oauth.py). One function per call; none of them touches a database.

PORTED FROM `Surse/RawExport/Modules/mdl_EFactura.bas.txt` (read 06.10.2026) -- PRODUCTION addresses, as the operator
decided: the Access flow "is always right", so no test environment is used.

  upload     POST {BASE}/upload?standard=UBL&cif=<CUI>      body = the XML, Content-Type application/xml     (IncarcaFacturaXML)
             answer: XML root with `index_incarcare`, or a child element whose first attribute is the error text
  stare      GET  {BASE}/stareMesaj?id_incarcare=<id>        (StatusFactura)
             answer: XML root with `stare` = «ok» + `id_descarcare`, or «in prelucrare», or another state, or a child
             element whose first attribute is the error text
  descarca   GET  {BASE}/descarcare?id=<id>                  (DescarcaFactura / DescarcaMesaje) -> a zip; a body that
             starts with `{` is an error in JSON, as Access treated it
  mesaje     GET  {BASE}/listaMesajeFactura?zile=<n>&cif=<CUI>  (ListaMesaje) -> JSON `{"mesaje": [...]}` or `{"eroare": "..."}`
  pdf        POST {PDF_URL}/<FACT1|FCN>/DA   body = the invoice XML, NO token (slice 00EF-09): ANAF's public XML -> PDF service
             answer: the PDF, or a JSON / text error. NOT verified against the live service (written from ANAF's published
             description); `pdf_din_xml` checks the `%PDF` signature and says plainly when the answer is anything else.

The token goes only into the `Authorization` header and is never logged, raised or returned. A failure logs the call
name and the HTTP status; the text of ANAF's answer reaches the operator only after it was cut and cleaned (it is
ANAF's own error text, not a secret).

Failures become `EfEroare` (Romanian message, stable `reason`, HTTP status for our own answer):
  ANAF_INDISPONIBIL 502  network, timeout, 5xx, 429       -> try again later, nothing is known about the invoice
  TOKEN_NECESAR     409  401 / 403: ANAF no longer accepts the token -> the certificate step must be done again
  ANAF_EROARE       502  any other HTTP status
  ANAF_RASPUNS      502  an answer this code cannot read
"""
import io
import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass

from .tokens import EfEroare, TokenNecesar

logger = logging.getLogger(__name__)

BASE = "https://api.anaf.ro/prod/FCTEL/rest"
PDF_URL = "https://webservicesp.anaf.ro/prod/FCTEL/rest/transformare"
TIMEOUT_UPLOAD = 60
TIMEOUT_READ = 30
MAX_ANSWER = 25 * 1024 * 1024          # a downloaded zip; the other answers are tiny
MAX_ENTRY = 2 * 1024 * 1024            # one XML file inside an error zip
MAX_TEXT = 300
MAX_MESSAGES = 20
MESSAGE_KEYS = ("id", "data_creare", "id_incarcare", "id_solicitare", "cif_emitent", "cif_beneficiar", "tip", "detalii")
_DIGITS = re.compile(r"^\d{1,32}$")


class AnafIndisponibil(EfEroare):
    def __init__(self, message):
        super().__init__(message, "ANAF_INDISPONIBIL", 502)


@dataclass(frozen=True)
class UploadResult:
    index: str            # `index_incarcare`, "" when ANAF refused the file
    error: str            # ANAF's text, "" when the file was taken


@dataclass(frozen=True)
class StateResult:
    state: str            # «ok», «in prelucrare», or whatever ANAF sent ("" when there is an error text)
    download_id: str      # `id_descarcare`, "" when ANAF sent none
    error: str            # ANAF's text of an `Errors` answer, "" otherwise


def clean(text, limit=MAX_TEXT):
    """ANAF's text for the operator: control characters out, cut."""
    return "".join(ch for ch in str(text) if ch.isprintable()).strip()[:limit]


def is_id(value):
    """An ANAF identifier is a short run of digits; anything else never goes into an address."""
    return isinstance(value, str) and bool(_DIGITS.match(value))


# ---------------------------------------------------------------------------------------------
# the one HTTP call
# ---------------------------------------------------------------------------------------------
def _call(what, method, url, token, body=None, content_type=None, timeout=TIMEOUT_READ):
    headers = {"Accept": "*/*", "Authorization": "Bearer " + token, "User-Agent": "K-BOT"}
    if content_type:
        headers["Content-Type"] = content_type
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read(MAX_ANSWER + 1)
    except urllib.error.HTTPError as err:
        status = err.code
        logger.error("[efactura] ANAF %s: HTTP %s", what, status)
        if status in (401, 403):
            raise TokenNecesar("ANAF nu mai acceptă tokenul unității. Faceți din nou pasul cu certificatul.") from None
        if status in (408, 429) or status >= 500:
            raise AnafIndisponibil(f"ANAF nu poate răspunde acum (HTTP {status}). Încercați mai târziu.") from None
        raise EfEroare(f"ANAF a refuzat cererea (HTTP {status}).", "ANAF_EROARE", 502) from None
    except (urllib.error.URLError, TimeoutError, OSError) as err:
        logger.error("[efactura] ANAF %s: unreachable: %s", what, type(err).__name__)
        raise AnafIndisponibil("ANAF nu a putut fi contactat. Încercați mai târziu.") from None


def _bounded(data, what):
    if len(data) > MAX_ANSWER:
        logger.error("[efactura] ANAF %s: answer larger than %s bytes", what, MAX_ANSWER)
        raise EfEroare("Răspunsul ANAF este prea mare.", "ANAF_RASPUNS", 502)
    return data


def _root(data, what):
    """The root element of an XML answer, with no namespace in its attribute names."""
    try:
        return ET.fromstring(data)
    except ET.ParseError:
        logger.error("[efactura] ANAF %s: the answer is not XML", what)
        raise EfEroare("Răspunsul ANAF nu poate fi citit.", "ANAF_RASPUNS", 502) from None


def _first_attribute(element):
    """Access read `Attributes(0).Text` of the error element: the first attribute is the message."""
    return clean(next(iter(element.attrib.values()), ""))


# ---------------------------------------------------------------------------------------------
# the calls
# ---------------------------------------------------------------------------------------------
def upload(token, cui, xml_bytes):
    """Uploads one invoice. Raises only for transport / token / unreadable answers; ANAF's own refusal of the FILE
    comes back as `UploadResult(index="", error=<text>)` (Access showed it as «Eroare la prelucrarea facturii»)."""
    url = f"{BASE}/upload?standard=UBL&cif={urllib.parse.quote(str(cui), safe='')}"
    root = _root(_bounded(_call("upload", "POST", url, token, xml_bytes, "application/xml", TIMEOUT_UPLOAD), "upload"),
                 "upload")
    children = list(root)
    if children:
        return UploadResult("", _first_attribute(children[0]) or "ANAF a refuzat fișierul.")
    index = root.attrib.get("index_incarcare", "").strip()
    if not is_id(index):
        logger.error("[efactura] ANAF upload: no usable index_incarcare in the answer")
        raise EfEroare("ANAF nu a dat numărul încărcării.", "ANAF_RASPUNS", 502)
    return UploadResult(index, "")


def stare(token, id_incarcare):
    """The state of an uploaded invoice (`StatusFactura`)."""
    if not is_id(id_incarcare):
        raise EfEroare("Numărul încărcării este invalid.", "ANAF_RASPUNS", 409)
    url = f"{BASE}/stareMesaj?id_incarcare={id_incarcare}"
    root = _root(_bounded(_call("stare", "GET", url, token), "stare"), "stare")
    children = list(root)
    if children:
        return StateResult("", "", _first_attribute(children[0]) or "ANAF a raportat o eroare.")
    return StateResult(clean(root.attrib.get("stare", "")), clean(root.attrib.get("id_descarcare", ""), 32), "")


def descarca(token, download_id):
    """The zip ANAF keeps under `download_id` (the signed invoice, or the error report). A JSON body is an error."""
    if not is_id(download_id):
        raise EfEroare("Identificatorul de descărcare este invalid.", "ANAF_RASPUNS", 409)
    data = _bounded(_call("descarca", "GET", f"{BASE}/descarcare?id={download_id}", token), "descarca")
    if data[:1] == b"{":
        try:
            text = clean(json.loads(data.decode("utf-8", errors="replace")).get("eroare", ""))
        except (ValueError, AttributeError):
            text = ""
        raise EfEroare("ANAF nu a dat fișierul" + (f": {text}" if text else "."), "ANAF_EROARE", 502)
    if not data:
        raise EfEroare("ANAF a răspuns fără fișier.", "ANAF_RASPUNS", 502)
    return data


def factura_din_zip(zip_bytes):
    """The invoice XML inside the zip ANAF keeps for an accepted invoice: the one file that is not the signature."""
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
            for info in archive.infolist():
                name = info.filename.lower()
                if not name.endswith(".xml") or "semnatura" in name or "signature" in name or info.file_size > MAX_ENTRY:
                    continue
                with archive.open(info) as handle:
                    return handle.read(MAX_ENTRY + 1)[:MAX_ENTRY]
    except (zipfile.BadZipFile, OSError, RuntimeError) as err:
        logger.error("[efactura] invoice zip could not be read: %s", type(err).__name__)
    raise EfEroare("Arhiva ANAF nu conține fișierul facturii.", "ANAF_RASPUNS", 502)


def pdf_din_xml(xml_bytes):
    """The PDF ANAF draws from an invoice XML (the public transformation service, no token). Slice 00EF-09.
    The standard is read from the root element: a `CreditNote` goes to FCN, everything else to FACT1."""
    standard = "FCN" if b"<CreditNote" in xml_bytes[:2048] else "FACT1"
    request = urllib.request.Request(
        f"{PDF_URL}/{standard}/DA", data=xml_bytes, method="POST",
        headers={"Content-Type": "text/plain; charset=UTF-8", "Accept": "*/*", "User-Agent": "K-BOT"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_UPLOAD) as response:
            status, body = response.status, response.read(MAX_ANSWER + 1)
    except urllib.error.HTTPError as err:
        status, body = err.code, err.read(MAX_ENTRY)
    except (urllib.error.URLError, TimeoutError, OSError) as err:
        logger.error("[efactura] ANAF pdf: unreachable: %s", type(err).__name__)
        raise AnafIndisponibil("Serviciul ANAF care desenează factura nu a putut fi contactat. Încercați mai târziu.") from None
    if status >= 500 or status in (408, 429):
        logger.error("[efactura] ANAF pdf: HTTP %s", status)
        raise AnafIndisponibil(f"Serviciul ANAF care desenează factura nu răspunde (HTTP {status}).")
    if body[:4] != b"%PDF":
        logger.error("[efactura] ANAF pdf: HTTP %s, the answer is not a PDF", status)
        text = clean(body.decode("utf-8", errors="replace"), 200)
        raise EfEroare("ANAF nu a dat PDF-ul facturii" + (f": {text}" if text else "."), "ANAF_PDF", 502)
    return _bounded(body, "pdf")


def lista_mesaje(token, cui, days):
    """The messages of the last `days` days for `cui`: a list of dicts with the MESSAGE_KEYS that ANAF sent.
    `id` is what `descarca` takes (verified on a real answer, 09.10.2026); `id_solicitare` is the supplier's upload number, the
    name of the XML inside the zip. `cif_emitent` / `cif_beneficiar` are read from `detalii`."""
    url = f"{BASE}/listaMesajeFactura?zile={int(days)}&cif={urllib.parse.quote(str(cui), safe='')}"
    data = _bounded(_call("lista", "GET", url, token), "lista")
    if not data.strip():
        return []
    try:
        payload = json.loads(data.decode("utf-8", errors="replace"))
    except ValueError:
        logger.error("[efactura] ANAF lista: the answer is not JSON")
        raise EfEroare("Răspunsul ANAF nu poate fi citit.", "ANAF_RASPUNS", 502) from None
    if not isinstance(payload, dict):
        raise EfEroare("Răspunsul ANAF nu poate fi citit.", "ANAF_RASPUNS", 502)
    if payload.get("eroare"):
        raise EfEroare("ANAF: " + clean(payload["eroare"]), "ANAF_EROARE", 502)
    out = []
    for item in payload.get("mesaje") or []:
        if isinstance(item, dict):
            message = {key: clean(item[key], 500) for key in MESSAGE_KEYS if key in item and item[key] is not None}
            # ANAF sends the two tax codes only inside `detalii` («... cif_emitent=N pentru cif_beneficiar=M»)
            for key in ("cif_emitent", "cif_beneficiar"):
                found = re.search(key + r"=(\d{1,32})", message.get("detalii", ""))
                if key not in message and found:
                    message[key] = found.group(1)
            out.append(message)
    return out


def error_messages(zip_bytes):
    """The reasons inside an ANAF error zip: for each XML file, the first attribute of each child of the root
    (what `ParseXML_ERR` read). Best effort: an unreadable zip gives an empty list and a log line."""
    messages = []
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
            for info in archive.infolist():
                name = info.filename.lower()
                if not name.endswith(".xml") or "semnatura" in name or "signature" in name or info.file_size > MAX_ENTRY:
                    continue
                with archive.open(info) as handle:
                    root = ET.fromstring(handle.read(MAX_ENTRY))
                messages += [text for text in (_first_attribute(child) for child in root) if text]
    except (zipfile.BadZipFile, ET.ParseError, OSError, RuntimeError) as err:
        logger.error("[efactura] error zip could not be read: %s", type(err).__name__)
    return messages[:MAX_MESSAGES]
