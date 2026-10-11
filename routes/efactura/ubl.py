# routes/efactura/ubl.py
"""
UBL 2.1 / CIUS-RO invoice XML of an ISSUED invoice (slice 00EF-06). Pure functions: no database, no network,
no clock -- every input is a plain dict, so the output can be compared against what Access produced.

PORTED FROM `Surse/RawExport/Modules/mdl_EFactura_Add.bas.txt` (`GENEREAZA_XML_EFACTURA` and its `Add*`
helpers) and the query `qFacturi_Vanzare`, read in full 06.10.2026. The ELEMENTS, their ORDER and their
constant values are the same as Access wrote. Kept on purpose, even where they look odd, because ANAF
accepted those invoices:

  * every invoice is in RON, VAT category `Z` at 0 %, TaxAmount `0.0` (the unit sells without VAT);
  * DueDate = IssueDate + 10 days;
  * the supplier PartyTaxScheme holds an EMPTY `cac:TaxScheme` (the `VAT` id was commented out in Access);
  * the customer is identified by `CodFiscal` as typed. Access did NOT use `ClientiEF.IndFiscal`, `Cont`,
    `Banca` or `Sector` in the XML, and neither does this;
  * the customer has no PartyTaxScheme; PaymentMeansCode is `42`; the bank name comes from `BIC.Banca`
    for the four characters at position 5-8 of the unit's IBAN;
  * the `cac:Contact` of the supplier is written even when it has neither phone nor mail.

DIFFERENCES from Access (each one is deliberate; the comparison tool `tools/efactura/compare_xml.py` shows
them if they ever matter):

  * quantity keeps up to 3 decimals and the price up to 4 (Access called `Round(x, 3)` but then formatted
    with `"0.00"`, so 2.125 became 2.13). Whenever the value has at most two decimals the text is identical;
  * rounding is half-up on exact decimals; VBA `Round` is banker's rounding on doubles. Only a value that
    ends exactly in 5 beyond the second decimal can differ, and the stored amounts already have two;
  * the order reference is written only when it holds something other than spaces. Access saved a single
    space for «empty» (`IIf(Len(BT_13)=0, " ", BT_13)`) and its test `Nz(BT_13,"") <> ""` then wrote an
    order reference of one space;
  * (slice 00EF-14) when the invoice asks for it (`AtasamentOriginal`) the classic PDF of the invoice rides inside the XML as
    BG-24 (`cac:AdditionalDocumentReference` / `cac:Attachment` / `cbc:EmbeddedDocumentBinaryObject`, base64, mimeCode
    application/pdf). The PDF is drawn by the PC and handed to `build`; Access did not attach anything;
  * the file is indented (Access wrote it on one line). Whitespace between elements carries no meaning.
"""
import base64
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
from xml.sax.saxutils import escape, quoteattr

NS_ROOT = "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
NS_CBC = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
NS_CAC = "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
NS_EXT = "urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2"
NS_XSI = "http://www.w3.org/2001/XMLSchema-instance"
SCHEMA_LOCATION = NS_ROOT + " http://docs.oasis-open.org/ubl/os-UBL-2.1/xsd/maindoc/UBL-Invoice-2.1.xsd"
CUSTOMIZATION_ID = "urn:cen.eu:en16931:2017#compliant#urn:efactura.mfinante.ro:CIUS-RO:1.0.1"
CURRENCY = "RON"
DUE_DAYS = 10
INDENT = "  "

_TWO = Decimal("0.01")
_THREE = Decimal("0.001")
_FOUR = Decimal("0.0001")


# ---------------------------------------------------------------------------------------------
# number formats (Access `FormatNumber`: zero is written «0.0»)
# ---------------------------------------------------------------------------------------------
def _trim(value, step, min_places):
    """`value` at `step` precision, trailing zeros cut down to `min_places` decimals."""
    quantized = value.quantize(step, ROUND_HALF_UP)
    if quantized == 0:
        quantized = abs(quantized)
    whole, _, decimals = format(quantized, "f").partition(".")
    return whole + "." + decimals.rstrip("0").ljust(min_places, "0")


def amount(value):
    """Money: two decimals; exactly zero is `0.0`."""
    value = Decimal(value)
    return "0.0" if value == 0 else _trim(value, _TWO, 2)


def quantity(value):
    """Quantity: up to three decimals, at least two."""
    value = Decimal(value)
    return "0.0" if value == 0 else _trim(value, _THREE, 2)


def unit_price(value):
    """Unit price: up to four decimals, at least two."""
    value = Decimal(value)
    return "0.0" if value == 0 else _trim(value, _FOUR, 2)


def invoice_total(lines):
    """Sum of the stored line values (Access summed `Valoare`, it never recomputed Cant x PU)."""
    return sum((Decimal(line["Valoare"]) for line in lines), Decimal(0))


def invoice_id(invoice):
    """`cbc:ID`: series and number joined with `_`, an empty series skipped (Access `Concat_WS`)."""
    parts = [str(invoice.get("SerieFactura") or "").strip(), str(invoice["NumarFactura"])]
    return "_".join(part for part in parts if part)


def file_name(invoice):
    """`<series>_<number>_<yyyy_mm_dd>.xml`, the name Access gave the file in `<CALEEF>\\OUT`."""
    parts = [str(invoice.get("SerieFactura") or "").strip(), str(invoice["NumarFactura"]),
             invoice["DataFactura"].strftime("%Y_%m_%d")]
    return "_".join(part for part in parts if part) + ".xml"


# ---------------------------------------------------------------------------------------------
# a tiny element tree: (tag, attributes, text, children). Written by hand so the prefixes and the
# unused `xmlns:ns4` stay exactly as Access wrote them (ElementTree drops unused declarations).
# ---------------------------------------------------------------------------------------------
def _el(tag, text=None, attrs=None, children=None):
    return (tag, attrs or [], text, children or [])


def _clean(text):
    """XML 1.0 forbids most control characters: they are dropped rather than failing the whole invoice."""
    return "".join(ch for ch in str(text) if ch in "\t\n\r" or ord(ch) >= 0x20)


def _render(node, depth, out):
    tag, attrs, text, children = node
    pad = INDENT * depth
    head = pad + "<" + tag + "".join(f" {name}={quoteattr(_clean(value))}" for name, value in attrs)
    if children:
        out.append(head + ">")
        for child in children:
            _render(child, depth + 1, out)
        out.append(pad + "</" + tag + ">")
    elif text is None:
        out.append(head + "/>")
    else:
        out.append(head + ">" + escape(_clean(text)) + "</" + tag + ">")


def _text(value):
    return "" if value is None else str(value).strip()


# ---------------------------------------------------------------------------------------------
# the parts of the document
# ---------------------------------------------------------------------------------------------
def _supplier_party(f):
    cui = _text(f.get("CodFiscal"))
    contact = []
    if _text(f.get("Telefon")):
        contact.append(_el("cbc:Telephone", _text(f["Telefon"])))
    if _text(f.get("Mail")):
        contact.append(_el("cbc:ElectronicMail", _text(f["Mail"])))
    party = [
        _el("cac:PartyIdentification", children=[_el("cbc:ID", cui)]),
        _el("cac:PostalAddress", children=[
            _el("cbc:StreetName", _text(f.get("Adresa"))),
            _el("cbc:CityName", _text(f.get("Orasul"))),
            _el("cbc:CountrySubentity", "RO-" + _text(f.get("Judetul"))),
            _el("cac:Country", children=[_el("cbc:IdentificationCode", "RO")]),
        ]),
        _el("cac:PartyTaxScheme", children=[_el("cbc:CompanyID", cui), _el("cac:TaxScheme")]),
        _el("cac:PartyLegalEntity", children=[
            _el("cbc:RegistrationName", _text(f.get("Denumire"))),
            _el("cbc:CompanyID", cui),
        ]),
        _el("cac:Contact", children=contact),
    ]
    return _el("cac:AccountingSupplierParty", children=[_el("cac:Party", children=party)])


def _customer_party(c):
    cui = _text(c.get("CodFiscal"))
    party = [
        _el("cac:PartyIdentification", children=[_el("cbc:ID", cui)]),
        _el("cac:PostalAddress", children=[
            _el("cbc:StreetName", _text(c.get("Adresa"))),
            _el("cbc:CityName", _text(c.get("Orasul"))),
            _el("cbc:CountrySubentity", "RO-" + _text(c.get("Judetul"))),
            _el("cac:Country", children=[_el("cbc:IdentificationCode", "RO")]),
        ]),
        _el("cac:PartyLegalEntity", children=[
            _el("cbc:RegistrationName", _text(c.get("DenumireClient"))),
            _el("cbc:CompanyID", cui),
        ]),
    ]
    return _el("cac:AccountingCustomerParty", children=[_el("cac:Party", children=party)])


def _payment_means(invoice, bank_name):
    return _el("cac:PaymentMeans", children=[
        _el("cbc:PaymentMeansCode", "42"),
        _el("cac:PayeeFinancialAccount", children=[
            _el("cbc:ID", _text(invoice.get("ContPlata"))),
            _el("cbc:Name", _text(bank_name)),
        ]),
    ])


def _money(tag, text):
    return _el(tag, text, [("currencyID", CURRENCY)])


def _tax_total(total):
    return _el("cac:TaxTotal", children=[
        _money("cbc:TaxAmount", "0.0"),
        _el("cac:TaxSubtotal", children=[
            _money("cbc:TaxableAmount", total),
            _money("cbc:TaxAmount", "0.0"),
            _el("cac:TaxCategory", children=[
                _el("cbc:ID", "Z"),
                _el("cbc:Percent", "0"),
                _el("cac:TaxScheme", children=[_el("cbc:ID", "VAT")]),
            ]),
        ]),
    ])


def _monetary_total(total):
    return _el("cac:LegalMonetaryTotal", children=[
        _money("cbc:LineExtensionAmount", total),
        _money("cbc:TaxExclusiveAmount", total),
        _money("cbc:TaxInclusiveAmount", total),
        _money("cbc:PayableAmount", total),
    ])


def _invoice_line(line):
    return _el("cac:InvoiceLine", children=[
        _el("cbc:ID", _text(line.get("NrCrt"))),
        _el("cbc:InvoicedQuantity", quantity(line["Cant"]), [("unitCode", _text(line.get("Um")))]),
        _money("cbc:LineExtensionAmount", amount(line["Valoare"])),
        _el("cac:Item", children=[
            _el("cbc:Name", _text(line.get("Continut"))),
            _el("cac:ClassifiedTaxCategory", children=[
                _el("cbc:ID", "Z"),
                _el("cbc:Percent", "0.0"),
                _el("cac:TaxScheme", children=[_el("cbc:ID", "VAT")]),
            ]),
        ]),
        _el("cac:Price", children=[_money("cbc:PriceAmount", unit_price(line["PU"]))]),
    ])


def _attachment_ref(invoice, pdf):
    """BG-24: the classic PDF of the invoice, base64 inside the XML. BT-122 = the invoice's own number."""
    return _el("cac:AdditionalDocumentReference", children=[
        _el("cbc:ID", invoice_id(invoice)),
        _el("cbc:DocumentDescription", "Factura originala"),
        _el("cac:Attachment", children=[
            _el("cbc:EmbeddedDocumentBinaryObject", base64.b64encode(pdf).decode("ascii"),
                [("mimeCode", "application/pdf"), ("filename", file_name(invoice)[:-4] + ".pdf")]),
        ]),
    ])


def build(furnizor, client, invoice, lines, bank_name, schema_location=True, attachment_pdf=None):
    """The invoice as UTF-8 bytes.

    furnizor   Unitati_Date row (dict)         client  EF_Clienti row (dict)
    invoice    EF_Facturi row (dict; `DataFactura` is a `date`)
    lines      EF_FacturiLinii rows (dicts, in the order they are to be written)
    bank_name  `BIC.Banca` for characters 5-8 of the invoice's IBAN, or "" when unknown
    schema_location  False for ANAF's validation service: Access removed `xsi:schemaLocation` before that call.
    attachment_pdf   the bytes of the classic PDF to embed (BG-24), or None for no attachment.

    Raises ValueError when there are no lines (Access: «Lipsa produse?»); everything else is the job of
    `validare.verifica`, which tells the operator WHAT is wrong before the XML is built.
    """
    if not lines:
        raise ValueError("Factura nu are nicio linie.")

    total = amount(invoice_total(lines))
    issued = invoice["DataFactura"]

    root_attrs = [
        ("xmlns", NS_ROOT), ("xmlns:cbc", NS_CBC), ("xmlns:cac", NS_CAC),
        ("xmlns:ns4", NS_EXT), ("xmlns:xsi", NS_XSI),
    ]
    if schema_location:
        root_attrs.append(("xsi:schemaLocation", SCHEMA_LOCATION))

    children = [
        _el("cbc:CustomizationID", CUSTOMIZATION_ID),
        _el("cbc:ID", invoice_id(invoice)),
        _el("cbc:IssueDate", issued.strftime("%Y-%m-%d")),
        _el("cbc:DueDate", (issued + timedelta(days=DUE_DAYS)).strftime("%Y-%m-%d")),
        _el("cbc:InvoiceTypeCode", _text(invoice.get("TipFactura")) or "380"),
    ]
    if _text(invoice.get("Comentarii")):
        children.append(_el("cbc:Note", _text(invoice["Comentarii"])))
    children.append(_el("cbc:DocumentCurrencyCode", CURRENCY))
    if _text(invoice.get("BT_13")):
        children.append(_el("cac:OrderReference", children=[_el("cbc:ID", _text(invoice["BT_13"]))]))
    if attachment_pdf:
        children.append(_attachment_ref(invoice, attachment_pdf))
    children += [
        _supplier_party(furnizor),
        _customer_party(client),
        _payment_means(invoice, bank_name),
        _tax_total(total),
        _monetary_total(total),
    ]
    children += [_invoice_line(line) for line in lines]

    out = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>']
    _render(_el("Invoice", None, root_attrs, children), 0, out)
    return ("\n".join(out) + "\n").encode("utf-8")
