# routes/efactura/primite_access_legare.py
"""
Automatic link of the received invoices that a preluare from Access ADDED to the receptions that already exist (slice 00EF-43; the same
rules the Migrator's E-Factura tab has had since 00EF-38, `EfReceptionLinker`: keep them in step).

It does what the accountant does by hand on the invoice's «Recepții» view (slices KE01..KE04), but only when the match is certain, and writes what
that screen writes: `EF_PrimiteLinii.IdClsf` (the classification of every line) and one row of `EF_PrimiteReceptii` (type «existenta»).

The angajamente an invoice may go to are those of the DDFs whose partner has the supplier's tax code (`FX_DDF_Parteneri.CodFiscal` and the
header partner `FX_DDF.CodFiscal`), both normalised like `EF_Primite.CuiNormalizat`. A supplier with no DDF is never linked.

Certainty, in this order: (1) the TOTAL: a reception of those angajamente whose uncovered amount (value of its last counting snapshot minus
what other links already allocate) equals the invoice total with the same sign; exactly one such reception = linked. (2) several with that
amount: the MONTH of the invoice date against the month of the reception date; exactly one in that month = linked. Anything else is left for the
accountant: none, several, a reception on more than one classification, or two invoices that want the same reception (both left alone).
Only the invoices passed in are looked at, so an invoice that was already here (and may have been linked by hand since) is never touched.
"""
import logging
from decimal import Decimal, ROUND_HALF_UP

from . import primite_ubl

logger = logging.getLogger(__name__)

TOLERANTA = Decimal("0.005")
CENT = Decimal("0.01")
LEGAT_DE = "preluare Access"
TABELE = ("EF_Primite", "EF_PrimiteLinii", "EF_PrimiteReceptii", "FX_DDF", "FX_Receptii_R", "FX_Receptii_H", "FX_Receptii", "FX_Indicatori")

_SQL_RECEPTII = (
    "SELECT R.IDRR, R.CodAngajament, R.DataR, I.IdClsf, SUM(Rc.Valoare) AS Valoare "
    "FROM FX_Receptii_R R "
    "JOIN FX_Receptii_H H ON H.IDRH = ("
    "  SELECT H2.IDRH FROM FX_Receptii_H H2 WHERE H2.IDRR = R.IDRR AND COALESCE(H2.Sters, 0) = 0 "
    "  AND (COALESCE(H2.EsteStergere, 0) <> 0 OR EXISTS (SELECT 1 FROM FX_Receptii L WHERE L.IDRH = H2.IDRH)) "
    "  ORDER BY H2.DataH DESC, H2.NrCrt DESC, H2.IDRH DESC LIMIT 1) "
    "LEFT JOIN FX_Receptii Rc ON Rc.IDRH = H.IDRH "
    "LEFT JOIN FX_Indicatori I ON I.CodAI = Rc.CodAI "
    "WHERE COALESCE(R.Sters, 0) = 0 AND COALESCE(H.EsteStergere, 0) = 0 "
    "GROUP BY R.IDRR, R.CodAngajament, R.DataR, I.IdClsf")
_SQL_ALOCAT = (
    "SELECT COALESCE(l.IDRR, (SELECT H.IDRR FROM FX_Receptii_H H WHERE H.IDRH = l.IDRH LIMIT 1)) AS IdReceptie, "
    "l.IdClsf, SUM(l.Suma) AS Suma FROM EF_PrimiteReceptii l GROUP BY IdReceptie, l.IdClsf")


def _tables_missing(cursor):
    cursor.execute("SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN (%s)"
                   % ", ".join(["%s"] * len(TABELE)), TABELE)
    have = {row["TABLE_NAME"] for row in cursor.fetchall()}
    missing = [name for name in TABELE if name not in have]
    if not missing:
        cursor.execute("SELECT COUNT(*) AS n FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() "
                       "AND TABLE_NAME = 'EF_PrimiteLinii' AND COLUMN_NAME = 'IdClsf'")
        if not cursor.fetchone()["n"]:
            missing.append("EF_PrimiteLinii.IdClsf")
    return missing


def _supplier_angajamente(cursor):
    cursor.execute("SELECT 1 AS x FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'FX_DDF_Parteneri'")
    sql = "SELECT d.CodFiscal, d.CodAngajament FROM FX_DDF d WHERE d.CodFiscal IS NOT NULL"
    if cursor.fetchone():
        sql += " UNION SELECT p.CodFiscal, d.CodAngajament FROM FX_DDF_Parteneri p JOIN FX_DDF d ON d.IDDF = p.IDDF"
    cursor.execute(sql)
    result = {}
    for row in cursor.fetchall():
        if row["CodFiscal"] is None or row["CodAngajament"] is None:
            continue
        cui = primite_ubl.normalize_cui(row["CodFiscal"])
        if cui:
            result.setdefault(cui, set()).add(str(row["CodAngajament"]).lower())
    return result


def _receptii_pe_angajament(cursor):
    """{cod angajament (lower case): [reception]}, a reception = { id, cod, data, valori: {IdClsf: sum}, rest: {IdClsf: uncovered} }."""
    cursor.execute(_SQL_RECEPTII)
    receptii = {}
    for row in cursor.fetchall():
        item = receptii.get(row["IDRR"])
        if item is None:
            item = receptii[row["IDRR"]] = {"id": row["IDRR"], "cod": row["CodAngajament"] or "", "data": row["DataR"], "valori": {}, "rest": {}}
        if row["IdClsf"] is None or row["Valoare"] is None:
            continue
        value = Decimal(row["Valoare"]).quantize(CENT, ROUND_HALF_UP)
        item["valori"][row["IdClsf"]] = item["valori"].get(row["IdClsf"], Decimal(0)) + value
    cursor.execute(_SQL_ALOCAT)
    alocat = {(row["IdReceptie"], row["IdClsf"]): Decimal(row["Suma"]) for row in cursor.fetchall()
              if row["IdReceptie"] is not None and row["Suma"] is not None}
    by_cod = {}
    for item in receptii.values():
        if not item["cod"]:
            continue
        for clsf, value in item["valori"].items():
            rest = value - alocat.get((item["id"], clsf), Decimal(0))
            if abs(rest) > TOLERANTA:
                item["rest"][clsf] = rest
        by_cod.setdefault(item["cod"].lower(), []).append(item)
    return by_cod


def _sign(value):
    return (value > 0) - (value < 0)


def lega_automat(cursor, id_primite):
    """Links the invoices `id_primite`. Answers the counts, or `{ omisa: text }` when a table of the receptions is missing."""
    answer = {"citite": len(id_primite), "legate": 0, "fara_furnizor": 0, "fara_receptie": 0, "nesigure": 0,
              "mai_multe_clasificatii": 0, "conflicte": 0, "omisa": None}
    if not id_primite:
        return answer
    missing = _tables_missing(cursor)
    if missing:
        answer["omisa"] = ("Legarea automată de recepții este omisă: în baza unității lipsesc " + ", ".join(missing) +
                           ". Legați facturile din K-BOT.")
        return answer

    invoices = []
    for start in range(0, len(id_primite), 500):
        chunk = id_primite[start:start + 500]
        cursor.execute("SELECT IdPrimita, NrFact, CuiNormalizat, Total, Semn, DataFact FROM EF_Primite WHERE IdPrimita IN (%s)"
                       % ", ".join(["%s"] * len(chunk)), tuple(chunk))
        invoices += cursor.fetchall()
    invoices.sort(key=lambda row: (row["DataFact"] is None, row["DataFact"], row["IdPrimita"]))

    suppliers = _supplier_angajamente(cursor)
    receptii = _receptii_pe_angajament(cursor)
    chosen = []
    for inv in invoices:
        total = Decimal(inv["Total"] or 0) * (-1 if (inv["Semn"] or 1) < 0 else 1)
        cods = suppliers.get(inv["CuiNormalizat"] or "")
        if total == 0 or not cods:
            answer["fara_furnizor"] += 1
            continue
        candidates = [r for cod in cods for r in receptii.get(cod, [])
                      if r["rest"] and _sign(sum(r["rest"].values())) == _sign(total) and abs(sum(r["rest"].values()) - total) <= TOLERANTA]
        if not candidates:
            answer["fara_receptie"] += 1
            continue
        pick = None
        if len(candidates) == 1:
            pick = candidates[0]
        elif inv["DataFact"] is not None:
            same = [r for r in candidates if r["data"] is not None and (r["data"].year, r["data"].month) == (inv["DataFact"].year, inv["DataFact"].month)]
            if len(same) == 1:
                pick = same[0]
        if pick is None:
            answer["nesigure"] += 1
            continue
        if len(pick["rest"]) != 1:
            answer["mai_multe_clasificatii"] += 1
            continue
        chosen.append((inv, total, pick))

    by_reception = {}
    for item in chosen:
        by_reception.setdefault(item[2]["id"], []).append(item)
    for group in by_reception.values():
        if len(group) > 1:
            answer["conflicte"] += len(group)
            continue
        inv, total, pick = group[0]
        clsf = next(iter(pick["rest"]))
        cursor.execute("UPDATE EF_PrimiteLinii SET IdClsf = %s WHERE IdPrimita = %s AND IdClsf IS NULL", (clsf, inv["IdPrimita"]))
        cursor.execute("INSERT INTO EF_PrimiteReceptii (IdPrimita, CodAngajament, IdClsf, IDRR, Suma, Tip, UN) "
                       "VALUES (%s, %s, %s, %s, %s, 'existenta', %s)", (inv["IdPrimita"], pick["cod"], clsf, pick["id"], total, LEGAT_DE))
        answer["legate"] += 1
    return answer
