# Offline tests for slice 0081-07: a capture already stored on the revision (same bytes) is not
# stored again -- routes/forexe/ddf_trimitere.py::_captura_existenta / _captura.
# No database: a fake dictionary cursor answers the queries in order.
# Written, not run (house rule).
import base64
import hashlib

import pytest

try:
    from routes.forexe import ddf_trimitere as mod
except Exception as e:                              # pragma: no cover - off-host
    pytest.skip(f"host-only import (config.py unavailable): {e}",
                allow_module_level=True)


class _Cursor:
    """Answers fetchone() from a queue, in the order the code asks."""

    def __init__(self, answers):
        self._answers = list(answers)
        self.executed = []
        self.lastrowid = 0

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return self._answers.pop(0) if self._answers else None


PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 20
SHA = hashlib.sha256(PNG).hexdigest()


def test_existing_capture_found_by_sha_when_the_image_table_exists():
    cur = _Cursor([{"n": 1}, {"id": 42}])
    assert mod._captura_existenta(cur, 10, PNG, SHA) == 42
    sql, params = cur.executed[-1]
    assert "FX_DDF_REV_ATT_IMG" in sql and "Sha256" in sql
    assert params == (10, SHA)


def test_existing_capture_found_by_base64_on_an_old_database():
    cur = _Cursor([{"n": 0}, {"id": 7}])
    assert mod._captura_existenta(cur, 10, PNG, SHA) == 7
    sql, params = cur.executed[-1]
    assert "DateFisier" in sql
    assert params == (10, base64.b64encode(PNG).decode("ascii"))


def test_no_existing_capture_gives_zero():
    cur = _Cursor([{"n": 1}, None])
    assert mod._captura_existenta(cur, 10, PNG, SHA) == 0


def test_captura_does_not_insert_a_duplicate(monkeypatch):
    monkeypatch.setattr(mod, "citeste_revizia",
                        lambda cursor, idrev: {"IDDF": 3, "StareTrimitere": mod.STAGE_INTERRUPTED})
    monkeypatch.setattr(mod, "_captura_existenta", lambda cursor, idrev, octeti, sha: 42)
    cur = _Cursor([])
    rezultat = mod._captura(cur, 10, "Poza_IdSecA-1.png", PNG, SHA)
    assert rezultat == {"id_rev_att": 42, "dimensiune": len(PNG), "exista_deja": True}
    assert not any("INSERT" in sql for sql, _ in cur.executed)
