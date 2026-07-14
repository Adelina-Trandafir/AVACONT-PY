# Makes the PYTHON app root importable (main, config, utils, routes) when pytest
# is launched either from PYTHON/ or from the tests/ folder itself.
import os
import sys

_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP_ROOT not in sys.path:
    sys.path.insert(0, _APP_ROOT)
