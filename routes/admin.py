
from __future__ import annotations

import os
import logging
import re

from flask import Blueprint, request, jsonify, send_file, current_app
import json
from mysql.connector import errorcode
from utils.security import require_admin
from utils.database import get_db_connection
from config import FILE_BAZA_PATH, FILE_EF_PATH 
from decimal import Decimal
from typing import Any

admin_bp = Blueprint('admin', __name__)
logger = logging.getLogger(__name__)

# Baza de date ETALON de unde copiem structura
SOURCE_DB = 'AVACONT_SURSA'
COMMON_DB = "AVACONT_COMUN"  # Baza comuna (care nu trebuie modificata in referinte)
DB_NAME_REGEX = re.compile(r'^[A-Za-z0-9_]+$')

from utils.dbname import validate_db_name as _validate_db_name  # moved to utils (security plan S2)

@admin_bp.route('/api/admin/login/reset', methods=['POST'])
@require_admin
def login_reset():
    """
    Clears the failed-login counters (and any lockout) for one operator.
    Body: { "username": "<email>" }. The name is normalised exactly like
    /api/auth/login (strip + lower) so it hits the same key.
    Counters are in-process (routes/auth/ratelimit.py), so this only works
    against the running server -- a script on the box cannot reach them.
    """
    from routes.auth.ratelimit import LIMITER

    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip().lower()
    if not username:
        return jsonify({"error": "Lipsește utilizatorul."}), 400

    removed = LIMITER.reset_user(username)
    logger.info("LOGIN_RESET un=%s buckets=%s ip=%s", username, removed, request.remote_addr)
    return jsonify({"ok": True, "username": username, "cleared": removed}), 200


@admin_bp.route('/api/admin/users', methods=['GET'])
@require_admin
def users_list():
    """
    Every operator row (Unitati_Utilizatori JOIN Unitati, K-BOT server), one row
    per (UN, DC), plus the live failed-login state of that UN from the running
    limiter: fails = recent failures, blocked = seconds of lockout left (0 = free).
    Used by the AvacontPush admin tab.
    """
    from routes.auth.ratelimit import LIMITER
    from utils.database import get_kbot_comun_connection

    conn = None
    try:
        conn = get_kbot_comun_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT ua.UN AS un, ua.DC AS dc, u.NumeUnitate AS nume_unitate,
                   ua.Rol AS rol, ua.LastSS AS last_ss
            FROM Unitati_Utilizatori AS ua
            LEFT JOIN Unitati AS u ON u.DC = ua.DC
            ORDER BY ua.UN, ua.DC
            """
        )
        rows = cursor.fetchall()
    except Exception as e:
        logger.error("users_list DB error: %s", e, exc_info=True)
        return current_app.response_class(
            json.dumps({"error": f"Eroare la citirea utilizatorilor: {e}"}, ensure_ascii=False),
            status=500, mimetype="application/json")
    finally:
        if conn is not None and conn.is_connected():
            conn.close()

    states = LIMITER.user_states()
    users = []
    for r in rows:
        un = str(r.get("un") or "")
        st = states.get(un.strip().lower(), {"fails": 0, "blocked": 0})
        users.append({
            "un": un,
            "dc": r.get("dc") or "",
            "nume_unitate": r.get("nume_unitate") or "",
            "rol": r.get("rol") or "",
            "last_ss": r.get("last_ss") or "",
            "fails": st["fails"],
            "blocked": st["blocked"],
        })
    return current_app.response_class(
        json.dumps({"users": users}, ensure_ascii=False), status=200,
        mimetype="application/json")


@admin_bp.route('/api/admin/receptii/refacere', methods=['POST'])
@require_admin
def receptii_refacere_admin():
    """
    Intretinere (felia 0062): reface instantaneele / liniile de receptie lipsa din istoric,
    FARA sesiune de operator -- cheia de administrare + baza numita in corp.

    Corp: { db_name, aplica?, cod? | toate?: true }. Acelasi contract si acelasi raspuns ca
    POST /api/forexe/receptii/refacere (vezi routes/forexe/receptii_refacere.py).

    ATENTIE: tabelele FX_ traiesc pe serverul K-BOT (get_kbot_connection), nu pe cel legacy
    pe care lucreaza celelalte rute de aici -- de aceea nu trece prin get_db_connection.
    """
    # Import local, nu la nivel de modul: routes.forexe.angajamente importa de aici
    # `_validate_db_name`, deci un import la varf ar inchide un cerc la pornire.
    from routes.forexe.receptii_refacere import (
        CerereInvalida, citeste_cererea, executa_refacerea)

    data = request.get_json(silent=True) or {}
    try:
        db_name = _validate_db_name(data.get('db_name'))
    except ValueError:
        return jsonify({"error": "db_name invalid"}), 400
    try:
        cod, toate, aplica = citeste_cererea(data)
    except CerereInvalida as e:
        return current_app.response_class(
            json.dumps({"error": str(e)}, ensure_ascii=False), status=400,
            mimetype="application/json")

    logger.info("[admin.receptii.refacere] db=%s cod=%s toate=%s aplica=%s ip=%s",
                db_name, cod or "-", toate, aplica, request.remote_addr)
    try:
        payload = executa_refacerea(db_name, cod, toate, aplica)
        payload["db_name"] = db_name
        # Diacritice literale in `avertismente` / `erori`: ensure_ascii=False, ca la forexe.
        return current_app.response_class(
            json.dumps(payload, ensure_ascii=False), status=200, mimetype="application/json")
    except Exception as e:
        logger.error(f"[admin.receptii.refacere] db={db_name}: {e}", exc_info=True)
        return current_app.response_class(
            json.dumps({"error": f"Eroare la refacerea recepțiilor: {e}"}, ensure_ascii=False),
            status=500, mimetype="application/json")

