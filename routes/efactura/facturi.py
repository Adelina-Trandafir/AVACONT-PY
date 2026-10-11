# routes/efactura/facturi.py
"""
The rules of the ISSUED invoices of E-Factura (slice 00EF-06): the issuer's data, the customers, the invoices and
their lines, the XML and its validation. The SQL is in facturi_store.py, the XML in ubl.py, the checks in validare.py;
the routes (factura_routes.py) only translate HTTP to these functions.

Every function takes the unit's database name `dc` (= `g.session.db_name`) and opens and closes its own transactional
connection to THAT database (`get_kbot_connection(dc)`), plus a short read of AVACONT_COMUN for the two common lists
(`EF_UM`, `BIC`) where it needs them. Failures the operator can act on are `EfEroare` (message in Romanian, stable
`reason`, HTTP status); anything else propagates and the route turns it into the generic 500.

(Sending, state and download are in trimitere.py, slice 00EF-07.)

THE LIFE OF AN INVOICE, read from `Surse/RawExport/Forms/EFACTURA_ADD.txt` (`bMod_Click`, `bSAV_Click`), 06.10.2026:

    ciorna      no `id_incarcare`            editable in full; it may be deleted while it is the last number
    incarcata   `id_incarcare`, no id_descarcare   sent to ANAF, the result not known yet: nothing changes
    refuzata    `id_descarcare` = «Err»      ANAF refused it: Access never edited it again; neither does this
    acceptata   `id_descarcare` numeric      ANAF accepted it: it can be CORRECTED (type 384: only the comment and
                                             the order reference change, `Corectata` = 1) or CANCELLED by a storno

A storno (Access: a «factura de stornare») is a new invoice with `IdFacturaA` = the cancelled one and the same lines
with quantity and value negated; the same save also creates the replacement invoice the operator typed. It is never
edited. The columns `id_incarcare`, `id_descarcare` and `Trimisa` are written by the upload step (00EF-07), not here.

Wire names: the columns keep their names (all ASCII); what is computed here is lower-case snake_case Romanian
(`stare`, `poate_modifica`, `total`, `linii`, `client`, ...). Money and quantities leave as JSON numbers.
"""
import logging
import re
from contextlib import contextmanager
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

import mysql.connector

from utils.database import COMMON_DB, get_kbot_connection

from routes.inregistrare import anaf

from . import facturi_store as store, ubl, validare
from .adresa_anaf import county_and_city
from .tokens import EfEroare

logger = logging.getLogger(__name__)

SQL_FILES = "sql/00EF_02_efactura_unitate.sql și sql/00EF_13_unitati_detalii.sql"
SQL_FILES_CONTURI = "sql/00EF_13_unitati_detalii.sql"
MAX_CONTURI = 50
MAX_LINES = 1000
MAX_LIST = 2000
DEFAULT_LIST = 500

DRAFT, UPLOADED, REFUSED, ACCEPTED = "ciorna", "incarcata", "refuzata", "acceptata"

_ER_BAD_FIELD, _ER_DUP_ENTRY, _ER_NO_SUCH_TABLE = 1054, 1062, 1146
_CUI = re.compile(r"^(RO)?\d{2,10}$")
_TWO = Decimal("0.01")
_LIMIT_QTY = Decimal(10) ** 11          # |Cant| and |PU| stay far below decimal(18,x)
_LIMIT_VALUE = Decimal(10) ** 15


# ---------------------------------------------------------------------------------------------
# connections
# ---------------------------------------------------------------------------------------------
@contextmanager
def _unit(dc, sql_files=SQL_FILES):
    """A transactional connection to the unit database: rolled back on any error, always closed. A missing
    table or column (the DDL was not run) and a taken invoice number become refusals the operator can act on."""
    conn = get_kbot_connection(dc)
    try:
        yield conn
    except mysql.connector.Error as err:
        _rollback(conn)
        errno = getattr(err, "errno", None)
        if errno in (_ER_NO_SUCH_TABLE, _ER_BAD_FIELD):
            logger.error("[efactura] schema: errno=%s %s", errno, getattr(err, "msg", ""))
            raise EfEroare(f"Tabelele E-Factura ale unității lipsesc sau sunt vechi. Rulați {sql_files}.",
                           "TABELE_LIPSA", 503) from err
        if errno == _ER_DUP_ENTRY:
            raise EfEroare("Seria și numărul facturii sunt deja folosite. Reîncercați.", "NUMAR_FOLOSIT", 409) from err
        raise
    except BaseException:
        _rollback(conn)
        raise
    finally:
        if conn.is_connected():
            conn.close()


def _rollback(conn):
    try:
        conn.rollback()
    except mysql.connector.Error as err:
        logger.error("[efactura] rollback failed: %s", err)


def _common(action, default, what):
    """One read of AVACONT_COMUN. A failure is logged and `default` stands in: the lists only feed warnings
    (`UM_NECITITA`, `BANCA_NECUNOSCUTA`), they never decide whether an invoice can be saved."""
    conn = None
    try:
        conn = get_kbot_connection(COMMON_DB)
        return action(conn.cursor(dictionary=True))
    except mysql.connector.Error as err:
        logger.error("[efactura] common list %s could not be read: errno=%s", what, getattr(err, "errno", None))
        return default
    finally:
        if conn is not None and conn.is_connected():
            conn.close()


# ---------------------------------------------------------------------------------------------
# reading what the client sent
# ---------------------------------------------------------------------------------------------
def _bad(message, reason="CAMP_INVALID"):
    return EfEroare(message, reason, 400)


def _text(data, key, label, max_len, required=False, upper=False, multiline=False, strip_spaces=False):
    """A text field: control characters out, trimmed, cut-checked. Blank -> None (or a refusal when required)."""
    value = data.get(key)
    if value is None:
        text = ""
    elif isinstance(value, (str, int)) and not isinstance(value, bool):
        text = str(value)
    else:
        raise _bad(f"{label}: valoare invalidă.")
    keep = "\n" if multiline else ""
    text = "".join(ch for ch in text if ch.isprintable() or ch in keep).strip()
    if strip_spaces:
        text = text.replace(" ", "")
    if upper:
        text = text.upper()
    if required and not text:
        raise _bad(f"{label} este obligatoriu(ă).")
    if len(text) > max_len:
        raise _bad(f"{label}: cel mult {max_len} caractere.")
    return text or None


def _int(value, label, minimum=0, maximum=2147483647):
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise _bad(f"{label}: număr întreg invalid.")
    try:
        number = int(str(value).strip())
    except ValueError:
        raise _bad(f"{label}: număr întreg invalid.") from None
    if not minimum <= number <= maximum:
        raise _bad(f"{label}: valoare în afara limitelor.")
    return number


def _flag(value, label):
    if value in (None, 0, False, "0", ""):
        return 0
    if value in (1, True, "1"):
        return 1
    raise _bad(f"{label}: trebuie să fie da/nu.")


def _decimal(value, label, places):
    """An exact decimal with at most `places` decimals (a value the column cannot hold is refused, not rounded)."""
    if value is None or isinstance(value, bool):
        raise _bad(f"{label}: număr lipsă sau invalid.")
    try:
        number = Decimal(str(value).strip())
    except InvalidOperation:
        raise _bad(f"{label}: număr invalid.") from None
    if not number.is_finite() or abs(number) >= _LIMIT_QTY:
        raise _bad(f"{label}: număr invalid sau prea mare.")
    if number != number.quantize(Decimal(1).scaleb(-places)):
        raise _bad(f"{label}: cel mult {places} zecimale.")
    return number


def _date(value, label):
    if not isinstance(value, str):
        raise _bad(f"{label}: dată lipsă sau invalidă (aaaa-ll-zz).")
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        raise _bad(f"{label}: dată invalidă (aaaa-ll-zz).") from None


def _not_older(value, minimum):
    """Slice 00EF-09: an invoice may not be dated before the one that comes just before it in its series."""
    if minimum is not None and value < minimum:
        raise EfEroare(f"Data facturii nu poate fi mai veche decât a ultimei facturi ({minimum.strftime('%d.%m.%Y')}).",
                       "DATA_PREA_VECHE", 409)


def _parse_lines(raw):
    if not isinstance(raw, list) or not raw:
        raise EfEroare("Factura trebuie să aibă cel puțin o linie.", "LINII_LIPSA", 400)
    if len(raw) > MAX_LINES:
        raise _bad(f"Cel mult {MAX_LINES} linii pe factură.")
    lines = []
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise _bad(f"Linia {index}: format invalid.")
        label = f"Linia {index}"
        cant = _decimal(item.get("Cant"), f"{label}: cantitatea", 3)
        pu = _decimal(item.get("PU"), f"{label}: prețul", 4)
        value = (cant * pu).quantize(_TWO, ROUND_HALF_UP)          # always computed here; a sent `Valoare` is ignored
        if abs(value) >= _LIMIT_VALUE:
            raise _bad(f"{label}: valoarea este prea mare.")
        lines.append({
            "NrCrt": _text(item, "NrCrt", f"{label}: numărul", 16) or str(index),
            "Continut": _text(item, "Continut", f"{label}: denumirea", 1000, required=True, multiline=True),
            "Um": _text(item, "Um", f"{label}: unitatea de măsură", 8, required=True, upper=True),
            "Cant": cant, "PU": pu, "Valoare": value,
            "Platit": _flag(item.get("Platit"), f"{label}: plătit"),
            "Grup": _int(item.get("Grup", 0), f"{label}: grupul"),
        })
    return lines


def _parse_header(data):
    """The fields of a new or edited invoice (never the number, the series, the type or ANAF's ids)."""
    return {
        "IdClient": _int(data.get("IdClient"), "Cumpărătorul", minimum=1),
        "DataFactura": _date(data.get("DataFactura"), "Data facturii"),
        "Comentarii": _text(data, "Comentarii", "Comentariile", 255),
        "BT_13": _text(data, "BT_13", "Referința comenzii", 30),
        "ContPlata": _text(data, "ContPlata", "Contul de plată (IBAN)", 34, required=True, upper=True, strip_spaces=True),
        "AtasamentOriginal": _flag(data.get("AtasamentOriginal"), "Atașează originalul"),
    }


def _parse_invoice(data):
    header = _parse_header(data)
    return header, _parse_lines(data.get("linii"))


# ---------------------------------------------------------------------------------------------
# state and presentation
# ---------------------------------------------------------------------------------------------
def stare_factura(row):
    """ciorna / incarcata / refuzata / acceptata from the two ids ANAF gave (see the module header)."""
    if not (row.get("id_incarcare") or "").strip():
        return DRAFT
    download = (row.get("id_descarcare") or "").strip()
    if download.isdigit():
        return ACCEPTED
    if download.lower() == "err":
        return REFUSED
    return UPLOADED                       # empty, or a value this code does not know: treated as «not settled»


def _number(value):
    return float(value) if isinstance(value, Decimal) else value


def _iso(value):
    return None if value is None else value.isoformat()


def _present_client(row):
    return None if row is None else dict(row)


def _present_line(row):
    return {**row, "Cant": _number(row["Cant"]), "PU": _number(row["PU"]), "Valoare": _number(row["Valoare"])}


def _present_header(row):
    """The columns of an invoice plus what is derived from the row alone (state, total)."""
    out = {name: row.get(name) for name in store.FACTURA_COLUMNS}
    out["DataFactura"] = _iso(row["DataFactura"])
    out["total"] = _number(row.get("Total", 0))
    out["stare"] = stare_factura(row)
    out["este_storno"] = row["IdFacturaA"] is not None
    return out


def _present(row, client, lines, last_of_series, has_storno, min_date=None):
    """The full picture of one invoice: header, customer, lines and what the operator may do with it now.
    `poate_modifica_data` (slice 00EF-09): the date of a draft moves only while it is the last of its series, and never
    before `data_minima` = the date of the invoice just before it."""
    out = _present_header(row)
    state, is_storno = out["stare"], out["este_storno"]
    out["poate_modifica"] = state == DRAFT and not is_storno
    out["poate_modifica_data"] = state == DRAFT and not is_storno and last_of_series
    out["data_minima"] = _iso(min_date)
    out["poate_corecta"] = state == ACCEPTED and not is_storno
    out["poate_storna"] = state == ACCEPTED and not is_storno and not has_storno
    out["poate_sterge"] = state == DRAFT and last_of_series
    out["poate_trimite"] = state == DRAFT
    out["poate_verifica"] = state == UPLOADED
    out["poate_descarca"] = state == ACCEPTED
    out["client"] = _present_client(client)
    out["linii"] = [_present_line(line) for line in lines]
    return out


def _detail(cursor, id_factura):
    row = store.factura_get(cursor, id_factura)
    if row is None:
        raise EfEroare("Factura nu există.", "NU_EXISTA", 404)
    return _present(
        row, store.client_get(cursor, row["IdClient"]), store.linii_get(cursor, id_factura),
        last_of_series=store.is_last_of_series(cursor, row["SerieFactura"], row["NumarFactura"]),
        has_storno=store.storno_of(cursor, id_factura) is not None,
        min_date=store.max_date(cursor, row["SerieFactura"], row["NumarFactura"]))


def _need_furnizor(cursor, dc, for_update=False):
    furnizor = store.furnizor_get(cursor, dc, for_update)
    if furnizor is None:
        raise EfEroare("Datele unității care emite facturile nu sunt completate (denumire, cod fiscal, adresă, serie).",
                       "FURNIZOR_LIPSA", 409)
    return furnizor


def _need_client(cursor, id_client):
    client = store.client_get(cursor, id_client)
    if client is None:
        raise EfEroare("Cumpărătorul ales nu există.", "CLIENT_LIPSA", 400)
    return client


def _lock(cursor, id_factura):
    row = store.factura_get(cursor, id_factura, for_update=True)
    if row is None:
        raise EfEroare("Factura nu există.", "NU_EXISTA", 404)
    return row


# ---------------------------------------------------------------------------------------------
# the issuer
# ---------------------------------------------------------------------------------------------
def _furnizor_answer(cursor, dc):
    """The unit's row as the window needs it: the columns and `are_facturi` (the series and the first number are then
    fixed). `exista` is false when the unit has not filled its data in yet."""
    row = store.furnizor_get(cursor, dc)
    if row is None:
        return {"exista": False, "furnizor": None, "are_facturi": store.have_invoices(cursor)}
    return {"exista": True, "furnizor": dict(row), "are_facturi": store.have_invoices(cursor)}


def furnizor_get(dc):
    with _unit(dc) as conn:
        return _furnizor_answer(conn.cursor(dictionary=True), dc)


def furnizor_set(dc, data):
    values = {
        "Denumire": _text(data, "Denumire", "Denumirea", 255, required=True),
        "CodFiscal": _text(data, "CodFiscal", "Codul fiscal", 32, required=True, upper=True, strip_spaces=True),
        "Adresa": _text(data, "Adresa", "Adresa", 255),
        "Orasul": _text(data, "Orasul", "Localitatea", 255),
        "Judetul": _text(data, "Judetul", "Județul", 8, upper=True),
        "Mail": _text(data, "Mail", "E-mailul", 255),
        "Telefon": _text(data, "Telefon", "Telefonul", 64),
        "SerieFactura": _text(data, "SerieFactura", "Seria facturilor", 10, upper=True, strip_spaces=True),
        "NumarInitial": _int(data.get("NumarInitial", 1), "Primul număr de factură", minimum=1),
        "AfiseazaPrimiteNoi": _flag(data.get("AfiseazaPrimiteNoi"), "Afișează facturile primite noi"),
    }
    if not _CUI.match(values["CodFiscal"]):
        raise _bad("Codul fiscal trebuie să fie cifre, cu «RO» în față dacă unitatea este plătitoare de TVA.")
    with _unit(dc) as conn:
        cursor = conn.cursor(dictionary=True)
        # Slice 00EF-13: once the unit has issued an invoice the series and the first number are fixed (the numbering of
        # the invoices already made depends on them). The window shows them read only; this refuses a client that sends
        # another value.
        if store.have_invoices(cursor):
            old = store.furnizor_get(cursor, dc, for_update=True)
            if old is not None and ((old["SerieFactura"] or None) != values["SerieFactura"]
                                    or old["NumarInitial"] != values["NumarInitial"]):
                raise EfEroare("Unitatea a emis deja facturi: seria și primul număr nu se mai pot schimba.",
                               "SERIE_BLOCATA", 409)
        store.furnizor_save(cursor, dc, values)
        conn.commit()
        return _furnizor_answer(cursor, dc)


def furnizor_anaf(dc):
    """Takes the unit's name, county, city and address from ANAF, by the tax code the unit has in Unitati (slice 00EF-13).
    It can be repeated at will: ANAF's text overwrites what was typed in those four fields (the window asks first). A unit
    with no issuer row yet gets one (tax code from Unitati); the rest is then typed and saved."""
    with _unit(dc) as conn:
        cursor = conn.cursor(dictionary=True)
        unit = store.furnizor_unit(cursor, dc)
        if unit is None:
            raise EfEroare("Unitatea nu există în lista unităților.", "UNITATE_LIPSA", 404)
        cf = anaf.normalize_cf(unit["CF"])
        if not anaf.is_valid_cf(cf):
            raise EfEroare("Codul fiscal al unității nu este valid.", "CF_INVALID", 409)
        try:
            found = anaf.lookup(cf)
        except anaf.AnafNotFound:
            raise EfEroare("ANAF nu cunoaște codul fiscal al unității.", "ANAF_NECUNOSCUT", 404) from None
        except anaf.AnafIncomplete:
            raise EfEroare("ANAF a găsit codul fiscal, dar nu a dat denumirea.", "ANAF_INCOMPLET", 502) from None
        except anaf.AnafUnavailable:
            raise EfEroare("ANAF nu a putut fi contactat acum. Încercați mai târziu.", "ANAF_INDISPONIBIL", 502) from None
        county, city = county_and_city(found)
        store.furnizor_mark_anaf(cursor, dc, found["denumire"][:255], found["adresa"][:255] or None, county, city, cf)
        conn.commit()
        return _furnizor_answer(cursor, dc)


# ---------------------------------------------------------------------------------------------
# the issuer's bank accounts (slice 00EF-12, moved to AVACONT_COMUN.Unitati_Conturi by 00EF-13)
# ---------------------------------------------------------------------------------------------
_IBAN = re.compile(r"^[A-Z]{2}\d{2}[A-Z0-9]{11,30}$")


def _iban_ok(iban):
    """The ISO 13616 check: the first four characters go to the end, letters become 10..35, the number mod 97 is 1."""
    moved = iban[4:] + iban[:4]
    return int("".join(str(int(ch, 36)) for ch in moved)) % 97 == 1


def _clean_iban(value, position):
    """One account as typed: spaces out, upper case, shape and check digits verified. `position` is the row number."""
    if not isinstance(value, str):
        raise _bad(f"Contul {position}: valoare invalidă.")
    iban = "".join(value.split()).upper()
    if not iban:
        raise _bad(f"Contul {position}: scrieți contul sau ștergeți rândul.")
    if not _IBAN.match(iban) or not _iban_ok(iban):
        raise _bad(f"Contul {position} («{iban}») nu este un IBAN valid: 2 litere, 2 cifre de control și restul contului.")
    return iban


def _conturi_answer(rows):
    return {"conturi": [{"IdCont": row["IdCont"], "Cont": row["Cont"], "Banca": row["Banca"] or ""} for row in rows]}


def conturi_get(dc):
    with _unit(dc, SQL_FILES_CONTURI) as conn:
        return _conturi_answer(store.conturi_list(conn.cursor(dictionary=True), dc))


def conturi_set(dc, data):
    """Replaces the list of accounts with `data["conturi"]` (each `{ "Cont": ... }`); the bank of each is deduced from
    its characters 5-8 in AVACONT_COMUN.BIC. An empty list removes every account."""
    items = data.get("conturi")
    if not isinstance(items, list):
        raise _bad("Lista de conturi lipsește.")
    if len(items) > MAX_CONTURI:
        raise _bad(f"Cel mult {MAX_CONTURI} de conturi.")
    seen = set()
    rows = []
    for position, item in enumerate(items, start=1):
        iban = _clean_iban(item.get("Cont") if isinstance(item, dict) else None, position)
        if iban in seen:
            raise _bad(f"Contul {iban} apare de două ori.")
        seen.add(iban)
        rows.append((iban, _bank_for(iban) or None))
    with _unit(dc, SQL_FILES_CONTURI) as conn:
        cursor = conn.cursor(dictionary=True)
        store.conturi_replace(cursor, dc, rows)
        conn.commit()
        return _conturi_answer(store.conturi_list(cursor, dc))


# ---------------------------------------------------------------------------------------------
# customers
# ---------------------------------------------------------------------------------------------
def _parse_client(data):
    values = {
        "DenumireClient": _text(data, "DenumireClient", "Denumirea", 255, required=True),
        "CodFiscal": _text(data, "CodFiscal", "Codul fiscal", 32, strip_spaces=True),
        "IndFiscal": _text(data, "IndFiscal", "Prefixul fiscal", 8, upper=True, strip_spaces=True),
        "Cont": _text(data, "Cont", "Contul (IBAN)", 34, upper=True, strip_spaces=True),
        "Banca": _text(data, "Banca", "Banca", 255),
        "Adresa": _text(data, "Adresa", "Adresa", 255),
        "Judetul": _text(data, "Judetul", "Județul", 8, upper=True, strip_spaces=True),
        "Orasul": _text(data, "Orasul", "Localitatea", 255),
        "Sector": _text(data, "Sector", "Sectorul", 8, upper=True, strip_spaces=True),
        "CNP": _flag(data.get("CNP"), "Persoană fizică"),
    }
    if values["Judetul"] != "B":
        values["Sector"] = None             # the sector exists only for Bucharest
    return values


def clienti_list(dc, q, limit):
    with _unit(dc) as conn:
        return {"clienti": [dict(row) for row in store.clienti_list(conn.cursor(dictionary=True), q, limit)]}


def client_anaf(dc, data):
    """The customer fields ANAF knows for a tax code typed on the «Cumpărător» tab: name, VAT prefix, county, city, address.
    Nothing is written: the operator reviews the fields and saves the customer. `dc` is only the session's unit (the lookup
    itself is not per unit)."""
    raw = data.get("CodFiscal") if isinstance(data, dict) else None
    cf = anaf.normalize_cf(raw)
    if not anaf.is_valid_cf(cf):
        raise _bad("Codul fiscal al clientului nu este valid.", "CF_INVALID")
    try:
        found = anaf.lookup(cf)
    except anaf.AnafNotFound:
        raise EfEroare("ANAF nu cunoaște acest cod fiscal.", "ANAF_NECUNOSCUT", 404) from None
    except anaf.AnafIncomplete:
        raise EfEroare("ANAF a găsit codul fiscal, dar nu a dat denumirea.", "ANAF_INCOMPLET", 502) from None
    except anaf.AnafUnavailable:
        raise EfEroare("ANAF nu a putut fi contactat acum. Încercați mai târziu.", "ANAF_INDISPONIBIL", 502) from None
    county, city = county_and_city(found)
    return {"client": {
        "DenumireClient": found["denumire"][:255],
        "CodFiscal": cf,
        "IndFiscal": "RO" if found.get("platitor_tva") else "",
        "Adresa": found["adresa"][:255],
        "Judetul": county or "",
        "Orasul": city or "",
        "Sector": city if city and city.startswith("SECTOR") else "",
    }}


def client_get(dc, id_client):
    with _unit(dc) as conn:
        row = store.client_get(conn.cursor(dictionary=True), id_client)
    if row is None:
        raise EfEroare("Cumpărătorul nu există.", "NU_EXISTA", 404)
    return dict(row)


def client_create(dc, data):
    values = _parse_client(data)
    with _unit(dc) as conn:
        cursor = conn.cursor(dictionary=True)
        new_id = store.client_insert(cursor, values)
        conn.commit()
        return dict(store.client_get(cursor, new_id))


def client_update(dc, id_client, data):
    values = _parse_client(data)
    with _unit(dc) as conn:
        cursor = conn.cursor(dictionary=True)
        if store.client_get(cursor, id_client) is None:
            raise EfEroare("Cumpărătorul nu există.", "NU_EXISTA", 404)
        store.client_update(cursor, id_client, values)
        conn.commit()
        return dict(store.client_get(cursor, id_client))


def client_delete(dc, id_client):
    with _unit(dc) as conn:
        cursor = conn.cursor(dictionary=True)
        row = store.client_get(cursor, id_client)
        if row is None:
            raise EfEroare("Cumpărătorul nu există.", "NU_EXISTA", 404)
        if store.client_has_invoices(cursor, id_client):
            raise EfEroare("Cumpărătorul are facturi și nu poate fi șters.", "CLIENT_FOLOSIT", 409)
        store.client_delete(cursor, id_client)
        conn.commit()
    return {"sters": True, "IdClient": id_client}


# ---------------------------------------------------------------------------------------------
# lists of the common database
# ---------------------------------------------------------------------------------------------
def um_list(q):
    """The units of measure (UN/ECE codes) of AVACONT_COMUN.EF_UM."""
    rows = _common(lambda cursor: store.um_all(cursor, q), None, "EF_UM")
    if rows is None:
        raise EfEroare("Lista unităților de măsură nu a putut fi citită.", "UM_NECITITA", 503)
    return {"um": [dict(row) for row in rows]}


def _bank_for(iban):
    code = (iban or "").replace(" ", "").upper()[4:8]
    return _common(lambda cursor: store.bank_name(cursor, code), "", "BIC") if len(code) == 4 else ""


def _known_units():
    return _common(store.um_codes, None, "EF_UM")


# ---------------------------------------------------------------------------------------------
# invoices
# ---------------------------------------------------------------------------------------------
def numar_urmator(dc):
    with _unit(dc) as conn:
        cursor = conn.cursor(dictionary=True)
        furnizor = _need_furnizor(cursor, dc)
        series = furnizor["SerieFactura"] or ""
        return {"serie": series, "numar": store.next_number(cursor, series, furnizor["NumarInitial"]),
                "data_minima": _iso(store.max_date(cursor, series))}


def facturi_list(dc, year, q, limit):
    with _unit(dc) as conn:
        rows = store.facturi_list(conn.cursor(dictionary=True), year, q, limit)
    return {"facturi": [{**_present_header(row), "ClientDenumire": row["ClientDenumire"]} for row in rows]}


def factura_get(dc, id_factura):
    with _unit(dc) as conn:
        return _detail(conn.cursor(dictionary=True), id_factura)


def _insert_invoice(cursor, header, lines, series, number, **extra):
    values = {"SerieFactura": series, "NumarFactura": number, "TipFactura": "380", **header, **extra}
    new_id = store.factura_insert(cursor, values)
    store.linii_insert(cursor, new_id, lines)
    return new_id


def factura_create(dc, data):
    header, lines = _parse_invoice(data)
    with _unit(dc) as conn:
        cursor = conn.cursor(dictionary=True)
        furnizor = _need_furnizor(cursor, dc, for_update=True)         # the lock serialises the numbering
        _need_client(cursor, header["IdClient"])
        series = furnizor["SerieFactura"] or ""
        _not_older(header["DataFactura"], store.max_date(cursor, series))
        number = store.next_number(cursor, series, furnizor["NumarInitial"])
        new_id = _insert_invoice(cursor, header, lines, series, number)
        conn.commit()
        return _detail(cursor, new_id)


def factura_update(dc, id_factura, data):
    """A draft is edited in full. An accepted invoice is corrected only by sending it again (trimitere.trimite with a
    `corectie`), which changes the comment and the order reference and writes type 384 once ANAF took the file."""
    with _unit(dc) as conn:
        cursor = conn.cursor(dictionary=True)
        row = _lock(cursor, id_factura)
        state = stare_factura(row)
        if row["IdFacturaA"] is not None:
            raise EfEroare("O factură de stornare nu se modifică.", "STORNO_NEMODIFICABILA", 409)
        if state == DRAFT:
            header, lines = _parse_invoice(data)
            _need_client(cursor, header["IdClient"])
            if header["DataFactura"] != row["DataFactura"]:
                if not store.is_last_of_series(cursor, row["SerieFactura"], row["NumarFactura"]):
                    raise EfEroare("Data se mai poate schimba doar la ultima factură a seriei; la celelalte rămâne cea "
                                   "de la salvare.", "DATA_BLOCATA", 409)
                _not_older(header["DataFactura"], store.max_date(cursor, row["SerieFactura"], row["NumarFactura"]))
            store.factura_update_full(cursor, id_factura, header)
            store.linii_delete(cursor, id_factura)
            store.linii_insert(cursor, id_factura, lines)
        elif state == ACCEPTED:
            raise EfEroare("O factură acceptată de ANAF nu se modifică; se corectează prin retrimitere (comentariul și "
                           "referința comenzii) sau se stornează.", "SE_CORECTEAZA_PRIN_TRIMITERE", 409)
        elif state == UPLOADED:
            raise EfEroare("Factura a fost trimisă la ANAF, dar rezultatul nu este încă cunoscut. "
                           "Verificați mai întâi starea ei.", "INCARCATA_NEVERIFICATA", 409)
        else:
            raise EfEroare("Factura a fost refuzată de ANAF și nu se mai modifică.", "REFUZATA", 409)
        conn.commit()
        return _detail(cursor, id_factura)


def factura_delete(dc, id_factura):
    """Only a draft that carries the LAST number of its series: removing one from the middle would leave a gap."""
    with _unit(dc) as conn:
        cursor = conn.cursor(dictionary=True)
        row = _lock(cursor, id_factura)
        if stare_factura(row) != DRAFT:
            raise EfEroare("O factură trimisă la ANAF nu se șterge.", "TRIMISA", 409)
        if not store.is_last_of_series(cursor, row["SerieFactura"], row["NumarFactura"]):
            raise EfEroare("Se poate șterge doar factura cu ultimul număr al seriei; altfel rămâne un număr lipsă.",
                           "NU_ULTIMA", 409)
        store.factura_delete(cursor, id_factura)
        conn.commit()
    return {"sters": True, "IdFactura": id_factura}


def factura_storno(dc, id_factura, data):
    """Cancels an accepted invoice and writes the replacement `data` describes, in one transaction.
    Answers `{"storno": <detail>, "factura": <detail>}`; the storno takes the next number, the replacement the one after."""
    header, lines = _parse_invoice(data)
    with _unit(dc) as conn:
        cursor = conn.cursor(dictionary=True)
        furnizor = _need_furnizor(cursor, dc, for_update=True)
        original = _lock(cursor, id_factura)
        if stare_factura(original) != ACCEPTED:
            raise EfEroare("Se poate storna doar o factură acceptată de ANAF.", "NU_SE_POATE_STORNA", 409)
        if original["IdFacturaA"] is not None:
            raise EfEroare("O factură de stornare nu se mai stornează.", "NU_SE_POATE_STORNA", 409)
        if store.storno_of(cursor, id_factura) is not None:
            raise EfEroare("Factura a fost deja stornată.", "DEJA_STORNATA", 409)
        _need_client(cursor, header["IdClient"])

        series = furnizor["SerieFactura"] or ""
        _not_older(header["DataFactura"], store.max_date(cursor, series))
        number = store.next_number(cursor, series, furnizor["NumarInitial"])
        storno_header = {
            "IdClient": original["IdClient"], "DataFactura": header["DataFactura"],
            "Comentarii": original["Comentarii"], "BT_13": original["BT_13"], "ContPlata": original["ContPlata"],
            "AtasamentOriginal": original["AtasamentOriginal"],
        }
        storno_id = _insert_invoice(
            cursor, storno_header, store.negate(store.linii_get(cursor, id_factura)), series, number,
            IdFacturaA=id_factura, SerieFacturaA=original["SerieFactura"], NumarFacturaA=str(original["NumarFactura"]))
        new_id = _insert_invoice(cursor, header, lines, series, number + 1)
        conn.commit()
        return {"storno": _detail(cursor, storno_id), "factura": _detail(cursor, new_id)}


# ---------------------------------------------------------------------------------------------
# XML and validation
# ---------------------------------------------------------------------------------------------
def _material(dc, id_factura):
    """Everything the XML and the checks need, read in one transaction."""
    with _unit(dc) as conn:
        cursor = conn.cursor(dictionary=True)
        row = store.factura_get(cursor, id_factura)
        if row is None:
            raise EfEroare("Factura nu există.", "NU_EXISTA", 404)
        return (store.furnizor_get(cursor, dc), store.client_get(cursor, row["IdClient"]), row,
                store.linii_get(cursor, id_factura))


def factura_xml(dc, id_factura):
    """(xml bytes, file name). Refuses what cannot be written at all; whether ANAF will like it is `factura_valideaza`."""
    furnizor, client, row, lines = _material(dc, id_factura)
    if furnizor is None:
        raise EfEroare("Datele unității care emite facturile nu sunt completate.", "FURNIZOR_LIPSA", 409)
    if not lines:
        raise EfEroare("Factura nu are nicio linie.", "LINII_LIPSA", 409)
    xml = ubl.build(furnizor, client, row, lines, _bank_for(row["ContPlata"]))
    return xml, ubl.file_name(row)


def factura_valideaza(dc, id_factura, with_anaf):
    """Our own checks and, when asked and none of them is an error, ANAF's validation service.

    `valida` is true only when there is no error AND (the service was not asked, or it said ok).
    The answer carries `constatari` (level, code, message), `nume_fisier` and, when asked, `anaf`:
    `{"trimis": bool, "ok": bool|None, "mesaje": [...], "indisponibil": bool, "motiv": text|None}`.
    """
    furnizor, client, row, lines = _material(dc, id_factura)
    findings = validare.verifica(furnizor, client, row, lines, _known_units(), _bank_for(row["ContPlata"]))
    local_ok = not validare.has_errors(findings)
    answer = {"IdFactura": id_factura, "nume_fisier": ubl.file_name(row), "constatari": findings,
              "valida_local": local_ok, "anaf": None}
    if with_anaf:
        if not local_ok:
            answer["anaf"] = {"trimis": False, "ok": None, "mesaje": [], "indisponibil": False,
                              "motiv": "Mai întâi trebuie corectate erorile de mai sus."}
        else:
            xml = ubl.build(furnizor, client, row, lines, _bank_for(row["ContPlata"]), schema_location=False)
            try:
                result = validare.anaf_valideaza(xml)
                answer["anaf"] = {"trimis": True, "ok": result["ok"], "mesaje": result["mesaje"],
                                  "indisponibil": False, "motiv": None}
            except validare.ValidareIndisponibila as err:
                answer["anaf"] = {"trimis": False, "ok": None, "mesaje": [], "indisponibil": True, "motiv": str(err)}
    answer["valida"] = local_ok and (answer["anaf"] is None or answer["anaf"]["ok"] is True)
    return answer
