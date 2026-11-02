# routes/avacont/introducere_date/operatii/postare.py
"""
The POSTING SERVICE of Avacont (slice AV02-01 tables, first caller: 01EF-04, the booking of a received e-invoice).

It is the only code that writes TZ_NoteC, TZ_Documente, TZ_Oper, TZ_Debit and TZ_Credit. It takes a cursor from the CALLER'S transaction (never commits,
never opens a connection) so a caller can book an invoice, link it in EF_PrimiteOperatii and roll everything back together. No Flask here.

Rules kept here (the database cannot say them across rows):
- the number of a note, and the commitment number of an operation (NrAng), are the highest of the subunit and year + 1, read with FOR UPDATE;
- an account of a line must be an ACTIVE row of the chart of accounts of the SAME subunit (found from the base account, the analytic and the classification);
- debit total = credit total of an operation;
- a date inside a closed month moves to the 1st of the next month; a date of an earlier year moves to 1 January of the work year (Access AjusteazaDataFacturi).
"""
import logging
from datetime import date, timedelta
from decimal import Decimal

logger = logging.getLogger(__name__)

FEL_INCHIDERE = "I"
CENT = Decimal("0.01")


class PostareEroare(Exception):
    """A refusal the operator can act on: `reason` is a code, the text is Romanian."""

    def __init__(self, message, reason, status=409):
        super().__init__(message)
        self.reason = reason
        self.status = status


# ---------------------------------------------------------------------------------------------
# subunit and dates
# ---------------------------------------------------------------------------------------------
def subunitate_pentru(cursor, sursa_sector, cerut=None):
    """The subunit a booking belongs to: the one asked for, else the only active one with this sector+source, else the default among them."""
    if cerut is not None:
        cursor.execute("SELECT IdSubunitate, Denumire, SursaSector, Capitol, Implicita FROM TZ_Subunitati WHERE IdSubunitate = %s AND Activ = 1", (cerut,))
        row = cursor.fetchone()
        if row is None:
            raise PostareEroare("Subunitatea aleasă nu există sau nu este activă.", "SUBUNITATE_INVALIDA")
        return row
    cursor.execute("SELECT IdSubunitate, Denumire, SursaSector, Capitol, Implicita FROM TZ_Subunitati WHERE SursaSector = %s AND Activ = 1 ORDER BY Implicita DESC, IdSubunitate",
                   (sursa_sector,))
    rows = cursor.fetchall()
    if not rows:
        raise PostareEroare("Nu există nicio subunitate cu sectorul și sursa %s. Creați-o în «Plan de conturi»." % sursa_sector, "SUBUNITATE_LIPSA")
    if len(rows) > 1 and not any(r["Implicita"] for r in rows):
        raise PostareEroare("Mai multe subunități au sectorul și sursa %s: alegeți una." % sursa_sector, "SUBUNITATE_AMBIGUA")
    return rows[0]


def data_ultimei_inchideri(cursor, id_subunitate, an):
    """The date of the newest closing document («CCV», Fel = I) of the subunit in that year, or None."""
    cursor.execute("SELECT MAX(d.DataDocument) AS D FROM TZ_Documente d JOIN TZ_NoteC n ON n.IdNota = d.IdNota "
                   "WHERE n.IdSubunitate = %s AND n.An = %s AND d.Fel = %s", (id_subunitate, an, FEL_INCHIDERE))
    row = cursor.fetchone()
    return row["D"] if row else None


def ajusteaza_data(data, inchidere, an_lucru):
    """(date, reason): a date in a closed month goes to the 1st of the next month; a date of an earlier year to 1 January of the work year. reason is '' when unchanged."""
    if data is None:
        return data, ""
    nou, motiv = data, ""
    if inchidere is not None and data.year == inchidere.year and data.month <= inchidere.month:
        nou = (date(inchidere.year, inchidere.month, 28) + timedelta(days=4)).replace(day=1)
        motiv = "luna era închisă"
    if an_lucru and nou.year < an_lucru:
        nou, motiv = date(an_lucru, 1, 1), "factura este dintr-un an anterior"
    return nou, motiv


# ---------------------------------------------------------------------------------------------
# numbers
# ---------------------------------------------------------------------------------------------
def numar_nota_urmator(cursor, id_subunitate, an):
    cursor.execute("SELECT COALESCE(MAX(NumarNC), 0) + 1 AS N FROM TZ_NoteC WHERE IdSubunitate = %s AND An = %s FOR UPDATE", (id_subunitate, an))
    return int(cursor.fetchone()["N"])


def numar_angajament_urmator(cursor, id_subunitate, an):
    cursor.execute("SELECT COALESCE(MAX(o.NrAng), 0) + 1 AS N FROM TZ_Oper o JOIN TZ_Documente d ON d.IdDocument = o.IdDocument JOIN TZ_NoteC n ON n.IdNota = d.IdNota "
                   "WHERE n.IdSubunitate = %s AND n.An = %s FOR UPDATE", (id_subunitate, an))
    return int(cursor.fetchone()["N"])


def nota_existenta(cursor, id_subunitate, an, numar):
    """IdNota of the note with this number in the subunit and year (locked), or None."""
    cursor.execute("SELECT IdNota FROM TZ_NoteC WHERE IdSubunitate = %s AND An = %s AND NumarNC = %s FOR UPDATE", (id_subunitate, an, numar))
    row = cursor.fetchone()
    return row["IdNota"] if row else None


def note_ale_subunitatii(cursor, id_subunitate, an, limita=300):
    """The notes of the subunit and year, newest number first, with the number of documents: [{NumarNC, DataNc, Documente}], and the next free number."""
    cursor.execute("SELECT n.NumarNC, n.DataNc, COUNT(d.IdDocument) AS Documente FROM TZ_NoteC n LEFT JOIN TZ_Documente d ON d.IdNota = n.IdNota "
                   "WHERE n.IdSubunitate = %s AND n.An = %s GROUP BY n.IdNota, n.NumarNC, n.DataNc ORDER BY n.NumarNC DESC LIMIT %s", (id_subunitate, an, limita))
    rows = cursor.fetchall()
    cursor.execute("SELECT COALESCE(MAX(NumarNC), 0) + 1 AS N FROM TZ_NoteC WHERE IdSubunitate = %s AND An = %s", (id_subunitate, an))
    return rows, int(cursor.fetchone()["N"])


# ---------------------------------------------------------------------------------------------
# accounts
# ---------------------------------------------------------------------------------------------
def cont_pentru(cursor, id_subunitate, cont, analitic, clsf_f, clsf_e):
    """IdCont of the account in the subunit's chart: base account + analytic, tied to the classification when the account has ClsfF / ClsfE.
    Several rows fit when the account exists for several classifications: the one that names the classification wins."""
    cursor.execute("SELECT IdCont, ClsfF, ClsfE FROM TZ_PlanCont WHERE IdSubunitate = %s AND Cont = %s AND COALESCE(Analitic, '') = %s AND Activ = 1 "
                   "AND (ClsfF IS NULL OR ClsfF = %s) AND (ClsfE IS NULL OR ClsfE = %s)", (id_subunitate, cont, analitic or "", clsf_f, clsf_e))
    rows = cursor.fetchall()
    if not rows:
        etichet = "%s%s" % (cont, ("." + analitic) if analitic else "")
        raise PostareEroare("Contul %s nu există (activ) în planul de conturi al subunității, pentru această clasificație." % etichet, "CONT_LIPSA")
    if len(rows) > 1:
        rows.sort(key=lambda r: (r["ClsfF"] is not None) + (r["ClsfE"] is not None), reverse=True)
        if (rows[0]["ClsfF"] is not None) + (rows[0]["ClsfE"] is not None) == (rows[1]["ClsfF"] is not None) + (rows[1]["ClsfE"] is not None):
            raise PostareEroare("Contul %s apare de mai multe ori în planul de conturi al subunității." % cont, "CONT_AMBIGUU")
    return rows[0]["IdCont"]


def _cont_din_subunitate(cursor, id_cont, id_subunitate):
    cursor.execute("SELECT IdSubunitate, Activ FROM TZ_PlanCont WHERE IdCont = %s", (id_cont,))
    row = cursor.fetchone()
    if row is None or row["IdSubunitate"] != id_subunitate or not row["Activ"]:
        raise PostareEroare("Un cont al operației nu aparține planului de conturi al subunității.", "CONT_STRAIN")


# ---------------------------------------------------------------------------------------------
# writing
# ---------------------------------------------------------------------------------------------
def creeaza_nota(cursor, id_subunitate, an, user, numar=None):
    """A new note. `numar` = the number the operator typed (it must not exist yet); None = the next number."""
    if numar is None:
        numar = numar_nota_urmator(cursor, id_subunitate, an)
    else:
        cursor.execute("SELECT IdNota FROM TZ_NoteC WHERE IdSubunitate = %s AND An = %s AND NumarNC = %s FOR UPDATE", (id_subunitate, an, numar))
        if cursor.fetchone() is not None:
            raise PostareEroare("Nota contabilă %d există deja." % numar, "NOTA_EXISTENTA")
    cursor.execute("INSERT INTO TZ_NoteC (IdSubunitate, An, NumarNC, DataNc, UN) VALUES (%s, %s, %s, NOW(), %s)", (id_subunitate, an, numar, user))
    return cursor.lastrowid, numar


def creeaza_document(cursor, id_nota, numar_document, data_document, fel_document, fel, auto, user):
    cursor.execute("INSERT INTO TZ_Documente (IdNota, NumarDocument, DataDocument, FelDocument, Fel, Auto, UN) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                   (id_nota, numar_document, data_document, fel_document, fel, 1 if auto else 0, user))
    return cursor.lastrowid


def creeaza_operatie(cursor, id_subunitate, an, id_document, antet, linii_debit, linii_credit, user):
    """One operation with its lines. antet: IdJurnal, IdClsf, IdSablon, DataOperatie, Explicatie, Tip, Compartiment, Angajament, Propunere, Ordonantare, Chelt, Auto.
    linii_debit / linii_credit: [(IdCont, IdPartener or None, Suma)]. Returns (IdOper, NrAng)."""
    for id_cont, _, _ in list(linii_debit) + list(linii_credit):
        _cont_din_subunitate(cursor, id_cont, id_subunitate)
    nr_ang = numar_angajament_urmator(cursor, id_subunitate, an)
    cursor.execute(
        "INSERT INTO TZ_Oper (IdDocument, IdJurnal, IdClsf, IdSablon, DataOperatie, Explicatie, Tip, Compartiment, Cfpp, Angajament, Propunere, Ordonantare, Chelt, NrAng, Auto, UN) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 1, %s, %s, %s, %s, %s, %s, %s)",
        (id_document, antet["IdJurnal"], antet.get("IdClsf"), antet.get("IdSablon"), antet["DataOperatie"], antet.get("Explicatie") or "", antet.get("Tip"),
         antet.get("Compartiment"), 1 if antet.get("Angajament") else 0, 1 if antet.get("Propunere") else 0, 1 if antet.get("Ordonantare") else 0,
         1 if antet.get("Chelt") else 0, nr_ang, 1 if antet.get("Auto") else 0, user))
    id_oper = cursor.lastrowid
    for tabel, linii in (("TZ_Debit", linii_debit), ("TZ_Credit", linii_credit)):
        for id_cont, id_partener, suma in linii:
            cursor.execute("INSERT INTO " + tabel + " (IdOper, IdCont, IdPartener, Suma) VALUES (%s, %s, %s, %s)", (id_oper, id_cont, id_partener, suma))
    verifica_echilibru(cursor, id_oper)
    return id_oper, nr_ang


def verifica_echilibru(cursor, id_oper):
    cursor.execute("SELECT (SELECT COALESCE(SUM(Suma), 0) FROM TZ_Debit WHERE IdOper = %s) AS D, (SELECT COALESCE(SUM(Suma), 0) FROM TZ_Credit WHERE IdOper = %s) AS C",
                   (id_oper, id_oper))
    row = cursor.fetchone()
    if row["D"].quantize(CENT) != row["C"].quantize(CENT):
        raise PostareEroare("Operația nu este echilibrată: debit %s, credit %s." % (row["D"], row["C"]), "DEBIT_DIFERIT_CREDIT")
