# routes/efactura/primite_arhiva.py
"""
01EF-03: the ARCHIVE of the received e-invoices (the «Arhiva» view of the Access form EFACTURA_2025). An invoice the accountant does not
want to book (nothing to account for, a copy, a wrong addressee) is put in the archive and leaves the working list; it can be taken out again.
The table is EF_PrimiteArhiva (sql/01EF_01_efactura_arhiva.sql): one row per archived invoice, deleted on taking out (no history).

Rules (decided here, not only on the screen):
- Every WRITE is for the role «Contabil» (403 ROL_INTERZIS otherwise), the same rule as the links to receptions and the sending to VERCON.
- An invoice that has accounting operations (EF_PrimiteOperatii) cannot be archived: it is already in the books.
- DataArhivare and UN are set by the server (the session's e-mail). Rows migrated from Access have both NULL.
"""
import logging

from . import facturi as F
from . import primite_ke
from .tokens import EfEroare

logger = logging.getLogger(__name__)

ARHIVA_SQL = "sql/01EF_01_efactura_arhiva.sql"
MAX_MOTIV = 255


def _iso(value):
    return value.isoformat() if value is not None else None


def _rand(row):
    return {"IdPrimita": row["IdPrimita"], "DataArhivare": _iso(row["DataArhivare"]), "UN": row["UN"], "Motiv": row["Motiv"]}


def lista(dc):
    """Every archived invoice of the unit: [{IdPrimita, DataArhivare, UN, Motiv}] (DataArhivare / UN are None for rows migrated from Access)."""
    with F._unit(dc, ARHIVA_SQL) as conn:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT IdPrimita, DataArhivare, UN, Motiv FROM EF_PrimiteArhiva ORDER BY IdPrimita")
        return {"arhivate": [_rand(r) for r in cursor.fetchall()]}


def _exista(cursor, id_primita):
    cursor.execute("SELECT IdPrimita FROM EF_Primite WHERE IdPrimita = %s", (id_primita,))
    if cursor.fetchone() is None:
        raise EfEroare("Factura nu există.", "FACTURA_INEXISTENTA", 404)


def arhiveaza(dc, id_primita, role, user, motiv):
    """Puts the invoice in the archive (a second call changes nothing) and returns its row."""
    primite_ke.cere_contabil(role)
    text = (motiv or "").strip() or None
    if text is not None and len(text) > MAX_MOTIV:
        raise EfEroare("Motivul poate avea cel mult %d de caractere." % MAX_MOTIV, "CAMP_INVALID", 400)
    with F._unit(dc, ARHIVA_SQL) as conn:
        cursor = conn.cursor(dictionary=True)
        _exista(cursor, id_primita)
        cursor.execute("SELECT 1 AS X FROM EF_PrimiteOperatii WHERE IdPrimita = %s LIMIT 1", (id_primita,))
        if cursor.fetchone() is not None:
            raise EfEroare("Factura are operații contabile și nu poate fi arhivată.", "ARE_OPERATII", 409)
        cursor.execute("SELECT IdPrimita FROM EF_PrimiteArhiva WHERE IdPrimita = %s", (id_primita,))
        if cursor.fetchone() is None:
            cursor.execute("INSERT INTO EF_PrimiteArhiva (IdPrimita, DataArhivare, UN, Motiv) VALUES (%s, NOW(), %s, %s)",
                           (id_primita, user, text))
        conn.commit()
        cursor.execute("SELECT IdPrimita, DataArhivare, UN, Motiv FROM EF_PrimiteArhiva WHERE IdPrimita = %s", (id_primita,))
        return _rand(cursor.fetchone())


def scoate(dc, id_primita, role):
    """Takes the invoice out of the archive (nothing happens when it is not there)."""
    primite_ke.cere_contabil(role)
    with F._unit(dc, ARHIVA_SQL) as conn:
        cursor = conn.cursor(dictionary=True)
        _exista(cursor, id_primita)
        cursor.execute("DELETE FROM EF_PrimiteArhiva WHERE IdPrimita = %s", (id_primita,))
        conn.commit()
        return {"IdPrimita": id_primita, "arhivata": False}
