# routes/forexe/ord.py
"""
Ruta ORD pentru vederea Ordonantari (felia 0033).

Contract (GET /api/forexe/ord?cod=<CodAngajament>):
    { "cod": "<CodAngajament>",
      "ordonantari": [ {...}, ... ],   # FX_ORD     — un rand per IDORDP, cu SUM-ul real
      "linii":       [ {...}, ... ] }  # FX_ORD_TBL — un rand per IDORDTBLP, PLAT, dar
                                       #   fiecare linie isi poarta beneficiarul (FX_ORD_PART)

Scope: baza conectata ESTE unitatea (o baza MariaDB = o unitate), deci nu exista
parametru db_name / id_unitate — baza vine din sesiune (g.session.db_name), exact ca la
/api/forexe/tree, /sumar, /rezervari, /receptii, /plati si /ddf.

Un singur drum dus-intors pentru tot CodAngajament-ul: clientul (OrdView) construieste
arborele din `ordonantari` si, la click pe un nod, FILTREAZA `linii` dupa `idordp` — fara
alta cerere de retea (aceeasi decizie 7 ca la DDF).

Sursa Access (verificata in export, NU reghicita):
  - qFX_ORD_TREE      : FX_ORD x FX_DDF x FX_ORD_PART x FX_ORD_TBL, Sum(Valoare) AS TotalORD,
                        filtrat pe FX_DDF.CodAngajament.
  - qFX_MAIN_ORD_TBL  : liniile, prin ClasificatiiG.

CELE TREI CAPCANE ale familiei FX_ORD, tratate explicit:

1. CHEILE «...P» sunt cheile MariaDB. `FX_ORD.IDORDP`, `FX_ORD_TBL.IDORDTBLP`,
   `FX_ORD_PART.IDORDPARTP` sunt cheile REALE ale bazei; omonimele fara «P» (IDORD,
   IDORDTBL, IDORDPART) sunt id-urile Access PASTRATE. Toate legaturile de aici merg pe
   «...P» (`t.IDORDP = o.IDORDP`) — un port literal al join-ului Access (`IDORDPART`)
   ar lega cheia gresita. Aceeasi familie de defect ca `aggOrd`.

2. `ClasificatiiG` NU EXISTA in MariaDB. Clasificatia se rezolva prin `Clasificatii`
   (vezi nota urmatoare), niciodata prin interogarea Access ca atare.

3. INVERSIUNEA `IdClsf`. In `FX_ORD_TBL`, MariaDB `IdClsf` este FK-ul catre
   `Clasificatii` (id-ul global/PY) iar `IdClsfAcc` este id-ul Access pastrat —
   documentat in `routes/ord/sync_mdb_acc.py` (liniile 6-8) si in `sync_acc_mdb.py`.
   INVERS fata de `FX_Indicatori`, unde `IdClsf` tine id-ul Access.

CLASIFICATIA (`clsf` / `descriere`) — de ce AMANDOUA drumurile, intr-un COALESCE:
  - Drumul DIRECT (`Clasificatii.IDClsf = t.IdClsf`) e cel documentat de sync-ul ORD si e
    acelasi tipar ca `FX_DDF_REV_SA` din routes/forexe/ddf.py: cheia e PK -> unica prin
    definitie, deci fara fan-out si fara predicat IdUnitate.
  - Drumul de REZERVA (`FX_Indicatori` pe CodAI -> `Clasificatii.IdClsfAcc + IdUnitate`) e
    cel VERIFICAT live in 0011-03 si folosit de plati.py / receptii.py.
  - Planul cerea „alege unul dupa o proba pe date reale". Proba pe date reale NU s-a putut
    face (ruta n-a atins niciodata o baza vie), deci se incearca intai directul si se cade
    pe cel verificat cand primul e NULL/gol — in loc sa se ghiceasca unul si sa iasa o
    coloana goala in productie (capcana `Clsf` gol din 0011-03 / 0015).
  `FX_ORD_TBL` NU are coloana `Clsf` denormalizata (vezi lista de coloane din
  routes/ord/tbl.py), deci nu exista a treia varianta.

FAN-OUT: `total_ord` e o SUBINTEROGARE SCALARA peste `FX_ORD_TBL`, nu un
`JOIN ... GROUP BY`. Access aduna peste un join cu `FX_ORD_PART` — aici forma interogarii
garanteaza singura „un rand per ordonantare", indiferent cati beneficiari are.
La fel, `linii` se filtreaza prin `IN (SELECT ...)`, nu prin join pe antet.

PDF-ul (`pdf`): Access tine `FX_ORD.ArePDF` / `FX_ORD.CalePDF`, dar NICIUNA din rutele de
migrare (`routes/ord/sync_acc_mdb.py`, `commit.py`) nu le scrie — exact ca la cele patru
coloane PDF ale DDF-ului, scoase deliberat la migrare. Nu se poate sti offline daca
`CalePDF` mai exista in MariaDB, iar un SELECT pe o coloana inexistenta ar da 500 pe toata
ruta. De aceea coloana se PROBEAZA o data (information_schema, memorat per baza) si campul
`pdf` se trimite doar cand exista cu adevarat; altfel e `None`.
`pdf` este oricum DOAR UN SEMNAL (confirma conventia de nume) — clientul isi calculeaza
singur calea locala din `KBotPaths` + NrORD + Cod si verifica existenta pe DISCUL LUI.
"""
import json
import logging

from flask import request, g, current_app

from routes.auth.guard import require_session
from utils.database import get_db_connection

from . import forexe_bp

logger = logging.getLogger(__name__)

# Rezultatul probei „exista FX_ORD.CalePDF?", memorat per baza: proba se face o data pe
# proces si pe baza, nu la fiecare cerere. Schema nu se schimba sub picioarele serverului.
_cale_pdf_cache: dict = {}

_SQL_ARE_CALE_PDF = (
    "SELECT COUNT(*) FROM information_schema.COLUMNS "
    "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'FX_ORD' AND COLUMN_NAME = 'CalePDF'"
)

# Un rand per IDORDP (PK MariaDB — capcana 1). `TotalORD` = SUM(Valoare) peste liniile
# ordonantarii, ca SUBINTEROGARE SCALARA (vezi nota FAN-OUT). COALESCE -> o ordonantare
# fara linii da 0, nu NULL.
#
# `PartAng` / `NumePartener` vin din FX_DDF prin `o.IDDF`, tot ca subinterogari scalare cu
# LIMIT 1: PK-ul FX_DDF e COMPUS (IDDF, CUAL), deci acelasi IDDF poate purta mai multe
# randuri, iar un join ar dubla ordonantarea. Ele nu se afiseaza — compun folderul PDF-ului
# (partener normalizat vs «GENERAL»), exact regula din mdl_FX_ORD_PDF.
#
# `{cale_pdf}` se completeaza doar cand coloana exista pe baza (vezi nota de modul).
_SQL_ORDONANTARI = (
    "SELECT "
    "o.IDORDP, o.NrORD, o.DataORD, o.Incarcat, o.Preluat, "
    "COALESCE((SELECT SUM(t.Valoare) FROM FX_ORD_TBL t "
    "          WHERE t.IDORDP = o.IDORDP), 0) AS TotalORD, "
    "(SELECT d.PartAng FROM FX_DDF d WHERE d.IDDF = o.IDDF LIMIT 1) AS PartAng, "
    "(SELECT d.NumePartener FROM FX_DDF d WHERE d.IDDF = o.IDDF LIMIT 1) AS NumePartener, "
    # Felia 0041 — evidenta PDF-ului SEMNAT stocat pe server. LEFT JOIN (nu subinterogare):
    # `FX_ORD_PDF.IDORDP` are cheie UNICA, deci cel mult un rand per ordonantare. Cele trei
    # coloane stau INAINTEA fragmentului optional `{cale_pdf}`, ca pozitiile fixe din
    # despachetarea de mai jos sa nu depinda de proba de schema.
    "p.Sha256, p.Dimensiune, p.DataModif"
    "{cale_pdf} "
    "FROM FX_ORD o "
    "LEFT JOIN FX_ORD_PDF p ON p.IDORDP = o.IDORDP "
    "WHERE o.CodAngajament = %s "
    "ORDER BY o.DataORD, o.NrORD"
)

# Toate liniile ordonantarilor angajamentului. Lista ramane PLATA (un rand per IDORDTBLP,
# fara grupare pe beneficiar in raspuns), dar fiecare linie isi POARTA acum beneficiarul —
# vezi nota BENEFICIARUL de mai jos. Fiecare linie poarta si `IDORDP`, ca sa poata fi
# filtrata pe client dupa nodul selectat. Legatura cu antetul: `IN (SELECT IDORDP ...)`,
# nu join (vezi nota FAN-OUT).
#
# Clasificatia: COALESCE(drum direct, drum verificat) — vezi nota de modul. `NULLIF(..., '')`
# fiindca un cod gol e la fel de inutil ca un NULL.
#
# BENEFICIARUL (`den_bene` / `cod_fiscal` / `cont_iban`), documentele justificative
# (`doc_just`) si obiectul DDF (`obiect_ddf`) — sursele lor, verificate in export, NU
# reghicite:
#   * `FX_ORD_PART` (DenBene / CodFiscal / ContIBAN) e legata de linie prin `t.IDORDPARTP`.
#     JOIN si nu subinterogare fiindca `IDORDPARTP` e CHEIA PRIMARA a lui FX_ORD_PART (vezi
#     routes/ord/part.py) -> un singur rand prin definitie, deci fan-out imposibil. LEFT,
#     pentru ca o linie migrata fara PART parinte nu are voie sa DISPARA din grila.
#     Corespondentul Access e frmFX_ORD_PART (lstDenBene + CodFiscal/ContIBAN/Banca).
#     `Banca` NU se trimite: pagina nu are camp pentru ea.
#   * `FX_ORD_DOC` are MAI MULTE randuri per beneficiar, deci ar produce fan-out intr-un
#     join — se aduna cu GROUP_CONCAT intr-o subinterogare scalara, ordonat determinist.
#   * `ObiectDDF` sta in `FX_DDF`, la care se ajunge prin `FX_ORD.IDDF`. `LIMIT 1` fiindca
#     PK-ul FX_DDF e COMPUS (IDDF, CUAL) — acelasi IDDF poate purta mai multe randuri
#     (aceeasi capcana ca la `PartAng` / `NumePartener` din _SQL_ORDONANTARI).
#     Se pune pe LINIE, nu pe antet, ca subsolul paginii sa se poata umple si pe o radacina
#     de luna, unde randul selectat poate veni din oricare ordonantare a lunii.
_SQL_LINII = (
    "SELECT "
    "t.IDORDTBLP, t.IDORDP, t.IDORDPARTP, "
    "COALESCE(NULLIF((SELECT c.Clsf FROM Clasificatii c "
    "                 WHERE c.IDClsf = t.IdClsf LIMIT 1), ''), "
    "         (SELECT c.Clsf FROM Clasificatii c "
    "          WHERE c.IdClsfAcc = i.IdClsf AND c.IdUnitate = i.IdUnitate LIMIT 1)) AS Clsf, "
    "COALESCE(NULLIF((SELECT c.Denumire FROM Clasificatii c "
    "                 WHERE c.IDClsf = t.IdClsf LIMIT 1), ''), "
    "         (SELECT c.Denumire FROM Clasificatii c "
    "          WHERE c.IdClsfAcc = i.IdClsf AND c.IdUnitate = i.IdUnitate LIMIT 1)) AS Denumire, "
    "t.TotalReceptii, t.PlatiAnt, t.Valoare, t.Ramas, "
    "p.DenBene, p.CodFiscal, p.ContIBAN, "
    "(SELECT GROUP_CONCAT(DISTINCT d.DocJust ORDER BY d.DocJust SEPARATOR ', ') "
    "   FROM FX_ORD_DOC d WHERE d.IDORDPARTP = t.IDORDPARTP) AS DocJust, "
    "(SELECT f.ObiectDDF FROM FX_DDF f "
    "  JOIN FX_ORD o ON o.IDDF = f.IDDF "
    "  WHERE o.IDORDP = t.IDORDP LIMIT 1) AS ObiectDDF "
    "FROM FX_ORD_TBL t "
    "LEFT JOIN FX_Indicatori i ON i.CodAI = t.CodAI "
    "LEFT JOIN FX_ORD_PART p ON p.IDORDPARTP = t.IDORDPARTP "
    "WHERE t.IDORDP IN (SELECT IDORDP FROM FX_ORD WHERE CodAngajament = %s) "
    "ORDER BY t.IDORDP, p.DenBene, Clsf, t.IDORDTBLP"
)


def _json_utf8(payload, status):
    """Raspuns JSON cu diacritice LITERALE (ensure_ascii=False): `Denumire` clasificatiei si
    numele partenerilor contin text romanesc si trebuie sa ajunga la client ca UTF-8 real."""
    body = json.dumps(payload, ensure_ascii=False)
    return current_app.response_class(body, status=status, mimetype="application/json")


def _iso(value):
    """DateTime/Date -> 'YYYY-MM-DD' (ISO) sau None. DataORD e DATETIME in Access; vederea
    grupeaza pe luna si afiseaza pe zi, deci .date() taie ora deterministic."""
    if value is None:
        return None
    try:
        return value.date().isoformat()
    except AttributeError:
        return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _iso_dt(value):
    """DateTime -> ISO CU ora, sau None. Sora lui `_iso`, dar fara `.date()`: `DataModif` din
    FX_ORD_PDF spune CAND s-a semnat documentul, iar taierea orei ar face doua semnari din
    aceeasi zi sa arate identic."""
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _num(value):
    """Coloana de bani -> float. None devine 0.0 (server-side), ca grila si totalurile
    arborelui sa arate «0,00», nu gol."""
    return float(value) if value is not None else 0.0


def _are_cale_pdf(cursor, db_name: str) -> bool:
    """Exista `FX_ORD.CalePDF` pe baza asta? Proba se face O SINGURA DATA per baza.

    O proba esuata NU e o eroare de ruta: se raspunde «nu exista» si se logheaza, fiindca
    `pdf` e un camp de semnal, iar lipsa lui nu impiedica vederea sa functioneze.
    """
    if db_name in _cale_pdf_cache:
        return _cale_pdf_cache[db_name]
    try:
        cursor.execute(_SQL_ARE_CALE_PDF, (db_name,))
        row = cursor.fetchone()
        exista = bool(row and row[0])
    except Exception as e:
        logger.warning("[forexe.ord] %s: proba FX_ORD.CalePDF a esuat (%s); se continua fara", db_name, e)
        exista = False
    _cale_pdf_cache[db_name] = exista
    return exista


@forexe_bp.route("/api/forexe/ord", methods=["GET"])
@require_session
def get_ord():
    """Ordonantarile unui angajament: antete (cu SUM real) + liniile lor.

    Query: cod (obligatoriu) = CodAngajament.

    Un `cod` necunoscut / fara ordonantari NU este 404: un angajament fara ORD este
    legitim, deci raspunsul este 200 cu ambele liste goale. Apelantul trebuie sa poata
    distinge „nu are ordonantari" de „a cazut transportul" — un 404 ar amesteca cele doua.
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

        # --- ordonantari: FX_ORD, cu SUM(Valoare) real ---------------------------------
        are_cale = _are_cale_pdf(cursor, db_name)
        sql_ord = _SQL_ORDONANTARI.format(cale_pdf=", o.CalePDF" if are_cale else "")
        # SQL parametrizat — `cod` nu se interpoleaza NICIODATA in text. (Fragmentul
        # `{cale_pdf}` e un literal ales de server, nu date de la client.)
        cursor.execute(sql_ord, (cod,))
        ordonantari = []
        for row in cursor.fetchall():
            (idordp, nr_ord, data_ord, incarcat, preluat,
             total_ord, part_ang, nume_partener,
             pdf_sha, pdf_dim, pdf_modif) = row[:11]
            cale_pdf = row[11] if are_cale else None
            ordonantari.append({
                "idordp": int(idordp) if idordp is not None else None,
                "nr_ord": int(nr_ord) if nr_ord is not None else 0,
                "data_ord": _iso(data_ord),
                "total_ord": _num(total_ord),
                # Semnal, NU cale de deschis: clientul isi calculeaza calea locala
                # (KBotPaths + NrORD + Cod) si verifica existenta pe discul lui.
                "pdf": cale_pdf,
                # Compun folderul PDF-ului pe client (partener normalizat vs «GENERAL»),
                # dupa aceeasi regula ca DDF (mdl_FX_ORD_PDF citeste FX_DDF prin IDDF).
                "part_ang": bool(part_ang),
                "nume_partener": nume_partener,
                "incarcat": bool(incarcat),
                "preluat": bool(preluat),
                # Felia 0041 — PDF-ul SEMNAT stocat pe server. `pdf_sha256` non-null INSEAMNA
                # «exista PDF semnat» si e si validatorul de cache local al clientului. Null
                # cand nu exista rand. Distinct de `pdf` de mai sus, care e vechea CALE Access.
                "pdf_sha256": pdf_sha,
                "pdf_dimensiune": int(pdf_dim) if pdf_dim is not None else None,
                "pdf_data_modif": _iso_dt(pdf_modif),
            })

        # --- linii: FX_ORD_TBL, plate, cu beneficiarul lor (FX_ORD_PART) ---------------
        cursor.execute(_SQL_LINII, (cod,))
        linii = []
        for (idordtblp, idordp, idordpartp, clsf, denumire,
             total_receptii, plati_ant, valoare, ramas,
             den_bene, cod_fiscal, cont_iban, doc_just, obiect_ddf) in cursor.fetchall():
            linii.append({
                "idordtblp": int(idordtblp) if idordtblp is not None else None,
                "idordp": int(idordp) if idordp is not None else None,
                # Beneficiarul liniei (FX_ORD_PART). 0 = linie fara PART parinte.
                "idordpartp": int(idordpartp) if idordpartp is not None else 0,
                "clsf": clsf,
                "descriere": denumire,
                "total_receptii": _num(total_receptii),
                "plati_ant": _num(plati_ant),
                "valoare": _num(valoare),
                "ramas": _num(ramas),
                # Subsolul paginii «Vizualizare» + filtrul pe beneficiar din antetul ei.
                "den_bene": den_bene,
                "cod_fiscal": cod_fiscal,
                "cont_iban": cont_iban,
                "doc_just": doc_just,
                "obiect_ddf": obiect_ddf,
            })

        logger.info("[forexe.ord] %s: cod=%s -> ordonantari=%s linii=%s (cale_pdf=%s)",
                    db_name, cod, len(ordonantari), len(linii), are_cale)
        return _json_utf8({"cod": cod, "ordonantari": ordonantari, "linii": linii}, 200)
    except Exception as e:
        # Fara inghitire: o eroare de baza intoarce motivul, NU liste goale — listele goale
        # ar minti operatorul ca angajamentul nu are ordonantari.
        logger.error(f"[forexe.ord] {e}", exc_info=True)
        return _json_utf8({"error": f"Eroare la citirea ordonanțărilor: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()
