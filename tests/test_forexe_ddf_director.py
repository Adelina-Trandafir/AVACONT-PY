# Offline tests for the director's list (slice 0081-06): routes/forexe/ddf_director.py.
# No database: the two connections are replaced with fakes, and the route body is called
# through __wrapped__ (require_session uses functools.wraps) inside a Flask request context.
# Written, not run (house rule).
import json
from datetime import date
from types import SimpleNamespace

import pytest

try:
    from flask import Flask, g
    import mysql.connector
    from routes.forexe import ddf_director as mod
except Exception as e:                              # pragma: no cover - off-host
    pytest.skip(f"host-only import (config.py unavailable): {e}",
                allow_module_level=True)


class _Cursor:
    def __init__(self, rows, column_present=True):
        self._rows = rows
        self._column_present = column_present
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        # are_stare_trimitere's probe (tuple cursor).
        return (1 if self._column_present else 0,)

    def fetchall(self):
        return self._rows


class _Conn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self, dictionary=False):
        return self._cursor

    def is_connected(self):
        return True

    def close(self):
        pass


def _row(idrev=10, semnatura="A,B"):
    return (idrev, 3, 7, "AAB2EF2MCP4", "Burse", 1, date(2026, 9, 25), "Scurt", semnatura,
            "ab" * 32, 1380.0)


def test_revizii_din_maps_the_row_and_names_the_unit(monkeypatch):
    cur = _Cursor([_row()])
    monkeypatch.setattr(mod, "get_kbot_connection", lambda dc: _Conn(cur))
    rezultat = mod._revizii_din("001_TEST", "Scoala 1")
    assert rezultat == [{
        "db_name": "001_TEST", "nume_unitate": "Scoala 1", "idrev": 10, "iddf": 3, "cual": 7,
        "cod_angajament": "AAB2EF2MCP4", "obiect_ddf": "Burse", "numar_rev": 1,
        "data_rev": "2026-09-25", "desc_scurta": "Scurt", "total": 1380.0,
        "semnatura": "A,B", "pdf_sha256": "ab" * 32,
    }]
    sql = cur.executed[-1][0]
    assert "FIND_IN_SET('Ordonator'" in sql
    assert "JOIN FX_DDF_PDF" in sql


def test_revizii_din_without_the_0081_column_uses_only_incarcat_preluat(monkeypatch):
    cur = _Cursor([], column_present=False)
    monkeypatch.setattr(mod, "get_kbot_connection", lambda dc: _Conn(cur))
    mod._revizii_din("002_FARA_COLOANA", "Scoala 2")
    sql = cur.executed[-1][0]
    assert "StareTrimitere" not in sql
    assert "(0 OR" in sql


def _call_route(role, monkeypatch, unitati=None, fail_dc=None):
    app = Flask(__name__)
    monkeypatch.setattr(mod, "_unitati_directorului", lambda un: unitati or [])

    def fake_revizii(dc, nume):
        if dc == fail_dc:
            raise mysql.connector.Error("down")
        return [{"db_name": dc, "nume_unitate": nume, "idrev": 1}]

    monkeypatch.setattr(mod, "_revizii_din", fake_revizii)
    with app.test_request_context("/api/forexe/ddf/director/de-semnat"):
        g.session = SimpleNamespace(username="dir@x.ro", ctx={"Role": role}, db_name="001")
        resp = mod.get_ddf_director_de_semnat.__wrapped__()
        return resp.status_code, json.loads(resp.get_data(as_text=True))


def test_route_refuses_a_non_director(monkeypatch):
    status, body = _call_route("Contabil", monkeypatch)
    assert status == 403
    assert "director" in body["error"]


def test_route_spans_every_unit_and_names_the_unreadable_one(monkeypatch):
    unitati = [{"DC": "001", "NumeUnitate": "Unu"}, {"DC": "002", "NumeUnitate": "Doi"},
               {"DC": "003", "NumeUnitate": "Trei"}]
    status, body = _call_route("Director", monkeypatch, unitati=unitati, fail_dc="002")
    assert status == 200
    assert [r["db_name"] for r in body["revizii"]] == ["001", "003"]
    assert body["unitati_necitite"] == ["Doi"]
