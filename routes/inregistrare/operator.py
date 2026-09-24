# routes/inregistrare/operator.py
"""
The operator's approval page (slice 0075-05, plan 7, decisions D10 and D25).

    GET  /operator                                  the page
    POST /api/operator/login        {email, parola} -> password checked, code mailed
    POST /api/operator/verifica     {pending, cod}  -> the operator's session token
    POST /api/operator/logout
    GET  /api/operator/cereri?stare=                -> the list, newest first
    GET  /api/operator/cereri/<id>                  -> one request, with the live plan
    POST /api/operator/cereri/<id>/verifica   {cod_program, db_name} -> the plan again
    POST /api/operator/cereri/<id>/aproba     {cod_program, db_name} -> starts the job
    GET  /api/operator/joburi/<job>?de_la=N         -> the job's progress lines
    POST /api/operator/cereri/<id>/respinge   {motiv}
    POST /api/operator/cereri/<id>/link-nou

WHO GETS IN (operator, 23.09.2026). Two factors, like the password change of slice
0072: the K-BOT password, proven by a MariaDB login AS the operator (auth.py), then a
six-digit code mailed to that address. And the address must be in `OPERATORI` in
config.py -- a K-BOT user is not an operator just because their password is right.
An empty or missing list keeps the page shut.

WHY NOT THE K-BOT BEARER SESSION. That session is tied to a unit database
(`db_name`) and every data route trusts it for that unit; an operator session has no
unit. So the operator's session is a note in the same STORE, under its own name,
carried in its own header (`X-Operator-Token`). A K-BOT token is not an operator
token and an operator token is not a K-BOT token -- neither can be replayed on the
other side.

THE APPROVAL RUNS IN A THREAD of this process and the page polls it (plan 5.6: "job +
poll"). The job registry is in memory, which is safe only because gunicorn runs one
worker (gunicorn.conf.py) -- the same constraint as the session store and the rate
limiter. A restart of gunicorn DURING an approval kills the job without its undo:
don't restart while the page says a job is running.

The name of the database and the CodProgram values are the operator's to change
before approving (D25 and the operator, 23.09.2026). What the operator saw on screen
is what is sent back, so the job builds exactly the name that was confirmed -- never a
name recomputed behind the operator's back.
"""
import hashlib
import hmac
import json
import logging
import secrets
import threading
import time

import mysql.connector
from flask import Blueprint, current_app, request, send_from_directory

import config
from routes.auth import mailer
from routes.auth.auth import log_action, verify_operator
from routes.auth.ratelimit import LIMITER
from routes.auth.session_store import STORE
from utils.database import get_kbot_comun_connection, get_kbot_provisioning_connection

from . import cerere as cerere_mod
from . import nomenclatoare
from . import provizionare
from .inregistrare import _PAGE_HEADERS

logger = logging.getLogger(__name__)

operator_bp = Blueprint("operator", __name__)

TOKEN_HEADER = "X-Operator-Token"

# Session notes in routes.auth.session_store.STORE.
_NOTE_PENDING = "operator_pending"      # password right, code not typed yet
_NOTE_SESSION = "operator_session"      # both factors passed

CODE_TTL = 10 * 60
CODE_MAX_ATTEMPTS = 5
SESSION_IDLE = 30 * 60                  # sliding
SESSION_MAX = 4 * 60 * 60               # absolute, from the code

MOTIV_MIN = 5
MOTIV_MAX = 2000
LIST_LIMIT = 500

# Finished jobs are kept this long for a page that reloads; then dropped.
_JOB_KEEP = 60 * 60

_STARI = (
    cerere_mod.STARE_IN_ASTEPTARE,
    cerere_mod.STARE_ESUATA,
    cerere_mod.STARE_APROBATA,
    cerere_mod.STARE_RESPINSA,
)
_APPROVABLE = (cerere_mod.STARE_IN_ASTEPTARE, cerere_mod.STARE_ESUATA)

_MSG_RATE_LIMITED = "Prea multe încercări eșuate. Reîncercați peste 15 minute."
_MSG_SESSION = "Sesiunea a expirat. Autentificați-vă din nou."


def _json(payload, status=200):
    """JSON with LITERAL diacritics; dates as text."""
    body = json.dumps(payload, ensure_ascii=False, default=_date_text)
    return current_app.response_class(body, status=status, mimetype="application/json")


def _fail(reason, message, status):
    return _json({"error": message, "reason": reason}, status)


def _date_text(value):
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d %H:%M")
    raise TypeError(f"not serializable: {type(value).__name__}")


def _operators() -> set:
    """`OPERATORI` from config.py, lowercased. Read on every call: a line removed from
    config.py and a restart take an operator out, sessions included."""
    raw = getattr(config, "OPERATORI", None) or ()
    if isinstance(raw, str):
        raw = [raw]
    return {str(item).strip().lower() for item in raw if str(item).strip()}


# ---------------------------------------------------------------------------
# Sign-in: password, then the mailed code
# ---------------------------------------------------------------------------
@operator_bp.route("/api/operator/login", methods=["POST"])
def operator_login():
    body = request.get_json(silent=True) or {}
    email = (body.get("email") or "").strip().lower()
    parola = body.get("parola") or ""
    if not email or not parola:
        return _fail("DATE_INCOMPLETE", "Introduceți adresa de e-mail și parola.", 400)

    ip = request.remote_addr
    if LIMITER.is_blocked(ip, email):
        return _fail("RATE_LIMITED", _MSG_RATE_LIMITED, 429)
    if not _operators():
        return _fail("OPERATORI_NECONFIGURATI",
                     "Lista operatorilor (OPERATORI) lipsește din configurația serverului.", 503)
    if not mailer.is_configured():
        return _fail("MAIL_NOT_CONFIGURED",
                     "Trimiterea e-mailului nu este configurată pe server.", 503)

    try:
        right = verify_operator(email, parola)
    except mysql.connector.Error as err:
        logger.error("operator login: password check failed for %s: %s",
                     mailer.mask_address(email), err)
        return _fail("DB_ERROR", "Parola nu a putut fi verificată. Reîncercați.", 500)
    if not right:
        LIMITER.record_failure(ip, email)
        log_action(email, None, "OPERATOR_AUTH_FAIL", rezultat="EROARE", ip=ip)
        return _fail("CREDENTIALE", "Utilizator sau parolă incorecte.", 401)
    if email not in _operators():
        # The password was right, so this is a K-BOT user who is not an operator. Still
        # counted as a failure: nobody should get to try operator addresses for free.
        LIMITER.record_failure(ip, email)
        log_action(email, None, "OPERATOR_DENIED", rezultat="EROARE", ip=ip)
        return _fail("NU_ESTE_OPERATOR", "Contul nu are acces la pagina de cereri.", 403)

    code = f"{secrets.randbelow(1_000_000):06d}"
    try:
        mailer.send_operator_code(email, code, CODE_TTL // 60)
    except Exception as err:            # smtplib, socket: nothing arrived, say so
        logger.error("operator code mail to %s failed: %s", mailer.mask_address(email), err)
        return _fail("MAIL_FAILED", "E-mailul cu codul nu a putut fi trimis. Reîncercați.", 502)

    LIMITER.record_success(ip, email)
    pending = secrets.token_urlsafe(32)
    STORE.put_note(pending, _NOTE_PENDING,
                   {"email": email, "hash": _hash(code), "attempts": 0,
                    "issued_at": time.time()},
                   CODE_TTL)
    return _json({"pending": pending, "email_masked": mailer.mask_address(email),
                  "expires_in": CODE_TTL})


@operator_bp.route("/api/operator/verifica", methods=["POST"])
def operator_verifica():
    body = request.get_json(silent=True) or {}
    pending = (body.get("pending") or "").strip()
    code = (body.get("cod") or "").strip()
    note = STORE.get_note(pending, _NOTE_PENDING) if pending else None
    if note is None:
        return _fail("COD_EXPIRAT", "Codul a expirat. Autentificați-vă din nou.", 401)
    if not code:
        return _fail("COD_ABSENT", "Introduceți codul primit pe e-mail.", 400)

    email = note["email"]
    ip = request.remote_addr
    if LIMITER.is_blocked(ip, email):
        return _fail("RATE_LIMITED", _MSG_RATE_LIMITED, 429)

    if not hmac.compare_digest(note.get("hash", ""), _hash(code)):
        LIMITER.record_failure(ip, email)
        attempts = int(note.get("attempts", 0)) + 1
        if attempts >= CODE_MAX_ATTEMPTS:
            STORE.delete_note(pending, _NOTE_PENDING)
            log_action(email, None, "OPERATOR_AUTH_FAIL", detalii="code attempts exhausted",
                       rezultat="EROARE", ip=ip)
            return _fail("COD_EPUIZAT", "Prea multe coduri greșite. Autentificați-vă din nou.", 401)
        note["attempts"] = attempts
        left = CODE_TTL - int(time.time() - float(note.get("issued_at", time.time())))
        STORE.put_note(pending, _NOTE_PENDING, note, max(1, left))
        return _fail("COD_GRESIT", "Codul este greșit.", 400)

    STORE.delete_note(pending, _NOTE_PENDING)
    if email not in _operators():
        return _fail("NU_ESTE_OPERATOR", "Contul nu are acces la pagina de cereri.", 403)

    LIMITER.record_success(ip, email)
    token = secrets.token_urlsafe(32)
    STORE.put_note(token, _NOTE_SESSION, {"email": email, "issued_at": time.time()},
                   SESSION_IDLE)
    log_action(email, None, "OPERATOR_LOGIN", ip=ip)
    return _json({"token": token, "email": email, "expires_in": SESSION_IDLE})


@operator_bp.route("/api/operator/logout", methods=["POST"])
def operator_logout():
    token = (request.headers.get(TOKEN_HEADER) or "").strip()
    if token:
        STORE.delete_note(token, _NOTE_SESSION)
    return _json({"ok": True})


def _session():
    """`(email, None)` for a live operator session, else `(None, <401/403>)`.
    Slides the idle window, never past the absolute cap."""
    token = (request.headers.get(TOKEN_HEADER) or "").strip()
    note = STORE.get_note(token, _NOTE_SESSION) if token else None
    if note is None:
        return None, _fail("SESIUNE_EXPIRATA", _MSG_SESSION, 401)
    now = time.time()
    issued = float(note.get("issued_at", 0))
    if now >= issued + SESSION_MAX:
        STORE.delete_note(token, _NOTE_SESSION)
        return None, _fail("SESIUNE_EXPIRATA", _MSG_SESSION, 401)
    email = note.get("email") or ""
    if email not in _operators():
        STORE.delete_note(token, _NOTE_SESSION)
        return None, _fail("NU_ESTE_OPERATOR", "Contul nu are acces la pagina de cereri.", 403)
    STORE.put_note(token, _NOTE_SESSION, note,
                   max(1, int(min(SESSION_IDLE, issued + SESSION_MAX - now))))
    return email, None


# ---------------------------------------------------------------------------
# The list and one request
# ---------------------------------------------------------------------------
@operator_bp.route("/api/operator/cereri", methods=["GET"])
def operator_cereri():
    email, refusal = _session()
    if refusal is not None:
        return refusal

    stare = (request.args.get("stare") or cerere_mod.STARE_IN_ASTEPTARE).strip()
    if stare != "toate" and stare not in _STARI:
        return _fail("STARE_INVALIDA", "Filtru de stare necunoscut.", 400)

    conn = None
    try:
        conn = get_kbot_provisioning_connection()
        cur = conn.cursor(dictionary=True, buffered=True)
        sql = ("SELECT IdCerere, DataCerere, Stare, CF, Email, Denumire, An, DbName, "
               "DataDecizie, Decis, Payload FROM FX_Inregistrari")
        params = ()
        if stare != "toate":
            sql += " WHERE Stare = %s"
            params = (stare,)
        cur.execute(sql + f" ORDER BY IdCerere DESC LIMIT {LIST_LIMIT}", params)
        rows = cur.fetchall()
        cur.execute("SELECT Stare, COUNT(*) AS n FROM FX_Inregistrari GROUP BY Stare")
        counts = {r["Stare"]: int(r["n"]) for r in cur.fetchall()}
        conn.rollback()
    except Exception as err:
        logger.error("operator list failed: %s", err)
        return _fail("DB_ERROR", "Lista cererilor nu a putut fi citită.", 500)
    finally:
        if conn is not None and conn.is_connected():
            conn.close()

    out = []
    for r in rows:
        payload = _payload(r.pop("Payload"))
        r["randuri"] = len(payload["ss"]) * len(payload["f"]) * len(payload["e"])
        out.append(r)
    return _json({"cereri": out, "numar": counts, "limita": LIST_LIMIT})


@operator_bp.route("/api/operator/cereri/<int:id_cerere>", methods=["GET"])
def operator_cerere(id_cerere):
    email, refusal = _session()
    if refusal is not None:
        return refusal

    conn = None
    try:
        conn = get_kbot_provisioning_connection()
        cur = conn.cursor(dictionary=True, buffered=True)
        cur.execute(
            "SELECT IdCerere, Email, CF, DenumireAnaf, Denumire, An, Payload, Stare, DbName, "
            "Motiv, DataCerere, DataDecizie, Decis, IpAddress, "
            "ParolaHash IS NOT NULL AS LinkActiv, ParolaExpira, "
            "ParolaExpira > NOW() AS LinkValabil "
            "FROM FX_Inregistrari WHERE IdCerere = %s",
            (id_cerere,),
        )
        row = cur.fetchone()
        conn.rollback()
    except Exception as err:
        logger.error("operator detail %s failed: %s", id_cerere, err)
        return _fail("DB_ERROR", "Cererea nu a putut fi citită.", 500)
    finally:
        if conn is not None and conn.is_connected():
            conn.close()
    if row is None:
        return _fail("CERERE_INEXISTENTA", f"Cererea {id_cerere} nu există.", 404)

    payload = _payload(row.pop("Payload"))
    try:
        captions = _captions()
    except mysql.connector.Error as err:
        logger.error("operator detail %s: nomenclators failed: %s", id_cerere, err)
        return _fail("DB_ERROR", "Nomenclatoarele nu au putut fi citite.", 500)

    row["LinkActiv"] = bool(row["LinkActiv"])
    row["LinkValabil"] = bool(row["LinkValabil"])
    row["ss"] = [{"cod": c, "denumire": captions["ss"].get(c, "")} for c in payload["ss"]]
    row["f"] = [{"cod": c, "denumire": captions["f"].get(c, "")} for c in payload["f"]]
    row["e"] = [{"cod": c, "denumire": captions["e"].get(c, "")} for c in payload["e"]]
    row["randuri"] = len(payload["ss"]) * len(payload["f"]) * len(payload["e"])
    row["aprobabila"] = row["Stare"] in _APPROVABLE
    row["job"] = _running_job_for(id_cerere)
    if row["aprobabila"]:
        row["plan"] = _plan(id_cerere, None, None)
    return _json(row)


@operator_bp.route("/api/operator/cereri/<int:id_cerere>/verifica", methods=["POST"])
def operator_verifica_plan(id_cerere):
    email, refusal = _session()
    if refusal is not None:
        return refusal
    codes, db_name, problem = _edits(request.get_json(silent=True) or {})
    if problem is not None:
        return problem
    return _json(_plan(id_cerere, codes, db_name))


def _plan(id_cerere, codes, db_name) -> dict:
    """provizionare.plan() for the page: its answer plus the lines it said, or the
    reason it refused. Writes nothing."""
    lines = []
    try:
        result = provizionare.plan(id_cerere, codes, progress=lines.append, db_name=db_name)
        return {"ok": True, "rezultat": result, "linii": lines}
    except provizionare.ProvizionareRefuzata as err:
        return {"ok": False, "problema": str(err), "linii": lines}
    except Exception as err:
        logger.exception("operator plan for request %s failed", id_cerere)
        return {"ok": False, "problema": f"Verificarea nu a putut fi făcută: {err}",
                "linii": lines}


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------
@operator_bp.route("/api/operator/cereri/<int:id_cerere>/aproba", methods=["POST"])
def operator_aproba(id_cerere):
    email, refusal = _session()
    if refusal is not None:
        return refusal
    codes, db_name, problem = _edits(request.get_json(silent=True) or {})
    if problem is not None:
        return problem
    if not db_name:
        # The page always sends the name the operator confirmed; see the note on top.
        return _fail("NUME_ABSENT", "Lipsește numele bazei.", 400)

    job, running = _start_job(id_cerere, email)
    if running:
        return _fail("JOB_IN_CURS", f"Cererea {id_cerere} se aprobă chiar acum.", 409)

    log_action(email, db_name, "CERERE_APROBARE_START", tinta=str(id_cerere),
               ip=request.remote_addr)
    thread = threading.Thread(
        target=_run_job, args=(job, id_cerere, email, codes, db_name),
        name=f"aprobare-{id_cerere}", daemon=True,
    )
    thread.start()
    return _json({"job": job["id"]}, 202)


@operator_bp.route("/api/operator/joburi/<job_id>", methods=["GET"])
def operator_job(job_id):
    email, refusal = _session()
    if refusal is not None:
        return refusal
    try:
        start = max(0, int(request.args.get("de_la") or 0))
    except ValueError:
        return _fail("DE_LA_INVALID", "Parametrul de_la trebuie să fie un număr.", 400)
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            return _fail("JOB_NECUNOSCUT",
                         "Aprobarea nu mai este urmărită (serverul a repornit sau a trecut "
                         "prea mult timp). Deschideți cererea din nou pentru starea ei.", 404)
        snapshot = {
            "linii": job["linii"][start:],
            "total": len(job["linii"]),
            "gata": job["gata"],
            "ok": job["ok"],
            "rezultat": job["rezultat"],
            "eroare": job["eroare"],
            "ramas": job["ramas"],
            "id_cerere": job["id_cerere"],
        }
    return _json(snapshot)


@operator_bp.route("/api/operator/cereri/<int:id_cerere>/respinge", methods=["POST"])
def operator_respinge(id_cerere):
    email, refusal = _session()
    if refusal is not None:
        return refusal
    body = request.get_json(silent=True) or {}
    motiv = " ".join(str(body.get("motiv") or "").split())
    if len(motiv) < MOTIV_MIN:
        return _fail("MOTIV_ABSENT", "Scrieți motivul respingerii (îl primește solicitantul).", 400)
    if len(motiv) > MOTIV_MAX:
        return _fail("MOTIV_PREA_LUNG", f"Motivul poate avea cel mult {MOTIV_MAX} caractere.", 400)
    if _running_job_for(id_cerere):
        return _fail("JOB_IN_CURS", f"Cererea {id_cerere} se aprobă chiar acum.", 409)

    try:
        row = provizionare.reject(id_cerere, f"web:{email}", motiv)
    except provizionare.ProvizionareRefuzata as err:
        return _fail("RESPINGERE_REFUZATA", str(err), 409)
    except Exception as err:
        logger.exception("rejecting request %s failed", id_cerere)
        return _fail("DB_ERROR", f"Cererea nu a putut fi respinsă: {err}", 500)

    log_action(email, None, "CERERE_RESPINSA", tinta=str(id_cerere), detalii=motiv[:500],
               ip=request.remote_addr)
    # Recorded either way; a mail that does not leave is reported, not a failure.
    try:
        mailer.send_registration_rejected(row["email"], row["denumire"], motiv)
        anuntat = True
    except Exception as err:
        logger.error("rejection mail for request %s failed: %s", id_cerere, err)
        anuntat = False
    return _json({"ok": True, "anuntat": anuntat, "email": row["email"]})


@operator_bp.route("/api/operator/cereri/<int:id_cerere>/link-nou", methods=["POST"])
def operator_link_nou(id_cerere):
    email, refusal = _session()
    if refusal is not None:
        return refusal
    lines = []
    try:
        result = provizionare.link_nou(id_cerere, progress=lines.append)
    except provizionare.ProvizionareRefuzata as err:
        return _fail("LINK_REFUZAT", str(err), 409)
    except Exception as err:
        logger.exception("new password link for request %s failed", id_cerere)
        return _fail("DB_ERROR", f"Linkul nu a putut fi generat: {err}", 500)
    log_action(email, None, "CERERE_LINK_NOU", tinta=str(id_cerere),
               rezultat="OK" if result["link_trimis"] else "EROARE", ip=request.remote_addr)
    result["linii"] = lines
    return _json(result)


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------
@operator_bp.route("/operator", methods=["GET"])
@operator_bp.route("/operator/", methods=["GET"])
def operator_page():
    response = send_from_directory(current_app.static_folder, "operator.html")
    response.headers.update(_PAGE_HEADERS)
    return response


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------
_JOBS = {}
_JOBS_LOCK = threading.Lock()


def _start_job(id_cerere, email):
    """A new job, or `(existing, True)` when this request already has one running."""
    now = time.time()
    with _JOBS_LOCK:
        for job_id in [k for k, j in _JOBS.items()
                       if j["gata"] and now - j["terminat"] > _JOB_KEEP]:
            del _JOBS[job_id]
        for job in _JOBS.values():
            if job["id_cerere"] == id_cerere and not job["gata"]:
                return job, True
        job = {
            "id": secrets.token_urlsafe(12),
            "id_cerere": id_cerere,
            "operator": email,
            "linii": [],
            "gata": False,
            "ok": False,
            "rezultat": None,
            "eroare": None,
            "ramas": [],
            "pornit": now,
            "terminat": 0.0,
        }
        _JOBS[job["id"]] = job
        return job, False


def _running_job_for(id_cerere):
    with _JOBS_LOCK:
        for job in _JOBS.values():
            if job["id_cerere"] == id_cerere and not job["gata"]:
                return job["id"]
    return None


def _run_job(job, id_cerere, email, codes, db_name):
    def say(line):
        with _JOBS_LOCK:
            job["linii"].append(str(line))

    ok, result, error, left = False, None, None, []
    try:
        result = provizionare.approve(id_cerere, f"web:{email}", codes, progress=say,
                                      db_name=db_name)
        ok = True
    except provizionare.ProvizionareRefuzata as err:
        error = str(err)
        say(f"REFUZAT: {error}")
    except provizionare.ProvizionareEsuata as err:
        error, left = err.message, list(err.ramas)
        say(f"EȘUAT: {error}")
    except Exception as err:
        logger.exception("approval job for request %s crashed", id_cerere)
        error = f"Eroare neașteptată: {err}"
        say(error)

    with _JOBS_LOCK:
        job.update(gata=True, ok=ok, rezultat=result, eroare=error, ramas=left,
                   terminat=time.time())
    log_action(email, db_name, "CERERE_APROBATA" if ok else "CERERE_APROBARE_ESUATA",
               tinta=str(id_cerere), detalii=None if ok else (error or "")[:500],
               rezultat="OK" if ok else "EROARE")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _edits(body):
    """The operator's two kinds of edit, shape-checked: `(codes, db_name, None)` or
    `(None, None, <400>)`. Their meaning is checked by provizionare itself."""
    raw_codes = body.get("cod_program")
    if raw_codes is None:
        raw_codes = {}
    if not isinstance(raw_codes, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in raw_codes.items()):
        return None, None, _fail("COD_PROGRAM_INVALID",
                                 "CodProgram trebuie trimis ca listă sursă-sector → valoare.", 400)
    db_name = body.get("db_name")
    if db_name is not None and not isinstance(db_name, str):
        return None, None, _fail("NUME_INVALID", "Numele bazei trebuie să fie text.", 400)
    codes = {k.strip().upper(): v.strip() for k, v in raw_codes.items()}
    return codes, (db_name or "").strip().upper() or None, None


def _payload(text) -> dict:
    """`{"ss", "f", "e"}` from the stored JSON. A broken payload shows as empty lists
    here; approving it is refused by provizionare with the reason."""
    try:
        data = json.loads(text or "{}")
    except ValueError:
        logger.warning("FX_Inregistrari row with a Payload that is not JSON")
        data = {}
    return {key: [str(v) for v in data.get(key) or []] for key in ("ss", "f", "e")}


def _captions() -> dict:
    """Names for the codes, from the same reads the public page uses."""
    conn = get_kbot_comun_connection()
    try:
        return {
            "ss": {r["cod"]: r["denumire"] for r in nomenclatoare.read_sursasector(conn)},
            "f": {r["cod"]: r["denumire"] for r in nomenclatoare.read_clasificatii(conn, "F")},
            "e": {r["cod"]: r["denumire"] for r in nomenclatoare.read_clasificatii(conn, "E")},
        }
    finally:
        if conn.is_connected():
            conn.close()


def _hash(code) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()
