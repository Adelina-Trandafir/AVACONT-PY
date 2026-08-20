"""
schema_execute.py -- run what schema_generate.py recorded.

Three things the old stored procedure could not do:

  1. is_destructive is REAL. If any pending row is destructive and
     --allow-destructive was not given, NOTHING runs. A refusal, not a
     skip: many destructive rows are the first half of a pair (DROP
     FOREIGN KEY then ADD CONSTRAINT), and running only the second half
     fails with errno 1826 or 1061 and leaves the schema worse than
     untouched.

  2. A dump is taken before destructive work and verified on disk. No
     mysqldump, no destructive run.

  3. The MariaDB error NUMBER is recorded. Telling 3780 (FK collation
     mismatch) from 1071 (key too long) from 1061 (duplicate key) is the
     whole diagnostic value.

There is no rollback. DDL causes an implicit commit in MariaDB, so a
transaction around these statements would be theatre. Recovery is the
dump, restored by hand, deliberately.

Usage:
    python schema_execute.py --dry-run
    python schema_execute.py --out changes.sql
    python schema_execute.py
    python schema_execute.py --allow-destructive --backup-dir /volume1/backup
"""

import argparse
import os
import shlex
import shutil
import subprocess
import sys
import time
from datetime import datetime

import mysql.connector

from config import DB_CONFIG
from .schema_common import (CONTROL_DB, SchemaSyncError, check_prerequisites,
                            connect, discover_targets, ensure_control_table,
                            fetch_pending, parse_targets, setup_logging,
                            summarise, verify_targets)


# ---------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------

def find_dump_tool() -> str:
    """mysqldump, or MariaDB's newer mariadb-dump. None if neither."""
    for name in ("mysqldump", "mariadb-dump"):
        path = shutil.which(name)
        if path:
            return path
    return None


def dump_database(db_name: str, backup_dir: str, logger) -> str:
    """Dump one database to a timestamped file. Returns the path.

    A dump that exists but is empty is worse than no dump, because it
    invites false confidence -- so the size is checked.
    """
    tool = find_dump_tool()
    if not tool:
        raise SchemaSyncError(
            "mysqldump / mariadb-dump nu a fost găsit. Fără copie de "
            "siguranță nu execut operații distructive.\n"
            "  Instalați clientul MariaDB, sau faceți copia manual și "
            "rulați cu --skip-backup, pe răspunderea dumneavoastră.")

    os.makedirs(backup_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(backup_dir, f"{db_name}_{stamp}.sql")

    cmd = [tool,
           f"--host={DB_CONFIG['host']}",
           f"--port={DB_CONFIG['port']}",
           f"--user={DB_CONFIG['user']}",
           f"--password={DB_CONFIG['password']}",
           "--databases", db_name,
           "--routines", "--triggers", "--events",
           "--single-transaction",
           "--default-character-set=utf8mb4"]

    safe = " ".join(shlex.quote(c) for c in cmd
                    if not c.startswith("--password"))
    logger.info("Copie de siguranță: %s --password=*** > %s", safe, out_path)

    try:
        with open(out_path, "wb") as fh:
            proc = subprocess.run(cmd, stdout=fh, stderr=subprocess.PIPE,
                                  check=False)
    except OSError as exc:
        raise SchemaSyncError(
            f"Copia pentru `{db_name}` nu a putut fi scrisă în {out_path}: "
            f"{exc}") from exc

    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", errors="replace").strip()
        raise SchemaSyncError(
            f"mysqldump a eșuat pentru `{db_name}` (cod {proc.returncode}): "
            f"{err}")

    size = os.path.getsize(out_path)
    if size < 1024:
        raise SchemaSyncError(
            f"Copia `{out_path}` are doar {size} octeți — aproape sigur "
            f"incompletă. Opresc.")

    logger.info("Copie OK: %s (%.1f MB)", out_path, size / (1024 * 1024))
    return out_path


def restore_hint(db_name: str, dump_path: str) -> str:
    """The exact commands to put a database back. Printed, never run."""
    host, port, user = DB_CONFIG["host"], DB_CONFIG["port"], DB_CONFIG["user"]
    return (
        f"  Pentru a reveni la starea dinainte pentru `{db_name}`:\n"
        f"    1. Verificați că nimeni nu este conectat la baza respectivă.\n"
        f"    2. mysql -h {host} -P {port} -u {user} -p "
        f"-e \"DROP DATABASE \\`{db_name}\\`\"\n"
        f"    3. mysql -h {host} -P {port} -u {user} -p < {dump_path}\n"
        f"  ATENȚIE: restaurarea pierde tot ce s-a scris după copie.")


# ---------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------

def render_sql(rows: list) -> str:
    """The pending batch as a readable .sql file, in execution order."""
    out = ["-- " + "-" * 60,
           f"-- schema_diff — {len(rows)} instrucțiuni, în ordinea execuției",
           f"-- generat: {datetime.now().isoformat(timespec='seconds')}",
           "-- " + "-" * 60, ""]
    current = None
    for r in rows:
        key = (r["target_db"], r["object_type"], r["action_type"])
        if key != current:
            current = key
            mark = "   *** DISTRUCTIV ***" if r["is_destructive"] else ""
            out.append("")
            out.append(f"-- === {r['target_db']} — {r['object_type']} "
                       f"{r['action_type']}{mark} ===")
        label = r["table_name"] or ""
        if r["object_name"]:
            label += f".{r['object_name']}"
        out.append(f"-- [id {r['id']}] {label}")
        out.append(r["ddl_sql"])
    out.append("")
    return "\n".join(out)


# ---------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------

def refuse_destructive(destructive: list, logger) -> int:
    logger.error("%d instrucțiuni sunt DISTRUCTIVE. Nu execut NIMIC.",
                 len(destructive))
    logger.error(
        "Refuz total, nu sărire selectivă: multe sunt primul pas dintr-o "
        "pereche (DROP FOREIGN KEY apoi ADD CONSTRAINT). Sărirea unuia "
        "singur produce errno 1826 / 1061 la pasul următor.")
    for r in destructive[:20]:
        logger.error("  id=%s  %s.%s  %s %s", r["id"], r["target_db"],
                     r["table_name"] or "", r["object_type"], r["action_type"])
    if len(destructive) > 20:
        logger.error("  ... și încă %d.", len(destructive) - 20)
    logger.error("Reluați cu --allow-destructive dacă sunt intenționate. "
                 "Rândurile rămân în așteptare.")
    return 2


def confirm_destructive(destructive: list, logger) -> bool:
    affected = sorted({r["target_db"] for r in destructive})
    print()
    print(f"Urmează {len(destructive)} operații DISTRUCTIVE asupra: "
          f"{', '.join(affected)}")
    print("DDL nu se poate anula. Tastați exact  DA  pentru a continua.")
    try:
        answer = input("> ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        answer = ""
    if answer != "DA":
        logger.info("Anulat de operator. Nimic nu s-a executat.")
        return False
    return True


def take_backups(destructive, backup_dir, skip, logger) -> dict:
    if skip:
        logger.warning("--skip-backup: operații distructive FĂRĂ copie de "
                       "siguranță.")
        return {}
    dumps = {}
    for db in sorted({r["target_db"] for r in destructive}):
        dumps[db] = dump_database(db, backup_dir, logger)
    return dumps


# ---------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------

def execute_rows(conn, rows, logger, stop_on_error=True) -> tuple:
    """Run each statement, recording the outcome. Nothing is swallowed."""
    ok = failed = 0
    first_failure = None

    for idx, r in enumerate(rows, 1):
        cur = conn.cursor()
        started = time.time()
        try:
            logger.debug("[%d/%d] id=%s %s", idx, len(rows), r["id"],
                         r["ddl_sql"])
            cur.execute(r["ddl_sql"])
            _mark(conn, r["id"], None)
            ok += 1
            logger.info("[%d/%d] OK  %s %s %s.%s (%.2fs)", idx, len(rows),
                        r["object_type"], r["action_type"], r["target_db"],
                        r["table_name"] or "", time.time() - started)
        except mysql.connector.Error as exc:
            errno = getattr(exc, "errno", None)
            detail = getattr(exc, "msg", None) or str(exc)
            msg = f"[errno={errno}] {detail}"
            _mark(conn, r["id"], msg)
            failed += 1
            if first_failure is None:
                first_failure = r
            logger.error("[%d/%d] EȘEC %s %s %s.%s — %s", idx, len(rows),
                         r["object_type"], r["action_type"], r["target_db"],
                         r["table_name"] or "", msg)
            logger.error("    SQL: %s", r["ddl_sql"])
            if stop_on_error:
                logger.error("Opresc după prima eroare. Restul de %d "
                             "instrucțiuni rămân neexecutate și se reiau la "
                             "următoarea rulare.", len(rows) - idx)
                break
        finally:
            cur.close()

    return ok, failed, first_failure


def _mark(conn, row_id, error_msg) -> None:
    cur = conn.cursor()
    try:
        cur.execute(f"UPDATE `{CONTROL_DB}`.`schema_diff_log` "
                    f"SET executed_at = NOW(), error_msg = %s WHERE id = %s",
                    (error_msg, row_id))
    finally:
        cur.close()


def report(rows, ok, failed, dumps, first_failure, logger) -> None:
    print()
    logger.info("Rezultat: %d reușite, %d eșuate, %d neatinse.",
                ok, failed, len(rows) - ok - failed)
    if failed and dumps:
        print()
        db = first_failure["target_db"] if first_failure else None
        for name, path in dumps.items():
            if db is None or name == db:
                print(restore_hint(name, path))


# ---------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Execută instrucțiunile din schema_diff_log.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--out", default=None)
    parser.add_argument("--allow-destructive", action="store_true")
    parser.add_argument("--backup-dir", default="backup")
    parser.add_argument("--skip-backup", action="store_true")
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument("--targets", default=None)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    logger = setup_logging(args.verbose)
    conn = None
    try:
        conn = connect()
        ensure_control_table(conn, logger)
        check_prerequisites(conn, logger)
        targets = (verify_targets(conn, parse_targets(args.targets), logger)
                   if args.targets else discover_targets(conn, logger))

        rows = fetch_pending(conn, targets)
        if not rows:
            logger.info("Nimic de executat. Rulați întâi schema_generate.py.")
            return 0

        print()
        print(summarise(rows))
        print()

        if args.out:
            os.makedirs(os.path.dirname(os.path.abspath(args.out)),
                        exist_ok=True)
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write(render_sql(rows))
            logger.info("Scris în %s", os.path.abspath(args.out))

        if args.dry_run:
            logger.info("--dry-run: nu s-a executat nimic.")
            return 0

        destructive = [r for r in rows if r["is_destructive"]]
        if destructive and not args.allow_destructive:
            return refuse_destructive(destructive, logger)

        dumps = {}
        if destructive:
            dumps = take_backups(destructive, args.backup_dir,
                                 args.skip_backup, logger)
            if not confirm_destructive(destructive, logger):
                return 3

        ok, failed, first_failure = execute_rows(
            conn, rows, logger, stop_on_error=not args.continue_on_error)
        report(rows, ok, failed, dumps, first_failure, logger)
        return 0 if failed == 0 else 1

    except SchemaSyncError as exc:
        logger.error("%s", exc)
        return 1
    except mysql.connector.Error as exc:
        logger.error("Eroare MariaDB: [%s] %s",
                     getattr(exc, "errno", "?"), exc)
        return 1
    finally:
        if conn is not None and conn.is_connected():
            conn.close()


if __name__ == "__main__":
    sys.exit(main())
