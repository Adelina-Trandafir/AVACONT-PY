# Tests for POST /api/admin/receptii/refacere -- the keyed, session-less twin of
# /api/forexe/receptii/refacere (slice 0062). OFFLINE: a bare Flask app with only
# admin_bp, and `executa_refacerea` replaced by a recorder, so no database is touched.
import json

import pytest
from flask import Flask

try:
    from routes.admin import admin_bp
    import routes.forexe.receptii_refacere as RR
    from config import API_KEY
except Exception as e:                              # pragma: no cover - broken install
    pytest.skip(f"imports unavailable: {e}", allow_module_level=True)

URL = "/api/admin/receptii/refacere"

_app = Flask(__name__)
_app.register_blueprint(admin_bp)
_app.config["TESTING"] = True


@pytest.fixture
def client():
    with _app.test_client() as c:
        yield c


@pytest.fixture
def recorder(monkeypatch):
    calls = []

    def fake(db_name, cod, toate, aplica):
        calls.append((db_name, cod, toate, aplica))
        if toate:
            return {"toate": True, "aplicat": aplica, "angajamente": 3, "cu_lipsuri": 1,
                    "totaluri": {}, "detalii": [], "erori": []}
        return {"cod": cod, "aplicat": aplica, "antete_lipsa": 1,
                "avertismente": ["Rândul 7 n-are indicator în FX_Indicatori"]}

    monkeypatch.setattr(RR, "executa_refacerea", fake)
    return calls


def _post(client, body, key=API_KEY):
    headers = {"Content-Type": "application/json"}
    if key is not None:
        headers["X-Api-Key"] = key
    return client.post(URL, data=json.dumps(body), headers=headers)


def test_missing_or_wrong_key_is_401(client, recorder):
    assert _post(client, {"db_name": "000_DEMO", "toate": True}, key=None).status_code == 401
    assert _post(client, {"db_name": "000_DEMO", "toate": True}, key="nope").status_code == 401
    assert recorder == []


def test_invalid_db_name_is_400_before_anything_runs(client, recorder):
    assert _post(client, {"toate": True}).status_code == 400
    assert _post(client, {"db_name": "000_DEMO; DROP", "toate": True}).status_code == 400
    assert recorder == []


def test_missing_cod_and_toate_is_400(client, recorder):
    r = _post(client, {"db_name": "000_DEMO"})
    assert r.status_code == 400
    assert "cod" in r.get_json()["error"]
    assert recorder == []


def test_whole_database_goes_through_with_the_named_db(client, recorder):
    r = _post(client, {"db_name": "000_DEMO", "toate": True, "aplica": True})
    assert r.status_code == 200
    body = r.get_json()
    assert body["db_name"] == "000_DEMO"
    assert body["angajamente"] == 3
    assert recorder == [("000_DEMO", "", True, True)]


def test_single_cod_defaults_to_dry_run_and_keeps_literal_diacritics(client, recorder):
    r = _post(client, {"db_name": "000_DEMO", "cod": "AAB37CNBK95"})
    assert r.status_code == 200
    assert recorder == [("000_DEMO", "AAB37CNBK95", False, False)]
    # ensure_ascii=False: the operator reads «ă», not «\\u0103».
    assert "indicator în" in r.get_data(as_text=True)


def test_database_failure_is_500_with_the_reason(client, monkeypatch):
    def boom(db_name, cod, toate, aplica):
        raise RuntimeError("Unknown database '000_NOPE'")

    monkeypatch.setattr(RR, "executa_refacerea", boom)
    r = _post(client, {"db_name": "000_NOPE", "toate": True})
    assert r.status_code == 500
    assert "000_NOPE" in r.get_json()["error"]
