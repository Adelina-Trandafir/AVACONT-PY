# routes/auth.py
"""
K-BOT login / session endpoints.

Model (option A):
  - Identity is proven by trying to log in to MariaDB AS the operator
    (username = e-mail, lowercase). If that login works, the person is real.
    The password is used ONLY for that check and is then thrown away — it is
    never stored in the session.
  - Which databases a person may open, their role, and their last SS come from
    three tables in AVACONT_COMUN, read through the read-only 'db_reader'
    account (get_comun_reader_connection). Operators never touch AVACONT_COMUN:
      * Unitati               (DC, NumeUnitate, CF)
      * Unitati_Utilizatori   (UN, DC, Rol, LastSS)
      * Unitati_Ani           (DC, AN, SS, CodProgram)
  - A successful login mints an opaque bearer token (a random string that stands
    in for "this person is logged in"), kept in routes.auth.session_store.STORE —
    the SAME registry the data-route guard validates against. The backend is either
    in-memory (single-worker Gunicorn, like _upload_sessions) or Redis (shared,
    multi-worker safe), chosen by SESSION_BACKEND in config.
  - Post-login endpoints authenticate with that token via guard.require_session,
    which exposes the live session as g.session — never with the password.
  - Business events are written to the Jurnal audit table, keyed by the acting
    user (UN). Only auth events are attributable here today; data-action logging
    arrives with token enforcement on the data endpoints.

Endpoints:
  POST /api/auth/units     (pre-auth)  -> databases this user may open (friendly names)
  POST /api/auth/login     (pre-auth)  -> token + identity + last SS
  POST /api/auth/logout    (token)     -> drop the session
  GET  /api/auth/periods   (token)     -> year / SS / CodProgram catalog for a database
  POST /api/auth/last-ss   (token)     -> remember the SS the user just picked
"""
import logging
import os
from datetime import datetime, timezone

import mysql.connector
from flask import Blueprint, request, jsonify, g

from config import DB_CONFIG
from utils.database import get_db_connection, get_comun_reader_connection
from routes.auth.session_store import STORE, REASON_CONTEXT_MISMATCH
from routes.auth.guard import require_session, json_response
from routes.auth.ratelimit import LIMITER

auth_bp = Blueprint("auth", __name__)
logger = logging.getLogger(__name__)

# MySQL/MariaDB error number for "access denied" (wrong user or password).
_ER_ACCESS_DENIED = 1045

# Baza comuna care contine tabelele de login.
COMMON_DB = "AVACONT_COMUN"

# Mesaj (RO, diacritice literale) returnat cu 429 cand limita anti-forta-bruta
# blocheaza IP-ul sau perechea (IP, utilizator). Durata blocarii = _LOCKOUT din
# routes/auth/ratelimit.py (15 min).
_RATE_LIMIT_MSG = "Prea multe încercări eșuate. Reîncercați peste 15 minute."

# NOTA (fix split-brain): sesiunile traiesc ACUM intr-un singur registru,
# routes.auth.session_store.STORE — acelasi pe care il valideaza guard.require_session
# de pe rutele de date. Istoric, auth.py tinea un dict privat `_sessions` + propriul
# decorator, deci un token emis la login era INVIZIBIL rutelor de date (401 pe prima
# cerere autentificata). Un singur STORE = o singura sursa de adevar.


# ---------------------------------------------------------------------------
# Identity check (option A): can this e-mail + password log in to MariaDB?
# ---------------------------------------------------------------------------
def _verify_operator(username: str, password: str) -> bool:
    """
    True if (username, password) is a valid MariaDB login.
    Connects with NO default database (operator accounts are USAGE-only).
    The connection is opened only to prove the password, then closed at once.
    """
    conn = None
    try:
        conn = mysql.connector.connect(
            host=DB_CONFIG["host"],
            port=DB_CONFIG.get("port", 3306),
            user=username,
            password=password,
            connection_timeout=10,
        )
        return True
    except mysql.connector.Error as err:
        if getattr(err, "errno", None) == _ER_ACCESS_DENIED:
            return False        # wrong user / password
        raise                   # server down / network — a real error, surface it
    finally:
        if conn is not None and conn.is_connected():
            conn.close()


# ---------------------------------------------------------------------------
# Audit log (business events). Best-effort: a failed log write must NEVER break
# the user's action — but it is recorded in the app log, not silently dropped.
# Written by the service account (INSERT on Jurnal); db_reader is read-only.
# ---------------------------------------------------------------------------
def _log_action(un, dc, actiune, tinta=None, detalii=None,
                rezultat="OK", masina=None, ip=None):
    conn = None
    try:
        conn = get_db_connection(COMMON_DB)     # DB_CONFIG service account
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO Jurnal (Moment, UN, DC, Actiune, Tinta, Detalii, Rezultat, Masina, IP)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (datetime.now(timezone.utc), un, dc, actiune, tinta, detalii,
             rezultat, masina, ip),
        )
        conn.commit()
    except mysql.connector.Error as err:
        # Do not break the caller; surface the failure in the server log.
        logger.error("audit log write failed (%s / %s): %s", actiune, un, err)
        if conn is not None:
            try:
                conn.rollback()
            except mysql.connector.Error:
                pass
    finally:
        if conn is not None and conn.is_connected():
            conn.close()


# ---------------------------------------------------------------------------
# Token guard for post-login endpoints.
# Rutele autentificate (logout / periods / last-ss) folosesc guard.require_session,
# care valideaza tokenul in STORE si aseaza g.session (obiect Session). Nu mai exista
# un decorator local + dict privat aici (vezi nota split-brain de mai sus).
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# POST /api/auth/units   (pre-auth)
# Body: { "username": "<email>", "password": "<pass>" }
# Returns the databases this user may open, with friendly names.
# ---------------------------------------------------------------------------
@auth_bp.route("/api/auth/units", methods=["POST"])
def auth_units():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip().lower()
    password = data.get("password") or ""

    if not username or not password:
        return jsonify({"error": "Utilizator și parolă obligatorii."}), 400

    # Anti-forta-bruta: blocam INAINTE de a atinge MariaDB (o incercare blocata nu
    # consuma o conexiune de verificare). request.remote_addr = IP-ul real (ProxyFix).
    ip = request.remote_addr
    if LIMITER.is_blocked(ip, username):
        _log_action(username, None, "AUTH_BLOCKED", detalii="units",
                    rezultat="EROARE", ip=ip)
        return jsonify({"error": _RATE_LIMIT_MSG}), 429

    if not _verify_operator(username, password):
        LIMITER.record_failure(ip, username)
        _log_action(username, None, "AUTH_FAIL", detalii="units",
                    rezultat="EROARE", ip=ip)
        return jsonify({"error": "Utilizator sau parolă incorecte."}), 401

    # Parola corecta: stergem contoarele (login reusit reseteaza ambele bucket-uri).
    LIMITER.record_success(ip, username)

    conn = None
    try:
        conn = get_comun_reader_connection()
        cursor = conn.cursor(dictionary=True)   # dictionary=True -> rows as {col: value}
        cursor.execute(
            """
            SELECT ua.DC AS DC, u.NumeUnitate AS NumeUnitate
            FROM Unitati_Utilizatori AS ua
            JOIN Unitati             AS u ON u.DC = ua.DC
            WHERE ua.UN = %s
            ORDER BY u.NumeUnitate
            """,
            (username,),                        # %s is a placeholder — value passed
                                                # separately so it can't be SQL-injected
        )
        units = cursor.fetchall()
        return jsonify({"units": units}), 200
    except mysql.connector.Error as err:
        logger.error("auth_units DB error: %s", err)
        return jsonify({"error": "Eroare la citirea unităților."}), 500
    finally:
        if conn is not None and conn.is_connected():
            conn.close()


# ---------------------------------------------------------------------------
# POST /api/auth/login   (pre-auth)
# Body: { "username", "password", "db_name", "machine" }
# Re-verifies identity, checks the user is granted that database, mints a token.
# ---------------------------------------------------------------------------
@auth_bp.route("/api/auth/login", methods=["POST"])
def auth_login():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip().lower()
    password = data.get("password") or ""
    db_name  = (data.get("db_name") or "").strip()
    machine  = (data.get("machine") or "").strip()

    if not username or not password or not db_name:
        return jsonify({"error": "Date de autentificare incomplete."}), 400

    # Anti-forta-bruta: acelasi LIMITER ca /units (aceeasi cheie IP + (IP,utilizator)),
    # deci esecurile de pe cele doua rute se aduna spre acelasi prag.
    ip = request.remote_addr
    if LIMITER.is_blocked(ip, username):
        _log_action(username, db_name, "AUTH_BLOCKED", detalii="login",
                    rezultat="EROARE", masina=machine, ip=ip)
        return jsonify({"error": _RATE_LIMIT_MSG}), 429

    if not _verify_operator(username, password):
        LIMITER.record_failure(ip, username)
        _log_action(username, db_name, "AUTH_FAIL", detalii="login",
                    rezultat="EROARE", masina=machine, ip=ip)
        return jsonify({"error": "Utilizator sau parolă incorecte."}), 401

    # Parola corecta: stergem contoarele (accesul refuzat pe unitate, 403 de mai jos,
    # NU e esec de credentiale, deci nu conteaza la brute-force).
    LIMITER.record_success(ip, username)

    conn = None
    try:
        conn = get_comun_reader_connection()
        cursor = conn.cursor(dictionary=True)

        # Authorization: is this user actually granted this database?
        cursor.execute(
            "SELECT Rol, LastSS FROM Unitati_Utilizatori WHERE UN = %s AND DC = %s",
            (username, db_name),
        )
        access = cursor.fetchone()
        if access is None:
            _log_action(username, db_name, "ACCESS_DENIED",
                        rezultat="EROARE", masina=machine, ip=request.remote_addr)
            return jsonify({"error": "Nu aveți acces la această unitate."}), 403

        # Unit details (friendly name + CF).
        cursor.execute(
            "SELECT NumeUnitate, CF FROM Unitati WHERE DC = %s",
            (db_name,),
        )
        unit = cursor.fetchone()
        if unit is None:
            # Roster points at a DC that is not registered — a data problem.
            logger.error("login: DC %s in Unitati_Utilizatori but missing from Unitati", db_name)
            return jsonify({"error": "Unitate neconfigurată."}), 500
    except mysql.connector.Error as err:
        logger.error("auth_login DB error: %s", err)
        return jsonify({"error": "Eroare la autentificare."}), 500
    finally:
        if conn is not None and conn.is_connected():
            conn.close()

    # SessionContext returnat clientului = si contextul pastrat pe sesiune (ctx).
    session_context = {
        "DbName": db_name,
        "NumeUnitate": unit["NumeUnitate"],
        "CF": unit["CF"],
        "Role": access["Rol"],
    }
    # Mint in STORE (unica sursa de adevar). SECURITY (R2): NU pastram parola pe
    # sesiune (option A o arunca dupa verificare) — password="".
    token, _sess = STORE.create(
        username=username, password="", id_unitate=0,
        db_name=db_name, ctx=session_context, pcname=machine,
    )

    # Log de succes autoexplicativ (pereche cu logul de 401 din guard): daca PID-ul
    # de la login difera de PID-ul de la un 401 imediat, e semn de multi-worker.
    # SECURITY (R1): doar primele 8 caractere ale tokenului.
    logger.info("AUTH_LOGIN pid=%s token8=%s store_size=%s un=%s dc=%s",
                os.getpid(), token[:8], STORE.size(), username, db_name)

    _log_action(username, db_name, "LOGIN", masina=machine, ip=request.remote_addr)

    return jsonify({
        "Token": token,
        "SessionContext": session_context,
        "LastSS": access["LastSS"],             # runtime hint for the main form (may be null)
    }), 200


# ---------------------------------------------------------------------------
# POST /api/auth/logout   (token)
# ---------------------------------------------------------------------------
@auth_bp.route("/api/auth/logout", methods=["POST"])
@require_session
def auth_logout():
    s = g.session
    STORE.revoke(g.session_token)
    _log_action(s.username, s.db_name, "LOGOUT",
                masina=s.pcname, ip=request.remote_addr)
    return jsonify({"ok": True}), 200


# ---------------------------------------------------------------------------
# GET /api/auth/periods?db_name=<DC>   (token)
# Year / SS / CodProgram catalog for the main-form combos.
# ---------------------------------------------------------------------------
@auth_bp.route("/api/auth/periods", methods=["GET"])
@require_session
def auth_periods():
    db_name = (request.args.get("db_name") or "").strip()
    if not db_name:
        return jsonify({"error": "Lipsește 'db_name'."}), 400

    # A user may only read periods for the database they are logged into. Corp cu
    # cod-motiv (CONTEXT_MISMATCH) + diacritice literale, ca restul stratului auth.
    if db_name != g.session.db_name:
        return json_response(
            {"error": "Acces interzis pentru această unitate.",
             "reason": REASON_CONTEXT_MISMATCH}, 403)

    conn = None
    try:
        conn = get_comun_reader_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT AN, SS, CodProgram
            FROM Unitati_Ani
            WHERE DC = %s
            ORDER BY AN DESC, SS
            """,
            (db_name,),
        )
        periods = cursor.fetchall()
        return jsonify({"periods": periods}), 200
    except mysql.connector.Error as err:
        logger.error("auth_periods DB error: %s", err)
        return jsonify({"error": "Eroare la citirea perioadelor."}), 500
    finally:
        if conn is not None and conn.is_connected():
            conn.close()


# ---------------------------------------------------------------------------
# POST /api/auth/last-ss   (token)
# Body: { "ss": "<SS>" }
# Remembers the SS for THIS user on THIS database. The (user, database) come
# from the token's session, never from the client, so a user can't write another
# user's row. Written by the API service account (db_reader is read-only).
# ---------------------------------------------------------------------------
@auth_bp.route("/api/auth/last-ss", methods=["POST"])
@require_session
def auth_last_ss():
    data = request.get_json(silent=True) or {}
    ss = (data.get("ss") or "").strip()
    if not ss:
        return jsonify({"error": "Lipsește 'ss'."}), 400

    un = g.session.username
    dc = g.session.db_name

    # Validate SS is a real SS for this database (read-only reader).
    rconn = None
    try:
        rconn = get_comun_reader_connection()
        rcur = rconn.cursor()
        rcur.execute(
            "SELECT 1 FROM Unitati_Ani WHERE DC = %s AND SS = %s LIMIT 1",
            (dc, ss),
        )
        known_ss = rcur.fetchone() is not None
    except mysql.connector.Error as err:
        logger.error("auth_last_ss validate error: %s", err)
        return jsonify({"error": "Eroare la validarea SS."}), 500
    finally:
        if rconn is not None and rconn.is_connected():
            rconn.close()

    if not known_ss:
        return jsonify({"error": "SS necunoscut pentru această unitate."}), 400

    # Write via the service account (has UPDATE on Unitati_Utilizatori).
    wconn = None
    try:
        wconn = get_db_connection(COMMON_DB)    # DB_CONFIG service account
        wcur = wconn.cursor()
        wcur.execute(
            "UPDATE Unitati_Utilizatori SET LastSS = %s WHERE UN = %s AND DC = %s",
            (ss, un, dc),
        )
        wconn.commit()
        _log_action(un, dc, "SS_CHANGE", tinta=ss,
                    masina=g.session.pcname, ip=request.remote_addr)
        return jsonify({"ok": True}), 200
    except mysql.connector.Error as err:
        if wconn is not None:
            wconn.rollback()
        logger.error("auth_last_ss write error: %s", err)
        return jsonify({"error": "Eroare la salvarea SS."}), 500
    finally:
        if wconn is not None and wconn.is_connected():
            wconn.close()