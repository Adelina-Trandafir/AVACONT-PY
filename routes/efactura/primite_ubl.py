# routes/efactura/primite_ubl.py
"""
Reads the UBL XML of a RECEIVED invoice or credit note (slice 00EF-17). Pure: no database, no network.

Written against real ANAF files (`Surse/EF_EXEMPLE`, 3 invoices of 2024): the suppliers' software differs a lot (some send
`UBLVersionID` and `TaxPointDate`, some do not; one sends 11 `PaymentMeans`; one carries a PDF in an
`AdditionalDocumentReference`; the VAT rate is 9 or 19; element texts may hold only white space). So every read is lenient:
a missing element is an empty value, never an error. The only refusal is a file that is not an `Invoice` or a `CreditNote`.

What comes out (`parse(xml_bytes)` -> dict):
  cote     [{Categorie, CotaTVA, Baza, TVA}] one per TaxSubtotal (an invoice may carry several rates)
  header   NrFact, DataFact, DataScad, Tip («FC» / «NC»), Semn, CUI (as written, «RO» kept), CuiNormalizat, DenumireP, Adresa,
           Valoare (taxable amount), TVA, CotaTVA (the rate of the biggest taxable amount), Total (payable), Atasament
           (file name of the first embedded document), Ref («number / date» of the invoice a credit note refers to)
  linii    NrLinie, Denumire, Explicatie, Unit, Cant, Pret, Valoare
  note     the document-level notes (text)
  atasamente  [{nume, mime, octeti}] the embedded documents (the bytes are decoded by `atasament_bytes`)

The rule for `CuiNormalizat` is the same as `EfCui.Normalize` in KBot.Migrator and the partner join of the DDF view: letters and
digits only, upper case, a leading «RO» before digits dropped. Change it in all places or in none.
"""
import base64
import binascii
import re
import xml.etree.ElementTree as ET
from decimal import Decimal, InvalidOperation

MAX_XML = 25 * 1024 * 1024
_NS = "{*}"


class XmlNeinteles(ValueError):
    """The file is not a UBL invoice / credit note that can be read."""


def normalize_cui(value):
    cleaned = "".join(ch for ch in str(value or "") if ch.isalnum()).upper()
    if len(cleaned) > 2 and cleaned.startswith("RO") and cleaned[2].isdigit():
        cleaned = cleaned[2:]
    return cleaned


# ---------------------------------------------------------------------------------------------
# small readers
# ---------------------------------------------------------------------------------------------
def _find(element, path):
    """First element at a path of local names, e.g. `cac:Party/cac:PostalAddress`, ignoring namespaces."""
    if element is None:
        return None
    return element.find("/".join(_NS + part for part in path.split("/")))


def _findall(element, path):
    if element is None:
        return []
    return element.findall("/".join(_NS + part for part in path.split("/")))


def _text(element, path=None):
    node = element if path is None else _find(element, path)
    if node is None or node.text is None:
        return ""
    return " ".join(node.text.split())


def _number(value):
    text = str(value or "").strip().replace(",", ".")
    if not text:
        return Decimal(0)
    try:
        return Decimal(text)
    except InvalidOperation:
        return Decimal(0)


def _money(element, path):
    return _number(_text(element, path))


def _date(value):
    text = (value or "").strip()
    return text[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", text) else None


def _clip(text, limit):
    return text[:limit] if text else text


# ---------------------------------------------------------------------------------------------
# parts
# ---------------------------------------------------------------------------------------------
def _supplier(root):
    party = _find(root, "AccountingSupplierParty/Party")
    # the VAT number («RO7273547») is in PartyTaxScheme; PartyLegalEntity/CompanyID is the trade-register number
    cui = _text(party, "PartyTaxScheme/CompanyID")
    if not cui:
        cui = _text(party, "PartyLegalEntity/CompanyID")
    if not cui:
        cui = _text(party, "PartyIdentification/ID")
    name = _text(party, "PartyLegalEntity/RegistrationName") or _text(party, "PartyName/Name")
    address = _find(party, "PostalAddress")
    parts = [_text(address, "StreetName"), _text(address, "AdditionalStreetName"), _text(address, "CityName"),
             _text(address, "CountrySubentity")]
    return cui, name, ", ".join(part for part in parts if part)


def _due_date(root):
    due = _date(_text(root, "DueDate"))
    if due:
        return due
    for means in _findall(root, "PaymentMeans"):
        due = _date(_text(means, "PaymentDueDate"))
        if due:
            return due
    return _date(_text(root, "PaymentTerms/PaymentDueDate"))


def _totals(root):
    currency = _text(root, "DocumentCurrencyCode")
    monetary = _find(root, "LegalMonetaryTotal")
    payable = _money(monetary, "PayableAmount")
    if not payable:
        payable = _money(monetary, "TaxInclusiveAmount")
    taxable = _money(monetary, "TaxExclusiveAmount") or _money(monetary, "LineExtensionAmount")

    # UBL may carry a second TaxTotal in the accounting currency: take the one in the document currency
    vat, rate, biggest, rates = Decimal(0), Decimal(0), None, []
    totals = _findall(root, "TaxTotal")
    chosen = next((t for t in totals if _find(t, "TaxAmount") is not None
                   and _find(t, "TaxAmount").attrib.get("currencyID") == currency), totals[0] if totals else None)
    if chosen is not None:
        vat = _money(chosen, "TaxAmount")
        for sub in _findall(chosen, "TaxSubtotal"):
            base = _money(sub, "TaxableAmount")
            percent = _money(sub, "TaxCategory/Percent")
            rates.append({"Categorie": _clip(_text(sub, "TaxCategory/ID"), 8) or None, "CotaTVA": percent, "Baza": base,
                          "TVA": _money(sub, "TaxAmount")})
            if biggest is None or base > biggest:
                biggest, rate = base, percent
    return taxable, vat, rate, payable, rates


def _reference(root):
    ref = _find(root, "BillingReference/InvoiceDocumentReference") or _find(root, "BillingReference/CreditNoteDocumentReference")
    number, issued = _text(ref, "ID"), _text(ref, "IssueDate")
    return " / ".join(part for part in (number, issued) if part)


def _attachments(root):
    out = []
    for reference in _findall(root, "AdditionalDocumentReference"):
        node = _find(reference, "Attachment/EmbeddedDocumentBinaryObject")
        if node is None or not (node.text or "").strip():
            continue
        out.append({"nume": node.attrib.get("filename") or "atasament", "mime": node.attrib.get("mimeCode") or "",
                    "octeti": len(re.sub(r"\s", "", node.text)) * 3 // 4, "_referinta": _text(reference, "ID")})
    return out


def _lines(root, credit):
    lines = []
    for index, line in enumerate(_findall(root, "CreditNoteLine" if credit else "InvoiceLine"), 1):
        quantity = _find(line, "CreditedQuantity" if credit else "InvoicedQuantity")
        explanation = [_text(line, "Item/Description")]
        explanation += [text for text in (_text(note) for note in _findall(line, "Note")) if text]
        explanation += [text for text in (_text(note) for note in _findall(line, "Item/Note")) if text]
        lines.append({
            "NrLinie": _clip(_text(line, "ID") or str(index), 16),
            "Denumire": _clip(_text(line, "Item/Name") or _text(line, "Item/Description"), 500),
            "Explicatie": "\n".join(dict.fromkeys(text for text in explanation if text)) or None,
            "Unit": _clip(quantity.attrib.get("unitCode", "") if quantity is not None else "", 16) or None,
            "Cant": _number(quantity.text if quantity is not None else ""),
            "Pret": _money(line, "Price/PriceAmount"),
            "Valoare": _money(line, "LineExtensionAmount"),
        })
    return lines


# ---------------------------------------------------------------------------------------------
# entry points
# ---------------------------------------------------------------------------------------------
def parse(xml_bytes):
    """The data of one received invoice; see the module text. Raises `XmlNeinteles` for a file that is not UBL."""
    if not xml_bytes or len(xml_bytes) > MAX_XML:
        raise XmlNeinteles("Fișierul facturii lipsește sau este prea mare.")
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        raise XmlNeinteles("Fișierul facturii nu este XML valid.") from None
    kind = root.tag.rsplit("}", 1)[-1]
    if kind not in ("Invoice", "CreditNote"):
        raise XmlNeinteles(f"Fișierul nu este o factură UBL (rădăcina «{kind}»).")
    credit = kind == "CreditNote"

    cui, name, address = _supplier(root)
    taxable, vat, rate, payable, rates = _totals(root)
    type_code = _text(root, "InvoiceTypeCode") or _text(root, "CreditNoteTypeCode")
    note_type = credit or type_code in ("381", "261", "396")      # credit note / self-billed credit note / factored credit note
    # a credit note with a positive total counts against the account (Access EFT.NC); one already negative is not turned again
    sign = -1 if note_type and payable > 0 else 1
    attachments = _attachments(root)

    return {
        "NrFact": _clip(_text(root, "ID"), 64),
        "DataFact": _date(_text(root, "IssueDate")),
        "DataScad": _due_date(root),
        "Tip": "NC" if note_type else "FC",
        "Semn": sign,
        "CUI": _clip(cui, 32) or None,
        "CuiNormalizat": _clip(normalize_cui(cui), 32) or None,
        "DenumireP": _clip(name, 255) or None,
        "Adresa": _clip(address, 500) or None,
        "Valoare": taxable, "TVA": vat, "CotaTVA": rate, "Total": payable, "cote": rates,
        "Atasament": _clip(attachments[0]["nume"], 255) if attachments else None,
        "Ref": _clip(_reference(root), 255) or None,
        "linii": _lines(root, credit),
        "note": [text for text in (_text(note) for note in _findall(root, "Note")) if text],
        "atasamente": [{key: value for key, value in item.items() if not key.startswith("_")} for item in attachments],
    }


def atasament_bytes(xml_bytes, index):
    """(name, mime, bytes) of the `index`-th embedded document (0-based), or None when there is none / it cannot be decoded."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return None
    found = [node for node in (_find(ref, "Attachment/EmbeddedDocumentBinaryObject") for ref in _findall(root, "AdditionalDocumentReference"))
             if node is not None and (node.text or "").strip()]
    if not 0 <= index < len(found):
        return None
    node = found[index]
    try:
        data = base64.b64decode(re.sub(r"\s", "", node.text), validate=True)
    except (binascii.Error, ValueError):
        return None
    return node.attrib.get("filename") or "atasament", node.attrib.get("mimeCode") or "application/octet-stream", data
