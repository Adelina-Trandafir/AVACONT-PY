"""
Teste LIVE pentru login-ul aplicatiei K-BOT (token bearer opac), lovind API-ul
care ruleaza. Se skip-uiesc cand variabilele de mediu lipsesc SAU cand `requests`
nu e instalat, deci o rulare offline ramane verde.

Preconditii: un rand in Unitati cu DC='000_DEMO' si un cont operator de test cu
grant pe 000_DEMO in Unitati_Utilizatori. Nu exista X-Api-Key: units/login se
autentifica prin credentiale, rutele de date prin Authorization: Bearer.

Contract (verificat in routes/auth/auth.py, 2026-07-15):
  POST /api/auth/units  {username, password}
       -> 200 {"units": [{"DC": ..., "NumeUnitate": ...}]}
  POST /api/auth/login  {username, password, db_name, machine}
       -> 200 {"Token", "SessionContext": {DbName, NumeUnitate, CF, Role}, "LastSS"}
       -> 403 daca operatorul nu are grant pe acel DC
Unitatea e identificata prin DC (numele bazei), nu printr-un IdUnitate numeric;
campul de masina se numeste `machine`. Rolul vine din coloana Rol a tabelei
Unitati_Utilizatori — NU e derivat din sufixul username-ului.

Env: KBOT_API_BASE_URL, TEST_OP_USER, TEST_OP_PASS,
     optional TEST_OP_DENIED_DC (un DC pentru care operatorul NU are grant).
"""
import os

import pytest

# `requests` lipseste din venv-ul de dezvoltare; fara importorskip modulul crapa
# la colectare si o rulare offline pare rupta desi testele sunt doar inaplicabile.
requests = pytest.importorskip("requests")

BASE = os.getenv("KBOT_API_BASE_URL")
USER = os.getenv("TEST_OP_USER")
PWD = os.getenv("TEST_OP_PASS")

DEMO_DC = "000_DEMO"
MACHINE = "PYTEST"

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
    dcs = [u["DC"] for u in r.json()["units"]]
    assert DEMO_DC in dcs


def _login():
    return _post("/api/auth/login",
                 {"username": USER, "password": PWD,
                  "db_name": DEMO_DC, "machine": MACHINE})


def test_login_returns_token_context_and_role():
    r = _login()
    assert r.status_code == 200
    body = r.json()
    assert body["Token"]                      # token opac ne-gol
    assert "session_id" not in body           # contractul vechi a disparut
    ctx = body["SessionContext"]
    assert ctx["DbName"] == DEMO_DC
    assert ctx["NumeUnitate"]
    # Rolul e citit din Unitati_Utilizatori.Rol; aici verificam doar ca soseste
    # populat — valoarea concreta depinde de grantul contului de test.
    assert ctx["Role"]


def test_login_missing_db_name_returns_400():
    r = _post("/api/auth/login",
              {"username": USER, "password": PWD, "machine": MACHINE})
    assert r.status_code == 400


def test_logout_revokes_token_immediately():
    token = _login().json()["Token"]
    first = _post("/api/auth/logout", {}, token=token)
    assert first.status_code == 200 and first.json()["ok"] is True
    # Token-ul revocat e mort instant: guard-ul intoarce 401.
    second = _post("/api/auth/logout", {}, token=token)
    assert second.status_code == 401


def test_data_route_without_token_returns_401():
    r = requests.get(f"{BASE}/api/forexe/angajamente?db_name={DEMO_DC}", timeout=30)
    assert r.status_code == 401


def test_data_route_with_token_succeeds():
    token = _login().json()["Token"]
    try:
        r = requests.get(f"{BASE}/api/forexe/angajamente?db_name={DEMO_DC}",
                         headers={"Authorization": f"Bearer {token}"}, timeout=30)
        assert r.status_code == 200
    finally:
        _post("/api/auth/logout", {}, token=token)


@pytest.mark.skipif(not os.getenv("TEST_OP_DENIED_DC"),
                    reason="No denied-DC configured")
def test_login_rejects_unit_without_grant():
    denied = os.getenv("TEST_OP_DENIED_DC")
    r = _post("/api/auth/login",
              {"username": USER, "password": PWD,
               "db_name": denied, "machine": MACHINE})
    assert r.status_code == 403
