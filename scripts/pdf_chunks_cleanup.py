# scripts/pdf_chunks_cleanup.py
"""
Slice 0078-05 -- maintenance of the shared PDF chunk store (FX_PDF_BUCATI), run BY HAND.

Two jobs, per unit database (AVACONT_SURSA + every `NNN_*`, or one --db):

  default      Delete chunks that no document references any more. A re-signature replaces
               a document's chunk list, so the chunks only the old version used are left
               behind. Chunks younger than --min-age-minutes (default 60) are never deleted:
               an upload in progress may have written its chunks and not yet committed the
               list that points at them.

  --convert    Rewrite rows still holding the whole file (`Continut`, written before 0078-05)
               as chunk lists. Each row is rebuilt from its new chunks and checked against its
               stored Sha256 BEFORE the row is switched; any mismatch rolls that row back and
               is reported. One transaction per row.

--dry-run shows what would happen and changes nothing.

Usage (on the VPS):
    /root/AVACONT/.venv/bin/python3 scripts/pdf_chunks_cleanup.py --dry-run
    /root/AVACONT/.venv/bin/python3 scripts/pdf_chunks_cleanup.py --convert --db 000_DEMO
"""
import argparse
import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mysql.connector  # noqa: E402

from config import DB_CONFIG_NEW  # noqa: E402
from routes.schema_sync.schema_common import (FORBIDDEN_TARGETS, SOURCE_DB,  # noqa: E402
                                              UNIT_DB_RE)
from utils import pdf_chunks  # noqa: E402

CHUNK_TABLE = "FX_PDF_BUCATI"
DOC_TABLES = ("FX_DDF_PDF", "FX_ORD_PDF")
BATCH = 200


class CleanupError(Exception):
    pass


def connect():
    cfg = dict(DB_CONFIG_NEW)
    cfg.setdefault("charset", "utf8mb4")
    cfg["autocommit"] = False
    try:
        return mysql.connector.connect(**cfg)
    except mysql.connector.Error as exc:
        raise CleanupError(
            f"Conectare eșuată la {cfg.get('host')}:{cfg.get('port')} — {exc}") from exc


def q(name: str) -> str:
    if "`" in name:
        raise CleanupError(f"Identificator invalid: {name!r}")
    return f"`{name}`"


def list_databases(cur) -> list:
    cur.execute("SELECT SCHEMA_NAME FROM information_schema.SCHEMATA ORDER BY SCHEMA_NAME")
    names = [r[0] for r in cur.fetchall()]
    units = [n for n in names if UNIT_DB_RE.match(n or "") and n not in FORBIDDEN_TARGETS]
    return ([SOURCE_DB] if SOURCE_DB in names else []) + units


def has_chunk_store(cur, db) -> bool:
    cur.execute(
        "SELECT COUNT(*) FROM information_schema.COLUMNS "
        " WHERE TABLE_SCHEMA = %s "
        "   AND ((TABLE_NAME IN ('FX_DDF_PDF', 'FX_ORD_PDF') AND COLUMN_NAME = 'Bucati') "
        "     OR (TABLE_NAME = %s AND COLUMN_NAME = 'Continut'))", (db, CHUNK_TABLE))
    return int(cur.fetchone()[0]) == 3


def referenced_digests(cur, db) -> set:
    refs = set()
    for table in DOC_TABLES:
        cur.execute(f"SELECT Bucati FROM {q(db)}.{q(table)} WHERE Bucati IS NOT NULL")
        for (packed,) in cur.fetchall():
            refs.update(pdf_chunks.unpack_list(packed))
    return refs


def cleanup(conn, db, min_age, dry_run, say):
    cur = conn.cursor()
    refs = referenced_digests(cur, db)
    cur.execute(
        f"SELECT Sha256, LENGTH(Continut) FROM {q(db)}.{q(CHUNK_TABLE)} "
        f" WHERE DataCreare < NOW() - INTERVAL %s MINUTE", (min_age,))
    orphans = [(bytes(d), n) for d, n in cur.fetchall() if bytes(d) not in refs]
    total = sum(n for _, n in orphans)
    say(f"  {db:<16} {len(refs):>6} bucăți folosite, {len(orphans):>6} orfane ({total} octeți)")
    if dry_run or not orphans:
        return len(orphans)
    for i in range(0, len(orphans), BATCH):
        part = [d for d, _ in orphans[i:i + BATCH]]
        marks = ", ".join(["%s"] * len(part))
        cur.execute(f"DELETE FROM {q(db)}.{q(CHUNK_TABLE)} WHERE Sha256 IN ({marks})", tuple(part))
    conn.commit()
    return len(orphans)


def _fetch(cur, db, digests):
    found = {}
    for i in range(0, len(digests), BATCH):
        part = digests[i:i + BATCH]
        marks = ", ".join(["%s"] * len(part))
        cur.execute(f"SELECT Sha256, Continut FROM {q(db)}.{q(CHUNK_TABLE)} "
                    f" WHERE Sha256 IN ({marks})", tuple(part))
        for d, blob in cur.fetchall():
            found[bytes(d)] = bytes(blob)
    return found


def convert(conn, db, dry_run, say):
    cur = conn.cursor()
    converted = failed = 0
    for table in DOC_TABLES:
        cur.execute(f"SELECT IDPDF FROM {q(db)}.{q(table)} WHERE Continut IS NOT NULL")
        ids = [r[0] for r in cur.fetchall()]
        say(f"  {db:<16} {table}: {len(ids)} rânduri cu fișier întreg")
        if dry_run:
            continue
        for idpdf in ids:
            try:
                cur.execute(f"SELECT Sha256, Continut FROM {q(db)}.{q(table)} "
                            f" WHERE IDPDF = %s FOR UPDATE", (idpdf,))
                sha, content = cur.fetchone()
                data = bytes(content)
                parts = pdf_chunks.split(data)
                for digest, chunk in parts:
                    cur.execute(
                        f"INSERT INTO {q(db)}.{q(CHUNK_TABLE)} "
                        f"       (Sha256, Dimensiune, Continut, DataCreare) "
                        f"VALUES (%s, %s, %s, NOW()) ON DUPLICATE KEY UPDATE Sha256 = Sha256",
                        (digest, len(chunk), pdf_chunks.compress(chunk)))
                digests = [d for d, _ in parts]
                back = pdf_chunks.rebuild(digests, lambda ds: _fetch(cur, db, ds))
                if hashlib.sha256(back).hexdigest() != sha:
                    raise CleanupError(f"IDPDF={idpdf}: fișierul refăcut nu corespunde sumei")
                cur.execute(f"UPDATE {q(db)}.{q(table)} SET Bucati = %s, Continut = NULL "
                            f" WHERE IDPDF = %s", (pdf_chunks.pack_list(digests), idpdf))
                conn.commit()
                converted += 1
            except Exception as exc:
                conn.rollback()
                failed += 1
                say(f"    EȘEC {table} IDPDF={idpdf}: {exc}")
    return converted, failed


def build_parser():
    p = argparse.ArgumentParser(description="Întreținerea bucăților PDF (slice 0078-05).")
    p.add_argument("--dry-run", action="store_true", help="arată, nu modifică nimic")
    p.add_argument("--db", help="o singură bază (implicit: AVACONT_SURSA + toate unitățile)")
    p.add_argument("--convert", action="store_true",
                   help="transformă rândurile cu fișier întreg în liste de bucăți")
    p.add_argument("--min-age-minutes", type=int, default=60,
                   help="bucățile mai noi de atât nu se șterg (implicit 60)")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    say = print
    try:
        conn = connect()
        try:
            cur = conn.cursor()
            if args.db:
                if args.db in FORBIDDEN_TARGETS and args.db != SOURCE_DB:
                    raise CleanupError(f"Bază interzisă: {args.db}")
                targets = [args.db]
            else:
                targets = list_databases(cur)
            for db in targets:
                if not has_chunk_store(cur, db):
                    say(f"  {db:<16} fără DDL 0078 (sql/0078_fx_pdf_bucati.sql) — sărită")
                    continue
                if args.convert:
                    done, bad = convert(conn, db, args.dry_run, say)
                    if not args.dry_run:
                        say(f"  {db:<16} convertite {done}, eșuate {bad}")
                cleanup(conn, db, args.min_age_minutes, args.dry_run, say)
            return 0
        finally:
            conn.close()
    except CleanupError as exc:
        print(f"EROARE: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
