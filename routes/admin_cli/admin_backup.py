"""
admin_backup.py -- the dump files of KBotAdmin's "Copii" page (slice ME01, extended in ME02-02).

    backups [--db D]                     the dump files and schema plans, each dump with the reason it was made
    backup-text --path P                 the text of one dump / plan file (first 2 MB)
    backup --db D|ALL --reason T         WRITE  dump one unit database, or all of them, to backup/

ALL means every unit database EXCEPT 000_DEMO (the operator copies that one by hand, by name, when she wants it).
AVACONT_COMUN and AVACONT_SURSA are never dumped from here (_resolve_db never returns them).
Every dump is journaled in AVACONT_COMUN.Jurnal (action ADMIN_BACKUP, target = the file name, details = the reason).
"""
import glob
import gzip
import os
import re
from utils.clock import utc_from_timestamp, utc_now

import mysql.connector

from routes.admin_cli.admin_common import AdminError, _iso, _journal, _quiet_logger, _resolve_db, _rows
from routes.schema_sync.schema_common import COMMON_DB, SchemaSyncError, connect

BACKUP_DIR = "backup"
BACKUP_PLAN_DIR = "schema_diff"
PLAN_SCAN_BYTES = 8 * 1024 * 1024      # how much of a schema plan is searched for the database name
TEXT_MAX_BYTES = 2 * 1024 * 1024       # how much of a file the text view returns
EXCLUDED_FROM_ALL = {"000_demo"}       # casefolded; only a copy asked for by name may touch these
REASON_MIN = 5
REASON_MAX = 2000
JOURNAL_ACTION = "ADMIN_BACKUP"


def _plan_mentions(path, db):
    """True when a schema plan file names the database (plans cover several databases at once)."""
    needle = db.casefold()
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return needle in handle.read(PLAN_SCAN_BYTES).casefold()
    except OSError:
        return False


def _reasons_by_file(names):
    """file name -> the reason journaled when that dump was made (the newest entry wins)."""
    if not names:
        return {}
    conn = connect()
    try:
        marks = ",".join(["%s"] * len(names))
        rows = _rows(conn, "SELECT Tinta, Detalii FROM `%s`.`Jurnal` WHERE Actiune = %%s AND Tinta IN (%s) ORDER BY Id"
                     % (COMMON_DB, marks), [JOURNAL_ACTION] + list(names))
    finally:
        if conn.is_connected():
            conn.close()
    return {r["Tinta"]: (r["Detalii"] or "") for r in rows}


def cmd_backups(args):
    """The dump files and schema plans. With --db (not ALL) only those of that database remain:
    dumps are named <db>_<stamp>.sql, plans are kept when their text names the database."""
    wanted = "" if args.db in ("", "ALL") else args.db
    out = []
    for pattern in (os.path.join(BACKUP_DIR, "*.sql*"), os.path.join(BACKUP_PLAN_DIR, "*.sql")):
        for path in glob.glob(pattern):
            is_dump = path.startswith(BACKUP_DIR)
            name = os.path.basename(path)
            if wanted:
                if is_dump:
                    if not name.casefold().startswith(wanted.casefold() + "_"):
                        continue
                elif not _plan_mentions(path, wanted):
                    continue
            stat = os.stat(path)
            out.append({"path": os.path.abspath(path), "name": name, "size": stat.st_size,
                        "modified": utc_from_timestamp(stat.st_mtime).strftime("%Y-%m-%dT%H:%M:%S"),
                        "kind": "copie" if is_dump else "plan schema", "reason": ""})
    out.sort(key=lambda r: r["modified"], reverse=True)
    out = out[:300]
    reasons = _reasons_by_file([r["name"] for r in out if r["kind"] == "copie"])
    for row in out:
        row["reason"] = reasons.get(row["name"], "")
    return {"files": out}


def cmd_backup_text(args):
    """The text of one dump or plan file (the first TEXT_MAX_BYTES). The path must be one the listing returned."""
    real = os.path.realpath(args.path)
    allowed = [os.path.realpath(BACKUP_DIR), os.path.realpath(BACKUP_PLAN_DIR)]
    if not any(real.startswith(folder + os.sep) for folder in allowed) or not os.path.isfile(real):
        raise AdminError("Fisierul nu este o copie sau un plan de pe server.")
    size = os.path.getsize(real)
    opener = gzip.open if real.endswith(".gz") else open
    with opener(real, "rb") as handle:
        raw = handle.read(TEXT_MAX_BYTES + 1)
    truncated = len(raw) > TEXT_MAX_BYTES
    text = raw[:TEXT_MAX_BYTES].decode("utf-8", errors="replace")
    return {"name": os.path.basename(real), "size": size, "text": text, "truncated": truncated,
            "shown_bytes": min(len(raw), TEXT_MAX_BYTES)}


def _clean_reason(text):
    """The reason with whitespace folded to single spaces; refused when it is too short or too long."""
    reason = re.sub(r"\s+", " ", text or "").strip()
    if len(reason) < REASON_MIN:
        raise AdminError("Motivul copiei lipseste sau e prea scurt (cel putin %d caractere)." % REASON_MIN)
    if len(reason) > REASON_MAX:
        raise AdminError("Motivul copiei e prea lung (cel mult %d caractere)." % REASON_MAX)
    return reason


def _journal_one(item, reason):
    """Writes the journal row of one finished dump on its own connection (a dump can outlive a connection).
    Returns '' when written, else the readable reason it was not."""
    try:
        conn = connect()
        try:
            details = reason if not item["error"] else "%s | EROARE: %s" % (reason, item["error"])
            _journal(conn, JOURNAL_ACTION, item["db"], item["name"] or item["db"], details[:2000],
                     "OK" if not item["error"] else "EROARE")
        finally:
            if conn.is_connected():
                conn.close()
    except (mysql.connector.Error, SchemaSyncError) as err:
        return "Copia s-a facut, dar nu s-a putut scrie in jurnal: %s" % err
    return ""


def cmd_backup(args):
    """Dumps one unit database, or every unit database (ALL, minus 000_DEMO), with one shared reason.
    A database that fails does not stop the others; the answer lists every one."""
    from routes.schema_sync.schema_execute import dump_database
    reason = _clean_reason(args.reason)
    everything = (args.db or "").upper() == "ALL"
    conn = connect()
    try:
        dbs = _resolve_db(conn, args.db)
    finally:
        if conn.is_connected():
            conn.close()
    if everything:
        dbs = [d for d in dbs if d.casefold() not in EXCLUDED_FROM_ALL]
    if not dbs:
        raise AdminError("Nu exista nicio baza de unitate de copiat.")
    logger = _quiet_logger()
    results = []
    for db in dbs:
        item = {"db": db, "path": "", "name": "", "size": 0, "error": "", "journal_error": ""}
        try:
            path = dump_database(db, BACKUP_DIR, logger)
            item["path"] = os.path.abspath(path)
            item["name"] = os.path.basename(path)
            item["size"] = os.path.getsize(path)
        except (SchemaSyncError, OSError) as err:
            item["error"] = str(err)
        item["journal_error"] = _journal_one(item, reason)
        results.append(item)
    return {"reason": reason, "results": results, "made": sum(1 for r in results if not r["error"]),
            "failed": sum(1 for r in results if r["error"]), "total_size": sum(r["size"] for r in results),
            "time": _iso(utc_now())}


def register(sub):
    p = sub.add_parser("backups")
    p.add_argument("--db", default="ALL")
    p.set_defaults(fn=cmd_backups)

    p = sub.add_parser("backup-text")
    p.add_argument("--path", required=True)
    p.set_defaults(fn=cmd_backup_text)

    p = sub.add_parser("backup")
    p.add_argument("--db", required=True)
    p.add_argument("--reason", default="")
    p.set_defaults(fn=cmd_backup)
