# tests/test_redis_session_store_live.py
import pytest
import redis as _redis

import config

from routes.auth.session_store import RedisSessionStore
from routes.auth import session_store

PREFIX = getattr(config, 'REDIS_KEY_PREFIX', 'kbot:sess:')


def _live_client():
    """Create a Redis client. Raises if connection/auth fails."""
    return _redis.StrictRedis(
        host=config.REDIS_HOST,
        port=config.REDIS_PORT,
        password=getattr(config, 'REDIS_PASSWORD', None) or None,
        db=getattr(config, 'REDIS_DB', 0),
        decode_responses=True
    )


def _live_store():
    client = _live_client()
    # Wipe the test database
    client.flushdb()
    return RedisSessionStore(client, key_prefix=PREFIX), client


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
    raw = client.get(PREFIX + token)
    assert "super-secret" not in raw
    assert store.validate_and_touch(token).password == ""


def test_keys_are_namespaced_live():
    store, client = _live_store()
    token, _ = _create(store)
    assert client.exists(PREFIX + token) == 1