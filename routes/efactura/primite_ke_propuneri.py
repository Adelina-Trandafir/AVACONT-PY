# routes/efactura/primite_ke_propuneri.py
"""
KE16: PROPOSALS to link the not yet linked received invoices of a supplier to existing receptions, in bulk (the accountant looks at them in a window and
confirms; nothing is written until she does). The rule is the one the Access preluare uses (primite_access_legare.py) with the operator's date rule:

  ONE invoice = ONE reception, whole (1=1): the total of the invoice (with its sign) equals what the reception still has uncovered, and the reception
  has rest on ONE classification only. No line is picked apart and no reception is split: a partial amount is never proposed.
  The angajamente looked at are those of the SUPPLIER (an angajament chosen in the page belongs to the supplier, not to the invoice): the DDFs it is partner of and the
  manual associations / links of any of its invoices (`_angajamente_candidate`, `_cods_furnizor`), plus the ones the window sends (picked in the page for it).
  A supplier with none gets a reason that says so, not «no reception».
  Several receptions with that rest: the one dated from the invoice date up to ZILE_DUPA_FACTURA days after it (the accountant receives an invoice within a
  couple of days). Exactly one such = proposed; none or several = no proposal, the window still lists all of them to pick from.
  Two invoices that would take the same reception: only an invoice whose date fits the window keeps it (and only if it is the only one); otherwise none does.
  An invoice whose lines already have another classification than the one the reception has rest on is left out: the choice of the accountant is not overwritten.

Writing (`aplica`) re-checks every pair against the database and writes all of them in one transaction, or none.
"""
import logging
from datetime import date, datetime

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


def _facturi_neasociate(conn, cursor, ids, extra=()):
    """The invoices of `ids` that have no link at all and can be linked whole: {IdPrimita: {row, total, data, cods, clsf_linii, reason}}.
    An invoice with links is left out (not «neasociata»); one whose lines cannot be linked whole is kept with `reason` and no angajamente.
    `extra` = codes of angajamente picked for the supplier in the window (they count for every invoice of the supplier)."""
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


def _candidate(item, receptii):
    """The receptions that have, whole, the value of the invoice: [{IDRR, CodAngajament, DataR, ..., IdClsf, Clsf, Rest, Zile, InFereastra}], by date."""
    found = []
    for cod in item["cods"]:
        values, allocated, names = receptii.of(cod)
        for reception in values.values():
            rest = {c: v - allocated.get((reception["IDRR"], c), 0) for c, v in reception["valori"].items()}
            rest = {c: r for c, r in rest.items() if abs(r) > TOLERANTA}
            if len(rest) != 1:
                continue
            id_clsf, amount = next(iter(rest.items()))
            if _sign(amount) != _sign(item["total"]) or abs(amount - item["total"]) > TOLERANTA:
                continue
            if item["clsf_linii"] is not None and item["clsf_linii"] != id_clsf:
                continue
            day = _zi(reception["DataR"])
            days = (day - item["data"]).days if day is not None and item["data"] is not None else None
            found.append({"IDRR": reception["IDRR"], "CodAngajament": cod, "DataR": day.isoformat() if day else None,
                          "TipReceptie": reception["TipReceptie"], "Descriere": reception["Descriere"], "IdClsf": id_clsf,
                          "Clsf": names.get(id_clsf), "Rest": _num(amount), "Zile": days,
                          "InFereastra": days is not None and 0 <= days <= ZILE_DUPA_FACTURA})
    found.sort(key=lambda c: (c["DataR"] or "", c["IDRR"]))
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


def propuneri(dc, role, id_facturi, angajamente=None):
    """What can be linked among the invoices `id_facturi`. {zile_dupa_factura, omise, facturi: [{IdPrimita, NrFact, DataFact, Furnizor, Total, candidate, propunere (IDRR|null), motiv}]}"""
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
            rows.append({"item": item, "id": id_primita, "cands": cands, "pick": pick, "motiv": reason})
        # two invoices on the same reception: the one whose date fits the window keeps it, alone; otherwise neither does
        wanting = {}
        for entry in rows:
            if entry["pick"] is not None:
                wanting.setdefault(entry["pick"]["IDRR"], []).append(entry)
        for group in wanting.values():
            if len(group) < 2:
                continue
            inside = [e for e in group if e["pick"]["InFereastra"]]
            keep = inside[0] if len(inside) == 1 else None
            for entry in group:
                if entry is not keep:
                    entry["pick"], entry["motiv"] = None, "Altă factură cu aceeași valoare vrea aceeași recepție: alegeți."
        facturi = []
        for entry in sorted(rows, key=lambda e: (e["item"]["data"] is None, e["item"]["data"], e["id"])):
            row = entry["item"]["row"]
            facturi.append({"IdPrimita": entry["id"], "NrFact": row["NrFact"], "DataFact": entry["item"]["data"].isoformat() if entry["item"]["data"] else None,
                            "Furnizor": row["DenumireP"], "Total": float(entry["item"]["total"]), "candidate": entry["cands"],
                            "propunere": entry["pick"]["IDRR"] if entry["pick"] else None, "motiv": "" if entry["pick"] else entry["motiv"]})
        return {"zile_dupa_factura": ZILE_DUPA_FACTURA, "omise": len(ids) - len(items), "facturi": facturi}


def aplica(dc, role, user, perechi, angajamente=None):
    """Writes the links the accountant confirmed: perechi = [{IdPrimita, CodAngajament, IDRR}]. Every pair must still be a valid whole link (the invoice still has
    none, the reception still has exactly its value); all are written in one transaction, or none. {legate}"""
    cere_contabil(role)
    if not isinstance(perechi, list) or not perechi or len(perechi) > MAX_FACTURI:
        raise EfEroare(f"«legaturi» trebuie sa fie o lista de 1 pana la {MAX_FACTURI} elemente.", "CAMP_INVALID", 400)
    seen_invoice, seen_reception = set(), set()
    for pair in perechi:
        if (not isinstance(pair, dict) or set(pair) != {"IdPrimita", "CodAngajament", "IDRR"} or isinstance(pair["IdPrimita"], bool)
                or isinstance(pair["IDRR"], bool) or not isinstance(pair["IdPrimita"], int) or not isinstance(pair["IDRR"], int)
                or not isinstance(pair["CodAngajament"], str) or not pair["CodAngajament"].strip()):
            raise EfEroare("O legătură nu are forma cerută.", "CAMP_INVALID", 400)
        if pair["IdPrimita"] in seen_invoice or pair["IDRR"] in seen_reception:
            raise EfEroare("O factură sau o recepție apare de două ori: se leagă 1 la 1.", "CAMP_INVALID", 400)
        seen_invoice.add(pair["IdPrimita"])
        seen_reception.add(pair["IDRR"])
    with F._unit(dc, KE_SQL) as conn:
        cursor = conn.cursor(dictionary=True)
        items = _facturi_neasociate(conn, cursor, [p["IdPrimita"] for p in perechi], _cods_existente(cursor, angajamente))
        receptii = _Receptii(cursor)
        writes = []
        for pair in perechi:
            item = items.get(pair["IdPrimita"])
            if item is None or item["reason"]:
                raise EfEroare(f"Factura {pair['IdPrimita']} nu mai poate fi legată (are deja o legătură sau liniile ei nu o permit). Citiți din nou propunerile.",
                               "NU_MAI_ESTE_VALIDA", 409)
            match = next((c for c in _candidate(item, receptii)
                          if c["IDRR"] == pair["IDRR"] and c["CodAngajament"].lower() == pair["CodAngajament"].strip().lower()), None)
            if match is None:
                raise EfEroare(f"Recepția aleasă pentru factura {item['row']['NrFact']} nu mai are exact valoarea facturii. Citiți din nou propunerile.",
                               "NU_MAI_ESTE_VALIDA", 409)
            writes.append((pair["IdPrimita"], match, item["total"]))
        for id_primita, match, total in writes:
            cursor.execute("UPDATE EF_PrimiteLinii SET IdClsf = %s WHERE IdPrimita = %s AND IdClsf IS NULL", (match["IdClsf"], id_primita))
            cursor.execute("INSERT INTO EF_PrimiteReceptii (IdPrimita, CodAngajament, IdClsf, IDRR, Suma, Tip, UN) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                           (id_primita, match["CodAngajament"], match["IdClsf"], match["IDRR"], total, TIP_LEGATURA, user))
        conn.commit()
    return {"legate": len(writes)}
