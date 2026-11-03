# routes/efactura/primite_parteneri.py
"""
The partner of a received e-invoice's supplier, found by TAX CODE ONLY (slice 00EF-55). Shared by the sending to VERCON (primite_vercon.py) and the «Contari»
(primite_contari.py). Nothing else decides: not the name, not «Ascuns» (a hidden partner is found too), not the unit. The normalisation is the one rule of
utils.vercon_suppliers.

The partner in force for a supplier is, in this order: the one the operator named in the request; the one KEPT for that tax code (EF_FurnizorPartener, see
sql/00EF_55_furnizor_partener.sql; ignored when that partner no longer has the code); the only one with the code. Several and none kept: None, and the operator
chooses. When an operation is done for an invoice, the partner it used is kept (`leaga`) and stops being hidden (`activeaza`), in the same transaction.
"""
from utils import vercon_efactura as associations

from .tokens import EfEroare


def parteneri(cursor, cui):
    """EVERY partner row whose tax code equals this CUI, visible rows first: [{IdPartener, CodPartener, Denumire, Adresa, ContIBAN, Banca, Ascuns}]."""
    code = associations.fiscal_code(cui)
    if not code:
        return []
    cursor.execute("SELECT p.IdPartener, p.CodPartener, p.DenumirePartener, p.Adresa, p.ContIBAN, p.Banca, COALESCE(p.Ascuns, 0) AS Ascuns FROM Parteneri p WHERE "
                   + associations.fiscal_sql("p.CodFiscal") + " = %s ORDER BY (COALESCE(p.Ascuns, 0) <> 0), p.DenumirePartener, p.IdPartener", (code,))
    return [{"IdPartener": r["IdPartener"], "CodPartener": r["CodPartener"], "Denumire": r["DenumirePartener"] or "", "Adresa": r["Adresa"] or "",
             "ContIBAN": r["ContIBAN"] or "", "Banca": r["Banca"] or "", "Ascuns": bool(r["Ascuns"])} for r in cursor.fetchall()]


def _legat(cursor, cui):
    """The IdPartener kept for this tax code, or None."""
    code = associations.fiscal_code(cui)
    if not code:
        return None
    cursor.execute("SELECT IdPartener FROM EF_FurnizorPartener WHERE CuiNormalizat = %s", (code,))
    row = cursor.fetchone()
    return row["IdPartener"] if row else None


def alege(candidati, id_partener, id_legat=None):
    """The partner row to work with, or None while the operator still has to choose. Order: `id_partener` (refused when it is not among the candidates), then
    `id_legat` when it is one of them, then the only candidate."""
    if id_partener is not None and (isinstance(id_partener, bool) or not isinstance(id_partener, int)):
        raise EfEroare("«id_partener» trebuie să fie un număr.", "CAMP_INVALID", 400)
    if not candidati:
        return None
    if id_partener is not None:
        chosen = next((p for p in candidati if p["IdPartener"] == id_partener), None)
        if chosen is None:
            raise EfEroare("Partenerul ales nu are codul fiscal al furnizorului.", "PARTENER_INVALID", 409)
        return chosen
    kept = next((p for p in candidati if p["IdPartener"] == id_legat), None) if id_legat is not None else None
    if kept is not None:
        return kept
    return candidati[0] if len(candidati) == 1 else None


def rezolva(cursor, cui, id_partener=None):
    """(candidates, partner in force | None) for the supplier with this CUI."""
    candidati = parteneri(cursor, cui)
    return candidati, alege(candidati, id_partener, _legat(cursor, cui) if candidati else None)


def activeaza(cursor, id_partener):
    """The partner an invoice was processed for enters the flow: it stops being hidden."""
    cursor.execute("UPDATE Parteneri SET Ascuns = 0 WHERE IdPartener = %s AND COALESCE(Ascuns, 0) <> 0", (id_partener,))


def leaga(cursor, cui, id_partener, utilizator):
    """Keeps the partner for the supplier's tax code, so the operator is not asked again."""
    code = associations.fiscal_code(cui)
    if not code:
        return
    cursor.execute("INSERT INTO EF_FurnizorPartener (CuiNormalizat, IdPartener, UnAles) VALUES (%s, %s, %s) "
                   "ON DUPLICATE KEY UPDATE IdPartener = VALUES(IdPartener), UnAles = VALUES(UnAles)", (code, id_partener, utilizator))
