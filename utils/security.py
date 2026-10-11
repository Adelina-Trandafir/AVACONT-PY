from functools import wraps
from flask import request, jsonify
import logging
from config import API_KEY

logger = logging.getLogger(__name__)

def require_api_key(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        client_ip = request.remote_addr
        endpoint = request.path
        
        if request.headers.get('X-Api-Key') != API_KEY:
            logger.warning(f"ACCES REFUZAT! IP: {client_ip} a incercat sa acceseze {endpoint} cu cheie gresita.")
            return jsonify({"error": "Unauthorized"}), 401
        
        return func(*args, **kwargs)
    return wrapper

# ---------------------------------------------------------------------------
# Security plan S2/S4: administration has its OWN credential, never the key shipped to clients.
# ADMIN_TOKEN lives only in the server's config.py (and in the memory of the admin tools, which
# read it over SSH). Header: X-Admin-Token. Unset or empty = every admin route refuses (503).
#
# TRANSITION ONLY: until the final retirement step the legacy X-Api-Key is still accepted here so
# the old and the new system can be tested side by side. That step deletes the branch marked
# LEGACY-KEY-BRANCH together with require_api_key and config.API_KEY.
# ---------------------------------------------------------------------------
def _admin_token():
    try:
        import config
        return str(getattr(config, "ADMIN_TOKEN", "") or "")
    except Exception:
        return ""


def require_admin(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        import hmac
        expected = _admin_token()
        given = request.headers.get('X-Admin-Token', '')
        if expected and given and hmac.compare_digest(given.encode("utf-8"), expected.encode("utf-8")):
            return func(*args, **kwargs)
        # LEGACY-KEY-BRANCH (removed at retirement)
        if request.headers.get('X-Api-Key') == API_KEY:
            return func(*args, **kwargs)
        if not expected:
            logger.error("ADMIN_TOKEN is not set in config.py: admin routes accept only the legacy key")
        logger.warning(f"ACCES ADMIN REFUZAT! IP: {request.remote_addr} {request.path}")
        return jsonify({"error": "Unauthorized"}), 401
    return wrapper
