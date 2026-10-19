# routes/efactura/primite_access.py
"""
Exchange of the received e-invoices with the Access e-invoice store `ef_<an>.accdb` (slice 00EF-43).

The PC reads and writes the Access file (KBot.Access, 64-bit ACE); this module is the MariaDB half. It never sees a file.

PRELUARE (Access -> K-BOT), what the Migrator's «E-Factura» tab does, without the operator's choices:
    `preia`        a batch of Access messages, each with its invoices, lines, notes, messages and booked operations (EFT_O).
                   One transaction PER MESSAGE. Nothing already here is overwritten.
    `finalizeaza`  after the last batch: the credit-note references between the invoices that were added, then the automatic
                   link of those invoices to receptions that already exist (primite_access_legare.py).

HOW A ROW IS RECOGNISED (operator's choice, 17.10.2026): by its NATURAL key, never by the Access number, because Access and
K-BOT both create rows and their auto-numbers collide.
    message   EF_Mesaje.IdSol (= Access EF.id_sol, unique)
    invoice   IdSol + NrFact + CuiNormalizat
    note      the invoice + the text;   message line  the invoice + ANAF id + text;   operation  Access EFT_O.IDEFTO (only Access creates it)
A row that is already here is left as it is. Children of an invoice that was already here are added only when missing.
The Access numbers never reach MariaDB: the children are written under the id the invoice has HERE.

TRIMITERE (K-BOT -> Access): `exporta` returns the messages of a year that the file does not have, with everything the Access
tables EF, EFT, EFS, EFT_C and EFT_M hold (not EFT_O). The PC writes them with fresh Access numbers.

SIZE: main.py caps a request at 17 MB; the PC sends batches that stay far below it (the ANAF zip travels as base64).
"""
import base64
import binascii
import logging
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import mysql.connector

from utils.database import COMMON_DB, get_kbot_connection

from . import anaf_api, facturi as F, primite_ubl, token_store
from .primite_access_legare import lega_automat
from .tokens import EfEroare

logger = logging.getLogger(__name__)

SQL_FILES = ("sql/00EF_02_efactura_unitate.sql si sql/00EF_27_primite_linii_cpv.sql si sql/00EF_27_primite_operatii.sql")
MAX_MESAJE = 60
MAX_BLOB = 12 * 1024 * 1024
EXPORT_BUDGET = 4 * 1024 * 1024
MAX_EXPORT = 40
MAX_DEJA = 200000
PROBLEME_AFISATE = 25

_ER_BAD_FIELD, _ER_NO_SUCH_TABLE = 1054, 1146
_FOUR = Decimal("0.0001")


# ---------------------------------------------------------------------------------------------
# small readers (the body is the PC's: every value is checked here)
# ---------------------------------------------------------------------------------------------
def _text(value, limit, required=False, name="valoare"):
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise ValueError(f"«{name}» lipsește.")
        return None
    text = str(value).strip()
    return text[:limit]


def _whole(value, default=None):
    if value is None or isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _amount(value):
    if value is None or isinstance(value, bool):
        return Decimal(0)
    try:
        return Decimal(str(value)).quantize(_FOUR)
    except (InvalidOperation, ValueError):
        raise ValueError(f"suma «{value}» nu este un număr.") from None


def _date(value):
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _datetime(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)[:19])
    except ValueError:
        return None


def _blob(value):
    if not value:
        return None
    try:
        data = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("Arhiva mesajului nu este base64 valid.") from None
    if len(data) > MAX_BLOB:
        raise ValueError("Arhiva mesajului este prea mare.")
    return data or None


def _unit_cursor_tables(cursor, names):
    cursor.execute("SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN (%s)"
                   % ", ".join(["%s"] * len(names)), tuple(names))
    return {row["TABLE_NAME"] for row in cursor.fetchall()}


# ---------------------------------------------------------------------------------------------
# the unit's tax code (the PC filters the Access messages by it)
# ---------------------------------------------------------------------------------------------
def stare(dc):
    """`{ cui }`: the unit's tax code, digits only, as K-BOT knows it (`Unitati.CF`)."""
    conn = get_kbot_connection(COMMON_DB)
    try:
        cui = token_store.unit_cui(conn.cursor(), dc)
    finally:
        if conn.is_connected():
            conn.close()
    if not cui:
        raise EfEroare("Unitatea nu are un cod fiscal valid în evidența K-BOT.", "CUI_LIPSA", 409)
    return {"cui": cui}


# ---------------------------------------------------------------------------------------------
# PRELUARE
# ---------------------------------------------------------------------------------------------
def _cpv_pe_linie(xml_bytes):
    """{NrLinie: cpv} from the invoice XML inside an ANAF zip (or a bare XML). None = the invoice could not be read (CPV stays «not read»).
    A line number that repeats inside the invoice cannot be told apart: it is left out (NULL)."""
    if not xml_bytes:
        return None
    try:
        raw = xml_bytes if xml_bytes[:5] == b"<?xml" or xml_bytes[:1] == b"<" else anaf_api.factura_din_zip(xml_bytes)
        parsed = primite_ubl.parse(raw)
    except (EfEroare, primite_ubl.XmlNeinteles):
        return None
    seen, repeated, codes = set(), set(), {}
    for line in parsed["linii"]:
        number = line["NrLinie"]
        if number in seen:
            repeated.add(number)
        seen.add(number)
        codes[number] = line["Cpv"]
    return {number: code for number, code in codes.items() if number not in repeated}


def _clasificatii(cursor):
    """{(IdClsfAcc, IdUnitate): [IDClsf, ...]} of the unit database (the rule of slice 0080-01)."""
    cursor.execute("SELECT IDClsf, IdClsfAcc, IdUnitate FROM Clasificatii WHERE IdUnitate IS NOT NULL")
    pairs = {}
    for row in cursor.fetchall():
        pairs.setdefault((row["IdClsfAcc"], row["IdUnitate"]), []).append(row["IDClsf"])
    return pairs


def _rezultat():
    return {"mesaje_noi": 0, "mesaje_sarite": 0, "facturi_noi": 0, "facturi_sarite": 0, "linii": 0, "note": 0,
            "mesaje_factura": 0, "operatii": 0, "operatii_sarite": 0, "id_primite": [], "probleme": [], "erori": []}


def _problema(rezultat, text):
    if len(rezultat["probleme"]) < PROBLEME_AFISATE:
        rezultat["probleme"].append(text)


def preia(dc, mesaje):
    """Writes a batch of Access messages. Answers the counts, `id_primite` (the invoices this batch ADDED), `probleme`
    (operations without a classification, ...) and `erori` (a message that could not be written: [{ id_sol, motiv }])."""
    if not isinstance(mesaje, list) or not 1 <= len(mesaje) <= MAX_MESAJE:
        raise EfEroare(f"Lotul trebuie să aibă între 1 și {MAX_MESAJE} mesaje.", "CAMP_INVALID", 400)
    rezultat = _rezultat()
    with F._unit(dc, SQL_FILES) as conn:
        cursor = conn.cursor(dictionary=True)
        tables = _unit_cursor_tables(cursor, ("EF_PrimiteOperatii", "Clasificatii"))
        context = {"cu_operatii": "EF_PrimiteOperatii" in tables, "clasificatii": None, "cursor": cursor}
        if not context["cu_operatii"]:
            _problema(rezultat, "Tabelul EF_PrimiteOperatii lipsește: operațiunile contabile din Access nu se preiau.")
        for mesaj in mesaje:
            id_sol = str(mesaj.get("IdSol") or "?") if isinstance(mesaj, dict) else "?"
            try:
                if not isinstance(mesaj, dict):
                    raise ValueError("mesaj invalid.")
                _preia_mesaj(context, mesaj, rezultat)
                conn.commit()
            except mysql.connector.Error as err:
                conn.rollback()
                if getattr(err, "errno", None) in (_ER_BAD_FIELD, _ER_NO_SUCH_TABLE):
                    raise
                logger.error("[efactura] access preia %s: database write failed", id_sol, exc_info=True)
                rezultat["erori"].append({"id_sol": id_sol, "motiv": "Scrierea în baza de date a eșuat."})
            except (ValueError, KeyError, TypeError) as err:
                conn.rollback()
                logger.error("[efactura] access preia %s: %s", id_sol, err)
                rezultat["erori"].append({"id_sol": id_sol, "motiv": str(err)})
    return rezultat


def _preia_mesaj(context, mesaj, rezultat):
    cursor = context["cursor"]
    id_sol = _text(mesaj.get("IdSol"), 32, True, "id_sol")
    xml = _blob(mesaj.get("Xml"))
    cursor.execute("SELECT IdMesaj FROM EF_Mesaje WHERE IdSol = %s", (id_sol,))
    if cursor.fetchone() is None:
        cursor.execute(
            "INSERT INTO EF_Mesaje (IdSol, IdIncarcare, CifEmitent, DataMesaj, CuiUnitate, Nou, NumeFisier, XmlContinut, DataAdaugare) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, COALESCE(%s, NOW()))",
            (id_sol, _text(mesaj.get("IdIncarcare"), 32), _text(mesaj.get("CifEmitent"), 32), _date(mesaj.get("DataMesaj")),
             _text(mesaj.get("CuiUnitate"), 32), 1 if mesaj.get("Nou") else 0, _text(mesaj.get("NumeFisier"), 255), xml,
             _datetime(mesaj.get("DataAdaugare"))))
        rezultat["mesaje_noi"] += 1
    else:
        rezultat["mesaje_sarite"] += 1
    cpv = None
    for factura in mesaj.get("facturi") or []:
        if cpv is None:
            cpv = _cpv_pe_linie(xml) or {}
        _preia_factura(context, id_sol, factura, cpv, xml is not None, rezultat)


def _gaseste_factura(cursor, id_sol, nr_fact, cui_n):
    cursor.execute("SELECT IdPrimita, CuiNormalizat FROM EF_Primite WHERE IdSol = %s AND NrFact <=> %s ORDER BY IdPrimita", (id_sol, nr_fact))
    for row in cursor.fetchall():
        if not cui_n or not row["CuiNormalizat"] or row["CuiNormalizat"] == cui_n:
            return row["IdPrimita"]
    return None


def _preia_factura(context, id_sol, f, cpv, are_xml, rezultat):
    cursor = context["cursor"]
    nr_fact = _text(f.get("NrFact"), 64)
    cui = _text(f.get("CUI"), 32)
    cui_n = primite_ubl.normalize_cui(cui) or None
    id_primita = _gaseste_factura(cursor, id_sol, nr_fact, cui_n)
    nou = id_primita is None
    if nou:
        cursor.execute(
            "INSERT INTO EF_Primite (IdSol, NrFact, DataFact, DataScad, CotaTVA, TVA, Valoare, Total, CUI, CuiNormalizat, DenumireP, "
            "Adresa, Atasament, Tip, Semn, Ref, DataAdaugare) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
            "COALESCE(%s, NOW()))",
            (id_sol, nr_fact, _date(f.get("DataFact")), _date(f.get("DataScad")), _amount(f.get("CotaTVA")), _amount(f.get("TVA")),
             _amount(f.get("Valoare")), _amount(f.get("Total")), cui, cui_n, _text(f.get("DenumireP"), 255), _text(f.get("Adresa"), 500),
             _text(f.get("Atasament"), 255), _text(f.get("Tip"), 16) or "FC", -1 if _whole(f.get("Semn"), 1) < 0 else 1,
             _text(f.get("Ref"), 255), _datetime(f.get("DataAdaugare"))))
        id_primita = cursor.lastrowid
        rezultat["facturi_noi"] += 1
        rezultat["id_primite"].append(id_primita)
        linii = [_linie(id_primita, linie, cpv, are_xml) for linie in f.get("linii") or []]
        if linii:
            cursor.executemany(
                "INSERT INTO EF_PrimiteLinii (IdPrimita, NrLinie, Denumire, Explicatie, Cpv, Unit, Cant, Pret, Valoare) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)", linii)
            rezultat["linii"] += len(linii)
    else:
        rezultat["facturi_sarite"] += 1
    _note(cursor, id_primita, f.get("note") or [], rezultat)
    _mesaje_factura(cursor, id_primita, f.get("mesaje") or [], rezultat)
    if context["cu_operatii"]:
        _operatii(context, id_primita, f.get("operatii") or [], rezultat)


def _linie(id_primita, linie, cpv, are_xml):
    number = _text(linie.get("NrLinie"), 16)
    code = cpv.get(number) if are_xml and number is not None else None
    return (id_primita, number, _text(linie.get("Denumire"), 500), _text(linie.get("Explicatie"), 65535), code,
            _text(linie.get("Unit"), 16), _amount(linie.get("Cant")), _amount(linie.get("Pret")), _amount(linie.get("Valoare")))


def _note(cursor, id_primita, note, rezultat):
    if not note:
        return
    cursor.execute("SELECT Nota FROM EF_PrimiteNote WHERE IdPrimita = %s", (id_primita,))
    have = {row["Nota"] for row in cursor.fetchall()}
    for nota in note:
        text = _text(nota.get("Nota"), 65535)
        if text is None or text in have:
            continue
        have.add(text)
        cursor.execute("INSERT INTO EF_PrimiteNote (IdPrimita, Nota, DataAdaugare) VALUES (%s, %s, COALESCE(%s, NOW()))",
                       (id_primita, text, _datetime(nota.get("DataAdaugare"))))
        rezultat["note"] += 1


def _mesaje_factura(cursor, id_primita, mesaje, rezultat):
    if not mesaje:
        return
    cursor.execute("SELECT IdMesajAnaf, Mesaj FROM EF_PrimiteMesaje WHERE IdPrimita = %s", (id_primita,))
    have = {(row["IdMesajAnaf"], row["Mesaj"]) for row in cursor.fetchall()}
    for item in mesaje:
        key = (_text(item.get("IdMesajAnaf"), 32), _text(item.get("Mesaj"), 65535))
        if key[1] is None or key in have:
            continue
        have.add(key)
        cursor.execute("INSERT INTO EF_PrimiteMesaje (IdPrimita, IdSol, IdMesajAnaf, Mesaj, DataMesaj, DataAdaugare) "
                       "VALUES (%s, %s, %s, %s, %s, COALESCE(%s, NOW()))",
                       (id_primita, _text(item.get("IdSol"), 32), key[0], key[1], _datetime(item.get("DataMesaj")),
                        _datetime(item.get("DataAdaugare"))))
        rezultat["mesaje_factura"] += 1


def _operatii(context, id_primita, operatii, rezultat):
    """EFT_O: the booked operations (what tells the operator the invoice was processed). The classification is resolved on the row's
    own unit through (IdClsfAcc, IdUnitate); a pair that does not resolve to exactly one row is reported and the operation is left for
    a later preluare (nothing is invented)."""
    cursor = context["cursor"]
    for op in operatii:
        id_op = _whole(op.get("IdOperatiePrimita"))
        if id_op is None:
            continue
        cursor.execute("SELECT 1 FROM EF_PrimiteOperatii WHERE IdOperatiePrimita = %s", (id_op,))
        if cursor.fetchone() is not None:
            rezultat["operatii_sarite"] += 1
            continue
        acc, unit = _whole(op.get("IdClsfAcc"), 0), _whole(op.get("IdUnitate"), 0)
        id_clsf = None
        if acc:
            if context["clasificatii"] is None:
                context["clasificatii"] = _clasificatii(cursor)
            found = context["clasificatii"].get((acc, unit), [])
            if len(found) != 1:
                _problema(rezultat, f"operațiunea {id_op}: clasificația Access {acc}, unitatea {unit} are {len(found)} potriviri în "
                                    "Clasificații (trebuie exact una); nu s-a preluat.")
                continue
            id_clsf = found[0]
        cursor.execute(
            "INSERT INTO EF_PrimiteOperatii (IdOperatiePrimita, IdPrimita, IdOperatie, IdClsf, IdUnitate, Sursa, Suma, DataAdaugare) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, COALESCE(%s, NOW()))",
            (id_op, id_primita, _whole(op.get("IdOperatie"), 0), id_clsf, unit or None, _text(op.get("Sursa"), 255),
             _amount(op.get("Suma")), _datetime(op.get("DataAdaugare"))))
        rezultat["operatii"] += 1


def finalizeaza(dc, id_primite, referinte):
    """After the last batch. `id_primite` = the invoices the batches added; `referinte` = [{ IdSol, NrFact, RefIdSol, RefNrFact }] of the credit
    notes (Access EFT.IDEFT_REF, told by natural key). Sets the reference of the added invoices and links them to existing receptions."""
    if not isinstance(id_primite, list) or len(id_primite) > 200000 or not all(isinstance(i, int) and not isinstance(i, bool) for i in id_primite):
        raise EfEroare("Lista facturilor adăugate nu este validă.", "CAMP_INVALID", 400)
    if not isinstance(referinte, list) or len(referinte) > 200000:
        raise EfEroare("Lista trimiterilor între facturi nu este validă.", "CAMP_INVALID", 400)
    added = set(id_primite)
    setate = 0
    with F._unit(dc, SQL_FILES) as conn:
        cursor = conn.cursor(dictionary=True)
        for ref in referinte:
            if not isinstance(ref, dict):
                continue
            own = _gaseste_factura(cursor, _text(ref.get("IdSol"), 32), _text(ref.get("NrFact"), 64), None)
            target = _gaseste_factura(cursor, _text(ref.get("RefIdSol"), 32), _text(ref.get("RefNrFact"), 64), None)
            if own in added and target is not None and target != own:
                cursor.execute("UPDATE EF_Primite SET IdPrimitaRef = %s WHERE IdPrimita = %s AND IdPrimitaRef IS NULL", (target, own))
                setate += cursor.rowcount
        legare = lega_automat(cursor, sorted(added))
        conn.commit()
    return {"referinte_setate": setate, "legare": legare}


# ---------------------------------------------------------------------------------------------
# TRIMITERE
# ---------------------------------------------------------------------------------------------
def _plain(row):
    return {key: (value.isoformat() if hasattr(value, "isoformat") else float(value) if value.__class__.__name__ == "Decimal" else value)
            for key, value in row.items() if not isinstance(value, (bytes, bytearray))}


def exporta(dc, an, deja, limita):
    """The messages of year `an` (by their message date) whose `IdSol` is not in `deja`, up to `limita` and about 4 MB of archives, each with
    its invoices, lines, notes and ANAF messages. Answers `{ mesaje, ramase }`; the PC repeats the call, adding what it wrote to `deja`."""
    if not isinstance(deja, list) or len(deja) > MAX_DEJA:
        raise EfEroare("Lista mesajelor existente în Access nu este validă.", "CAMP_INVALID", 400)
    if isinstance(an, bool) or not isinstance(an, int) or not 1990 <= an <= 2100:
        raise EfEroare("Anul trebuie să fie între 1990 și 2100.", "CAMP_INVALID", 400)
    if isinstance(limita, bool) or not isinstance(limita, int) or not 1 <= limita <= MAX_EXPORT:
        raise EfEroare(f"Lotul trebuie să fie între 1 și {MAX_EXPORT}.", "CAMP_INVALID", 400)
    have = {str(x).strip() for x in deja}
    with F._unit(dc, SQL_FILES) as conn:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT IdSol FROM EF_Mesaje WHERE YEAR(DataMesaj) = %s ORDER BY DataMesaj, IdMesaj", (an,))
        missing = [row["IdSol"] for row in cursor.fetchall() if row["IdSol"] not in have]
        out, size = [], 0
        for id_sol in missing:
            if len(out) >= limita or (out and size >= EXPORT_BUDGET):
                break
            item, blob_size = _mesaj_export(cursor, id_sol)
            if out and size + blob_size > EXPORT_BUDGET * 2:
                break
            out.append(item)
            size += blob_size
        return {"mesaje": out, "ramase": len(missing) - len(out), "total_lipsa": len(missing)}


def _mesaj_export(cursor, id_sol):
    cursor.execute("SELECT IdSol, IdIncarcare, CifEmitent, DataMesaj, CuiUnitate, Nou, NumeFisier, XmlContinut, ZipContinut, DataAdaugare "
                   "FROM EF_Mesaje WHERE IdSol = %s", (id_sol,))
    row = cursor.fetchone()
    # Access keeps the ANAF zip in its XML column; the zip is what K-BOT keeps in ZipContinut (a migrated row has it in XmlContinut)
    blob = bytes(row["ZipContinut"]) if row["ZipContinut"] else (bytes(row["XmlContinut"]) if row["XmlContinut"] else b"")
    item = _plain(row)
    item["Xml"] = base64.b64encode(blob).decode("ascii") if blob else None
    item["facturi"] = []
    cursor.execute("SELECT IdPrimita, NrFact, DataFact, DataScad, CotaTVA, TVA, Valoare, Total, CUI, DenumireP, Adresa, Atasament, Tip, Semn, "
                   "Ref, IdPrimitaRef, DataAdaugare FROM EF_Primite WHERE IdSol = %s ORDER BY IdPrimita", (id_sol,))
    for inv in cursor.fetchall():
        entry = _plain(inv)
        cursor.execute("SELECT NrLinie, Denumire, Explicatie, Unit, Cant, Pret, Valoare FROM EF_PrimiteLinii WHERE IdPrimita = %s "
                       "ORDER BY IdLinie", (inv["IdPrimita"],))
        entry["linii"] = [_plain(r) for r in cursor.fetchall()]
        cursor.execute("SELECT Nota, DataAdaugare FROM EF_PrimiteNote WHERE IdPrimita = %s ORDER BY IdNota", (inv["IdPrimita"],))
        entry["note"] = [_plain(r) for r in cursor.fetchall()]
        cursor.execute("SELECT IdSol, IdMesajAnaf, Mesaj, DataMesaj, DataAdaugare FROM EF_PrimiteMesaje WHERE IdPrimita = %s ORDER BY IdMsg",
                       (inv["IdPrimita"],))
        entry["mesaje"] = [_plain(r) for r in cursor.fetchall()]
        if inv["IdPrimitaRef"]:
            cursor.execute("SELECT IdSol, NrFact FROM EF_Primite WHERE IdPrimita = %s", (inv["IdPrimitaRef"],))
            ref = cursor.fetchone()
            if ref:
                entry["RefIdSol"], entry["RefNrFact"] = ref["IdSol"], ref["NrFact"]
        item["facturi"].append(entry)
    return item, len(blob)
