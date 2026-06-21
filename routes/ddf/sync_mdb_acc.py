# routes/ddf/sync_mdb_acc.py
# GET /api/ddf/sync_mdb_acc/list  -> {"iddf_list": [71, 72, ...]}
# GET /api/ddf/sync_mdb_acc?iddf=Y -> JSON complet cu DDF + REV + SA/SB/PRT/ATT
#
# JSON-ul returnat foloseste NUMELE COLOANELOR DIN ACCESS, nu din MariaDB:
#   MariaDB.IdClsfAcc -> JSON.IdClsf    (Access column name)
#   MariaDB.IdClsf    -> JSON.IdClsfPY  (Access column name)
# Astfel VBA poate folosi json("IdClsf") direct pentru rs!IdClsf.

import datetime
import logging

from flask import jsonify, request

from utils.security import require_api_key
from utils.db_retry import _run_with_retry

from . import ddf_bp

logger = logging.getLogger(__name__)


def _rows(cursor, sql, params=()):
    """Executa SQL si returneaza lista de dict-uri cu serializare tipuri.
    Suporta atat cursor tuple cat si cursor dictionary (DictCursor).
    """
    cursor.execute(sql, params)
    cols = [d[0] for d in cursor.description]
    result = []
    for row in cursor.fetchall():
        # cursor dictionary -> row este dict; cursor normal -> row este tuple
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


def _read_ddf_list(cursor, _data):
    cursor.execute("SELECT IDDF FROM FX_DDF ORDER BY IDDF")
    return {"iddf_list": [row[0] if not isinstance(row, dict) else row["IDDF"]
                          for row in cursor.fetchall()]}


def _read_ddf_one(cursor, iddf: int):
    rows = _rows(cursor, "SELECT * FROM FX_DDF WHERE IDDF = %s", (iddf,))
    if not rows:
        raise ValueError(f"IDDF={iddf} negasit pe server")
    ddf_row = rows[0]

    revs_raw = _rows(cursor,
        "SELECT * FROM FX_DDF_REV WHERE IDDF = %s ORDER BY IDREV", (iddf,))

    revs = []
    for rev in revs_raw:
        idrev = rev["IDREV"]

        sa_rows = _rows(cursor, """
            SELECT IdSecA, IDDF, IDREV,
                   IdUnitate, CodAngajament, CodIndicator,
                   CodPartener, IdPartener,
                   IdClsfAcc AS IdClsf,
                   IdClsf    AS IdClsfPY,
                   Clsf, ElementFund, ParametriiFund,
                   ValPrec, ValCur, ValTot,
                   PartInd, Ramane, SS
            FROM FX_DDF_REV_SA
            WHERE IDREV = %s
        """, (idrev,))

        sb_rows = _rows(cursor, """
            SELECT IdSecB, IDDF, IDREV,
                   CodAngajament, CodIndicator, CodPartener,
                   IdUnitate, IdPartener,
                   IdClsfAcc AS IdClsf,
                   IdClsf    AS IdClsfPY,
                   CodSSI,
                   CA_Anterior, Inf1, CA_Curent,
                   CB_Anterior, Inf2, CB_Curent, SS
            FROM FX_DDF_REV_SB
            WHERE IDREV = %s
        """, (idrev,))

        prt_rows = _rows(cursor, """
            SELECT IDREVP, IDDF, IDREV,
                   IdClsfAcc AS IdClsf,
                   IdClsf    AS IdClsfPY,
                   CodAngajament, DateFisier, Expl, Tip
            FROM FX_DDF_REV_PRT
            WHERE IDREV = %s
        """, (idrev,))

        att_rows = _rows(cursor, """
            SELECT IdRevAtt, IDDF, IDREV,
                   IDVBNET, CaleFisier, PrtScr, DateFisier
            FROM FX_DDF_REV_ATT
            WHERE IDREV = %s
        """, (idrev,))

        rev_dict = dict(rev)
        rev_dict["sa"]  = sa_rows
        rev_dict["sb"]  = sb_rows
        rev_dict["prt"] = prt_rows
        rev_dict["att"] = att_rows
        revs.append(rev_dict)

    return {"ddf": ddf_row, "revs": revs}


@ddf_bp.route("/api/ddf/sync_mdb_acc/list", methods=["GET"])
@require_api_key
def restore_ddf_list():
    db_name = request.args.get("db_name", "")
    if not db_name:
        return jsonify({"error": "db_name lipsa"}), 400
    try:
        result = _run_with_retry(
            lambda cursor: _read_ddf_list(cursor, None), {"db_name": db_name}
        )
        return jsonify(result), 200
    except Exception as e:
        logger.error(f"[RESTORE_DDF_LIST] ERROR: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@ddf_bp.route("/api/ddf/sync_mdb_acc", methods=["GET"])
@require_api_key
def restore_ddf():
    db_name  = request.args.get("db_name", "")
    iddf_str = request.args.get("iddf", "")
    if not db_name:
        return jsonify({"error": "db_name lipsa"}), 400
    if not iddf_str:
        return jsonify({"error": "iddf lipsa"}), 400
    try:
        iddf = int(iddf_str)
    except ValueError:
        return jsonify({"error": f"iddf invalid: {iddf_str!r}"}), 400
    try:
        result = _run_with_retry(
            lambda cursor: _read_ddf_one(cursor, iddf), {"db_name": db_name}
        )
        logger.info(f"[RESTORE_DDF] OK IDDF={iddf} revs={len(result['revs'])}")
        return jsonify(result), 200
    except Exception as e:
        logger.error(f"[RESTORE_DDF] ERROR IDDF={iddf}: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500