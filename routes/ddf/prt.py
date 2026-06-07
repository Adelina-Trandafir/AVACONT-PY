# routes/ddf/prt.py
"""
CRUD direct in FX_DDF_REV_PRT (fara staging).
Relocat verbatim din monolit — nicio coloana afectata de schimbarea de schema.
"""
from typing import Dict, List

from flask import jsonify, request

from utils.security import require_api_key
from utils.database import get_db_connection

from . import ddf_bp, _dlog, logger


@ddf_bp.route('/api/ddf/prt/insert', methods=['POST'])
@require_api_key
def prt_insert():
    """
    INSERT direct in FX_DDF_REV_PRT. Fara staging.
    Payload: {db_name, rows: [{TmpID, IDDF, IDREV, IdClsf, IdClsfAcc, DateFisier, Expl, Tip, CodAngajament}]}
    Returneaza PRT_Map: [{TmpID, IDREVP}]
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
        _dlog(f"[prt_insert] db={db_name} rows={len(rows)}")
        conn   = get_db_connection(db_name)
        cursor = conn.cursor(dictionary=True)

        prt_map: List[Dict] = []
        for row in rows:
            cursor.execute("""
                INSERT INTO FX_DDF_REV_PRT (
                    IDDF, IDREV, IdClsf, IdClsfAcc,
                    DateFisier, Expl, Tip, CodAngajament
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                row.get('IDDF'),    row.get('IDREV'),
                row.get('IdClsf'),  row.get('IdClsfAcc'),
                row.get('DateFisier'), row.get('Expl'),
                row.get('Tip'),        row.get('CodAngajament'),
            ))
            prt_map.append({'TmpID': row.get('TmpID'), 'IDREVP': cursor.lastrowid})

        conn.commit()
        _dlog(f"[prt_insert] PRT_Map={prt_map}")
        return jsonify({'ok': True, 'PRT_Map': prt_map}), 200

    except Exception as e:
        if conn: conn.rollback()
        logger.exception("prt_insert error")
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor: cursor.close()
        if conn:   conn.close()


@ddf_bp.route('/api/ddf/prt/update', methods=['POST'])
@require_api_key
def prt_update():
    """
    UPDATE direct in FX_DDF_REV_PRT. Fara staging.
    Payload: {db_name, IDREVP, Expl, Tip}
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'error': 'Body JSON lipsa'}), 400

    idrevp = data.get('IDREVP')
    if not idrevp:
        return jsonify({'error': 'IDREVP este obligatoriu'}), 400

    conn = None
    cursor = None
    try:
        db_name = data.get("db_name")
        _dlog(f"[prt_update] db={db_name} IDREVP={idrevp}")
        conn   = get_db_connection(db_name)
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            UPDATE FX_DDF_REV_PRT
            SET Expl=%s, Tip=%s
            WHERE IDREVP = %s
        """, (
            data.get('Expl'), data.get('Tip'),
            idrevp,
        ))
        if cursor.rowcount == 0:
            return jsonify({'error': f'IDREVP={idrevp} nu exista'}), 404
        conn.commit()
        _dlog(f"[prt_update] IDREVP={idrevp} updated")
        return jsonify({'ok': True, 'IDREVP': idrevp}), 200

    except Exception as e:
        if conn: conn.rollback()
        logger.exception("prt_update error")
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor: cursor.close()
        if conn:   conn.close()


@ddf_bp.route('/api/ddf/prt/delete', methods=['POST'])
@require_api_key
def prt_delete():
    """
    DELETE direct din FX_DDF_REV_PRT. Fara staging.
    Payload: {db_name, IDREVP}
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'error': 'Body JSON lipsa'}), 400

    idrevp = data.get('IDREVP')
    if not idrevp:
        return jsonify({'error': 'IDREVP este obligatoriu'}), 400

    conn = None
    cursor = None
    try:
        db_name = data.get("db_name")
        _dlog(f"[prt_delete] db={db_name} IDREVP={idrevp}")
        conn   = get_db_connection(db_name)
        cursor = conn.cursor(dictionary=True)
        cursor.execute("DELETE FROM FX_DDF_REV_PRT WHERE IDREVP = %s", (idrevp,))
        if cursor.rowcount == 0:
            return jsonify({'error': f'IDREVP={idrevp} nu exista'}), 404
        conn.commit()
        _dlog(f"[prt_delete] IDREVP={idrevp} deleted")
        return jsonify({'ok': True, 'IDREVP': idrevp}), 200

    except Exception as e:
        if conn: conn.rollback()
        logger.exception("prt_delete error")
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor: cursor.close()
        if conn:   conn.close()