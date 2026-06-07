# routes/ord/patch.py
"""
Patch ORD (Sectiunea 9 din monolit) + endpoint-urile /api/ord/patch/*.

Patch: adaugari punctuale dupa salvarea initiala, fara circuit complet
de staging. IDORDPARTP trebuie sa fie mereu > 0 (PART confirmat in DB).
Daca e necesar un PART nou, se apeleaza intai patch/part → se obtine
IDORDPARTP real din response → se foloseste la patch/tbl|att|doc.
"""
from flask import jsonify, request

from utils.security import require_api_key
from utils.db_retry import _run_with_retry
from utils.parsing import (
    _strict_pos_int,
    _strict_float,
    _strict_str,
    _strict_str_nonempty,
    _opt_int,
    _opt_str,
)

from . import ord_bp, logger


# ===========================================================================
# GUARDS
# ===========================================================================

def _assert_ord_exists(cursor, idordp: int):
    """Raise daca FX_ORD cu IDORDP nu exista. Folosit ca guard in patch."""
    cursor.execute("SELECT 1 FROM FX_ORD WHERE IDORDP=%s", (idordp,))
    if not cursor.fetchone():
        raise ValueError(f"FX_ORD cu IDORDP={idordp} nu exista")


def _assert_part_belongs_to_ord(cursor, idordpartp: int, idordp: int):
    """
    Raise daca IDORDPARTP nu apartine IDORDP.
    Previne inserarea unui TBL/ATT/DOC sub un PART din alt ORD.
    """
    cursor.execute(
        "SELECT 1 FROM FX_ORD_PART WHERE IDORDPARTP=%s AND IDORDP=%s",
        (idordpartp, idordp),
    )
    if not cursor.fetchone():
        raise ValueError(
            f"IDORDPARTP={idordpartp} nu apartine IDORDP={idordp} "
            "sau nu exista in FX_ORD_PART"
        )


# ===========================================================================
# PATCH — FUNCTII PURE
# ===========================================================================

def _patch_part(cursor, idordp: int, rows: list) -> list:
    """
    Insereaza PART-uri noi intr-un ORD existent (fara staging).
    Returneaza [{TmpID, IDORDPARTP}].
    """
    _assert_ord_exists(cursor, idordp)
    part_map = []
    for r in rows:
        cursor.execute("""
            INSERT INTO FX_ORD_PART
                (IDORDP, IDORDPART, Counter, DenBene,
                 CodPartener, IdPartener, CodFiscal, ContIBAN, Banca)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            idordp,
            _strict_pos_int(r.get("IDORDPART"),    "IDORDPART"),
            _strict_str(r.get("Counter"),          "Counter"),
            _strict_str_nonempty(r.get("DenBene"), "DenBene"),
            _opt_str(r.get("CodPartener")),
            _opt_int(r.get("IdPartener"),          "IdPartener"),
            _strict_str(r.get("CodFiscal"),        "CodFiscal"),
            _strict_str(r.get("ContIBAN"),         "ContIBAN"),
            _strict_str(r.get("Banca"),            "Banca"),
        ))
        part_map.append({
            "TmpID":      _strict_pos_int(r.get("TmpID"), "TmpID"),
            "IDORDPARTP": cursor.lastrowid,
        })
    logger.debug(f"[PATCH][PART] IDORDP={idordp} inserted={len(rows)}")
    return part_map


def _patch_tbl(cursor, idordp: int, rows: list) -> list:
    """
    Insereaza TBL-uri noi intr-un ORD existent (fara staging).
    IDORDPARTP trebuie sa fie > 0 (PART confirmat).
    IDRP: nullable — FK read-only catre FX_Receptii_Plati (v5).
    IDRD: eliminat din v5.
    Returneaza [{TmpID, IDORDTBLP}].
    """
    _assert_ord_exists(cursor, idordp)
    tbl_map = []
    for r in rows:
        idordpartp = _strict_pos_int(r.get("IDORDPARTP"), "IDORDPARTP")
        _assert_part_belongs_to_ord(cursor, idordpartp, idordp)
        cursor.execute("""
            INSERT INTO FX_ORD_TBL
                (IDORDP, IDORDPARTP, IDORDTBL,
                 CodAI, CodAngajament, CodIndicator, CodSSI,
                 TotalReceptii, PlatiAnt, Valoare, Ramas,
                 IdClsf, IdClsfAcc, Explicatie, IDRP)
            VALUES (%s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            idordp, idordpartp,
            _strict_pos_int(r.get("IDORDTBL"),    "IDORDTBL"),
            _strict_str_nonempty(r.get("CodAI"),  "CodAI"),
            _strict_str(r.get("CodAngajament"),   "CodAngajament"),
            _strict_str(r.get("CodIndicator"),    "CodIndicator"),
            _strict_str(r.get("CodSSI"),          "CodSSI"),
            _strict_float(r.get("TotalReceptii"), "TotalReceptii"),
            _strict_float(r.get("PlatiAnt"),      "PlatiAnt"),
            _strict_float(r.get("Valoare"),       "Valoare"),
            _strict_float(r.get("Ramas"),         "Ramas"),
            _opt_int(r.get("IdClsf"),             "IdClsf"),
            _opt_int(r.get("IdClsfAcc"),          "IdClsfAcc"),
            _opt_str(r.get("Explicatie")),
            _opt_int(r.get("IDRP"),               "IDRP"),    # v5: IDRP, fara IDRD
        ))
        tbl_map.append({
            "TmpID":     _strict_pos_int(r.get("TmpID"), "TmpID"),
            "IDORDTBLP": cursor.lastrowid,
        })
    logger.debug(f"[PATCH][TBL] IDORDP={idordp} inserted={len(rows)}")
    return tbl_map


def _patch_att(cursor, idordp: int, rows: list) -> list:
    """
    Insereaza ATT-uri noi intr-un ORD existent (fara staging).
    IDORDPARTP trebuie sa fie > 0 (PART confirmat).
    Returneaza [{TmpID, IDORDATTP}].
    """
    _assert_ord_exists(cursor, idordp)
    att_map = []
    for r in rows:
        idordpartp = _strict_pos_int(r.get("IDORDPARTP"), "IDORDPARTP")
        _assert_part_belongs_to_ord(cursor, idordpartp, idordp)
        cursor.execute("""
            INSERT INTO FX_ORD_ATT
                (IDORDP, IDORDPARTP, IDORDATT, Imagine)
            VALUES (%s, %s, %s, %s)
        """, (
            idordp, idordpartp,
            _strict_pos_int(r.get("IDORDATT"),     "IDORDATT"),
            _strict_str_nonempty(r.get("Imagine"), "Imagine"),
        ))
        att_map.append({
            "TmpID":     _strict_pos_int(r.get("TmpID"), "TmpID"),
            "IDORDATTP": cursor.lastrowid,
        })
    logger.debug(f"[PATCH][ATT] IDORDP={idordp} inserted={len(rows)}")
    return att_map


def _patch_doc(cursor, idordp: int, rows: list) -> list:
    """
    Insereaza DOC-uri noi intr-un ORD existent (fara staging).
    IDORDPARTP trebuie sa fie > 0 (PART confirmat).
    Returneaza [{TmpID, IDORDDOCP}].
    """
    _assert_ord_exists(cursor, idordp)
    doc_map = []
    for r in rows:
        idordpartp = _strict_pos_int(r.get("IDORDPARTP"), "IDORDPARTP")
        _assert_part_belongs_to_ord(cursor, idordpartp, idordp)
        cursor.execute("""
            INSERT INTO FX_ORD_DOC
                (IDORDP, IDORDPARTP, IDORDDOC,
                 DocJust, NumeDoc, TipDoc)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (
            idordp, idordpartp,
            _strict_pos_int(r.get("IDORDDOC"),     "IDORDDOC"),
            _opt_str(r.get("DocJust")),
            _strict_str_nonempty(r.get("NumeDoc"), "NumeDoc"),
            _strict_str_nonempty(r.get("TipDoc"),  "TipDoc"),
        ))
        doc_map.append({
            "TmpID":     _strict_pos_int(r.get("TmpID"), "TmpID"),
            "IDORDDOCP": cursor.lastrowid,
        })
    logger.debug(f"[PATCH][DOC] IDORDP={idordp} inserted={len(rows)}")
    return doc_map


# ===========================================================================
# PATCH ENDPOINTS
# ===========================================================================

@ord_bp.route("/api/ord/patch/part", methods=["POST"])
@require_api_key
def patch_part():
    """
    Insereaza PART-uri noi intr-un ORD existent (fara staging).
    Payload:  {db_name, IDORDP, rows: [{TmpID, IDORDPART, DenBene, ...}]}
    Response: {Part_Map: [{TmpID, IDORDPARTP}]}
    """
    data = request.json
    try:
        idordp = _strict_pos_int(data.get("IDORDP"), "IDORDP")
        rows   = data.get("rows", [])
        if not isinstance(rows, list):
            return jsonify({"error": "'rows' trebuie sa fie list"}), 400

        def operation(cursor):
            return {"Part_Map": _patch_part(cursor, idordp, rows)}

        return jsonify(_run_with_retry(operation, data)), 200

    except Exception as e:
        logger.error(f"[patch/part] ERROR: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@ord_bp.route("/api/ord/patch/tbl", methods=["POST"])
@require_api_key
def patch_tbl():
    """
    Insereaza TBL-uri noi intr-un ORD existent (fara staging).
    IDORDPARTP trebuie sa fie > 0 (PART confirmat).
    IDRP: optional (nullable — v5).
    Payload:  {db_name, IDORDP, rows: [{TmpID, IDORDPARTP, IDORDTBL, CodAI, ..., IDRP?}]}
    Response: {TBL_Map: [{TmpID, IDORDTBLP}]}
    """
    data = request.json
    try:
        idordp = _strict_pos_int(data.get("IDORDP"), "IDORDP")
        rows   = data.get("rows", [])
        if not isinstance(rows, list):
            return jsonify({"error": "'rows' trebuie sa fie list"}), 400

        def operation(cursor):
            return {"TBL_Map": _patch_tbl(cursor, idordp, rows)}

        return jsonify(_run_with_retry(operation, data)), 200

    except Exception as e:
        logger.error(f"[patch/tbl] ERROR: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@ord_bp.route("/api/ord/patch/att", methods=["POST"])
@require_api_key
def patch_att():
    """
    Insereaza ATT-uri noi intr-un ORD existent (fara staging).
    IDORDPARTP trebuie sa fie > 0 (PART confirmat).
    Payload:  {db_name, IDORDP, rows: [{TmpID, IDORDPARTP, IDORDATT, Imagine}]}
    Response: {ATT_Map: [{TmpID, IDORDATTP}]}
    """
    data = request.json
    try:
        idordp = _strict_pos_int(data.get("IDORDP"), "IDORDP")
        rows   = data.get("rows", [])
        if not isinstance(rows, list):
            return jsonify({"error": "'rows' trebuie sa fie list"}), 400

        def operation(cursor):
            return {"ATT_Map": _patch_att(cursor, idordp, rows)}

        return jsonify(_run_with_retry(operation, data)), 200

    except Exception as e:
        logger.error(f"[patch/att] ERROR: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@ord_bp.route("/api/ord/patch/doc", methods=["POST"])
@require_api_key
def patch_doc():
    """
    Insereaza DOC-uri noi intr-un ORD existent (fara staging).
    IDORDPARTP trebuie sa fie > 0 (PART confirmat).
    Payload:  {db_name, IDORDP, rows: [{TmpID, IDORDPARTP, IDORDDOC, NumeDoc, TipDoc, DocJust?}]}
    Response: {DOC_Map: [{TmpID, IDORDDOCP}]}
    """
    data = request.json
    try:
        idordp = _strict_pos_int(data.get("IDORDP"), "IDORDP")
        rows   = data.get("rows", [])
        if not isinstance(rows, list):
            return jsonify({"error": "'rows' trebuie sa fie list"}), 400

        def operation(cursor):
            return {"DOC_Map": _patch_doc(cursor, idordp, rows)}

        return jsonify(_run_with_retry(operation, data)), 200

    except Exception as e:
        logger.error(f"[patch/doc] ERROR: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500