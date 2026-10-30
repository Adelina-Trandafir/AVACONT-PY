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
    REASON_PASSWORD_CHANGE_REQUIRED, REASON_SESSION_REVOKED, SCOPE_FULL,
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
    REASON_PASSWORD_CHANGE_REQUIRED: "Trebuie să vă schimbați parola înainte de a continua.",
    REASON_SESSION_REVOKED: "Sesiunea a fost închisă (parola contului s-a schimbat). Autentificați-vă din nou.",
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


def _resolve(fn, args, kwargs, allow_restricted=False, touch=True):
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

    v = STORE.validate_and_touch_ex(token, touch=touch)
    if v.session is None:
        if v.reason in (REASON_EXPIRED_IDLE, REASON_EXPIRED_ABSOLUTE):
            return _reject_expiry(v, token)
        return reject(REASON_TOKEN_UNKNOWN, token)

    sess = v.session
    # Revocation: a session minted under an older AuthVersion of the account is dead
    # (password changed or administrator reset). Sessions without a version (0) predate this.
    if sess.auth_version:
        try:
            from routes.auth import account_state
            if account_state.current_version(sess.username) != sess.auth_version:
                STORE.revoke(token)
                return reject(REASON_SESSION_REVOKED, token)
        except Exception as err:   # DB down and nothing cached: do not guess, do not let it through
            logger.error("AuthVersion check failed: %s", err)
            return json_response({"error": "Serviciul de autentificare este indisponibil. Reîncercați.",
                                  "reason": "AUTH_UNAVAILABLE"}, 503)

    # A restricted session (initial password) may only reach the routes that opt in.
    if sess.scope != SCOPE_FULL and not allow_restricted:
        return json_response({"error": _REASON_MESSAGES[REASON_PASSWORD_CHANGE_REQUIRED],
                              "reason": REASON_PASSWORD_CHANGE_REQUIRED}, 403)

    g.session = sess
    g.session_token = token

    # Slice 0124-03 (operator, 30.10.2026, CRITICAL): a Secretar / Administrator sees no DDF but the ones addressed to them;
    # everything else works as before. Imported here, not at the top: routes.forexe imports this module.
    from routes.forexe.ddf_flux_core import refuza_restrans, rol_sesiune
    refuzat = refuza_restrans(request.path, request.method)
    if refuzat is not None:
        logger.warning("AUTH_403 restricted role=%s user=%s path=%s %s",
                       rol_sesiune(), sess.username, request.method, request.path)
        return refuzat
    return fn(*args, **kwargs)


def require_session(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        return _resolve(fn, args, kwargs)
    return wrapper


def require_session_no_touch(fn):
    """Like require_session, but does NOT slide the idle window (SLICE-CHAT-08): for a poll that
    must never keep an otherwise idle session alive. The absolute cap and revocation still apply."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        return _resolve(fn, args, kwargs, touch=False)
    return wrapper


def require_session_any_scope(fn):
    """Like require_session, but ALSO accepts the restricted password_change session.
    Only for: password policy / code / change and logout."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        return _resolve(fn, args, kwargs, allow_restricted=True)
    return wrapper
