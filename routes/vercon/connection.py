"""SLICE-VC01-03: the one way VERCON code opens a MariaDB connection. It reuses the portal's connection
settings (utils.database); no pool and no session of its own. The connection is always closed here.

Writes are never retried automatically (a lost response is handled by the operation key, from VC03-03)."""
from contextlib import contextmanager


def _default_factory(db_name):
    from utils.database import get_kbot_connection
    return get_kbot_connection(db_name)


@contextmanager
def connection(db_name, factory=None):
    conn = (factory or _default_factory)(db_name)
    try:
        yield conn
    finally:
        try:
            conn.close()
        except Exception:  # a close error must not hide the real outcome
            import logging
            logging.getLogger("vercon").exception("VR connection close failed")
