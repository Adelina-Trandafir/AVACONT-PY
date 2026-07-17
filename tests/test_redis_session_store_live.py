# tests/test_redis_session_store_live.py
#
# Teste LIVE pentru RedisSessionStore, rulate DOAR pe gazda unde exista config.py
# si un Redis pornit. Off-host toate se skip-uiesc curat (nu pica, nu dau eroare):
# vezi _live_client(), care sare peste test la orice lipsa de config sau Redis.
#
# ATENTIE: aceste teste dau flushdb() pe config.REDIS_DB. Baza aia trebuie sa fie
# dedicata K-BOT (REDIS_DB = 2 pe gazda), niciodata una cu date reale.
#
# Acoperirea offline a aceluiasi cod traieste in test_redis_session_store.py, pe
# fakeredis — deci skip-ul de aici nu lasa calea Redis netestata.
import pytest

_redis = pytest.importorskip("redis")

# Garda de config trebuie sa fie INAINTEA importului din routes.auth: pachetul ala
# trage auth.py, care face `from config import DB_CONFIG` la nivel de modul — deci
# off-host importul crapa la COLECTARE inainte sa apuce vreun skip din corpul unui test.
#
# `import config` poate REUSI totusi si off-host: test_redis_session_store.py pune un
# modul `config` de proba in sys.modules (cu doar DB_CONFIG/API_KEY), iar acela ramane
# vizibil in restul rularii. Deci prezenta modulului nu dovedeste nimic — cerem
# atributele Redis efective.
try:
    import config
except Exception:                                   # pragma: no cover - off-host
    config = None

if config is None or not hasattr(config, "REDIS_HOST"):
    pytest.skip("no host config.py with Redis settings (off-host)",
                allow_module_level=True)

from routes.auth.session_store import RedisSessionStore


def _prefix():
    # Numele curent e SESSION_KEY_PREFIX (fostul REDIS_KEY_PREFIX, redenumit odata
    # cu SESSION_BACKEND). Fallback-ul acopera o gazda inca neactualizata.
    return getattr(config, "SESSION_KEY_PREFIX",
                   getattr(config, "REDIS_KEY_PREFIX", "kbot:sess:"))


def _live_client():
    """Create a Redis client and prove it answers, or skip (same pattern as
    _live_primary_conn in test_ddf_global_id.py)."""
    client = _redis.StrictRedis(
        host=config.REDIS_HOST,
        port=config.REDIS_PORT,
        password=getattr(config, "REDIS_PASSWORD", None) or None,
        db=getattr(config, "REDIS_DB", 0),
        decode_responses=True,
    )
    try:
        client.ping()
    except Exception as e:
        pytest.skip(f"no live Redis at {config.REDIS_HOST}:{config.REDIS_PORT}: {e}")
    return client


def _live_store():
    client = _live_client()
    # Wipe the test database
    client.flushdb()
    return RedisSessionStore(client, key_prefix=_prefix()), client


def _create(store, **kw):
    args = dict(username="op", password="", id_unitate=0,
                db_name="000_DEMO", ctx={"DbName": "000_DEMO"}, pcname="PC")
    args.update(kw)
    return store.create(**args)


# ---------- connection check as a test, not at import time ----------

def test_redis_connection():
    """Verify we can connect and authenticate."""
    client = _live_client()
    assert client.ping() is True


def test_create_and_validate_live():
    store, client = _live_store()
    token, s = _create(store)
    assert s.db_name == "000_DEMO"
    got = store.validate_and_touch(token)
    assert got is not None and got.username == "op"


def test_unknown_token_live():
    store, client = _live_store()
    assert store.validate_and_touch("no-such") is None


def test_revoke_live():
    store, client = _live_store()
    token, _ = _create(store)
    store.revoke(token)
    assert store.validate_and_touch(token) is None


def test_password_not_persisted_live():
    store, client = _live_store()
    token, s = _create(store, password="super-secret")
    raw = client.get(_prefix() + token)
    assert "super-secret" not in raw
    assert store.validate_and_touch(token).password == ""


def test_keys_are_namespaced_live():
    store, client = _live_store()
    token, _ = _create(store)
    assert client.exists(_prefix() + token) == 1
