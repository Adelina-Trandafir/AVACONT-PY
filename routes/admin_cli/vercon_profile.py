"""KBotAdmin command `profil_produs`: everything a unit database holds about ONE product (IdProdus), read only.

IdProdus is per gestiune (VC_ProduseP key is SubunitId + GestiuneId + IdProdus), so the same number can be a different
product in another gestiune: the profile is built per (subunit, gestiune) that has that IdProdus.

Blocks, in the order the operator reads them:
  1. the product itself (VC_ProduseP + VC_Produse), one row per gestiune
  2. where it appears: every VC_ table that has IdProdus, rows and total quantity per gestiune
  3. per gestiune: the OI checks (does every bon line find its register row), the OI stock per holder,
     then every row of every table, untruncated, with the header of the bon / NIR next to it.
"""
import re
from collections import defaultdict
from decimal import Decimal

from routes.admin_cli.admin_common import AdminError, _q, _rows

ROW_LIMIT = 1000
_RX_ID = re.compile(r"^[0-9]{1,10}$")

# The header the lines hang on, so a row can be read without a second lookup: table -> (join, extra select).
_BON_HEADER = (" LEFT JOIN VC_Iesiri_H h ON h.SubunitId=t.SubunitId AND h.GestiuneId=t.GestiuneId AND h.ExercitiuId=t.ExercitiuId "
               "AND h.IdBonConsum=t.IdBonConsum LEFT JOIN VC_Clienti c ON c.SubunitId=h.SubunitId AND c.GestiuneId=h.GestiuneId AND c.IDC=h.IDC",
               ", h.NumarBC AS bon_numar, h.DataBC AS bon_data, c.Denumire AS bon_client, h.Retur AS bon_retur, h.Anulat AS bon_anulat")
_NIR_HEADER = (" LEFT JOIN VC_NIR n ON n.SubunitId=t.SubunitId AND n.GestiuneId=t.GestiuneId AND n.ExercitiuId=t.ExercitiuId "
               "AND n.IdIntrare=t.IdIntrare",
               ", n.TipDoc AS nir_tip, n.Numar AS nir_numar, n.NumarSub AS nir_sub, n.Data AS nir_data, n.NrFact AS nir_factura, "
               "n.FurnizorV AS nir_furnizor, n.Storno AS nir_storno, n.Anulat AS nir_anulat")


def _holder_header(column):
    return (" LEFT JOIN VC_Clienti c ON c.SubunitId=t.SubunitId AND c.GestiuneId=t.GestiuneId AND c.IDC=t.%s" % column,
            ", c.Denumire AS detinator")


EXTRAS = {
    "VC_Iesiri": _BON_HEADER,
    "VC_IesiriRetur": _BON_HEADER,
    "VC_Intrari": _NIR_HEADER,
    "VC_IntrariStorno": _NIR_HEADER,
    "VC_OI_Predare": _holder_header("IDC_BP"),
    "VC_OI_Transfer": _holder_header("IDC_BT"),
    "VC_OI_Casare": _holder_header("IDC_BC"),
    "VC_OI_Produse": _holder_header("IDC_PTC"),
}

# family, table, document key, number, date, holder: the same triples routes/vercon/oi_accounts.py reads.
_OI_BONS = (
    ("bp", "VC_OI_Predare", "IDBP", "NRBP", "DATABP", "IDC_BP"),
    ("bt", "VC_OI_Transfer", "IDBT", "NRBT", "DATABT", "IDC_BT"),
    ("bs", "VC_OI_Casare", "IDBC", "NRBC", "DATABC", "IDC_BC"),
)


def _num(value):
    return format(Decimal(value or 0).normalize(), "f")


def _tables(conn, db):
    """table -> its columns, for every VC_ table that can hold a movement of a product."""
    columns = defaultdict(list)
    for row in _rows(conn, "SELECT TABLE_NAME AS n, COLUMN_NAME AS c FROM information_schema.COLUMNS "
                           "WHERE TABLE_SCHEMA=%s AND LEFT(TABLE_NAME,3)='VC_' ORDER BY TABLE_NAME, ORDINAL_POSITION", (db,)):
        columns[row["n"]].append(row["c"])
    return {name: cols for name, cols in columns.items() if {"IdProdus", "SubunitId", "GestiuneId"} <= set(cols)}


def _dump(conn, table, columns, sub, gest, product):
    join, select = EXTRAS.get(table, ("", ""))
    sql = ("SELECT t.*{select} FROM {table} t{join} WHERE t.SubunitId=%s AND t.GestiuneId=%s AND t.IdProdus=%s "
           "ORDER BY t.{first} LIMIT {limit}").format(select=select, table=_q(table), join=join, first=_q(columns[0]), limit=ROW_LIMIT + 1)
    return _rows(conn, sql, (sub, gest, product))


def _oi_checks(conn, sub, gest, product, names):
    """The refusal the reports give (OI_ACCOUNT_ORIGIN) found before a report runs: does every bon line of the product
    find its register row, alone or as the sum of the rows the register split it into."""
    from routes.vercon.nomenclatoare import NomError
    from routes.vercon.oi_custody import register_rows

    problems = []
    cur = conn.cursor(dictionary=True)
    try:
        resolved = [r for r in register_rows(cur, {"subunit": sub, "gestiune": gest}) if r["IdProdus"] == product]
    except NomError as err:
        resolved = None
        problems.append("Registrul OI al gestiunii nu se poate rezolva: %s" % err)
    finally:
        cur.close()
    # A withdrawal row must carry a negative value like its quantity (oi.py writes both with the same sign): a register row
    # whose quantity and value disagree leaves a holder with quantity 0 and a value left over.
    for odd in _rows(conn, "SELECT IDOI, Cant, ValoareCUTVA FROM VC_OI_Produse WHERE SubunitId=%s AND GestiuneId=%s AND IdProdus=%s "
                           "AND ((Cant<0 AND ValoareCUTVA>0) OR (Cant>0 AND ValoareCUTVA<0)) ORDER BY IDOI", (sub, gest, product)):
        problems.append("Randul %s din registrul OI are cantitatea %s si valoarea %s: semne diferite." % (
            odd["IDOI"], _num(odd["Cant"]), _num(odd["ValoareCUTVA"])))
    received, batch_accounts, batch_quantity, lots =defaultdict(set), defaultdict(set), defaultdict(Decimal), defaultdict(list)
    for row in resolved or []:
        if row["family"] == "bs" and row["quantity"] > 0 or row["family"] != "bs" and row["quantity"] < 0:
            continue
        batch = (row["family"], row["document"], row["IDC_PTC"], row["IdProdus"], row["PUTVA"])
        received[batch + (abs(row["quantity"]),)].add(row["IDM"])
        batch_accounts[batch].add(row["IDM"])
        batch_quantity[batch] += abs(row["quantity"])
        lots[batch[:4]].append("%s x %s (cont %s)" % (_num(abs(row["quantity"])), _num(row["PUTVA"]), row["IDM"]))

    out = []
    missing = 0
    for family, table, key, number, date, holder in _OI_BONS:
        lines = _rows(conn, "SELECT ID, ExercitiuId, {key} AS document, {number} AS number, {date} AS day, {holder} AS holder, Cant, PUTVA, "
                            "Anulat, MotivAnulare FROM {table} WHERE SubunitId=%s AND GestiuneId=%s AND IdProdus=%s "
                            "ORDER BY {date}, ID".format(key=key, number=number, date=date, holder=holder, table=_q(table)),
                      (sub, gest, product))
        totals = defaultdict(Decimal)
        for line in lines:
            if not line["Anulat"]:
                totals[(line["ExercitiuId"], family, line["document"], line["holder"], product, line["PUTVA"])] += abs(Decimal(line["Cant"] or 0))
        for line in lines:
            batch = (family, line["document"], line["holder"], product, line["PUTVA"])
            quantity = abs(Decimal(line["Cant"] or 0))
            if line["Anulat"]:
                status = "anulat" + (" (%s)" % line["MotivAnulare"] if line["MotivAnulare"] else "")
            elif resolved is None:
                status = "necunoscut: registrul nu se rezolva"
            else:
                accounts, how = received.get(batch + (quantity,), set()), "rand exact"
                if not accounts and batch_quantity.get(batch) == totals[(line["ExercitiuId"],) + batch]:
                    accounts, how = batch_accounts[batch], "suma randurilor din registru"
                if len(accounts) == 1:
                    status = "OK, cont %s (%s)" % (next(iter(accounts)), how)
                else:
                    missing += 1
                    status = ("LIPSA in registru. Registrul are pentru acest bon: %s" % ("; ".join(lots.get(batch[:4], [])) or "nimic")
                              if not accounts else "CONFLICT, conturi diferite: %s" % ", ".join(sorted(str(a) for a in accounts)))
            out.append([family.upper(), line["ID"], line["ExercitiuId"], line["number"], line["day"], line["document"], line["holder"],
                        names.get(line["holder"], ""), _num(line["Cant"]), _num(line["PUTVA"]), status])
    if missing:
        problems.append("%d linii de bon nu gasesc un cont in registrul OI (cele care dau OI_ACCOUNT_ORIGIN la rapoarte)." % missing)
    return out, problems


def profile(conn, db, param, block):
    product = (param or "").strip()
    if not _RX_ID.match(product):
        raise AdminError("IdProdus trebuie sa fie un numar intreg.")
    product = int(product)
    tables = _tables(conn, db)
    if "VC_ProduseP" not in tables:
        raise AdminError("Baza %s nu are tabelele VERCON (VC_ProduseP)." % db)
    pairs = _rows(conn, "SELECT pp.SubunitId AS sub, pp.GestiuneId AS gest, s.Name AS sname, g.Name AS gname, g.Type AS gtype, pp.ID AS id, "
                        "pr.DenumireP AS name, pr.UM AS um, pp.COD AS cod, pp.PUTVA AS putva, pp.Activ AS activ, pr.Activ AS name_activ "
                        "FROM VC_ProduseP pp LEFT JOIN VC_Produse pr ON pr.SubunitId=pp.SubunitId AND pr.GestiuneId=pp.GestiuneId "
                        "AND pr.ID=pp.ID LEFT JOIN VC_Gestiuni g ON g.SubunitId=pp.SubunitId AND g.GestiuneId=pp.GestiuneId "
                        "LEFT JOIN VC_Subunitati s ON s.SubunitId=pp.SubunitId WHERE pp.IdProdus=%s ORDER BY pp.SubunitId, pp.GestiuneId", (product,))
    if not pairs:
        return [block(db, "Produs %d" % product, ["rezultat"], [["IdProdus %d nu exista in VC_ProduseP, in nicio gestiune." % product]])]

    blocks = [block(db, "Produs %d" % product,
                    ["subunitate", "gestiune", "tip", "cod nomenclator", "denumire", "UM", "cod", "pret", "activ"],
                    [[p["sname"] or p["sub"], "%s (%s)" % (p["gname"], p["gest"]), p["gtype"], p["id"], p["name"], p["um"], p["cod"],
                      _num(p["putva"]), "da" if p["activ"] else "NU"] for p in pairs],
                    "IdProdus este numarat pe gestiune: acelasi numar poate fi alt produs in alta gestiune.", raw=True)]

    summary = []
    for table, columns in sorted(tables.items()):
        for p in pairs:
            quantity = ", SUM(Cant) AS q" if "Cant" in columns else ""
            row = _rows(conn, "SELECT COUNT(*) AS n{q} FROM {table} WHERE SubunitId=%s AND GestiuneId=%s AND IdProdus=%s".format(
                q=quantity, table=_q(table)), (p["sub"], p["gest"], product))[0]
            if row["n"]:
                summary.append([table, p["gname"], row["n"], _num(row["q"]) if "q" in row and row["q"] is not None else ""])
    blocks.append(block(db, "Unde apare produsul %d" % product, ["tabela", "gestiune", "randuri", "suma Cant"], summary,
                        "Doar tabelele unde produsul are cel putin un rand. Suma Cant e simpla, fara sa tina cont de semn sau de anulate."))

    for p in pairs:
        label = "%s / %s" % (p["sname"] or p["sub"], p["gname"])
        names = {c["IDC"]: c["Denumire"] for c in _rows(conn, "SELECT IDC, Denumire FROM VC_Clienti WHERE SubunitId=%s AND GestiuneId=%s",
                                                       (p["sub"], p["gest"]))}
        if "VC_OI_Produse" in tables:
            lines, problems = _oi_checks(conn, p["sub"], p["gest"], product, names)
            blocks.append(block(db, "%s: observatii OI" % label, ["observatie"], [[x] for x in problems] or [["Nimic de semnalat."]], raw=True))
            blocks.append(block(db, "%s: liniile bonurilor OI si registrul" % label,
                                ["tip", "ID linie", "exercitiu", "numar bon", "data", "cod intern", "detinator", "nume detinator", "cant", "pret",
                                 "verificare in registru"], lines,
                                "Numarul bonului este cel tiparit pe bon; codul intern este IDBP/IDBT/IDBC.", raw=True))
            stock = _rows(conn, "SELECT IDC_PTC, COUNT(*) AS n, SUM(Cant) AS q, SUM(ValoareCUTVA) AS v FROM VC_OI_Produse "
                                "WHERE SubunitId=%s AND GestiuneId=%s AND IdProdus=%s GROUP BY IDC_PTC ORDER BY IDC_PTC",
                          (p["sub"], p["gest"], product))
            blocks.append(block(db, "%s: stocul OI pe detinator (din registru)" % label, ["detinator", "nume", "randuri", "cantitate", "valoare"],
                                [[s["IDC_PTC"], names.get(s["IDC_PTC"], ""), s["n"], _num(s["q"]), _num(s["v"])] for s in stock],
                                "Cantitatea ramasa la fiecare detinator: suma randurilor din registru, cu semnul lor.", raw=True))
        for table, columns in sorted(tables.items()):
            rows = _dump(conn, table, columns, p["sub"], p["gest"], product)
            if not rows:
                continue
            note = "Atinsa limita de %d randuri: restul nu se arata." % ROW_LIMIT if len(rows) > ROW_LIMIT else ""
            rows = rows[:ROW_LIMIT]
            names_of_columns = list(rows[0].keys())
            blocks.append(block(db, "%s: %s" % (label, table), names_of_columns, [[r[c] for c in names_of_columns] for r in rows], note, raw=True))
    return blocks


def sign_check(conn, db, block):
    """Read only, one database: register rows whose quantity and value have different signs, per gestiune.

    Withdrawal rows carry a negative value like their quantity (oi.py); rows migrated by the old program sometimes do not,
    which leaves a holder with quantity 0 and a value left over."""
    if not _rows(conn, "SELECT 1 AS x FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s AND TABLE_NAME='VC_OI_Produse'", (db,)):
        return block(db, "Semne in registrul OI", ["rezultat"], [["Baza nu are VC_OI_Produse (nu este o baza VERCON)."]])
    data = _rows(conn, "SELECT p.SubunitId AS sub, p.GestiuneId AS gest, MIN(g.Name) AS gname, COUNT(*) AS n, COUNT(DISTINCT p.IdProdus) AS products, "
                       "SUM(COALESCE(p.IDBP,0)<>0) AS bp, SUM(COALESCE(p.IDBT,0)<>0) AS bt, "
                       "SUM(COALESCE(p.IDBP,0)=0 AND COALESCE(p.IDBT,0)=0) AS bs, SUM(p.ValoareCUTVA) AS value_sum, "
                       "SUBSTRING_INDEX(GROUP_CONCAT(p.IDOI ORDER BY p.IDOI), ',', 8) AS examples, "
                       "SUBSTRING_INDEX(GROUP_CONCAT(CASE WHEN COALESCE(p.IDBP,0)=0 AND COALESCE(p.IDBT,0)=0 THEN p.IDOI END ORDER BY p.IDOI), ',', 8) AS bs_examples, "
                       "SUM(p.Cant<0) AS negative_quantity "
                       "FROM VC_OI_Produse p LEFT JOIN VC_Gestiuni g ON g.SubunitId=p.SubunitId AND g.GestiuneId=p.GestiuneId "
                       "WHERE (p.Cant<0 AND p.ValoareCUTVA>0) OR (p.Cant>0 AND p.ValoareCUTVA<0) "
                       "GROUP BY p.SubunitId, p.GestiuneId ORDER BY p.SubunitId, p.GestiuneId")
    total = sum(int(r["n"]) for r in data)
    summary = block(db, "Semne diferite intre Cant si ValoareCUTVA in registrul OI",
                    ["subunit", "gestiune", "nume", "randuri", "produse", "pe bon predare", "pe bon transfer", "pe bon casare", "cu cantitate negativa",
                     "suma valorilor", "primele IDOI", "IDOI casare"],
                    [[r["sub"], r["gest"], r["gname"], r["n"], r["products"], r["bp"], r["bt"], r["bs"], r["negative_quantity"], _num(r["value_sum"]),
                      r["examples"], r["bs_examples"] or ""] for r in data],
                    "Total: %d randuri. Goala = nimic de corectat. Doar citire." % total, raw=True)
    if not total:
        return summary
    # The rows themselves, the odd ones first (not the giver side of a transfer, the known migration case), so one can be traced to its bon.
    rows = _rows(conn, "SELECT p.IDOI, p.SubunitId, p.GestiuneId, p.IDBP, p.IDBT, p.IDBC, p.IDBO, p.IDM, p.IDC_PTC, c.Denumire AS holder, p.IdProdus, "
                       "pr.DenumireP AS product, p.Cant, p.PUTVA, p.ValoareCUTVA, p.DataSalvare FROM VC_OI_Produse p "
                       "LEFT JOIN VC_Clienti c ON c.SubunitId=p.SubunitId AND c.GestiuneId=p.GestiuneId AND c.IDC=p.IDC_PTC "
                       "LEFT JOIN VC_ProduseP pp ON pp.SubunitId=p.SubunitId AND pp.GestiuneId=p.GestiuneId AND pp.IdProdus=p.IdProdus "
                       "LEFT JOIN VC_Produse pr ON pr.SubunitId=pp.SubunitId AND pr.GestiuneId=pp.GestiuneId AND pr.ID=pp.ID "
                       "WHERE (p.Cant<0 AND p.ValoareCUTVA>0) OR (p.Cant>0 AND p.ValoareCUTVA<0) "
                       "ORDER BY (COALESCE(p.IDBT,0)<>0 AND p.Cant<0), p.IDOI LIMIT 50")
    columns = ["IDOI", "subunit", "gestiune", "IDBP", "IDBT", "IDBC", "IDBO", "IDM", "detinator", "nume detinator", "IdProdus", "produs", "cant", "pret",
               "valoare", "salvat"]
    detail = block(db, "Primele randuri cu semne diferite (cele care nu sunt partea celui care da la transfer, intai)", columns,
                   [[r["IDOI"], r["SubunitId"], r["GestiuneId"], r["IDBP"], r["IDBT"], r["IDBC"], r["IDBO"], r["IDM"], r["IDC_PTC"], r["holder"],
                     r["IdProdus"], r["product"], _num(r["Cant"]), _num(r["PUTVA"]), _num(r["ValoareCUTVA"]), r["DataSalvare"]] for r in rows],
                   "Cel mult 50 de randuri. IdProdus de aici se foloseste la «Profil produs VERCON».", raw=True)
    return [summary, detail]


def repair_signs(conn, db, block):
    """WRITES, one database: a register row with a negative quantity and a positive value gets the negative value.

    The quantity is the truth (the sign of the movement: minus on the side that gives or writes off), the value follows it, which is
    what oi.py writes. Only that case is repaired: the opposite one (positive quantity, negative value) has not been seen and stays
    in the check's result for a human look. The statement is one UPDATE, so it is all or nothing; the Version is bumped like
    every register edit, and one line goes to the Jurnal."""
    from routes.admin_cli.admin_common import _exec, _journal
    if not _rows(conn, "SELECT 1 AS x FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s AND TABLE_NAME='VC_OI_Produse'", (db,)):
        raise AdminError("Baza %s nu are VC_OI_Produse (nu este o baza VERCON)." % db)
    changed = _exec(conn, "UPDATE VC_OI_Produse SET ValoareCUTVA=-ABS(ValoareCUTVA), Version=Version+1 WHERE Cant<0 AND ValoareCUTVA>0")
    if changed:
        _journal(conn, "oi_semne_repara", db, "VC_OI_Produse", "%d randuri: valoarea facuta negativa (cantitate negativa, valoare pozitiva)" % changed)
    done = block(db, "Reparare semne in registrul OI", ["rezultat"],
                 [["%d randuri corectate (valoarea a devenit negativa, ca cantitatea)." % changed if changed else "Nu era nimic de corectat."]],
                 "Ce a mai ramas de verificat este in blocul urmator.", raw=True)
    left = sign_check(conn, db, block)
    return [done] + (left if isinstance(left, list) else [left])
