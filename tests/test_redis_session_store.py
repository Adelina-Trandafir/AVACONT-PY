# Offline tests for the Redis-backed session store, exercised against fakeredis
# (an in-process Redis fake) so the Redis code path is genuinely covered without a
# live server:  python -m pytest tests/test_redis_session_store.py
#
# In-app idle/absolute expiry is driven by monkeypatching time.time (same technique
# as test_session_store.py); the fake key persists because its native TTL is set to
# the absolute-cap seconds in real time, far longer than a test takes.
import sys
import types

import pytest

fakeredis = pytest.importorskip("fakeredis")


# routes.auth.__init__ pulls auth.py, which imports config + mysql.connector. On a
# bare dev box neither exists; stub them so the store under test imports cleanly
# (same pattern as test_session_store.py).
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
            conn_mod.connect = lambda **_kw: (_ for _ in ()).throw(RuntimeError("no db"))
            mysql_mod.connector = conn_mod
            sys.modules["mysql"] = mysql_mod
            sys.modules["mysql.connector"] = conn_mod


_ensure_importable()

from flask import Flask, jsonify, g
from routes.auth import session_store
from routes.auth.session_store import RedisSessionStore
from routes.auth.guard import require_session

PREFIX = "kbot:sess:"


class _Clock:
    """Ceas logic controlat de test, independent de reaper-ul (real-time) al fakeredis."""
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def _store(clock=None):
    client = fakeredis.FakeStrictRedis(decode_responses=True)
    kw = {"key_prefix": PREFIX}
    if clock is not None:
        kw["clock"] = clock
    return RedisSessionStore(client, **kw), client


def _create(store, **kw):
    args = dict(username="op", password="", id_unitate=0,
                db_name="000_DEMO", ctx={"DbName": "000_DEMO"}, pcname="PC")
    args.update(kw)
    return store.create(**args)


# ---------- contract parity with SessionStore ----------

def test_create_then_validate_returns_session():
    store, _ = _store()
    token, s = _create(store)
    assert s.db_name == "000_DEMO"
    got = store.validate_and_touch(token)
    assert got is not None and got.username == "op" and got.db_name == "000_DEMO"


def test_unknown_token_is_rejected():
    store, _ = _store()
    assert store.validate_and_touch("no-such") is None
    v = store.validate_and_touch_ex("no-such")
    assert v.reason == "TOKEN_UNKNOWN"


def test_idle_expiry_kills_session():
    clock = _Clock(1_000_000.0)
    store, client = _store(clock)
    token, _ = _create(store)
    clock.t += session_store._TTL_SECONDS + 1
    v = store.validate_and_touch_ex(token)
    assert v.session is None and v.reason == "EXPIRED_IDLE"
    assert client.get(PREFIX + token) is None       # key reaped on access


def test_active_use_slides_idle_window():
    clock = _Clock(1_000_000.0)
    store, _ = _store(clock)
    token, _ = _create(store)
    clock.t += 15 * 60
    assert store.validate_and_touch(token) is not None
    clock.t += 13 * 60                               # 28 min total, kept alive by touches
    assert store.validate_and_touch(token) is not None


def test_absolute_cap_expires_even_under_use():
    clock = _Clock(1_000_000.0)
    store, _ = _store(clock)
    token, _ = _create(store)
    clock.t += 15 * 60
    assert store.validate_and_touch(token) is not None
    clock.t = 1_000_000.0 + session_store._ABS_TTL_SECONDS + 1
    v = store.validate_and_touch_ex(token)
    assert v.session is None and v.reason == "EXPIRED_ABSOLUTE"


def test_revoke_kills_token():
    store, _ = _store()
    token, _ = _create(store)
    store.revoke(token)
    assert store.validate_and_touch(token) is None


def test_size_counts_only_prefixed_keys():
    store, client = _store()
    _create(store)
    _create(store)
    client.set("other-project:key", "x")     # foreign key must NOT be counted
    assert store.size() == 2


# ---------- security + namespacing ----------

def test_password_is_never_persisted_to_redis():
    store, client = _store()
    token, s = _create(store, password="super-secret")
    raw = client.get(PREFIX + token)
    assert "super-secret" not in raw                 # R2: password stays out of Redis
    assert store.validate_and_touch(token).password == ""


def test_keys_are_namespaced_with_prefix():
    store, client = _store()
    token, _ = _create(store)
    assert client.exists(PREFIX + token) == 1
    assert list(client.scan_iter(match=PREFIX + "*"))    # lives under the K-BOT prefix


# ---------- guard accepts a Redis-minted token ----------

def test_guard_accepts_redis_token(monkeypatch):
    store, _ = _store()
    monkeypatch.setattr("routes.auth.guard.STORE", store)
    app = Flask(__name__)

    @app.route("/protected")
    @require_session
    def protected():
        return jsonify(db=g.session.db_name)

    app.config["TESTING"] = True
    token, _ = _create(store)
    r = app.test_client().get("/protected",
                              headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.get_json() == {"db": "000_DEMO"}
