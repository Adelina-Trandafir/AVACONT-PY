# routes/efactura/primite_ke_propuneri.py
"""
KE16: PROPOSALS to link the not yet linked received invoices of a supplier to existing receptions, in bulk (the accountant looks at them in a window and
confirms; nothing is written until she does). The rule is the one the Access preluare uses (primite_access_legare.py) with the operator's date rule:

  The proposal (what comes ticked): ONE invoice = ONE reception, whole: the total of the invoice (with its sign) equals what the reception still has uncovered, and
  the reception has rest on ONE classification only. Several receptions with that rest: the one dated from the invoice date up to ZILE_DUPA_FACTURA days after it
  (the accountant receives an invoice within a couple of days); exactly one such = proposed; none or several = no proposal. Two invoices that would take the same
  reception: only an invoice whose date fits the window keeps it (and only if it is the only one); otherwise none does.

  What the accountant may do in the window (20.10.2026): pick, for each invoice, the angajament (when the supplier has several) and the reception, and write the
  VALUE she receives from the invoice (`Suma`, not above the invoice total, same sign). A reception may serve several invoices as long as its rest on the
  classification covers the sums; the window works the rest out, the server checks it again in `aplica`. So the answer carries ALL the receptions with rest of the
  angajamente of the invoices (per reception AND classification: `receptii`), not only the ones that fit whole.

  The angajamente looked at are those of the SUPPLIER (an angajament chosen in the page belongs to the supplier, not to the invoice): the DDFs it is partner of, the
  links supplier <-> angajament and the manual associations / links of any of its invoices (`_angajamente_candidate`, `_cods_furnizor`), plus the ones the window sends.
  A supplier with none gets a reason that says so, not «no reception».
  An invoice whose lines already have another classification than the one of the reception is not linked to it: the choice of the accountant is not overwritten.

Writing (`aplica`) re-checks every pair against the database and writes all of them in one transaction, or none.
"""
import logging
import math
from datetime import date, datetime
from decimal import Decimal

from . import facturi as F
from . import primite
from .primite_ke import (KE_SQL, TOLERANTA, _SQL_INDICATORI, _alocat_receptii, _angajamente_candidate, _cent, _dec, _factura_si_linii, _legaturi, _num,
                         _sign, _valori_receptii, cere_contabil)
from .tokens import EfEroare

logger = logging.getLogger(__name__)

ZILE_DUPA_FACTURA = 2
MAX_FACTURI = 500
MAX_ANGAJAMENTE = 50
TIP_LEGATURA = "existenta"
FARA_ANGAJAMENT = "Furnizorul nu are niciun angajament: alegeți-l în tabelul «Angajamente» al paginii și deschideți din nou propunerile."
CHEI_LEGATURA = {"IdPrimita", "CodAngajament", "IDRR", "IdClsf", "Suma"}


def _zi(value):
    if isinstance(value, datetime):
        return value.date()
    return value if isinstance(value, date) else None


def _ids(values, what):
    if not isinstance(values, list) or not values or len(values) > MAX_FACTURI:
        raise EfEroare(f"{what} trebuie sa fie o lista de 1 pana la {MAX_FACTURI} elemente.", "CAMP_INVALID", 400)
    for value in values:
        if isinstance(value, bool) or not isinstance(value, int):
            raise EfEroare(f"{what} contine un element care nu este un numar.", "CAMP_INVALID", 400)
    return values


class _Receptii:
    """The receptions of the angajamente read so far (one read per angajament)."""

    def __init__(self, cursor):
        self._cursor = cursor
        self._cache = {}

    def of(self, cod):
        if cod not in self._cache:
            cursor = self._cursor
            values, allocated = _valori_receptii(cursor, cod), _alocat_receptii(cursor, cod)
            cursor.execute(_SQL_INDICATORI, (cod,))
            names = {i["IdClsf"]: i["Clsf"] for i in cursor.fetchall() if i["IdClsf"] is not None}
            self._cache[cod] = (values, allocated, names)
        return self._cache[cod]

    def rows(self, cod):
        """The receptions of the angajament that still have rest, ONE ROW PER RECEPTION AND CLASSIFICATION:
        [{IDRR, CodAngajament, DataR (date|None), TipReceptie, Descriere, IdClsf, Clsf, Amount (Decimal, signed), Unica}]; `Unica` = the reception has rest on that
        classification only."""
        values, allocated, names = self.of(cod)
        out = []
        for reception in values.values():
            rest = {c: v - allocated.get((reception["IDRR"], c), 0) for c, v in reception["valori"].items()}
            rest = {c: r for c, r in rest.items() if abs(r) > TOLERANTA}
            for id_clsf, amount in sorted(rest.items()):
                out.append({"IDRR": reception["IDRR"], "CodAngajament": cod, "DataR": _zi(reception["DataR"]), "TipReceptie": reception["TipReceptie"],
                            "Descriere": reception["Descriere"], "IdClsf": id_clsf, "Clsf": names.get(id_clsf), "Amount": amount, "Unica": len(rest) == 1})
        return out


def _cods_furnizor(cursor, cui):
    """The angajamente tied to a SUPPLIER, whichever of its invoices they were picked on (an angajament chosen in the page belongs to the supplier, not to the
    invoice): the manual DDF associations of any invoice of that tax code and the receptions any of them is already linked to."""
    if not cui:
        return []
    found = []
    cursor.execute("SELECT DISTINCT d.CodAngajament FROM EF_PrimiteAsocieri a JOIN EF_Primite p ON p.IdPrimita = a.IdPrimita "
                   "JOIN FX_DDF d ON d.IDDF = a.IDDF WHERE p.CuiNormalizat = %s", (cui,))
    found += [r["CodAngajament"] for r in cursor.fetchall() if r["CodAngajament"]]
    cursor.execute("SELECT DISTINCT l.CodAngajament FROM EF_PrimiteReceptii l JOIN EF_Primite p ON p.IdPrimita = l.IdPrimita WHERE p.CuiNormalizat = %s", (cui,))
    found += [r["CodAngajament"] for r in cursor.fetchall() if r["CodAngajament"]]
    return found


def _cods_existente(cursor, cods):
    """Of the codes the window sends (the angajamente picked there for the supplier), the ones that exist."""
    clean = []
    for cod in cods or []:
        if not isinstance(cod, str) or not cod.strip() or len(cod) > 255 or len(clean) >= MAX_ANGAJAMENTE:
            raise EfEroare("«angajamente» trebuie sa fie o lista de coduri.", "CAMP_INVALID", 400)
        clean.append(cod.strip())
    if not clean:
        return []
    cursor.execute("SELECT CodAngajament FROM FX_Angajamente WHERE CodAngajament IN (" + ", ".join(["%s"] * len(clean)) + ")", tuple(clean))
    return [r["CodAngajament"] for r in cursor.fetchall()]


def _etichete(cursor, cods):
    """{cod: the name the accountant recognises} -- the alias given in K-BOT and the description of the site, as the page of the receptions shows them."""
    if not cods:
        return {}
    cursor.execute("SELECT CodAngajament, Descriere, Alias FROM FX_Angajamente WHERE CodAngajament IN (" + ", ".join(["%s"] * len(cods)) + ")", tuple(cods))
    names = {}
    for item in cursor.fetchall():
        alias, desc = (item["Alias"] or "").strip(), (item["Descriere"] or "").strip()
        names[item["CodAngajament"]] = f"{alias} — {desc}" if alias and desc and alias != desc else (alias or desc)
    return names


def _facturi_neasociate(conn, cursor, ids, extra=()):
    """The invoices of `ids` that have no link at all and can be linked: {IdPrimita: {row, total, data, cods, clsf_linii, reason}}.
    An invoice with links is left out (not «neasociata»); one whose lines cannot be linked (they do not add up to the total, or carry two classifications) is kept
    with `reason` and no angajamente. `extra` = codes of angajamente picked for the supplier in the window (they count for every invoice of the supplier)."""
    out = {}
    for id_primita in ids:
        row = primite._row(cursor, id_primita)
        if _legaturi(cursor, id_primita):
            continue
        sign = int(row["Semn"] or 1)
        total = _cent(_dec(row["Total"]) * sign)
        item = {"row": row, "total": total, "data": _zi(row["DataFact"]), "cods": [], "clsf_linii": None, "reason": ""}
        out[id_primita] = item
        if total == 0:
            item["reason"] = "Factura are valoarea 0."
            continue
        _, lines = _factura_si_linii(conn, cursor, id_primita)
        explained = _cent(sum((line["ValoareCuTVA"] for line in lines), 0) * sign)
        if not lines or abs(explained - total) > TOLERANTA:
            item["reason"] = "Liniile facturii nu acoperă totalul ei (legați-o din pagina de recepții)."
            continue
        classes = {line["IdClsf"] for line in lines if line["IdClsf"] is not None}
        if len(classes) > 1:
            item["reason"] = "Liniile facturii au clasificații diferite (legați-o din pagina de recepții)."
            continue
        item["clsf_linii"] = next(iter(classes)) if classes else None
        cods, seen = [], set()
        for cod in [a["CodAngajament"] for a in _angajamente_candidate(cursor, row, [])] + _cods_furnizor(cursor, row["CuiNormalizat"]) + list(extra):
            if cod.lower() not in seen:
                seen.add(cod.lower())
                cods.append(cod)
        item["cods"] = cods
    return out


def _se_potriveste(item, row):
    """Can the invoice take something from this reception row: same sign, and the classification of its lines (when chosen) is the row's."""
    return _sign(row["Amount"]) == _sign(item["total"]) and (item["clsf_linii"] is None or item["clsf_linii"] == row["IdClsf"])


def _candidate(item, receptii):
    """The receptions that have, WHOLE, the value of the invoice (rest on one classification only, equal to the total): the rows of `Receptii.rows`, by date,
    with `Zile` (days from the invoice date) and `InFereastra`."""
    found = []
    for cod in item["cods"]:
        for row in receptii.rows(cod):
            if not row["Unica"] or not _se_potriveste(item, row) or abs(row["Amount"] - item["total"]) > TOLERANTA:
                continue
            days = (row["DataR"] - item["data"]).days if row["DataR"] is not None and item["data"] is not None else None
            found.append(dict(row, Zile=days, InFereastra=days is not None and 0 <= days <= ZILE_DUPA_FACTURA))
    found.sort(key=lambda c: (c["DataR"].isoformat() if c["DataR"] else "", c["IDRR"]))
    return found


def _alege(cands):
    """The reception proposed among the candidates, or None and the reason."""
    if not cands:
        return None, "Nicio recepție cu același rest ca valoarea facturii."
    if len(cands) == 1:
        return cands[0], ""
    inside = [c for c in cands if c["InFereastra"]]
    if len(inside) == 1:
        return inside[0], ""
    if not inside:
        return None, f"Mai multe recepții cu aceeași valoare, niciuna în cel mult {ZILE_DUPA_FACTURA} zile după data facturii: alegeți."
    return None, f"Mai multe recepții cu aceeași valoare în cel mult {ZILE_DUPA_FACTURA} zile după data facturii: alegeți."


def _angajament_implicit(item, pick, receptii):
    """The angajament shown first for the invoice: the one of the proposal, else the first that has a reception row it could take, else the first."""
    if pick is not None:
        return pick["CodAngajament"]
    for cod in item["cods"]:
        if any(_se_potriveste(item, row) for row in receptii.rows(cod)):
            return cod
    return item["cods"][0] if item["cods"] else None


def propuneri(dc, role, id_facturi, angajamente=None):
    """What can be linked among the invoices `id_facturi`.
    {zile_dupa_factura, omise, angajamente: [{CodAngajament, Descriere}], receptii: [{IDRR, CodAngajament, DataR, TipReceptie, Descriere, IdClsf, Clsf, Rest}],
     facturi: [{IdPrimita, NrFact, DataFact, Furnizor, Total, IdClsfLinii, angajamente: [cod], angajament, propunere: {IDRR, IdClsf, CodAngajament} | null, motiv}]}"""
    cere_contabil(role)
    ids = _ids(id_facturi, "«facturi»")
    with F._unit(dc, KE_SQL) as conn:
        cursor = conn.cursor(dictionary=True)
        items = _facturi_neasociate(conn, cursor, ids, _cods_existente(cursor, angajamente))
        receptii = _Receptii(cursor)
        rows = []
        for id_primita, item in items.items():
            cands = _candidate(item, receptii) if not item["reason"] else []
            if item["reason"]:
                pick, reason = None, item["reason"]
            elif not item["cods"]:
                pick, reason = None, FARA_ANGAJAMENT
            else:
                pick, reason = _alege(cands)
            rows.append({"item": item, "id": id_primita, "pick": pick, "motiv": reason})
        # two invoices on the same reception: the one whose date fits the window keeps it, alone; otherwise neither does
        wanting = {}
        for entry in rows:
            if entry["pick"] is not None:
                wanting.setdefault((entry["pick"]["CodAngajament"], entry["pick"]["IDRR"], entry["pick"]["IdClsf"]), []).append(entry)
        for group in wanting.values():
            if len(group) < 2:
                continue
            inside = [e for e in group if e["pick"]["InFereastra"]]
            keep = inside[0] if len(inside) == 1 else None
            for entry in group:
                if entry is not keep:
                    entry["pick"], entry["motiv"] = None, "Altă factură cu aceeași valoare vrea aceeași recepție: alegeți."
        cods = []
        for entry in rows:
            cods += [c for c in entry["item"]["cods"] if c not in cods]
        names = _etichete(cursor, cods)
        facturi = []
        for entry in sorted(rows, key=lambda e: (e["item"]["data"] is None, e["item"]["data"], e["id"])):
            item, pick = entry["item"], entry["pick"]
            row = item["row"]
            facturi.append({"IdPrimita": entry["id"], "NrFact": row["NrFact"], "DataFact": item["data"].isoformat() if item["data"] else None,
                            "Furnizor": row["DenumireP"], "Total": float(item["total"]), "IdClsfLinii": item["clsf_linii"], "angajamente": item["cods"],
                            "angajament": _angajament_implicit(item, pick, receptii),
                            "propunere": {"IDRR": pick["IDRR"], "IdClsf": pick["IdClsf"], "CodAngajament": pick["CodAngajament"]} if pick else None,
                            "motiv": "" if pick else entry["motiv"]})
        lista = []
        for cod in cods:
            for row in receptii.rows(cod):
                lista.append({"IDRR": row["IDRR"], "CodAngajament": cod, "DataR": row["DataR"].isoformat() if row["DataR"] else None,
                              "TipReceptie": row["TipReceptie"], "Descriere": row["Descriere"], "IdClsf": row["IdClsf"], "Clsf": row["Clsf"],
                              "Rest": _num(row["Amount"])})
        return {"zile_dupa_factura": ZILE_DUPA_FACTURA, "omise": len(ids) - len(items),
                "angajamente": [{"CodAngajament": cod, "Descriere": names.get(cod, "")} for cod in cods], "receptii": lista, "facturi": facturi}


def _suma(value):
    """The `Suma` of a pair as a Decimal in cents; refuses what is not a finite number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or (isinstance(value, float) and not math.isfinite(value)):
        raise EfEroare("O legătură are o sumă care nu este un număr.", "CAMP_INVALID", 400)
    return _cent(Decimal(str(value)))


def aplica(dc, role, user, perechi, angajamente=None):
    """Writes the links the accountant confirmed: perechi = [{IdPrimita, CodAngajament, IDRR, IdClsf, Suma}]. Every pair must still be valid (the invoice still has no
    link, the angajament is one of the supplier's, `Suma` has the sign of the invoice and is not above its total, the classification is the one of the invoice's lines
    when they have one) and the pairs that take from the same reception and classification must fit together in its rest. All are written in one transaction, or none.
    {legate}"""
    cere_contabil(role)
    if not isinstance(perechi, list) or not perechi or len(perechi) > MAX_FACTURI:
        raise EfEroare(f"«legaturi» trebuie sa fie o lista de 1 pana la {MAX_FACTURI} elemente.", "CAMP_INVALID", 400)
    seen_invoice = set()
    for pair in perechi:
        if (not isinstance(pair, dict) or set(pair) != CHEI_LEGATURA or isinstance(pair["IdPrimita"], bool) or isinstance(pair["IDRR"], bool)
                or isinstance(pair["IdClsf"], bool) or not isinstance(pair["IdPrimita"], int) or not isinstance(pair["IDRR"], int)
                or not isinstance(pair["IdClsf"], int) or not isinstance(pair["CodAngajament"], str) or not pair["CodAngajament"].strip()):
            raise EfEroare("O legătură nu are forma cerută.", "CAMP_INVALID", 400)
        _suma(pair["Suma"])
        if pair["IdPrimita"] in seen_invoice:
            raise EfEroare("O factură apare de două ori: ea se leagă de o singură recepție.", "CAMP_INVALID", 400)
        seen_invoice.add(pair["IdPrimita"])
    with F._unit(dc, KE_SQL) as conn:
        cursor = conn.cursor(dictionary=True)
        items = _facturi_neasociate(conn, cursor, [p["IdPrimita"] for p in perechi], _cods_existente(cursor, angajamente))
        receptii = _Receptii(cursor)
        taken = {}
        writes = []
        for pair in perechi:
            item = items.get(pair["IdPrimita"])
            if item is None or item["reason"]:
                raise EfEroare(f"Factura {pair['IdPrimita']} nu mai poate fi legată (are deja o legătură sau liniile ei nu o permit). Citiți din nou propunerile.",
                               "NU_MAI_ESTE_VALIDA", 409)
            nr = item["row"]["NrFact"]
            cod = next((c for c in item["cods"] if c.lower() == pair["CodAngajament"].strip().lower()), None)
            if cod is None:
                raise EfEroare(f"Angajamentul ales pentru factura {nr} nu mai este al furnizorului. Citiți din nou propunerile.", "NU_MAI_ESTE_VALIDA", 409)
            suma = _suma(pair["Suma"])
            if suma == 0 or _sign(suma) != _sign(item["total"]) or abs(suma) > abs(item["total"]) + TOLERANTA:
                raise EfEroare(f"Suma recepționată a facturii {nr} trebuie să aibă semnul facturii și să nu depășească valoarea ei.", "CAMP_INVALID", 400)
            if item["clsf_linii"] is not None and item["clsf_linii"] != pair["IdClsf"]:
                raise EfEroare(f"Liniile facturii {nr} au deja altă clasificație decât cea a recepției alese. Citiți din nou propunerile.", "NU_MAI_ESTE_VALIDA", 409)
            match = next((r for r in receptii.rows(cod) if r["IDRR"] == pair["IDRR"] and r["IdClsf"] == pair["IdClsf"]), None)
            if match is None or _sign(match["Amount"]) != _sign(suma):
                raise EfEroare(f"Recepția aleasă pentru factura {nr} nu mai are rest pe clasificația aleasă. Citiți din nou propunerile.", "NU_MAI_ESTE_VALIDA", 409)
            key = (cod.lower(), match["IDRR"], match["IdClsf"])
            taken[key] = taken.get(key, Decimal(0)) + suma
            if abs(taken[key]) > abs(match["Amount"]) + TOLERANTA:
                raise EfEroare(f"Recepția aleasă pentru factura {nr} nu mai are destul rest pentru toate sumele alese. Citiți din nou propunerile.",
                               "NU_MAI_ESTE_VALIDA", 409)
            writes.append((pair["IdPrimita"], cod, match["IDRR"], match["IdClsf"], suma))
        for id_primita, cod, id_rr, id_clsf, suma in writes:
            cursor.execute("UPDATE EF_PrimiteLinii SET IdClsf = %s WHERE IdPrimita = %s AND IdClsf IS NULL", (id_clsf, id_primita))
            cursor.execute("INSERT INTO EF_PrimiteReceptii (IdPrimita, CodAngajament, IdClsf, IDRR, Suma, Tip, UN) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                           (id_primita, cod, id_clsf, id_rr, suma, TIP_LEGATURA, user))
        conn.commit()
    return {"legate": len(writes)}
