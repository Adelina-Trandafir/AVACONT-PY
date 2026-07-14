# Offline regression tests for the K-BOT auth login <-> session-guard contract.
# No real DB, no config.py, no network:  python -m pytest tests/test_auth_login_session.py
#
# THE bug this pins down: login mints a bearer token, and the FIRST authenticated
# call (e.g. /api/auth/periods, guarded by the shared session guard) must accept
# that token IN THE SAME PROCESS, IMMEDIATELY. Historically login wrote the token
# into a private dict while the guards validated a different store -> instant 401.
#
# Also covers one test per 401 reason code emitted by the guard.
import json
import sys
import time
import types

import pytest


# ---------------------------------------------------------------------------
# Make routes.auth importable without a real config.py / DB driver (same pattern
# as test_session_store.py). Stub BEFORE importing the auth blueprint.
# ---------------------------------------------------------------------------
def _ensure_importable():
    if "config" not in sys.modules:
        try:
            import config  # noqa: F401
        except Exception:
            cfg = types.ModuleType("config")
            cfg.DB_CONFIG = {}
            cfg.API_KEY = "test"
            sys.modules["config"] = cfg
    if "mysql.connector" not in sys.modules:
        try:
            import mysql.connector  # noqa: F401
        except Exception:
            mysql_mod = types.ModuleType("mysql")
            conn_mod = types.ModuleType("mysql.connector")

            class _Err(Exception):
                errno = None

            conn_mod.Error = _Err
            conn_mod.connect = lambda **_kw: (_ for _ in ()).throw(RuntimeError("no db driver"))
            mysql_mod.connector = conn_mod
            sys.modules["mysql"] = mysql_mod
            sys.modules["mysql.connector"] = conn_mod


_ensure_importable()

from flask import Flask
from routes.auth import auth as auth_module
from routes.auth.auth import auth_bp
from routes.auth import session_store
from routes.auth.session_store import SessionStore

DB_NAME = "000_DEMO"

_DATA = {
    "access": {"Rol": "Contabil", "LastSS": "02A"},
    "unit": {"NumeUnitate": "Primăria Demo", "CF": "12345678"},
    "periods": [
        {"AN": 2026, "SS": "02A", "CodProgram": "P1"},
        {"AN": 2026, "SS": "02B", "CodProgram": "P2"},
    ],
}


class _FakeCursor:
    def __init__(self):
        self._last = ""

    def execute(self, sql, params=None):
        self._last = sql

    def fetchone(self):
        if "Unitati_Utilizatori" in self._last:
            return _DATA["access"]
        if "FROM Unitati " in self._last:      # trailing space => NOT Unitati_*
            return _DATA["unit"]
        return None

    def fetchall(self):
        if "Unitati_Ani" in self._last:
            return _DATA["periods"]
        return []

    def close(self):
        pass


class _FakeConn:
    def cursor(self, dictionary=False):
        return _FakeCursor()

    def is_connected(self):
        return True

    def close(self):
        pass


@pytest.fixture
def app(monkeypatch):
    # Fresh in-memory store on BOTH module references, so tests are isolated but the
    # login side and the guard side still share ONE object (the whole point).
    fresh = SessionStore()
    monkeypatch.setattr("routes.auth.auth.STORE", fresh)
    monkeypatch.setattr("routes.auth.guard.STORE", fresh)
    monkeypatch.setattr(session_store, "STORE", fresh)

    # No real MariaDB: identity always valid, reader returns canned rows, audit no-op.
    monkeypatch.setattr(auth_module, "_verify_operator", lambda u, p: True)
    monkeypatch.setattr(auth_module, "get_comun_reader_connection", lambda: _FakeConn())
    monkeypatch.setattr(auth_module, "_log_action", lambda *a, **k: None)

    flask_app = Flask(__name__)
    flask_app.json.ensure_ascii = False
    flask_app.register_blueprint(auth_bp)
    flask_app.config["TESTING"] = True
    return flask_app, fresh


def _login(client):
    return client.post("/api/auth/login", data=json.dumps({
        "username": "op@example.ro", "password": "pw",
        "db_name": DB_NAME, "machine": "PC1",
    }), content_type="application/json")


# ---------------------------------------------------------------------------
# THE regression: token from /login validates on /periods, same process, at once.
# ---------------------------------------------------------------------------
def test_login_token_validates_on_periods_immediately(app):
    flask_app, _store = app
    client = flask_app.test_client()

    r = _login(client)
    assert r.status_code == 200, r.get_data(as_text=True)
    token = r.get_json()["Token"]
    assert token

    # First authenticated call — must NOT 401 (this is what was failing in prod).
    r2 = client.get(f"/api/auth/periods?db_name={DB_NAME}",
                    headers={"Authorization": f"Bearer {token}"})
    assert r2.status_code == 200, r2.get_data(as_text=True)
    assert r2.get_json()["periods"] == _DATA["periods"]


def test_login_writes_into_the_same_store_the_guard_reads(app):
    flask_app, store = app
    client = flask_app.test_client()
    token = _login(client).get_json()["Token"]
    # The guard's store literally contains the login token.
    assert store.validate_and_touch(token) is not None


# ---------------------------------------------------------------------------
# One test per 401 reason code (+ the 403 context mismatch).
# ---------------------------------------------------------------------------
def test_reason_token_absent(app):
    flask_app, _ = app
    r = flask_app.test_client().get(f"/api/auth/periods?db_name={DB_NAME}")
    assert r.status_code == 401
    assert r.get_json()["reason"] == "TOKEN_ABSENT"


def test_reason_token_malformed(app):
    flask_app, _ = app
    r = flask_app.test_client().get(f"/api/auth/periods?db_name={DB_NAME}",
                                    headers={"Authorization": "Basic abc"})
    assert r.status_code == 401
    assert r.get_json()["reason"] == "TOKEN_MALFORMED"


def test_reason_token_unknown(app):
    flask_app, _ = app
    r = flask_app.test_client().get(f"/api/auth/periods?db_name={DB_NAME}",
                                    headers={"Authorization": "Bearer nope-nope"})
    assert r.status_code == 401
    assert r.get_json()["reason"] == "TOKEN_UNKNOWN"


def test_reason_expired_idle(app, monkeypatch):
    flask_app, store = app
    token = _login(flask_app.test_client()).get_json()["Token"]
    real = time.time()
    monkeypatch.setattr(time, "time", lambda: real + session_store._TTL_SECONDS + 1)
    r = flask_app.test_client().get(f"/api/auth/periods?db_name={DB_NAME}",
                                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401
    assert r.get_json()["reason"] == "EXPIRED_IDLE"


def test_reason_expired_absolute(app, monkeypatch):
    flask_app, store = app
    token = _login(flask_app.test_client()).get_json()["Token"]
    real = time.time()
    # Keep touching inside the idle window, but blow past the absolute cap.
    monkeypatch.setattr(time, "time", lambda: real + session_store._ABS_TTL_SECONDS + 1)
    r = flask_app.test_client().get(f"/api/auth/periods?db_name={DB_NAME}",
                                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401
    assert r.get_json()["reason"] == "EXPIRED_ABSOLUTE"


def test_reason_context_mismatch(app):
    flask_app, _ = app
    token = _login(flask_app.test_client()).get_json()["Token"]
    # Valid live token, but asking for a DIFFERENT database than the one logged into.
    r = flask_app.test_client().get("/api/auth/periods?db_name=999_OTHER",
                                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403
    assert r.get_json()["reason"] == "CONTEXT_MISMATCH"


def test_401_body_has_literal_diacritics(app):
    flask_app, _ = app
    r = flask_app.test_client().get(f"/api/auth/periods?db_name={DB_NAME}",
                                    headers={"Authorization": "Bearer nope"})
    raw = r.get_data(as_text=True)
    assert "\\u" not in raw            # NOT \uXXXX escaped
    assert "Autentificați-vă" in raw   # literal UTF-8 diacritics
