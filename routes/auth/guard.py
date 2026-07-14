# routes/auth/guard.py
"""
Decoratorul bearer pentru rutele autentificate ale aplicatiei K-BOT.

Cererea trebuie sa poarte `Authorization: Bearer <token opac>`. Un token absent,
malformat, necunoscut sau expirat -> 401 cu un COD-MOTIV stabil + mesaj romanesc;
clientul re-arata LoginForm (si, la un al doilea 401 dupa re-login, avertizeaza ca
e un defect de server — vezi MainForm.WithReauth).

Fiecare 401 se logheaza structurat (worker_pid, motiv, marimea store-ului, primele
8 caractere ale tokenului, marcaje de timp la expirare, calea) ca sa se explice
singur — exact ce lipsea cand login-ul reusea dar prima cerere autentificata pica.

SECURITY (R1): token-ul e credential bearer — se logheaza DOAR primele 8 caractere,
niciodata valoarea intreaga; corpul si logul nu contin parola.
"""
import json
import logging
import os
from functools import wraps

from flask import request, jsonify, g, current_app

from routes.auth.session_store import (
    STORE,
    REASON_TOKEN_ABSENT, REASON_TOKEN_MALFORMED, REASON_TOKEN_UNKNOWN,
    REASON_EXPIRED_IDLE, REASON_EXPIRED_ABSOLUTE, REASON_CONTEXT_MISMATCH,
)

logger = logging.getLogger(__name__)

# Mesaj romanesc (cu diacritice reale) pentru fiecare cod-motiv. Toate contin
# «Autentificați-vă» ca operatorul sa stie clar ce are de facut.
_REASON_MESSAGES = {
    REASON_TOKEN_ABSENT: "Autentificare necesară. Autentificați-vă.",
    REASON_TOKEN_MALFORMED: "Antet de autorizare invalid. Autentificați-vă.",
    REASON_TOKEN_UNKNOWN: "Sesiune necunoscută. Autentificați-vă din nou.",
    REASON_EXPIRED_IDLE: "Sesiune expirată din inactivitate. Autentificați-vă din nou.",
    REASON_EXPIRED_ABSOLUTE: "Sesiune expirată. Autentificați-vă din nou.",
    REASON_CONTEXT_MISMATCH: "Acces interzis pentru această unitate.",
}


def json_response(payload, status):
    """Raspuns JSON cu diacritice LITERALE UTF-8 (ensure_ascii=False), nu \\uXXXX.
    Folosit pentru toate raspunsurile auth de eroare, indiferent de configul global."""
    body = json.dumps(payload, ensure_ascii=False)
    return current_app.response_class(body, status=status, mimetype="application/json")


def reject(reason, token, status=401):
    """Logheaza structurat si intoarce corpul de 401/403 cu cod-motiv + mesaj roman."""
    msg = _REASON_MESSAGES.get(reason, "Autentificare necesară. Autentificați-vă.")
    token8 = (token or "")[:8]
    try:
        store_size = STORE.size()
    except Exception:                     # marimea e diagnostic; nu blocheaza raspunsul
        store_size = -1
    logger.warning(
        "AUTH_401 pid=%s reason=%s path=%s store_size=%s token8=%s",
        os.getpid(), reason, request.path, store_size, token8,
    )
    return json_response({"error": msg, "reason": reason}, status)


def _reject_expiry(v, token):
    """Ca reject, dar adauga issued_at/last_seen/now (ISO) — un bug de fus orar sau
    ceas se vede pe loc in log."""
    from datetime import datetime, timezone

    def _iso(t):
        return None if t is None else datetime.fromtimestamp(t, tz=timezone.utc).isoformat()

    msg = _REASON_MESSAGES.get(v.reason, "Sesiune expirată. Autentificați-vă din nou.")
    logger.warning(
        "AUTH_401 pid=%s reason=%s path=%s store_size=%s token8=%s "
        "issued_at=%s last_seen=%s now=%s",
        os.getpid(), v.reason, request.path,
        _safe_size(), (token or "")[:8],
        _iso(v.issued_at), _iso(v.last_seen), _iso(v.now),
    )
    return json_response({"error": msg, "reason": v.reason}, 401)


def _safe_size():
    try:
        return STORE.size()
    except Exception:
        return -1


def _resolve(fn, args, kwargs):
    """Nucleul comun: valideaza bearer-ul, aseaza g.session/g.session_token, apeleaza
    handler-ul. Intoarce raspunsul de 401 (cu motiv) daca tokenul nu e viu."""
    auth = request.headers.get("Authorization", "")
    if not auth:
        return reject(REASON_TOKEN_ABSENT, "")
    if not auth.startswith("Bearer "):
        return reject(REASON_TOKEN_MALFORMED, "")
    token = auth[7:].strip()
    if not token:
        return reject(REASON_TOKEN_MALFORMED, "")

    v = STORE.validate_and_touch_ex(token)
    if v.session is None:
        if v.reason in (REASON_EXPIRED_IDLE, REASON_EXPIRED_ABSOLUTE):
            return _reject_expiry(v, token)
        return reject(REASON_TOKEN_UNKNOWN, token)

    g.session = v.session
    g.session_token = token
    return fn(*args, **kwargs)


def require_session(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        return _resolve(fn, args, kwargs)
    return wrapper


def require_session_or_api_key(fn):
    """
    Guard de TRANZITIE pentru rutele partajate intre K-BOT (bearer) si FOREXE
    legacy (X-Api-Key), ex. /api/tools/process_excel.

    Regula: daca cererea poarta un header Authorization, e judecata EXCLUSIV ca
    bearer (un token expirat primeste mesajul de re-login, nu cade pe cheia
    legacy). Fara Authorization, se accepta X-Api-Key-ul flotei vechi. Cand
    ultimul FOREXE legacy dispare, rutele trec pe require_session si acest
    decorator se sterge.

    Pe calea legacy g.session NU exista — handler-ele partajate nu trebuie sa
    depinda de el.
    """
    @wraps(fn)
    def wrapper(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        if auth:
            return _resolve(fn, args, kwargs)

        # Cale legacy (FOREXE vechi). Import lazy: guard-ul ramane importabil
        # fara config.py pe o masina de dev (vezi test_session_store).
        from config import API_KEY
        if request.headers.get("X-Api-Key") == API_KEY:
            return fn(*args, **kwargs)
        return jsonify(error="Unauthorized"), 401
    return wrapper
