"""
«Nota contabila corectie CAB» (form F1135, slice 0088).

FOREXE lists under «Operatiuni necorectate» the treasury operations it could not attach to an
angajament: they carry the code «ERRRRRRRRRR». The operator attaches EVERY one of them to an
angajament + indicator of the unit and saves them together: ONE note, with one pair of rows per
operation (the storno of the operation, then the same amount on the chosen angajament). K-BOT
keeps the note here, makes its PDF and uploads it into FOREXE.

    GET  /api/forexe/note-cab/pregatire
        -> 200 { "urmatorul_numar": 24, "an": 2026,
                 "angajamente": [ {"cod_angajament", "descriere",
                                   "indicatori": [ {"cod_indicator", "cod_ai", "ss", "clsf_sal",
                                                    "clsf", "denumire", "programe": [...]} ]} ] }
    POST /api/forexe/note-cab/lot   (slice 0088-05 -- what the note window uses)
        { "data_nota", "denumire_ep", "cif_ep", "note": [ {"nr_nota", "corectii": [...]} ] }
        -> 201 { "note": [ {"idnc", "nr_nota", "an"} ] }  -- ALL or NOTHING: one note per number,
           one angajament per note, no number twice, none already used this year
    POST /api/forexe/note-cab
        { "nr_nota", "data_nota", "denumire_ep", "cif_ep",
          "corectii": [ { "idfxp", "referinta_trezor", "nr_doc", "simbol_cont", "cod_program",
                          "data_oper_initiala", "coloana", "suma", "cod_angajament",
                          "cod_indicator", "cod_ai", "simbol_cont_corectie",
                          "cod_program_corectie", "explicatii" }, ... ] }
        -> 201 { "idnc", "nr_nota", "an" };  409 when the number or an operation already has a note
    GET  /api/forexe/note-cab?cod=<CodAngajament>
        -> 200 { "note": [ { header..., "pdf_sha256", "corectii": [ ...every correction... ] } ] }
           the notes that have at least one correction on that angajament (all their rows).
    POST /api/forexe/note-cab/<idnc>/trimitere   { "raspuns": "...", "index": "1230450081" }
        -> 200 { "idnc" }  -- the note was uploaded into FOREXE (index: FOREXE's registration)
    POST /api/forexe/note-cab/<idnc>/recipisa   (slice 0088-04)
        { "index", "numar_inregistrare", "id_mesaj", "descriere", "data_mesaj", "nume_fisier",
          "continut" (base64) }
        -> 200 { "recipisa": {...} }  -- the FOREXE receipt, kept on the note's PDF
           (FX_NoteCAB_Recipisa, one row per index); 409 when the note has no PDF on the server
    GET  /api/forexe/note-cab/recipisa/<idrcp>
        -> 200 the receipt file (bytes; X-Sha256, X-Nume-Fisier-B64 headers)

The note's PDF itself travels on /api/forexe/nc/pdf/<idnc> (routes/forexe/pdf.py, family _NC).

JOINS (house rule: count what a join returns). FX_Indicatori -> Clasificatii on
C.IDClsf = I.IdClsf (the primary key since slice 0080-01: never more than one row). DefaProgram is
read apart, as a SS -> programs map, so an SS with two programs does not double the indicators.
"""
import base64
import binascii
import hashlib
import json
import logging
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from flask import g, current_app, request
from mysql.connector import errorcode
import mysql.connector

from routes.auth.guard import require_session
from utils.database import COMMON_DB, get_kbot_connection

from . import forexe_bp

logger = logging.getLogger(__name__)

_SQL_ANGAJAMENTE = (
    "SELECT A.CodAngajament, A.Descriere, I.CodAI, I.CodIndicator, "
    "       COALESCE(NULLIF(I.SS, ''), C.SS) AS SS, C.Sector, C.Sursa, C.ClsfSal, C.Clsf, "
    "       C.Denumire "
    "  FROM FX_Angajamente A "
    "  JOIN FX_Indicatori I ON I.CodAngajament = A.CodAngajament "
    "  LEFT JOIN Clasificatii C ON C.IDClsf = I.IdClsf "
    " WHERE A.Ascuns = 0 "
    " ORDER BY A.CodAngajament, I.CodIndicator"
)
_SQL_PROGRAME = f"SELECT SS, Program FROM {COMMON_DB}.DefaProgram ORDER BY ID"
_SQL_URMATOR = "SELECT COALESCE(MAX(NrNota), 0) + 1 AS N FROM FX_NoteCAB WHERE An = %s"
_SQL_NUMERE = "SELECT NrNota FROM FX_NoteCAB WHERE An = %s ORDER BY NrNota"
_SQL_ANGAJAMENT = "SELECT 1 FROM FX_Angajamente WHERE CodAngajament = %s LIMIT 1"
_SQL_EXISTA_NR = "SELECT 1 FROM FX_NoteCAB WHERE An = %s AND NrNota = %s LIMIT 1"
_SQL_EXISTA_OP = (
    "SELECT N.NrNota FROM FX_NoteCAB_Corectii K JOIN FX_NoteCAB N ON N.IDNC = K.IDNC "
    " WHERE K.ReferintaTrezor = %s AND K.NrDoc = %s LIMIT 1"
)
_SQL_OPERATIUNE = (
    "SELECT IDFXP FROM FX_Operatiuni WHERE ReferintaTrezor = %s AND NrDoc = %s "
    "ORDER BY IDFXP LIMIT 1"
)
_SQL_INSERT_NOTA = (
    "INSERT INTO FX_NoteCAB (NrNota, An, DataNota, DenumireEP, CifEP, UN) "
    "VALUES (%s, %s, %s, %s, %s, %s)"
)
_SQL_INSERT_CORECTIE = (
    "INSERT INTO FX_NoteCAB_Corectii "
    "(IDNC, NrOrdine, IDFXP, ReferintaTrezor, NrDoc, SimbolCont, CodProgram, DataOperInitiala, "
    " Coloana, Suma, CodAngajament, CodIndicator, CodAI, SimbolContCorectie, CodProgramCorectie, "
    " Explicatii) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
)
_SQL_NOTE_ANGAJAMENT = (
    "SELECT N.IDNC, N.NrNota, N.An, N.DataNota, N.DenumireEP, N.CifEP, N.Semnatura, N.Trimis, "
    "       N.DataTrimitere, N.RaspunsTrimitere, N.IndexInregistrare, P.Sha256 AS PdfSha256, "
    "       R.IDRCP, R.IndexInregistrare AS RcpIndex, R.NumarInregistrare AS RcpNumar, "
    "       R.NumeFisier AS RcpFisier, R.Dimensiune AS RcpDimensiune, R.Sha256 AS RcpSha256, "
    "       R.DataMesaj AS RcpDataMesaj, R.DescriereMesaj AS RcpDescriere "
    "  FROM FX_NoteCAB N "
    "  LEFT JOIN FX_NoteCAB_PDF P ON P.IDNC = N.IDNC "
    "  LEFT JOIN FX_NoteCAB_Recipisa R ON R.IDRCP = "
    "       (SELECT MAX(R2.IDRCP) FROM FX_NoteCAB_Recipisa R2 WHERE R2.IDPDF = P.IDPDF) "
    " WHERE EXISTS (SELECT 1 FROM FX_NoteCAB_Corectii K WHERE K.IDNC = N.IDNC AND K.CodAngajament = %s) "
    " ORDER BY N.DataNota, N.NrNota"
)
_SQL_CORECTII = (
    "SELECT IDNC, IDFXP, ReferintaTrezor, NrDoc, SimbolCont, CodProgram, DataOperInitiala, Coloana, "
    "       Suma, CodAngajament, CodIndicator, CodAI, SimbolContCorectie, CodProgramCorectie, "
    "       Explicatii "
    "  FROM FX_NoteCAB_Corectii WHERE IDNC IN ({marks}) ORDER BY IDNC, NrOrdine"
)
_SQL_TRIMIS = (
    "UPDATE FX_NoteCAB SET Trimis = 1, DataTrimitere = NOW(), RaspunsTrimitere = %s, "
    "       IndexInregistrare = COALESCE(%s, IndexInregistrare) "
    " WHERE IDNC = %s"
)
# Slice 0088-04: the receipt. The note must have its PDF on the server: the receipt hangs on it.
_SQL_PDF_NOTA = "SELECT IDPDF FROM FX_NoteCAB_PDF WHERE IDNC = %s LIMIT 1"
_SQL_INSERT_RECIPISA = (
    "INSERT INTO FX_NoteCAB_Recipisa "
    "(IDPDF, IndexInregistrare, NumarInregistrare, IdMesaj, DescriereMesaj, DataMesaj, "
    " NumeFisier, Dimensiune, Sha256, Continut, UN) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
    "ON DUPLICATE KEY UPDATE IDPDF = VALUES(IDPDF), NumarInregistrare = VALUES(NumarInregistrare), "
    "  IdMesaj = VALUES(IdMesaj), DescriereMesaj = VALUES(DescriereMesaj), "
    "  DataMesaj = VALUES(DataMesaj), NumeFisier = VALUES(NumeFisier), "
    "  Dimensiune = VALUES(Dimensiune), Sha256 = VALUES(Sha256), Continut = VALUES(Continut), "
    "  UN = VALUES(UN)"
)
_SQL_RECIPISA_ID = "SELECT IDRCP FROM FX_NoteCAB_Recipisa WHERE IndexInregistrare = %s"
_SQL_INDEX_NOTA = "UPDATE FX_NoteCAB SET IndexInregistrare = %s WHERE IDNC = %s"
_SQL_RECIPISA_FISIER = "SELECT NumeFisier, Sha256, Continut FROM FX_NoteCAB_Recipisa WHERE IDRCP = %s"
_RE_INDEX = re.compile(r"^[0-9]{4,20}$")
_RECIPISA_MAX = 20 * 1024 * 1024

# The F1135 rules the form's own script checks (csValidareNC.validareFieldNC), so a note K-BOT
# stores is one the form accepts.
_RE_SIMBOL = re.compile(r"^(2[3-579][A-M][0-9]{12}|2[01268][A-M][0-9]{6}|5([0-9]{3}|[0-9]{5}|[0-9]{7}))$")
_RE_PROGRAM = re.compile(r"^[0-9]{10}$")
_RE_INDICATOR = re.compile(r"^[A-Z][0-9A-Z]{2}$")
_RE_TEXT = re.compile(r"^[A-Za-z0-9 .,\-/]*$")
_RE_NUME = re.compile(r"^[A-Za-z0-9 ]*$")
_RE_CIF = re.compile(r"^[0-9]{1,10}$")


def _json_utf8(payload, status):
    """A JSON response with LITERAL diacritics (ensure_ascii=False)."""
    body = json.dumps(payload, ensure_ascii=False, default=str)
    return current_app.response_class(body, status=status, mimetype="application/json")


def _text(d: dict, key: str) -> str:
    return str(d.get(key) or "").strip()


def _num(value):
    if value is None:
        return None
    return float(value) if isinstance(value, Decimal) else value


@forexe_bp.route("/api/forexe/note-cab/pregatire", methods=["GET"])
@require_session
def get_note_cab_pregatire():
    """What the note window needs: the next number and the angajamente with their indicators."""
    db_name = g.session.db_name
    an = date.today().year
    conn = None
    try:
        conn = get_kbot_connection(db_name)
        cursor = conn.cursor(dictionary=True)

        cursor.execute(_SQL_PROGRAME)
        programe = {}
        for r in cursor.fetchall():
            lista = programe.setdefault(str(r["SS"] or ""), [])
            if r["Program"] not in lista:
                lista.append(r["Program"])

        cursor.execute(_SQL_ANGAJAMENTE)
        angajamente = []
        dupa_cod = {}
        for r in cursor.fetchall():
            cod = r["CodAngajament"]
            a = dupa_cod.get(cod)
            if a is None:
                a = {"cod_angajament": cod, "descriere": r["Descriere"] or "", "indicatori": []}
                dupa_cod[cod] = a
                angajamente.append(a)
            ss = str(r["SS"] or "")
            a["indicatori"].append({
                "cod_indicator": r["CodIndicator"] or "", "cod_ai": r["CodAI"] or "", "ss": ss,
                "clsf_sal": r["ClsfSal"] or "", "clsf": r["Clsf"] or "",
                "denumire": r["Denumire"] or "", "programe": programe.get(ss, []),
            })

        cursor.execute(_SQL_URMATOR, (an,))
        urmator = int(cursor.fetchone()["N"])
        cursor.execute(_SQL_NUMERE, (an,))
        folosite = [int(r["NrNota"]) for r in cursor.fetchall()]
        return _json_utf8({"urmatorul_numar": urmator, "an": an, "numere_folosite": folosite,
                           "angajamente": angajamente}, 200)
    except Exception as e:
        logger.error("[forexe.note_cab] pregatire %s: %s", db_name, e, exc_info=True)
        return _json_utf8({"error": f"Eroare la citirea datelor pentru nota de corecție: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()


def _valideaza_corectie(nr: int, k: dict, data_nota, erori: list):
    """One correction (pair of rows); its errors go into `erori`, prefixed with its reference."""
    referinta = _text(k, "referinta_trezor")
    p = (referinta or f"Rândul {nr}") + ": "
    if not (6 <= len(referinta) <= 15):
        erori.append(p + "Referința TREZOR trebuie să aibă între 6 și 15 caractere.")
    try:
        data_oper = date.fromisoformat(_text(k, "data_oper_initiala"))
    except ValueError:
        data_oper = None
        erori.append(p + "data operațiunii inițiale lipsește sau nu este o dată.")
    if data_nota and data_oper and data_oper > data_nota:
        erori.append(p + "data operațiunii inițiale nu poate fi după data notei.")
    simbol = _text(k, "simbol_cont").upper()
    simbol_cor = _text(k, "simbol_cont_corectie").upper()
    for eticheta, v in (("simbolul contului de stornare", simbol),
                        ("simbolul contului de corecție", simbol_cor)):
        if not _RE_SIMBOL.match(v):
            erori.append(p + f"{eticheta} «{v}» nu are forma cerută de formular.")
    program = _text(k, "cod_program")
    program_cor = _text(k, "cod_program_corectie")
    for eticheta, v in (("codul programului de stornare", program),
                        ("codul programului de corecție", program_cor)):
        if not _RE_PROGRAM.match(v):
            erori.append(p + f"{eticheta} trebuie să aibă 10 cifre.")
    coloana = _text(k, "coloana").upper()
    if coloana not in ("D", "C"):
        erori.append(p + "coloana sumei trebuie să fie «D» sau «C».")
    try:
        suma = Decimal(str(k.get("suma"))).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        suma = None
    if suma is None or suma >= 0:
        erori.append(p + "suma stornată trebuie să fie negativă.")
    cod_ang = _text(k, "cod_angajament").upper()
    if len(cod_ang) != 11 or cod_ang in ("ERRRRRRRRRR", "ZZZZZZZZZZZ", "YYYYYYYYYYY"):
        erori.append(p + "angajamentul ales trebuie să fie un angajament real, de 11 caractere.")
    cod_ind = _text(k, "cod_indicator").upper()
    if not _RE_INDICATOR.match(cod_ind):
        erori.append(p + "indicatorul trebuie să aibă 3 caractere (literă + 2 litere/cifre).")
    explicatii = " ".join(_text(k, "explicatii").split())
    if not explicatii or len(explicatii) > 70 or not _RE_TEXT.match(explicatii):
        erori.append(p + "explicațiile sunt obligatorii: cel mult 70 de caractere, fără diacritice, "
                         "doar litere, cifre, spațiu și . , - /")
    idfxp = k.get("idfxp")
    return {"idfxp": idfxp if isinstance(idfxp, int) and idfxp > 0 else None,
            "referinta": referinta, "nr_doc": _text(k, "nr_doc"), "simbol": simbol,
            "program": program, "data_oper": data_oper, "coloana": coloana, "suma": suma,
            "cod_ang": cod_ang, "cod_ind": cod_ind, "cod_ai": _text(k, "cod_ai") or None,
            "simbol_cor": simbol_cor, "program_cor": program_cor, "explicatii": explicatii}


def _valideaza(corp: dict):
    """(values, None) or (None, Romanian error). Every rule is one the F1135 form checks too."""
    erori = []
    try:
        nr_nota = int(corp.get("nr_nota"))
    except (TypeError, ValueError):
        nr_nota = 0
    if nr_nota <= 0 or nr_nota > 9999999999:
        erori.append("Numărul notei trebuie să fie un număr între 1 și 9999999999.")
    try:
        data_nota = date.fromisoformat(_text(corp, "data_nota"))
    except ValueError:
        data_nota = None
        erori.append("Data notei lipsește sau nu este o dată.")
    denumire = " ".join(_text(corp, "denumire_ep").split())
    if not denumire or len(denumire) > 30 or not _RE_NUME.match(denumire):
        erori.append("Denumirea entității: cel mult 30 de caractere, doar litere, cifre și spațiu.")
    cif = _text(corp, "cif_ep")
    if not _RE_CIF.match(cif):
        erori.append("Codul fiscal al entității: doar cifre, cel mult 10.")

    lista = corp.get("corectii")
    if not isinstance(lista, list) or not lista:
        erori.append("Nota nu are nicio operațiune corectată.")
        lista = []
    corectii = []
    chei = set()
    for nr, k in enumerate(lista, start=1):
        if not isinstance(k, dict):
            erori.append(f"Rândul {nr} nu are forma așteptată.")
            continue
        c = _valideaza_corectie(nr, k, data_nota, erori)
        cheie = (c["referinta"], c["nr_doc"])
        if cheie in chei:
            erori.append(f"{c['referinta']}: operațiunea apare de două ori în notă.")
        chei.add(cheie)
        corectii.append(c)

    if erori:
        return None, " ".join(erori)
    return {"nr_nota": nr_nota, "an": data_nota.year, "data_nota": data_nota,
            "denumire": denumire, "cif": cif, "corectii": corectii}, None


def _rollback(conn):
    if conn is None:
        return
    try:
        conn.rollback()
    except Exception:
        # A rollback on a dead connection saves nothing; the real error is the caller's.
        logger.warning("[forexe.note_cab] rollback esuat", exc_info=True)


def _verifica_in_baza(cursor, v):
    """The database checks of one validated note: (error, status) or None."""
    cursor.execute(_SQL_EXISTA_NR, (v["an"], v["nr_nota"]))
    if cursor.fetchone() is not None:
        return (f"Numărul {v['nr_nota']} este deja folosit de o notă de corecție din {v['an']}. "
                f"Alegeți alt număr.", 409)
    for c in v["corectii"]:
        cursor.execute(_SQL_ANGAJAMENT, (c["cod_ang"],))
        if cursor.fetchone() is None:
            return f"Angajamentul «{c['cod_ang']}» nu există în K-BOT.", 404
        cursor.execute(_SQL_EXISTA_OP, (c["referinta"], c["nr_doc"]))
        r = cursor.fetchone()
        if r is not None:
            return (f"Operațiunea {c['referinta']} / nr. {c['nr_doc']} are deja nota de corecție "
                    f"nr. {r['NrNota']}.", 409)
    return None


def _insereaza_nota(cursor, v, un) -> int:
    """Inserts one validated note with its corrections; returns its IDNC. No commit."""
    cursor.execute(_SQL_INSERT_NOTA, (v["nr_nota"], v["an"], v["data_nota"], v["denumire"],
                                      v["cif"], un))
    idnc = cursor.lastrowid
    for ordine, c in enumerate(v["corectii"], start=1):
        idfxp = c["idfxp"]
        if idfxp is None:
            cursor.execute(_SQL_OPERATIUNE, (c["referinta"], c["nr_doc"]))
            op = cursor.fetchone()
            idfxp = op["IDFXP"] if op else None
        cursor.execute(_SQL_INSERT_CORECTIE, (
            idnc, ordine, idfxp, c["referinta"], c["nr_doc"], c["simbol"], c["program"],
            c["data_oper"], c["coloana"], c["suma"], c["cod_ang"], c["cod_ind"], c["cod_ai"],
            c["simbol_cor"], c["program_cor"], c["explicatii"]))
    return idnc


@forexe_bp.route("/api/forexe/note-cab", methods=["POST"])
@require_session
def post_nota_cab():
    """Stores one note with ALL its corrections (its PDF follows on /api/forexe/nc/pdf/<idnc>)."""
    corp = request.get_json(silent=True)
    if not isinstance(corp, dict):
        return _json_utf8({"error": "Corp JSON lipsă sau nevalid."}, 400)
    v, eroare = _valideaza(corp)
    if eroare:
        return _json_utf8({"error": eroare}, 400)

    db_name = g.session.db_name
    conn = None
    try:
        conn = get_kbot_connection(db_name)
        conn.autocommit = False
        cursor = conn.cursor(dictionary=True)
        if not conn.in_transaction:
            conn.start_transaction()

        refuz = _verifica_in_baza(cursor, v)
        if refuz:
            conn.rollback()
            return _json_utf8({"error": refuz[0]}, refuz[1])

        un = (getattr(g.session, "username", "") or "")[:128] or None
        idnc = _insereaza_nota(cursor, v, un)
        conn.commit()
        logger.info("[forexe.note_cab] %s: nota %s/%s (IDNC=%s), %s corectii",
                    db_name, v["nr_nota"], v["an"], idnc, len(v["corectii"]))
        return _json_utf8({"idnc": idnc, "nr_nota": v["nr_nota"], "an": v["an"]}, 201)
    except mysql.connector.Error as e:
        _rollback(conn)
        if e.errno == errorcode.ER_DUP_ENTRY:
            # A concurrent save took the number or an operation between the checks and the insert.
            return _json_utf8({"error": "Între timp s-a salvat o altă notă cu același număr sau "
                                        "pentru una dintre aceste operațiuni. Reîncercați."}, 409)
        logger.error("[forexe.note_cab] salvare %s: %s", db_name, e, exc_info=True)
        return _json_utf8({"error": f"Eroare la salvarea notei de corecție: {e}"}, 500)
    except Exception as e:
        _rollback(conn)
        logger.error("[forexe.note_cab] salvare %s: %s", db_name, e, exc_info=True)
        return _json_utf8({"error": f"Eroare la salvarea notei de corecție: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()


@forexe_bp.route("/api/forexe/note-cab/lot", methods=["POST"])
@require_session
def post_note_cab_lot():
    """Slice 0088-05 (operator): every note of one save of the window, in ONE transaction.

    One note per number; the rows of a note are all on ONE angajament. A number given twice, a
    number already used this year, or a note mixing angajamente -> nothing is saved.
    Body: {"data_note", "denumire_ep", "cif_ep", "note": [{"nr_nota", "corectii": [...]}, ...]}
    -> 201 {"note": [{"idnc", "nr_nota", "an"}, ...]}"""
    corp = request.get_json(silent=True)
    if not isinstance(corp, dict):
        return _json_utf8({"error": "Corp JSON lipsă sau nevalid."}, 400)
    lista = corp.get("note")
    if not isinstance(lista, list) or not lista:
        return _json_utf8({"error": "Nu este nicio notă de salvat."}, 400)

    validate = []
    erori = []
    numere = {}
    operatii = {}
    for n in lista:
        if not isinstance(n, dict):
            erori.append("O notă nu are forma așteptată.")
            continue
        v, eroare = _valideaza({"nr_nota": n.get("nr_nota"), "data_nota": corp.get("data_nota"),
                                "denumire_ep": corp.get("denumire_ep"), "cif_ep": corp.get("cif_ep"),
                                "corectii": n.get("corectii")})
        if eroare:
            erori.append(f"Nota nr. {n.get('nr_nota')}: {eroare}")
            continue
        angajamente = sorted({c["cod_ang"] for c in v["corectii"]})
        if len(angajamente) > 1:
            erori.append(f"Nota nr. {v['nr_nota']} are rânduri pe angajamente diferite "
                         f"({', '.join(angajamente)}): fiecare angajament are numărul lui.")
        if v["nr_nota"] in numere:
            erori.append(f"Numărul {v['nr_nota']} este dat de două ori "
                         f"({numere[v['nr_nota']]} și {', '.join(angajamente)}).")
        numere[v["nr_nota"]] = ", ".join(angajamente)
        for c in v["corectii"]:
            cheie = (c["referinta"], c["nr_doc"])
            if cheie in operatii:
                erori.append(f"Operațiunea {c['referinta']} / nr. {c['nr_doc']} este în două note.")
            operatii[cheie] = v["nr_nota"]
        validate.append(v)
    if erori:
        return _json_utf8({"error": "Nu s-a salvat nimic. " + " ".join(erori)}, 400)

    db_name = g.session.db_name
    conn = None
    try:
        conn = get_kbot_connection(db_name)
        conn.autocommit = False
        cursor = conn.cursor(dictionary=True)
        if not conn.in_transaction:
            conn.start_transaction()
        for v in validate:
            refuz = _verifica_in_baza(cursor, v)
            if refuz:
                conn.rollback()
                return _json_utf8({"error": "Nu s-a salvat nimic. " + refuz[0]}, refuz[1])
        un = (getattr(g.session, "username", "") or "")[:128] or None
        salvate = []
        for v in validate:
            idnc = _insereaza_nota(cursor, v, un)
            salvate.append({"idnc": idnc, "nr_nota": v["nr_nota"], "an": v["an"]})
        conn.commit()
        logger.info("[forexe.note_cab] %s: lot de %s note (%s)", db_name, len(salvate),
                    ", ".join(f"{x['nr_nota']}/{x['an']}=IDNC {x['idnc']}" for x in salvate))
        return _json_utf8({"note": salvate}, 201)
    except mysql.connector.Error as e:
        _rollback(conn)
        if e.errno == errorcode.ER_DUP_ENTRY:
            return _json_utf8({"error": "Nu s-a salvat nimic: între timp s-a salvat o altă notă cu "
                                        "unul dintre aceste numere sau operațiuni. Reîncercați."}, 409)
        logger.error("[forexe.note_cab] lot %s: %s", db_name, e, exc_info=True)
        return _json_utf8({"error": f"Nu s-a salvat nimic: {e}"}, 500)
    except Exception as e:
        _rollback(conn)
        logger.error("[forexe.note_cab] lot %s: %s", db_name, e, exc_info=True)
        return _json_utf8({"error": f"Nu s-a salvat nimic: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()


def _iso(value):
    return value.isoformat() if value else None


def _recipisa(r):
    """The note's latest receipt out of a _SQL_NOTE_ANGAJAMENT row, or None."""
    if not r.get("IDRCP"):
        return None
    return {"idrcp": r["IDRCP"], "index": r["RcpIndex"], "numar_inregistrare": r["RcpNumar"],
            "nume_fisier": r["RcpFisier"], "dimensiune": r["RcpDimensiune"],
            "sha256": r["RcpSha256"], "data_mesaj": _iso(r["RcpDataMesaj"]),
            "descriere": r["RcpDescriere"]}


@forexe_bp.route("/api/forexe/note-cab", methods=["GET"])
@require_session
def get_note_cab():
    """The notes with at least one correction on one angajament (the «Note corecție» view)."""
    cod = (request.args.get("cod") or "").strip()
    if not cod:
        return _json_utf8({"error": "Parametrul «cod» (codul angajamentului) lipsește."}, 400)
    db_name = g.session.db_name
    conn = None
    try:
        conn = get_kbot_connection(db_name)
        cursor = conn.cursor(dictionary=True)
        cursor.execute(_SQL_NOTE_ANGAJAMENT, (cod,))
        note = []
        dupa_id = {}
        for r in cursor.fetchall():
            n = {"idnc": r["IDNC"], "nr_nota": r["NrNota"], "an": r["An"],
                 "data_nota": _iso(r["DataNota"]), "denumire_ep": r["DenumireEP"],
                 "cif_ep": r["CifEP"], "semnatura": r["Semnatura"] or "",
                 "trimis": bool(r["Trimis"]), "data_trimitere": _iso(r["DataTrimitere"]),
                 "raspuns_trimitere": r["RaspunsTrimitere"], "pdf_sha256": r["PdfSha256"],
                 "index_inregistrare": r["IndexInregistrare"],
                 "recipisa": _recipisa(r), "corectii": []}
            note.append(n)
            dupa_id[r["IDNC"]] = n
        if dupa_id:
            ids = list(dupa_id)
            cursor.execute(_SQL_CORECTII.format(marks=", ".join(["%s"] * len(ids))), tuple(ids))
            for r in cursor.fetchall():
                dupa_id[r["IDNC"]]["corectii"].append({
                    "idfxp": r["IDFXP"], "referinta_trezor": r["ReferintaTrezor"],
                    "nr_doc": r["NrDoc"], "simbol_cont": r["SimbolCont"],
                    "cod_program": r["CodProgram"],
                    "data_oper_initiala": _iso(r["DataOperInitiala"]),
                    "coloana": r["Coloana"], "suma": _num(r["Suma"]),
                    "cod_angajament": r["CodAngajament"], "cod_indicator": r["CodIndicator"],
                    "cod_ai": r["CodAI"], "simbol_cont_corectie": r["SimbolContCorectie"],
                    "cod_program_corectie": r["CodProgramCorectie"],
                    "explicatii": r["Explicatii"],
                })
        return _json_utf8({"note": note}, 200)
    except Exception as e:
        logger.error("[forexe.note_cab] lista %s/%s: %s", db_name, cod, e, exc_info=True)
        return _json_utf8({"error": f"Eroare la citirea notelor de corecție: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()


@forexe_bp.route("/api/forexe/note-cab/<int:idnc>/trimitere", methods=["POST"])
@require_session
def post_nota_cab_trimitere(idnc):
    """Marks the note as uploaded into FOREXE, with what the FOREXE page answered."""
    corp = request.get_json(silent=True) or {}
    raspuns = _text(corp, "raspuns")[:2000] or None
    index = _text(corp, "index") or None
    if index is not None and not _RE_INDEX.match(index):
        return _json_utf8({"error": f"Indexul de înregistrare «{index}» nu este valid (doar cifre)."}, 400)
    db_name = g.session.db_name
    conn = None
    try:
        conn = get_kbot_connection(db_name)
        cursor = conn.cursor()
        cursor.execute(_SQL_TRIMIS, (raspuns, index, idnc))
        if cursor.rowcount == 0:
            conn.rollback()
            return _json_utf8({"error": "Nota de corecție nu există."}, 404)
        conn.commit()
        logger.info("[forexe.note_cab] %s: IDNC=%s trimisa in FOREXE", db_name, idnc)
        return _json_utf8({"idnc": idnc}, 200)
    except Exception as e:
        logger.error("[forexe.note_cab] trimitere %s/%s: %s", db_name, idnc, e, exc_info=True)
        return _json_utf8({"error": f"Eroare la marcarea notei ca trimisă: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()


@forexe_bp.route("/api/forexe/note-cab/<int:idnc>/recipisa", methods=["POST"])
@require_session
def post_nota_cab_recipisa(idnc):
    """Stores the FOREXE receipt of the note's upload, found by K-BOT in the SNM inbox."""
    corp = request.get_json(silent=True) or {}
    index = _text(corp, "index")
    if not _RE_INDEX.match(index):
        return _json_utf8({"error": f"Indexul de înregistrare «{index}» nu este valid (doar cifre)."}, 400)
    nume = _text(corp, "nume_fisier")[:255]
    if not nume:
        return _json_utf8({"error": "Numele fișierului recipisei lipsește."}, 400)
    try:
        continut = base64.b64decode(_text(corp, "continut"), validate=True)
    except (ValueError, binascii.Error):
        return _json_utf8({"error": "Conținutul recipisei nu este base64 valid."}, 400)
    if not continut:
        return _json_utf8({"error": "Recipisa este goală."}, 400)
    if len(continut) > _RECIPISA_MAX:
        return _json_utf8({"error": "Recipisa depășește 20 MB."}, 413)
    numar = _text(corp, "numar_inregistrare")[:64] or None
    descriere = _text(corp, "descriere")[:500] or None
    id_mesaj = _text(corp, "id_mesaj")
    id_mesaj = int(id_mesaj) if id_mesaj.isdigit() else None
    data_mesaj = None
    if _text(corp, "data_mesaj"):
        try:
            data_mesaj = datetime.fromisoformat(_text(corp, "data_mesaj"))
        except ValueError:
            return _json_utf8({"error": "Data mesajului recipisei nu este validă."}, 400)
    sha = hashlib.sha256(continut).hexdigest()
    un = (getattr(g.session, "username", "") or "")[:128] or None

    db_name = g.session.db_name
    conn = None
    try:
        conn = get_kbot_connection(db_name)
        cursor = conn.cursor()
        cursor.execute(_SQL_PDF_NOTA, (idnc,))
        row = cursor.fetchone()
        if not row:
            return _json_utf8({"error": "Nota de corecție nu are PDF pe server; recipisa nu are "
                                        "de ce să fie legată."}, 409)
        idpdf = row[0]
        cursor.execute(_SQL_INSERT_RECIPISA, (idpdf, index, numar, id_mesaj, descriere, data_mesaj,
                                              nume, len(continut), sha, continut, un))
        cursor.execute(_SQL_INDEX_NOTA, (index, idnc))
        cursor.execute(_SQL_RECIPISA_ID, (index,))
        idrcp = cursor.fetchone()[0]
        conn.commit()
        logger.info("[forexe.note_cab] %s: IDNC=%s recipisa index=%s IDRCP=%s (%s octeti, sha=%s)",
                    db_name, idnc, index, idrcp, len(continut), sha[:8])
        return _json_utf8({"recipisa": {
            "idrcp": idrcp, "index": index, "numar_inregistrare": numar, "nume_fisier": nume,
            "dimensiune": len(continut), "sha256": sha, "data_mesaj": _iso(data_mesaj),
            "descriere": descriere}}, 200)
    except Exception as e:
        _rollback(conn)
        logger.error("[forexe.note_cab] recipisa %s/%s: %s", db_name, idnc, e, exc_info=True)
        return _json_utf8({"error": f"Eroare la salvarea recipisei: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()


@forexe_bp.route("/api/forexe/note-cab/recipisa/<int:idrcp>", methods=["GET"])
@require_session
def get_nota_cab_recipisa(idrcp):
    """The receipt file, as FOREXE served it."""
    db_name = g.session.db_name
    conn = None
    try:
        conn = get_kbot_connection(db_name)
        cursor = conn.cursor()
        cursor.execute(_SQL_RECIPISA_FISIER, (idrcp,))
        row = cursor.fetchone()
        if not row:
            return _json_utf8({"error": "Recipisa nu există."}, 404)
        nume, sha, continut = row
        resp = current_app.response_class(bytes(continut), status=200,
                                          mimetype="application/octet-stream")
        resp.headers["X-Sha256"] = sha
        # A header is ASCII only: the name travels as base64 of its UTF-8.
        resp.headers["X-Nume-Fisier-B64"] = base64.b64encode(str(nume).encode("utf-8")).decode("ascii")
        return resp
    except Exception as e:
        logger.error("[forexe.note_cab] recipisa fisier %s/%s: %s", db_name, idrcp, e, exc_info=True)
        return _json_utf8({"error": f"Eroare la citirea recipisei: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()
