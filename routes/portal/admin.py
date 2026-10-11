"""
The admin page of the web area (slice 0110-09). ONLY for the accounts in config.PORTAL_ADMIN_EMAILS
(default: scavatarsoft@gmail.com); for everybody else every route here answers 404, as if it did
not exist.

    GET /api/portal/admin/databases      the databases: size, tables, activity, errors, schema state
    GET /api/portal/admin/logs/meta      the log files that exist + the databases to filter on
    GET /api/portal/admin/logs           log entries: ?file=&db=&limit=&gens=
    GET /api/portal/admin/visits         who looked at the presentation page: ?days=&bots=

READ-ONLY. Nothing here writes to a database or to a file. The routes sit on the portal session
(`X-Portal-Token`) but, unlike the data routes of 0110-06, need no unit opened: an administrator
looks at all of them.

THE LOGS. Same files and same line formats routes/logs.py reads for the operator's own journal,
but ALL users' lines. A line carries `{s=<token8> u=<user> dc=<database>}` when it was written
inside an authenticated K-BOT request; lines without it (startup, login, the old Access client,
schema_sync) belong to no database and are found with the filter «fara baza de date».
  api_server.log / api_server_vba.log / schema_sync.log   one entry per header line (+ traceback lines)
  forexe_timing.log / asociere.log                        one entry per `====` block
Files are read newest generation first (.1 .. .5 are the rotated copies); `gens` says how many of
the rotated copies are read besides the live file.
"""
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta
from functools import wraps

import mysql.connector
from flask import g, request

from routes.landing import vizite as vizite_module
from utils import geoip
from utils import logger as server_logger
from utils import timing
from utils.database import get_kbot_comun_connection

from .portal import _fail, _json, _live_session, _slide, is_admin_email, portal_bp

logger = logging.getLogger(__name__)

DEFAULT_LIMIT = 1500
MAX_LIMIT = 5000
MAX_GENS = 5
MAX_DETAIL = 3000
MAX_VISIT_ROWS = 5000
COUNTS_TTL = 60

COMMON_DB = "AVACONT_COMUN"
SOURCE_DB = "AVACONT_SURSA"
_NO_DB = "-"        # the value of the `db` filter that means «fara baza de date»

_RX_LINE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d{3} - (?P<lvl>[A-Z]+) - (?P<ip>\S+) - (?P<msg>.*)$")
_RX_TAG = re.compile(r"^\{s=(?P<s>\S+) u=(?P<u>\S+) dc=(?P<dc>\S+)\} (?P<rest>.*)$", re.DOTALL)
_RX_BLOCK_HEAD = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\.\d{3}\s+rulare\s+(?P<run>\d+)\s+\[(?P<label>[^\]]*)\]"
    r"\s+status\s+(?P<status>\S+)")
_RX_NOTE = re.compile(r"(\w+)=(\S+)")
_SEPARATOR = "=" * 100


# ---------------------------------------------------------------------------
# The guard
# ---------------------------------------------------------------------------
def require_portal_admin(fn):
    """Portal session + an administrator account. Anyone else gets a plain 404."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        token, note, refusal = _live_session()
        if refusal is not None:
            return refusal
        if not is_admin_email(note.get("email")):
            logger.warning("portal admin: %s is not an administrator", note.get("email"))
            return _fail("NEGASIT", "Pagina nu există.", 404)
        _slide(token, note)
        g.portal_admin = note["email"]
        return fn(*args, **kwargs)
    return wrapper


def _connect():
    return get_kbot_comun_connection()


def _int_arg(name, default, low, high):
    try:
        value = int(request.args.get(name, ""))
    except (TypeError, ValueError):
        return default
    return max(low, min(high, value))


def _iso(value):
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%dT%H:%M:%S")
    return value


# ---------------------------------------------------------------------------
# The log files
# ---------------------------------------------------------------------------
def _env_or_config_path(env, attr, default):
    try:
        import config
    except Exception:    # pragma: no cover - config is always there on the server
        config = None
    return os.environ.get(env) or getattr(config, attr, None) or default


def _schema_sync_path():
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "schema_sync", "schema_sync.log")


# key -> (label, path, backups, kind)
def _files():
    return {
        "server": ("api_server.log", server_logger.SERVER_LOG_PATH, server_logger.SERVER_LOG_BACKUPS, "lines"),
        "vba": ("api_server_vba.log", server_logger.VBA_LOG_PATH, server_logger.SERVER_LOG_BACKUPS, "lines"),
        "timing": ("forexe_timing.log", timing.log_path(), timing.LOG_BACKUPS, "blocks"),
        "asociere": ("asociere.log", _env_or_config_path("KBOT_ASOCIERE_LOG", "ASOCIERE_LOG_PATH", "asociere.log"),
                     5, "blocks"),
        "schema": ("schema_sync.log", _schema_sync_path(), 5, "lines"),
    }


def _generations(path, backups, gens):
    yield path
    for i in range(1, min(backups, gens) + 1):
        yield "%s.%d" % (path, i)


def _read_lines(path):
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        return handle.read().splitlines()


def _db_matches(entry_db, wanted):
    """`wanted`: '' = any database, '-' = none, else that database (case does not matter)."""
    if not wanted:
        return True
    if wanted == _NO_DB:
        return entry_db in ("", _NO_DB)
    return entry_db.casefold() == wanted.casefold()


def _entry(file_label, ts, level, ip, user, db, session, msg, lines, keep_text):
    text = "\n".join(lines)
    return {
        "ts": ts.replace(" ", "T"),
        "file": file_label,
        "level": level,
        "ip": ip,
        "user": user,
        "db": "" if db == _NO_DB else db,
        "session": session,
        "msg": msg,
        # the detail only when there is more than the first line: a plain line says it all in `msg`
        "detail": text[:MAX_DETAIL] if keep_text and len(lines) > 1 else "",
    }


def _parse_lines(label, path, wanted_db, keep_text):
    """Entries of a `date - LEVEL - ip - message` file, in file order. A line that is not a header
    (a traceback) belongs to the entry above it."""
    out = []
    current = None
    body = []
    for line in _read_lines(path):
        head = _RX_LINE.match(line)
        if head is None:
            if current is not None:
                body.append(line)
            continue
        if current is not None:
            out.append(_close(current, body, keep_text))
        current, body = None, []
        message = head.group("msg")
        tag = _RX_TAG.match(message)
        if tag is not None:
            sess, user, db, rest = tag.group("s"), tag.group("u"), tag.group("dc"), tag.group("rest")
        else:
            sess, user, db, rest = "", "", "", message
        if not _db_matches("" if db == _NO_DB else db, wanted_db):
            continue
        first = rest.splitlines()[0] if rest else ""
        current = (label, head.group("ts"), head.group("lvl"), head.group("ip"), user, db, sess, first, [rest])
    if current is not None:
        out.append(_close(current, body, keep_text))
    return out


def _close(current, body, keep_text):
    label, ts, level, ip, user, db, sess, first, lines = current
    return _entry(label, ts, level, ip, user, db, sess, first, lines + body, keep_text)


def _block_level(status, text):
    if status.startswith("5") or status.startswith("EXC"):
        return "ERROR"
    if status.startswith("4") or "RESPINS" in text:
        return "WARNING"
    return "INFO"


def _parse_blocks(label, path, wanted_db, keep_text):
    """Entries of a file of `====` blocks (forexe_timing.log, asociere.log), in file order."""
    out = []
    block = []
    for line in _read_lines(path) + [_SEPARATOR]:
        if line.strip() != _SEPARATOR:
            block.append(line)
            continue
        entry = _block_entry(label, block, wanted_db, keep_text)
        if entry is not None:
            out.append(entry)
        block = []
    return out


def _block_entry(label, block, wanted_db, keep_text):
    if not block:
        return None
    head = _RX_BLOCK_HEAD.match(block[0])
    if head is None:
        return None
    notes = dict(_RX_NOTE.findall(" ".join(block[1:4])))
    db = notes.get("dc", "")
    if not _db_matches("" if db == _NO_DB else db, wanted_db):
        return None
    text = "\n".join(block)
    level = _block_level(head.group("status"), text)
    msg = "rulare %s [%s] status %s" % (head.group("run"), head.group("label"), head.group("status"))
    return _entry(label, head.group("ts"), level, "", notes.get("user", ""), db, notes.get("session", ""),
                  msg, block if keep_text else [msg], keep_text)


_PARSERS = {"lines": _parse_lines, "blocks": _parse_blocks}


def _file_info(label, path, backups):
    exists = os.path.isfile(path)
    rotated = sum(1 for i in range(1, backups + 1) if os.path.isfile("%s.%d" % (path, i)))
    stat = os.stat(path) if exists else None
    return {
        "label": label,
        "exists": exists,
        "size": stat.st_size if stat else 0,
        "modified": _iso(datetime.fromtimestamp(stat.st_mtime)) if stat else None,
        "rotated": rotated,
    }


@portal_bp.route("/api/portal/admin/logs/meta", methods=["GET"])
@require_portal_admin
def admin_logs_meta():
    files = [{"key": key, **_file_info(label, path, backups)} for key, (label, path, backups, _k) in _files().items()]
    databases = []
    try:
        conn = _connect()
        try:
            cur = conn.cursor()
            cur.execute("SELECT DC, NumeUnitate FROM Unitati ORDER BY DC")
            databases = [{"dc": dc, "name": name} for dc, name in cur.fetchall()]
        finally:
            if conn.is_connected():
                conn.close()
    except mysql.connector.Error as err:
        logger.error("portal admin: the units could not be read: %s", err)
        return _fail("DB_ERROR", "Lista bazelor nu a putut fi citită.", 500)
    return _json({"files": files, "databases": databases, "server_time": _iso(datetime.now())})


@portal_bp.route("/api/portal/admin/logs", methods=["GET"])
@require_portal_admin
def admin_logs():
    files = _files()
    wanted_file = (request.args.get("file") or "").strip()
    if wanted_file and wanted_file not in files:
        return _fail("FISIER_NECUNOSCUT", "Fișierul de jurnal cerut nu există în listă.", 400)
    wanted_db = (request.args.get("db") or "").strip()
    limit = _int_arg("limit", DEFAULT_LIMIT, 1, MAX_LIMIT)
    gens = _int_arg("gens", 1, 0, MAX_GENS)

    entries = []
    unreadable = []
    for key, (label, path, backups, kind) in files.items():
        if wanted_file and key != wanted_file:
            continue
        # oldest generation first, so the whole list is in time order
        for gen_path in reversed(list(_generations(path, backups, gens))):
            if not os.path.isfile(gen_path):
                continue
            try:
                entries.extend(_PARSERS[kind](label, gen_path, wanted_db, True))
            except OSError as err:
                logger.error("portal admin: %s could not be read: %s", gen_path, err)
                unreadable.append(os.path.basename(gen_path))
    entries.reverse()
    entries.sort(key=lambda e: e["ts"], reverse=True)
    total = len(entries)
    rows = entries[:limit]
    for number, row in enumerate(rows, 1):
        row["id"] = number
    return _json({"rows": rows, "total": total, "truncated": total > limit, "unreadable": unreadable,
                  "server_time": _iso(datetime.now())})


# ---------------------------------------------------------------------------
# Errors and warnings per database, from the same files (cached for a minute)
# ---------------------------------------------------------------------------
_counts_lock = threading.Lock()
_counts_cache = {"stamp": None, "at": 0.0, "value": {}}


def _files_stamp():
    parts = []
    for key, (label, path, backups, kind) in _files().items():
        for gen_path in _generations(path, backups, MAX_GENS):
            try:
                stat = os.stat(gen_path)
            except OSError:
                continue
            parts.append((gen_path, stat.st_size, int(stat.st_mtime)))
    return tuple(parts)


def _level_counts():
    """{db casefolded: {"err24","warn24","err7","warn7"}} over the last seven days, all files."""
    stamp = _files_stamp()
    with _counts_lock:
        if _counts_cache["stamp"] == stamp and time.time() - _counts_cache["at"] < COUNTS_TTL:
            return _counts_cache["value"]
    now = datetime.now()
    cut24 = (now - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S")
    cut7 = (now - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S")
    counts = {}
    for key, (label, path, backups, kind) in _files().items():
        for gen_path in _generations(path, backups, MAX_GENS):
            if not os.path.isfile(gen_path):
                continue
            try:
                entries = _PARSERS[kind](label, gen_path, "", False)
            except OSError as err:
                logger.error("portal admin: %s could not be counted: %s", gen_path, err)
                continue
            for entry in entries:
                if entry["ts"] < cut7 or entry["level"] not in ("ERROR", "WARNING"):
                    continue
                slot = counts.setdefault(entry["db"].casefold(),
                                         {"err24": 0, "warn24": 0, "err7": 0, "warn7": 0})
                tag = "err" if entry["level"] == "ERROR" else "warn"
                slot[tag + "7"] += 1
                if entry["ts"] >= cut24:
                    slot[tag + "24"] += 1
    with _counts_lock:
        _counts_cache.update(stamp=stamp, at=time.time(), value=counts)
    return counts


# ---------------------------------------------------------------------------
# The databases
# ---------------------------------------------------------------------------
@portal_bp.route("/api/portal/admin/databases", methods=["GET"])
@require_portal_admin
def admin_databases():
    try:
        conn = _connect()
        try:
            cur = conn.cursor(dictionary=True)
            cur.execute("SELECT DC, NumeUnitate, CF FROM Unitati ORDER BY DC")
            units = cur.fetchall()
            wanted = [u["DC"] for u in units] + [COMMON_DB, SOURCE_DB]
            marks = ",".join(["%s"] * len(wanted))

            cur.execute("SELECT DC, COUNT(*) AS n FROM Unitati_Utilizatori GROUP BY DC")
            users = {r["DC"].casefold(): r["n"] for r in cur.fetchall()}

            cur.execute("SELECT DC, MIN(AN) AS an_min, MAX(AN) AS an_max FROM Unitati_Ani GROUP BY DC")
            years = {r["DC"].casefold(): r for r in cur.fetchall()}

            cur.execute(
                "SELECT TABLE_SCHEMA AS db, COUNT(*) AS tables_n, "
                "SUM(COALESCE(DATA_LENGTH,0) + COALESCE(INDEX_LENGTH,0)) AS bytes_n, "
                "SUM(COALESCE(TABLE_ROWS,0)) AS rows_n, MAX(UPDATE_TIME) AS last_write "
                "FROM information_schema.TABLES WHERE TABLE_SCHEMA IN (%s) AND TABLE_TYPE = 'BASE TABLE' "
                "GROUP BY TABLE_SCHEMA" % marks, wanted)
            sizes = {r["db"].casefold(): r for r in cur.fetchall()}

            cur.execute(
                "SELECT TABLE_SCHEMA AS db, TABLE_NAME AS name, COALESCE(TABLE_ROWS,0) AS rows_n "
                "FROM information_schema.TABLES WHERE TABLE_SCHEMA IN (%s) AND TABLE_TYPE = 'BASE TABLE' "
                "ORDER BY TABLE_ROWS DESC" % marks, wanted)
            big = {}
            for r in cur.fetchall():
                slot = big.setdefault(r["db"].casefold(), [])
                if len(slot) < 3:
                    slot.append("%s (%s)" % (r["name"], format(int(r["rows_n"]), ",").replace(",", ".")))

            cur.execute(
                "SELECT DC, MAX(Moment) AS last_action, "
                "MAX(CASE WHEN Actiune IN ('LOGIN','PORTAL_LOGIN') AND (Rezultat IS NULL OR Rezultat='OK') "
                "THEN Moment END) AS last_login, "
                "COUNT(DISTINCT CASE WHEN Moment >= NOW() - INTERVAL 30 DAY THEN UN END) AS users_30d, "
                "SUM(CASE WHEN Actiune IN ('LOGIN','PORTAL_LOGIN') AND (Rezultat IS NULL OR Rezultat='OK') "
                "AND Moment >= NOW() - INTERVAL 30 DAY THEN 1 ELSE 0 END) AS logins_30d "
                "FROM Jurnal WHERE DC IS NOT NULL GROUP BY DC")
            activity = {r["DC"].casefold(): r for r in cur.fetchall()}

            schema, schema_ready = {}, True
            try:
                cur.execute(
                    "SELECT target_db, COUNT(*) AS pending, SUM(COALESCE(is_destructive,0)) AS destructive, "
                    "SUM(CASE WHEN error_msg IS NOT NULL THEN 1 ELSE 0 END) AS errors "
                    "FROM schema_diff_log WHERE executed_at IS NULL GROUP BY target_db")
                schema = {str(r["target_db"] or "").casefold(): r for r in cur.fetchall()}
            except mysql.connector.Error as err:
                if err.errno != 1146:            # 1146 = the table does not exist (schema_sync never ran)
                    raise
                schema_ready = False
        finally:
            if conn.is_connected():
                conn.close()
    except mysql.connector.Error as err:
        logger.error("portal admin: databases could not be read: %s", err)
        return _fail("DB_ERROR", "Informațiile despre baze nu au putut fi citite.", 500)

    counts = _level_counts()
    rows = []
    listed = [(u["DC"], u["NumeUnitate"], u["CF"]) for u in units] + [
        (COMMON_DB, "(baza comună: utilizatori, jurnal, înregistrări)", ""),
        (SOURCE_DB, "(baza sursă: șablonul unităților)", ""),
    ]
    for dc, name, cf in listed:
        key = dc.casefold()
        size, act, yrs = sizes.get(key), activity.get(key, {}), years.get(key, {})
        sch, cnt = schema.get(key, {}), counts.get(key, {})
        rows.append({
            "dc": dc, "name": name, "cf": cf,
            "exists": size is not None,
            "tables": int(size["tables_n"]) if size else 0,
            "size_mb": round(float(size["bytes_n"]) / 1048576, 1) if size else 0,
            "rows_est": int(size["rows_n"]) if size else 0,
            "big_tables": " · ".join(big.get(key, [])),
            "last_write": _iso(size["last_write"]) if size else None,
            "users": users.get(key, 0),
            "an_min": yrs.get("an_min"), "an_max": yrs.get("an_max"),
            "last_login": _iso(act.get("last_login")), "last_action": _iso(act.get("last_action")),
            "users_30d": int(act.get("users_30d") or 0), "logins_30d": int(act.get("logins_30d") or 0),
            "err24": cnt.get("err24", 0), "warn24": cnt.get("warn24", 0),
            "err7": cnt.get("err7", 0), "warn7": cnt.get("warn7", 0),
            "schema_pending": int(sch.get("pending") or 0),
            "schema_destructive": int(sch.get("destructive") or 0),
            "schema_errors": int(sch.get("errors") or 0),
        })
    return _json({"databases": rows, "schema_ready": schema_ready, "server_time": _iso(datetime.now())})


# ---------------------------------------------------------------------------
# The visitors of the presentation page
# ---------------------------------------------------------------------------
@portal_bp.route("/api/portal/admin/visits", methods=["GET"])
@require_portal_admin
def admin_visits():
    days = _int_arg("days", 30, 1, 3650)
    with_bots = request.args.get("bots") == "1"
    geo = geoip.status()
    rows = []
    try:
        conn = _connect()
        try:
            cur = conn.cursor(dictionary=True)
            cur.execute(
                "SELECT IdVizita, Prima, Ultima, IP, Tara, Mobil, Bot, Referrer, Ecran, Limba, UserAgent, "
                "DurataSec, Sectiuni FROM Vizite_Site WHERE Prima >= NOW() - INTERVAL %s DAY"
                + ("" if with_bots else " AND Bot = 0") + " ORDER BY Prima DESC LIMIT %s",
                (days, MAX_VISIT_ROWS))
            fetched = cur.fetchall()
        finally:
            if conn.is_connected():
                conn.close()
    except mysql.connector.Error as err:
        if err.errno == 1146:
            return _json({"rows": [], "table_missing": True, "geo": geo,
                          "retention_days": vizite_module._retention_days()})
        logger.error("portal admin: visits could not be read: %s", err)
        return _fail("DB_ERROR", "Vizitele nu au putut fi citite.", 500)

    for r in fetched:
        try:
            sections = json.loads(r["Sectiuni"] or "{}")
        except ValueError:
            sections = {}
        ordered = sorted(((k, v) for k, v in sections.items() if isinstance(v, int)),
                         key=lambda kv: kv[1], reverse=True)
        rows.append({
            "id": r["IdVizita"],
            "first": _iso(r["Prima"]), "last": _iso(r["Ultima"]),
            "ip": r["IP"] or "",
            "country": r["Tara"] or geoip.country(r["IP"]),
            "mobile": bool(r["Mobil"]), "bot": bool(r["Bot"]),
            "seconds": int(r["DurataSec"] or 0),
            "top_section": ordered[0][0] if ordered else "",
            "sections": [{"id": k, "seconds": v} for k, v in ordered],
            "referrer": r["Referrer"] or "", "screen": r["Ecran"] or "", "lang": r["Limba"] or "",
            "agent": r["UserAgent"] or "",
        })
    return _json({"rows": rows, "table_missing": False, "geo": geo, "truncated": len(rows) >= MAX_VISIT_ROWS,
                  "retention_days": vizite_module._retention_days(), "server_time": _iso(datetime.now())})
