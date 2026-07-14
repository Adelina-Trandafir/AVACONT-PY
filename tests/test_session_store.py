# Offline unit tests for the K-BOT auth session store + bearer guard.
# No DB, no config.py, no network — runs anywhere:  python -m pytest tests/test_session_store.py
#
# Covers the Felia 1 contract: opaque CSPRNG token, 20-min sliding idle window,
# 30-min absolute cap, instant revocation, and the guard's 401 behaviour.
import sys
import time
import types

import pytest
from flask import Flask, jsonify


# ---------------------------------------------------------------------------
# Make routes.auth importable even without a real config.py / DB driver
# (same pattern as test_ddf_global_id.py): the package __init__ pulls auth.py,
# which imports config and mysql.connector. On the Flask host both exist; on a
# bare dev box we stub them so the store/guard under test import cleanly.
# ---------------------------------------------------------------------------
def _ensure_importable():
    if 'config' not in sys.modules:
        try:
            import config  # noqa: F401
        except Exception:
            cfg = types.ModuleType('config')
            cfg.DB_CONFIG = {}
            cfg.API_KEY = 'test'
            sys.modules['config'] = cfg
    if 'mysql.connector' not in sys.modules:
        try:
            import mysql.connector  # noqa: F401
        except Exception:
            mysql_mod = types.ModuleType('mysql')
            conn_mod = types.ModuleType('mysql.connector')

            class _Err(Exception):
                errno = None

            conn_mod.Error = _Err
            conn_mod.errorcode = types.SimpleNamespace(ER_ACCESS_DENIED_ERROR=1045)

            def _no_connect(**_kw):
                raise RuntimeError('no db driver')

            conn_mod.connect = _no_connect
            mysql_mod.connector = conn_mod
            sys.modules['mysql'] = mysql_mod
            sys.modules['mysql.connector'] = conn_mod


_ensure_importable()

from routes.auth import session_store
from routes.auth.session_store import SessionStore
from routes.auth.guard import require_session, require_session_or_api_key

CTX = {"DbName": "000_DEMO"}


def _create(store, **kw):
    args = dict(username="op", password="pw", id_unitate=1,
                db_name="000_DEMO", ctx=CTX, pcname="PC")
    args.update(kw)
    return store.create(**args)


# ---------- token shape ----------

def test_token_is_opaque_and_unique():
    store = SessionStore()
    t1, _ = _create(store)
    t2, _ = _create(store)
    assert t1 != t2
    assert len(t1) >= 43          # 32 octeti url-safe => ~43 caractere


def test_create_returns_live_session_with_password_in_memory_only():
    store = SessionStore()
    token, s = _create(store)
    assert s.username == "op" and s.password == "pw"
    assert store.validate_and_touch(token) is s


# ---------- validate / expiry ----------

def test_unknown_or_empty_token_is_rejected():
    store = SessionStore()
    assert store.validate_and_touch("") is None
    assert store.validate_and_touch(None) is None
    assert store.validate_and_touch("no-such-token") is None


def test_idle_expiry_kills_session(monkeypatch):
    store = SessionStore()
    token, _ = _create(store)
    real_time = time.time()
    monkeypatch.setattr(time, "time", lambda: real_time + session_store._TTL_SECONDS + 1)
    assert store.validate_and_touch(token) is None
    assert token not in store._by_token       # maturat lenes


def test_active_use_slides_the_idle_window(monkeypatch):
    store = SessionStore()
    token, _ = _create(store)
    real_time = time.time()
    # Atinge sesiunea la fiecare 15 min: idle-ul nu o omoara niciodata...
    monkeypatch.setattr(time, "time", lambda: real_time + 15 * 60)
    assert store.validate_and_touch(token) is not None
    monkeypatch.setattr(time, "time", lambda: real_time + 28 * 60)
    assert store.validate_and_touch(token) is not None


def test_absolute_cap_expires_even_under_continuous_use(monkeypatch):
    store = SessionStore()
    token, _ = _create(store)
    real_time = time.time()
    # ...dar plafonul absolut (30 min de la emitere) loveste oricum, chiar cu
    # atingeri continue in interiorul ferestrei de idle.
    monkeypatch.setattr(time, "time", lambda: real_time + 15 * 60)
    assert store.validate_and_touch(token) is not None
    monkeypatch.setattr(time, "time", lambda: real_time + 29 * 60)
    assert store.validate_and_touch(token) is not None
    monkeypatch.setattr(time, "time",
                        lambda: real_time + session_store._ABS_TTL_SECONDS + 1)
    assert store.validate_and_touch(token) is None


def test_slide_never_extends_past_absolute_cap(monkeypatch):
    store = SessionStore()
    token, s = _create(store)
    real_time = time.time()
    monkeypatch.setattr(time, "time", lambda: real_time + 15 * 60)
    assert store.validate_and_touch(token) is not None
    # Atins la minutul 25: glisarea (25+20=45) e taiata la plafonul absolut (30).
    monkeypatch.setattr(time, "time", lambda: real_time + 25 * 60)
    assert store.validate_and_touch(token) is not None
    assert s.expires_at <= s.issued_at + session_store._ABS_TTL_SECONDS


# ---------- revoke / sweep ----------

def test_revoke_kills_token_instantly():
    store = SessionStore()
    token, _ = _create(store)
    store.revoke(token)
    assert store.validate_and_touch(token) is None
    store.revoke(token)                       # idempotent


def test_sweep_purges_expired_sessions(monkeypatch):
    store = SessionStore()
    token, _ = _create(store)
    real_time = time.time()
    monkeypatch.setattr(time, "time", lambda: real_time + session_store._TTL_SECONDS + 1)
    store.sweep()
    assert token not in store._by_token


# ---------- guard ----------

@pytest.fixture
def guard_app(monkeypatch):
    """Flask minimal cu o ruta protejata de require_session, pe un STORE curat."""
    fresh = SessionStore()
    monkeypatch.setattr("routes.auth.guard.STORE", fresh)
    app = Flask(__name__)

    @app.route("/protected")
    @require_session
    def protected():
        from flask import g
        return jsonify(db=g.session.db_name, user=g.session.username)

    app.config["TESTING"] = True
    return app, fresh


def test_guard_rejects_missing_header(guard_app):
    app, _ = guard_app
    r = app.test_client().get("/protected")
    assert r.status_code == 401
    assert "Autentificați-vă" in r.get_json()["error"]


def test_guard_rejects_malformed_and_unknown_tokens(guard_app):
    app, _ = guard_app
    c = app.test_client()
    assert c.get("/protected", headers={"Authorization": "Basic abc"}).status_code == 401
    assert c.get("/protected", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_guard_accepts_live_token_and_exposes_g_session(guard_app):
    app, store = guard_app
    token, _ = _create(store)
    r = app.test_client().get("/protected",
                              headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.get_json() == {"db": "000_DEMO", "user": "op"}


def test_guard_rejects_revoked_token(guard_app):
    app, store = guard_app
    token, _ = _create(store)
    store.revoke(token)
    r = app.test_client().get("/protected",
                              headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


# ---------- guard de tranzitie (bearer SAU X-Api-Key legacy) ----------
# Acopera contractul rutelor partajate cu FOREXE vechi (process_excel):
# bearer-ul K-BOT e judecat strict, cheia legacy trece doar FARA Authorization.

@pytest.fixture
def dual_app(monkeypatch):
    """Flask minimal cu o ruta pe require_session_or_api_key, STORE curat."""
    fresh = SessionStore()
    monkeypatch.setattr("routes.auth.guard.STORE", fresh)
    app = Flask(__name__)

    @app.route("/shared", methods=["POST"])
    @require_session_or_api_key
    def shared():
        from flask import g
        has_session = hasattr(g, "session")
        return jsonify(via="bearer" if has_session else "apikey")

    app.config["TESTING"] = True
    return app, fresh


def test_dual_guard_rejects_anonymous(dual_app):
    app, _ = dual_app
    assert app.test_client().post("/shared").status_code == 401


def test_dual_guard_accepts_live_bearer(dual_app):
    app, store = dual_app
    token, _ = _create(store)
    r = app.test_client().post("/shared",
                               headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.get_json()["via"] == "bearer"


def test_dual_guard_accepts_legacy_api_key(dual_app):
    app, _ = dual_app
    # config-ul (real sau stub-ul de test) e sursa cheii legacy
    from config import API_KEY
    r = app.test_client().post("/shared", headers={"X-Api-Key": API_KEY})
    assert r.status_code == 200
    assert r.get_json()["via"] == "apikey"


def test_dual_guard_rejects_wrong_api_key(dual_app):
    app, _ = dual_app
    r = app.test_client().post("/shared", headers={"X-Api-Key": "gresit"})
    assert r.status_code == 401


def test_dual_guard_expired_bearer_does_not_fall_back_to_key(dual_app):
    # Un client care a INTENTIONAT bearer primeste mesajul de re-login,
    # chiar daca ar fi trimis (ipotetic) si cheia legacy in aceeasi cerere.
    app, store = dual_app
    token, _ = _create(store)
    store.revoke(token)
    from config import API_KEY
    r = app.test_client().post("/shared",
                               headers={"Authorization": f"Bearer {token}",
                                        "X-Api-Key": API_KEY})
    assert r.status_code == 401
    assert "Autentificați-vă" in r.get_json()["error"]
