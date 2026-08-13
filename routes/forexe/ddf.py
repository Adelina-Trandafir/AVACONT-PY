# routes/forexe/ddf.py
"""
Ruta DDF pentru frmFX_MAIN_DDF (felia 0020, vederea DDF).

Contract (GET /api/forexe/ddf?cod=<CodAngajament>[&pentru_generare=1]):
    { "cod": "<CodAngajament>",
      "antet":     [ {...}, ... ],   # FX_DDF        — ARRAY, nu obiect (vezi mai jos)
      "revizii":   [ {...}, ... ],   # FX_DDF_REV    — un rand per IDREV, cu SUM-ul real
      "linii":     [ {...}, ... ],   # FX_DDF_REV_SA — un rand per IdSecA
      "sectiuneb": [ {...}, ... ],   # FX_DDF_REV_SB — GOL fara pentru_generare (felia 05)
      "atasamente":[ {...}, ... ] }  # FX_DDF_REV_ATT— GOL fara pentru_generare (base64 mare)

`sectiuneb` si `atasamente` sunt OPT-IN printr-un SINGUR flag `pentru_generare=1` (ABATERE
de la schita planului, care cerea un `atasamente=1` doar pentru atasamente): amandoua sunt
necesare DOAR la generarea PDF-ului, deci un singur flag e mai curat si evita o incarcatura
de generare pe jumatate. Fara flag, cele doua array-uri sunt GOALE — vederea nu poarta date
pe care nu le arata (§2.8).

Scope: baza conectata ESTE unitatea (o baza MariaDB = o unitate), deci nu exista
parametru db_name / id_unitate — baza vine din sesiune (g.session.db_name), exact
ca la /api/forexe/tree, /sumar, /rezervari, /receptii si /plati. Un token nu poate
tinti alta baza decat cea pe care s-a logat.

Un singur drum dus-intors pentru tot CodAngajament-ul: clientul (DdfView) filtreaza
LOCAL (arbore luna -> revizie, grila, combo-ul de clasificatii), fara alte cereri.

Sursa Access (verificata in export, NU reghicita):
  - qFX_MAIN_DDF_TREE : D INNER JOIN R INNER JOIN SA, cu `SA.ValCur AS TotalRevizie`.

DEVIERE DELIBERATA (defect Access reprodus INTENTIONAT gresit aici):
  `TotalRevizie` din Access este valoarea UNEI SINGURE linii de sectiune A, purtand
  numele unui total. Show_Revizii parcurge randurile si cheama AddTree_Leaf cu cheia
  "RC_" & IDREV, deci o revizie cu trei clasificatii incearca aceeasi frunza de trei
  ori si afiseaza valoarea unei linii ARBITRARE (ultima castiga). Aceeasi familie de
  defect ca fan-out-ul `aggRev` din 0011-03.
  -> Aici `TotalRevizie` este un SUM(ValCur) REAL per revizie, calculat printr-o
     SUBINTEROGARE SCALARA. Clientul NU insumeaza niciodata un fan-out.

FAN-OUT — de ce NU se face join pe antet (abatere de la schita planului):
  `FX_DDF` are PRIMARY KEY COMPUS (IDDF, CUAL) si NICIO constrangere unica pe
  CodAngajament. Un `FX_DDF_REV INNER JOIN FX_DDF ON IDDF` ar DUBLA fiecare revizie
  daca acelasi IDDF poarta doua randuri CUAL. De aceea `revizii` si `linii` se
  filtreaza prin `IN (SELECT ...)`, nu prin join: „un rand per IDREV" / „un rand per
  IdSecA" ramane o proprietate a FORMEI interogarii, nu una pe care o poate strica
  cineva editand un GROUP BY mai tarziu. (Lectia 0011-03.)

`antet` este un ARRAY, nu un obiect: nimic in schema nu impune UN SINGUR antet per
angajament (vezi mai sus), desi fiecare interogare Access presupune ca exista unul.
Clientul alege randul care se potriveste cu AngajamentTreeInfo.IDDF cand acesta e
setat, altfel primul, si LOGHEAZA un avertisment cand numarul e mai mare ca 1. Nu are
voie sa aleaga tacut.

Clasificatia (`Clsf`) — NU are nevoie de tratamentul IdClsfAcc + IdUnitate din 0011-03:
  - `routes/ddf/sync_acc_mdb.py` (liniile 7-8) documenteaza maparea:
        Access.IdClsf   -> MariaDB.IdClsfAcc
        Access.IdClsfPY -> MariaDB.IdClsf   (FK Clasificatii)
    iar DDL-ul confirma: `FX_DDF_REV_SA_ibfk_4 FOREIGN KEY (IdClsf) REFERENCES
    Clasificatii (IDClsf)`. Pe ACEASTA tabela MariaDB.IdClsf tine deja CHEIA PRIMARA
    a nomenclatorului — INVERS fata de FX_Indicatori, unde IdClsf tine id-ul Access.
  - Cheia pe `Clasificatii.IDClsf` este unica prin definitie -> fara fan-out, deci
    NU se pune predicat IdUnitate (in plus, `FX_DDF_REV_SA.IdUnitate` e nullable si
    gol in export).
  - `FX_DDF_REV_SA.Clsf` este o coloana TEXT denormalizata si POPULATA, deci se
    prefera ea, cu cadere pe nomenclator doar cand e goala. `Clasificatii.Clsf` este
    coloana GENERATED PERSISTENT (Capitol.Subcapitol.Articol.Alineat), confirmata in DDL.

Cele patru coloane de evidenta a PDF-ului (ArePDFDDF / CalePDFDDF / AreDDF / CaleDDF)
NU exista in MariaDB — au fost scoase deliberat la migrare (`sync_acc_mdb.py` o spune
explicit, DDL-ul o confirma). Serverul NU stie nimic despre PDF-uri: existenta unui
document se decide EXCLUSIV printr-o scanare de disc pe client, iar K-BOT nu scrie
nicio cale inapoi in baza.
"""
import json
import logging

from flask import request, g, current_app

from routes.auth.guard import require_session
from utils.database import get_db_connection

from . import forexe_bp

logger = logging.getLogger(__name__)

# Antetul DDF al angajamentului. ARRAY (vezi nota de modul): nimic nu impune unicitatea
# pe CodAngajament, iar PK-ul e compus (IDDF, CUAL). ORDER BY IDDF, CUAL = ordine stabila
# intre refresh-uri, ca alegerea „primul rand" a clientului sa fie determinista.
_SQL_ANTET = (
    "SELECT "
    "IDDF, CodAngajament, CUAL, ObiectDDF, Comp, Program, "
    "DataCreare, DataDef, Stare, PartAng, CodFiscal, NumePartener, "
    "Salarii, Incarcat, Preluat "
    "FROM FX_DDF "
    "WHERE CodAngajament = %s "
    "ORDER BY IDDF, CUAL"
)

# Un rand per IDREV. `TotalRevizie` = SUM(ValCur) REAL peste sectiunea A a reviziei,
# ca SUBINTEROGARE SCALARA (nu join + GROUP BY): forma interogarii garanteaza singura
# „un rand per revizie". COALESCE -> o revizie fara linii de sectiune A da 0, nu NULL.
# Filtrul e `IN (SELECT ...)`, nu un join pe antet — vezi nota FAN-OUT din modul.
_SQL_REVIZII = (
    "SELECT "
    "r.IDREV, r.IDDF, r.NumarRev, r.DataRev, r.Desc_Scurta, r.Desc_Lunga_ANSI, "
    "r.Tip, r.Incarcat, r.Preluat, r.Semnatura, "
    "COALESCE((SELECT SUM(sa.ValCur) FROM FX_DDF_REV_SA sa "
    "          WHERE sa.IDREV = r.IDREV), 0) AS TotalRevizie "
    "FROM FX_DDF_REV r "
    "WHERE r.IDDF IN (SELECT IDDF FROM FX_DDF WHERE CodAngajament = %s) "
    "ORDER BY r.DataRev, r.NumarRev"
)

# Un rand per IdSecA (PK FX_DDF_REV_SA), pentru TOATE reviziile angajamentului.
# `Clsf` = coloana denormalizata daca e populata, altfel nomenclatorul prin subinterogare
# scalara cu LIMIT 1 (cheiata pe IDClsf = PK -> unica prin definitie, fara IdUnitate).
# `ParametriiFund` NU se afiseaza in grila (decizia 4 a operatorului), dar se poarta pe fir:
# constructorul de XML al feliei 05 il scrie in Cell4 al sectiunii A.
# `SS` (felia 05) — pentru Cell3 al form1 (`SS + Left(Clsf,2) + ...`) si pentru `codSSI` din
# NOTAFD. Access citeste `rsSA!SS` in form1 si `Clasificatii.CodSSI` in NOTAFD; in MariaDB
# `Clasificatii` NU are `CodSSI` — are coloana GENERATED `SS` (Sector+Sursa) — deci un singur
# `ss` (denormalizatul de pe linie, cu cadere pe nomenclator) le acopera pe amandoua.
_SQL_LINII = (
    "SELECT "
    "sa.IdSecA, sa.IDREV, sa.IdClsf, "
    "COALESCE(NULLIF(sa.Clsf, ''), "
    "         (SELECT c.Clsf FROM Clasificatii c "
    "          WHERE c.IDClsf = sa.IdClsf LIMIT 1)) AS Clsf, "
    "COALESCE(NULLIF(sa.SS, ''), "
    "         (SELECT c.SS FROM Clasificatii c "
    "          WHERE c.IDClsf = sa.IdClsf LIMIT 1)) AS SS, "
    "sa.ElementFund, sa.ParametriiFund, sa.ValPrec, sa.ValCur, sa.ValTot "
    "FROM FX_DDF_REV_SA sa "
    "WHERE sa.IDREV IN ("
    "  SELECT IDREV FROM FX_DDF_REV "
    "  WHERE IDDF IN (SELECT IDDF FROM FX_DDF WHERE CodAngajament = %s)) "
    "ORDER BY sa.IDREV, Clsf"
)

# Sectiunea B (FX_DDF_REV_SB) — felia 05. NU se afiseaza nicaieri (decizia 2 a operatorului
# se aplica grilei si sub-navigarii), dar PDF-ul o cere: `GenereazaXML_PentruPython` scrie
# `SubformSectiuneaB/Table3` si `GenereazaXML_NOTAFD` scrie `sectiuneaB/rowT_ang_ctrl_ang`.
# Coloanele sunt exact cele citite de constructorul de XML (Cell7 e sarit intentionat acolo).
# Nu se trimite decat cand `pentru_generare=1` — vederea nu poarta date pe care nu le arata.
_SQL_SECTIUNEB = (
    "SELECT "
    "sb.IdSecB, sb.IDREV, sb.CodAngajament, sb.CodIndicator, sb.CodSSI, "
    "sb.CA_Anterior, sb.Inf1, sb.CB_Anterior, sb.Inf2 "
    "FROM FX_DDF_REV_SB sb "
    "WHERE sb.IDREV IN ("
    "  SELECT IDREV FROM FX_DDF_REV "
    "  WHERE IDDF IN (SELECT IDDF FROM FX_DDF WHERE CodAngajament = %s)) "
    "ORDER BY sb.IDREV, sb.IdSecB"
)

# Atasamentele (FX_DDF_REV_ATT) — felia 05. `DateFisier` e un `longtext` de base64, deci
# array-ul e OPT-IN (`pentru_generare=1`), niciodata pe un simplu click de arbore.
_SQL_ATASAMENTE = (
    "SELECT "
    "a.IdRevAtt, a.IDREV, a.CaleFisier, a.PrtScr, a.DateFisier "
    "FROM FX_DDF_REV_ATT a "
    "WHERE a.IDREV IN ("
    "  SELECT IDREV FROM FX_DDF_REV "
    "  WHERE IDDF IN (SELECT IDDF FROM FX_DDF WHERE CodAngajament = %s)) "
    "ORDER BY a.IDREV, a.IdRevAtt"
)


def _json_utf8(payload, status):
    """Raspuns JSON cu diacritice LITERALE (ensure_ascii=False): ObiectDDF / Desc_Scurta /
    ElementFund contin text romanesc si trebuie sa ajunga la client ca UTF-8 real, nu \\uXXXX."""
    body = json.dumps(payload, ensure_ascii=False)
    return current_app.response_class(body, status=status, mimetype="application/json")


def _iso(value):
    """DateTime/Date -> 'YYYY-MM-DD' (ISO) sau None.

    DataRev este DATE in schema; DataCreare/DataDef sunt DATETIME. Vederea grupeaza si
    afiseaza pe zi, deci .date() taie ora deterministic pentru cele din urma.
    """
    if value is None:
        return None
    try:
        return value.date().isoformat()
    except AttributeError:
        return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _num(value):
    """Coloana de bani -> float. DOUBLE vine ca float; None devine 0.0 (server-side), ca
    grila si totalurile arborelui sa arate «0,00», nu gol."""
    return float(value) if value is not None else 0.0


def _truthy(value):
    """Flag din query string -> bool. Acceptam «1»/«true»/«yes» (case-insensitive)."""
    if value is None:
        return False
    return str(value).strip().lower() in ("1", "true", "yes")


@forexe_bp.route("/api/forexe/ddf", methods=["GET"])
@require_session
def get_ddf():
    """DDF-ul unui angajament: antet(e) + revizii (cu SUM real) + linii de sectiune A.

    Query: cod (obligatoriu) = CodAngajament.

    Un `cod` necunoscut / fara DDF NU este 404: un angajament fara document de fundamentare
    este legitim, deci raspunsul este 200 cu toate cele trei liste goale. Apelantul trebuie
    sa poata distinge „nu are DDF" de „a cazut transportul" — un 404 ar amesteca cele doua.
    """
    cod = request.args.get("cod")
    if cod is None or str(cod).strip() == "":
        return _json_utf8({"error": "Parametru lipsă: cod"}, 400)
    cod = str(cod).strip()

    # Scope: baza sesiunii, niciodata din cerere (o baza = o unitate).
    db_name = g.session.db_name

    conn = None
    try:
        conn = get_db_connection(db_name)
        cursor = conn.cursor()

        # --- antet: FX_DDF (array, vezi nota de modul) ---------------------------------
        # SQL parametrizat — `cod` nu se interpoleaza NICIODATA in text.
        cursor.execute(_SQL_ANTET, (cod,))
        antet = []
        for (iddf, cod_angajament, cual, obiect_ddf, comp, program,
             data_creare, data_def, stare, part_ang, cod_fiscal, nume_partener,
             salarii, incarcat, preluat) in cursor.fetchall():
            antet.append({
                "iddf": int(iddf) if iddf is not None else None,
                "cod_angajament": cod_angajament,
                # CUAL intra in numele fisierului PDF (DDF_NR_{CUAL}_REV_{NumarRev}_{Cod}.PDF).
                "cual": int(cual) if cual is not None else None,
                "obiect_ddf": obiect_ddf,
                "comp": comp,
                "program": program,
                "data_creare": _iso(data_creare),
                "data_def": _iso(data_def),
                "stare": stare,
                # PartAng conduce alegerea folderului: partener vs GENERAL (vezi §2.5).
                "part_ang": bool(part_ang),
                "cod_fiscal": cod_fiscal,
                "nume_partener": nume_partener,
                "salarii": bool(salarii),
                "incarcat": bool(incarcat),
                "preluat": bool(preluat),
            })

        # --- revizii: FX_DDF_REV, cu SUM(ValCur) real ----------------------------------
        cursor.execute(_SQL_REVIZII, (cod,))
        revizii = []
        for (idrev, iddf, numar_rev, data_rev, desc_scurta, desc_lunga,
             tip, incarcat, preluat, semnatura, total_revizie) in cursor.fetchall():
            revizii.append({
                "idrev": int(idrev) if idrev is not None else None,
                "iddf": int(iddf) if iddf is not None else None,
                # NumarRev intra in numele PDF-ului; Access il formateaza cu Format(.,"@@@")
                # = aliniere la dreapta in 3 caractere cu SPATII (nu zerouri) -> PadLeft(3)
                # pe client, niciodata D3/000.
                "numar_rev": int(numar_rev) if numar_rev is not None else None,
                "data_rev": _iso(data_rev),
                "desc_scurta": desc_scurta,
                "desc_lunga": desc_lunga,
                "tip": tip,
                "incarcat": bool(incarcat),
                "preluat": bool(preluat),
                "semnatura": semnatura,
                # SUM real peste sectiunea A — NU valoarea unei linii arbitrare (vezi nota).
                "total_revizie": _num(total_revizie),
            })

        # --- linii: FX_DDF_REV_SA ------------------------------------------------------
        cursor.execute(_SQL_LINII, (cod,))
        linii = []
        for (id_sec_a, idrev, id_clsf, clsf, ss, element_fund, parametrii_fund,
             val_prec, val_cur, val_tot) in cursor.fetchall():
            linii.append({
                "id_sec_a": int(id_sec_a) if id_sec_a is not None else None,
                "idrev": int(idrev) if idrev is not None else None,
                "id_clsf": int(id_clsf) if id_clsf is not None else 0,
                "clsf": clsf,
                # SS efectiv al liniei (Cell3 al form1 + codSSI din NOTAFD). Nefolosit de grila.
                "ss": ss,
                "element_fund": element_fund,
                # Nefolosit de grila (decizia 4), purtat pentru constructorul de XML (felia 05).
                "parametrii_fund": parametrii_fund,
                "val_prec": _num(val_prec),
                "val_cur": _num(val_cur),
                "val_tot": _num(val_tot),
            })

        # --- sectiuneb + atasamente: OPT-IN, doar pentru generare (felia 05) ------------
        # Vederea nu poarta date pe care nu le arata (§2.8); constructorul de XML le cere.
        sectiuneb = []
        atasamente = []
        pentru_generare = _truthy(request.args.get("pentru_generare"))
        if pentru_generare:
            cursor.execute(_SQL_SECTIUNEB, (cod,))
            for (id_sec_b, idrev, cod_ang, cod_indicator, cod_ssi,
                 ca_anterior, inf1, cb_anterior, inf2) in cursor.fetchall():
                sectiuneb.append({
                    "id_sec_b": int(id_sec_b) if id_sec_b is not None else None,
                    "idrev": int(idrev) if idrev is not None else None,
                    "cod_angajament": cod_ang,
                    "cod_indicator": cod_indicator,
                    "cod_ssi": cod_ssi,
                    "ca_anterior": _num(ca_anterior),
                    "inf1": _num(inf1),
                    "cb_anterior": _num(cb_anterior),
                    "inf2": _num(inf2),
                })

            cursor.execute(_SQL_ATASAMENTE, (cod,))
            for (id_rev_att, idrev, cale_fisier, prt_scr, date_fisier) in cursor.fetchall():
                atasamente.append({
                    "id_rev_att": int(id_rev_att) if id_rev_att is not None else None,
                    "idrev": int(idrev) if idrev is not None else None,
                    "cale_fisier": cale_fisier,
                    "prt_scr": bool(prt_scr),
                    # longtext base64 — mare; ajunge doar cand pentru_generare=1.
                    "date_fisier": date_fisier,
                })

        if len(antet) > 1:
            # Nu e o eroare (schema o permite), dar clientul trebuie sa aleaga EXPLICIT.
            logger.warning("[forexe.ddf] %s: cod=%s are %s randuri de antet FX_DDF",
                           db_name, cod, len(antet))

        logger.info("[forexe.ddf] %s: cod=%s -> antet=%s revizii=%s linii=%s "
                    "sectiuneb=%s atasamente=%s (generare=%s)",
                    db_name, cod, len(antet), len(revizii), len(linii),
                    len(sectiuneb), len(atasamente), pentru_generare)
        return _json_utf8(
            {"cod": cod, "antet": antet, "revizii": revizii, "linii": linii,
             "sectiuneb": sectiuneb, "atasamente": atasamente}, 200)
    except Exception as e:
        # Fara inghitire: o eroare de baza intoarce motivul, NU liste goale — listele goale
        # ar minti operatorul ca angajamentul nu are document de fundamentare.
        logger.error(f"[forexe.ddf] {e}", exc_info=True)
        return _json_utf8({"error": f"Eroare la citirea DDF-ului: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()
