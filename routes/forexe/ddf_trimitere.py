# routes/forexe/ddf_trimitere.py
"""
The SENDING routes of a DDF revision, KBOT -> forexecab (slice 0081).

    POST /api/forexe/ddf/trimitere/<idrev>/anuleaza-semnatura   (0081-02)
        A revision signed A on its interim PDF (S1) was edited again: its signature and its
        stored signed PDF no longer describe the data, so both go -> S0.
    POST /api/forexe/ddf/trimitere/<idrev>/start                (0081-04)
        The send begins: stage 1 ("interrupted") BEFORE forexecab is touched.
    POST /api/forexe/ddf/trimitere/<idrev>/coduri               (0081-04)
        What forexecab assigned: the real angajament code (replaces the "!" code everywhere)
        and each section-A line's row code. Idempotent.
    PUT  /api/forexe/ddf/trimitere/<idrev>/captura              (0081-04)
        One screen capture (raw PNG) -> a PrtScr = 1 attachment of the revision.
    POST /api/forexe/ddf/trimitere/<idrev>/stare                (0081-04)
        The send stage: 1 interrupted, 2 sent / in progress, 3 final PDF.

The IMPORT (forexecab -> KBOT: prelucrare_pasi.py, the DDF generation in ddf_edit.py, the read
workflows) is NOT touched from here -- it is only called, unchanged, by the client after a
send. `ddf_edit.py` (the editor's save) is not touched either: the new write paths live here.

Every route: `@require_session`, the database is the session's (one database = one unit),
one transaction, Romanian `error` text, `ensure_ascii=False`.
"""
import base64
import hashlib
import json
import logging

from flask import g, current_app, request

from routes.auth.guard import require_session
from utils.database import get_kbot_connection

from . import forexe_bp
from .ddf_stare import (MISSING_COLUMN_MESSAGE, STAGE_FINAL_PDF, STAGE_INTERRUPTED,
                        STAGE_NOT_SENT, STAGE_SENT_IN_PROGRESS, are_stare_trimitere)

logger = logging.getLogger(__name__)


class Refuz(Exception):
    """The request is refused before anything is written. The message is already Romanian."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _json_utf8(payload, status):
    body = json.dumps(payload, ensure_ascii=False, default=str)
    return current_app.response_class(body, status=status, mimetype="application/json")


def _in_tranzactie(treaba, eticheta: str):
    """Run `treaba(cursor)` in ONE transaction; `Refuz` -> its status, anything else -> 500."""
    db_name = g.session.db_name
    conn = None
    try:
        conn = get_kbot_connection(db_name)
        conn.autocommit = False
        cursor = conn.cursor(dictionary=True)
        if not are_stare_trimitere(cursor, db_name):
            raise Refuz(MISSING_COLUMN_MESSAGE, 409)
        if not conn.in_transaction:
            conn.start_transaction()
        rezultat = treaba(cursor)
        conn.commit()
        logger.info("[forexe.ddf_trimitere] %s: %s ok", db_name, eticheta)
        return _json_utf8(rezultat, 200)
    except Refuz as e:
        if conn is not None:
            conn.rollback()
        return _json_utf8({"error": str(e)}, e.status)
    except Exception as e:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                logger.warning("[forexe.ddf_trimitere] rollback esuat", exc_info=True)
        logger.error("[forexe.ddf_trimitere] %s: %s", eticheta, e, exc_info=True)
        return _json_utf8({"error": f"Eroare la trimiterea documentului de fundamentare: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()


_SQL_REVIZIE = (
    "SELECT r.IDREV, r.IDDF, r.CodAngajament, r.NumarRev, r.Incarcat, r.Preluat, "
    "       r.Semnatura, r.StareTrimitere, "
    "       EXISTS (SELECT 1 FROM FX_Rezervari rz WHERE rz.IDREV = r.IDREV) AS AreRezervari "
    "  FROM FX_DDF_REV r WHERE r.IDREV = %s FOR UPDATE"
)


def citeste_revizia(cursor, idrev: int) -> dict:
    """The revision row, locked for the transaction, or `Refuz` 404."""
    cursor.execute(_SQL_REVIZIE, (idrev,))
    rev = cursor.fetchone()
    if rev is None:
        raise Refuz(f"Revizia {idrev} nu există.", 404)
    return rev


def deja_in_forexe(rev: dict) -> bool:
    """A stage-0 revision forexecab already has (KBot.Domain RevizieRow.DejaInForexe)."""
    return bool(rev.get("Incarcat")) or bool(rev.get("Preluat")) or bool(rev.get("AreRezervari"))


# =========================================================================================
# 0081-02 -- the interim signature goes when section A is edited again (S1 -> S0)
# =========================================================================================

def _anuleaza_semnatura(cursor, idrev: int) -> dict:
    rev = citeste_revizia(cursor, idrev)
    if int(rev.get("StareTrimitere") or 0) != STAGE_NOT_SENT or deja_in_forexe(rev):
        raise Refuz("Revizia a fost deja trimisă în FOREXE; semnătura ei nu se mai anulează. "
                    "Orice modificare cere o revizie nouă.", 409)
    cursor.execute("DELETE FROM FX_DDF_PDF WHERE IDREV = %s", (idrev,))
    pdf_sters = cursor.rowcount
    cursor.execute("UPDATE FX_DDF_REV SET Semnatura = NULL WHERE IDREV = %s", (idrev,))
    return {"idrev": idrev, "pdf_sters": pdf_sters > 0}


@forexe_bp.route("/api/forexe/ddf/trimitere/<int:idrev>/anuleaza-semnatura", methods=["POST"])
@require_session
def post_ddf_anuleaza_semnatura(idrev):
    """S1 -> S0: the signed interim PDF and its roles are removed, in one transaction.

    Called by the editor right after it saved an S1 revision: the A signature was on the
    values that just changed. The shared chunks the PDF used (FX_PDF_BUCATI, slice 0078-05)
    are left in place -- the store is content-addressed and holds pieces of other PDFs too.
    """
    return _in_tranzactie(lambda c: _anuleaza_semnatura(c, idrev), f"anuleaza-semnatura {idrev}")


# =========================================================================================
# 0081-04 -- THE SEND
# =========================================================================================

def _roluri(semnatura) -> list:
    return [x.strip() for x in str(semnatura or "").split(",") if x.strip()]


def _start(cursor, idrev: int) -> dict:
    rev = citeste_revizia(cursor, idrev)
    stage = int(rev.get("StareTrimitere") or 0)
    if stage == STAGE_INTERRUPTED:
        # A resume: the stage is already right, forexecab may be half changed.
        return {"idrev": idrev, "stare_trimitere": stage, "reluare": True}
    if stage != STAGE_NOT_SENT or deja_in_forexe(rev):
        raise Refuz("Revizia a fost deja trimisă în FOREXE.", 409)
    if "A" not in _roluri(rev.get("Semnatura")):
        raise Refuz("Revizia nu este semnată A pe documentul intermediar. Generați-l, semnați "
                    "secțiunea A, apoi trimiteți.", 409)
    cursor.execute("UPDATE FX_DDF_REV SET StareTrimitere = %s WHERE IDREV = %s",
                   (STAGE_INTERRUPTED, idrev))
    return {"idrev": idrev, "stare_trimitere": STAGE_INTERRUPTED, "reluare": False}


@forexe_bp.route("/api/forexe/ddf/trimitere/<int:idrev>/start", methods=["POST"])
@require_session
def post_ddf_trimitere_start(idrev):
    """S1 -> stage 1 ("interrupted") BEFORE forexecab is touched: a crash halfway must leave
    S1x behind, never a revision that looks unsent. A revision already at stage 1 is a resume."""
    return _in_tranzactie(lambda c: _start(c, idrev), f"start {idrev}")


# ---- the "!" code -> forexecab's code --------------------------------------------------

# Every table that can hold the angajament code, read from MariaDB_Schema/ (000_DEMO and
# AVACONT_SURSA, 22.09.2026). The first group follows by itself: their CodAngajament is a
# foreign key ON UPDATE CASCADE to FX_Angajamente (FX_DDF, FX_Indicatori, FX_Istoric,
# FX_Receptii_H, FX_Receptii_R). The second group has the column without a foreign key and is
# updated here. FX_Extrase carries it as CodContract. The CodAI tables (FX_Istoric, FX_ORD_TBL,
# FX_Plati, FX_Receptii, FX_Receptii_RHR, FX_Rezervari) follow FX_Indicatori.CodAI by cascade.
_FARA_CASCADA = ("FX_DDF_REV", "FX_DDF_REV_SA", "FX_DDF_REV_SB", "FX_DDF_REV_PRT",
                 "FX_NumberLock", "FX_ORD", "FX_ORD_TBL", "FX_Plati", "FX_Receptii",
                 "FX_Receptii_RHR", "FX_Rezervari")

_SQL_TABELE = (
    "SELECT TABLE_NAME FROM information_schema.COLUMNS "
    " WHERE TABLE_SCHEMA = DATABASE() AND COLUMN_NAME = %s"
)


def _tabele_cu_coloana(cursor, coloana: str) -> set:
    cursor.execute(_SQL_TABELE, (coloana,))
    return {str(next(iter(r.values()))) for r in cursor.fetchall()}


def _cod_valid(cod: str) -> bool:
    return 6 <= len(cod) <= 20 and cod.isascii() and cod.isalnum()


def _schimba_codul(cursor, vechi: str, nou: str) -> dict:
    """The K-BOT-only angajament «!…» becomes forexecab's angajament «nou», everywhere."""
    cursor.execute("SELECT 1 FROM FX_Angajamente WHERE CodAngajament = %s", (nou,))
    if cursor.fetchone() is not None:
        raise Refuz(f"Codul «{nou}» întors de FOREXE există deja în K-BOT. Angajamentul creat "
                    f"manual («{vechi}») nu poate fi mutat peste el; verificați în FOREXE ce "
                    f"s-a creat.", 409)
    atinse = {}
    cursor.execute("UPDATE FX_Angajamente SET CodAngajament = %s WHERE CodAngajament = %s",
                   (nou, vechi))
    if cursor.rowcount == 0:
        raise Refuz(f"Angajamentul «{vechi}» nu mai există în K-BOT.", 404)
    atinse["FX_Angajamente"] = cursor.rowcount
    # FX_Indicatori.CodAngajament followed by cascade; its key CodAI («!…-IND») is rewritten
    # here, and every CodAI child follows by cascade. No '%' in the SQL (mysql.connector).
    cursor.execute(
        "UPDATE FX_Indicatori SET CodAI = CONCAT(%s, '-', CodIndicator) "
        " WHERE CodAngajament = %s AND LEFT(CodAI, CHAR_LENGTH(%s) + 1) = CONCAT(%s, '-')",
        (nou, nou, vechi, vechi))
    atinse["FX_Indicatori"] = cursor.rowcount
    existente = _tabele_cu_coloana(cursor, "CodAngajament")
    for tabela in _FARA_CASCADA:
        if tabela not in existente:
            continue
        cursor.execute(f"UPDATE {tabela} SET CodAngajament = %s WHERE CodAngajament = %s",
                       (nou, vechi))
        atinse[tabela] = cursor.rowcount
    if "FX_Extrase" in _tabele_cu_coloana(cursor, "CodContract"):
        cursor.execute("UPDATE FX_Extrase SET CodContract = %s WHERE CodContract = %s", (nou, vechi))
        atinse["FX_Extrase"] = cursor.rowcount
    logger.info("[forexe.ddf_trimitere] cod %s -> %s: %s", vechi, nou, atinse)
    return atinse


def _schimba_codul_randului(cursor, cod: str, vechi: str, nou: str) -> None:
    """One line's K-BOT row code «!xyz» becomes forexecab's row code «AAB»."""
    cursor.execute("SELECT 1 FROM FX_Indicatori WHERE CodAngajament = %s AND CodIndicator = %s",
                   (cod, nou))
    if cursor.fetchone() is not None:
        # The import already wrote forexecab's row: the K-BOT copy has nothing left to carry.
        cursor.execute("DELETE FROM FX_Indicatori WHERE CodAngajament = %s AND CodIndicator = %s",
                       (cod, vechi))
    else:
        cursor.execute(
            "UPDATE FX_Indicatori SET CodIndicator = %s, CodAI = CONCAT(%s, '-', %s) "
            " WHERE CodAngajament = %s AND CodIndicator = %s", (nou, cod, nou, cod, vechi))
    for tabela in ("FX_DDF_REV_SA", "FX_DDF_REV_SB"):
        cursor.execute(f"UPDATE {tabela} SET CodIndicator = %s "
                       f" WHERE CodAngajament = %s AND CodIndicator = %s", (nou, cod, vechi))


def _coduri(cursor, idrev: int, corp: dict) -> dict:
    rev = citeste_revizia(cursor, idrev)
    stage = int(rev.get("StareTrimitere") or 0)
    if stage not in (STAGE_INTERRUPTED, STAGE_SENT_IN_PROGRESS):
        raise Refuz("Codurile din FOREXE se scriu doar în timpul trimiterii reviziei.", 409)

    cod = str(rev.get("CodAngajament") or "").strip()
    cod_real = str(corp.get("cod_real") or "").strip().upper()
    atinse = {}
    if cod_real and cod_real != cod:
        if not cod.startswith("!"):
            raise Refuz(f"Revizia are deja codul «{cod}», iar FOREXE a întors «{cod_real}».", 409)
        if not _cod_valid(cod_real):
            raise Refuz(f"Codul întors de FOREXE («{cod_real}») nu arată a cod de angajament.", 400)
        atinse = _schimba_codul(cursor, cod, cod_real)
        cod = cod_real

    randuri = corp.get("randuri") or []
    if not isinstance(randuri, list):
        raise Refuz("Câmpul «randuri» trebuie să fie o listă.", 400)
    schimbate = 0
    for r in randuri:
        id_sec_a = int((r or {}).get("id_sec_a") or 0)
        nou = str((r or {}).get("cod_indicator") or "").strip()
        if id_sec_a <= 0 or not nou or nou.startswith("!"):
            continue
        cursor.execute("SELECT CodIndicator FROM FX_DDF_REV_SA WHERE IdSecA = %s AND IDREV = %s",
                       (id_sec_a, idrev))
        linie = cursor.fetchone()
        if linie is None:
            raise Refuz(f"Rândul {id_sec_a} nu aparține reviziei {idrev}.", 400)
        vechi = str(linie.get("CodIndicator") or "").strip()
        if vechi == nou:
            continue
        _schimba_codul_randului(cursor, cod, vechi, nou)
        schimbate += 1
    return {"idrev": idrev, "cod": cod, "tabele": atinse, "randuri_schimbate": schimbate}


@forexe_bp.route("/api/forexe/ddf/trimitere/<int:idrev>/coduri", methods=["POST"])
@require_session
def post_ddf_trimitere_coduri(idrev):
    """Body: {"cod_real": "<forexecab code or empty>",
              "randuri": [{"id_sec_a": n, "cod_indicator": "AAB"}, ...]}.

    Written as soon as forexecab gave them, in one transaction, and safe to repeat: a resume
    sends them again and nothing moves twice. `Creare Angajament` must never run twice for one
    revision; once the real code is stored here, the client continues with `Incarca Rezervare`.
    """
    corp = request.get_json(silent=True)
    if not isinstance(corp, dict):
        return _json_utf8({"error": "Corp JSON lipsă sau nevalid."}, 400)
    return _in_tranzactie(lambda c: _coduri(c, idrev, corp), f"coduri {idrev}")


# ---- captures --------------------------------------------------------------------------

MAX_CAPTURA_BYTES = 16 * 1024 * 1024
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
# JPEG since 28.09.2026 (operator): the workflow compresses its captures, because they end
# up inside the signed PDF. Both are accepted, and what a row IS is read from its bytes.
JPEG_MAGIC = b"\xff\xd8\xff"
H_SHA = "X-Sha256"
H_NUME = "X-Nume-Fisier"


def _tip_imagine(octeti: bytes) -> str:
    """What the bytes are, read from them -- never from the file name."""
    return "image/jpeg" if octeti.startswith(JPEG_MAGIC) else "image/png"


def _are_att_img(cursor) -> bool:
    cursor.execute("SELECT COUNT(*) AS n FROM information_schema.TABLES "
                   " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'FX_DDF_REV_ATT_IMG'")
    row = cursor.fetchone() or {}
    return bool(row.get("n"))


def _captura_existenta(cursor, idrev: int, octeti: bytes, sha: str) -> int:
    """Slice 0081-07: the id of a capture of this revision with the SAME bytes, or 0.

    A FOREXE answer loaded again from «Rezultate_Forexe» (K-BOT's replay mode) brings its
    captures a second time; stored twice, they would show twice in the final PDF (Table4).
    Same bytes = same sha256 (or, on a database without FX_DDF_REV_ATT_IMG, the same base64).
    """
    if _are_att_img(cursor):
        cursor.execute(
            "SELECT a.IdRevAtt AS id FROM FX_DDF_REV_ATT a "
            "  JOIN FX_DDF_REV_ATT_IMG i ON i.IdRevAtt = a.IdRevAtt "
            " WHERE a.IDREV = %s AND a.PrtScr = 1 AND i.Sha256 = %s LIMIT 1",
            (idrev, sha))
    else:
        cursor.execute(
            "SELECT IdRevAtt AS id FROM FX_DDF_REV_ATT "
            " WHERE IDREV = %s AND PrtScr = 1 AND DateFisier = %s LIMIT 1",
            (idrev, base64.b64encode(octeti).decode("ascii")))
    row = cursor.fetchone() or {}
    return int(row.get("id") or 0)


def _captura(cursor, idrev: int, nume: str, octeti: bytes, sha: str) -> dict:
    rev = citeste_revizia(cursor, idrev)
    stage = int(rev.get("StareTrimitere") or 0)
    if stage not in (STAGE_INTERRUPTED, STAGE_SENT_IN_PROGRESS):
        raise Refuz("Capturile din FOREXE se adaugă doar în timpul trimiterii reviziei "
                    "(inclusiv «Definitivează» / «Derulează»).", 409)
    existing = _captura_existenta(cursor, idrev, octeti, sha)
    if existing > 0:
        return {"id_rev_att": existing, "dimensiune": len(octeti), "exista_deja": True}
    cursor.execute("INSERT INTO FX_DDF_REV_ATT (IDDF, IDREV, CaleFisier, PrtScr) VALUES (%s, %s, %s, 1)",
                   (rev.get("IDDF"), idrev, nume))
    id_rev_att = int(cursor.lastrowid or 0)
    if id_rev_att <= 0:
        raise RuntimeError("FX_DDF_REV_ATT nu a intors o cheie noua (AUTO_INCREMENT lipsa?)")
    if _are_att_img(cursor):
        # Decision D12 of slice 0051: the bytes live in FX_DDF_REV_ATT_IMG.
        cursor.execute(
            "INSERT INTO FX_DDF_REV_ATT_IMG (IdRevAtt, NumeFisier, TipMime, Dimensiune, Sha256, "
            "  Continut, DataModif) VALUES (%s, %s, %s, %s, %s, %s, NOW())",
            (id_rev_att, nume, _tip_imagine(octeti), len(octeti), sha, octeti))
    else:
        # A database without sql/0051_ddf_rev_att_img.sql: the old column, base64, which the
        # generation read route serves as it is.
        cursor.execute("UPDATE FX_DDF_REV_ATT SET DateFisier = %s WHERE IdRevAtt = %s",
                       (base64.b64encode(octeti).decode("ascii"), id_rev_att))
    return {"id_rev_att": id_rev_att, "dimensiune": len(octeti)}


@forexe_bp.route("/api/forexe/ddf/trimitere/<int:idrev>/captura", methods=["PUT"])
@require_session
def put_ddf_trimitere_captura(idrev):
    """Raw JPEG or PNG bytes (never base64 in JSON), `X-Sha256` over them, `X-Nume-Fisier` (ASCII).
    Stored as a PrtScr = 1 attachment: read-only in the editor, drawn in Table4 of the final PDF."""
    octeti = request.get_data()
    if not octeti:
        return _json_utf8({"error": "Captura este goală."}, 400)
    if len(octeti) > MAX_CAPTURA_BYTES:
        return _json_utf8({"error": f"Captura depășește {MAX_CAPTURA_BYTES // (1024 * 1024)} MB."}, 413)
    if not (octeti.startswith(JPEG_MAGIC) or octeti.startswith(PNG_MAGIC)):
        return _json_utf8({"error": "Captura nu este o imagine JPEG sau PNG."}, 400)
    sha = hashlib.sha256(octeti).hexdigest()
    sha_client = (request.headers.get(H_SHA) or "").strip().lower()
    if sha_client and sha_client != sha:
        return _json_utf8({"error": "Suma de control a capturii nu se potrivește: s-a stricat pe drum."}, 400)
    nume = (request.headers.get(H_NUME) or "").strip()
    if not nume or len(nume) > 255 or not nume.isascii():
        return _json_utf8({"error": f"Antet lipsă sau nevalid: {H_NUME}."}, 400)
    return _in_tranzactie(lambda c: _captura(c, idrev, nume, octeti, sha), f"captura {idrev} {nume}")


# ---- the stage ---------------------------------------------------------------------------

def _stare(cursor, idrev: int, noua: int) -> dict:
    if noua not in (STAGE_INTERRUPTED, STAGE_SENT_IN_PROGRESS, STAGE_FINAL_PDF):
        raise Refuz(f"Stare de trimitere nevalidă: {noua}.", 400)
    rev = citeste_revizia(cursor, idrev)
    curenta = int(rev.get("StareTrimitere") or 0)
    if curenta == STAGE_NOT_SENT:
        raise Refuz("Revizia nu a început trimiterea.", 409)
    if curenta == STAGE_FINAL_PDF and noua != STAGE_FINAL_PDF:
        raise Refuz("Revizia are deja PDF-ul final; starea ei nu se mai întoarce.", 409)
    cursor.execute("UPDATE FX_DDF_REV SET StareTrimitere = %s WHERE IDREV = %s", (noua, idrev))
    return {"idrev": idrev, "stare_trimitere": noua}


@forexe_bp.route("/api/forexe/ddf/trimitere/<int:idrev>/stare", methods=["POST"])
@require_session
def post_ddf_trimitere_stare(idrev):
    """Body: {"stare": 1 | 2 | 3}. Never back from 3 (the final PDF exists), never to 0."""
    corp = request.get_json(silent=True)
    if not isinstance(corp, dict):
        return _json_utf8({"error": "Corp JSON lipsă sau nevalid."}, 400)
    try:
        noua = int(corp.get("stare"))
    except (TypeError, ValueError):
        return _json_utf8({"error": "Câmpul «stare» lipsește sau nu e un număr."}, 400)
    return _in_tranzactie(lambda c: _stare(c, idrev, noua), f"stare {idrev} -> {noua}")
