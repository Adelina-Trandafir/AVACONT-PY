# routes/efactura/primite_ke.py
"""
KE: links of a received e-invoice to receptions (slices KE01..KE07, plan docs/KE_PLAN.md). The rules live here, the routes in
primite_ke_routes.py. Every write is for the role «Contabil» only (the session's role, checked HERE, not only on the screen).

WHAT IS LINKED. A line of the invoice belongs to a budget classification (`EF_PrimiteLinii.IdClsf`, chosen by the accountant).
The invoice is covered on a classification by receptions: each link row of `EF_PrimiteReceptii` names a reception, a classification
and the amount WITH VAT of that classification the reception covers. A credit note is a normal reception with a negative sign:
its allocations are negative, nothing else changes.

AMOUNTS. A line value for a reception is its taxable value plus its VAT (the payable amount of the line). The rate of a line comes
from the XML (`EF_PrimiteLinii.CotaTVA`); a line without one takes the rate of the biggest taxable amount of the invoice. The cents
of rounding between the lines of one rate and the VAT the invoice states for that rate are put on the LAST line of the rate, so the
lines add up to the invoice total. Whatever the lines do not explain (an allowance or charge on the whole document) stays visible as
`nealocat_liniilor`; it is never spread over the lines.

THE VALUE OF A RECEPTION per classification is the sum of the lines (FX_Receptii) of its LAST counting snapshot (FX_Receptii_H), the same
rule the receptions view uses (slice 0065). A deleted reception (FX_Receptii_R.Sters, or a last snapshot that is a deletion) has none.
"""
import logging
import re
from decimal import Decimal, ROUND_HALF_UP

from . import facturi as F
from . import primite, primite_ubl
from .tokens import EfEroare

logger = logging.getLogger(__name__)

KE_SQL = "sql/KE_01_primite_receptii.sql"
ROL_CONTABIL = "Contabil"
CENT = Decimal("0.01")
TOLERANTA = Decimal("0.005")
MAX_ANGAJAMENTE = 300
TIPURI_LEGATURA = ("existenta", "noua", "modificata")


# ---------------------------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------------------------
def _dec(value):
    return Decimal(str(value)) if value is not None else Decimal(0)


def _cent(value):
    return _dec(value).quantize(CENT, rounding=ROUND_HALF_UP)


def _num(value):
    return float(_cent(value))


def _sign(value):
    return 1 if value > 0 else (-1 if value < 0 else 0)


def cere_contabil(role):
    """Raises 403 unless the session's role is «Contabil» (case ignored)."""
    if (role or "").strip().lower() != ROL_CONTABIL.lower():
        raise EfEroare("Numai un utilizator cu rolul «Contabil» poate lega facturile de recepții.", "ROL_INTERZIS", 403)


def _gol(text):
    return not (text or "").strip()


# ---------------------------------------------------------------------------------------------
# the amounts of the lines (pure)
# ---------------------------------------------------------------------------------------------
def valori_cu_tva(lines, rates):
    """Adds to every line of `lines` (dicts with Valoare and CotaTVA, None allowed) the keys `Cota` (the rate used), `CotaDedusa`
    (True when the line had none and several rates exist), `TvaLinie` and `ValoareCuTVA`. `rates` = [{CotaTVA, Baza, TVA}] of the
    invoice. Returns the same list."""
    biggest = None
    for rate in rates:
        if biggest is None or _dec(rate["Baza"]) > _dec(biggest["Baza"]):
            biggest = rate
    default = _dec(biggest["CotaTVA"]) if biggest is not None else Decimal(0)
    several = len({_cent(r["CotaTVA"]) for r in rates}) > 1
    groups = {}
    for line in lines:
        if line.get("CotaTVA") is not None:
            cota, deduced = _dec(line["CotaTVA"]), False
        else:
            cota, deduced = default, several
        line["Cota"], line["CotaDedusa"] = cota, deduced
        line["TvaLinie"] = _cent(_dec(line["Valoare"]) * cota / 100)
        groups.setdefault(_cent(cota), []).append(line)
    for cota, group in groups.items():
        target = sum((_dec(r["TVA"]) for r in rates if _cent(r["CotaTVA"]) == cota), Decimal(0))
        matching = [r for r in rates if _cent(r["CotaTVA"]) == cota]
        if not matching:
            continue
        diff = _cent(target) - sum((line["TvaLinie"] for line in group), Decimal(0))
        # a few cents of rounding are put on the last line of the rate; a bigger gap is something else (an allowance) and stays out
        if diff != 0 and abs(diff) <= Decimal("0.05") * len(group):
            group[-1]["TvaLinie"] += diff
    for line in lines:
        line["ValoareCuTVA"] = _cent(_dec(line["Valoare"]) + line["TvaLinie"])
    return lines


# ---------------------------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------------------------
_SQL_LINII = ("SELECT L.IdLinie, L.NrLinie, L.Denumire, L.Cpv, L.Valoare, L.CotaTVA, L.IdClsf, "
              "(SELECT C.Clsf FROM Clasificatii C WHERE C.IDClsf = L.IdClsf LIMIT 1) AS Clsf "
              "FROM EF_PrimiteLinii L WHERE L.IdPrimita = %s ORDER BY L.IdLinie")

_SQL_LEGATURI = ("SELECT l.IdLegatura, l.CodAngajament, l.IdClsf, l.Suma, l.Tip, l.UN, l.DataAdaugare, l.IDRH, l.IDR, "
                 "COALESCE(l.IDRR, (SELECT H.IDRR FROM FX_Receptii_H H WHERE H.IDRH = l.IDRH LIMIT 1)) AS IdReceptie, "
                 "(SELECT C.Clsf FROM Clasificatii C WHERE C.IDClsf = l.IdClsf LIMIT 1) AS Clsf "
                 "FROM EF_PrimiteReceptii l WHERE l.IdPrimita = %s ORDER BY l.IdLegatura")

_SQL_ALOCAT_RECEPTII = ("SELECT COALESCE(l.IDRR, (SELECT H.IDRR FROM FX_Receptii_H H WHERE H.IDRH = l.IDRH LIMIT 1)) AS IdReceptie, "
                        "l.IdClsf, SUM(l.Suma) AS Suma FROM EF_PrimiteReceptii l WHERE l.CodAngajament = %s "
                        "GROUP BY IdReceptie, l.IdClsf")

# The reception, its LAST counting snapshot and the sum of that snapshot's lines per classification (the rule of the receptions view).
_SQL_VALORI_RECEPTII = (
    "SELECT R.IDRR, R.DataR, R.TipReceptie, R.Descriere, H.IDRH, H.DataH, I.IdClsf, SUM(Rc.Valoare) AS Valoare "
    "FROM FX_Receptii_R R "
    "JOIN FX_Receptii_H H ON H.IDRH = ("
    "  SELECT H2.IDRH FROM FX_Receptii_H H2 WHERE H2.IDRR = R.IDRR AND COALESCE(H2.Sters, 0) = 0 "
    "  AND (COALESCE(H2.EsteStergere, 0) <> 0 OR EXISTS (SELECT 1 FROM FX_Receptii L WHERE L.IDRH = H2.IDRH)) "
    "  ORDER BY H2.DataH DESC, H2.NrCrt DESC, H2.IDRH DESC LIMIT 1) "
    "LEFT JOIN FX_Receptii Rc ON Rc.IDRH = H.IDRH "
    "LEFT JOIN FX_Indicatori I ON I.CodAI = Rc.CodAI "
    "WHERE R.CodAngajament = %s AND COALESCE(R.Sters, 0) = 0 AND COALESCE(H.EsteStergere, 0) = 0 "
    "GROUP BY R.IDRR, R.DataR, R.TipReceptie, R.Descriere, H.IDRH, H.DataH, I.IdClsf "
    "ORDER BY R.DataR DESC, R.IDRR DESC")

_SQL_INDICATORI = (
    "SELECT I.CodAI, I.CodIndicator, I.IdClsf, I.IndicatorFX, I.Angajament_Legal, I.Receptii, I.Plati, I.SS, I.NrCrt, "
    "(SELECT C.Clsf FROM Clasificatii C WHERE C.IDClsf = I.IdClsf LIMIT 1) AS Clsf, "
    "(SELECT C.Denumire FROM Clasificatii C WHERE C.IDClsf = I.IdClsf LIMIT 1) AS Denumire, "
    "(SELECT C.ClsfSal FROM Clasificatii C WHERE C.IDClsf = I.IdClsf LIMIT 1) AS ClsfSal "
    "FROM FX_Indicatori I WHERE I.CodAngajament = %s ORDER BY I.NrCrt, I.CodAI")


def _completeaza_cote(conn, cursor, id_primita, id_sol, lines):
    """A line stored before KE01 has no rate: read it from the kept XML (the lines come in the same order) and keep it."""
    if all(line["CotaTVA"] is not None for line in lines):
        return
    xml = primite._xml_of(cursor, id_sol)
    if not xml:
        return
    try:
        parsed = primite_ubl.parse(xml)
    except primite_ubl.XmlNeinteles:
        return
    if len(parsed["linii"]) != len(lines):
        logger.warning("[ke] IdPrimita=%s: %d linii in baza, %d in XML; cotele nu se completeaza", id_primita, len(lines), len(parsed["linii"]))
        return
    changed = False
    for line, read in zip(lines, parsed["linii"]):
        if line["CotaTVA"] is None and read.get("CotaTVA") is not None:
            cursor.execute("UPDATE EF_PrimiteLinii SET CotaTVA = %s WHERE IdLinie = %s", (read["CotaTVA"], line["IdLinie"]))
            line["CotaTVA"] = read["CotaTVA"]
            changed = True
    if changed:
        conn.commit()


def _cote(cursor, id_primita):
    cursor.execute("SELECT Categorie, CotaTVA, Baza, TVA FROM EF_PrimiteTVA WHERE IdPrimita = %s", (id_primita,))
    return cursor.fetchall()


def _factura_si_linii(conn, cursor, id_primita):
    """(invoice row, lines with their VAT values) -- amounts WITH the invoice sign (a credit note is negative)."""
    row = primite._row(cursor, id_primita)
    cursor.execute(_SQL_LINII, (id_primita,))
    lines = cursor.fetchall()
    _completeaza_cote(conn, cursor, id_primita, row["IdSol"], lines)
    valori_cu_tva(lines, _cote(cursor, id_primita))
    return row, lines


def _pe_clasificatie(lines, sign):
    """{IdClsf: {Clsf, Valoare}} of the lines that have a classification, amounts with the invoice sign; and the sum of the others."""
    by, without = {}, Decimal(0)
    for line in lines:
        amount = line["ValoareCuTVA"] * sign
        if line["IdClsf"] is None:
            without += amount
            continue
        entry = by.setdefault(line["IdClsf"], {"Clsf": line["Clsf"], "Valoare": Decimal(0)})
        entry["Valoare"] += amount
    return by, without


def _stare_facturii(total, alocat, are_legaturi):
    if not are_legaturi:
        return "nelegata"
    rest = total - alocat
    if abs(rest) <= TOLERANTA:
        return "acoperita"
    return "partiala" if _sign(rest) == _sign(total) else "depasita"


def _legaturi(cursor, id_primita):
    cursor.execute(_SQL_LEGATURI, (id_primita,))
    rows = cursor.fetchall()
    ids = sorted({r["IdReceptie"] for r in rows if r["IdReceptie"] is not None})
    info = {}
    if ids:
        cursor.execute("SELECT IDRR, DataR, TipReceptie, Descriere, COALESCE(Sters, 0) AS Sters FROM FX_Receptii_R WHERE IDRR IN ("
                       + ", ".join(["%s"] * len(ids)) + ")", tuple(ids))
        info = {r["IDRR"]: r for r in cursor.fetchall()}
    out = []
    for row in rows:
        reception = info.get(row["IdReceptie"])
        out.append({
            "IdLegatura": row["IdLegatura"], "CodAngajament": row["CodAngajament"], "IdClsf": row["IdClsf"], "Clsf": row["Clsf"],
            "Suma": _num(row["Suma"]), "Tip": row["Tip"], "UN": row["UN"],
            "DataAdaugare": row["DataAdaugare"].isoformat() if row["DataAdaugare"] else None,
            "IDRR": row["IdReceptie"], "IDRH": row["IDRH"], "IDR": row["IDR"],
            "DataR": reception["DataR"].isoformat() if reception and reception["DataR"] else None,
            "TipReceptie": reception["TipReceptie"] if reception else None,
            "DescriereR": reception["Descriere"] if reception else None,
            "StearsaPeSite": bool(reception and reception["Sters"]),
            # a link written before the download has no reception row yet: it waits for the download
            "AsteaptaDescarcarea": reception is None,
        })
    return out


def _angajamente_candidate(cursor, row, links):
    """The angajamente an invoice can be linked to: those of its DDFs (the supplier's partner link or the manual link) and those
    it is already linked to. [{CodAngajament, Descriere, Stare, Sursa}]"""
    found = {}
    cursor.execute("SELECT DISTINCT d.CodAngajament FROM EF_PrimiteAsocieri a JOIN FX_DDF d ON d.IDDF = a.IDDF WHERE a.IdPrimita = %s",
                   (row["IdPrimita"],))
    for item in cursor.fetchall():
        found.setdefault(item["CodAngajament"], "ddf")
    cursor.execute("SELECT DISTINCT d.CodAngajament FROM FX_DDF_Parteneri p JOIN FX_DDF d ON d.IDDF = p.IDDF WHERE "
                   + primite._PARTNER_CUI + " = %s", (row["CuiNormalizat"] or "-",))
    for item in cursor.fetchall():
        found.setdefault(item["CodAngajament"], "ddf")
    for link in links:
        found[link["CodAngajament"]] = "legatura"
    if not found:
        return []
    codes = sorted(found)
    cursor.execute("SELECT CodAngajament, Descriere, Alias, Stare FROM FX_Angajamente WHERE CodAngajament IN ("
                   + ", ".join(["%s"] * len(codes)) + ")", tuple(codes))
    info = {r["CodAngajament"]: r for r in cursor.fetchall()}
    def _name(code):
        # alias (the short name given in K-BOT) and description (the one the site shows): both, so the accountant recognises it
        item = info.get(code) or {}
        alias, desc = (item.get("Alias") or "").strip(), (item.get("Descriere") or "").strip()
        return f"{alias} — {desc}" if alias and desc and alias != desc else (alias or desc)

    return [{"CodAngajament": code, "Descriere": _name(code),
             "Stare": (info.get(code) or {}).get("Stare") or "", "Sursa": found[code]} for code in codes]


def starea(dc, id_primita, role):
    """Everything the «Receptii» view of an invoice shows: the lines with their values, the totals per classification, the links."""
    with F._unit(dc, KE_SQL) as conn:
        cursor = conn.cursor(dictionary=True)
        row, lines = _factura_si_linii(conn, cursor, id_primita)
        sign = int(row["Semn"] or 1)
        total = _cent(_dec(row["Total"]) * sign)
        by, without = _pe_clasificatie(lines, sign)
        links = _legaturi(cursor, id_primita)
        allocated = {}
        for link in links:
            allocated[link["IdClsf"]] = allocated.get(link["IdClsf"], Decimal(0)) + _dec(link["Suma"])
        allocated_total = sum(allocated.values(), Decimal(0))
        explained = sum((line["ValoareCuTVA"] for line in lines), Decimal(0)) * sign
        classes = []
        for id_clsf in sorted(set(by) | set(allocated)):
            value = by.get(id_clsf, {}).get("Valoare", Decimal(0))
            classes.append({"IdClsf": id_clsf, "Clsf": by.get(id_clsf, {}).get("Clsf"), "Valoare": _num(value),
                            "Alocat": _num(allocated.get(id_clsf, 0)), "Rest": _num(value - allocated.get(id_clsf, Decimal(0)))})
        return {
            "poate_scrie": (role or "").strip().lower() == ROL_CONTABIL.lower(),
            "total": _num(total), "alocat": _num(allocated_total), "rest": _num(total - allocated_total),
            "stare": _stare_facturii(total, allocated_total, bool(links)),
            "nealocat_liniilor": _num(total - explained), "fara_clasificatie": _num(without),
            "linii": [{"IdLinie": ln["IdLinie"], "NrLinie": ln["NrLinie"], "Denumire": ln["Denumire"], "Cpv": ln["Cpv"],
                       "Valoare": _num(ln["Valoare"]), "CotaTVA": _num(ln["Cota"]), "CotaDedusa": ln["CotaDedusa"],
                       "ValoareCuTVA": _num(ln["ValoareCuTVA"] * sign), "IdClsf": ln["IdClsf"], "Clsf": ln["Clsf"]} for ln in lines],
            "pe_clasificatie": classes, "legaturi": links,
            "angajamente": _angajamente_candidate(cursor, row, links),
        }


# ---------------------------------------------------------------------------------------------
# classification of the lines
# ---------------------------------------------------------------------------------------------
def seteaza_clasificatii(dc, id_primita, role, items):
    """items = [{IdLinie, IdClsf (None clears)}]. Every line must belong to the invoice and every classification must exist."""
    cere_contabil(role)
    if not isinstance(items, list) or not items:
        raise EfEroare("Nu a fost trimisă nicio linie.", "CAMP_INVALID", 400)
    with F._unit(dc, KE_SQL) as conn:
        cursor = conn.cursor(dictionary=True)
        primite._row(cursor, id_primita)
        cursor.execute("SELECT IdLinie FROM EF_PrimiteLinii WHERE IdPrimita = %s", (id_primita,))
        own = {r["IdLinie"] for r in cursor.fetchall()}
        for item in items:
            id_linie, id_clsf = item.get("IdLinie"), item.get("IdClsf")
            if isinstance(id_linie, bool) or not isinstance(id_linie, int) or id_linie not in own:
                raise EfEroare("O linie nu aparține facturii.", "CAMP_INVALID", 400)
            if id_clsf is not None:
                if isinstance(id_clsf, bool) or not isinstance(id_clsf, int):
                    raise EfEroare("Clasificația trebuie să fie un număr.", "CAMP_INVALID", 400)
                cursor.execute("SELECT 1 FROM Clasificatii WHERE IDClsf = %s LIMIT 1", (id_clsf,))
                if cursor.fetchone() is None:
                    raise EfEroare("O clasificație aleasă nu există.", "NU_EXISTA", 404)
        for item in items:
            cursor.execute("UPDATE EF_PrimiteLinii SET IdClsf = %s WHERE IdLinie = %s AND IdPrimita = %s",
                           (item.get("IdClsf"), item["IdLinie"], id_primita))
        conn.commit()


# ---------------------------------------------------------------------------------------------
# the angajament side
# ---------------------------------------------------------------------------------------------
def angajamente(dc, q=None):
    """The angajamente the accountant can pick when the supplier has no DDF: code, description, state, the partners of its DDFs."""
    where, params = "COALESCE(a.Ascuns, 0) = 0", []
    if q:
        like = "%" + re.sub(r"([\\%_])", r"\\\1", q) + "%"
        where += (" AND (a.CodAngajament LIKE %s OR a.Descriere LIKE %s OR a.Alias LIKE %s OR EXISTS (SELECT 1 FROM FX_DDF d "
                  "WHERE d.CodAngajament = a.CodAngajament AND (d.NumePartener LIKE %s OR d.CodFiscal LIKE %s)))")
        params = [like] * 5
    with F._unit(dc, KE_SQL) as conn:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT a.CodAngajament, a.Descriere, a.Alias, a.Stare, a.DataCreare, "
                       "(SELECT GROUP_CONCAT(DISTINCT d.NumePartener SEPARATOR ', ') FROM FX_DDF d "
                       " WHERE d.CodAngajament = a.CodAngajament) AS Parteneri "
                       "FROM FX_Angajamente a WHERE " + where + f" ORDER BY a.DataCreare DESC LIMIT {MAX_ANGAJAMENTE}", tuple(params))
        return {"angajamente": [primite._plain(r) for r in cursor.fetchall()]}


def _valori_receptii(cursor, cod):
    """{IDRR: {DataR, TipReceptie, Descriere, IDRH, valori: {IdClsf: Decimal}}} of the angajament, deleted ones left out."""
    cursor.execute(_SQL_VALORI_RECEPTII, (cod,))
    out = {}
    for row in cursor.fetchall():
        item = out.setdefault(row["IDRR"], {"IDRR": row["IDRR"], "DataR": row["DataR"], "TipReceptie": row["TipReceptie"],
                                            "Descriere": row["Descriere"], "IDRH": row["IDRH"], "valori": {}})
        if row["IdClsf"] is not None:
            item["valori"][row["IdClsf"]] = item["valori"].get(row["IdClsf"], Decimal(0)) + _cent(row["Valoare"])
    return out


def _alocat_receptii(cursor, cod):
    cursor.execute(_SQL_ALOCAT_RECEPTII, (cod,))
    return {(r["IdReceptie"], r["IdClsf"]): _dec(r["Suma"]) for r in cursor.fetchall() if r["IdReceptie"] is not None}


def _angajament(cursor, cod):
    cursor.execute("SELECT CodAngajament, Descriere, Alias, Stare FROM FX_Angajamente WHERE CodAngajament = %s", (cod,))
    row = cursor.fetchone()
    if row is None:
        raise EfEroare("Angajamentul nu există.", "NU_EXISTA", 404)
    return row


def receptii_angajament(dc, cod):
    """The indicators and the receptions of an angajament, with what the invoices already cover and what is left on each
    classification. {indicatori, receptii: [{IDRR, DataR, TipReceptie, Descriere, pe_clasificatie: [{IdClsf, Clsf, Valoare, Alocat, Rest}]}]}"""
    with F._unit(dc, KE_SQL) as conn:
        cursor = conn.cursor(dictionary=True)
        _angajament(cursor, cod)
        cursor.execute(_SQL_INDICATORI, (cod,))
        indicators = cursor.fetchall()
        names = {i["IdClsf"]: i["Clsf"] for i in indicators if i["IdClsf"] is not None}
        values, allocated = _valori_receptii(cursor, cod), _alocat_receptii(cursor, cod)
        # every snapshot number of each reception: the K-BOT marker the site keeps in the description names one of them
        cursor.execute("SELECT IDRR, IDRH FROM FX_Receptii_H WHERE CodAngajament = %s AND IDRR IS NOT NULL ORDER BY IDRH", (cod,))
        snapshots = {}
        for snap in cursor.fetchall():
            snapshots.setdefault(snap["IDRR"], []).append(snap["IDRH"])
        receptions = []
        for item in values.values():
            classes = []
            for id_clsf, value in sorted(item["valori"].items()):
                done = allocated.get((item["IDRR"], id_clsf), Decimal(0))
                classes.append({"IdClsf": id_clsf, "Clsf": names.get(id_clsf), "Valoare": _num(value), "Alocat": _num(done),
                                "Rest": _num(value - done)})
            receptions.append({"IDRR": item["IDRR"], "DataR": item["DataR"].isoformat() if item["DataR"] else None,
                               "TipReceptie": item["TipReceptie"], "Descriere": item["Descriere"], "IDRH": item["IDRH"],
                               "Total": _num(sum(item["valori"].values(), Decimal(0))), "Idrhs": snapshots.get(item["IDRR"], []),
                               "pe_clasificatie": classes})
        return {"indicatori": [{"CodAI": i["CodAI"], "CodIndicator": i["CodIndicator"], "IdClsf": i["IdClsf"], "Clsf": i["Clsf"],
                                "Denumire": i["Denumire"], "AngajamentLegal": _num(i["Angajament_Legal"]),
                                "Receptii": _num(i["Receptii"]), "Plati": _num(i["Plati"]), "SS": i["SS"]} for i in indicators],
                "receptii": receptions}


# ---------------------------------------------------------------------------------------------
# checks before anything is sent or written
# ---------------------------------------------------------------------------------------------
def _alocari(items):
    """The allocations of a request as [(IdClsf, Decimal)], zero ones dropped; a repeated classification is refused."""
    if not isinstance(items, list):
        raise EfEroare("Alocările trebuie să fie o listă.", "CAMP_INVALID", 400)
    out, seen = [], set()
    for item in items:
        if not isinstance(item, dict):
            raise EfEroare("O alocare nu are forma cerută.", "CAMP_INVALID", 400)
        id_clsf, suma = item.get("IdClsf"), item.get("Suma")
        if isinstance(id_clsf, bool) or not isinstance(id_clsf, int):
            raise EfEroare("Clasificația unei alocări trebuie să fie un număr.", "CAMP_INVALID", 400)
        if isinstance(suma, bool) or not isinstance(suma, (int, float)):
            raise EfEroare("Suma unei alocări trebuie să fie un număr.", "CAMP_INVALID", 400)
        if id_clsf in seen:
            raise EfEroare("O clasificație apare de două ori.", "CAMP_INVALID", 400)
        seen.add(id_clsf)
        if _cent(suma) != 0:
            out.append((id_clsf, _cent(suma)))
    if not out:
        raise EfEroare("Nu este nicio sumă de alocat.", "CAMP_INVALID", 400)
    return out


def _verifica(cursor, conn, id_primita, cod, alocari, tip):
    """Returns (problems, warnings, rows). rows = one per allocation with what the robot needs to find the indicator on the site."""
    problems, warnings, rows = [], [], []
    row, lines = _factura_si_linii(conn, cursor, id_primita)
    sign = int(row["Semn"] or 1)
    by, _ = _pe_clasificatie(lines, sign)
    links = _legaturi(cursor, id_primita)
    done = {}
    for link in links:
        done[link["IdClsf"]] = done.get(link["IdClsf"], Decimal(0)) + _dec(link["Suma"])
    cursor.execute(_SQL_INDICATORI, (cod,))
    indicators = {}
    for ind in cursor.fetchall():
        if ind["IdClsf"] is not None:
            indicators.setdefault(ind["IdClsf"], ind)
    for id_clsf, suma in alocari:
        entry = by.get(id_clsf)
        ind = indicators.get(id_clsf)
        label = (ind or {}).get("Clsf") or (entry or {}).get("Clsf") or f"clasificația {id_clsf}"
        if entry is None:
            problems.append(f"{label}: nicio linie a facturii nu are această clasificație.")
        else:
            rest = entry["Valoare"] - done.get(id_clsf, Decimal(0))
            if _sign(suma) != _sign(entry["Valoare"]):
                problems.append(f"{label}: semnul sumei nu se potrivește cu al facturii.")
            elif abs(suma) > abs(rest) + TOLERANTA:
                problems.append(f"{label}: suma {suma} depășește restul facturii ({_cent(rest)}).")
        if ind is None:
            problems.append(f"{label}: clasificația nu există ca indicator al angajamentului {cod}. Este nevoie întâi de o revizie "
                            "nouă de rezervare (documentul de fundamentare), apoi de recepție.")
            continue
        legal = _dec(ind["Angajament_Legal"])
        if tip in ("noua", "modificata") and legal > 0 and _dec(ind["Receptii"]) + suma > legal + TOLERANTA:
            warnings.append(f"{label}: recepțiile ({_cent(_dec(ind['Receptii']) + suma)}) depășesc angajamentul legal ({_cent(legal)}).")
        rows.append({"IdClsf": id_clsf, "CodAI": ind["CodAI"], "CodIndicator": ind["CodIndicator"], "Clsf": ind["Clsf"],
                     "ClsfSal": re.sub(r"[^A-Za-z0-9]", "", ind["ClsfSal"] or ind["IndicatorFX"] or ""), "SS": ind["SS"],
                     "Suma": float(suma)})
    return problems, warnings, rows


def verifica(dc, id_primita, role, cod, tip, alocari_brute, id_receptie=None):
    """Checks an allocation before the robot is started or a link written. {ok, probleme, avertismente, randuri}"""
    cere_contabil(role)
    if tip not in TIPURI_LEGATURA:
        raise EfEroare("Tipul legăturii nu este cunoscut.", "CAMP_INVALID", 400)
    alocari = _alocari(alocari_brute)
    with F._unit(dc, KE_SQL) as conn:
        cursor = conn.cursor(dictionary=True)
        _angajament(cursor, cod)
        problems, warnings, rows = _verifica(cursor, conn, id_primita, cod, alocari, tip)
        if tip == "modificata" and id_receptie:
            current = _valori_receptii(cursor, cod).get(id_receptie)
            if current is None:
                problems.append("Recepția de modificat nu există sau a fost ștearsă.")
            else:
                for item in rows:
                    old = current["valori"].get(item["IdClsf"], Decimal(0))
                    item["ValoareVeche"] = float(old)
                    item["ValoareNoua"] = float(_cent(old + _dec(item["Suma"])))
        return {"ok": not problems, "probleme": problems, "avertismente": warnings, "randuri": rows}


# ---------------------------------------------------------------------------------------------
# writing the links
# ---------------------------------------------------------------------------------------------
def leaga(dc, id_primita, role, user, cod, tip, id_receptie, idrh, idr, alocari_brute):
    """Writes the links of one reception. `existenta`: the reception must have the rest; `noua`: the reception is named by the numbers the
    marker reserved (written AFTER the robot saved it); `modificata`: the reception is the one just changed. Answers the new state."""
    cere_contabil(role)
    if tip not in TIPURI_LEGATURA:
        raise EfEroare("Tipul legăturii nu este cunoscut.", "CAMP_INVALID", 400)
    alocari = _alocari(alocari_brute)
    if tip == "noua":
        if not isinstance(idrh, int) or not isinstance(idr, int) or isinstance(idrh, bool) or isinstance(idr, bool):
            raise EfEroare("Recepția nouă nu are numerele rezervate de marcaj.", "CAMP_INVALID", 400)
    elif not isinstance(id_receptie, int) or isinstance(id_receptie, bool):
        raise EfEroare("Recepția nu a fost aleasă.", "CAMP_INVALID", 400)
    with F._unit(dc, KE_SQL) as conn:
        cursor = conn.cursor(dictionary=True)
        _angajament(cursor, cod)
        problems, _, _ = _verifica(cursor, conn, id_primita, cod, alocari, tip)
        if problems:
            raise _refuz(problems)
        if tip == "existenta":
            values, allocated = _valori_receptii(cursor, cod), _alocat_receptii(cursor, cod)
            reception = values.get(id_receptie)
            if reception is None:
                raise EfEroare("Recepția aleasă nu există sau a fost ștearsă.", "NU_EXISTA", 404)
            for id_clsf, suma in alocari:
                rest = reception["valori"].get(id_clsf, Decimal(0)) - allocated.get((id_receptie, id_clsf), Decimal(0))
                if _sign(suma) != _sign(rest) or abs(suma) > abs(rest) + TOLERANTA:
                    raise EfEroare(f"Recepția nu are destul rest pe clasificația {id_clsf}: rest {_cent(rest)}, de alocat {suma}. "
                                   "Modificați recepția sau faceți una nouă pentru diferență.", "REST_RECEPTIE", 409)
        elif tip == "modificata":
            cursor.execute("SELECT 1 FROM FX_Receptii_R WHERE IDRR = %s AND CodAngajament = %s", (id_receptie, cod))
            if cursor.fetchone() is None:
                raise EfEroare("Recepția modificată nu aparține angajamentului.", "NU_EXISTA", 404)
        for id_clsf, suma in alocari:
            cursor.execute("INSERT INTO EF_PrimiteReceptii (IdPrimita, CodAngajament, IdClsf, IDRR, IDRH, IDR, Suma, Tip, UN) "
                           "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                           (id_primita, cod, id_clsf, id_receptie if tip != "noua" else None, idrh if tip == "noua" else None,
                            idr if tip == "noua" else None, suma, tip, user))
        conn.commit()
    return starea(dc, id_primita, role)


def _refuz(problems):
    return EfEroare(" ".join(problems), "ALOCARE_INVALIDA", 409)


def sterge_legatura(dc, id_primita, role, id_legatura):
    cere_contabil(role)
    with F._unit(dc, KE_SQL) as conn:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("DELETE FROM EF_PrimiteReceptii WHERE IdLegatura = %s AND IdPrimita = %s", (id_legatura, id_primita))
        if cursor.rowcount == 0:
            raise EfEroare("Legătura nu există.", "NU_EXISTA", 404)
        conn.commit()
    return starea(dc, id_primita, role)
