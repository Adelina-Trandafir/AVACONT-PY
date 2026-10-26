# routes/avacont/common.py
"""Small helpers every AVACONT route module shares (kept out of the routes so none grows past its job)."""
import json
import logging

from flask import current_app

logger = logging.getLogger(__name__)


def json_utf8(payload, status):
    """A JSON response with LITERAL diacritics (ensure_ascii=False)."""
    body = json.dumps(payload, ensure_ascii=False, default=str)
    return current_app.response_class(body, status=status, mimetype="application/json")


def text(d: dict, key: str) -> str:
    """The trimmed text of a body field; missing / None -> ''."""
    return str(d.get(key) or "").strip()


def flag(d: dict, key: str, default: bool = False) -> bool:
    """A body field read as a boolean (true / false / 1 / 0); missing -> `default`."""
    v = d.get(key)
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "da")


def rollback(conn):
    """Best-effort rollback that never hides the error being handled."""
    if conn is not None:
        try:
            conn.rollback()
        except Exception:                                   # pragma: no cover - best effort
            logger.warning("[avacont] rollback a esuat", exc_info=True)
