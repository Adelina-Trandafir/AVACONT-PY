"""
schema_sync.py -- generate and execute in one command.

Three ways to run it:

    python schema_sync.py --view
        Work out the changes, write them to a .sql file, show the
        summary, execute nothing.

    python schema_sync.py
        Work out the changes, show them, ask, execute if you agree.
        This is the default.

    python schema_sync.py --run
        Work out the changes and execute without asking. Destructive
        statements are still refused unless --allow-destructive is
        given, and still require the typed DA.

    python schema_sync.py --drop-legacy
        Remove proc_SchemaDiff_DDL, proc_SchemaDiff_CreateTable and
        proc_ExecuteSchemaDiff. Nothing in this package uses them, and
        left in place they can be called directly, bypassing the
        destructive gate.
"""

import argparse
import os
import sys
from datetime import datetime

import mysql.connector

from .schema_common import (OUT_DIR, SchemaSyncError, check_prerequisites,
                            connect, discover_targets, drop_legacy_procedures,
                            ensure_control_table, parse_targets, setup_logging,
                            summarise, verify_targets)
from .schema_execute import (confirm_destructive, execute_rows,
                             refuse_destructive, render_sql, report,
                             take_backups)
from .schema_generate import generate


def _ask(prompt: str, choices: tuple) -> str:
    options = "/".join(choices)
    while True:
        try:
            answer = input(f"{prompt} [{options}] > ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return choices[-1]
        if answer in choices:
            return answer
        print(f"Răspunsuri acceptate: {options}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Generează și execută sincronizarea de schemă.")
    parser.add_argument("--view", action="store_true",
                        help="Doar generează și arată. Nu execută nimic.")
    parser.add_argument("--run", action="store_true",
                        help="Execută fără a întreba.")
    parser.add_argument("--mode", choices=["SAFE", "FORCE"], default="SAFE")
    parser.add_argument("--out", default=None,
                        help=f"Fișierul .sql. Implicit: "
                             f"{OUT_DIR}/schema_diff_<dată>.sql")
    parser.add_argument("--allow-destructive", action="store_true")
    parser.add_argument("--backup-dir", default="backup")
    parser.add_argument("--skip-backup", action="store_true")
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument("--no-reset", action="store_true")
    parser.add_argument("--drop-legacy", action="store_true",
                        help="Șterge procedurile stocate înlocuite.")
    parser.add_argument("--targets", default=None)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    if args.view and args.run:
        print("--view și --run se exclud reciproc.", file=sys.stderr)
        return 1

    logger = setup_logging(args.verbose)
    conn = None
    try:
        conn = connect()
        ensure_control_table(conn, logger)
        check_prerequisites(conn, logger)

        if args.drop_legacy:
            drop_legacy_procedures(conn, logger)

        targets = (verify_targets(conn, parse_targets(args.targets), logger)
                   if args.targets else discover_targets(conn, logger))

        rows = generate(conn, targets, args.mode, logger,
                        reset=not args.no_reset)
        if not rows:
            logger.info("Schemele sunt deja sincronizate. Nimic de făcut.")
            return 0

        out_path = args.out or os.path.join(
            OUT_DIR, f"schema_diff_{datetime.now():%Y%m%d_%H%M%S}.sql")
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(render_sql(rows))
        logger.info("Instrucțiunile scrise în: %s", os.path.abspath(out_path))

        destructive = [r for r in rows if r["is_destructive"]]
        print()
        print(summarise(rows))
        print()
        if destructive:
            affected = sorted({r["target_db"] for r in destructive})
            print(f"  {len(destructive)} DISTRUCTIVE, pe: "
                  f"{', '.join(affected)}")
            print()

        if args.view:
            logger.info("--view: nu s-a executat nimic. Citiți %s.", out_path)
            return 0

        if not args.run:
            print(f"Instrucțiunile complete sunt în {out_path}.")
            if _ask("Executați acum?", ("da", "nu")) != "da":
                logger.info("Anulat. Nimic nu s-a executat. Rândurile rămân "
                            "în așteptare.")
                return 0

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
