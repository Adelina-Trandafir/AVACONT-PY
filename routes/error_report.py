# routes/error_report.py
"""
Slice 0112-04: an error message the operator chose to send from K-BOT's message window (the button
in its title bar). One row in AVACONT_COMUN.FX_RaportErori (sql/0112_04_fx_raport_erori.sql).

    POST /api/errors/report   {"rid": "<guid>", "text": "...", ...}   ->   {"saved": 1}

WHO SENT IT comes from the SESSION (user, unit id, database, PC name) and the request (IP), never
from the body: the body only describes the message and the state of the application. An idempotent
insert by `rid` (a GUID made by the client): a report sent twice is stored once.

Every text field is cut to its column's size, and characters outside the Basic Multilingual Plane
are replaced by '?' (the columns are utf8mb3: a 4-byte character would fail the whole insert).
"""
import json
import logging
import re
from datetime import datetime

import mysql.connector
from flask import Blueprint, g, request

from routes.auth.guard import require_session, json_response
from utils import database
from utils.database import COMMON_DB

error_report_bp = Blueprint("error_report", __name__)
logger = logging.getLogger(__name__)

MAX_BODY = 768 * 1024

_RX_RID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_RX_ASTRAL = re.compile("[\U00010000-\U0010FFFF]")

_SQL_INSERT = (
    f"INSERT INTO `{COMMON_DB}`.`FX_RaportErori` "
    "(Rid, MomentAfisat, Utilizator, IdUnitate, DbName, NumePc, Ip, "
    "NumeUnitate, Cf, An, Ss, CodProgram, Rol, "
    "VersiuneApp, Sistem, Runtime, NumeCalculator, UtilizatorWin, Cultura, Ecran, Tema, MemorieMb, ActivMinute, "
    "Sursa, LinieSursa, FereastraOwner, FereastraActiva, FerestreDeschise, Titlu, Antet, Mesaj, Butoane, "
    "JurnalErori, JurnalMesaje) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, "
    "%s, %s, %s, %s, %s, %s, "
    "%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
    "%s, %s, %s, %s, %s, %s, %s, %s, %s, "
    "%s, %s) "
    "ON DUPLICATE KEY UPDATE Rid = Rid"
)


def _txt(value, limit):
    """A string cut to `limit` characters, 4-byte characters replaced; '' when not a string."""
    if not isinstance(value, str):
        return ""
    return _RX_ASTRAL.sub("?", value)[:limit]


def _int(value):
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if -2147483648 <= value <= 2147483647 else None


def _utc(value):
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None


@error_report_bp.route("/api/errors/report", methods=["POST"])
@require_session
def error_report():
    try:
        length = request.content_length
        if length is not None and length > MAX_BODY:
            return json_response({"error": "Raportul este prea mare."}, 413)
        raw = request.get_data(cache=False)
        if len(raw) > MAX_BODY:
            return json_response({"error": "Raportul este prea mare."}, 413)
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            body = None
        if not isinstance(body, dict):
            return json_response({"error": "Cererea nu are forma așteptată."}, 400)

        rid = body.get("rid")
        if not isinstance(rid, str) or not _RX_RID.match(rid):
            return json_response({"error": "Raportul nu are un identificator valid."}, 400)
        if not isinstance(body.get("text"), str) or not body["text"].strip():
            return json_response({"error": "Raportul nu conține mesajul."}, 400)

        s = g.session
        values = (
            rid, _utc(body.get("shown_utc")),
            _txt(getattr(s, "username", ""), 120), _int(getattr(s, "id_unitate", None)),
            _txt(getattr(s, "db_name", ""), 64), _txt(getattr(s, "pcname", ""), 100),
            _txt(request.remote_addr, 45),
            _txt(body.get("unit_name"), 200), _txt(body.get("cf"), 32), _int(body.get("year")),
            _txt(body.get("ss"), 16), _txt(body.get("program"), 32), _txt(body.get("role"), 32),
            _txt(body.get("app_version"), 32), _txt(body.get("os"), 200), _txt(body.get("runtime"), 80),
            _txt(body.get("machine"), 100), _txt(body.get("windows_user"), 100), _txt(body.get("culture"), 20),
            _txt(body.get("screen"), 120), _txt(body.get("theme"), 40),
            _int(body.get("memory_mb")), _int(body.get("uptime_min")),
            _txt(body.get("source"), 200), _int(body.get("source_line")),
            _txt(body.get("owner_form"), 300), _txt(body.get("active_form"), 300),
            _txt(body.get("open_forms"), 8000),
            _txt(body.get("caption"), 300), _txt(body.get("header"), 600),
            _txt(body.get("text"), 500000), _txt(body.get("buttons"), 40),
            _txt(body.get("error_log_tail"), 500000), _txt(body.get("message_log_tail"), 500000),
        )
        _save(values)
        logger.info("ERROR_REPORT rid=%s user=%s source=%s", rid, values[2], values[23])
        return json_response({"saved": 1}, 200)
    except mysql.connector.Error as ex:
        # The driver message may quote a value, but this log is the server's own and the report is the operator's to send.
        logger.error("ERROR_REPORT database error errno=%s msg=%s", getattr(ex, "errno", "?"), str(ex)[:300])
        return json_response({"error": "Raportul nu a putut fi salvat pe server."}, 500)
    except Exception as ex:
        logger.exception("ERROR_REPORT unexpected error %s", type(ex).__name__)
        return json_response({"error": "Raportul nu a putut fi salvat pe server."}, 500)


def _save(values):
    conn = None
    try:
        conn = database.get_kbot_connection(COMMON_DB)
        cur = conn.cursor()
        cur.execute(_SQL_INSERT, values)
        conn.commit()
    except Exception:
        if conn is not None:
            conn.rollback()
        raise
    finally:
        if conn is not None and conn.is_connected():
            conn.close()
