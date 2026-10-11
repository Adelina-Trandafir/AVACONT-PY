"""
admin_logs.py -- the server log files as structured entries, for KBotAdmin (slice ME01-01).

READ-ONLY. Same files and the same line formats the former web admin page read,
with ONE difference on purpose: the `{...}` tag is read as key=value pairs, so a portal-session line
(`{u=<email> dc=<db>}`, no `s=`) is attributed to its user and database too.

  api_server.log / api_server_vba.log / schema_sync.log   one entry per header line (+ traceback lines)
  forexe_timing.log / asociere.log                        one entry per `====` block
  gunicorn error.log / help_feedback.log / one_time.log   raw lines (no common format)
  gunicorn access.log                                     one entry per request (only the last ACCESS_TAIL_BYTES are read)

Files are read newest generation first on disk (.1 .. .5 are the rotated copies); `gens` says how many
rotated copies are read besides the live file.
"""
import os
import re
from datetime import datetime, timedelta

MAX_DETAIL = 4000
ACCESS_TAIL_BYTES = 6 * 1024 * 1024   # the access log is huge: only its end is read

_RX_LINE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d{3} - (?P<lvl>[A-Z]+) - (?P<ip>\S+) - (?P<msg>.*)$")
_RX_TAG = re.compile(r"^\{(?P<kv>[^}]*)\} ?(?P<rest>.*)$", re.DOTALL)
_RX_KV = re.compile(r"(\w+)=(\S+)")
_RX_BLOCK_HEAD = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\.\d{3}\s+rulare\s+(?P<run>\d+)\s+\[(?P<label>[^\]]*)\]"
    r"\s+status\s+(?P<status>\S+)")
_RX_RAW_TS = re.compile(r"^(?P<ts>\d{4}[-/]\d{2}[-/]\d{2} \d{2}:\d{2}:\d{2})")
_RX_ACCESS = re.compile(
    r'^(?P<ip>\S+) \S+ \S+ \[(?P<ts>[^\]]+)\] "(?P<req>[^"]*)" (?P<status>\d{3}) (?P<size>\S+)'
    r'(?: "(?P<ref>[^"]*)" "(?P<ua>[^"]*)")?')
_SEPARATOR = "=" * 100
NO_DB = "-"

_LEVELS = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3, "CRITICAL": 4}


def _cfg(attr, default):
    try:
        import config
        return getattr(config, attr, None) or default
    except Exception:  # pragma: no cover - config is always there on the server
        return default


def files():
    """key -> (label, path, backups, kind)."""
    try:
        from utils import logger as server_logger
        server_path, vba_path, backups = (server_logger.SERVER_LOG_PATH, server_logger.VBA_LOG_PATH,
                                          server_logger.SERVER_LOG_BACKUPS)
    except Exception:
        server_path, vba_path, backups = "api_server.log", "api_server_vba.log", 5
    try:
        from utils import timing
        timing_path, timing_backups = timing.log_path(), timing.LOG_BACKUPS
    except Exception:
        timing_path, timing_backups = "forexe_timing.log", 5
    schema_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               "schema_sync", "schema_sync.log")
    app_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    error_path = os.environ.get("KBOT_GUNICORN_ERROR_LOG") or "/var/log/avacont/error.log"
    access_path = os.environ.get("KBOT_GUNICORN_ACCESS_LOG") or os.path.join(os.path.dirname(error_path), "access.log")
    return {
        "server": ("api_server.log", server_path, backups, "lines"),
        "vba": ("api_server_vba.log", vba_path, backups, "lines"),
        "timing": ("forexe_timing.log", timing_path, timing_backups, "blocks"),
        "asociere": ("asociere.log", os.environ.get("KBOT_ASOCIERE_LOG") or _cfg("ASOCIERE_LOG_PATH", "asociere.log"),
                     5, "blocks"),
        "schema": ("schema_sync.log", schema_path, 5, "lines"),
        "gunicorn": ("gunicorn error.log", error_path, 0, "raw"),
        "access": ("gunicorn access.log", access_path, 0, "access"),
        "feedback": ("help_feedback.log", os.path.join(app_root, "help_feedback.log"), 0, "raw"),
        "onetime": ("one_time.log", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "one_time", "one_time.log"),
                    0, "raw"),
    }


def generations(path, backups, gens):
    yield path
    for i in range(1, min(backups, gens) + 1):
        yield "%s.%d" % (path, i)


def _read_lines(path):
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        return handle.read().splitlines()


def _read_tail_lines(path, max_bytes):
    """The last `max_bytes` of a big file as lines (the first, cut, line is dropped)."""
    with open(path, "rb") as handle:
        size = os.fstat(handle.fileno()).st_size
        cut = size > max_bytes
        handle.seek(max(0, size - max_bytes))
        data = handle.read()
    lines = data.decode("utf-8", errors="replace").splitlines()
    return lines[1:] if cut and lines else lines


def _iso(ts):
    return ts.replace(" ", "T").replace("/", "-")


def _entry(label, ts, level, ip, user, db, session, msg, lines):
    text = "\n".join(lines)
    return {
        "ts": _iso(ts),
        "file": label,
        "level": level,
        "ip": ip,
        "user": user,
        "db": "" if db == NO_DB else db,
        "session": session,
        "msg": msg,
        "detail": text[:MAX_DETAIL] if len(lines) > 1 else "",
    }


def db_matches(entry_db, wanted):
    """`wanted`: '' = any database, '-' = none, else that database (case does not matter)."""
    if not wanted:
        return True
    if wanted == NO_DB:
        return entry_db in ("", NO_DB)
    return entry_db.casefold() == wanted.casefold()


def _parse_lines(label, path):
    out = []
    current = None
    body = []

    def close():
        lab, ts, lvl, ip, user, db, sess, first, lines = current
        out.append(_entry(lab, ts, lvl, ip, user, db, sess, first, lines + body))

    for line in _read_lines(path):
        head = _RX_LINE.match(line)
        if head is None:
            if current is not None:
                body.append(line)
            continue
        if current is not None:
            close()
        body = []
        message = head.group("msg")
        user = db = sess = ""
        rest = message
        tag = _RX_TAG.match(message)
        if tag is not None and "=" in tag.group("kv"):
            pairs = dict(_RX_KV.findall(tag.group("kv")))
            user, db, sess = pairs.get("u", ""), pairs.get("dc", ""), pairs.get("s", "")
            rest = tag.group("rest")
        first = rest.splitlines()[0] if rest else ""
        current = (label, head.group("ts"), head.group("lvl"), head.group("ip"), user, db, sess, first, [rest])
    if current is not None:
        close()
    return out


def _block_level(status, text):
    if status.startswith("5") or status.startswith("EXC"):
        return "ERROR"
    if status.startswith("4") or "RESPINS" in text:
        return "WARNING"
    return "INFO"


def _parse_blocks(label, path):
    out = []
    block = []
    for line in _read_lines(path) + [_SEPARATOR]:
        if line.strip() != _SEPARATOR:
            block.append(line)
            continue
        entry = _block_entry(label, block)
        if entry is not None:
            out.append(entry)
        block = []
    return out


def _block_entry(label, block):
    if not block:
        return None
    head = _RX_BLOCK_HEAD.match(block[0])
    if head is None:
        return None
    notes = dict(_RX_KV.findall(" ".join(block[1:4])))
    text = "\n".join(block)
    msg = "rulare %s [%s] status %s" % (head.group("run"), head.group("label"), head.group("status"))
    return _entry(label, head.group("ts"), _block_level(head.group("status"), text), "",
                  notes.get("user", ""), notes.get("dc", ""), notes.get("session", ""), msg, block)


def _raw_level(text):
    upper = text.upper()
    return "ERROR" if ("ERROR" in upper or "CRITICAL" in upper) else ("WARNING" if "WARN" in upper else "INFO")


def _parse_raw(label, path):
    """Lines that carry no time of their own (a traceback, a worker message) are folded into ONE entry
    and take the time of the first stamped line after them (the traceback is printed just before the
    line that says what happened), else of the one before, else the file's own time."""
    out = []
    stamp = datetime.fromtimestamp(os.stat(path).st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    last = ""
    pending = []     # untimed lines waiting for the next stamp

    def flush(ts):
        if not pending:
            return
        head = [x for x in pending if not x[:1].isspace()]
        msg = (head[-1] if head else pending[-1]).strip()
        out.append(_entry(label, ts, _raw_level(" ".join(pending)), "", "", "", "", msg, list(pending)))
        del pending[:]

    for line in _read_lines(path):
        if not line.strip():
            continue
        found = _RX_RAW_TS.match(line)
        if found is None:
            pending.append(line)
            continue
        ts = found.group("ts")
        flush(ts)
        last = ts
        out.append(_entry(label, ts, _raw_level(line), "", "", "", "", line, [line]))
    flush(last or stamp)
    out.sort(key=lambda e: e["ts"])
    return out


def _parse_access(label, path):
    """One entry per request: 5xx = ERROR, 4xx = WARNING, the rest INFO."""
    out = []
    for line in _read_tail_lines(path, ACCESS_TAIL_BYTES):
        found = _RX_ACCESS.match(line)
        if found is None:
            continue
        try:
            stamp = datetime.strptime(found.group("ts").split()[0], "%d/%b/%Y:%H:%M:%S").strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        status = int(found.group("status"))
        level = "ERROR" if status >= 500 else ("WARNING" if status >= 400 else "INFO")
        msg = "%s %s" % (status, found.group("req"))
        detail = [msg, "bytes: %s" % found.group("size"), "referer: %s" % (found.group("ref") or "-"), "agent: %s" % (found.group("ua") or "-")]
        out.append(_entry(label, stamp, level, found.group("ip"), "", "", "", msg, detail))
    return out


_PARSERS = {"lines": _parse_lines, "blocks": _parse_blocks, "raw": _parse_raw, "access": _parse_access}


def file_info():
    out = []
    for key, (label, path, backups, _kind) in files().items():
        exists = os.path.isfile(path)
        stat = os.stat(path) if exists else None
        out.append({
            "key": key, "label": label, "exists": exists,
            "size": stat.st_size if stat else 0,
            "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%dT%H:%M:%S") if stat else None,
            "rotated": sum(1 for i in range(1, backups + 1) if os.path.isfile("%s.%d" % (path, i))),
        })
    return out


def read_entries(file_key="", db="", gens=1, since=None):
    """Entries in time order (oldest first). `since` is an ISO timestamp lower bound."""
    entries = []
    unreadable = []
    for key, (label, path, backups, kind) in files().items():
        if file_key and key != file_key:
            continue
        if kind == "access" and not file_key:
            continue    # only on request: it is huge and 4xx would swamp the warning counts
        for gen_path in generations(path, backups, gens):
            if not os.path.isfile(gen_path):
                continue
            try:
                parsed = _PARSERS[kind](label, gen_path)
            except OSError:
                unreadable.append(os.path.basename(gen_path))
                continue
            entries.extend(e for e in parsed if db_matches(e["db"], db) and (since is None or e["ts"] >= since))
    entries.sort(key=lambda e: e["ts"])
    return entries, unreadable


def level_counts(hours=24, gens=1):
    """{db casefolded: {"err", "warn"}} over the last `hours` hours, all files ('' = no database)."""
    cut = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S")
    entries, _bad = read_entries("", "", gens, since=cut)
    counts = {}
    for entry in entries:
        if _LEVELS.get(entry["level"], 1) < _LEVELS["WARNING"]:
            continue
        slot = counts.setdefault(entry["db"].casefold(), {"err": 0, "warn": 0})
        slot["err" if _LEVELS[entry["level"]] >= _LEVELS["ERROR"] else "warn"] += 1
    return counts
