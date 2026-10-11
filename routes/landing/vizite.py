"""
Who looks at the presentation page (slice 0110-09): POST /api/vizita.

PUBLIC, no cookie, no login. static/site/visit.js (loaded by the landing page) sends a small JSON
now and then while the page is open:

    {v: <32 hex, made by the page, one per page load>, t: <seconds the page was really in front
     of the person>, s: {<section id>: <seconds>, ...}, w, h, m (touch device), l, r (referrer host)}

Every message carries the TOTALS so far, not the increment, so a lost or late message costs
nothing: the row keeps the largest `t` it has seen (and the sections that go with it).
The server adds what the page cannot know: the address (ProxyFix makes it the real one), the
country (utils/geoip, offline), the browser line, and whether it looks like a robot.

The rows are read back only by KBotAdmin (routes/admin_cli). The table is
AVACONT_COMUN.Vizite_Site (sql/0110_09_vizite_site.sql); rows older than
config.VIZITE_RETENTIE_ZILE days (180 by default) are deleted, a handful of requests at a time.

Abuse: a message is small (4 KB), every field is cut to a known length and a known shape, the
section names are plain ids, and one address may send MAX_PER_WINDOW messages per WINDOW seconds
(a page open for ten minutes sends about twenty). The answer is always 204: the page has nothing
to do with a refusal.
"""
import json
import logging
import re
import threading
import time
from collections import deque

import mysql.connector
from flask import Blueprint, request

from utils import geoip
from utils.database import COMMON_DB, get_kbot_connection

try:                     # the offline tests stand a stub `config` in sys.modules
    import config
except Exception:        # pragma: no cover - config is always there on the server
    config = None

logger = logging.getLogger(__name__)

vizite_bp = Blueprint("vizite", __name__)

MAX_BODY = 4096
WINDOW = 600
MAX_PER_WINDOW = 120
MAX_SECTIONS = 40
MAX_SECONDS = 24 * 3600
PURGE_EVERY = 200

_VISIT_ID = re.compile(r"^[0-9a-f]{32}$")
_SECTION_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
_HOST = re.compile(r"^[a-z0-9.-]{1,80}$")
_LANG = re.compile(r"^[A-Za-z-]{2,16}$")
_MOBILE_UA = re.compile(r"Mobi|Android|iPhone|iPad|iPod|Windows Phone|Opera Mini", re.IGNORECASE)
_BOT_UA = re.compile(
    r"bot|crawl|spider|slurp|facebookexternalhit|preview|monitor|uptime|headless|lighthouse|"
    r"curl/|wget/|python-requests|httpclient|okhttp|scrapy|go-http", re.IGNORECASE)
_PAGE = "/"

_lock = threading.Lock()
_hits = {}                   # address -> deque of times
_writes = 0


def _retention_days():
    return int(getattr(config, "VIZITE_RETENTIE_ZILE", 180) or 180)


def _allowed(ip):
    """One sliding window per address, in this process (one gunicorn worker, as the rest)."""
    now = time.time()
    with _lock:
        queue = _hits.get(ip)
        if queue is None:
            if len(_hits) > 20000:           # an address that never comes back must not stay forever
                _hits.clear()
            queue = _hits[ip] = deque()
        while queue and queue[0] < now - WINDOW:
            queue.popleft()
        if len(queue) >= MAX_PER_WINDOW:
            return False
        queue.append(now)
        return True


def _int(value, low, high):
    """An int in [low, high] or None; a bool or a float is not accepted."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return max(low, min(high, value))


def _clean(body):
    """The message as safe values, or None when it is not one of ours."""
    visit = body.get("v")
    if not isinstance(visit, str) or not _VISIT_ID.match(visit):
        return None
    total = _int(body.get("t"), 0, MAX_SECONDS)
    if total is None:
        return None
    sections = {}
    raw = body.get("s")
    if isinstance(raw, dict):
        for key, value in list(raw.items())[:MAX_SECTIONS]:
            seconds = _int(value, 0, MAX_SECONDS)
            if isinstance(key, str) and _SECTION_ID.match(key) and seconds:
                sections[key] = seconds
    width, height = _int(body.get("w"), 0, 20000), _int(body.get("h"), 0, 20000)
    lang = body.get("l")
    referrer = body.get("r")
    return {
        "visit": visit,
        "total": total,
        "sections": sections,
        "screen": "%dx%d" % (width, height) if width and height else None,
        "touch": body.get("m") is True,
        "lang": lang if isinstance(lang, str) and _LANG.match(lang) else None,
        "referrer": referrer.lower() if isinstance(referrer, str) and _HOST.match(referrer.lower()) else None,
    }


@vizite_bp.route("/api/vizita", methods=["POST"])
def vizita():
    ip = request.remote_addr or ""
    if not _allowed(ip):
        return "", 204
    if (request.content_length or 0) > MAX_BODY:
        return "", 204
    try:
        body = json.loads(request.get_data(cache=False, as_text=True) or "null")
    except ValueError:
        return "", 204
    data = _clean(body) if isinstance(body, dict) else None
    if data is None:
        return "", 204
    agent = (request.headers.get("User-Agent") or "")[:255]
    try:
        _save(ip, agent, data)
    except Exception as err:
        # the visitor must never see a problem; the operator reads it in api_server.log
        logger.error("vizita: the visit could not be saved: %s", err)
    return "", 204


def _save(ip, agent, data):
    global _writes
    # iPadOS Safari calls itself a Mac; a Mac with a touch screen is an iPad
    mobile = 1 if _MOBILE_UA.search(agent) or (data["touch"] and "Macintosh" in agent) else 0
    bot = 1 if (not agent or _BOT_UA.search(agent)) else 0
    conn = None
    try:
        conn = get_kbot_connection(COMMON_DB)
        cur = conn.cursor()
        # `Sectiuni` is assigned BEFORE `DurataSec` on purpose: MariaDB evaluates the list left to
        # right, so the comparison still sees the old total. A late message (smaller total) changes
        # only `Ultima`.
        cur.execute(
            "INSERT INTO Vizite_Site (VisitId, IP, Tara, Mobil, Bot, Pagina, Referrer, Ecran, Limba, "
            "UserAgent, DurataSec, Sectiuni) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON DUPLICATE KEY UPDATE Ultima = NOW(), "
            "Sectiuni = IF(VALUES(DurataSec) >= DurataSec, VALUES(Sectiuni), Sectiuni), "
            "DurataSec = GREATEST(DurataSec, VALUES(DurataSec))",
            (data["visit"], ip[:45] or None, geoip.country(ip) or None, mobile, bot, _PAGE,
             data["referrer"], data["screen"], data["lang"], agent or None, data["total"],
             json.dumps(data["sections"], separators=(",", ":"))),
        )
        conn.commit()
        with _lock:
            _writes += 1
            purge = _writes % PURGE_EVERY == 0
        if purge:
            cur.execute("DELETE FROM Vizite_Site WHERE Prima < NOW() - INTERVAL %s DAY", (_retention_days(),))
            conn.commit()
    except mysql.connector.Error:
        if conn is not None and conn.is_connected():
            conn.rollback()
        raise
    finally:
        if conn is not None and conn.is_connected():
            conn.close()
