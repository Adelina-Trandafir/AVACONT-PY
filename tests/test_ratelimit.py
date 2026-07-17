# Offline tests for the anti-brute-force limiter and its wiring into the auth
# routes:  python -m pytest tests/test_ratelimit.py
#
# Two layers:
#   1. RateLimiter in isolation — the per-(IP,user) and per-IP thresholds, and that
#      a success clears both counters.
#   2. The WIRING — the whole point. ratelimit.py existed for a while as dead code
#      (defined, never called), so login could be brute-forced. These tests drive
#      /api/auth/login and assert a 6th wrong-password attempt is rejected 429
#      BEFORE identity is checked, and that a correct password resets the counter.
import json
import sys
import types

import pytest


# ---------------------------------------------------------------------------
# Make routes.auth importable without a real config.py / DB driver (same pattern
# as test_auth_login_session.py). Stub BEFORE importing the auth blueprint.
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
from routes.auth import ratelimit
from routes.auth.auth import auth_bp
from routes.auth.ratelimit import RateLimiter
from routes.auth import session_store
from routes.auth.session_store import SessionStore

DB_NAME = "000_DEMO"
USER = "op@example.ro"


# ===========================================================================
# Layer 1: the limiter in isolation.
# ===========================================================================
def test_pair_blocks_after_max_failures():
    rl = RateLimiter()
    for _ in range(ratelimit._MAX_PER_USER - 1):
        rl.record_failure("1.1.1.1", USER)
    assert rl.is_blocked("1.1.1.1", USER) is False   # still under threshold
    rl.record_failure("1.1.1.1", USER)               # the Nth failure
    assert rl.is_blocked("1.1.1.1", USER) is True


def test_success_clears_both_counters():
    rl = RateLimiter()
    for _ in range(ratelimit._MAX_PER_USER):
        rl.record_failure("1.1.1.1", USER)
    assert rl.is_blocked("1.1.1.1", USER) is True
    rl.record_success("1.1.1.1", USER)
    assert rl.is_blocked("1.1.1.1", USER) is False


def test_ip_bucket_blocks_independently_of_username():
    # _MAX_PER_IP failures from ONE ip spread across DISTINCT usernames — each pair
    # bucket stays under its own threshold, but the shared IP bucket trips, so even a
    # brand-new username from that IP is blocked.
    rl = RateLimiter()
    for i in range(ratelimit._MAX_PER_IP):
        rl.record_failure("9.9.9.9", f"user{i}@x.ro")
    assert rl.is_blocked("9.9.9.9", "never-seen@x.ro") is True
    # A different IP is unaffected.
    assert rl.is_blocked("8.8.8.8", "never-seen@x.ro") is False


# ===========================================================================
# Layer 2: the wiring into /api/auth/login.
# ===========================================================================
class _FakeCursor:
    def __init__(self):
        self._last = ""

    def execute(self, sql, params=None):
        self._last = sql

    def fetchone(self):
        if "Unitati_Utilizatori" in self._last:
            return {"Rol": "Contabil", "LastSS": "02A"}
        if "FROM Unitati " in self._last:
            return {"NumeUnitate": "Primăria Demo", "CF": "12345678"}
        return None

    def fetchall(self):
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
    # Fresh limiter per test (LIMITER is a module singleton — isolate it). The route
    # uses the name bound in auth's namespace, so patch there.
    fresh_limiter = RateLimiter()
    monkeypatch.setattr(auth_module, "LIMITER", fresh_limiter)

    # Fresh session store so a successful login can mint a token cleanly.
    fresh_store = SessionStore()
    monkeypatch.setattr("routes.auth.auth.STORE", fresh_store)
    monkeypatch.setattr(session_store, "STORE", fresh_store)

    # Identity result is controlled per test via this mutable holder.
    creds_ok = {"value": False}
    verify_calls = {"count": 0}

    def _fake_verify(u, p):
        verify_calls["count"] += 1
        return creds_ok["value"]

    monkeypatch.setattr(auth_module, "_verify_operator", _fake_verify)
    monkeypatch.setattr(auth_module, "get_comun_reader_connection", lambda: _FakeConn())
    monkeypatch.setattr(auth_module, "_log_action", lambda *a, **k: None)

    flask_app = Flask(__name__)
    flask_app.json.ensure_ascii = False
    flask_app.register_blueprint(auth_bp)
    flask_app.config["TESTING"] = True
    return flask_app, creds_ok, verify_calls


def _login(client, password="wrong"):
    return client.post("/api/auth/login", data=json.dumps({
        "username": USER, "password": password,
        "db_name": DB_NAME, "machine": "PC1",
    }), content_type="application/json")


def test_login_locks_out_after_max_wrong_passwords(app):
    flask_app, creds_ok, verify_calls = app
    creds_ok["value"] = False            # every attempt = wrong password
    client = flask_app.test_client()

    for _ in range(ratelimit._MAX_PER_USER):
        assert _login(client).status_code == 401

    calls_before = verify_calls["count"]
    r = _login(client)                   # the (N+1)-th attempt
    assert r.status_code == 429
    body = r.get_json()
    assert "Prea multe" in body["error"]
    # Blocked BEFORE the identity check — no extra _verify_operator call.
    assert verify_calls["count"] == calls_before


def test_lockout_message_has_literal_diacritics(app):
    flask_app, creds_ok, _ = app
    creds_ok["value"] = False
    client = flask_app.test_client()
    for _ in range(ratelimit._MAX_PER_USER):
        _login(client)
    raw = _login(client).get_data(as_text=True)
    assert raw != ""
    assert "\\u" not in raw               # NOT \uXXXX escaped
    assert "încercări" in raw             # literal UTF-8 diacritics


def test_successful_login_resets_the_counter(app):
    flask_app, creds_ok, _ = app
    client = flask_app.test_client()

    creds_ok["value"] = False
    for _ in range(ratelimit._MAX_PER_USER - 1):   # one short of a lockout
        assert _login(client).status_code == 401

    creds_ok["value"] = True                       # correct password clears counters
    assert _login(client, password="right").status_code == 200

    creds_ok["value"] = False
    # Fresh budget: another (N-1) failures must NOT be blocked yet.
    for _ in range(ratelimit._MAX_PER_USER - 1):
        assert _login(client).status_code == 401
