"""
Route guards that are NOT the bearer session (security plan, S2).

The key that used to be shipped to every client (config.API_KEY, header X-Api-Key) is gone: no route
accepts it any more. Two narrow credentials remain, each held only where it is needed and each valid
for its own routes only:

  * require_admin        X-Admin-Token == config.ADMIN_TOKEN   administration tools (KBotAdmin, migrator)
  * require_access_sync  X-Access-Key  == config.ACCESS_SYNC_KEY   the Access application's sync of
                         classifications and partners (routes/clasificatii.py, routes/parteneri.py)

A credential that is unset or empty makes its routes refuse everything (never "open").
"""
import hmac
import logging
from functools import wraps

from flask import request, jsonify

logger = logging.getLogger(__name__)


def _config_secret(name):
    try:
        import config
        return str(getattr(config, name, "") or "")
    except Exception:
        return ""


def _guard(header, config_name):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            expected = _config_secret(config_name)
            given = request.headers.get(header, "")
            if expected and given and hmac.compare_digest(given.encode("utf-8"), expected.encode("utf-8")):
                return func(*args, **kwargs)
            if not expected:
                logger.error("%s is not set in config.py: routes guarded by %s refuse every request",
                             config_name, header)
            logger.warning("ACCES REFUZAT (%s)! IP: %s %s", header, request.remote_addr, request.path)
            return jsonify({"error": "Unauthorized"}), 401
        return wrapper
    return decorator


require_admin = _guard("X-Admin-Token", "ADMIN_TOKEN")
require_access_sync = _guard("X-Access-Key", "ACCESS_SYNC_KEY")
