# routes/help_feedback.py
"""
Slice 0000-21: the questions typed in K-BOT's help, with the results shown, what was opened
and the 1-5 star rating. One table, AVACONT_COMUN.FX_AjutorIntrebari
(sql/0000_21_fx_ajutor_intrebari.sql). Read later to decide whether plain search is good
enough (docs/PLAN_help_assistant.md, "step 4").

    POST /api/help/feedback   {"rows": [ {...}, ... ]}   ->   {"saved": n, "rejected": m}

NOTHING ABOUT WHO OR WHERE. The route is behind the K-BOT bearer guard (only a logged-in
K-BOT may send), but it never reads g.session: no user, e-mail, unit, DC, IP or machine goes
into the row. Its own log lines go to a separate file (help_feedback.log) through a logger that
does NOT propagate to the root logger -- the root handlers stamp every line with the caller's
IP and session tag (utils/logger.py). Those lines carry counts and error types only, never the
question text.

BATCHES. The client keeps the questions in a local waiting list and sends them in batches
(at most MAX_ROWS rows, MAX_BODY bytes). Every row is an idempotent upsert by `qid` (a random
GUID the client makes per question): a batch sent twice changes nothing, a later rating or
click updates the same row. A row that fails validation is counted in `rejected` and skipped;
the rest of the batch is saved. The client drops the whole batch on a 200 either way -- a bad
row would be bad again on every retry.
"""
import json
import logging
import re
from datetime import datetime
from logging.handlers import RotatingFileHandler

import mysql.connector
from flask import Blueprint, g, request

from routes.auth.guard import require_session, json_response
from utils import database
from utils.database import COMMON_DB

help_feedback_bp = Blueprint("help_feedback", __name__)

MAX_ROWS = 50
MAX_BODY = 64 * 1024
MAX_QUESTION = 300
MAX_HITS = 5

LOG_PATH = "help_feedback.log"

_RX_QID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_RX_HIT = re.compile(r"^[a-z0-9._-]{1,80}(#[a-z0-9-]{1,60})?$")
_RX_PARTS = re.compile(r"^(contabil|avansat|director)(\+(contabil|avansat|director)){0,2}$")
_RX_APP_VERSION = re.compile(r"^[0-9]{1,5}(\.[0-9]{1,6}){0,3}$")
_RX_HELP_VERSION = re.compile(r"^([0-9]{4}-[0-9]{2}-[0-9]{2})?$")
_ACTIONS = {"open", "tour"}
_PLACES = {"popup", "fereastra"}

_SQL_UPSERT = (
    f"INSERT INTO `{COMMON_DB}`.`FX_AjutorIntrebari` "
    "(Qid, Intrebare, Moment, Parte, VersiuneApp, VersiuneAjutor, Rezultate, Deschis, Actiune, Nota, Loc) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
    "ON DUPLICATE KEY UPDATE "
    "Intrebare = VALUES(Intrebare), Moment = VALUES(Moment), Parte = VALUES(Parte), "
    "VersiuneApp = VALUES(VersiuneApp), VersiuneAjutor = VALUES(VersiuneAjutor), "
    "Rezultate = VALUES(Rezultate), Deschis = VALUES(Deschis), Actiune = VALUES(Actiune), "
    "Nota = VALUES(Nota), Loc = VALUES(Loc)"
)


def _make_logger():
    """A logger of its own: no IP, no session tag, never the root handlers."""
    log = logging.getLogger("help_feedback")
    log.propagate = False
    if not log.handlers:
        handler = RotatingFileHandler(LOG_PATH, maxBytes=2 * 1024 * 1024, backupCount=3,
                                      encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
        log.addHandler(handler)
        log.setLevel(logging.INFO)
    return log


logger = _make_logger()


@help_feedback_bp.route("/api/help/feedback", methods=["POST"])
@require_session
def help_feedback():
    # The guard has checked the token; nothing below needs the login. Dropping it from `g`
    # means no line written during this request (by us or a library) can carry the user tag.
    g.pop("session", None)
    g.pop("session_token", None)
    try:
        length = request.content_length
        if length is not None and length > MAX_BODY:
            return json_response({"error": "Prea multe întrebări într-un singur trimis."}, 413)
        raw = request.get_data(cache=False)
        if len(raw) > MAX_BODY:
            return json_response({"error": "Prea multe întrebări într-un singur trimis."}, 413)
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            body = None
        rows = body.get("rows") if isinstance(body, dict) else None
        if not isinstance(rows, list) or not rows or len(rows) > MAX_ROWS:
            return json_response({"error": "Cererea nu are forma așteptată."}, 400)

        values = []
        rejected = 0
        for row in rows:
            v = _validate(row)
            if v is None:
                rejected += 1
            else:
                values.append(v)

        if values:
            _save(values)
        logger.info("batch rows=%d saved=%d rejected=%d", len(rows), len(values), rejected)
        return json_response({"saved": len(values), "rejected": rejected}, 200)
    except mysql.connector.Error as ex:
        # errno only: a driver message may quote a value of the row.
        logger.error("database error errno=%s", getattr(ex, "errno", "?"))
        return json_response({"error": "Întrebările nu au putut fi salvate pe server."}, 500)
    except Exception as ex:
        logger.error("unexpected error %s", type(ex).__name__)
        return json_response({"error": "Întrebările nu au putut fi salvate pe server."}, 500)


def _validate(row):
    """The row's values in column order, or None when any field is not what the client sends."""
    if not isinstance(row, dict):
        return None
    qid = row.get("qid")
    if not isinstance(qid, str) or not _RX_QID.match(qid):
        return None
    question = row.get("question")
    if not isinstance(question, str):
        return None
    question = " ".join(question.split())
    if not question or len(question) > MAX_QUESTION:
        return None
    asked = _parse_utc(row.get("asked_utc"))
    if asked is None:
        return None
    parts = row.get("parts")
    if not isinstance(parts, str) or not _RX_PARTS.match(parts):
        return None
    app_version = row.get("app_version")
    if not isinstance(app_version, str) or not _RX_APP_VERSION.match(app_version):
        return None
    help_version = row.get("help_version", "")
    if help_version is None:
        help_version = ""
    if not isinstance(help_version, str) or not _RX_HELP_VERSION.match(help_version):
        return None
    hits = row.get("hits", [])
    if not isinstance(hits, list) or len(hits) > MAX_HITS:
        return None
    if not all(isinstance(h, str) and _RX_HIT.match(h) for h in hits):
        return None
    opened = row.get("opened")
    if opened is not None and (not isinstance(opened, str) or not _RX_HIT.match(opened)):
        return None
    action = row.get("action")
    if action is not None and action not in _ACTIONS:
        return None
    rating = row.get("rating")
    if rating is not None and (isinstance(rating, bool) or not isinstance(rating, int) or not 1 <= rating <= 5):
        return None
    where = row.get("where")
    if where not in _PLACES:
        return None
    return (qid, question, asked, parts, app_version, help_version, "|".join(hits),
            opened, action, rating, where)


def _parse_utc(value):
    """'YYYY-MM-DDTHH:MM:SSZ' -> naive datetime (UTC), or None."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None


def _connect():
    """
    The K-BOT server's AVACONT_COMUN, opened here rather than through
    utils.database.get_kbot_connection: that helper logs a failed connect through the root
    logger, whose handlers add the caller's IP and session tag to the line.
    """
    cfg = database._timeouts(database._kbot_config())
    cfg["database"] = COMMON_DB
    conn = mysql.connector.connect(**cfg)
    conn.autocommit = False
    return conn


def _save(values):
    conn = None
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.executemany(_SQL_UPSERT, values)
        conn.commit()
    except Exception:
        if conn is not None:
            conn.rollback()
        raise
    finally:
        if conn is not None and conn.is_connected():
            conn.close()
