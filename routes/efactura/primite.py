# routes/efactura/primite.py
"""
Received e-invoices (slice 00EF-17): synchronise from ANAF, list, detail, files, link to a DDF.

SYNC (`sincronizeaza`): the ANAF messages of kind «FACTURA PRIMITA» of the last N days (N <= 60) that are not yet in `EF_Mesaje`
are downloaded one by one (the zip is kept, the invoice XML inside it is read), read by primite_ubl.py and written in ONE transaction per message: EF_Mesaje
(with the ANAF zip: XML + signature, no separate XML), EF_Primite, EF_PrimiteLinii, EF_PrimiteNote. `EF_Mesaje.IdSol` is unique, so a repeated or interrupted sync never
doubles anything. A call handles at most `limita` new messages and answers how many are left (`ramase`), so the screen can
loop with a progress bar. A message that cannot be read is reported in `erori` and skipped (it stays out of EF_Mesaje, so the next sync tries
again); the others go on.

Supplier name: it comes from the XML (`RegistrationName`, mandatory in CIUS-RO). ANAF's company service is not called.

LINK TO A DDF: automatic = the supplier's normalised CUI equals a partner of the DDF (`FX_DDF_Parteneri.CodFiscal`, same
normalisation); manual = a row in `EF_PrimiteAsocieri`. A list for a DDF returns both and says which (`legatura`: «auto» / «manual»).
"""
import io
import logging
import re
import zipfile
from datetime import datetime

from . import anaf_api, facturi as F, primite_ubl, tokens, trimitere
from .tokens import EfEroare

logger = logging.getLogger(__name__)

SQL_FILES = ("sql/00EF_02_efactura_unitate.sql si sql/00EF_17_primite_asocieri.sql si sql/00EF_17_primite_tva.sql "
             "si sql/00EF_23_mesaje_zip.sql")
DEFAULT_BATCH = 20
MAX_BATCH = 50
MAX_LIST = 2000
_PARTNER_CUI = "UPPER(REGEXP_REPLACE(REGEXP_REPLACE(p.CodFiscal, '[^[:alnum:]]', ''), '^RO([0-9])', '\\\\1'))"


def _message_date(text):
    """`data_creare` of an ANAF message is «yyyymmddhhmm»; anything else gives None."""
    match = re.match(r"^(\d{4})(\d{2})(\d{2})", text or "")
    if not match:
        return None
    try:
        return datetime(int(match[1]), int(match[2]), int(match[3])).date()
    except ValueError:
        return None


def _digits(text):
    return "".join(ch for ch in str(text or "") if ch.isdigit())[:32] or None


# ---------------------------------------------------------------------------------------------
# sync
# ---------------------------------------------------------------------------------------------
def _store_message(cursor, message, cui, zip_bytes, parsed):
    """One message and its invoice; the ANAF zip is kept (XML + ANAF signature), the XML alone is not. Raises on any database error (the caller rolls back this message only)."""
    cursor.execute(
        "INSERT INTO EF_Mesaje (IdSol, IdIncarcare, CifEmitent, DataMesaj, CuiUnitate, Nou, NumeFisier, ZipContinut) "
        "VALUES (%s, %s, %s, %s, %s, 1, %s, %s)",
        (message["id_solicitare"], message.get("id") or None,
         _digits(message.get("cif_emitent")) or parsed["CuiNormalizat"], _message_date(message.get("data_creare")), cui,
         (parsed["NrFact"] or "")[:255] + ".xml", zip_bytes))
    cursor.execute(
        "INSERT INTO EF_Primite (IdSol, NrFact, DataFact, DataScad, CotaTVA, TVA, Valoare, Total, CUI, CuiNormalizat, DenumireP, "
        "Adresa, Atasament, Tip, Semn, Ref) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (message["id_solicitare"], parsed["NrFact"], parsed["DataFact"], parsed["DataScad"], parsed["CotaTVA"], parsed["TVA"],
         parsed["Valoare"], parsed["Total"], parsed["CUI"], parsed["CuiNormalizat"], parsed["DenumireP"], parsed["Adresa"],
         parsed["Atasament"], parsed["Tip"], parsed["Semn"], parsed["Ref"]))
    new_id = cursor.lastrowid
    if parsed["linii"]:
        cursor.executemany(
            "INSERT INTO EF_PrimiteLinii (IdPrimita, NrLinie, Denumire, Explicatie, Unit, Cant, Pret, Valoare) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            [(new_id, ln["NrLinie"], ln["Denumire"], ln["Explicatie"], ln["Unit"], ln["Cant"], ln["Pret"], ln["Valoare"])
             for ln in parsed["linii"]])
    if parsed["cote"]:
        cursor.executemany("INSERT INTO EF_PrimiteTVA (IdPrimita, Categorie, CotaTVA, Baza, TVA) VALUES (%s, %s, %s, %s, %s)",
                           [(new_id, r["Categorie"], r["CotaTVA"], r["Baza"], r["TVA"]) for r in parsed["cote"]])
    if parsed["note"]:
        cursor.executemany("INSERT INTO EF_PrimiteNote (IdPrimita, Nota) VALUES (%s, %s)", [(new_id, n) for n in parsed["note"]])
    # a credit note points at an invoice of the same supplier that may already be here
    if parsed["Tip"] == "NC" and parsed["Ref"] and parsed["CuiNormalizat"]:
        cursor.execute("SELECT IdPrimita FROM EF_Primite WHERE CuiNormalizat = %s AND NrFact = %s AND IdPrimita <> %s LIMIT 1",
                       (parsed["CuiNormalizat"], parsed["Ref"].split(" / ")[0], new_id))
        target = cursor.fetchone()
        if target:
            cursor.execute("UPDATE EF_Primite SET IdPrimitaRef = %s WHERE IdPrimita = %s", (target["IdPrimita"], new_id))
    return new_id


def sincronizeaza(dc, days, batch=DEFAULT_BATCH):
    """Downloads the new received messages (at most `batch`). Answers
    `{ cui, zile, gasite, adaugate, sarite, ramase, erori: [{id_solicitare, motiv}] }`."""
    if not 1 <= batch <= MAX_BATCH:
        raise EfEroare(f"Lotul trebuie sa fie intre 1 si {MAX_BATCH}.", "CAMP_INVALID", 400)
    listed = trimitere.mesaje(dc, days, True)
    cui = listed["cui"]
    pending = [m for m in listed["mesaje"] if not m.get("deja_in_baza") and anaf_api.is_id(m.get("id_solicitare", ""))
               and anaf_api.is_id(m.get("id", ""))]
    todo, left = pending[:batch], max(0, len(pending) - batch)
    _, token = tokens.access_token(dc)

    added, errors = 0, []
    with F._unit(dc, SQL_FILES) as conn:
        cursor = conn.cursor(dictionary=True)
        for message in todo:
            try:
                zip_bytes = anaf_api.descarca(token, message["id"])
                parsed = primite_ubl.parse(anaf_api.factura_din_zip(zip_bytes))
                _store_message(cursor, message, cui, zip_bytes, parsed)
                conn.commit()
                added += 1
            except (EfEroare, primite_ubl.XmlNeinteles) as err:
                conn.rollback()
                logger.error("[efactura] received message %s skipped: %s", message["id_solicitare"], err)
                errors.append({"id_solicitare": message["id_solicitare"], "motiv": str(err)})
            except Exception:
                conn.rollback()
                logger.error("[efactura] received message %s: database write failed", message["id_solicitare"], exc_info=True)
                errors.append({"id_solicitare": message["id_solicitare"], "motiv": "Scrierea in baza de date a esuat."})
    return {"cui": cui, "zile": days, "gasite": len(listed["mesaje"]), "adaugate": added,
            "sarite": len(listed["mesaje"]) - len(pending), "ramase": left, "erori": errors}


# ---------------------------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------------------------
_LIST_COLUMNS = ("p.IdPrimita, p.IdSol, p.NrFact, p.DataFact, p.DataScad, p.CotaTVA, p.TVA, p.Valoare, p.Total, p.CUI, "
                 "p.CuiNormalizat, p.DenumireP, p.Atasament, p.Tip, p.Semn, p.Ref, p.IdPrimitaRef, m.Nou")


def _plain(row):
    """JSON-ready: dates as ISO text, decimals as numbers (like the issued-invoice routes)."""
    return {key: (value.isoformat() if hasattr(value, "isoformat") else float(value) if value.__class__.__name__ == "Decimal" else value)
            for key, value in row.items()}


def lista(dc, year=None, month=None, q=None, iddf=None, cui=None):
    """The received invoices, newest first. `iddf` = those linked to that DDF (automatic or manual, with `legatura`);
    `cui` = one supplier; `q` = text in number / supplier name / CUI."""
    where, params, link = ["1 = 1"], [], ""
    select = _LIST_COLUMNS
    if iddf is not None:
        select += ", CASE WHEN a.IdAsociere IS NOT NULL THEN 'manual' ELSE 'auto' END AS legatura"
        link = " LEFT JOIN EF_PrimiteAsocieri a ON a.IdPrimita = p.IdPrimita AND a.IDDF = %s"
        params.append(iddf)
        where.append("(a.IdAsociere IS NOT NULL OR EXISTS (SELECT 1 FROM FX_DDF_Parteneri dp WHERE dp.IDDF = %s AND "
                     + _PARTNER_CUI.replace("p.CodFiscal", "dp.CodFiscal") + " = p.CuiNormalizat))")
        params.append(iddf)
    if year is not None:
        where.append("YEAR(p.DataFact) = %s")
        params.append(year)
    if month is not None:
        where.append("MONTH(p.DataFact) = %s")
        params.append(month)
    if cui:
        where.append("p.CuiNormalizat = %s")
        params.append(primite_ubl.normalize_cui(cui))
    if q:
        like = "%" + re.sub(r"([\\%_])", r"\\\1", q) + "%"
        where.append("(p.NrFact LIKE %s OR p.DenumireP LIKE %s OR p.CUI LIKE %s)")
        params += [like, like, like]
    sql = (f"SELECT {select} FROM EF_Primite p JOIN EF_Mesaje m ON m.IdSol = p.IdSol{link} WHERE " + " AND ".join(where)
           + f" ORDER BY p.DataFact DESC, p.IdPrimita DESC LIMIT {MAX_LIST}")
    with F._unit(dc, SQL_FILES) as conn:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(sql, tuple(params))
        return {"facturi": [_plain(row) for row in cursor.fetchall()]}


def _row(cursor, id_primita):
    cursor.execute("SELECT p.*, m.Nou, m.DataMesaj, m.IdIncarcare FROM EF_Primite p JOIN EF_Mesaje m ON m.IdSol = p.IdSol "
                   "WHERE p.IdPrimita = %s", (id_primita,))
    row = cursor.fetchone()
    if row is None:
        raise EfEroare("Factura primita nu exista.", "NU_EXISTA", 404)
    return row


def detaliu(dc, id_primita):
    """Header, lines, notes, ANAF-side messages, embedded files and the DDFs it is linked to."""
    with F._unit(dc, SQL_FILES) as conn:
        cursor = conn.cursor(dictionary=True)
        row = _row(cursor, id_primita)
        cursor.execute("SELECT NrLinie, Denumire, Explicatie, Unit, Cant, Pret, Valoare FROM EF_PrimiteLinii "
                       "WHERE IdPrimita = %s ORDER BY IdLinie", (id_primita,))
        lines = [_plain(r) for r in cursor.fetchall()]
        cursor.execute("SELECT Categorie, CotaTVA, Baza, TVA FROM EF_PrimiteTVA WHERE IdPrimita = %s ORDER BY CotaTVA DESC",
                       (id_primita,))
        rates = [_plain(r) for r in cursor.fetchall()]
        cursor.execute("SELECT Nota FROM EF_PrimiteNote WHERE IdPrimita = %s ORDER BY IdNota", (id_primita,))
        notes = [r["Nota"] for r in cursor.fetchall()]
        cursor.execute("SELECT IdMsg, IdMesajAnaf, Mesaj, DataMesaj FROM EF_PrimiteMesaje WHERE IdPrimita = %s "
                       "ORDER BY DataMesaj, IdMsg", (id_primita,))
        messages = [_plain(r) for r in cursor.fetchall()]
        cursor.execute("SELECT a.IDDF, d.CodAngajament FROM EF_PrimiteAsocieri a JOIN FX_DDF d ON d.IDDF = a.IDDF "
                       "WHERE a.IdPrimita = %s", (id_primita,))
        manual = [_plain(r) for r in cursor.fetchall()]
        cursor.execute("SELECT d.IDDF, d.CodAngajament FROM FX_DDF_Parteneri p JOIN FX_DDF d ON d.IDDF = p.IDDF WHERE " + _PARTNER_CUI
                       + " = %s", (row["CuiNormalizat"] or "-",))
        auto = [_plain(r) for r in cursor.fetchall()]
        xml = _xml_of(cursor, row["IdSol"])
    files = primite_ubl.parse(xml)["atasamente"] if xml else []
    row.pop("DataAdaugare", None)
    return {"factura": _plain(row), "linii": lines, "cote": rates, "note": notes, "mesaje": messages, "atasamente": files,
            "legaturi": {"auto": auto, "manual": manual}}


def _xml_of(cursor, id_sol):
    """The invoice XML of a message: the stored XML, else the invoice entry of the stored ANAF zip.
    A migrated message keeps the zip only (the signature file sits next to the XML in it), a new one keeps the XML."""
    cursor.execute("SELECT XmlContinut, ZipContinut FROM EF_Mesaje WHERE IdSol = %s", (id_sol,))
    row = cursor.fetchone()
    if row["XmlContinut"]:
        return bytes(row["XmlContinut"])
    if not row["ZipContinut"]:
        return None
    try:
        with zipfile.ZipFile(io.BytesIO(bytes(row["ZipContinut"]))) as archive:
            for name in archive.namelist():
                base = name.rsplit("/", 1)[-1].lower()
                if base.endswith(".xml") and not base.startswith("semnatura_"):
                    return archive.read(name)
    except zipfile.BadZipFile:
        logger.exception("EF_Mesaje.ZipContinut deteriorat pentru IdSol=%s", id_sol)
    return None


def xml(dc, id_primita):
    """(xml bytes, file name) kept from ANAF."""
    with F._unit(dc, SQL_FILES) as conn:
        cursor = conn.cursor(dictionary=True)
        row = _row(cursor, id_primita)
        data = _xml_of(cursor, row["IdSol"])
    if not data:
        raise EfEroare("XML-ul facturii nu este salvat.", "NU_EXISTA", 404)
    return data, _file_name(row) + ".xml"


def zip_anaf(dc, id_primita):
    """(zip bytes, file name): the signed zip kept in the database (migrated messages), else downloaded again from ANAF."""
    with F._unit(dc, SQL_FILES) as conn:
        cursor = conn.cursor(dictionary=True)
        row = _row(cursor, id_primita)
        cursor.execute("SELECT ZipContinut FROM EF_Mesaje WHERE IdSol = %s", (row["IdSol"],))
        kept = cursor.fetchone()["ZipContinut"]
    if kept:
        return bytes(kept), _file_name(row) + ".zip"
    _, token = tokens.access_token(dc)
    if not anaf_api.is_id(row["IdIncarcare"] or ""):
        raise EfEroare("Mesajul nu are numarul de descarcare ANAF.", "NU_EXISTA", 404)
    return anaf_api.descarca(token, row["IdIncarcare"]), _file_name(row) + ".zip"


def pdf_anaf(dc, id_primita):
    """(pdf bytes, file name): the invoice as ANAF's public service draws it from the saved XML."""
    data, name = xml(dc, id_primita)
    return anaf_api.pdf_din_xml(data), name[:-4] + ".pdf"


def atasament(dc, id_primita, index):
    """(bytes, file name, mime) of one embedded file."""
    data, _ = xml(dc, id_primita)
    found = primite_ubl.atasament_bytes(data, index)
    if found is None:
        raise EfEroare("Atasamentul nu exista sau nu poate fi citit.", "NU_EXISTA", 404)
    name, mime, content = found
    return content, name, mime


def _file_name(row):
    base = "_".join(part for part in (row["CuiNormalizat"], row["NrFact"] or row["IdSol"]) if part)
    return re.sub(r"[^A-Za-z0-9._-]", "_", base)[:100] or str(row["IdPrimita"])


def marcheaza_citita(dc, id_primita):
    with F._unit(dc, SQL_FILES) as conn:
        cursor = conn.cursor(dictionary=True)
        row = _row(cursor, id_primita)
        cursor.execute("UPDATE EF_Mesaje SET Nou = 0 WHERE IdSol = %s", (row["IdSol"],))
        conn.commit()


# ---------------------------------------------------------------------------------------------
# manual link
# ---------------------------------------------------------------------------------------------
def asociaza(dc, id_primita, iddf, user):
    with F._unit(dc, SQL_FILES) as conn:
        cursor = conn.cursor(dictionary=True)
        _row(cursor, id_primita)
        cursor.execute("SELECT 1 FROM FX_DDF WHERE IDDF = %s", (iddf,))
        if cursor.fetchone() is None:
            raise EfEroare("DDF-ul nu exista.", "NU_EXISTA", 404)
        cursor.execute("INSERT IGNORE INTO EF_PrimiteAsocieri (IdPrimita, IDDF, UN) VALUES (%s, %s, %s)", (id_primita, iddf, user))
        conn.commit()


def desasociaza(dc, id_primita, iddf):
    with F._unit(dc, SQL_FILES) as conn:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("DELETE FROM EF_PrimiteAsocieri WHERE IdPrimita = %s AND IDDF = %s", (id_primita, iddf))
        if cursor.rowcount == 0:
            raise EfEroare("Nu exista o legatura manuala cu acel DDF.", "NU_EXISTA", 404)
        conn.commit()


# ---------------------------------------------------------------------------------------------
# DDF picker (00EF-20)
# ---------------------------------------------------------------------------------------------
MAX_DDF = 300


def lista_ddf(dc, q=None):
    """The DDFs a received invoice can be linked to by hand, newest first: id, angajament, object, partner. `q` narrows on the
    angajament code, the object, the partner name or tax code (at most MAX_DDF rows)."""
    where, params = "1 = 1", []
    if q:
        like = "%" + re.sub(r"([\\%_])", r"\\\1", q) + "%"
        where = "(d.CodAngajament LIKE %s OR d.ObiectDDF LIKE %s OR d.NumePartener LIKE %s OR d.CodFiscal LIKE %s)"
        params = [like, like, like, like]
    with F._unit(dc, SQL_FILES) as conn:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT d.IDDF, d.CodAngajament, d.ObiectDDF, d.NumePartener, d.CodFiscal FROM FX_DDF d WHERE " + where
                       + f" ORDER BY d.IDDF DESC LIMIT {MAX_DDF}", tuple(params))
        return {"ddf": [_plain(r) for r in cursor.fetchall()]}
