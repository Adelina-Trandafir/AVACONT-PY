# routes/efactura/trimitere.py
"""
Sending an issued invoice to ANAF, reading its state, downloading the answer, and the ANAF messages (slice 00EF-07).
The calls themselves are in anaf_api.py; the XML and the checks come from 00EF-06 (ubl.py, validare.py); the token comes
from tokens.access_token(dc) (00EF-04). The PC never sees a token.

THE FLOW, as the operator decided (06.10.2026): the VBA flow of `bSAV_Click` / `TrimiteFactura` is the reference.

  1. `trimite`   our checks -> ANAF's validation service -> upload. Nothing is written unless ANAF took the file; then
                 `id_incarcare` is stored, `id_descarcare` cleared, `Trimisa` = 1: the invoice is «incarcata».
  2. the screen waits 2-3 seconds (that pause is the screen's, 00EF-09) and calls
  3. `verifica`  ANAF's state: «ok» -> `id_descarcare` (acceptata); an error text or «nok» -> `id_descarcare` = «Err» and
                 the reason in `EroareAnaf` (refuzata); «in prelucrare» -> nothing changes and the screen offers
                 the re-check button, which calls the same route.

Differences from Access, deliberate:
  * (00EF-14) an invoice with `AtasamentOriginal` is sent with its classic PDF inside the XML; the PDF comes from the PC in the body
    (`atasament_pdf`, base64), the server checks it and embeds it. Without it such an invoice is not sent;
  * the validation before the upload is done by the server itself, not left to the caller;
  * an accepted invoice is CORRECTED by `trimite` with a `corectie` (comment and order reference): the change and type 384
    are written only after ANAF took the new file, so a failed attempt leaves the invoice as it was. (Access wrote the
    change first, then sent.) A draft is sent with no `corectie`;
  * the reason of a refusal is kept (`EroareAnaf`); Access showed ANAF's text once and did not store it;
  * the invoice row is LOCKED from the first check to the commit, so two clicks cannot upload it twice (the second waits,
    then finds it already «incarcata»). The lock is held during the calls to ANAF (up to a minute at worst).

If the database write fails AFTER ANAF took the file, the upload number is written to the server log (it is not a secret)
so the invoice can be reconciled by hand; that is the one case this code cannot repair on its own.
"""
import base64
import binascii
import logging

from . import anaf_api, facturi as F, facturi_store as store, tokens, ubl, validare
from .tokens import EfEroare

logger = logging.getLogger(__name__)

MAX_PDF_BYTES = 3 * 1024 * 1024                  # ANAF takes 5 MB per upload, base64 adds a third
MAX_DAYS = 60                                   # ANAF lists the messages of at most 60 days
RECEIVED = "FACTURA PRIMITA"
SENT = "FACTURA TRIMISA"
REFUSED_STATES = ("nok", "xml cu erori nepreluat")


class Refuzata(EfEroare):
    """A refusal that carries extra fields for the answer (`constatari`, `mesaje`)."""

    def __init__(self, message, reason, status, extra=None):
        super().__init__(message, reason, status)
        self.extra = extra or {}


# ---------------------------------------------------------------------------------------------
# sending
# ---------------------------------------------------------------------------------------------
def _correction(row, data):
    """(comentarii, bt_13) of a correction: only those two keys; a missing one keeps the invoice's own value."""
    if not isinstance(data, dict):
        raise EfEroare("Corecția trebuie să fie un obiect cu comentarii și referința comenzii.", "CAMP_INVALID", 400)
    if set(data) - {"Comentarii", "BT_13"}:
        raise EfEroare("O factură acceptată se corectează doar prin comentarii și referința comenzii; "
                       "pentru restul, factura se stornează.", "CAMP_NEPERMIS", 400)
    comments = F._text(data, "Comentarii", "Comentariile", 255) if "Comentarii" in data else row["Comentarii"]
    order = F._text(data, "BT_13", "Referința comenzii", 30) if "BT_13" in data else row["BT_13"]
    return comments, order


def _attachment(row, pdf_b64):
    """The classic PDF to embed, or None. The invoice decides: only an invoice with `AtasamentOriginal` carries one, and one that
    asks for it cannot be sent without it (the PC draws the PDF; a missing one is a defect, never a silent skip)."""
    if not row.get("AtasamentOriginal"):
        return None
    if not pdf_b64:
        raise EfEroare("Factura cere atașarea facturii originale, dar PDF-ul nu a fost trimis. Încercați din nou.",
                       "ATASAMENT_LIPSA", 400)
    try:
        pdf = base64.b64decode(pdf_b64, validate=True)
    except (binascii.Error, ValueError):
        raise EfEroare("PDF-ul atașat nu este valid.", "ATASAMENT_INVALID", 400) from None
    if not pdf.startswith(b"%PDF"):
        raise EfEroare("Fișierul atașat nu este un PDF.", "ATASAMENT_INVALID", 400)
    if len(pdf) > MAX_PDF_BYTES:
        raise EfEroare("PDF-ul atașat este prea mare (cel mult 3 MB).", "ATASAMENT_PREA_MARE", 400)
    return pdf


def _sendable(row, state, correction):
    """None for a draft; (comentarii, bt_13) for a correction; a refusal for every other case."""
    if state == F.DRAFT:
        if correction is not None:
            raise EfEroare("Doar o factură acceptată de ANAF se corectează; o ciornă se trimite așa cum este.",
                           "CAMP_NEPERMIS", 400)
        return None
    if state == F.ACCEPTED:
        if row["IdFacturaA"] is not None:
            raise EfEroare("O factură de stornare acceptată nu se mai trimite.", "DEJA_ACCEPTATA", 409)
        if correction is None:
            raise EfEroare("Factura a fost deja acceptată de ANAF. O corecție se trimite cu comentariile noi.",
                           "DEJA_ACCEPTATA", 409)
        return _correction(row, correction)
    if state == F.UPLOADED:
        raise EfEroare("Factura a fost deja trimisă. Verificați starea ei.", "DEJA_TRIMISA", 409)
    raise EfEroare("Factura a fost refuzată de ANAF și nu se mai retrimite.", "REFUZATA", 409)


def trimite(dc, id_factura, correction_data=None, pdf_b64=None):
    """Checks, validates and uploads one invoice. Answers `{"factura": <detail>, "id_incarcare", "constatari"}`
    (`constatari` = the warnings of our checks; errors would have stopped here)."""
    with F._unit(dc) as conn:
        cursor = conn.cursor(dictionary=True)
        row = F._lock(cursor, id_factura)
        correction = _sendable(row, F.stare_factura(row), correction_data)

        furnizor = store.furnizor_get(cursor, dc)
        if furnizor is None:
            raise EfEroare("Datele unității care emite facturile nu sunt completate.", "FURNIZOR_LIPSA", 409)
        client = store.client_get(cursor, row["IdClient"])
        lines = store.linii_get(cursor, id_factura)
        effective = row if correction is None else {**row, "TipFactura": "384",
                                                    "Comentarii": correction[0], "BT_13": correction[1]}
        bank = F._bank_for(row["ContPlata"])
        pdf = _attachment(row, pdf_b64)

        findings = validare.verifica(furnizor, client, effective, lines, F._known_units(), bank)
        if validare.has_errors(findings):
            raise Refuzata("Factura are erori și nu poate fi trimisă.", "INVALIDA", 409, {"constatari": findings})

        try:
            verdict = validare.anaf_valideaza(ubl.build(furnizor, client, effective, lines, bank, schema_location=False,
                                                                attachment_pdf=pdf))
        except validare.ValidareIndisponibila as err:
            raise Refuzata(f"{err} Factura nu a fost trimisă.", "VALIDARE_INDISPONIBILA", 502) from err
        if not verdict["ok"]:
            raise Refuzata("Validatorul ANAF a respins factura.", "RESPINSA_DE_VALIDATOR", 409,
                           {"constatari": findings, "mesaje": verdict["mesaje"]})

        cui, token = tokens.access_token(dc)
        outcome = anaf_api.upload(token, cui, ubl.build(furnizor, client, effective, lines, bank, attachment_pdf=pdf))
        if outcome.error:
            raise Refuzata(f"ANAF a refuzat fișierul: {outcome.error}", "ANAF_REFUZA_INCARCAREA", 409,
                           {"mesaje": [outcome.error]})

        try:
            store.factura_mark_uploaded(cursor, id_factura, outcome.index, correction)
            conn.commit()
        except Exception:
            logger.error("[efactura] invoice IdFactura=%s WAS UPLOADED (index_incarcare=%s) but the database write failed",
                         id_factura, outcome.index, exc_info=True)
            raise
        return {"factura": F._detail(cursor, id_factura), "id_incarcare": outcome.index, "constatari": findings}


# ---------------------------------------------------------------------------------------------
# state
# ---------------------------------------------------------------------------------------------
def _refusal_text(token, answer):
    """The text kept for a refused invoice: ANAF's error text, or the reasons read from its error zip."""
    if answer.error:
        return answer.error
    reasons = []
    if anaf_api.is_id(answer.download_id):
        try:
            reasons = anaf_api.error_messages(anaf_api.descarca(token, answer.download_id))
        except EfEroare as err:                       # the reasons are a bonus; the refusal itself is already known
            logger.error("[efactura] the error report could not be downloaded: %s", err.reason)
    text = f"ANAF a refuzat factura (stare «{answer.state}»)."
    if reasons:
        text += " Motive: " + "; ".join(reasons) + "."
    if anaf_api.is_id(answer.download_id):
        text += f" Identificator descărcare: {answer.download_id}."
    return text


def verifica(dc, id_factura):
    """Reads the state at ANAF of an uploaded invoice and writes what it says. Answers
    `{"rezultat": "acceptata"|"refuzata"|"in_prelucrare"|"necunoscut", "stare_anaf", "mesaj", "factura"}`.
    «in prelucrare» and «necunoscut» change nothing, so the call can be repeated."""
    with F._unit(dc) as conn:
        cursor = conn.cursor(dictionary=True)
        row = F._lock(cursor, id_factura)
        if F.stare_factura(row) != F.UPLOADED:
            raise EfEroare("Starea se verifică doar pentru o factură trimisă, dar încă neconfirmată de ANAF.",
                           "NU_SE_VERIFICA", 409)
        _, token = tokens.access_token(dc)
        answer = anaf_api.stare(token, row["id_incarcare"].strip())
        lowered = answer.state.lower()

        if answer.error or lowered in REFUSED_STATES:
            text = _refusal_text(token, answer)
            store.factura_mark_refused(cursor, id_factura, text)
            result, message = "refuzata", text
        elif lowered == "ok" and anaf_api.is_id(answer.download_id):
            store.factura_mark_accepted(cursor, id_factura, answer.download_id)
            result, message = "acceptata", "ANAF a acceptat factura."
        elif lowered == "in prelucrare":
            result, message = "in_prelucrare", "Factura este încă în prelucrare la ANAF."
        else:
            logger.error("[efactura] unknown state from ANAF for IdFactura=%s: %r", id_factura, answer.state)
            result, message = "necunoscut", f"Stare ANAF neașteptată: «{answer.state}». Nimic nu s-a schimbat."
        conn.commit()
        return {"rezultat": result, "stare_anaf": answer.state, "mesaj": message,
                "factura": F._detail(cursor, id_factura)}


# ---------------------------------------------------------------------------------------------
# download
# ---------------------------------------------------------------------------------------------
def descarca(dc, id_factura):
    """(zip bytes, file name) of an ACCEPTED invoice: the signed file ANAF keeps under `id_descarcare`."""
    with F._unit(dc) as conn:
        row = store.factura_get(conn.cursor(dictionary=True), id_factura)
    if row is None:
        raise EfEroare("Factura nu există.", "NU_EXISTA", 404)
    if F.stare_factura(row) != F.ACCEPTED:
        raise EfEroare("Fișierul se descarcă doar pentru o factură acceptată de ANAF.", "NU_SE_DESCARCA", 409)
    _, token = tokens.access_token(dc)
    return anaf_api.descarca(token, row["id_descarcare"].strip()), ubl.file_name(row)[:-4] + ".zip"


def pdf_anaf(dc, id_factura):
    """(pdf bytes, file name) of an ACCEPTED invoice as ANAF draws it (slice 00EF-09): the signed file ANAF keeps is
    downloaded, its XML is taken out of the zip and sent to ANAF's public XML -> PDF service."""
    zip_bytes, name = descarca(dc, id_factura)
    return anaf_api.pdf_din_xml(anaf_api.factura_din_zip(zip_bytes)), name[:-4] + ".pdf"


# ---------------------------------------------------------------------------------------------
# messages
# ---------------------------------------------------------------------------------------------
def mesaje(dc, days, received_only):
    """The ANAF messages of the last `days` days for the unit's tax code. As in Access, messages of kind
    «FACTURA TRIMISA» are left out; with `received_only` only «FACTURA PRIMITA» stays. Each carries
    `deja_in_baza` = its `id_solicitare` is already in EF_Mesaje."""
    if not 1 <= days <= MAX_DAYS:
        raise EfEroare(f"Numărul de zile trebuie să fie între 1 și {MAX_DAYS}.", "CAMP_INVALID", 400)
    cui, token = tokens.access_token(dc)
    items = anaf_api.lista_mesaje(token, cui, days)
    items = [m for m in items if (m.get("tip") == RECEIVED if received_only else m.get("tip") != SENT)]
    ids = [m["id_solicitare"] for m in items if anaf_api.is_id(m.get("id_solicitare", ""))]
    with F._unit(dc) as conn:
        known = store.messages_known(conn.cursor(dictionary=True), ids)
    for item in items:
        item["deja_in_baza"] = item.get("id_solicitare") in known
    return {"cui": cui, "zile": days, "mesaje": items}


def descarca_mesaj(dc, id_sol):
    """The zip of one ANAF message (`descarcare?id=<id_solicitare>`)."""
    if not anaf_api.is_id(id_sol):
        raise EfEroare("Identificatorul mesajului este invalid.", "CAMP_INVALID", 400)
    _, token = tokens.access_token(dc)
    return anaf_api.descarca(token, id_sol), f"mesaj_{id_sol}.zip"
