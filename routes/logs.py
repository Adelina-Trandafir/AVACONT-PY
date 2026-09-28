# routes/logs.py
"""
Slice 0089: the server journals, read back for the operator who wrote them.

Two read-only routes, both behind the K-BOT bearer guard:

    GET /api/logs/server?sessions=3   api_server.log   (+ .1 .. .5)
    GET /api/logs/timing?sessions=3   forexe_timing.log (+ .1 .. .5)

WHOSE LINES. Only the caller's. Every line written inside an authenticated
request carries `{s=<token8> u=<user> dc=<db>}` (utils/logger.SessionTagFilter)
and every timing block carries `session= user= dc=` on its header
(utils/timing._session_notes). A line or block without that mark -- written
before slice 0089, at startup, or on the legacy X-Api-Key path -- belongs to
nobody and is never returned. The user comes from the session, never from the
query string: a client cannot ask for somebody else's journal.

WHICH LINES (server log). Only `[forexe]` / `[forexe.xxx]` lines, plus the
traceback lines under them. Everything else in api_server.log is the old
Access/VBA path or the server's own bookkeeping.

HOW MANY. The last N sessions (= logins) of the caller, most recently active
first, N from the query (default 3, 1..50). Files are read newest generation
first; once N sessions are known, older generations are read only while they
still hold lines of those sessions. A session lives at most 30 minutes, so in
practice that is one extra file at most.

WHAT COMES BACK. The text itself, in file order, with the session mark
removed -- the client parses it with the same parser as a local file -- plus a
summary of the sessions it covers.
"""
import logging
import os
import re
from datetime import datetime

from flask import Blueprint, request, g

from routes.auth.guard import require_session, json_response
from utils import logger as server_logger
from utils import timing

logger = logging.getLogger(__name__)

logs_bp = Blueprint("logs", __name__)

DEFAULT_SESSIONS = 3
MAX_SESSIONS = 50
# The answer is cut (oldest first) past this many characters. A bad day of
# tracebacks must not become a 60 MB HTTP body.
MAX_OUTPUT_CHARS = 4 * 1024 * 1024

_RX_SERVER_HEADER = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) - (?P<lvl>[A-Z]+) - (?P<ip>\S+) - (?P<msg>.*)$")
_RX_SESSION_TAG = re.compile(r"^\{s=(?P<s>\S+) u=(?P<u>\S+) dc=(?P<dc>\S+)\} (?P<rest>.*)$", re.DOTALL)
_RX_FOREXE = re.compile(r"^\[forexe[\].]")

_TIMING_SEPARATOR = "=" * 100
_RX_TIMING_HEADER = re.compile(r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\.\d{3}\s+rulare\s")
_RX_NOTE = re.compile(r"(\w+)=(\S+)")


class _Record:
    """One entry that belongs to the caller: its session and its lines."""
    __slots__ = ("session", "dc", "ts", "level", "lines")

    def __init__(self, session, dc, ts, level, lines):
        self.session = session
        self.dc = dc
        self.ts = ts
        self.level = level
        self.lines = lines


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@logs_bp.route("/api/logs/server", methods=["GET"])
@require_session
def logs_server():
    return _answer("server", server_logger.SERVER_LOG_PATH,
                   server_logger.SERVER_LOG_BACKUPS, _parse_server_file)


@logs_bp.route("/api/logs/timing", methods=["GET"])
@require_session
def logs_timing():
    return _answer("timing", timing.log_path(), timing.LOG_BACKUPS, _parse_timing_file)


def _answer(kind, path, backups, parser):
    try:
        wanted = _sessions_param()
        user = (g.session.username or "").strip().casefold()
        records, sessions, truncated = _collect(path, backups, parser, user, wanted)
        text = "\n".join("\n".join(r.lines) for r in records)
        return json_response({
            "kind": kind,
            "text": text,
            "truncated": truncated,
            "sessions": sessions,
            "sessions_requested": wanted,
            "server_time": datetime.now().astimezone().isoformat(),
        }, 200)
    except Exception as ex:
        logger.exception("[logs] %s: %s", kind, ex)
        return json_response({"error": "Jurnalul serverului nu a putut fi citit: %s" % ex}, 500)


def _sessions_param():
    raw = request.args.get("sessions", "")
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_SESSIONS
    return max(1, min(MAX_SESSIONS, n))


# ---------------------------------------------------------------------------
# Walking the generations
# ---------------------------------------------------------------------------
def _generations(path, backups):
    """Newest first: the live file, then .1 .. .N."""
    yield path
    for i in range(1, backups + 1):
        yield "%s.%d" % (path, i)


def _collect(path, backups, parser, user, wanted):
    """
    Returns (records in file order, session summaries newest first, truncated).
    `order` is filled walking every file BACKWARDS, so its first N entries are
    the N sessions with the most recent activity.
    """
    order = []
    kept = []                     # per-file record lists, newest file first
    for gen_path in _generations(path, backups):
        if not os.path.isfile(gen_path):
            continue
        recs = parser(gen_path, user)
        if len(order) >= wanted:
            chosen = set(order[:wanted])
            if not any(r.session in chosen for r in recs):
                break
            kept.append(recs)
            continue
        kept.append(recs)
        for r in reversed(recs):
            if r.session not in order:
                order.append(r.session)

    chosen = set(order[:wanted])
    # Newest first so the cut drops the OLDEST lines, then back to file order.
    picked = []
    size = 0
    truncated = False
    for recs in kept:
        for r in reversed(recs):
            if r.session not in chosen:
                continue
            cost = sum(len(line) + 1 for line in r.lines)
            if size + cost > MAX_OUTPUT_CHARS:
                truncated = True
                break
            size += cost
            picked.append(r)
        if truncated:
            break
    picked.reverse()
    return picked, _summaries(picked, order[:wanted]), truncated


def _summaries(records, order):
    by_session = {}
    for r in records:
        s = by_session.get(r.session)
        if s is None:
            s = {"session": r.session, "dc": r.dc, "first": r.ts, "last": r.ts,
                 "entries": 0, "errors": 0, "warnings": 0}
            by_session[r.session] = s
        s["last"] = r.ts
        s["entries"] += 1
        if r.level == "ERROR":
            s["errors"] += 1
        elif r.level == "WARNING":
            s["warnings"] += 1
    return [by_session[s] for s in order if s in by_session]


def _read_lines(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read().splitlines()


# ---------------------------------------------------------------------------
# api_server.log
# ---------------------------------------------------------------------------
def _parse_server_file(path, user):
    """
    The caller's `[forexe...]` entries of one file. A header line opens an
    entry; every line that is not a header (a traceback) belongs to the entry
    above it, and is dropped with it when that entry is not the caller's.
    """
    out = []
    current = None                # the caller's open entry, or None to skip
    for line in _read_lines(path):
        m = _RX_SERVER_HEADER.match(line)
        if m is None:
            if current is not None:
                current.lines.append(line)
            continue
        current = None
        t = _RX_SESSION_TAG.match(m.group("msg"))
        if t is None or t.group("u").casefold() != user:
            continue
        rest = t.group("rest")
        if not _RX_FOREXE.match(rest):
            continue
        clean = "%s - %s - %s - %s" % (m.group("ts"), m.group("lvl"), m.group("ip"), rest)
        current = _Record(t.group("s"), t.group("dc"), m.group("ts")[:19],
                          m.group("lvl"), [clean])
        out.append(current)
    return out


# ---------------------------------------------------------------------------
# forexe_timing.log
# ---------------------------------------------------------------------------
def _parse_timing_file(path, user):
    """
    The caller's blocks of one file. Each block is ONE log record (written in
    one call), so rotation never splits it. The separator lines are left out:
    the client knows a block by its header line.
    """
    out = []
    block = []
    for line in _read_lines(path) + [_TIMING_SEPARATOR]:
        if line.strip() == _TIMING_SEPARATOR:
            rec = _timing_record(block, user)
            if rec is not None:
                out.append(rec)
            block = []
            continue
        block.append(line)
    return out


def _timing_record(block, user):
    if len(block) < 2:
        return None
    m = _RX_TIMING_HEADER.match(block[0])
    if m is None:
        return None
    notes = dict(_RX_NOTE.findall(block[1]))
    session = notes.get("session")
    if not session or (notes.get("user") or "").casefold() != user:
        return None
    status = block[0].rsplit("status", 1)[-1].strip()
    if status.startswith("5") or status.startswith("EXC"):
        level = "ERROR"
    elif status.startswith("4"):
        level = "WARNING"
    else:
        level = "INFO"
    return _Record(session, notes.get("dc", "-"), m.group("ts"), level, list(block))
