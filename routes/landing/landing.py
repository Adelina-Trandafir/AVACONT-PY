"""
The public presentation page of K-BOT: GET /

Pre-login, no data from the database, no cookies, no forms. The whole text of the page
lives in content.json (next to this file) and the layout in templates/landing/index.html,
so a person can change a sentence or a picture by editing one file, without touching code.
The registration wizard stays where it was: /inregistrare (routes/inregistrare).

Same page headers as the registration pages: scripts, styles, images and fonts only from
this server, no framing. That is why the page uses system fonts and inline SVG icons.
"""
import json
import logging
import os
import re
import threading

from flask import Blueprint, abort, make_response, render_template
from markupsafe import Markup, escape

logger = logging.getLogger(__name__)

landing_bp = Blueprint("landing", __name__, template_folder="templates")

_CONTENT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "content.json")

_PAGE_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
        "base-uri 'none'; form-action 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    # Revalidated on every visit, so an edited sentence shows up at once.
    "Cache-Control": "no-cache",
}

_BOLD = re.compile(r"\*\*(.+?)\*\*")

# The file is re-read only when it changes on disk.
_cache_lock = threading.Lock()
_cache = {"mtime": None, "data": None}


def _load_content():
    """Return the parsed content.json, re-reading it when the file was edited."""
    try:
        mtime = os.path.getmtime(_CONTENT_FILE)
        with _cache_lock:
            if _cache["data"] is None or _cache["mtime"] != mtime:
                with open(_CONTENT_FILE, "r", encoding="utf-8") as handle:
                    _cache["data"] = json.load(handle)
                _cache["mtime"] = mtime
            return _cache["data"]
    except Exception:
        logger.exception("landing: content.json could not be read")
        raise


@landing_bp.app_template_filter("rich")
def rich(text):
    """Escape the text, then turn **word** into <strong>word</strong>. Nothing else is allowed."""
    escaped = str(escape(text))
    return Markup(_BOLD.sub(r"<strong>\1</strong>", escaped))


@landing_bp.route("/", methods=["GET"])
def landing_page():
    try:
        content = _load_content()
        response = make_response(render_template("landing/index.html", c=content))
    except Exception:
        abort(500)
    response.headers.update(_PAGE_HEADERS)
    return response
