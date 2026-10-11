# routes/efactura/facturi_store.py
"""
SQL of the issued-invoice tables (slice 00EF-06): `EF_Clienti`, `EF_Facturi`, `EF_FacturiLinii` in the UNIT database
(sql/00EF_02_efactura_unitate.sql), the issuer's `AVACONT_COMUN.Unitati_Date` / `Unitati_Conturi` (slice 00EF-13,
sql/00EF_13_unitati_detalii.sql), and the two read-only common lists `AVACONT_COMUN.EF_UM` and `AVACONT_COMUN.BIC`.

Plain statements on a cursor the caller owns (a DICTIONARY cursor: rows come back as dicts); the rules --
who may change what, in which state -- are in facturi.py. Nothing here commits.

No `%` is ever written inside a statement: patterns travel as parameters (mysql.connector substitutes `%s` only).
"""
from decimal import Decimal

# The issuer's data and accounts live in AVACONT_COMUN (slice 00EF-13), one row per unit (key DC); the statements name the
# schema, so the unit connection reads and locks them in the same transaction as the invoice tables.
FURNIZOR_TABLE = "AVACONT_COMUN.Unitati_Date"
CONTURI_TABLE = "AVACONT_COMUN.Unitati_Conturi"
# What the window edits (and what the XML reads); DataAnaf is written only by furnizor_mark_anaf.
FURNIZOR_COLUMNS = ("Denumire", "CodFiscal", "Adresa", "Orasul", "Judetul", "Mail", "Telefon",
                    "SerieFactura", "NumarInitial", "AfiseazaPrimiteNoi")
CLIENT_COLUMNS = ("IdClient", "DenumireClient", "CodFiscal", "IndFiscal", "Cont", "Banca", "Adresa", "Judetul",
                  "Orasul", "Sector", "CNP")
FACTURA_COLUMNS = ("IdFactura", "IdClient", "SerieFactura", "NumarFactura", "DataFactura", "TipFactura",
                   "Comentarii", "BT_13", "ContPlata", "Anulata", "Trimisa", "id_incarcare", "id_descarcare",
                   "AtasamentOriginal", "IdFacturaA", "SerieFacturaA", "NumarFacturaA", "Corectata", "EroareAnaf")
LINIE_COLUMNS = ("IdContinut", "IdFactura", "NrCrt", "Continut", "Um", "Cant", "PU", "Valoare", "Platit", "Grup")

_FACTURA_SELECT = ", ".join("f." + name for name in FACTURA_COLUMNS)
_TOTAL = "(SELECT COALESCE(SUM(l.Valoare), 0) FROM EF_FacturiLinii l WHERE l.IdFactura = f.IdFactura)"


def like(text):
    """`text` as a LIKE pattern that matches it anywhere, with its own wildcards made literal."""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return "%" + escaped + "%"


# ---------------------------------------------------------------------------------------------
# issuer
# ---------------------------------------------------------------------------------------------
def furnizor_get(cursor, dc, for_update=False):
    """The unit's row of Unitati_Date (None = not filled in yet)."""
    cursor.execute("SELECT " + ", ".join(FURNIZOR_COLUMNS) + " FROM " + FURNIZOR_TABLE + " WHERE DC = %s"
                   + (" FOR UPDATE" if for_update else ""), (dc,))
    return cursor.fetchone()


def furnizor_save(cursor, dc, values):
    """Insert-or-update of the unit's row. `values` is a dict with every column of FURNIZOR_COLUMNS."""
    columns = ", ".join(FURNIZOR_COLUMNS)
    marks = ", ".join(["%s"] * len(FURNIZOR_COLUMNS))
    updates = ", ".join(f"{name} = VALUES({name})" for name in FURNIZOR_COLUMNS)
    cursor.execute(f"INSERT INTO {FURNIZOR_TABLE} (DC, {columns}) VALUES (%s, {marks}) ON DUPLICATE KEY UPDATE {updates}",
                   (dc,) + tuple(values[name] for name in FURNIZOR_COLUMNS))


def furnizor_unit(cursor, dc):
    """The unit's name and tax code from Unitati (None = no such unit)."""
    cursor.execute("SELECT NumeUnitate, CF FROM AVACONT_COMUN.Unitati WHERE DC = %s", (dc,))
    return cursor.fetchone()


def furnizor_mark_anaf(cursor, dc, denumire, adresa, judet, oras, cod_fiscal):
    """The take from ANAF (slice 00EF-13): writes the name and the address, and the county and the city when they could be
    read from the address (otherwise what is there stays). A unit with no row yet gets one, with `cod_fiscal` as the tax
    code; an existing row keeps everything else. DataAnaf = when it was last done."""
    cursor.execute(
        f"INSERT INTO {FURNIZOR_TABLE} (DC, Denumire, CodFiscal, Adresa, Judetul, Orasul, DataAnaf) "
        "VALUES (%s, %s, %s, %s, %s, %s, NOW()) "
        "ON DUPLICATE KEY UPDATE Denumire = VALUES(Denumire), Adresa = VALUES(Adresa), "
        "Judetul = COALESCE(VALUES(Judetul), Judetul), Orasul = COALESCE(VALUES(Orasul), Orasul), DataAnaf = NOW()",
        (dc, denumire, cod_fiscal, adresa, judet, oras))


def have_invoices(cursor):
    """True when the unit has issued at least one invoice (the series and the first number are then fixed)."""
    cursor.execute("SELECT 1 FROM EF_Facturi LIMIT 1")
    return cursor.fetchone() is not None


# ---------------------------------------------------------------------------------------------
# the issuer's bank accounts (slice 00EF-12, moved to AVACONT_COMUN.Unitati_Conturi by 00EF-13)
# ---------------------------------------------------------------------------------------------
def conturi_list(cursor, dc):
    cursor.execute("SELECT IdCont, Cont, Banca FROM " + CONTURI_TABLE + " WHERE DC = %s ORDER BY IdCont", (dc,))
    return cursor.fetchall()


def conturi_replace(cursor, dc, rows):
    """The whole list of the unit is replaced: `rows` is a list of (Cont, Banca). The caller commits (one transaction)."""
    cursor.execute("DELETE FROM " + CONTURI_TABLE + " WHERE DC = %s", (dc,))
    for cont, banca in rows:
        cursor.execute("INSERT INTO " + CONTURI_TABLE + " (DC, Cont, Banca) VALUES (%s, %s, %s)", (dc, cont, banca))


# ---------------------------------------------------------------------------------------------
# customers
# ---------------------------------------------------------------------------------------------
def clienti_list(cursor, q, limit):
    sql = "SELECT " + ", ".join(CLIENT_COLUMNS) + " FROM EF_Clienti"
    params = []
    if q:
        sql += " WHERE DenumireClient LIKE %s OR CodFiscal LIKE %s"
        params += [like(q), like(q)]
    sql += " ORDER BY DenumireClient, IdClient LIMIT %s"
    params.append(limit)
    cursor.execute(sql, tuple(params))
    return cursor.fetchall()


def client_get(cursor, id_client):
    cursor.execute("SELECT " + ", ".join(CLIENT_COLUMNS) + " FROM EF_Clienti WHERE IdClient = %s", (id_client,))
    return cursor.fetchone()


def client_insert(cursor, values):
    names = [name for name in CLIENT_COLUMNS if name != "IdClient"]
    cursor.execute(f"INSERT INTO EF_Clienti ({', '.join(names)}) VALUES ({', '.join(['%s'] * len(names))})",
                   tuple(values[name] for name in names))
    return cursor.lastrowid


def client_update(cursor, id_client, values):
    names = [name for name in CLIENT_COLUMNS if name != "IdClient"]
    cursor.execute(f"UPDATE EF_Clienti SET {', '.join(name + ' = %s' for name in names)} WHERE IdClient = %s",
                   tuple(values[name] for name in names) + (id_client,))
    return cursor.rowcount


def client_has_invoices(cursor, id_client):
    cursor.execute("SELECT COUNT(*) AS n FROM EF_Facturi WHERE IdClient = %s", (id_client,))
    return cursor.fetchone()["n"] > 0


def client_delete(cursor, id_client):
    cursor.execute("DELETE FROM EF_Clienti WHERE IdClient = %s", (id_client,))
    return cursor.rowcount


# ---------------------------------------------------------------------------------------------
# invoices
# ---------------------------------------------------------------------------------------------
def facturi_list(cursor, year, q, limit):
    """Headers (no lines) newest first, with the customer's name and the total of the lines."""
    sql = (f"SELECT {_FACTURA_SELECT}, c.DenumireClient AS ClientDenumire, {_TOTAL} AS Total "
           "FROM EF_Facturi f INNER JOIN EF_Clienti c ON c.IdClient = f.IdClient WHERE 1 = 1")
    params = []
    if year is not None:
        sql += " AND f.DataFactura >= %s AND f.DataFactura < %s"
        params += [f"{year:04d}-01-01", f"{year + 1:04d}-01-01"]
    if q:
        sql += " AND (c.DenumireClient LIKE %s OR c.CodFiscal LIKE %s OR CAST(f.NumarFactura AS CHAR) LIKE %s)"
        params += [like(q), like(q), like(q)]
    sql += " ORDER BY f.IdFactura DESC LIMIT %s"
    params.append(limit)
    cursor.execute(sql, tuple(params))
    return cursor.fetchall()


def factura_get(cursor, id_factura, for_update=False):
    cursor.execute(f"SELECT {_FACTURA_SELECT}, {_TOTAL} AS Total FROM EF_Facturi f WHERE f.IdFactura = %s"
                   + (" FOR UPDATE" if for_update else ""), (id_factura,))
    return cursor.fetchone()


def linii_get(cursor, id_factura):
    cursor.execute("SELECT " + ", ".join(LINIE_COLUMNS) + " FROM EF_FacturiLinii WHERE IdFactura = %s "
                   "ORDER BY IdContinut", (id_factura,))
    return cursor.fetchall()


def next_number(cursor, series, initial):
    """The number after the highest one of `series`, or `initial` when the series has no invoice yet."""
    cursor.execute("SELECT MAX(NumarFactura) AS m FROM EF_Facturi WHERE SerieFactura = %s", (series,))
    highest = cursor.fetchone()["m"]
    return initial if highest is None else int(highest) + 1


def is_last_of_series(cursor, series, number):
    cursor.execute("SELECT COUNT(*) AS n FROM EF_Facturi WHERE SerieFactura = %s AND NumarFactura > %s",
                   (series, number))
    return cursor.fetchone()["n"] == 0


def max_date(cursor, series, below_number=None):
    """The newest date among the invoices of `series` (those under `below_number` when given), or None when there is
    none. Numbers run in the order of the dates, so a new or moved invoice may not be older than this (slice 00EF-09)."""
    if below_number is None:
        cursor.execute("SELECT MAX(DataFactura) AS d FROM EF_Facturi WHERE SerieFactura = %s", (series,))
    else:
        cursor.execute("SELECT MAX(DataFactura) AS d FROM EF_Facturi WHERE SerieFactura = %s AND NumarFactura < %s",
                       (series, below_number))
    return cursor.fetchone()["d"]


def storno_of(cursor, id_factura):
    """Id of the invoice that cancels `id_factura`, or None."""
    cursor.execute("SELECT IdFactura FROM EF_Facturi WHERE IdFacturaA = %s LIMIT 1", (id_factura,))
    row = cursor.fetchone()
    return None if row is None else row["IdFactura"]


_INSERT_FACTURA = (
    "INSERT INTO EF_Facturi (IdClient, SerieFactura, NumarFactura, DataFactura, TipFactura, Comentarii, BT_13, "
    "ContPlata, AtasamentOriginal, IdFacturaA, SerieFacturaA, NumarFacturaA, Corectata) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
)


def factura_insert(cursor, v):
    cursor.execute(_INSERT_FACTURA, (
        v["IdClient"], v["SerieFactura"], v["NumarFactura"], v["DataFactura"], v["TipFactura"], v["Comentarii"],
        v["BT_13"], v["ContPlata"], v["AtasamentOriginal"], v.get("IdFacturaA"), v.get("SerieFacturaA"),
        v.get("NumarFacturaA"), v.get("Corectata", 0)))
    return cursor.lastrowid


def factura_update_full(cursor, id_factura, v):
    """A draft (never uploaded): the number and the series do not change here."""
    cursor.execute(
        "UPDATE EF_Facturi SET IdClient = %s, DataFactura = %s, Comentarii = %s, BT_13 = %s, ContPlata = %s, "
        "AtasamentOriginal = %s WHERE IdFactura = %s",
        (v["IdClient"], v["DataFactura"], v["Comentarii"], v["BT_13"], v["ContPlata"], v["AtasamentOriginal"],
         id_factura))


def factura_mark_uploaded(cursor, id_factura, id_incarcare, correction=None):
    """ANAF took the file: its upload number is stored, the download id and the old refusal text are cleared (the
    invoice is now «incarcata» until the state is read), `Trimisa` = 1. `correction` = (comentarii, bt_13) when an
    accepted invoice was resent as type 384 (Access: TipFactura = 384, Corectata = True, comment and order reference)."""
    if correction is None:
        cursor.execute("UPDATE EF_Facturi SET id_incarcare = %s, id_descarcare = NULL, Trimisa = 1, EroareAnaf = NULL "
                       "WHERE IdFactura = %s", (id_incarcare, id_factura))
    else:
        cursor.execute("UPDATE EF_Facturi SET id_incarcare = %s, id_descarcare = NULL, Trimisa = 1, EroareAnaf = NULL, "
                       "TipFactura = '384', Corectata = 1, Comentarii = %s, BT_13 = %s WHERE IdFactura = %s",
                       (id_incarcare, correction[0], correction[1], id_factura))


def factura_mark_accepted(cursor, id_factura, id_descarcare):
    cursor.execute("UPDATE EF_Facturi SET id_descarcare = %s, EroareAnaf = NULL WHERE IdFactura = %s",
                   (id_descarcare, id_factura))


def factura_mark_refused(cursor, id_factura, reason):
    """Access wrote the word «Err» in `id_descarcare`; the reason is new (there was no column for it)."""
    cursor.execute("UPDATE EF_Facturi SET id_descarcare = 'Err', EroareAnaf = %s WHERE IdFactura = %s",
                   (reason[:2000], id_factura))


def messages_known(cursor, ids):
    """Which of the ANAF message ids (`id_solicitare`) are already in EF_Mesaje."""
    if not ids:
        return set()
    cursor.execute("SELECT IdSol FROM EF_Mesaje WHERE IdSol IN (" + ", ".join(["%s"] * len(ids)) + ")", tuple(ids))
    return {row["IdSol"] for row in cursor.fetchall()}


def factura_delete(cursor, id_factura):
    cursor.execute("DELETE FROM EF_Facturi WHERE IdFactura = %s", (id_factura,))
    return cursor.rowcount


def linii_insert(cursor, id_factura, lines):
    """`lines`: dicts with NrCrt, Continut, Um, Cant, PU, Valoare, Platit, Grup (already checked)."""
    cursor.executemany(
        "INSERT INTO EF_FacturiLinii (IdFactura, NrCrt, Continut, Um, Cant, PU, Valoare, Platit, Grup) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
        [(id_factura, ln["NrCrt"], ln["Continut"], ln["Um"], ln["Cant"], ln["PU"], ln["Valoare"],
          ln.get("Platit", 0), ln.get("Grup", 0)) for ln in lines])


def linii_delete(cursor, id_factura):
    cursor.execute("DELETE FROM EF_FacturiLinii WHERE IdFactura = %s", (id_factura,))


def negate(lines):
    """The lines of a cancelling invoice: quantity and value with the opposite sign (Access `-1*Cant`, `-1*Valoare`)."""
    return [{**ln, "Cant": -Decimal(ln["Cant"]), "Valoare": -Decimal(ln["Valoare"])} for ln in lines]


# ---------------------------------------------------------------------------------------------
# AVACONT_COMUN lists (read only)
# ---------------------------------------------------------------------------------------------
def um_all(cursor, q):
    sql = "SELECT Cod, Explicatie FROM EF_UM"
    params = []
    if q:
        sql += " WHERE Cod LIKE %s OR Explicatie LIKE %s"
        params += [like(q), like(q)]
    cursor.execute(sql + " ORDER BY Cod", tuple(params))
    return cursor.fetchall()


def um_codes(cursor):
    cursor.execute("SELECT Cod FROM EF_UM")
    return {row["Cod"] for row in cursor.fetchall()}


def bank_name(cursor, code):
    cursor.execute("SELECT Banca FROM BIC WHERE Cod = %s", (code,))
    row = cursor.fetchone()
    return "" if row is None or row["Banca"] is None else row["Banca"]
