# routes/efactura/primite_vercon.py
"""
EV: a received e-invoice SENT to VERCON (slice 00EF-45 = EV01, plan docs/EV_PLAN.md). The accountant sends the invoice; the Administrator takes
it over in «Adaugare NIR» and the sending closes with the NIR(s) he created (VERCON side, slices EV04/EV05). The rules live here, the
routes in primite_vercon_routes.py. Every WRITE is for the role «Contabil» only (the session's role, checked HERE, not only on the screen).

NOTHING IS COPIED. The invoice stays in EF_PrimiteLinii / EF_Primite; a sending is a row of EF_PrimiteVercon plus one row of
EF_PrimiteVerconLinii for EVERY line of the invoice (decision D10: all lines are sent) holding the account the accountant proposes for it
(VC_Masini.IdMasina, any subunit; NULL = the Administrator chooses; decision D9: a proposal, not an order).

ACTIVE SENDING. At most one per invoice, Stare TRIMISA or PRELUATA. RETURNATA (sent back by the Administrator) and RETRASA (taken back by the
accountant) stay as history, and the invoice can be sent again.

EFFECTIVE STATE (decision D5). A sending marked PRELUATA whose NIRs (EF_PrimiteVerconNir) are all cancelled (VC_NIR.Anulat = 1) or no longer exist
counts as TRIMISA again. It is decided when READING, so it does not depend on every way a NIR can be cancelled.

AMOUNTS. The value of a line WITH VAT comes from primite_ke.valori_cu_tva (the same rule the receptions use); it needs KE_01_primite_receptii.sql.

NOT IN THIS SLICE: the invoices of kind credit note («NC», Semn -1) cannot be sent (decision D3); the VERCON side (list, return, take over) is EV04 / EV05.
"""
import logging

from . import facturi as F
from . import primite, primite_ke
from .tokens import EfEroare

logger = logging.getLogger(__name__)

EV_SQL = "sql/EV_01_primite_vercon.sql"
STARI_ACTIVE = ("TRIMISA", "PRELUATA")
MAX_OBSERVATIE = 500

# The CUI of a partner, normalised the way EF_Primite.CuiNormalizat is (no spaces / signs, no leading «RO» before a digit); same expression as primite.py.
_PARTENER_CUI = "UPPER(REGEXP_REPLACE(REGEXP_REPLACE(p.CodFiscal, '[^[:alnum:]]', ''), '^RO([0-9])', '\\\\1'))"


# ---------------------------------------------------------------------------------------------
# the accounts of VERCON, for the tree of the accountant
# ---------------------------------------------------------------------------------------------
def _conturi(cursor):
    """{(SubunitId, GestiuneId): gestiune} and {IdMasina: account} for every ACTIVE gestiune of an active subunit (MF and OI included, decision D8).
    An account is offered when the type of the gestiune accepts it (the rule of VERCON's own «Adaugare NIR»: routes.vercon.receipt_products.warehouse)."""
    from routes.vercon.receipt_products import warehouse  # lazy: no import-time coupling to the VERCON package
    cursor.execute("SELECT g.SubunitId, g.GestiuneId, g.Name AS Gestiune, g.Type AS Tip, s.Name AS Subunitate "
                   "FROM VC_Gestiuni g JOIN VC_Subunitati s ON s.SubunitId = g.SubunitId "
                   "WHERE g.Active = 1 AND s.Active = 1 ORDER BY s.Name, g.Name, g.GestiuneId")
    gestiuni = {}
    for row in cursor.fetchall():
        gestiuni[(row["SubunitId"], row["GestiuneId"])] = {
            "SubunitId": row["SubunitId"], "GestiuneId": row["GestiuneId"], "Subunitate": row["Subunitate"] or "",
            "Gestiune": row["Gestiune"] or "", "Tip": row["Tip"] or "", "conturi": []}
    conturi = {}
    for (subunit, gestiune), item in gestiuni.items():
        cursor.execute("SELECT IdMasina, Masina, TIP FROM VC_Masini WHERE SubunitId = %s AND GestiuneId = %s AND Activ = 1 ORDER BY Masina, IdMasina",
                       (subunit, gestiune))
        for account in cursor.fetchall():
            if not warehouse(item["Tip"], account["TIP"]):
                continue
            entry = {"IdMasina": account["IdMasina"], "Masina": account["Masina"] or "", "SubunitId": subunit, "GestiuneId": gestiune}
            item["conturi"].append({"IdMasina": entry["IdMasina"], "Masina": entry["Masina"]})
            conturi[account["IdMasina"]] = entry
    return gestiuni, conturi


def conturi_arbore(dc):
    """The tree: gestiuni (not choosable) with their accounts. Only gestiuni that have at least one account are listed."""
    with F._unit(dc, EV_SQL) as conn:
        cursor = conn.cursor(dictionary=True)
        gestiuni, _ = _conturi(cursor)
        return {"gestiuni": [item for item in gestiuni.values() if item["conturi"]]}


def cere_contabil(role):
    primite_ke.cere_contabil(role)


# ---------------------------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------------------------
_SQL_TRIMITERI = ("SELECT IdTrimitere, IdPrimita, Stare, Observatie, UnTrimis, DataTrimis, MotivReturnare, UnReturnat, DataReturnat, UnPreluat, DataPreluat "
                  "FROM EF_PrimiteVercon WHERE IdPrimita = %s ORDER BY IdTrimitere DESC")

_SQL_NIR = ("SELECT n.IdTrimitere, n.SubunitId, n.GestiuneId, n.ExercitiuId, n.IdIntrare, v.Numar, v.NumarSub, v.Data, "
            "(v.IdIntrare IS NOT NULL) AS Exista, COALESCE(v.Anulat, 0) AS Anulat, g.Name AS Gestiune "
            "FROM EF_PrimiteVerconNir n "
            "LEFT JOIN VC_NIR v ON v.SubunitId = n.SubunitId AND v.GestiuneId = n.GestiuneId AND v.ExercitiuId = n.ExercitiuId AND v.IdIntrare = n.IdIntrare "
            "LEFT JOIN VC_Gestiuni g ON g.SubunitId = n.SubunitId AND g.GestiuneId = n.GestiuneId "
            "WHERE n.IdTrimitere IN ({marks}) ORDER BY n.IdTrimitere, n.SubunitId, n.GestiuneId, n.IdIntrare")


def _nir_ale(cursor, ids):
    """{IdTrimitere: [NIR]} -- every NIR of the sendings, with `Valid` (exists and not cancelled)."""
    out = {i: [] for i in ids}
    if not ids:
        return out
    cursor.execute(_SQL_NIR.format(marks=", ".join(["%s"] * len(ids))), tuple(ids))
    for row in cursor.fetchall():
        out[row["IdTrimitere"]].append({
            "SubunitId": row["SubunitId"], "GestiuneId": row["GestiuneId"], "Gestiune": row["Gestiune"] or "", "ExercitiuId": row["ExercitiuId"],
            "IdIntrare": row["IdIntrare"], "Numar": row["Numar"], "NumarSub": row["NumarSub"],
            "Data": row["Data"].isoformat() if row["Data"] is not None else None,
            "Anulat": bool(row["Anulat"]), "Valid": bool(row["Exista"]) and not row["Anulat"]})
    return out


def stare_efectiva(stored, nirs):
    """(state, returned). A PRELUATA sending with no valid NIR left is TRIMISA again (decision D5)."""
    if stored == "PRELUATA" and not any(nir["Valid"] for nir in nirs):
        return "TRIMISA", True
    return stored, False


def _trimitere_json(row, nirs):
    state, back = stare_efectiva(row["Stare"], nirs)
    return {
        "IdTrimitere": row["IdTrimitere"], "Stare": state, "StareSalvata": row["Stare"], "Revenita": back,
        "Observatie": row["Observatie"], "UnTrimis": row["UnTrimis"], "DataTrimis": row["DataTrimis"].isoformat() if row["DataTrimis"] else None,
        "MotivReturnare": row["MotivReturnare"], "UnReturnat": row["UnReturnat"],
        "DataReturnat": row["DataReturnat"].isoformat() if row["DataReturnat"] else None,
        "UnPreluat": row["UnPreluat"], "DataPreluat": row["DataPreluat"].isoformat() if row["DataPreluat"] else None,
        "nir": nirs}


def _partener(cursor, cui):
    """The VISIBLE partner of the unit with this normalised CUI, or None (VERCON's «Adaugare NIR» takes only visible partners that have a tax code)."""
    if not cui:
        return None
    cursor.execute("SELECT p.IdPartener, p.DenumirePartener FROM Parteneri p WHERE " + _PARTENER_CUI + " = %s AND COALESCE(p.Ascuns, 0) = 0 "
                   "AND NULLIF(TRIM(p.CodFiscal), '') IS NOT NULL ORDER BY p.IdPartener LIMIT 1", (cui,))
    row = cursor.fetchone()
    return {"IdPartener": row["IdPartener"], "Denumire": row["DenumirePartener"] or ""} if row else None


def _duplicate(cursor, partner, nr_fact, data_fact):
    """The NIRs already recorded for the same supplier, invoice number and date (a warning, not a refusal)."""
    if partner is None or not nr_fact or data_fact is None:
        return []
    cursor.execute("SELECT v.SubunitId, v.GestiuneId, v.ExercitiuId, v.IdIntrare, v.Numar, v.NumarSub, v.Data, v.Anulat, g.Name AS Gestiune "
                   "FROM VC_NIR v LEFT JOIN VC_Gestiuni g ON g.SubunitId = v.SubunitId AND g.GestiuneId = v.GestiuneId "
                   "WHERE v.IdPartener = %s AND v.NrFact = %s AND DATE(v.DataFc) = %s AND COALESCE(v.Anulat, 0) = 0 ORDER BY v.Data DESC LIMIT 10",
                   (partner["IdPartener"], nr_fact, data_fact))
    return [{"Gestiune": r["Gestiune"] or "", "Numar": r["Numar"], "NumarSub": r["NumarSub"], "IdIntrare": r["IdIntrare"],
             "Data": r["Data"].isoformat() if r["Data"] is not None else None} for r in cursor.fetchall()]


def _linii(conn, cursor, id_primita, gestiuni, conturi, proposals):
    """The lines of the invoice with their values WITH VAT and, when a sending exists, the proposed account (`proposals` = {IdLinie: IdMasina})."""
    row, lines = primite_ke._factura_si_linii(conn, cursor, id_primita)
    cursor.execute("SELECT IdLinie, Unit, Cant, Pret FROM EF_PrimiteLinii WHERE IdPrimita = %s", (id_primita,))
    extra = {r["IdLinie"]: r for r in cursor.fetchall()}
    sign = int(row["Semn"] or 1)
    out = []
    for line in lines:
        more = extra.get(line["IdLinie"], {})
        id_masina = proposals.get(line["IdLinie"])
        account = conturi.get(id_masina) if id_masina is not None else None
        gestiune = gestiuni.get((account["SubunitId"], account["GestiuneId"])) if account else None
        out.append({"IdLinie": line["IdLinie"], "NrLinie": line["NrLinie"], "Denumire": line["Denumire"], "Cpv": line["Cpv"],
                    "Unit": more.get("Unit"), "Cant": primite_ke._num(more.get("Cant")), "Pret": primite_ke._num(more.get("Pret")),
                    "Valoare": primite_ke._num(line["Valoare"]), "ValoareCuTVA": primite_ke._num(line["ValoareCuTVA"] * sign),
                    "IdMasina": id_masina, "Cont": account["Masina"] if account else None,
                    "Gestiune": ("%s / %s" % (gestiune["Subunitate"], gestiune["Gestiune"])) if gestiune else None})
    return row, out


def starea(dc, id_primita, role):
    """Everything the «Trimite la Vercon» window shows for one invoice: the lines with their proposed accounts, the active sending, the history."""
    with F._unit(dc, EV_SQL) as conn:
        cursor = conn.cursor(dictionary=True)
        return _starea(conn, cursor, id_primita, role)


def _starea(conn, cursor, id_primita, role):
    gestiuni, conturi = _conturi(cursor)
    cursor.execute(_SQL_TRIMITERI, (id_primita,))
    rows = cursor.fetchall()
    nirs = _nir_ale(cursor, [r["IdTrimitere"] for r in rows])
    sendings = [_trimitere_json(r, nirs[r["IdTrimitere"]]) for r in rows]
    active = next((s for s, r in zip(sendings, rows) if r["Stare"] in STARI_ACTIVE), None)
    proposals = {}
    if active is not None:
        cursor.execute("SELECT IdLinie, IdMasina FROM EF_PrimiteVerconLinii WHERE IdTrimitere = %s", (active["IdTrimitere"],))
        proposals = {r["IdLinie"]: r["IdMasina"] for r in cursor.fetchall()}
    row, lines = _linii(conn, cursor, id_primita, gestiuni, conturi, proposals)
    partner = _partener(cursor, row["CuiNormalizat"])
    reason = None
    if row["Tip"] != "FC" or int(row["Semn"] or 1) != 1:
        reason = "Nota de credit (storno) nu se trimite la Vercon."
    elif not lines:
        reason = "Factura nu are linii."
    elif active is not None:
        reason = "Factura este deja trimisă la Vercon."
    elif partner is None:
        reason = "Furnizorul nu este în lista de parteneri a unității. Asociați-l întâi."
    sign = int(row["Semn"] or 1)
    return {
        "poate_scrie": (role or "").strip().lower() == primite_ke.ROL_CONTABIL.lower(),
        "poate_trimite": reason is None, "motiv": reason,
        "factura": {"IdPrimita": id_primita, "NrFact": row["NrFact"], "DataFact": row["DataFact"].isoformat() if row["DataFact"] else None,
                    "Furnizor": row["DenumireP"], "CuiNormalizat": row["CuiNormalizat"], "Total": primite_ke._num(primite_ke._dec(row["Total"]) * sign)},
        "partener": partner, "duplicate": _duplicate(cursor, partner, row["NrFact"], row["DataFact"]),
        "trimitere": active, "istoric": [s for s in sendings if s is not active], "linii": lines}


def stari(dc):
    """One short line per invoice that was ever sent: {IdPrimita, IdTrimitere, Stare (effective), Revenita, Nir: [{Gestiune, Numar, NumarSub}], Total, Preluate}.
    For the tree of the received invoices. The active sending wins; otherwise the newest one in the history."""
    with F._unit(dc, EV_SQL) as conn:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT IdTrimitere, IdPrimita, Stare, MotivReturnare, DataTrimis FROM EF_PrimiteVercon ORDER BY IdPrimita, IdTrimitere DESC")
        rows = cursor.fetchall()
        nirs = _nir_ale(cursor, [r["IdTrimitere"] for r in rows])
        chosen = {}
        for row in rows:
            old = chosen.get(row["IdPrimita"])
            if old is None or (row["Stare"] in STARI_ACTIVE and old["Stare"] not in STARI_ACTIVE):
                chosen[row["IdPrimita"]] = row
        out = []
        for id_primita, row in chosen.items():
            state, back = stare_efectiva(row["Stare"], nirs[row["IdTrimitere"]])
            valid = [n for n in nirs[row["IdTrimitere"]] if n["Valid"]]
            out.append({"IdPrimita": id_primita, "IdTrimitere": row["IdTrimitere"], "Stare": state, "Revenita": back,
                        "MotivReturnare": row["MotivReturnare"], "Nir": [{"Gestiune": n["Gestiune"], "Numar": n["Numar"], "NumarSub": n["NumarSub"]} for n in valid]})
        return {"trimiteri": out}


# ---------------------------------------------------------------------------------------------
# writing
# ---------------------------------------------------------------------------------------------
def _verifica_conturi(cursor, id_primita, items):
    """items = [{IdLinie, IdMasina | None}] -> {IdLinie: IdMasina | None} for EVERY line of the invoice (a line not named has no proposal).
    Every line must belong to the invoice, appear once, and every account must be an active one the tree offers."""
    if items is None:
        items = []
    if not isinstance(items, list):
        raise EfEroare("«conturi» trebuie să fie o listă.", "CAMP_INVALID", 400)
    cursor.execute("SELECT IdLinie FROM EF_PrimiteLinii WHERE IdPrimita = %s", (id_primita,))
    own = {r["IdLinie"] for r in cursor.fetchall()}
    _, conturi = _conturi(cursor)
    chosen = {linie: None for linie in own}
    seen = set()
    for item in items:
        if not isinstance(item, dict) or set(item) - {"IdLinie", "IdMasina"}:
            raise EfEroare("Fiecare cont are doar «IdLinie» și «IdMasina».", "CAMP_NEPERMIS", 400)
        id_linie, id_masina = item.get("IdLinie"), item.get("IdMasina")
        if isinstance(id_linie, bool) or not isinstance(id_linie, int) or id_linie not in own:
            raise EfEroare("O linie nu aparține facturii.", "CAMP_INVALID", 400)
        if id_linie in seen:
            raise EfEroare("O linie apare de două ori.", "CAMP_INVALID", 400)
        seen.add(id_linie)
        if id_masina is not None:
            if isinstance(id_masina, bool) or not isinstance(id_masina, int):
                raise EfEroare("Contul trebuie să fie un număr.", "CAMP_INVALID", 400)
            if id_masina not in conturi:
                raise EfEroare("Un cont ales nu există sau nu este activ în Vercon.", "CONT_INVALID", 404)
        chosen[id_linie] = id_masina
    return chosen


def _text_scurt(value, name):
    if value is None:
        return None
    if not isinstance(value, str):
        raise EfEroare("«%s» trebuie să fie text." % name, "CAMP_INVALID", 400)
    value = value.strip()
    if len(value) > MAX_OBSERVATIE:
        raise EfEroare("«%s» este prea lungă (cel mult %d caractere)." % (name, MAX_OBSERVATIE), "CAMP_INVALID", 400)
    return value or None


def _activ(cursor, id_primita):
    cursor.execute("SELECT IdTrimitere, Stare FROM EF_PrimiteVercon WHERE IdPrimita = %s AND Stare IN ('TRIMISA', 'PRELUATA') ORDER BY IdTrimitere DESC LIMIT 1",
                   (id_primita,))
    return cursor.fetchone()


def _incuie_factura(cursor, id_primita):
    """Locks the invoice row, so two sendings of the same invoice cannot both pass the «no active sending» check."""
    cursor.execute("SELECT IdPrimita FROM EF_Primite WHERE IdPrimita = %s FOR UPDATE", (id_primita,))
    if cursor.fetchone() is None:
        raise EfEroare("Factura primită nu există.", "NU_EXISTA", 404)


def trimite(dc, id_primita, role, username, conturi, observatie):
    """Sends the invoice: ALL its lines, each with the proposed account (or none). Role «Contabil»."""
    cere_contabil(role)
    note = _text_scurt(observatie, "observatie")
    with F._unit(dc, EV_SQL) as conn:
        cursor = conn.cursor(dictionary=True)
        # The state is read BEFORE the lock: reading the lines may commit (it completes the VAT rates of an old invoice), and a commit ends a lock.
        state = _starea(conn, cursor, id_primita, role)
        if not state["poate_trimite"]:
            raise EfEroare(state["motiv"], "NETRIMISIBILA", 409)
        chosen = _verifica_conturi(cursor, id_primita, conturi)
        _incuie_factura(cursor, id_primita)
        if _activ(cursor, id_primita) is not None:  # another session sent it between the read and the lock
            raise EfEroare("Factura este deja trimisă la Vercon.", "NETRIMISIBILA", 409)
        cursor.execute("INSERT INTO EF_PrimiteVercon (IdPrimita, Stare, Observatie, UnTrimis) VALUES (%s, 'TRIMISA', %s, %s)", (id_primita, note, username))
        id_trimitere = cursor.lastrowid
        cursor.executemany("INSERT INTO EF_PrimiteVerconLinii (IdTrimitere, IdLinie, IdMasina) VALUES (%s, %s, %s)",
                           [(id_trimitere, linie, masina) for linie, masina in sorted(chosen.items())])
        conn.commit()
        return _starea(conn, cursor, id_primita, role)


def schimba_conturi(dc, id_primita, role, conturi):
    """Changes the proposed accounts of the active sending while it waits (effective state TRIMISA)."""
    cere_contabil(role)
    with F._unit(dc, EV_SQL) as conn:
        cursor = conn.cursor(dictionary=True)
        _incuie_factura(cursor, id_primita)
        active = _activ(cursor, id_primita)
        if active is None:
            raise EfEroare("Factura nu este trimisă la Vercon.", "NETRIMISA", 409)
        nirs = _nir_ale(cursor, [active["IdTrimitere"]])[active["IdTrimitere"]]
        if stare_efectiva(active["Stare"], nirs)[0] != "TRIMISA":
            raise EfEroare("Factura a fost deja preluată în Vercon; conturile nu se mai pot schimba.", "DEJA_PRELUATA", 409)
        chosen = _verifica_conturi(cursor, id_primita, conturi)
        for linie, masina in chosen.items():
            cursor.execute("UPDATE EF_PrimiteVerconLinii SET IdMasina = %s WHERE IdTrimitere = %s AND IdLinie = %s", (masina, active["IdTrimitere"], linie))
        conn.commit()
        return _starea(conn, cursor, id_primita, role)


def retrage(dc, id_primita, role, id_trimitere):
    """Takes the sending back while it waits (effective state TRIMISA). The row stays, as RETRASA."""
    cere_contabil(role)
    with F._unit(dc, EV_SQL) as conn:
        cursor = conn.cursor(dictionary=True)
        _incuie_factura(cursor, id_primita)
        cursor.execute("SELECT IdTrimitere, Stare FROM EF_PrimiteVercon WHERE IdTrimitere = %s AND IdPrimita = %s", (id_trimitere, id_primita))
        row = cursor.fetchone()
        if row is None:
            raise EfEroare("Trimiterea nu există.", "NU_EXISTA", 404)
        if row["Stare"] not in STARI_ACTIVE:
            raise EfEroare("Trimiterea nu mai este activă.", "NETRIMISA", 409)
        nirs = _nir_ale(cursor, [id_trimitere])[id_trimitere]
        if stare_efectiva(row["Stare"], nirs)[0] != "TRIMISA":
            raise EfEroare("Factura a fost deja preluată în Vercon; nu se mai poate retrage.", "DEJA_PRELUATA", 409)
        cursor.execute("UPDATE EF_PrimiteVercon SET Stare = 'RETRASA' WHERE IdTrimitere = %s", (id_trimitere,))
        conn.commit()
        return _starea(conn, cursor, id_primita, role)
