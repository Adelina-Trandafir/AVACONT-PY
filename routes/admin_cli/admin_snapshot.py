"""
admin_snapshot.py -- the server half of the local encrypted backup (slice ME02-04). READ ONLY.

    snap-run --db D [--state-file P] [--mode fast|full] [--page-rows N] [--page-bytes N]

One run reads ONE unit database inside ONE consistent-snapshot transaction (what mysqldump --single-transaction
does), so the copy never holds half of an operation. It prints a stream of JSON lines (record type in "t"), then the
usual {"ok": true, "data": ...} envelope; a failure prints {"ok": false, "error": ...} instead and no "end" record.

A table is cut into PAGES by primary-key ranges: page k covers (hi of page k-1, hi of page k]; the first page has
lo = null and the last one hi = null, so the ranges always touch and cover every key, present or future. Each page
carries CHECK VALUES computed by the database itself ("count:a:b", the row count and two XORs of per-row MD5s).
The client sends the pages of its previous copy (--state-file); a page whose check value did not change is answered
with just its description ("same": true) and its rows are NOT read; a changed page is read again and re-cut.

Two check values per page:
    sum   the quick one. In a table that has a trusted "last changed" column (ON UPDATE CURRENT_TIMESTAMP or DataModif)
          a big binary column (PDF, picture) counts by its LENGTH, so the database does not read it again every day.
    full  counts big binary columns by the MD5 of their bytes. It is what mode "full" compares, to catch a file that was
          replaced by one of the same length without touching the "last changed" column.
In every other table the two are the same string.

Records:
    begin     {"t":"begin","db":D,"server_time":..,"version":..,"mode":..,"tables":N}
    table     {"t":"table","name":T,"ddl":..,"cols":[{"n","dt","bin"}],"pk":[..],"modcol":..|null}
    page      {"t":"page","table":T,"lo":[..]|null,"hi":[..]|null,"n":rows,"sum":..,"full":..,"same":bool,"z":base64(zlib(rows json))}
    tableend  {"t":"tableend","name":T,"pages":P,"rows":R,"changed":C}
    extra     {"t":"extra","kind":"view|trigger|routine|event","name":..,"ddl":..}
    end       {"t":"end","tables":N,"pages":P,"changed":C,"rows":R,"seconds":S}

Rows are lists of text (NULL = null); binary columns are base64. Generated columns are left out (the database
recomputes them). Nothing here writes to the database; the only file touched is the client's state file, which is
deleted after it is read (it must sit inside backup/).
Known limit: a row whose columns together are longer than max_allowed_packet makes CONCAT_WS return NULL, and such a
row would not count in the check value (its page is still read whole the first time).
"""
import base64
import json
import os
import re
import sys
import time
import zlib
from utils.clock import utc_now

import mysql.connector

from routes.admin_cli.admin_backup import BACKUP_DIR
from routes.admin_cli.admin_common import AdminError, _iso, _q, _resolve_db, _rows
from routes.schema_sync.schema_common import SchemaSyncError, connect

PAGE_ROWS_DEFAULT = 500
PAGE_BYTES_DEFAULT = 4 * 1024 * 1024
BIG_BLOBS = {"tinyblob", "blob", "mediumblob", "longblob"}
BIN_TYPES = BIG_BLOBS | {"binary", "varbinary", "bit", "geometry", "point", "linestring", "polygon", "multipoint",
                         "multilinestring", "multipolygon", "geometrycollection"}
MODCOL_RX = re.compile(r"ON UPDATE (?:current_timestamp|now)", re.I)


def _emit(record):
    sys.stdout.buffer.write(json.dumps(record, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8"))
    sys.stdout.buffer.write(b"\n")
    sys.stdout.buffer.flush()


# ---------------------------------------------------------------------------
# What a table looks like
# ---------------------------------------------------------------------------
def _describe(conn, db, table):
    """(ddl, columns, pk, modcol). Columns exclude generated ones; pk is [] when the table has no usable key."""
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SHOW CREATE TABLE %s.%s" % (_q(db), _q(table)))
        ddl = re.sub(r"\s+AUTO_INCREMENT=\d+", "", cur.fetchone()["Create Table"])
    finally:
        cur.close()
    generated, modcol = set(), None
    for line in ddl.splitlines():
        m = re.match(r"\s*`((?:[^`]|``)+)`\s", line)
        if not m:
            continue
        name = m.group(1).replace("``", "`")
        if "GENERATED ALWAYS AS" in line.upper():
            generated.add(name)
        if modcol is None and (MODCOL_RX.search(line) or name == "DataModif"):
            modcol = name
    cols = []
    for r in _rows(conn, "SELECT COLUMN_NAME AS n, DATA_TYPE AS dt FROM information_schema.COLUMNS "
                         "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s ORDER BY ORDINAL_POSITION", (db, table)):
        if r["n"] in generated:
            continue
        dt = (r["dt"] or "").lower()
        cols.append({"n": r["n"], "dt": dt, "bin": dt in BIN_TYPES})
    names = {c["n"]: c for c in cols}
    pk = [r["n"] for r in _rows(conn, "SELECT COLUMN_NAME AS n FROM information_schema.KEY_COLUMN_USAGE "
                                      "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s AND CONSTRAINT_NAME = 'PRIMARY' "
                                      "ORDER BY ORDINAL_POSITION", (db, table))]
    if any(c not in names or names[c]["bin"] for c in pk):
        pk = []                      # a generated or binary key cannot be compared as text: the table becomes one page
    if modcol is not None and modcol not in names:
        modcol = None
    return ddl, cols, pk, modcol


def _row_text_expr(cols, use_length):
    """One text value per row that changes when any stored value changes. Big binary columns count by LENGTH when
    use_length is on, by the MD5 of their bytes otherwise."""
    parts = []
    for c in cols:
        q = _q(c["n"])
        if c["bin"]:
            if use_length and c["dt"] in BIG_BLOBS:
                parts.append("COALESCE(CAST(LENGTH(%s) AS CHAR),'~N')" % q)
            else:
                parts.append("COALESCE(MD5(%s),'~N')" % q)
        else:
            parts.append("COALESCE(CAST(%s AS CHAR),'~N')" % q)
    return "CONCAT_WS(CHAR(31)," + ",".join(parts) + ")"


def _range_where(pk, lo, hi, after=None):
    """WHERE text and params for the keys in (lo, hi] (and > after when given)."""
    if not pk:
        return "", []
    tup = "(" + ",".join(_q(c) for c in pk) + ")"
    ph = "(" + ",".join(["%s"] * len(pk)) + ")"
    conds, params = [], []
    start = after if after is not None else lo
    if start is not None:
        conds.append("%s > %s" % (tup, ph))
        params += list(start)
    if hi is not None:
        conds.append("%s <= %s" % (tup, ph))
        params += list(hi)
    return (" WHERE " + " AND ".join(conds)) if conds else "", params


class _Table:
    """Everything the page logic needs to know about one table."""

    def __init__(self, conn, db, name):
        self.conn, self.db, self.name = conn, db, name
        self.ddl, self.cols, self.pk, self.modcol = _describe(conn, db, name)
        self.pk_idx = [next(i for i, c in enumerate(self.cols) if c["n"] == k) for k in self.pk]
        self.big = any(c["dt"] in BIG_BLOBS for c in self.cols)
        # the quick check differs from the full one only here: big binary columns and a trusted "last changed" column
        self.two_checks = self.big and self.modcol is not None

    def check(self, lo, hi, full):
        """(row count, 'count:a:b') of the rows with keys in (lo, hi]."""
        where, params = _range_where(self.pk, lo, hi)
        use_length = self.two_checks and not full
        sql = ("SELECT COUNT(*) AS n, BIT_XOR(CAST(CONV(LEFT(m,16),16,10) AS UNSIGNED)) AS a, "
               "BIT_XOR(CAST(CONV(SUBSTR(m,17,16),16,10) AS UNSIGNED)) AS b "
               "FROM (SELECT MD5(%s) AS m FROM %s.%s%s) x") % (_row_text_expr(self.cols, use_length), _q(self.db), _q(self.name), where)
        r = _rows(self.conn, sql, params)[0]
        return int(r["n"]), "%d:%x:%x" % (int(r["n"]), int(r["a"] or 0), int(r["b"] or 0))

    def rows(self, lo, hi, batch):
        """Rows with keys in (lo, hi] in key order, as (key, encoded row). Keyset batches: each batch is fully fetched
        before the next query, so the one connection (and its snapshot) can be used in between."""
        sel = ",".join(_q(c["n"]) for c in self.cols)
        if not self.pk:
            cur = self.conn.cursor(raw=True)
            try:
                cur.execute("SELECT %s FROM %s.%s" % (sel, _q(self.db), _q(self.name)))
                fetched = cur.fetchall()
            finally:
                cur.close()
            for raw in fetched:
                yield None, _encode_row(raw, self.cols)
            return
        after = None
        order = ",".join(_q(c) for c in self.pk)
        while True:
            where, params = _range_where(self.pk, lo, hi, after)
            cur = self.conn.cursor(raw=True)
            try:
                cur.execute("SELECT %s FROM %s.%s%s ORDER BY %s LIMIT %d" % (sel, _q(self.db), _q(self.name), where, order, batch), params)
                fetched = cur.fetchall()
            finally:
                cur.close()
            if not fetched:
                return
            for raw in fetched:
                enc = _encode_row(raw, self.cols)
                key = [enc[i] for i in self.pk_idx]
                yield key, enc
                after = key
            if len(fetched) < batch:
                return


def _encode_row(row, cols):
    out = []
    for value, col in zip(row, cols):
        if value is None:
            out.append(None)
        elif col["bin"]:
            out.append(base64.b64encode(bytes(value)).decode("ascii"))
        elif isinstance(value, str):
            out.append(value)
        else:
            out.append(bytes(value).decode("utf-8"))
    return out


def _row_bytes(row):
    return sum(len(v) for v in row if v is not None) + 4 * len(row)


# ---------------------------------------------------------------------------
# One table
# ---------------------------------------------------------------------------
def _usable_prev(pk, pages):
    """The previous pages are usable only when they cover the whole key space without gaps."""
    if not pages or not all(isinstance(p.get("sum"), str) for p in pages):
        return False
    if not pk:
        return len(pages) == 1 and pages[0].get("lo") is None and pages[0].get("hi") is None
    if pages[0].get("lo") is not None or pages[-1].get("hi") is not None:
        return False
    for before, after in zip(pages, pages[1:]):
        if before.get("hi") is None or before.get("hi") != after.get("lo"):
            return False
    return all(len(p["lo"] or pk) == len(pk) and len(p["hi"] or pk) == len(pk) for p in pages)


def _emit_page(table, lo, hi, rows, counts):
    n, sum_quick = table.check(lo, hi, False)
    sum_full = table.check(lo, hi, True)[1] if table.two_checks else sum_quick
    if n != len(rows):
        raise AdminError("Tabela %s s-a schimbat in timpul citirii (pagina are %d randuri, nu %d)." % (table.name, n, len(rows)))
    rec = {"t": "page", "table": table.name, "lo": lo, "hi": hi, "n": n, "sum": sum_quick, "full": sum_full, "same": False}
    rec["z"] = base64.b64encode(zlib.compress(json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), 3)).decode("ascii")
    _emit(rec)
    counts["pages"] += 1
    counts["changed"] += 1
    counts["rows"] += n


def _cut_range(table, lo, hi, page_rows, page_bytes, batch, counts):
    """Reads the keys in (lo, hi] and emits them as pages. The last page of the range ends exactly at hi, so the
    ranges of a table keep touching; an empty range stays as one empty page."""
    chunk, chunk_bytes, chunk_lo, last_key = [], 0, lo, None
    for key, enc in table.rows(lo, hi, batch):
        if table.pk and chunk and (len(chunk) >= page_rows or chunk_bytes >= page_bytes):
            _emit_page(table, chunk_lo, last_key, chunk, counts)
            chunk, chunk_bytes, chunk_lo = [], 0, last_key
        chunk.append(enc)
        chunk_bytes += _row_bytes(enc)
        last_key = key
    _emit_page(table, chunk_lo, hi, chunk, counts)


def _do_table(conn, db, name, prev, mode, page_rows, page_bytes, totals):
    table = _Table(conn, db, name)
    _emit({"t": "table", "name": name, "ddl": table.ddl, "cols": table.cols, "pk": table.pk, "modcol": table.modcol})
    batch = 8 if table.big else max(50, min(page_rows, 1000))
    old = []
    if prev is not None and prev.get("pk") == table.pk and _usable_prev(table.pk, prev.get("pages") or []):
        old = prev["pages"]
    if not old:
        old = [{"lo": None, "hi": None, "sum": None, "full": None}]   # nothing usable from before: cut the whole key space
    counts = {"pages": 0, "changed": 0, "rows": 0}
    full = mode == "full"
    for page in old:
        lo, hi = page.get("lo"), page.get("hi")
        if page.get("sum") is None:                   # nothing known about this range: read it, no need to check it first
            _cut_range(table, lo, hi, page_rows, page_bytes, batch, counts)
            continue
        n, current = table.check(lo, hi, full)
        known = page.get("full") if full else page.get("sum")
        if known is not None and current == known:
            if full:
                sum_quick, sum_full = page["sum"], current
            else:
                sum_quick, sum_full = current, (page.get("full") if table.two_checks else current)
            _emit({"t": "page", "table": name, "lo": lo, "hi": hi, "n": n, "sum": sum_quick, "full": sum_full, "same": True})
            counts["pages"] += 1
            counts["rows"] += n
        else:
            _cut_range(table, lo, hi, page_rows, page_bytes, batch, counts)
    _emit({"t": "tableend", "name": name, "pages": counts["pages"], "rows": counts["rows"], "changed": counts["changed"]})
    for k in counts:
        totals[k] += counts[k]


# ---------------------------------------------------------------------------
# Views, triggers, routines, events
# ---------------------------------------------------------------------------
def _extras(conn, db):
    jobs = [
        ("view", "SELECT TABLE_NAME AS n FROM information_schema.VIEWS WHERE TABLE_SCHEMA = %s", "SHOW CREATE VIEW %s.%s", "Create View"),
        ("trigger", "SELECT TRIGGER_NAME AS n FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA = %s", "SHOW CREATE TRIGGER %s.%s", "SQL Original Statement"),
        ("event", "SELECT EVENT_NAME AS n FROM information_schema.EVENTS WHERE EVENT_SCHEMA = %s", "SHOW CREATE EVENT %s.%s", "Create Event"),
    ]
    for kind, listing, show, key in jobs:
        for r in _rows(conn, listing, (db,)):
            got = _rows(conn, show % (_q(db), _q(r["n"])))
            _emit({"t": "extra", "kind": kind, "name": r["n"], "ddl": (got[0].get(key) or "") if got else ""})
    for r in _rows(conn, "SELECT ROUTINE_NAME AS n, ROUTINE_TYPE AS ty FROM information_schema.ROUTINES WHERE ROUTINE_SCHEMA = %s", (db,)):
        got = _rows(conn, "SHOW CREATE %s %s.%s" % (r["ty"], _q(db), _q(r["n"])))
        key = "Create Procedure" if r["ty"] == "PROCEDURE" else "Create Function"
        _emit({"t": "extra", "kind": "routine", "name": r["n"], "ddl": (got[0].get(key) or "") if got else ""})


# ---------------------------------------------------------------------------
# The verb
# ---------------------------------------------------------------------------
def _read_state(path):
    """The client's previous pages, {table: {"pk": [..], "pages": [..]}}; the file is deleted once read."""
    if not path:
        return {}
    real = os.path.realpath(path)
    allowed = os.path.realpath(BACKUP_DIR)
    if not real.startswith(allowed + os.sep) or not os.path.isfile(real):
        raise AdminError("Fisierul de stare nu este in dosarul backup de pe server.")
    try:
        with open(real, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as err:
        raise AdminError("Fisierul de stare nu a putut fi citit: %s" % err)
    finally:
        try:
            os.unlink(real)
        except OSError:
            pass
    return data.get("tables") or {}


def cmd_snap_run(args):
    if hasattr(os, "nice"):
        try:
            os.nice(10)                                # a backup must not slow the application down
        except OSError:
            pass
    if args.mode not in ("fast", "full"):
        raise AdminError("Mod necunoscut.")
    page_rows = max(20, min(int(args.page_rows), 5000))
    page_bytes = max(256 * 1024, min(int(args.page_bytes), 32 * 1024 * 1024))
    state = _read_state(args.state_file)
    conn = connect()
    try:
        dbs = _resolve_db(conn, args.db)
        if len(dbs) != 1:
            raise AdminError("Copia locala se face pe o singura baza.")
        db = dbs[0]
        started = time.time()
        conn.start_transaction(consistent_snapshot=True, isolation_level="REPEATABLE READ")
        names = [r["n"] for r in _rows(conn, "SELECT TABLE_NAME AS n FROM information_schema.TABLES "
                                             "WHERE TABLE_SCHEMA = %s AND TABLE_TYPE = 'BASE TABLE' ORDER BY TABLE_NAME", (db,))]
        version = _rows(conn, "SELECT VERSION() AS v")[0]["v"]
        _emit({"t": "begin", "db": db, "server_time": _iso(utc_now()), "version": version, "mode": args.mode, "tables": len(names)})
        totals = {"pages": 0, "changed": 0, "rows": 0}
        for name in names:
            _do_table(conn, db, name, state.get(name), args.mode, page_rows, page_bytes, totals)
        _extras(conn, db)
        conn.rollback()                                # nothing was written; this just ends the snapshot
        seconds = round(time.time() - started, 1)
        _emit({"t": "end", "tables": len(names), "pages": totals["pages"], "changed": totals["changed"], "rows": totals["rows"],
               "seconds": seconds})
        return {"db": db, "tables": len(names), "pages": totals["pages"], "changed": totals["changed"], "rows": totals["rows"],
                "seconds": seconds, "mode": args.mode}
    except (mysql.connector.Error, SchemaSyncError) as err:
        raise AdminError("Citirea bazei a esuat: %s" % err)
    finally:
        if conn.is_connected():
            conn.close()


def register(sub):
    p = sub.add_parser("snap-run")
    p.add_argument("--db", required=True)
    p.add_argument("--state-file", default="")
    p.add_argument("--mode", default="fast")
    p.add_argument("--page-rows", type=int, default=PAGE_ROWS_DEFAULT)
    p.add_argument("--page-bytes", type=int, default=PAGE_BYTES_DEFAULT)
    p.set_defaults(fn=cmd_snap_run)
