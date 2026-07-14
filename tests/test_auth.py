"""
Teste LIVE pentru login-ul aplicatiei K-BOT (token bearer opac), lovind API-ul
care ruleaza. Se skip-uiesc cand variabilele de mediu lipsesc, deci CI fara DB
ramane verde.

Preconditii: un rand CAI cu DbName='000_DEMO' si un cont operator de test
(ideal '000_DEMO_Contabil' ca asertiunea de rol sa fie relevanta) cu grant pe
000_DEMO. Nu mai exista X-Api-Key: units/login se autentifica prin credentiale,
rutele de date prin Authorization: Bearer.

Env: KBOT_API_BASE_URL, TEST_OP_USER, TEST_OP_PASS,
     optional TEST_OP_DENIED_UNIT (un IdUnitate pentru care operatorul nu are grant).
"""
import os

import pytest
import requests

BASE = os.getenv("KBOT_API_BASE_URL")
USER = os.getenv("TEST_OP_USER")
PWD = os.getenv("TEST_OP_PASS")

pytestmark = pytest.mark.skipif(
    not all([BASE, USER, PWD]),
    reason="Auth live env vars not set",
)


def _post(path, body, token=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return requests.post(f"{BASE}{path}", json=body, headers=headers, timeout=30)


def test_units_bad_credentials_returns_401():
    r = _post("/api/auth/units", {"username": USER, "password": PWD + "_wrong"})
    assert r.status_code == 401


def test_units_good_credentials_lists_demo():
    r = _post("/api/auth/units", {"username": USER, "password": PWD})
    assert r.status_code == 200
    dbs = [u["DbName"] for u in r.json()["units"]]
    assert "000_DEMO" in dbs


def _demo_id_unitate():
    r = _post("/api/auth/units", {"username": USER, "password": PWD})
    return next(u["IdUnitate"] for u in r.json()["units"] if u["DbName"] == "000_DEMO")


def _login():
    idu = _demo_id_unitate()
    return _post("/api/auth/login",
                 {"username": USER, "password": PWD, "IdUnitate": idu, "pcname": "PYTEST"})


def test_login_returns_token_context_and_role():
    r = _login()
    assert r.status_code == 200
    body = r.json()
    assert body["Token"]                      # token opac ne-gol
    assert "session_id" not in body           # contractul vechi a disparut
    ctx = body["SessionContext"]
    assert ctx["DbName"] == "000_DEMO"
    # rolul e derivat din sufixul username-ului (store-only)
    if USER.endswith("_Contabil"):
        assert ctx["Role"] == "Contabil"
    elif USER.endswith("_Administrator"):
        assert ctx["Role"] == "Administrator"


def test_logout_revokes_token_immediately():
    token = _login().json()["Token"]
    first = _post("/api/auth/logout", {}, token=token)
    assert first.status_code == 200 and first.json()["ok"] is True
    # Token-ul revocat e mort instant: guard-ul intoarce 401.
    second = _post("/api/auth/logout", {}, token=token)
    assert second.status_code == 401


def test_data_route_without_token_returns_401():
    r = requests.get(f"{BASE}/api/forexe/angajamente?db_name=000_DEMO", timeout=30)
    assert r.status_code == 401


def test_data_route_with_token_succeeds():
    token = _login().json()["Token"]
    r = requests.get(f"{BASE}/api/forexe/angajamente?db_name=000_DEMO",
                     headers={"Authorization": f"Bearer {token}"}, timeout=30)
    assert r.status_code == 200
    _post("/api/auth/logout", {}, token=token)


@pytest.mark.skipif(not os.getenv("TEST_OP_DENIED_UNIT"),
                    reason="No denied-unit configured")
def test_login_rejects_unit_without_grant():
    denied = int(os.getenv("TEST_OP_DENIED_UNIT"))
    r = _post("/api/auth/login",
              {"username": USER, "password": PWD, "IdUnitate": denied, "pcname": "PYTEST"})
    assert r.status_code == 403
