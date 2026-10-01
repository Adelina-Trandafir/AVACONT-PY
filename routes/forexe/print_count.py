# routes/forexe/print_count.py
"""
Slice 0099 -- PrintCount: one more print of a document shown in K-BOT.

Routes (all POST, no body):
    POST /api/forexe/ddf/pdf/<idrev>/print              a DDF revision
    POST /api/forexe/ord/pdf/<idordp>/print             an ordonantare
    POST /api/forexe/nc/pdf/<idnc>/print                a CAB correction note
    POST /api/forexe/note-cab/recipisa/<idrcp>/print    the FOREXE receipt of a note

K-BOT calls one of them each time it sees, in the Windows print queue, a job carrying the name
of the document on screen (KBot.Controls/Adobe/AdobePrintWatcher.vb). One call = one print job.

WHERE THE COUNT GOES (sql/0099_print_count.sql):
  * the document's PDF row (FX_DDF_PDF / FX_ORD_PDF / FX_NoteCAB_PDF / FX_NoteCAB_Recipisa)
    when there is one;
  * for a DDF / ORD with NO PDF row -- an unsigned document, generated on the operator's
    computer and never stored (see pdf.py) -- the document row itself (FX_DDF_REV / FX_ORD).
    The client does not choose: the row that exists NOW decides, in one transaction.
  * a CAB note with no stored PDF has nowhere to count (FX_NoteCAB carries no PrintCount): the
    answer is 200 with `numarat = false`, and the line goes to the log.

Answers:
    200 {"numarat": true,  "tinta": "pdf" | "document" | "recipisa", "print_count": n}
    200 {"numarat": false, "tinta": "", "print_count": null}
    404 the document does not exist
    409 the DDL of slice 0099 has not reached this database (nothing is written)

Scope: the connected database IS the unit (g.session.db_name), like every /api/forexe/* route.
The family descriptions (table, key, parent) are the ones of pdf.py -- one copy, not two.
"""
import json
import logging

from flask import g, current_app

from routes.auth.guard import require_session
from utils.database import get_kbot_connection

from . import forexe_bp
from .pdf import _DDF, _ORD, _NC

logger = logging.getLogger(__name__)

COLUMN = "PrintCount"
SQL_FILE = "sql/0099_print_count.sql"
RECEIPT_TABLE = "FX_NoteCAB_Recipisa"
RECEIPT_KEY = "IDRCP"

# What the answer's `tinta` says: which row took the count.
TARGET_PDF = "pdf"
TARGET_DOCUMENT = "document"
TARGET_RECEIPT = "recipisa"


def _json_utf8(payload, status):
    """JSON answer with LITERAL diacritics (ensure_ascii=False): the errors are Romanian."""
    body = json.dumps(payload, ensure_ascii=False)
    return current_app.response_class(body, status=status, mimetype="application/json")


def _columns_present(cursor, tables) -> bool:
    """Has sql/0099 reached every one of `tables` on this database? Probed per request, like the
    chunk store in pdf.py: a database the DDL has not reached answers 409, not error 1054."""
    marks = ", ".join(["%s"] * len(tables))
    cursor.execute(
        "SELECT COUNT(*) FROM information_schema.COLUMNS "
        " WHERE TABLE_SCHEMA = DATABASE() AND COLUMN_NAME = %s "
        f"   AND TABLE_NAME IN ({marks})", (COLUMN, *tables))
    row = cursor.fetchone()
    return bool(row) and int(row[0]) == len(tables)


def _bump(cursor, table, key_col, key):
    """Adds one to the row's count. Returns the new count, or None when there is no such row."""
    cursor.execute(
        f"UPDATE {table} SET {COLUMN} = {COLUMN} + 1 WHERE {key_col} = %s", (key,))
    if cursor.rowcount == 0:
        return None
    cursor.execute(f"SELECT {COLUMN} FROM {table} WHERE {key_col} = %s LIMIT 1", (key,))
    row = cursor.fetchone()
    return int(row[0]) if row else None


def _exists(cursor, table, key_col, key) -> bool:
    cursor.execute(f"SELECT 1 FROM {table} WHERE {key_col} = %s LIMIT 1", (key,))
    return cursor.fetchone() is not None


def _missing_ddl():
    return _json_utf8(
        {"error": f"Tipărirea nu a putut fi numărată: coloana {COLUMN} lipsește din baza de "
                  f"date. Rulați {SQL_FILE}."}, 409)


def _rollback(conn):
    if conn is None:
        return
    try:
        conn.rollback()
    except Exception:
        # A rollback on a dead connection has nothing left to undo; the REAL error is the one
        # the caller is about to report.
        logger.warning("[forexe.print] rollback failed after the error below", exc_info=True)


def _count(label, key, pdf_table, key_col, parent_table, count_on_parent):
    """One print of one document, in ONE transaction.

    `count_on_parent`: a document with no PDF row is counted on its own row (DDF / ORD). Without
    it (the CAB note) a missing PDF row means «nowhere to count», answered 200 / numarat=false.
    """
    db_name = g.session.db_name
    un = (getattr(g.session, "username", "") or "")[:128]
    conn = None
    try:
        conn = get_kbot_connection(db_name)
        cursor = conn.cursor()
        tables = [pdf_table] + ([parent_table] if count_on_parent else [])
        if not _columns_present(cursor, tables):
            logger.warning("[forexe.print] %s: %s not applied -- print of %s %s=%s by %s NOT counted",
                           db_name, SQL_FILE, label, key_col, key, un)
            return _missing_ddl()

        table = pdf_table
        target = TARGET_PDF
        count = _bump(cursor, pdf_table, key_col, key)
        if count is None and count_on_parent:
            table = parent_table
            target = TARGET_DOCUMENT
            count = _bump(cursor, parent_table, key_col, key)
        if count is None:
            # Nothing was written. For a family counted on its parent this means the document
            # itself is gone; for the note it may only mean «no PDF stored yet».
            _rollback(conn)
            if count_on_parent or not _exists(cursor, parent_table, key_col, key):
                return _json_utf8({"error": "Documentul tipărit nu există pe server."}, 404)
            logger.info("[forexe.print] %s: %s %s=%s printed by %s -- no stored PDF, not counted",
                        db_name, label, key_col, key, un)
            return _json_utf8({"numarat": False, "tinta": "", "print_count": None}, 200)

        conn.commit()
        logger.info("[forexe.print] %s: %s %s=%s printed by %s -> %s.%s = %s",
                    db_name, label, key_col, key, un, table, COLUMN, count)
        return _json_utf8({"numarat": True, "tinta": target, "print_count": count}, 200)
    except Exception as e:
        _rollback(conn)
        logger.error("[forexe.print] %s %s=%s on %s: %s", label, key_col, key, db_name, e,
                     exc_info=True)
        return _json_utf8({"error": f"Eroare la numărarea tipăririi: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()


def _count_family(spec, key, count_on_parent):
    return _count(spec["eticheta"], key, spec["tabela"], spec["cheie"], spec["parinte"],
                  count_on_parent)


@forexe_bp.route("/api/forexe/ddf/pdf/<int:idrev>/print", methods=["POST"])
@require_session
def post_ddf_print(idrev):
    """One more print of a DDF revision (signed -> FX_DDF_PDF, unsigned -> FX_DDF_REV)."""
    return _count_family(_DDF, idrev, True)


@forexe_bp.route("/api/forexe/ord/pdf/<int:idordp>/print", methods=["POST"])
@require_session
def post_ord_print(idordp):
    """One more print of an ordonantare (signed -> FX_ORD_PDF, unsigned -> FX_ORD)."""
    return _count_family(_ORD, idordp, True)


@forexe_bp.route("/api/forexe/nc/pdf/<int:idnc>/print", methods=["POST"])
@require_session
def post_nc_print(idnc):
    """One more print of a CAB correction note (FX_NoteCAB_PDF)."""
    return _count_family(_NC, idnc, False)


@forexe_bp.route("/api/forexe/note-cab/recipisa/<int:idrcp>/print", methods=["POST"])
@require_session
def post_receipt_print(idrcp):
    """One more print of a note's FOREXE receipt (FX_NoteCAB_Recipisa)."""
    db_name = g.session.db_name
    un = (getattr(g.session, "username", "") or "")[:128]
    conn = None
    try:
        conn = get_kbot_connection(db_name)
        cursor = conn.cursor()
        if not _columns_present(cursor, [RECEIPT_TABLE]):
            logger.warning("[forexe.print] %s: %s not applied -- print of receipt %s by %s NOT counted",
                           db_name, SQL_FILE, idrcp, un)
            return _missing_ddl()
        count = _bump(cursor, RECEIPT_TABLE, RECEIPT_KEY, idrcp)
        if count is None:
            _rollback(conn)
            return _json_utf8({"error": "Recipisa tipărită nu există pe server."}, 404)
        conn.commit()
        logger.info("[forexe.print] %s: receipt %s=%s printed by %s -> %s.%s = %s",
                    db_name, RECEIPT_KEY, idrcp, un, RECEIPT_TABLE, COLUMN, count)
        return _json_utf8({"numarat": True, "tinta": TARGET_RECEIPT, "print_count": count}, 200)
    except Exception as e:
        _rollback(conn)
        logger.error("[forexe.print] receipt %s on %s: %s", idrcp, db_name, e, exc_info=True)
        return _json_utf8({"error": f"Eroare la numărarea tipăririi: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()
