# routes/ddf/core.py
"""
Endpoint-uri principale DDF.

Pastreaza comportamentul tranzactional din monolit:
  - save/update/confirm folosesc conexiune directa (commit/rollback manual);
  - delete/rev_delete folosesc _run_with_retry (retry pe deadlock).

MODIFICARI fata de monolit:
  - update_ang_staging: IdUnitate scos complet din payload si din INSERT.
  - ddf_patch: SS / IdPartener / CodPartener scoase din ALLOWED (nu mai sunt
    coloane in FX_DDF); IdUnitate scos din BLOCKED (coloana nu mai exista).
  - sa_patch: endpoint NOU pentru patch pe FX_DDF_REV_SA (cheie IdSecA).
"""
import uuid

from flask import jsonify, request

from utils.security import require_api_key
from utils.database import get_db_connection
from utils.db_retry import _run_with_retry
from utils.parsing import _strict_pos_int

from . import ddf_bp, _dlog, logger
from .staging import (
    _insert_staging,
    _commit_staging_add,
    _commit_staging_mod,
    _commit_staging_upd_ang,
)

class NotFoundError(Exception):
    pass

# ===========================================================================
# STAGING — SAVE / UPDATE
# ===========================================================================
@ddf_bp.route('/api/ddf/save_staging', methods=['POST'])
@require_api_key
def save_staging():
    """
    Inlocuieste save_complex.
    Scrie in stg_* si returneaza token.
    Access salveaza local, apoi trimite /api/ddf/confirm.
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'error': 'Body JSON lipsa'}), 400

    token = str(uuid.uuid4())
    conn = None
    cursor = None
    try:
        db_name = data.get("db_name")
        _dlog(f"[save_staging] db={db_name} token={token}")
        conn   = get_db_connection(db_name)
        cursor = conn.cursor(dictionary=True)
        _insert_staging(cursor, token, 'ADD', data)
        conn.commit()
        return jsonify({'ok': True, 'token': token}), 200

    except Exception as e:
        if conn: conn.rollback()
        logger.exception("save_staging error token=%s", token)
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor: cursor.close()
        if conn:   conn.close()


@ddf_bp.route('/api/ddf/update_staging', methods=['POST'])
@require_api_key
def update_staging():
    """
    Inlocuieste update_complex.
    Scrie in stg_* si returneaza token.
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'error': 'Body JSON lipsa'}), 400

    token = str(uuid.uuid4())
    conn = None
    cursor = None
    try:
        db_name = data.get("db_name")
        _dlog(f"[update_staging] db={db_name} token={token}")
        conn   = get_db_connection(db_name)
        cursor = conn.cursor(dictionary=True)
        _insert_staging(cursor, token, 'MOD', data)
        conn.commit()
        return jsonify({'ok': True, 'token': token}), 200

    except Exception as e:
        if conn: conn.rollback()
        logger.exception("update_staging error token=%s", token)
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor: cursor.close()
        if conn:   conn.close()


@ddf_bp.route('/api/ddf/update_ang_staging', methods=['POST'])
@require_api_key
def update_ang_staging():
    """
    Scrie in stg_DocFund cu TipOperatie='UPD_ANG'.
    Payload: {db_name, IDDF, CodAngajament}
    (IdUnitate scos — FX_DDF nu mai are coloana.)
    Access actualizeaza local, apoi trimite /api/ddf/confirm.
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'error': 'Body JSON lipsa'}), 400

    iddf    = data.get('IDDF')
    cod_ang = data.get('CodAngajament')
    if not iddf or cod_ang is None:
        return jsonify({'error': 'IDDF si CodAngajament sunt obligatorii'}), 400

    token = str(uuid.uuid4())
    conn = None
    cursor = None
    try:
        db_name = data.get("db_name")
        _dlog(f"[update_ang_staging] db={db_name} IDDF={iddf} CodAngajament={cod_ang} token={token}")
        conn   = get_db_connection(db_name)
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            INSERT INTO stg_DocFund (Token, TipOperatie, IDDF, CodAngajament)
            VALUES (%s, 'UPD_ANG', %s, %s)
        """, (token, iddf, cod_ang))
        conn.commit()
        return jsonify({'ok': True, 'token': token}), 200

    except Exception as e:
        if conn: conn.rollback()
        logger.exception("update_ang_staging error token=%s", token)
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor: cursor.close()
        if conn:   conn.close()


# ===========================================================================
# CONFIRM
# ===========================================================================
@ddf_bp.route('/api/ddf/confirm', methods=['POST'])
@require_api_key
def ddf_confirm():
    """
    Primeste ACK de la Access.
    OK       -> muta din stg_* in tabele reale
    FAIL     -> sterge stg_* (CASCADE curata tot)
    FAIL_MOD -> marcheaza pentru reconciliere, nu atinge tabelele reale
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'error': 'Body JSON lipsa'}), 400

    token  = data.get('token')
    status = data.get('status')
    if not token or not status:
        return jsonify({'error': 'token si status sunt obligatorii'}), 400

    _dlog(f"[ddf_confirm] token={token} status={status}")

    conn = None
    cursor = None
    try:
        db_name = data.get("db_name")
        conn    = get_db_connection(db_name)
        cursor  = conn.cursor(dictionary=True)

        cursor.execute(
            "SELECT TipOperatie FROM stg_DocFund WHERE Token = %s AND Status = 'PENDING'",
            (token,)
        )
        row = cursor.fetchone()
        if not row:
            return jsonify({'error': 'Token invalid sau deja procesat'}), 404

        tip_operatie = row["TipOperatie"]  # type: ignore[index]
        _dlog(f"[ddf_confirm] TipOperatie={tip_operatie}")

        new_ids: dict = {}
        if status == 'OK':
            if tip_operatie == 'ADD':
                new_ids = _commit_staging_add(cursor, token)
            elif tip_operatie == 'MOD':
                new_ids = _commit_staging_mod(cursor, token)
            elif tip_operatie == 'UPD_ANG':
                new_ids = _commit_staging_upd_ang(cursor, token)
            else:
                return jsonify({'error': f'TipOperatie necunoscut: {tip_operatie}'}), 400

            cursor.execute("""
                UPDATE stg_DocFund SET Status='CONFIRMED', DataConfirm=NOW()
                WHERE Token = %s
            """, (token,))

        elif status == 'FAIL':
            cursor.execute("DELETE FROM stg_DocFund WHERE Token = %s", (token,))
            _dlog(f"[ddf_confirm] FAIL → DELETE token={token}")

        elif status == 'FAIL_MOD':
            cursor.execute("""
                UPDATE stg_DocFund SET Status='FAIL_MOD', DataConfirm=NOW()
                WHERE Token = %s
            """, (token,))
            logger.warning("FAIL_MOD token=%s - reconciliere necesara", token)

        else:
            return jsonify({'error': f'Status necunoscut: {status}'}), 400

        conn.commit()
        return jsonify({'ok': True, 'status': status, **new_ids}), 200

    except Exception as e:
        if conn: conn.rollback()
        logger.exception("ddf_confirm error token=%s", token)
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor: cursor.close()
        if conn:   conn.close()


# ===========================================================================
# PATCH FX_DDF
# ===========================================================================
@ddf_bp.route('/api/ddf/patch', methods=['POST'])
@require_api_key
def ddf_patch():
    """
    Actualizeaza DOAR campurile trimise in payload pentru un FX_DDF.
    Fara staging, fara confirm.
    Payload: {db_name, IDDF, <orice camp non-critic>}

    MODIFICARE: SS / IdPartener / CodPartener au fost scoase din ALLOWED
    (nu mai sunt coloane in FX_DDF). Pentru patch pe SS (acum la nivel SA)
    foloseste /api/ddf/sa/patch.
    """
    # Campuri blocate - nu pot fi modificate prin acest endpoint
    BLOCKED: set = {
        'IDDF', 'DC', 'db_name',
    }
    # Campuri permise explicit
    ALLOWED: set = {
        'CodAngajament', 'Cual', 'PartAng',
        'DataCreare', 'DataDef', 'ObiectDDF', 'Program',
        'Comp', 'Stare', 'Salarii',
        'Incarcat', 'Preluat', 'CodFiscal'
    }

    data = request.get_json(silent=True)
    if not data:
        return jsonify({'error': 'Body JSON lipsa'}), 400

    iddf = data.get('IDDF')
    if not iddf:
        return jsonify({'error': 'IDDF este obligatoriu'}), 400

    # Extrage doar campurile permise din payload
    fields = {k: v for k, v in data.items() if k in ALLOWED}
    if not fields:
        return jsonify({'error': 'Niciun camp valid de actualizat'}), 400

    set_clause = ', '.join(f"{col} = %s" for col in fields)
    values     = list(fields.values()) + [iddf]
    _dlog(f"[ddf_patch] set_clause={set_clause} | values={values}")
    _dlog(f"[ddf_patch] IDDF={iddf} fields={list(fields.keys())}")

    conn = None
    cursor = None
    try:
        db_name = data.get("db_name")
        conn   = get_db_connection(db_name)
        cursor = conn.cursor()
        cursor.execute(
            f"UPDATE FX_DDF SET {set_clause} WHERE IDDF = %s",
            values
        )
        if cursor.rowcount == 0:
            return jsonify({'error': f'IDDF={iddf} nu exista'}), 404
        conn.commit()
        _dlog(f"[ddf_patch] updated {cursor.rowcount} row(s) IDDF={iddf}")
        return jsonify({'ok': True, 'IDDF': iddf, 'updated': list(fields.keys())}), 200

    except Exception as e:
        if conn: conn.rollback()
        logger.exception("ddf_patch error IDDF=%s", iddf)
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor: cursor.close()
        if conn:   conn.close()


# ===========================================================================
# PATCH FX_DDF_REV_SA  (endpoint NOU)
# ===========================================================================
@ddf_bp.route('/api/ddf/sa/patch', methods=['POST'])
@require_api_key
def sa_patch():
    """
    Actualizeaza DOAR campurile trimise pentru un rand FX_DDF_REV_SA.
    Fara staging, fara confirm. Cheie: IdSecA.
    Payload: {db_name, IdSecA, <orice camp non-critic>}

    Aici traieste acum SS / IdUnitate (mutate de pe antet pe linia SA).
    """
    BLOCKED: set = {
        'IdSecA', 'IDDF', 'IDREV', 'db_name',
    }
    ALLOWED: set = {
        'IdPartener', 'CodPartener', 'IdUnitate', 'SS',
        'IdClsf', 'IdClsfAcc', 'Clsf',
        'ElementFund', 'ParametriiFund',
        'ValPrec', 'ValCur', 'ValTot',
        'PartInd', 'CodAngajament', 'CodIndicator', 'Ramane',
    }

    data = request.get_json(silent=True)
    if not data:
        return jsonify({'error': 'Body JSON lipsa'}), 400

    id_sec_a = data.get('IdSecA')
    if not id_sec_a:
        return jsonify({'error': 'IdSecA este obligatoriu'}), 400

    fields = {k: v for k, v in data.items() if k in ALLOWED}
    if not fields:
        return jsonify({'error': 'Niciun camp valid de actualizat'}), 400

    set_clause = ', '.join(f"{col} = %s" for col in fields)
    values     = list(fields.values()) + [id_sec_a]
    _dlog(f"[sa_patch] IdSecA={id_sec_a} fields={list(fields.keys())}")

    conn = None
    cursor = None
    try:
        db_name = data.get("db_name")
        conn   = get_db_connection(db_name)
        cursor = conn.cursor()
        cursor.execute(
            f"UPDATE FX_DDF_REV_SA SET {set_clause} WHERE IdSecA = %s",
            values
        )
        if cursor.rowcount == 0:
            return jsonify({'error': f'IdSecA={id_sec_a} nu exista'}), 404
        conn.commit()
        _dlog(f"[sa_patch] updated {cursor.rowcount} row(s) IdSecA={id_sec_a}")
        return jsonify({'ok': True, 'IdSecA': id_sec_a, 'updated': list(fields.keys())}), 200

    except Exception as e:
        if conn: conn.rollback()
        logger.exception("sa_patch error IdSecA=%s", id_sec_a)
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor: cursor.close()
        if conn:   conn.close()


# ===========================================================================
# CLEANUP STAGING
# ===========================================================================
@ddf_bp.route('/api/ddf/cleanup_staging', methods=['POST'])
@require_api_key
def cleanup_staging():
    """
    Sterge toate inregistrarile PENDING din stg_* mai vechi de 5 minute.
    ON DELETE CASCADE curata automat stg_Revizii, stg_RevA, stg_RevB, stg_Att.
    Apelat din VBA inainte de orice salvare.
    """
    data = request.get_json(silent=True) or {}
    db_name = data.get("db_name")

    conn = None
    cursor = None
    try:
        conn   = get_db_connection(db_name)
        cursor = conn.cursor()
        cursor.execute("""
            DELETE FROM stg_DocFund
            WHERE Status = 'PENDING'
              AND DTQ < NOW() - INTERVAL 5 MINUTE
        """)
        deleted = cursor.rowcount
        conn.commit()
        _dlog(f"[cleanup_staging] deleted={deleted}")
        return jsonify({'ok': True, 'deleted': deleted}), 200

    except Exception as e:
        if conn: conn.rollback()
        logger.exception("cleanup_staging error")
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor: cursor.close()
        if conn:   conn.close()


# ===========================================================================
# DELETE DDF / REVIZIE  (folosesc _run_with_retry)
# ===========================================================================
# ===========================================================================
# DELETE DDF / REVIZIE  (folosesc _run_with_retry)
# ===========================================================================
@ddf_bp.route("/api/ddf/delete", methods=["POST"])
@require_api_key
def delete_ddf():
    """
    Sterge un DDF complet din DB.
    Cascade FK MariaDB sterge automat: REV → SA / SB / ATT / PRT
    Payload: {db_name, IDDF}
    """
    data = request.json

    try:
        iddf = _strict_pos_int(data.get("IDDF"), "IDDF")

        def operation(cursor):
            cursor.execute(
                "SELECT IDDF FROM FX_DDF WHERE IDDF=%s FOR UPDATE",
                (iddf,)
            )
            if not cursor.fetchone():
                raise NotFoundError(f"FX_DDF cu IDDF={iddf} nu exista")

            cursor.execute("DELETE FROM FX_DDF WHERE IDDF=%s", (iddf,))
            if cursor.rowcount == 0:
                raise ValueError(f"DELETE FX_DDF IDDF={iddf}: 0 randuri afectate")

            return {"ok": True, "IDDF": iddf}

        result = _run_with_retry(operation, data)
        logger.info(f"[delete_ddf] OK IDDF={iddf}")
        return jsonify(result), 200

    except NotFoundError as e:
        return jsonify({"error": str(e)}), 404

    except Exception as e:
        logger.error(f"[delete_ddf] ERROR: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@ddf_bp.route("/api/ddf/rev/delete", methods=["POST"])
@require_api_key
def delete_ddf_rev():
    """
    Sterge DOAR o revizie DDF.
    Cascade FK MariaDB sterge automat: SA / SB / ATT / PRT
    FX_DDF ramane intact.
    Payload: {db_name, IDREV}
    """
    data = request.json

    try:
        idrev = _strict_pos_int(data.get("IDREV"), "IDREV")

        def operation(cursor):
            cursor.execute("""
                SELECT IDREV, IDDF
                FROM FX_DDF_REV
                WHERE IDREV=%s
                FOR UPDATE
            """, (idrev,))
            row = cursor.fetchone()
            if not row:
                raise ValueError(f"FX_DDF_REV cu IDREV={idrev} nu exista")

            iddf = row["IDDF"]

            cursor.execute("DELETE FROM FX_DDF_REV WHERE IDREV=%s", (idrev,))
            if cursor.rowcount == 0:
                raise ValueError(f"DELETE FX_DDF_REV IDREV={idrev}: 0 randuri afectate")

            return {"ok": True, "IDDF": iddf, "IDREV": idrev}

        result = _run_with_retry(operation, data)
        logger.info(f"[delete_ddf_rev] OK IDREV={idrev}")
        return jsonify(result), 200

    except ValueError as e:
        return jsonify({"error": str(e)}), 404

    except Exception as e:
        logger.error(f"[delete_ddf_rev] ERROR: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@ddf_bp.route('/api/ddf/update_cod_fiscal', methods=['POST'])
@require_api_key
def update_cod_fiscal():

    data = request.get_json(silent=True)
    if not data:
        return jsonify({'ok': False}), 400

    items = data.get("items", [])

    conn = None
    cursor = None

    try:
        conn = get_db_connection(data.get("db_name"))
        cursor = conn.cursor()

        updated = 0

        for item in items:
            cursor.execute("""
                UPDATE FX_DDF
                SET CodFiscal = %s,
                    NumePartener = %s
                WHERE IDDF = %s
                  AND CodFiscal IS NULL
            """, (
                item["CodFiscal"],
                item["NumePartener"],
                item["IDDF"]
            ))

            updated += cursor.rowcount

        conn.commit()

        return jsonify({
            "ok": True,
            "updated": updated,
            "total": len(items)
        }), 200

    except Exception as e:
        if conn:
            conn.rollback()

        logger.exception(f"update_cod_fiscal exception={e}")

        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


@ddf_bp.route('/api/ddf/update_semnatura', methods=['POST'])
@require_api_key
def update_semnatura():

    data = request.get_json(silent=True)
    if not data:
        return jsonify({'ok': False}), 400

    items = data.get("items", [])

    conn = None
    cursor = None

    try:
        conn = get_db_connection(data.get("db_name"))
        cursor = conn.cursor()

        updated = 0

        for item in items:
            cursor.execute(""" 
                UPDATE FX_DDF_REV SET Semnatura = %s WHERE IDREV = %s
            """, (
                item["Semnatura"],
                item["IDREV"]
            ))

            updated += cursor.rowcount

        conn.commit()

        return jsonify({
            "ok": True,
            "updated": updated,
            "total": len(items)
        }), 200

    except Exception as e:
        if conn:
            conn.rollback()

        logger.exception(f"update_semnatura exception={e}")

        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()