# routes/efactura/primite_contari.py
"""
01EF-02: the CALCULATION of the «Contari» of received e-invoices (the accounting half of the Access form EFACTURA_2025 with its subforms EF_F and EF_P).
NOTHING IS WRITTEN HERE. The operator ticks invoices of ONE supplier, the server answers what Access kept in its work tables tmpFacturi / tmpDoc2017P:
the evidence rows, the payment row, the templates that fit the classification, the codes of the partner and the warnings; and, from the chosen
templates, the debit / credit lines each booking would produce. The save (documents, operations, debit, credit, notes, order of payment) is 01EF-04/05:
it needs the posting tables of AVACONT stage 2, which do not exist yet.

Rules taken from Access (verified in EFACTURA_2025 / EF_F / EF_P / QEF_EV / QEF_Plata / SAV_*), with the FOREXE part of Access left out on purpose
(operator rule 31.10.2026) and without any link to receptions (the «Contari» has nothing to do with them):
- Suma of an invoice = its total (negative for a credit note, `Semn`) minus what EF_PrimiteOperatii already holds; Platit <= Suma, by default = Suma.
- NumarDocument = the digits of the LAST word of the invoice number (the whole number when it has no digit).   [reading of QEF_EV: inferred]
- Only templates of `Tip` M; evidence = `Fel` E, payment = `Fel` P. A template fits a classification when its `Cheie` equals Clasificatii.ClsfX and the
  sector+source of its journal equals Clasificatii.SS; its lines are those of the form (Subcapitol) of the classification, or without a form.
- Explanation of the order of payment: «F <numbers of the invoices paid> <FelOP of the template>». (Access added «C <client code>» from ParteneriAng.CodClient,
  a column K-BOT does not have.)
The role only decides `poate_scrie`; reading is open to every session.
"""
import logging
import re
from contextlib import contextmanager
from decimal import Decimal

from . import facturi as F
from . import primite_ke
from .primite_parteneri import activeaza as activeaza_partener, leaga as leaga_partener, rezolva as rezolva_partener
from .tokens import EfEroare

logger = logging.getLogger(__name__)

CONTARI_SQL = "sql/00EF_27_primite_operatii.sql, sql/AV01_14_sablon_operatii.sql, sql/00EF_55_furnizor_partener.sql"
MAX_FACTURI = 200
MAX_EXPLICATIE_OP = 254
EXPLICATIE_OP_GALBEN = 69

_dec = primite_ke._dec
_num = primite_ke._num


def numar_document(nr_fact):
    """The digits of the last word of the invoice number; the whole number when there is no digit (reading of QEF_EV)."""
    words = re.findall(r"\w+", nr_fact or "")
    digits = re.sub(r"\D+", "", words[-1]) if words else ""
    return digits or (nr_fact or "")


def _iso(value):
    return value.isoformat() if value is not None else None


def _ids(raw):
    if not isinstance(raw, list) or not raw or len(raw) > MAX_FACTURI:
        raise EfEroare("Alegeți între 1 și %d facturi." % MAX_FACTURI, "FACTURI_INVALIDE", 400)
    out = []
    for item in raw:
        if isinstance(item, bool) or not isinstance(item, int) or item <= 0:
            raise EfEroare("Lista facturilor conține o valoare nepermisă.", "FACTURI_INVALIDE", 400)
        if item not in out:
            out.append(item)
    return out


def _opt_int(body, key):
    value = body.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise EfEroare("Câmpul «%s» nu este valid." % key, "CAMP_INVALID", 400)
    return value


def _marks(n):
    return ", ".join(["%s"] * n)


# ---------------------------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------------------------
def _facturi(cursor, ids, blocheaza=False):
    cursor.execute("SELECT IdPrimita, NrFact, DataFact, Total, Semn, Tip, CuiNormalizat, CUI, DenumireP FROM EF_Primite "
                   "WHERE IdPrimita IN (%s) ORDER BY DataFact, IdPrimita%s" % (_marks(len(ids)), " FOR UPDATE" if blocheaza else ""), tuple(ids))
    rows = cursor.fetchall()
    if len(rows) != len(ids):
        raise EfEroare("Una dintre facturi nu mai există.", "FACTURA_INEXISTENTA", 404)
    suppliers = {(r["CuiNormalizat"] or "").strip() or ("N" + (r["DenumireP"] or "").strip().upper()) for r in rows}
    if len(suppliers) > 1:
        raise EfEroare("Facturile alese trebuie să fie de la același furnizor.", "FURNIZORI_DIFERITI", 409)
    return rows


def _salvat(cursor, ids):
    cursor.execute("SELECT IdPrimita, SUM(Suma) AS Salvat FROM EF_PrimiteOperatii WHERE IdPrimita IN (%s) GROUP BY IdPrimita" % _marks(len(ids)), tuple(ids))
    return {r["IdPrimita"]: _dec(r["Salvat"]) for r in cursor.fetchall()}


def _sabloane(cursor, id_subunitate=None):
    """{(Cheie, SS): {'E': [template], 'P': [template]}} for the templates of type M; with `id_subunitate`, only the templates of that subunit (as in Access, nothing is common)."""
    cursor.execute("SELECT s.IdSablon, s.Fel, s.Explicatie, s.FelOP, s.Cheie, s.Compartiment, j.Cod AS Jurnal, j.SursaSector "
                   "FROM TZ_SablonOperatii s JOIN TZ_Jurnale j ON j.IdJurnal = s.IdJurnal "
                   "WHERE s.Tip = 'M' AND s.Fel IN ('E', 'P') AND s.Cheie IS NOT NULL AND (%s IS NULL OR s.IdSubunitate = %s) ORDER BY s.Explicatie, s.IdSablon",
                   (id_subunitate, id_subunitate))
    index = {}
    for r in cursor.fetchall():
        slot = index.setdefault((r["Cheie"], r["SursaSector"]), {"E": [], "P": []})
        slot[r["Fel"]].append({"IdSablon": r["IdSablon"], "Explicatie": r["Explicatie"] or "", "FelOP": r["FelOP"] or "",
                               "Compartiment": r["Compartiment"] or "", "Jurnal": r["Jurnal"]})
    return index


def _clasificatii(cursor, index):
    """The classifications a template fits: [{IdClsf, Clsf, Denumire, Forma, Cheie, SS}] in code order."""
    cursor.execute("SELECT IDClsf, Clsf, ClsfX, ClsfF, ClsfE, Subcapitol, Denumire, SS, Titlu, IdUnitate FROM Clasificatii ORDER BY Clsf")
    return [{"IdClsf": r["IDClsf"], "Clsf": r["Clsf"], "Denumire": r["Denumire"] or "", "Forma": r["Subcapitol"], "Cheie": r["ClsfX"], "SS": r["SS"],
             "Titlu": r["Titlu"], "ClsfF": r["ClsfF"], "ClsfE": r["ClsfE"], "IdUnitate": r["IdUnitate"]} for r in cursor.fetchall() if (r["ClsfX"], r["SS"]) in index]


def _coduri(cursor, id_partener, id_clsf):
    if id_partener is None or id_clsf is None:
        return None
    cursor.execute("SELECT CodAng, CodInd, ContBancar FROM Parteneri_Coduri WHERE IdPartener = %s AND IdClsf = %s", (id_partener, id_clsf))
    row = cursor.fetchone()
    if row is None:
        return None
    return {"CodAng": row["CodAng"] or "", "CodInd": row["CodInd"] or "", "ContBancar": row["ContBancar"] or ""}


def _sugestie(cursor, partener, oferite):
    """(suggested IdClsf or None, [IdClsf of the partner offered]). The partner's classifications first; otherwise the only one of article 20 that has a template."""
    din_partener = []
    if partener is not None:
        cursor.execute("SELECT IdClsf FROM Parteneri_Coduri WHERE IdPartener = %s", (partener["IdPartener"],))
        ids = {r["IdClsf"] for r in cursor.fetchall()}
        din_partener = [c["IdClsf"] for c in oferite if c["IdClsf"] in ids]
    if len(din_partener) == 1:
        return din_partener[0], din_partener
    if din_partener:
        return None, din_partener
    art20 = [c["IdClsf"] for c in oferite if c["Titlu"] == "20"]
    return (art20[0] if len(art20) == 1 else None), din_partener


# ---------------------------------------------------------------------------------------------
# the lines each booking would produce
# ---------------------------------------------------------------------------------------------
def _parti(cursor, linii):
    ids = {l["IdPartDebit"] for l in linii if l["IdPartDebit"]} | {l["IdPartCredit"] for l in linii if l["IdPartCredit"]}
    if not ids:
        return {}
    cursor.execute("SELECT IdPartener, DenumirePartener FROM Parteneri WHERE IdPartener IN (%s)" % _marks(len(ids)), tuple(ids))
    return {r["IdPartener"]: r["DenumirePartener"] or "" for r in cursor.fetchall()}


def _linii_sablon(cursor, id_sablon, forma):
    cursor.execute("SELECT IdLinie, Forma, ContDebit, AnaliticDebit, ContCredit, AnaliticCredit, PartDebitMod, IdPartDebit, PartCreditMod, IdPartCredit, "
                   "PA, Ordonantare, Chelt FROM TZ_SablonOperatiiLinie WHERE IdSablon = %s AND (Forma = %s OR Forma IS NULL OR Forma = '') ORDER BY IdLinie",
                   (id_sablon, forma))
    rows = cursor.fetchall()
    exacte = [r for r in rows if r["Forma"] == forma]   # a template has one line per form: the line of the form wins over the line valid for every form
    return exacte or rows[:1]


def _simbol(cont, analitic):
    return "%s.%s" % (cont, analitic) if analitic else str(cont)


def _partener_text(mod, id_fix, partener, nume):
    if mod == "OPERATIE":
        return partener["Denumire"] if partener else "(furnizorul)"
    if mod == "FIX":
        return nume.get(id_fix, "")
    return ""


def _operatie(titlu, linii, suma, partener, nume):
    """One booking: the template lines with the amount (Access wrote the same amount on the debit and on the credit)."""
    return {"Titlu": titlu, "Suma": _num(suma), "Linii": [{
        "Debit": _simbol(l["ContDebit"], l["AnaliticDebit"]), "PartenerDebit": _partener_text(l["PartDebitMod"], l["IdPartDebit"], partener, nume),
        "Credit": _simbol(l["ContCredit"], l["AnaliticCredit"]), "PartenerCredit": _partener_text(l["PartCreditMod"], l["IdPartCredit"], partener, nume),
        "PA": bool(l["PA"]), "Ordonantare": bool(l["Ordonantare"]), "Chelt": bool(l["Chelt"])} for l in linii]}


# ---------------------------------------------------------------------------------------------
# the calculation
# ---------------------------------------------------------------------------------------------
@contextmanager
def _conexiune(dc, conn=None):
    """The caller's own connection when one is given (the save works in ONE transaction), else a new one of the unit."""
    if conn is not None:
        yield conn
    else:
        with F._unit(dc, CONTARI_SQL) as nou:
            yield nou


def calcul(dc, body, role, conn=None, interne=None, blocheaza=False, fara_plata=False):
    """Everything the page «Contari» shows for the ticked invoices. body: {ids, idClsf?, idSablonE?, idSablonP?, platit?: {id: value}, an?, idSubunitate?, idPartener?}.
    The save (01EF-04) calls it with its own connection (`conn`), `blocheaza` (the invoices are locked), `fara_plata` (no payment template, no payment warnings) and an
    `interne` dict that receives the objects it needs (invoices, exact rests, partner, classification, subunit, dates)."""
    ids = _ids(body.get("ids"))
    id_clsf = _opt_int(body, "idClsf")
    id_e = _opt_int(body, "idSablonE")
    id_p = None if fara_plata else _opt_int(body, "idSablonP")
    an = _opt_int(body, "an")
    platit_cerut = body.get("platit") or {}
    if not isinstance(platit_cerut, dict):
        raise EfEroare("Câmpul «platit» nu este valid.", "CAMP_INVALID", 400)
    with _conexiune(dc, conn) as conn:
        cursor = conn.cursor(dictionary=True)
        facturi = _facturi(cursor, ids, blocheaza)
        salvat = _salvat(cursor, ids)
        cui = (facturi[0]["CuiNormalizat"] or "").strip()
        # The partner is found by TAX CODE ONLY (hidden ones too): the one named in the body (`idPartener`), else the one kept for the code, else the only one;
        # several and none kept: None and the operator chooses.
        candidati, partener = rezolva_partener(cursor, cui, _opt_int(body, "idPartener"))
        # The subunit in force (chosen in the title bar; the booking belongs to it): only ITS templates and the classifications of ITS sector+source are offered.
        subunitate = None
        id_subunitate = _opt_int(body, "idSubunitate")
        index = _sabloane(cursor, id_subunitate)
        oferite = _clasificatii(cursor, index)
        if id_subunitate is not None:
            from routes.avacont.introducere_date.operatii import postare
            try:
                subunitate = postare.subunitate_pentru(cursor, None, id_subunitate)
            except postare.PostareEroare as err:
                raise EfEroare(str(err), err.reason, err.status) from err
            oferite = [c for c in oferite if c["SS"] == subunitate["SursaSector"]]
        sugerata, ale_partenerului = _sugestie(cursor, partener, oferite)
        avertismente = []
        if not candidati:
            avertismente.append({"Cod": "PARTENER_LIPSA", "Grav": "eroare",
                                 "Text": "Nu există niciun partener cu codul fiscal al furnizorului. Adăugați-l întâi în lista de parteneri."})
        elif partener is None:
            avertismente.append({"Cod": "PARTENER_DE_ALES", "Grav": "eroare",
                                 "Text": "Mai mulți parteneri au codul fiscal al furnizorului. Alegeți unul."})
        clsf = next((c for c in oferite if c["IdClsf"] == id_clsf), None) if id_clsf else None
        if id_clsf and clsf is None:
            raise EfEroare("Clasificația aleasă nu are șabloane de operații în sursa subunității în vigoare.", "CLASIFICATIE_FARA_SABLON", 409)

        # the invoices (EF_F)
        evidenta, total_platit, numere = [], Decimal(0), []
        ramas_exacte = {}
        for r in facturi:
            semn = int(r["Semn"] or 1)
            total = _dec(r["Total"]) * semn
            ramas = total - salvat.get(r["IdPrimita"], Decimal(0))
            ramas_exacte[r["IdPrimita"]] = ramas
            cerut = platit_cerut.get(str(r["IdPrimita"]))
            platit = _dec(cerut) if cerut is not None else ramas
            if (ramas >= 0 and (platit < 0 or platit > ramas)) or (ramas < 0 and (platit > 0 or platit < ramas)):
                raise EfEroare("Suma plătită a facturii %s nu poate depăși valoarea rămasă de înregistrat (%s)." % (r["NrFact"], _num(ramas)),
                               "PLATA_PESTE_RAMAS", 400)
            if ramas == 0:
                avertismente.append({"Cod": "FACTURA_COMPLETA", "Grav": "eroare", "Text": "Factura %s este deja înregistrată integral." % r["NrFact"]})
            if r["Tip"] == "NC":
                avertismente.append({"Cod": "NOTA_CREDIT", "Grav": "confirmare",
                                     "Text": "Atenție! Rândul %s reprezintă o notă de credit, nu o factură!" % r["NrFact"]})
            if an and r["DataFact"] is not None and r["DataFact"].year != an:
                avertismente.append({"Cod": "ALT_AN", "Grav": "confirmare", "Text": "Atenție! Factura %s este din alt an decât cel curent!" % r["NrFact"]})
            evidenta.append({"IdPrimita": r["IdPrimita"], "NrFact": r["NrFact"], "NumarDocument": numar_document(r["NrFact"]),
                             "DataDocument": _iso(r["DataFact"]), "FelDocument": "Notă credit" if r["Tip"] == "NC" else "Factură",
                             "Total": _num(total), "Salvat": _num(salvat.get(r["IdPrimita"], Decimal(0))), "Suma": _num(ramas), "Platit": _num(platit)})
            total_platit += platit
            if platit != 0:
                numere.append(numar_document(r["NrFact"]))
        cursor.execute("SELECT a.IdPrimita, COUNT(*) AS N FROM EF_Primite a JOIN EF_Primite b ON b.CuiNormalizat = a.CuiNormalizat AND b.NrFact = a.NrFact "
                       "AND b.DataFact <=> a.DataFact AND b.Total = a.Total AND b.IdPrimita <> a.IdPrimita WHERE a.IdPrimita IN (%s) GROUP BY a.IdPrimita"
                       % _marks(len(ids)), tuple(ids))
        for r in cursor.fetchall():
            nr = next(f["NrFact"] for f in facturi if f["IdPrimita"] == r["IdPrimita"])
            avertismente.append({"Cod": "DUBLA", "Grav": "confirmare",
                                 "Text": "ATENȚIE! Factura %s apare de mai multe ori în lista primită de la ANAF (același număr, dată și valoare)." % nr})

        # classification, templates, codes
        sabloane_e, sabloane_p, coduri, operatii = [], [], None, []
        cont_iban = (partener or {}).get("ContIBAN", "")
        if clsf is not None:
            slot = index[(clsf["Cheie"], clsf["SS"])]
            sabloane_e, sabloane_p = slot["E"], slot["P"]
            valid_e = {s["IdSablon"] for s in sabloane_e}
            valid_p = {s["IdSablon"] for s in sabloane_p}
            if id_e and id_e not in valid_e:
                raise EfEroare("Șablonul de evidențiere ales nu se potrivește clasificației.", "SABLON_INVALID", 409)
            if id_p and id_p not in valid_p:
                raise EfEroare("Șablonul de plată ales nu se potrivește clasificației.", "SABLON_INVALID", 409)
            if id_e is None and len(sabloane_e) == 1:
                id_e = sabloane_e[0]["IdSablon"]
            if id_p is None and len(sabloane_p) == 1 and not fara_plata:
                id_p = sabloane_p[0]["IdSablon"]
            coduri = _coduri(cursor, (partener or {}).get("IdPartener"), clsf["IdClsf"])
            if coduri and coduri["ContBancar"]:
                cont_iban = coduri["ContBancar"]
            if id_p and not coduri:
                avertismente.append({"Cod": "CODURI_LIPSA", "Grav": "atentie",
                                     "Text": "Codurile Angajament și Indicator nu sunt configurate pentru furnizorul și clasificația selectate!"})
            if id_p and not cont_iban:
                avertismente.append({"Cod": "CONT_BANCAR_LIPSA", "Grav": "eroare",
                                     "Text": "Nu se poate adăuga operația de plată: partenerul selectat nu are niciun cont bancar configurat!"})
            nume = {}
            for id_sablon, suma_de, eticheta in ((id_e, None, "Evidențiere"), (id_p, total_platit, "Plată")):
                if not id_sablon:
                    continue
                linii = _linii_sablon(cursor, id_sablon, clsf["Forma"])
                if not linii:
                    avertismente.append({"Cod": "SABLON_FARA_LINIE", "Grav": "eroare",
                                         "Text": "Șablonul de %s nu are nicio linie pentru forma %s a clasificației." % (eticheta.lower(), clsf["Forma"])})
                    continue
                nume.update(_parti(cursor, linii))
                if suma_de is None:
                    for e in evidenta:
                        if e["Suma"] != 0:
                            operatii.append(dict(_operatie("%s · %s" % (eticheta, e["NrFact"]), linii, _dec(e["Suma"]), partener, nume), Faza="E",
                                                 IdPrimita=e["IdPrimita"]))
                else:
                    operatii.append(dict(_operatie(eticheta, linii, suma_de, partener, nume), Faza="P", IdPrimita=None))
        elif evidenta:
            avertismente.append({"Cod": "CLASIFICATIE_NEALEASA", "Grav": "info", "Text": "Alegeți clasificația pentru a vedea șabloanele."})

        # the subunit of the booking and the dates of the documents (they need the classification)
        date_doc, subunitati, note = {}, [], None
        if clsf is not None and evidenta:
            from routes.avacont.introducere_date.operatii import postare
            cursor.execute("SELECT IdSubunitate, Denumire, Implicita FROM TZ_Subunitati WHERE SursaSector = %s AND Activ = 1 ORDER BY Implicita DESC, Denumire", (clsf["SS"],))
            subunitati = cursor.fetchall()
            try:
                if subunitate is None:
                    subunitate = postare.subunitate_pentru(cursor, clsf["SS"], None)
                for f, e in zip(facturi, evidenta):
                    if f["DataFact"] is None:
                        avertismente.append({"Cod": "FARA_DATA", "Grav": "eroare", "Text": "Factura %s nu are dată." % f["NrFact"]})
                        continue
                    data, motiv_an = postare.ajusteaza_data(f["DataFact"], None, an)
                    data, motiv_luna = postare.ajusteaza_data(data, postare.data_ultimei_inchideri(cursor, subunitate["IdSubunitate"], data.year), None)
                    date_doc[f["IdPrimita"]] = data
                    if data != f["DataFact"]:
                        e["DataDocument"] = _iso(data)
                        avertismente.append({"Cod": "DATA_MUTATA", "Grav": "info", "Text": "Data documentului pentru factura %s este %s (%s)."
                                             % (f["NrFact"], data.strftime("%d.%m.%Y"), motiv_luna or motiv_an)})
            except postare.PostareEroare as err:
                avertismente.append({"Cod": err.reason, "Grav": "eroare", "Text": str(err)})
            if subunitate is not None:
                an_nota = an or (max(d.year for d in date_doc.values()) if date_doc else None)
                if an_nota:
                    randuri_note, urmatoarea = postare.note_ale_subunitatii(cursor, subunitate["IdSubunitate"], an_nota)
                    note = {"An": an_nota, "Urmatoare": urmatoarea,
                            "Existente": [{"NumarNC": n["NumarNC"], "DataNc": _iso(n["DataNc"]), "Documente": n["Documente"]} for n in randuri_note]}

        # the payment row (EF_P)
        fel_op = next((s["FelOP"] for s in sabloane_p if s["IdSablon"] == id_p), "") if id_p else ""
        explicatie = " ".join(x for x in ["F " + " ".join(numere) if numere else "", fel_op] if x)
        if len(explicatie) > MAX_EXPLICATIE_OP:
            avertismente.append({"Cod": "EXPLICATIE_PREA_LUNGA", "Grav": "eroare",
                                 "Text": "Explicația ordinului de plată depășește %d de caractere." % MAX_EXPLICATIE_OP})
        data_minima = max((f["DataFact"] for f in facturi if f["DataFact"] is not None), default=None)
        rezultat = {
            "poate_scrie": (role or "").strip().lower() == primite_ke.ROL_CONTABIL.lower(),
            "furnizor": {"Cui": cui, "Denumire": facturi[0]["DenumireP"] or "", "Partener": partener, "Parteneri": candidati},
            "clasificatii": [{"IdClsf": c["IdClsf"], "Clsf": c["Clsf"], "Denumire": c["Denumire"], "Forma": c["Forma"],
                              "AlePartenerului": c["IdClsf"] in ale_partenerului} for c in oferite],
            "clasificatie": ({"IdClsf": clsf["IdClsf"], "Clsf": clsf["Clsf"], "Denumire": clsf["Denumire"], "Forma": clsf["Forma"]} if clsf else None),
            "clasificatieSugerata": sugerata,
            "subunitate": ({"IdSubunitate": subunitate["IdSubunitate"], "Denumire": subunitate["Denumire"]} if subunitate else None),
            "subunitati": [{"IdSubunitate": s["IdSubunitate"], "Denumire": s["Denumire"]} for s in subunitati],
            "note": note,
            "sabloaneEvidentiere": sabloane_e, "sabloanePlata": sabloane_p, "idSablonE": id_e, "idSablonP": id_p,
            "coduri": coduri, "contIban": cont_iban,
            "evidenta": evidenta,
            "plata": {"Valoare": _num(total_platit), "ExplicatieOP": explicatie, "ExplicatiePrea": EXPLICATIE_OP_GALBEN < len(explicatie),
                      "DataMinima": _iso(data_minima), "Activa": bool(id_p) and total_platit != 0},
            "operatii": operatii, "avertismente": avertismente}
        if interne is not None:
            interne.update(facturi=facturi, ramas=ramas_exacte, partener=partener, clsf=clsf, id_e=id_e, subunitate=subunitate, date_doc=date_doc)
        return rezultat


# ---------------------------------------------------------------------------------------------
# the save of the EVIDENCE (01EF-04)
# ---------------------------------------------------------------------------------------------
def _id_partener(mod, id_fix, partener):
    if mod == "OPERATIE":
        return partener["IdPartener"] if partener else None
    return id_fix if mod == "FIX" else None


def salveaza(dc, body, role, user):
    """Books the EVIDENCE of the ticked invoices through the posting service, in ONE transaction: one accounting note, a document per invoice, an operation per
    template line with its debit and credit, and a row of EF_PrimiteOperatii that ties the operation to the invoice. The payment is 01EF-05.
    body: the same fields as the calculation plus `confirmari` (the codes of the warnings the operator confirmed). Role «Contabil» only (checked here)."""
    primite_ke.cere_contabil(role)
    from routes.avacont.introducere_date.operatii import postare
    confirmari = body.get("confirmari") or []
    if not isinstance(confirmari, list) or any(not isinstance(c, str) for c in confirmari):
        raise EfEroare("Câmpul «confirmari» nu este valid.", "CAMP_INVALID", 400)
    an = _opt_int(body, "an")
    numar_cerut = _opt_int(body, "numarNC")
    with F._unit(dc, CONTARI_SQL) as conn:
        interne = {}
        r = calcul(dc, body, role, conn=conn, interne=interne, blocheaza=True, fara_plata=True)
        clsf, id_e, sub = interne.get("clsf"), interne.get("id_e"), interne.get("subunitate")
        if clsf is None or not id_e:
            raise EfEroare("Alegeți clasificația și șablonul de evidențiere.", "ALEGERE_INCOMPLETA", 400)
        for a in r["avertismente"]:
            if a["Grav"] == "eroare":
                raise EfEroare(a["Text"], "AVERTISMENT_BLOCANT", 409)
        necesare = [a for a in r["avertismente"] if a["Grav"] == "confirmare" and a["Cod"] not in confirmari]
        if necesare:
            err = EfEroare("Confirmați avertismentele înainte de salvare.", "CONFIRMARE_NECESARA", 409)
            err.extra = {"confirmari_necesare": [{"Cod": a["Cod"], "Text": a["Text"]} for a in necesare]}
            raise err
        randuri = [(f, interne["ramas"][f["IdPrimita"]]) for f in interne["facturi"] if primite_ke._cent(interne["ramas"][f["IdPrimita"]]) != 0]
        if not randuri or sub is None:
            raise EfEroare("Nimic de înregistrat: facturile alese sunt deja înregistrate integral.", "NIMIC_DE_INREGISTRAT", 409)
        ani = {interne["date_doc"][f["IdPrimita"]].year for f, _ in randuri}
        an_nota = an or max(ani)
        if ani - {an_nota}:
            raise EfEroare("Data unui document iese din anul de lucru (%d). Alegeți alt an sau corectați data facturii." % an_nota, "AN_DIFERIT", 409)
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute("SELECT IdJurnal, Explicatie, Compartiment FROM TZ_SablonOperatii WHERE IdSablon = %s", (id_e,))
            sablon = cursor.fetchone()
            linii = _linii_sablon(cursor, id_e, clsf["Forma"])
            if sablon is None or not linii:
                raise postare.PostareEroare("Șablonul de evidențiere nu are linie pentru forma %s a clasificației." % clsf["Forma"], "SABLON_FARA_LINIE")
            linie = linii[0]   # a template has at most one line per form (the window allows no more), or one line valid for every form
            sid = sub["IdSubunitate"]
            # the note the operator chose: an existing one takes the new documents, a number not used yet makes a new note with that number, none = the next number
            id_nota = postare.nota_existenta(cursor, sid, an_nota, numar_cerut) if numar_cerut else None
            if id_nota is not None:
                numar_nota = numar_cerut
            else:
                id_nota, numar_nota = postare.creeaza_nota(cursor, sid, an_nota, user, numar_cerut)
            cursor.execute("SELECT COALESCE(MAX(IdOperatiePrimita), 0) AS M FROM EF_PrimiteOperatii FOR UPDATE")
            urmator = int(cursor.fetchone()["M"]) + 1
            partener = interne["partener"]
            activeaza_partener(cursor, partener["IdPartener"])   # the partner the evidence is booked for enters the flow (not hidden) and is kept for the tax code, in this transaction
            leaga_partener(cursor, interne["facturi"][0]["CuiNormalizat"], partener["IdPartener"], user)
            operatii = []
            for f, ramas in randuri:
                suma = primite_ke._cent(ramas)
                data = interne["date_doc"][f["IdPrimita"]]
                id_doc = postare.creeaza_document(cursor, id_nota, numar_document(f["NrFact"]), data, "Notă credit" if f["Tip"] == "NC" else "Factură", "E", True, user)
                cont_d = postare.cont_pentru(cursor, sid, linie["ContDebit"], linie["AnaliticDebit"], clsf["ClsfF"], clsf["ClsfE"])
                cont_c = postare.cont_pentru(cursor, sid, linie["ContCredit"], linie["AnaliticCredit"], clsf["ClsfF"], clsf["ClsfE"])
                antet = {"IdJurnal": sablon["IdJurnal"], "IdClsf": clsf["IdClsf"], "IdSablon": id_e, "DataOperatie": data, "Explicatie": sablon["Explicatie"], "Tip": "E",
                         "Compartiment": sablon["Compartiment"], "Angajament": linie["PA"], "Propunere": linie["PA"], "Ordonantare": linie["Ordonantare"],
                         "Chelt": linie["Chelt"], "Auto": True}
                id_oper, nr_ang = postare.creeaza_operatie(
                    cursor, sid, an_nota, id_doc, antet,
                    [(cont_d, _id_partener(linie["PartDebitMod"], linie["IdPartDebit"], partener), suma)],
                    [(cont_c, _id_partener(linie["PartCreditMod"], linie["IdPartCredit"], partener), suma)], user)
                cursor.execute("INSERT INTO EF_PrimiteOperatii (IdOperatiePrimita, IdPrimita, IdOperatie, IdClsf, IdUnitate, Sursa, Suma) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                               (urmator, f["IdPrimita"], id_oper, clsf["IdClsf"], clsf["IdUnitate"], clsf["SS"], suma))
                urmator += 1
                operatii.append({"IdPrimita": f["IdPrimita"], "IdDocument": id_doc, "IdOper": id_oper, "NrAng": nr_ang})
        except postare.PostareEroare as err:
            raise EfEroare(str(err), err.reason, err.status) from err
        conn.commit()
    return {"IdNota": id_nota, "NumarNC": numar_nota, "An": an_nota, "Subunitate": sub["Denumire"], "Operatii": operatii}
