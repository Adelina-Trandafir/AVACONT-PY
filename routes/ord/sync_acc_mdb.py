# routes/ord/sync_acc_mdb.py
# POST /api/ord/sync_acc_mdb
# Sincronizare bulk ORD: Access -> MariaDB.
# Cate un IDORDP per request.
#
# Mapari non-triviale Access -> JSON:
#   TBL: Access.IdClsf   -> JSON.IdClsfAcc -> MariaDB.IdClsfAcc
#        Access.IdClsfPY -> JSON.IdClsf    -> MariaDB.IdClsf (FK Clasificatii)
#   TBL: IDORDPARTP rezolvat in VBA prin JOIN FX_ORD_TBL x FX_ORD_PART
#   TBL_REC: IDORDTBLP rezolvat in VBA prin JOIN x FX_ORD_TBL
#   ATT/DOC: IDORDPARTP rezolvat in VBA prin JOIN x FX_ORD_PART
# Coloane Access omise (nu exista in MariaDB):
#   FX_ORD_TBL: IDRR, IDRD, IDORDT

import logging

from flask import jsonify, request

from utils.security import require_api_key
from utils.db_retry import _run_with_retry
from utils.parsing import (
    _strict_int, _strict_pos_int, _strict_str, _strict_str_nonempty,
    _opt_int, _opt_str,
)

from . import ord_bp

logger = logging.getLogger(__name__)


def _opt_float(val, field: str):
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        raise ValueError(f"{field}: nu poate fi convertit la float: {val!r}")


def _as_tiny(val, field: str, nullable: bool = True):
    if val is None:
        if nullable:
            return None
        raise ValueError(f"{field}: None, asteptat 0 sau 1")
    return 1 if val else 0


def _insert_ord_one(cursor, data: dict):
    ord_     = data["ord"]
    parts    = data.get("parts",    [])
    tbls     = data.get("tbls",     [])
    tbl_recs = data.get("tbl_recs", [])
    atts     = data.get("atts",     [])
    docs     = data.get("docs",     [])

    idordp = _strict_pos_int(ord_["IDORDP"], "ord.IDORDP")

    cursor.execute("DELETE FROM FX_ORD WHERE IDORDP = %s", (idordp,))
    logger.debug(f"[SYNC_ORD] DELETE IDORDP={idordp} deleted={cursor.rowcount}")

    cursor.execute("""
        INSERT INTO FX_ORD
            (IDORDP, IDORD, IDDF, IDRR, IDRH,
             NrORD, DataORD, Comp, CUAL,
             Incarcat, Preluat, CodAngajament)
        VALUES (%s, %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s)
    """, (
        idordp,
        _strict_pos_int(ord_["IDORD"],             "ord.IDORD"),
        _opt_int(ord_.get("IDDF"),                 "ord.IDDF"),
        _opt_int(ord_.get("IDRR"),                 "ord.IDRR"),
        _opt_int(ord_.get("IDRH"),                 "ord.IDRH"),
        _strict_int(ord_["NrORD"],                 "ord.NrORD"),
        ord_.get("DataORD"),
        _opt_str(ord_.get("Comp")),
        _opt_str(ord_.get("Cual")),
        _as_tiny(ord_.get("Incarcat"), "ord.Incarcat", nullable=False),
        _as_tiny(ord_.get("Preluat"),  "ord.Preluat",  nullable=False),
        _strict_str_nonempty(ord_["CodAngajament"], "ord.CodAngajament"),
    ))

    for p in parts:
        cursor.execute("""
            INSERT INTO FX_ORD_PART
                (IDORDPARTP, IDORDPART, IDORDP,
                 Counter, DenBene, CodPartener, IdPartener,
                 CodFiscal, ContIBAN, Banca)
            VALUES (%s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s)
        """, (
            _strict_pos_int(p["IDORDPARTP"],      "part.IDORDPARTP"),
            _strict_int(p["IDORDPART"],           "part.IDORDPART"),
            idordp,
            _strict_str(p["Counter"],             "part.Counter"),
            _strict_str_nonempty(p["DenBene"],    "part.DenBene"),
            _opt_str(p.get("CodPartener")),
            _opt_int(p.get("IdPartener"),         "part.IdPartener"),
            _strict_str(p["CodFiscal"],           "part.CodFiscal"),
            _strict_str(p["ContIBAN"],            "part.ContIBAN"),
            _strict_str(p["Banca"],               "part.Banca"),
        ))

    for t in tbls:
        cursor.execute("""
            INSERT INTO FX_ORD_TBL
                (IDORDTBLP, IDORDTBL, IDORDP, IDORDPARTP, IDRP,
                 IdClsf, IdClsfAcc, CodAI, CodAngajament, CodIndicator, CodSSI,
                 TotalReceptii, PlatiAnt, Valoare, Ramas, Explicatie)
            VALUES (%s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s)
        """, (
            _strict_pos_int(t["IDORDTBLP"],       "tbl.IDORDTBLP"),
            _strict_int(t["IDORDTBL"],            "tbl.IDORDTBL"),
            idordp,
            _strict_pos_int(t["IDORDPARTP"],      "tbl.IDORDPARTP"),
            _opt_int(t.get("IDRP"),               "tbl.IDRP"),
            _strict_pos_int(t["IdClsf"],          "tbl.IdClsf"),
            _strict_int(t["IdClsfAcc"],           "tbl.IdClsfAcc"),
            _opt_str(t.get("CodAI")),
            _opt_str(t.get("CodAngajament")),
            _opt_str(t.get("CodIndicator")),
            _opt_str(t.get("CodSSI")),
            _opt_float(t.get("TotalReceptii"),    "tbl.TotalReceptii"),
            _opt_float(t.get("PlatiAnt"),         "tbl.PlatiAnt"),
            _opt_float(t.get("Valoare"),          "tbl.Valoare"),
            _opt_float(t.get("Ramas"),            "tbl.Ramas"),
            t.get("Explicatie"),
        ))

    for r in tbl_recs:
        cursor.execute("""
            INSERT INTO FX_ORD_TBL_REC
                (IDORDRECP, IDORDTBLP, IDORDREC, IDRP, Valoare)
            VALUES (%s, %s, %s, %s, %s)
        """, (
            _strict_pos_int(r["IDORDRECP"],       "rec.IDORDRECP"),
            _strict_pos_int(r["IDORDTBLP"],       "rec.IDORDTBLP"),
            _strict_int(r["IDORDREC"],            "rec.IDORDREC"),
            _opt_int(r.get("IdPlataFX"),          "rec.IdPlataFX"),
            _opt_float(r.get("Valoare"),          "rec.Valoare"),
        ))

    for a in atts:
        cursor.execute("""
            INSERT INTO FX_ORD_ATT
                (IDORDATTP, IDORDATT, IDORDP, IDORDPARTP, Imagine)
            VALUES (%s, %s, %s, %s, %s)
        """, (
            _strict_pos_int(a["IDORDATTP"],       "att.IDORDATTP"),
            _strict_int(a["IDORDATT"],            "att.IDORDATT"),
            idordp,
            _opt_int(a.get("IDORDPARTP"),         "att.IDORDPARTP"),
            a.get("Imagine"),
        ))

    for d in docs:
        cursor.execute("""
            INSERT INTO FX_ORD_DOC
                (IDORDDOCP, IDORDDOC, IDORDP, IDORDPARTP,
                 DocJust, NumeDoc, TipDoc)
            VALUES (%s, %s, %s, %s,
                    %s, %s, %s)
        """, (
            _strict_pos_int(d["IDORDDOCP"],       "doc.IDORDDOCP"),
            _strict_int(d["IDORDDOC"],            "doc.IDORDDOC"),
            idordp,
            _opt_int(d.get("IDORDPARTP"),         "doc.IDORDPARTP"),
            d.get("DocJust"),
            _opt_str(d.get("NumeDoc")),
            _opt_str(d.get("TipDoc")),
        ))

    return {
        "IDORDP":   idordp,
        "parts":    len(parts),
        "tbls":     len(tbls),
        "tbl_recs": len(tbl_recs),
        "atts":     len(atts),
        "docs":     len(docs),
    }


@ord_bp.route("/api/ord/sync_acc_mdb", methods=["POST"])
@require_api_key
def sync_ord():
    data = request.json
    if not data:
        return jsonify({"error": "Payload JSON lipsa"}), 400
    if not data.get("db_name"):
        return jsonify({"error": "db_name lipsa"}), 400
    try:
        result = _run_with_retry(
            lambda cursor: _insert_ord_one(cursor, data), data
        )
        logger.info(
            f"[SYNC_ORD] OK IDORDP={result['IDORDP']} "
            f"parts={result['parts']} tbls={result['tbls']} "
            f"tbl_recs={result['tbl_recs']} atts={result['atts']} docs={result['docs']}"
        )
        return jsonify(result), 200
    except Exception as e:
        logger.error(f"[SYNC_ORD] ERROR: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500