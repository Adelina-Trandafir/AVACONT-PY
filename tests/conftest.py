# Makes the PYTHON app root importable (main, config, utils, routes) when pytest
# is launched either from PYTHON/ or from the tests/ folder itself.
import os
import sys

_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP_ROOT not in sys.path:
    sys.path.insert(0, _APP_ROOT)

# The FOREXE stopwatch (utils/timing.py) is ON by default, because the server is
# where it is needed. OFF here: a test run has no business opening
# forexe_timing.log in whatever directory pytest was started from. Nothing else
# changes -- with no run open, `timing.stage` is a no-op and `timing.watch` hands
# back the very cursor it was given, fake ones included.
os.environ.setdefault("KBOT_TIMING", "0")

# The association journal (utils/asociere_log.py), same reasoning and one step
# stronger: `main.py` turns it ON with a switch of its own, and half the suite
# does `from main import app`. The environment variable outranks that switch, so
# this line is what keeps `asociere.log` from being created wherever pytest was
# started. With the journal off, every call in it returns on its first line.
os.environ.setdefault("KBOT_ASOCIERE", "0")
