# routes/ord/sync_mdb_acc.py
# GET /api/ord/sync_mdb_acc/list   -> {"idordp_list": [1, 2, ...]}
# GET /api/ord/sync_mdb_acc?idordp=Y -> JSON complet ORD + PART/TBL/TBL_REC/ATT/DOC
#
# JSON-ul returnat foloseste NUMELE COLOANELOR DIN ACCESS unde difera:
#   MariaDB.IdClsf    -> JSON.IdClsfPY  (Access column name in FX_ORD_TBL)
#   MariaDB.IdClsfAcc -> JSON.IdClsf    (Access column name in FX_ORD_TBL)
#
# FK-uri Access recuperate prin JOIN:
#   FX_ORD_PART.IDORD      : JOIN FX_ORD_PART x FX_ORD pe IDORDP
#   FX_ORD_TBL.IDORDPART   : JOIN FX_ORD_TBL x FX_ORD_PART pe IDORDPARTP
#   FX_ORD_TBL.IDORD       : JOIN FX_ORD_TBL x FX_ORD pe IDORDP
#   FX_ORD_TBL_REC.IDORDTBL: JOIN FX_ORD_TBL_REC x FX_ORD_TBL pe IDORDTBLP
#   FX_ORD_ATT.IDORD/IDORDPART: JOIN cu FX_ORD si FX_ORD_PART
#   (identic FX_ORD_DOC)

import datetime
import logging

from flask import jsonify, request

from utils.security import require_api_key
from utils.db_retry import _run_with_retry

from . import ord_bp

logger = logging.getLogger(__name__)


def _rows(cursor, sql, params=()):
    """Executa SQL si returneaza lista de dict-uri cu serializare tipuri.
    Suporta atat cursor tuple cat si cursor dictionary (DictCursor).
    """
    cursor.execute(sql, params)
    cols = [d[0] for d in cursor.description]
    result = []
    for row in cursor.fetchall():
        raw = row.values() if isinstance(row, dict) else row
        d = {}
        for col, val in zip(cols, raw):
            if isinstance(val, (datetime.date, datetime.datetime)):
                d[col] = val.isoformat()
            elif isinstance(val, bytes):
                d[col] = val.decode("utf-8", errors="replace")
            else:
                d[col] = val
        result.append(d)
    return result


def _read_ord_list(cursor, _data):
    cursor.execute("SELECT IDORDP FROM FX_ORD ORDER BY IDORDP")
    return {"idordp_list": [row[0] if not isinstance(row, dict) else row["IDORDP"]
                            for row in cursor.fetchall()]}


def _read_ord_one(cursor, idordp: int):
    rows = _rows(cursor, "SELECT * FROM FX_ORD WHERE IDORDP = %s", (idordp,))
    if not rows:
        raise ValueError(f"IDORDP={idordp} negasit pe server")
    ord_row = rows[0]

    part_rows = _rows(cursor, """
        SELECT p.IDORDPARTP, p.IDORDPART, p.IDORDP,
               o.IDORD,
               p.Counter, p.DenBene, p.CodPartener, p.IdPartener,
               p.CodFiscal, p.ContIBAN, p.Banca
        FROM FX_ORD_PART p
        JOIN FX_ORD o ON p.IDORDP = o.IDORDP
        WHERE p.IDORDP = %s
    """, (idordp,))

    tbl_rows = _rows(cursor, """
        SELECT t.IDORDTBLP, t.IDORDTBL, t.IDORDP, t.IDORDPARTP, t.IDRP,
               t.IdClsf    AS IdClsfPY,
               t.IdClsfAcc AS IdClsf,
               t.CodAI, t.CodAngajament, t.CodIndicator, t.CodSSI,
               t.TotalReceptii, t.PlatiAnt, t.Valoare, t.Ramas, t.Explicatie,
               p.IDORDPART,
               o.IDORD
        FROM FX_ORD_TBL t
        JOIN FX_ORD_PART p ON t.IDORDPARTP = p.IDORDPARTP
        JOIN FX_ORD o      ON t.IDORDP     = o.IDORDP
        WHERE t.IDORDP = %s
    """, (idordp,))

    tbl_rec_rows = _rows(cursor, """
        SELECT r.IDORDRECP, r.IDORDTBLP, r.IDORDREC, r.IdPlataFX, r.Valoare,
               t.IDORDTBL
        FROM FX_ORD_TBL_REC r
        JOIN FX_ORD_TBL t ON r.IDORDTBLP = t.IDORDTBLP
        WHERE t.IDORDP = %s
    """, (idordp,))

    att_rows = _rows(cursor, """
        SELECT a.IDORDATTP, a.IDORDATT, a.IDORDP, a.IDORDPARTP, a.Imagine,
               p.IDORDPART,
               o.IDORD
        FROM FX_ORD_ATT a
        JOIN FX_ORD_PART p ON a.IDORDPARTP = p.IDORDPARTP
        JOIN FX_ORD o      ON a.IDORDP     = o.IDORDP
        WHERE a.IDORDP = %s
    """, (idordp,))

    doc_rows = _rows(cursor, """
        SELECT d.IDORDDOCP, d.IDORDDOC, d.IDORDP, d.IDORDPARTP,
               d.DocJust, d.NumeDoc, d.TipDoc,
               p.IDORDPART,
               o.IDORD
        FROM FX_ORD_DOC d
        JOIN FX_ORD_PART p ON d.IDORDPARTP = p.IDORDPARTP
        JOIN FX_ORD o      ON d.IDORDP     = o.IDORDP
        WHERE d.IDORDP = %s
    """, (idordp,))

    return {
        "ord":      ord_row,
        "parts":    part_rows,
        "tbls":     tbl_rows,
        "tbl_recs": tbl_rec_rows,
        "atts":     att_rows,
        "docs":     doc_rows,
    }


@ord_bp.route("/api/ord/sync_mdb_acc/list", methods=["GET"])
@require_api_key
def sync_mdb_acc_ord_list():
    db_name = request.args.get("db_name", "")
    if not db_name:
        return jsonify({"error": "db_name lipsa"}), 400
    try:
        result = _run_with_retry(
            lambda cursor: _read_ord_list(cursor, None), {"db_name": db_name}
        )
        return jsonify(result), 200
    except Exception as e:
        logger.error(f"[SYNC_MDB_ACC_ORD_LIST] ERROR: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@ord_bp.route("/api/ord/sync_mdb_acc", methods=["GET"])
@require_api_key
def sync_mdb_acc_ord():
    db_name    = request.args.get("db_name", "")
    idordp_str = request.args.get("idordp", "")
    if not db_name:
        return jsonify({"error": "db_name lipsa"}), 400
    if not idordp_str:
        return jsonify({"error": "idordp lipsa"}), 400
    try:
        idordp = int(idordp_str)
    except ValueError:
        return jsonify({"error": f"idordp invalid: {idordp_str!r}"}), 400
    try:
        result = _run_with_retry(
            lambda cursor: _read_ord_one(cursor, idordp), {"db_name": db_name}
        )
        logger.info(
            f"[SYNC_MDB_ACC_ORD] OK IDORDP={idordp} "
            f"parts={len(result['parts'])} tbls={len(result['tbls'])} "
            f"tbl_recs={len(result['tbl_recs'])} "
            f"atts={len(result['atts'])} docs={len(result['docs'])}"
        )
        return jsonify(result), 200
    except Exception as e:
        logger.error(f"[SYNC_MDB_ACC_ORD] ERROR IDORDP={idordp}: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500