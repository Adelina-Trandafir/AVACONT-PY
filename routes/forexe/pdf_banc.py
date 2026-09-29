# routes/forexe/pdf_banc.py
"""
Slice 0078-05: the signing benches' server-side mirror. BENCH ONLY.

    PUT /api/forexe/banc/pdf/<tip>/<iddoc>?nume=<file>&pas=<text>  -> new row
    GET /api/forexe/banc/pdf                                    -> the rows, newest first, no content
    GET /api/forexe/banc/pdf/<id>                               -> one row's bytes (ETag = sha)

Stores the PDF body exactly as received, as a NEW row of `KBOT_BANC_PDF`
(sql/0078_05_kbot_banc_pdf.sql), so what a signing session actually sends -- and when -- can be
examined on the server. Nothing else is touched: no parent document, no concurrency check
(`X-Sha-Precedent` is only stored), no `Semnatura` column of any other table.

Only for the database `000_DEMO`: any other session database gets 403 -- this route must never
write into a unit's data. Same wire rules as routes/forexe/pdf.py: raw bytes, `X-Sha256`
recomputed over the body (400 on mismatch), `X-Semnatura` / `X-Semnaturi` / `X-Statie` stored
as sent (the last two base64 UTF-8 JSON, stored decoded). The answer has the same shape as the
real upload (sha256, nume_fisier, dimensiune, semnatura) plus the row id.

The blob content is never logged -- only sizes and checksums.
"""
import base64
import binascii
import hashlib
import json
import logging

from flask import request, g, current_app

from routes.auth.guard import require_session
from utils.database import get_kbot_connection

from . import forexe_bp

logger = logging.getLogger(__name__)

BANC_DB = "000_DEMO"
BANC_TABLE = "KBOT_BANC_PDF"
MAX_PDF_BYTES = 17 * 1024 * 1024
TIPURI = ("ddf", "ord", "nc")


def _json(payload, status):
    body = json.dumps(payload, ensure_ascii=False)
    return current_app.response_class(body, status=status, mimetype="application/json")


def _decode_b64(raw):
    """Base64 UTF-8 text of a header, or None when absent / unreadable (stored as NULL)."""
    if not raw:
        return None
    try:
        return base64.b64decode(raw.strip(), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None


@forexe_bp.route("/api/forexe/banc/pdf/<tip>/<int:iddoc>", methods=["PUT"])
@require_session
def put_banc_pdf(tip, iddoc):
    db_name = g.session.db_name
    if db_name != BANC_DB:
        return _json({"error": f"Ruta de probă scrie doar în baza {BANC_DB} (sesiunea e pe {db_name})."}, 403)
    tip = (tip or "").lower()
    if tip not in TIPURI:
        return _json({"error": f"Tip necunoscut: «{tip}» (ddf / ord / nc)."}, 400)

    octeti = request.get_data()
    if not octeti:
        return _json({"error": "Corpul cererii este gol: nu s-a primit niciun fișier."}, 400)
    if len(octeti) > MAX_PDF_BYTES:
        return _json({"error": f"Fișierul depășește {MAX_PDF_BYTES // (1024 * 1024)} MB."}, 413)
    sha_client = (request.headers.get("X-Sha256") or "").strip().lower()
    sha_server = hashlib.sha256(octeti).hexdigest()
    if sha_client != sha_server:
        logger.warning("[forexe.banc] %s %s=%s: sumă nepotrivită (client=%s… server=%s…)",
                       db_name, tip, iddoc, sha_client[:8], sha_server[:8])
        return _json({"error": "Fișierul a sosit corupt: suma de control nu corespunde."}, 400)

    nume = (request.args.get("nume") or "")[:255]
    pas = (request.args.get("pas") or "")[:255]
    semnatura = (request.headers.get("X-Semnatura") or "").strip()[:64]
    precedent = (request.headers.get("X-Sha-Precedent") or "").strip()[:64]
    semnaturi = _decode_b64(request.headers.get("X-Semnaturi"))
    statie = _decode_b64(request.headers.get("X-Statie"))
    operator = (getattr(g.session, "username", "") or "")[:128]

    conn = None
    try:
        conn = get_kbot_connection(db_name)
        cursor = conn.cursor()
        cursor.execute(
            f"INSERT INTO `{BANC_TABLE}` (Tip, IdDoc, NumeFisier, Pas, Semnatura, Semnaturi, Statie, "
            "ShaPrecedent, Sha256, Dimensiune, Operator, Continut) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (tip.upper(), iddoc, nume, pas, semnatura, semnaturi, statie,
             precedent, sha_server, len(octeti), operator, octeti))
        row_id = cursor.lastrowid
        conn.commit()
        logger.info("[forexe.banc] %s %s=%s: rând %s, %s octeți, sha %s…, roluri «%s», fișier «%s»",
                    db_name, tip, iddoc, row_id, len(octeti), sha_server[:8], semnatura, nume)
        return _json({"id": row_id, "sha256": sha_server, "nume_fisier": nume,
                      "dimensiune": len(octeti), "semnatura": semnatura or None}, 200)
    except Exception:
        logger.exception("[forexe.banc] %s %s=%s: scrierea a eșuat", db_name, tip, iddoc)
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                logger.exception("[forexe.banc] rollback eșuat")
        return _json({"error": f"PDF-ul nu a putut fi scris în {BANC_TABLE}. "
                               "Tabela există în 000_DEMO? (sql/0078_05_kbot_banc_pdf.sql)"}, 500)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                logger.exception("[forexe.banc] închiderea conexiunii a eșuat")


def _refuza_alta_baza():
    """403 for any session database other than 000_DEMO, else None."""
    db_name = g.session.db_name
    if db_name != BANC_DB:
        return _json({"error": f"Ruta de probă citește doar din baza {BANC_DB} (sesiunea e pe {db_name})."}, 403)
    return None


@forexe_bp.route("/api/forexe/banc/pdf", methods=["GET"])
@require_session
def list_banc_pdf():
    """Every stored upload, newest first, WITHOUT the content (at most 500 rows)."""
    refuz = _refuza_alta_baza()
    if refuz is not None:
        return refuz
    conn = None
    try:
        conn = get_kbot_connection(BANC_DB)
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            f"SELECT Id, Primit, Tip, IdDoc, NumeFisier, Pas, Semnatura, Sha256, Dimensiune, Operator "
            f"FROM `{BANC_TABLE}` ORDER BY Id DESC LIMIT 500")
        fisiere = [{
            "id": r["Id"],
            "primit": r["Primit"].strftime("%Y-%m-%d %H:%M:%S.%f")[:23] if r["Primit"] else "",
            "tip": r["Tip"] or "",
            "id_doc": r["IdDoc"],
            "nume_fisier": r["NumeFisier"] or "",
            "pas": r["Pas"] or "",
            "semnatura": r["Semnatura"] or "",
            "sha256": r["Sha256"] or "",
            "dimensiune": r["Dimensiune"],
            "operator": r["Operator"] or "",
        } for r in cursor.fetchall()]
        return _json({"fisiere": fisiere}, 200)
    except Exception:
        logger.exception("[forexe.banc] listarea a eșuat")
        return _json({"error": f"Lista din {BANC_TABLE} nu a putut fi citită. "
                               "Tabela există în 000_DEMO? (sql/0078_05_kbot_banc_pdf.sql)"}, 500)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                logger.exception("[forexe.banc] închiderea conexiunii a eșuat")


@forexe_bp.route("/api/forexe/banc/pdf/<int:row_id>", methods=["GET"])
@require_session
def get_banc_pdf(row_id):
    """The stored bytes of one upload, with its sha as ETag (the client re-checks it)."""
    refuz = _refuza_alta_baza()
    if refuz is not None:
        return refuz
    conn = None
    try:
        conn = get_kbot_connection(BANC_DB)
        cursor = conn.cursor()
        cursor.execute(f"SELECT Continut, Sha256 FROM `{BANC_TABLE}` WHERE Id = %s", (row_id,))
        row = cursor.fetchone()
        if row is None:
            return _json({"error": f"Rândul {row_id} nu există în {BANC_TABLE}."}, 404)
        octeti = bytes(row[0])
        sha = hashlib.sha256(octeti).hexdigest()
        if sha != (row[1] or "").lower():
            logger.error("[forexe.banc] rând %s: suma stocată nu corespunde conținutului", row_id)
            return _json({"error": f"Rândul {row_id}: conținutul nu corespunde sumei stocate."}, 500)
        resp = current_app.response_class(octeti, status=200, mimetype="application/pdf")
        resp.headers["Content-Length"] = str(len(octeti))
        resp.headers["ETag"] = f'"{sha}"'
        logger.info("[forexe.banc] rând %s -> 200 (%s octeți, sha %s…)", row_id, len(octeti), sha[:8])
        return resp
    except Exception:
        logger.exception("[forexe.banc] citirea rândului %s a eșuat", row_id)
        return _json({"error": f"Rândul {row_id} nu a putut fi citit."}, 500)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                logger.exception("[forexe.banc] închiderea conexiunii a eșuat")
