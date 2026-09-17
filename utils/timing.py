# utils/timing.py
"""
Cronometrul ingestiei FOREXE -- un jurnal SEPARAT, langa api_server.log.

DE CE UN AL DOILEA JURNAL. `api_server.log` raspunde la «ce s-a intamplat».
Asta raspunde la «unde s-a dus timpul», iar cele doua intrebari vor cantitati
opuse de zgomot: una vrea o linie pe cerere, cealalta vrea un bloc pe cerere cu
fiecare interogare SQL in el. Amestecate, prima devine ilizibila si a doua
imposibil de cautat. Deci modulul asta isi tine propriul logger ('kbot.timing'),
propriul fisier si propriul handler, si pune `propagate = False`: nimic din ce
se scrie aici nu ajunge la logger-ul radacina, iar jurnalul normal ramane exact
cat de vorbaret era.

CE MASOARA. Trei numere pe etapa, iar al treilea e cel care numeste vinovatul:

    total    ceasul de perete, cat a stat controlul in etapa
    sql      timpul din `cursor.execute()` plus aducerea randurilor, adica
             asteptarea dupa MariaDB
    propriu  total - sql, adica timpul pe care procesul asta l-a petrecut pe
             datele lui

O etapa care e 95% sql e o problema de baza de date -- de obicei UN singur
enunt rulat de mii de ori, si atunci blocul «SQL, dupa timp total» il arata
numarat. O etapa care e 95% propriu e o problema de Python. Fara despartirea
asta, amandoua arata doar «incet».

CUM SE FOLOSESTE.

    @timed("prelucrare")          # sub @require_session, ca `g.session` sa existe
    def post_prelucrare():
        note(cod=cod, mod=mod)
        with stage("conectare"):
            conn = get_kbot_connection(db_name)
        cursor = watch(conn.cursor(dictionary=True))   # numara fiecare enunt

`watch` intoarce un invelis care trimite totul mai departe catre cursorul
adevarat, deci codul de dedesubt (`prelucrare_pasi.py` si vecinii) nu afla
niciodata ca e cronometrat. Nicio semnatura de-a lui nu se schimba.

INTRERUPATORUL. Pornit implicit. `KBOT_TIMING=0` in mediu il opreste, si la fel
`TIMING_ENABLED = False` in config.py. Oprit inseamna oprit: nu se deschide
niciun fisier, nu se construieste niciun invelis, iar `watch` da inapoi chiar
cursorul primit -- costul e o citire de variabila de mediu pe cerere.

UNDE SCRIE. `forexe_timing.log` langa `api_server.log` (aceeasi radacina, deci
directorul din care porneste serverul). Se schimba din `KBOT_TIMING_LOG` in mediu
sau din `TIMING_LOG_PATH` in config.py. Se roteste la 10 MB, cu 5 copii -- ca
jurnalul normal.

CE NU SCRIE: PARAMETRII interogarilor. Textul enunturilor da, valorile nu.
Un jurnal de timpi nu are de ce sa poarte datele operatorului.
"""
import functools
import logging
import os
import re
import threading
import time
from logging.handlers import RotatingFileHandler

try:                     # the offline tests stand a stub `config` in sys.modules
    import config
except Exception:        # pragma: no cover - config is always there on the server
    config = None

_LOGGER_NAME = "kbot.timing"
_DEFAULT_PATH = "forexe_timing.log"

# A single statement slower than this gets its own line in the report. Anything
# under it is only visible through the aggregate, which is the right way round:
# 4000 fast statements are a bigger problem than one slow one, and the aggregate
# is what shows them.
SLOW_SQL_MS = 250.0

# How many statement SHAPES the report lists. Shapes, not statements: the same
# INSERT run 2400 times is one line with a count in front of it.
TOP_SQL = 12
MAX_SLOW_LINES = 20

_WIDTH = 100
_local = threading.local()
_setup_lock = threading.Lock()
_logger = None


# ---------------------------------------------------------------------------
# The switch and the file
# ---------------------------------------------------------------------------
def enabled():
    """Environment first, then config, then on."""
    raw = os.environ.get("KBOT_TIMING")
    if raw is not None:
        return raw.strip().lower() not in ("0", "false", "no", "off", "")
    return bool(getattr(config, "TIMING_ENABLED", True))


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
            path = (os.environ.get("KBOT_TIMING_LOG")
                    or getattr(config, "TIMING_LOG_PATH", None)
                    or _DEFAULT_PATH)
            handler = RotatingFileHandler(path, maxBytes=10 * 1024 * 1024,
                                          backupCount=5, encoding="utf-8")
            # No level, no logger name, no ip: every line here is one thing and
            # the timestamp is already in the header of each block.
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


class _Frame:
    """One open stage. Counters are INCLUSIVE: a child's SQL also lands here."""
    __slots__ = ("name", "depth", "seq", "t0", "sql_ms", "n_exec", "n_fetch")

    def __init__(self, name, depth, seq):
        self.name = name
        self.depth = depth
        # Order of OPENING, so the report can print a parent above its children.
        # Stages close inside-out, so the closing order would print the tree
        # upside down.
        self.seq = seq
        self.t0 = time.perf_counter()
        self.sql_ms = 0.0
        self.n_exec = 0
        self.n_fetch = 0


class _Run:
    """Everything one request accumulates. Lives in a thread local."""

    def __init__(self, label):
        self.id = _next_id()
        self.label = label
        self.t0 = time.perf_counter()
        self.wall = time.time()
        self.notes = {}          # cod, mod, dc, user ... -- context on the header
        self.counts = {}         # row counts of the incoming tables
        self.stack = []          # open frames
        self.seq = 0             # opening order, for the report
        # Closed stages, MERGED by (name, depth): a stage opened inside a loop
        # gives one line with a count, not one line per turn of the loop.
        # key -> [seq, n, ms, sql_ms, n_exec, n_fetch, max_ms]
        self.rows = {}
        self.shapes = {}         # normalized statement -> [n, ms, max_ms, stage]
        self.slow = []           # single statements over SLOW_SQL_MS
        self.sql_ms = 0.0
        self.fetch_ms = 0.0
        self.n_exec = 0
        self.n_fetch = 0

    # -- accounting ---------------------------------------------------------
    def add_sql(self, statement, ms, is_fetch):
        """Charge one statement to every OPEN stage, plus the run totals."""
        for f in self.stack:
            f.sql_ms += ms
            if is_fetch:
                f.n_fetch += 1
            else:
                f.n_exec += 1
        if is_fetch:
            self.fetch_ms += ms
            self.n_fetch += 1
            return
        self.sql_ms += ms
        self.n_exec += 1
        shape = _shape(statement)
        slot = self.shapes.get(shape)
        if slot is None:
            self.shapes[shape] = [1, ms, ms, self.where()]
        else:
            slot[0] += 1
            slot[1] += ms
            if ms > slot[2]:
                slot[2] = ms
        if ms >= SLOW_SQL_MS and len(self.slow) < MAX_SLOW_LINES:
            self.slow.append((ms, self.where(), shape))

    def where(self):
        return self.stack[-1].name if self.stack else "-"


_SPACES = re.compile(r"\s+")


def _shape(statement):
    """The statement on one line, cut short. Placeholders stay, values never
    arrive here -- this only ever sees the SQL text, not the parameters."""
    if not isinstance(statement, str):
        statement = str(statement)
    return _SPACES.sub(" ", statement).strip()[:110]


def _current():
    return getattr(_local, "run", None)


# ---------------------------------------------------------------------------
# The public three
# ---------------------------------------------------------------------------
class _Stage:
    """Context manager. A no-op when timing is off or no run is open, so call
    sites never need an `if`."""
    __slots__ = ("name", "run", "frame")

    def __init__(self, name):
        self.name = name
        self.run = _current()
        self.frame = None

    def __enter__(self):
        if self.run is not None:
            run = self.run
            run.seq += 1
            self.frame = _Frame(self.name, len(run.stack), run.seq)
            run.stack.append(self.frame)
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.frame is None:
            return False
        run = self.run
        run.stack.pop()
        f = self.frame
        ms = (time.perf_counter() - f.t0) * 1000.0
        key = (f.name, f.depth)
        slot = run.rows.get(key)
        if slot is None:
            run.rows[key] = [f.seq, 1, ms, f.sql_ms, f.n_exec, f.n_fetch, ms]
        else:
            slot[1] += 1
            slot[2] += ms
            slot[3] += f.sql_ms
            slot[4] += f.n_exec
            slot[5] += f.n_fetch
            if ms > slot[6]:
                slot[6] = ms
        return False       # never swallow


def stage(name):
    """`with stage("pas 4b receptii"): ...` -- nestable, and free when off."""
    return _Stage(name)


def note(**kw):
    """Context for the header line: cod, mod, dc, user. Ignored when off."""
    run = _current()
    if run is not None:
        run.notes.update({k: v for k, v in kw.items() if v is not None})


def count(**kw):
    """Row counts of what arrived, e.g. count(TabelIstoric=412)."""
    run = _current()
    if run is not None:
        run.counts.update(kw)


def watch(cursor):
    """Wrap a cursor so every statement is timed. Hands back the cursor
    unchanged when there is no run open -- so the same line of code works in
    the offline tests, where timing is off."""
    if _current() is None:
        return cursor
    return _WatchedCursor(cursor)


# ---------------------------------------------------------------------------
# The cursor wrapper
# ---------------------------------------------------------------------------
class _WatchedCursor:
    """
    Forwards EVERYTHING to the real cursor and times the four calls that can
    wait on the server. `__getattr__` carries the rest -- lastrowid, rowcount,
    description, close -- so nothing downstream can tell the difference.

    execute and fetch are counted apart on purpose: mysql.connector's plain
    cursor is unbuffered, so `execute` is the round trip and `fetchall` is the
    rows coming down the wire. A step slow in `execute` and a step slow in
    `fetchall` are two different problems.
    """
    __slots__ = ("_cursor", "_run")

    def __init__(self, cursor):
        object.__setattr__(self, "_cursor", cursor)
        object.__setattr__(self, "_run", _current())

    def execute(self, operation, params=None, **kw):
        t0 = time.perf_counter()
        try:
            return self._cursor.execute(operation, params, **kw)
        finally:
            self._run.add_sql(operation, (time.perf_counter() - t0) * 1000.0, False)

    def executemany(self, operation, seq_params, **kw):
        t0 = time.perf_counter()
        try:
            return self._cursor.executemany(operation, seq_params, **kw)
        finally:
            ms = (time.perf_counter() - t0) * 1000.0
            try:
                n = len(seq_params)
            except TypeError:
                n = -1
            self._run.add_sql("[executemany x%s] %s" % (n, operation), ms, False)

    def fetchall(self):
        t0 = time.perf_counter()
        try:
            return self._cursor.fetchall()
        finally:
            self._run.add_sql("", (time.perf_counter() - t0) * 1000.0, True)

    def fetchone(self):
        t0 = time.perf_counter()
        try:
            return self._cursor.fetchone()
        finally:
            self._run.add_sql("", (time.perf_counter() - t0) * 1000.0, True)

    def fetchmany(self, size=1):
        t0 = time.perf_counter()
        try:
            return self._cursor.fetchmany(size)
        finally:
            self._run.add_sql("", (time.perf_counter() - t0) * 1000.0, True)

    # -- transparency -------------------------------------------------------
    def __getattr__(self, name):
        return getattr(self._cursor, name)

    def __setattr__(self, name, value):
        setattr(self._cursor, name, value)

    def __iter__(self):
        return iter(self._cursor)

    def __enter__(self):
        self._cursor.__enter__()
        return self

    def __exit__(self, *a):
        return self._cursor.__exit__(*a)


# ---------------------------------------------------------------------------
# The decorator
# ---------------------------------------------------------------------------
def timed(label):
    """
    Opens a run around one Flask view and writes the block when it ends --
    including when it ends by raising, which is exactly when the timings are
    worth reading.
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
                raise
            finally:
                _local.run = None
                try:
                    _write(run, status)
                except Exception:            # a stopwatch must never break a route
                    logging.getLogger(__name__).exception(
                        "timing report failed for %s", label)
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
# The report
# ---------------------------------------------------------------------------
def _write(run, status):
    total_ms = (time.perf_counter() - run.t0) * 1000.0
    sql_ms = run.sql_ms + run.fetch_ms
    own_ms = total_ms - sql_ms
    pct = (sql_ms / total_ms * 100.0) if total_ms > 0 else 0.0

    out = []
    out.append("=" * _WIDTH)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(run.wall))
    out.append("%s.%03d  rulare %d  [%s]  status %s"
               % (stamp, int((run.wall % 1) * 1000), run.id, run.label, status))

    if run.notes:
        out.append("  " + "  ".join("%s=%s" % (k, v) for k, v in run.notes.items()))
    if run.counts:
        out.append("  intrare: " + "  ".join("%s=%s" % (k, v)
                                             for k, v in run.counts.items()))

    out.append("-" * _WIDTH)
    out.append("   total ms |    sql ms | propriu ms |  exec | fetch |    n | etapa")
    # Ordonat dupa DESCHIDERE, deci un parinte sta deasupra copiilor lui, si
    # adancimea e data de indentare. `n` > 1 = etapa dintr-o bucla, adunata.
    for (name, depth), slot in sorted(run.rows.items(), key=lambda kv: kv[1][0]):
        _seq, n, ms, s_ms, n_e, n_f, mx = slot
        linie = ("  %9.1f | %9.1f | %10.1f | %5d | %5d | %4d | %s%s"
                 % (ms, s_ms, ms - s_ms, n_e, n_f, n, "  " * depth, name))
        if n > 1:
            linie += "   (max %.1f ms)" % mx
        out.append(linie)
    out.append("  %9.1f | %9.1f | %10.1f | %5d | %5d | %4s | TOT (cererea intreaga)"
               % (total_ms, sql_ms, own_ms, run.n_exec, run.n_fetch, "-"))

    if run.shapes:
        out.append("  --- SQL, dupa timp total (doar execute; fetch-ul e in tabelul de sus) ---")
        top = sorted(run.shapes.items(), key=lambda kv: kv[1][1], reverse=True)
        for shape, (n, ms, mx, where) in top[:TOP_SQL]:
            out.append("  %9.1f ms  %6dx  %7.2f ms/buc  max %7.2f  [%s]"
                       % (ms, n, ms / n, mx, where))
            out.append("      %s" % shape)
        if len(top) > TOP_SQL:
            out.append("  ... si inca %d forme de enunt" % (len(top) - TOP_SQL))

    if run.slow:
        out.append("  --- executii peste %d ms, una cate una ---" % int(SLOW_SQL_MS))
        for ms, where, shape in run.slow:
            out.append("  %9.1f ms  [%s]  %s" % (ms, where, shape))

    out.append("  TOTAL %.1f ms   sql %.1f ms (%.1f%%)   propriu %.1f ms   "
               "%d executii / %d aduceri"
               % (total_ms, sql_ms, pct, own_ms, run.n_exec, run.n_fetch))

    # The one-line summary, LAST and on its own, so a whole session can be read
    # with `grep SUMAR forexe_timing.log` without the blocks in the way.
    sumar = ["SUMAR rulare=%d ruta=%s status=%s total_ms=%.1f sql_ms=%.1f "
             "propriu_ms=%.1f exec=%d fetch=%d"
             % (run.id, run.label, status, total_ms, sql_ms, own_ms,
                run.n_exec, run.n_fetch)]
    for k, v in run.notes.items():
        sumar.append("%s=%s" % (k, v))
    for k, v in run.counts.items():
        sumar.append("%s=%s" % (k, v))
    out.append(" ".join(sumar))

    _log().info("\n".join(out))
