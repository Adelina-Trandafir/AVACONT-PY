"""
schema_service.py -- schema_sync as a callable, for the HTTP route.

schema_sync.py is a COMMAND LINE: argparse, print(), and an input()
prompt standing between the plan and its execution. None of that
survives an HTTP request, so this module runs the same steps over the
same building blocks and hands every line back through a callback
instead of writing it to stdout.

It is a fourth entry point, not a wrapper. schema_sync.py,
schema_generate.py and schema_execute.py each already have their own
main() over schema_common + schema_diff; this is another door into the
same rooms. The three command lines are left exactly as they were, and
nothing in this file is imported by them -- so a mistake here cannot
reach an operator running the tool by hand.

TWO THINGS THE COMMAND LINE DOES THAT A REQUEST CANNOT.

First, `_ask("Executati acum?")`. There is nobody at a terminal, so the
caller has to have decided already: this module always behaves as if
--run were given, and `view_only=True` is the way to ask for the plan
without the execution.

Second, `confirm_destructive()` -- the typed DA. Replaced by
`allow_destructive`, which the caller must send as True. The refusal
when it is False is the command line's refusal, unchanged and total:
NOTHING runs, not even the additive half, because many destructive rows
are the first step of a pair (DROP FOREIGN KEY then ADD CONSTRAINT) and
running only the second half leaves the schema worse than untouched.

ONE RUN AT A TIME, SERVER-WIDE. Two syncs in parallel would interleave
their DDL and share the single `schema_diff_log`, where the second one's
clear_pending() would delete the first one's queued rows out from under
it. The lock is taken here rather than in the route so that no caller
can forget it.
"""

import logging
import os
import threading
from datetime import datetime

from .schema_common import (OUT_DIR, SchemaSyncError, check_prerequisites,
                            connect, ensure_control_table, parse_targets,
                            setup_logging, summarise, verify_targets)
from .schema_execute import (execute_rows, refuse_destructive, render_sql,
                             restore_hint, take_backups)
from .schema_generate import generate

# The modes schema_diff understands. SAFE adds (and repairs collation);
# FORCE also modifies and drops.
MODES = ("SAFE", "FORCE")

_run_lock = threading.Lock()


class _ProgressHandler(logging.Handler):
    """Feeds every line the package logs into the caller's callback.

    The package narrates itself through `logger`, not through return
    values, so without this the operator would watch a blank panel for
    the whole run and then be handed a number.
    """

    def __init__(self, progress):
        logging.Handler.__init__(self)
        self._progress = progress

    def emit(self, record):
        try:
            self._progress(self.format(record))
        except Exception:
            # A handler that raises would take down the run it is only
            # supposed to narrate. There is nowhere better to report to:
            # the reporting channel is the thing that just failed.
            pass


def run_sync(targets, mode="SAFE", allow_destructive=False, view_only=False,
             backup_dir="backup", skip_backup=False, continue_on_error=False,
             progress=None):
    """Generate the difference against AVACONT_SURSA and execute it.

    `targets` is one database name, a comma-separated list, or a list of
    names. They are always verified rather than discovered: a name that
    came over the wire is a name somebody typed, and verify_targets
    exists precisely so a typo fails loudly instead of reporting a clean
    run against a database that does not exist.

    Returns a dict for the JSON response. Raises SchemaSyncError for
    every condition that must stop the run, and lets mysql.connector
    errors out unchanged -- the caller turns both into a failed job.
    """
    say = progress if callable(progress) else (lambda _line: None)

    mode = (mode or "SAFE").strip().upper()
    if mode not in MODES:
        raise SchemaSyncError(
            "Mod necunoscut «%s». Acceptate: %s." % (mode, ", ".join(MODES)))

    if isinstance(targets, (list, tuple, set)):
        targets = ",".join(str(t) for t in targets)

    if not _run_lock.acquire(False):
        raise SchemaSyncError(
            "O sincronizare de schemă este deja în curs pe server. "
            "Așteptați să se încheie și porniți-o din nou.")

    # EVERYTHING past the acquire sits inside the try. setup_logging opens a
    # RotatingFileHandler and can fail on its own; a failure between the
    # acquire and the try would hold the lock for the life of the process and
    # every later run would be told one is already in progress.
    logger = None
    handler = None
    conn = None
    try:
        logger = setup_logging()
        handler = _ProgressHandler(say)
        # Bare message: the timestamp and the level are already in
        # schema_sync.log, and the caller's own log adds its own framing.
        handler.setFormatter(logging.Formatter("%(message)s"))
        handler.setLevel(logging.INFO)
        logger.addHandler(handler)

        conn = connect()
        ensure_control_table(conn, logger)
        check_prerequisites(conn, logger)

        wanted = verify_targets(conn, parse_targets(targets), logger)

        rows = generate(conn, wanted, mode, logger, reset=True)
        if not rows:
            logger.info(
                "REZULTAT: structura din AVACONT_SURSA se regăsește deja "
                "întocmai în %s — tabele, coloane, indexuri și chei străine, "
                "toate identice. Nu e nimic de reparat și nu s-a executat "
                "nimic.", ", ".join(wanted))
            return _result(wanted, mode, rows=[], destructive=[],
                           sql_path=None, executed=False)

        destructive = [r for r in rows if r["is_destructive"]]
        for line in summarise(rows).splitlines():
            say(line)
        if destructive:
            affected = sorted(set(r["target_db"] for r in destructive))
            say("  %d DISTRUCTIVE, pe: %s"
                % (len(destructive), ", ".join(affected)))

        # Written before anything is executed, and kept afterwards: it is
        # the only record of what the run INTENDED, readable next to what
        # schema_diff_log says actually happened.
        sql_path = os.path.join(
            OUT_DIR, "schema_diff_%s.sql" % datetime.now().strftime(
                "%Y%m%d_%H%M%S"))
        os.makedirs(os.path.dirname(os.path.abspath(sql_path)), exist_ok=True)
        with open(sql_path, "w", encoding="utf-8") as fh:
            fh.write(render_sql(rows))
        sql_path = os.path.abspath(sql_path)
        logger.info("Instrucțiunile scrise în: %s", sql_path)

        if view_only:
            logger.info("Doar vizualizare: nu s-a executat nimic. Rândurile "
                        "rămân în așteptare.")
            return _result(wanted, mode, rows, destructive, sql_path,
                           executed=False)

        if destructive and not allow_destructive:
            # Logs the whole refusal, ids included, and returns 2. The
            # code is the command line's; here only the lines matter.
            refuse_destructive(destructive, logger)
            logger.error("Reluați cerând explicit operațiile distructive, "
                         "dacă sunt intenționate.")
            return _result(wanted, mode, rows, destructive, sql_path,
                           executed=False, refused=True)

        dumps = {}
        if destructive:
            # Server-side mysqldump, exactly as on the command line. A dump
            # that cannot be written stops the run -- take_backups raises.
            dumps = take_backups(destructive, backup_dir, skip_backup, logger)

        ok, failed, first_failure = execute_rows(
            conn, rows, logger, stop_on_error=not continue_on_error)

        logger.info("Rezultat: %d reușite, %d eșuate, %d neatinse.",
                    ok, failed, len(rows) - ok - failed)
        if failed and dumps:
            failed_db = first_failure["target_db"] if first_failure else None
            for name, path in dumps.items():
                if failed_db is None or name == failed_db:
                    for line in restore_hint(name, path).splitlines():
                        logger.error("%s", line)

        return _result(wanted, mode, rows, destructive, sql_path,
                       executed=True, ok=ok, failed=failed, dumps=dumps)

    finally:
        if logger is not None and handler is not None:
            logger.removeHandler(handler)
        if conn is not None and conn.is_connected():
            conn.close()
        _run_lock.release()


def _result(targets, mode, rows, destructive, sql_path, executed,
            ok=0, failed=0, refused=False, dumps=None):
    """The shape of the JSON. ASCII keys on both sides of the wire."""
    total = len(rows)
    return {
        "tinte": list(targets),
        "mod": mode,
        "instructiuni": total,
        "distructive": len(destructive),
        "executat": bool(executed),
        "refuzat": bool(refused),
        "reusite": ok,
        "esuate": failed,
        "neatinse": (total - ok - failed) if executed else total,
        "fisier_sql": sql_path,
        "copii": dict(dumps or {}),
        # The one flag the migrator acts on: everything that was planned
        # ran and nothing failed. An empty plan counts -- a database that
        # already matches the source needed no work.
        "reusit": bool(total == 0 or (executed and failed == 0)),
    }
