# utils/asociere_log.py
"""
The association journal -- a log file of its own for everything the reception
association does, on both roads that do it: the two-phase ingestion
(`/api/forexe/prelucrare`) and the any-time editor (`/api/forexe/asociere`).

WHY A THIRD LOG FILE. `api_server.log` answers "what happened" with one line per
request; `forexe_timing.log` answers "where did the time go". Neither can answer
"why did it refuse a placement the operator knows is right", because that answer
is a whole picture: the chain as the server assembled it, both sides of every
comparison, and the rule that spoke. That is dozens of lines per request -- put
into `api_server.log` they would bury everything else, and they would still be
missing the numbers, because nothing writes them today. So this module keeps its
own logger ('kbot.asociere'), its own file and its own handler, with
`propagate = False`: not one line of it reaches the root logger.

WHAT IT WRITES. One block per request, held in memory and written whole at the
end, so two requests running at once cannot interleave their lines. The block
carries, in the order the code does them:

    * the header    -- route, dc, user, cod, mod, and the outcome (HTTP status)
    * the state     -- the fingerprint and what it was built from
    * the picture   -- every reception with its per-indicator sums, every
                      snapshot to be decided with its lines
    * the intention -- the automatic pass's suggestions, then every decision or
                      command with the reception it resolves to
    * the chains    -- each resulting chain in date order, exactly as the rules
                      see it
    * the verdicts  -- F14 / F15 / F16, each with the numbers it compared, the
                      ones that PASS as well as the one that refused
    * the writing   -- what was written, or that nothing was (rollback)

A refusal is written as `RESPINS` and repeated at the foot of the block, so a
whole day can be read with `grep RESPINS asociere.log`.

THE SWITCH. `main.py` owns it: `ASOCIERE_LOG_ENABLED` at the top of the file,
handed over with `asociere_log.set_enabled(...)`.
`KBOT_ASOCIERE=0` in the environment overrides main (that is how the test suite
keeps the file from being opened in whatever directory pytest was started from),
and with neither of them speaking, `ASOCIERE_LOG_ENABLED` in config.py decides.
Off means off: no file is opened and every call below returns on its first line.

WHERE IT WRITES. `asociere.log` next to `api_server.log` -- the directory the
server was started from. Changed with `KBOT_ASOCIERE_LOG` in the environment or
`ASOCIERE_LOG_PATH` in config.py. Rotates at 10 MB with 5 copies, like the rest.

WHAT IT DOES NOT WRITE: nothing that is not already in the FX_* tables. Sums,
dates, indicator codes and reception numbers -- the operator's own data, in the
operator's own database, on the operator's own server.
"""
import functools
import logging
import os
import threading
import time
from logging.handlers import RotatingFileHandler

try:                     # the offline tests stand a stub `config` in sys.modules
    import config
except Exception:        # pragma: no cover - config is always there on the server
    config = None

_LOGGER_NAME = "kbot.asociere"
_DEFAULT_PATH = "asociere.log"
_WIDTH = 100

_local = threading.local()
_setup_lock = threading.Lock()
_logger = None
_switch = None           # what main.py said; None = nobody said anything


# ---------------------------------------------------------------------------
# The switch and the file
# ---------------------------------------------------------------------------
def set_enabled(value):
    """
    The switch `main.py` owns. `None` gives the decision back to the environment
    and to config, which is what a fresh process starts with.
    """
    global _switch
    _switch = None if value is None else bool(value)


def enabled():
    """
    Environment first, then main.py, then config, then on.

    The environment comes FIRST on purpose: `main.py` is imported by the test
    suite too, so a switch that outranked the environment would turn the journal
    on inside every pytest run.
    """
    raw = os.environ.get("KBOT_ASOCIERE")
    if raw is not None:
        return raw.strip().lower() not in ("0", "false", "no", "off", "")
    if _switch is not None:
        return _switch
    return bool(getattr(config, "ASOCIERE_LOG_ENABLED", True))


def _log():
    """The dedicated logger, built once, never attached to the root one."""
    global _logger
    if _logger is not None:
        return _logger
    with _setup_lock:
        if _logger is not None:
            return _logger
        lg = logging.getLogger(_LOGGER_NAME)
        lg.setLevel(logging.INFO)
        # THE POINT OF THE WHOLE MODULE: nothing here reaches the root logger,
        # so api_server.log never sees a line of it.
        lg.propagate = False
        if not lg.handlers:
            path = (os.environ.get("KBOT_ASOCIERE_LOG")
                    or getattr(config, "ASOCIERE_LOG_PATH", None)
                    or _DEFAULT_PATH)
            handler = RotatingFileHandler(path, maxBytes=10 * 1024 * 1024,
                                          backupCount=5, encoding="utf-8")
            # No level and no logger name: every line here is one thing, and the
            # timestamp is already in the header of each block.
            handler.setFormatter(logging.Formatter("%(message)s"))
            lg.addHandler(handler)
        _logger = lg
        return _logger


# ---------------------------------------------------------------------------
# One run = one HTTP request
# ---------------------------------------------------------------------------
_counter_lock = threading.Lock()
_counter = 0


def _next_id():
    global _counter
    with _counter_lock:
        _counter += 1
        return _counter


class _Run:
    """Everything one request accumulates. Lives in a thread local."""

    def __init__(self, label):
        self.id = _next_id()
        self.label = label
        self.t0 = time.perf_counter()
        self.wall = time.time()
        self.notes = {}        # dc, user, cod, mod -- the header line
        self.lines = []        # the body, in the order it happened
        self.refusals = []     # every RESPINS, repeated at the foot of the block
        self.indent = 0


def _current():
    return getattr(_local, "run", None)


# ---------------------------------------------------------------------------
# Writing into the block
# ---------------------------------------------------------------------------
def _emit(text):
    """
    One line into the open block -- or, with no block open, straight to the file.

    The second case is not a mistake: `citeste_receptii` and its neighbours are
    called from places that never open a run, and a line from there is worth
    more on its own than dropped.
    """
    run = _current()
    if run is None:
        if enabled():
            _log().info(text)
        return
    run.lines.append(("  " * run.indent) + text)


def line(text, *args):
    """One line. Formats lazily, so an off journal costs one function call."""
    if not enabled():
        return
    _emit(text % args if args else text)


def section(title):
    """A named part of the block. Everything after it is indented under it."""
    if not enabled():
        return
    run = _current()
    if run is not None:
        run.indent = 0
    _emit("--- " + title + " " + "-" * max(0, _WIDTH - len(title) - 5))
    if run is not None:
        run.indent = 1


def note(**kw):
    """Context for the header line: dc, user, cod, mod. Ignored when off."""
    if not enabled():
        return
    run = _current()
    if run is not None:
        run.notes.update({k: v for k, v in kw.items() if v is not None})


def refusal(text, *args):
    """
    A rule that said no, or a request thrown out.

    Written where it happened AND repeated at the foot of the block, so a whole
    day reads with `grep RESPINS asociere.log`.
    """
    if not enabled():
        return
    msg = text % args if args else text
    _emit("RESPINS: " + msg)
    run = _current()
    if run is not None:
        run.refusals.append(msg)


def table(headers, rows):
    """
    An aligned table, or the word «(nimic)» when there is nothing in it.

    `headers` and every row are sequences of anything; every cell goes through
    `_text`, so a call site never has to format before calling.
    """
    if not enabled():
        return
    body = [[_text(c) for c in row] for row in rows]
    if not body:
        _emit("(nimic)")
        return
    head = [_text(h) for h in headers]
    widths = [len(h) for h in head]
    for row in body:
        for i, cell in enumerate(row):
            if i < len(widths):
                widths[i] = max(widths[i], len(cell))
            else:
                widths.append(len(cell))
    while len(head) < len(widths):
        head.append("")

    def _row(cells):
        out = []
        last = len(cells) - 1
        for i, cell in enumerate(cells):
            out.append(cell if i == last else cell.ljust(widths[i]))
        return " | ".join(out)

    _emit(_row(head))
    _emit("-+-".join("-" * w for w in widths))
    for row in body:
        _emit(_row(row))


# ---------------------------------------------------------------------------
# Small formatting helpers, so call sites stay one line long
# ---------------------------------------------------------------------------
def _text(v):
    if v is None:
        return "-"
    if isinstance(v, bool):
        return "da" if v else "nu"
    if isinstance(v, float):
        return "%.2f" % v
    if hasattr(v, "strftime"):
        s = v.strftime("%d.%m.%Y %H:%M:%S")
        return s[:10] if s.endswith(" 00:00:00") else s
    return str(v)


def moment(v):
    """A DataH / DataR written the way the operator sees it in the form."""
    return _text(v)


def sume_pe_indicator(linii):
    """
    «AAB=1200.00  AA2=310.50» -- the same shape for both sides of an F15
    comparison, so the two lines can be read against each other.

    Deliberately NOT the function the rule itself uses: `_valori_pe_indicator`
    drops the zeroes, because that is what the comparison means. This one shows
    everything, zeroes included, since a zero on one side and a missing line on
    the other is exactly the sort of thing worth seeing.
    """
    out = {}
    for l in linii or []:
        cod = (l.get("cod_indicator") or "").strip() or "(fără cod)"
        out[cod] = out.get(cod, 0.0) + float(l.get("valoare") or 0)
    if not out:
        return "(fara linii)"
    return "  ".join("%s=%.2f" % (c, v) for c, v in sorted(out.items()))


# ---------------------------------------------------------------------------
# The decorator
# ---------------------------------------------------------------------------
def traced(label):
    """
    Opens a run around one Flask view and writes the block when it ends --
    including when it ends by raising, which is exactly when the block is worth
    reading.
    """
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kw):
            if not enabled():
                return fn(*args, **kw)
            run = _Run(label)
            _local.run = run
            status = "?"
            try:
                result = fn(*args, **kw)
                status = _status_of(result)
                return result
            except Exception as err:
                status = "EXC %s" % type(err).__name__
                run.lines.append("EXCEPTIE %s: %s" % (type(err).__name__, err))
                raise
            finally:
                _local.run = None
                try:
                    _write(run, status)
                except Exception:      # a journal must never break a route
                    logging.getLogger(__name__).exception(
                        "the association journal failed for %s", label)
        return wrapper
    return deco


def _status_of(result):
    code = getattr(result, "status_code", None)
    if code is not None:
        return str(code)
    if isinstance(result, tuple) and len(result) > 1 and isinstance(result[1], int):
        return str(result[1])
    return "200"


# ---------------------------------------------------------------------------
# The block
# ---------------------------------------------------------------------------
def _write(run, status):
    total_ms = (time.perf_counter() - run.t0) * 1000.0
    out = ["=" * _WIDTH]
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(run.wall))
    out.append("%s.%03d  rulare %d  [%s]  status %s  %.0f ms"
               % (stamp, int((run.wall % 1) * 1000), run.id, run.label, status,
                  total_ms))
    if run.notes:
        out.append("  " + "  ".join("%s=%s" % (k, _text(v))
                                    for k, v in run.notes.items()))
    out.extend(run.lines)
    if run.refusals:
        out.append("--- ce a refuzat rularea " + "-" * (_WIDTH - 26))
        for m in run.refusals:
            out.append("  RESPINS: " + m)
    else:
        out.append("--- nicio respingere " + "-" * (_WIDTH - 22))

    # The one-line summary, LAST and on its own, so a whole session can be read
    # with `grep SUMAR asociere.log` without the blocks in the way.
    sumar = ["SUMAR rulare=%d ruta=%s status=%s ms=%.0f respingeri=%d"
             % (run.id, run.label, status, total_ms, len(run.refusals))]
    for k, v in run.notes.items():
        sumar.append("%s=%s" % (k, _text(v)))
    out.append(" ".join(sumar))
    _log().info("\n".join(out))
