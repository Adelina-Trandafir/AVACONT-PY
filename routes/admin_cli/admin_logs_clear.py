"""
admin_logs_clear.py -- empties the server log files for KBotAdmin (Loguri page, button «Goleste»).

The ONLY writer of the log files in admin_cli; admin_logs.py stays read-only.

  keep = "all"   -> the file is emptied and its rotated copies (.1 .. .N) are deleted
  keep = "day"   -> only the last 24 hours stay
  keep = "week"  -> only the last 7 days stay

The live file is rewritten IN PLACE (same inode, truncated), because gunicorn and the loggers keep it open in
append mode: a new file would leave them writing into a deleted one. Lines are cut by record: a line that carries
a time starts a record, the lines without one (a traceback, the body of a `====` block) belong to the record
before them. The files are written in time order, so the first record at or after the cut-off is where the
kept part begins. A rotated copy is deleted when its last write is older than the cut-off, otherwise it is kept
whole (it may hold lines newer than the cut-off).
"""
import os
import re
from datetime import timedelta
from utils.clock import parse_access_time, utc_from_timestamp, utc_now

from routes.admin_cli import admin_logs

KEEP_ALL = "all"
KEEP_DAY = "day"
KEEP_WEEK = "week"
_KEEP_SPAN = {KEEP_DAY: timedelta(days=1), KEEP_WEEK: timedelta(days=7)}

_RX_STAMP = re.compile(r"^(\d{4}[-/]\d{2}[-/]\d{2})[ T](\d{2}:\d{2}:\d{2})")
_RX_ACCESS_STAMP = re.compile(r"^\S+ \S+ \S+ \[([^\]]+)\]")


class ClearError(Exception):
    """A refusal the operator can read."""


def _stamp(line, kind):
    """The time of a line as 'YYYY-MM-DD HH:MM:SS', or '' when the line carries none."""
    if kind == "access":
        found = _RX_ACCESS_STAMP.match(line)
        if found is None:
            return ""
        try:
            return parse_access_time(found.group(1)).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            return ""
    found = _RX_STAMP.match(line)
    return "%s %s" % (found.group(1).replace("/", "-"), found.group(2)) if found else ""


def _kept_from(path, kind, cut):
    """Byte offset of the first record at or after `cut`; the size of the file when there is none."""
    offset = 0
    with open(path, "rb") as handle:
        for raw in handle:
            stamp = _stamp(raw.decode("utf-8", errors="replace"), kind)
            if stamp and stamp >= cut:
                return offset
            offset += len(raw)
    return offset


def _rewrite_in_place(path, start):
    """Keeps path[start:] and drops the rest, in the same file. Returns (bytes before, bytes after)."""
    with open(path, "r+b") as handle:
        size = os.fstat(handle.fileno()).st_size
        handle.seek(min(start, size))
        tail = handle.read()
        handle.seek(0)
        handle.write(tail)
        handle.truncate(len(tail))
    return size, len(tail)


def _clear_file(key, label, path, backups, kind, keep, cut):
    row = {"key": key, "label": label, "before": 0, "after": 0, "deleted_copies": 0}
    if os.path.isfile(path):
        if keep == KEEP_ALL:
            row["before"], row["after"] = _rewrite_in_place(path, os.path.getsize(path))
        else:
            row["before"], row["after"] = _rewrite_in_place(path, _kept_from(path, kind, cut))
    for index in range(1, max(0, backups) + 1):
        copy = "%s.%d" % (path, index)
        if not os.path.isfile(copy):
            continue
        if keep == KEEP_ALL or utc_from_timestamp(os.path.getmtime(copy)).strftime("%Y-%m-%d %H:%M:%S") < cut:
            row["before"] += os.path.getsize(copy)
            os.remove(copy)
            row["deleted_copies"] += 1
        else:
            size = os.path.getsize(copy)
            row["before"] += size
            row["after"] += size
    return row


def clear(keep, file_key=""):
    """Empties one log file (file_key) or all of them. Raises ClearError for an unknown option."""
    if keep != KEEP_ALL and keep not in _KEEP_SPAN:
        raise ClearError("Optiune necunoscuta pentru golire: %s." % keep)
    cut = "" if keep == KEEP_ALL else (utc_now() - _KEEP_SPAN[keep]).strftime("%Y-%m-%d %H:%M:%S")
    known = admin_logs.files()
    if file_key and file_key not in known:
        raise ClearError("Fisier de jurnal necunoscut: %s." % file_key)
    rows = []
    for key, (label, path, backups, kind) in known.items():
        if file_key and key != file_key:
            continue
        rows.append(_clear_file(key, label, path, backups, kind, keep, cut))
    return {"keep": keep, "cut": cut, "files": rows,
            "freed": sum(r["before"] - r["after"] for r in rows)}
