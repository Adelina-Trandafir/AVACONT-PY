"""
schema_generate.py -- work out what needs to change, and record it.

Reads AVACONT_SURSA and each target out of information_schema, compares
them in Python, and writes the resulting statements into
schema_diff_log. Executes no DDL of its own.

Usage:
    python schema_generate.py --mode SAFE
    python schema_generate.py --mode FORCE --targets 000_DEMO,018_GRRS
    python schema_generate.py --mode SAFE --no-reset

Options:
    --mode      SAFE (additive plus collation repair) or FORCE (also
                modifies and drops).
    --no-reset  Keep previously generated, unexecuted rows. By default
                they are cleared first, so a second run does not queue
                the same work twice.
    --targets   Override discovery. Default: every distinct DbName in
                AVACONT_COMUN.CAI that exists on the server.
"""

import argparse
import json
import os
import sys
from datetime import datetime

import mysql.connector

from .schema_common import (CONTROL_DB, OUT_DIR, SchemaSyncError,
                            check_prerequisites, connect, discover_targets,
                            ensure_control_table, fetch_pending, parse_targets,
                            setup_logging, summarise, verify_targets)
from .schema_diff import build_diff


def clear_pending(conn, logger) -> int:
    """Delete unexecuted rows. Executed rows are the history; kept."""
    cur = conn.cursor()
    try:
        cur.execute(f"DELETE FROM {_tbl()} WHERE executed_at IS NULL")
        n = cur.rowcount
    finally:
        cur.close()
    if n:
        logger.info(
            "Curățate %d instrucțiuni rămase neexecutate din rulările "
            "anterioare — planul valabil este cel calculat ACUM; dacă noua "
            "comparație nu le mai generează, ele nu mai erau necesare.", n)
    return n


def _tbl() -> str:
    return f"`{CONTROL_DB}`.`schema_diff_log`"


def persist(conn, statements: list, mode: str, logger) -> None:
    """Write the statements into schema_diff_log in one batch."""
    if not statements:
        return
    rows = [(s.target_db, s.table_name, s.object_name, s.object_type,
             s.action_type, s.ddl_sql, mode, int(s.is_destructive),
             s.priority, s.error_msg)
            for s in statements]
    cur = conn.cursor()
    try:
        cur.executemany(
            f"INSERT INTO {_tbl()} "
            f"(target_db, table_name, object_name, object_type, action_type, "
            f" ddl_sql, sync_mode, is_destructive, priority, error_msg) "
            f"VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", rows)
    finally:
        cur.close()
    logger.info("Scrise %d instrucțiuni în schema_diff_log.", len(rows))


def write_blocks_report(blocks: list, logger) -> str:
    """Write the blocking data as JSON. Returns the path, or None.

    The log lines name three example values, which is enough to know that
    something is wrong and not enough to fix it. This file names every
    offending value, with the primary keys of the rows carrying it and the
    SELECT that lists them.
    """
    if not blocks:
        return None

    payload = {
        "generat": datetime.now().isoformat(timespec="seconds"),
        "blocaje": len(blocks),
        "explicatie": (
            "Fiecare intrare este o cheie străină care NU poate fi creată. "
            "«valori» conține valorile din baza țintă care nu au corespondent "
            "în tabelul referit; «chei_primare» arată exact rândurile care le "
            "poartă. Corectați datele, apoi reluați sincronizarea."),
        "chei_blocate": blocks,
    }

    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(
        OUT_DIR, f"blocaje_{datetime.now():%Y%m%d_%H%M%S}.json")
    with open(path, "w", encoding="utf-8") as fh:
        # default=str: a blocking value can be a date or a Decimal, and a
        # report that crashes on one row is worth nothing.
        json.dump(payload, fh, ensure_ascii=False, indent=2, default=str)

    logger.warning("%d chei nu se pot crea din cauza datelor. Lista completă: "
                   "%s", len(blocks), os.path.abspath(path))
    return path


def generate(conn, targets, mode, logger, reset=True) -> list:
    """Full generation pass. Returns the pending rows as stored."""
    ensure_control_table(conn, logger)

    # The comparison runs BEFORE the old rows are cleared. Cleared first,
    # a comparison that then fails half-way would leave the control table
    # empty: the previous plan deleted, the new one never written, and
    # nothing at all to look at afterwards. Computing first keeps the old
    # rows until there is something to put in their place.
    blocks = []
    statements = build_diff(conn, targets, mode, logger, blocks)

    if reset:
        clear_pending(conn, logger)

    blocked = [s for s in statements if s.error_msg]
    for s in blocked:
        logger.error("%s.%s — %s", s.target_db, s.table_name, s.error_msg)

    write_blocks_report(blocks, logger)

    persist(conn, statements, mode, logger)
    return fetch_pending(conn, targets)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Generează diferențele de schemă în schema_diff_log.")
    parser.add_argument("--mode", choices=["SAFE", "FORCE"], default="SAFE")
    parser.add_argument("--no-reset", action="store_true")
    parser.add_argument("--targets", default=None)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    logger = setup_logging(args.verbose)
    conn = None
    try:
        conn = connect()
        check_prerequisites(conn, logger)
        targets = (verify_targets(conn, parse_targets(args.targets), logger)
                   if args.targets else discover_targets(conn, logger))

        rows = generate(conn, targets, args.mode, logger,
                        reset=not args.no_reset)

        print()
        print(summarise(rows))
        print()
        destructive = [r for r in rows if r["is_destructive"]]
        if destructive:
            print(f"  {len(destructive)} DISTRUCTIVE. Execuția le refuză "
                  f"fără --allow-destructive.")
        return 0

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
