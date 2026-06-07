# routes/ddf/att.py
"""
INSERT direct in FX_DDF_REV_ATT (fara staging).
Relocat verbatim din monolit — nicio coloana afectata de schimbarea de schema.
"""
from typing import Dict, List

from flask import jsonify, request

from utils.security import require_api_key
from utils.database import get_db_connection

from . import ddf_bp, _dlog, logger


@ddf_bp.route('/api/ddf/att/insert', methods=['POST'])
@require_api_key
def att_insert():
    """
    INSERT direct in FX_DDF_REV_ATT. Fara staging.
    Payload: {db_name, rows: [{TmpID, IDDF, IDREV, IDVBNET, CaleFisier, PrtScr, DateFisier}]}
    Returneaza ATT_Map: [{TmpID, IdRevAtt}]
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'error': 'Body JSON lipsa'}), 400

    rows = data.get('rows', [])
    if not rows:
        return jsonify({'error': 'rows este obligatoriu si nu poate fi gol'}), 400

    conn = None
    cursor = None
    try:
        db_name = data.get("db_name")
        _dlog(f"[att_insert] db={db_name} rows={len(rows)}")
        conn   = get_db_connection(db_name)
        cursor = conn.cursor()

        att_map: List[Dict] = []
        for row in rows:
            cursor.execute("""
                INSERT INTO FX_DDF_REV_ATT (
                    IDDF, IDREV, IDVBNET, CaleFisier, PrtScr, DateFisier
                ) VALUES (%s, %s, %s, %s, %s, %s)
            """, (
                row.get('IDDF'),       row.get('IDREV'),
                row.get('IDVBNET'),    row.get('CaleFisier'),
                row.get('PrtScr'),     row.get('DateFisier'),
            ))
            att_map.append({'TmpID': row.get('TmpID'), 'IdRevAtt': cursor.lastrowid})

        conn.commit()
        _dlog(f"[att_insert] ATT_Map={att_map}")
        return jsonify({'ok': True, 'ATT_Map': att_map}), 200

    except Exception as e:
        if conn: conn.rollback()
        logger.exception("att_insert error")
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor: cursor.close()
        if conn:   conn.close()