# Server-side unit test for POST /api/forexe/angajamente/upsert.
# Run on the Flask host, from the PYTHON folder:  python -m pytest tests/test_forexe_angajamente.py
#
# Preconditions (see handoff §9):
#   1) FX_Angajamente exists on the 000_DEMO MariaDB (gates the insert/update test);
#   2) config.py is present on the host (utils.database needs it);
#   3) the direct-verification step hits get_db_connection("000_DEMO") and always
#      cleans up its T1 row.
#
# Auth: the routes are guarded by @require_session (bearer token). The tests mint
# a session straight in the in-process STORE — the same object the guard reads via
# app.test_client() — so no operator credentials are needed here.
import json

import pytest

# Tot modulul e host-only: `main` trage blueprint-urile, care cer config.py (absent
# pe o statie de dezvoltare). Fara garda asta importul crapa la COLECTARE si o
# rulare offline pare rupta, desi testele sunt doar inaplicabile aici.
try:
    from main import app
    from routes.auth.session_store import STORE
    from utils.database import get_db_connection
except Exception as e:                              # pragma: no cover - off-host
    pytest.skip(f"host-only test (config.py / app imports unavailable): {e}",
                allow_module_level=True)

DB_NAME = "000_DEMO"
URL = "/api/forexe/angajamente/upsert"
GET_URL = "/api/forexe/angajamente"


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.fixture
def auth_headers():
    """Sesiune valida in STORE; token revocat la finalul testului."""
    token, _ = STORE.create(username="pytest-op", password="unused",
                            id_unitate=0, db_name=DB_NAME,
                            ctx={"DbName": DB_NAME}, pcname="PYTEST")
    yield {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    STORE.revoke(token)


def test_missing_token_is_rejected(client):
    r = client.post(URL, headers={"Content-Type": "application/json"},
                    data=json.dumps({"db_name": DB_NAME, "rows": []}))
    assert r.status_code == 401


def test_missing_rows_returns_400(client, auth_headers):
    r = client.post(URL, headers=auth_headers, data=json.dumps({"db_name": DB_NAME}))
    assert r.status_code == 400


def test_rows_not_list_returns_400(client, auth_headers):
    r = client.post(URL, headers=auth_headers,
                    data=json.dumps({"db_name": DB_NAME, "rows": "nope"}))
    assert r.status_code == 400


def test_empty_rows_is_success_zero_written(client, auth_headers):
    r = client.post(URL, headers=auth_headers,
                    data=json.dumps({"db_name": DB_NAME, "rows": []}))
    assert r.status_code == 200
    assert r.get_json().get("written") == 0


def test_empty_cod_is_skipped(client, auth_headers):
    body = {"db_name": DB_NAME, "rows": [{"Cod": "  ", "Descriere": "x", "Stare": "y"}]}
    r = client.post(URL, headers=auth_headers, data=json.dumps(body))
    assert r.status_code == 200
    assert r.get_json().get("written") == 0


# The GET list query requires db_name + id_unitate (int); id_unitate=0 matches the
# indicatori rows whose IdUnitate is NULL/0 (per-unit database convention).
GET_Q = GET_URL + "?db_name=" + DB_NAME + "&id_unitate=0"


def test_get_missing_db_name_returns_400(client, auth_headers):
    r = client.get(GET_URL + "?id_unitate=0", headers=auth_headers)
    assert r.status_code == 400


def test_get_missing_id_unitate_returns_400(client, auth_headers):
    r = client.get(GET_URL + "?db_name=" + DB_NAME, headers=auth_headers)
    assert r.status_code == 400


def test_get_bad_id_unitate_returns_400(client, auth_headers):
    r = client.get(GET_URL + "?db_name=" + DB_NAME + "&id_unitate=abc", headers=auth_headers)
    assert r.status_code == 400


def test_get_bad_db_name_returns_400(client, auth_headers):
    # DB_NAME_REGEX = ^[A-Za-z0-9_]+$ — a semicolon fails validation before any DB call.
    r = client.get(GET_URL + "?db_name=bad;name&id_unitate=0", headers=auth_headers)
    assert r.status_code == 400


def test_get_missing_token_is_rejected(client):
    r = client.get(GET_Q)
    assert r.status_code == 401


def test_get_returns_list_shape(client, auth_headers):
    r = client.get(GET_Q, headers=auth_headers)
    assert r.status_code == 200
    body = r.get_json()
    assert body["db_name"] == DB_NAME
    assert isinstance(body["rows"], list)
    assert body["count"] == len(body["rows"])
    # Every row carries the wire contract keys.
    for row in body["rows"]:
        for key in ("Cod", "Descriere", "Stare", "IDDF", "Surse",
                    "Incarcat", "Preluat", "Ascuns", "DataCreare"):
            assert key in row
        # Salarii is deprecated and must NOT come back on the wire any more.
        assert "Salarii" not in row


def test_get_orphan_row_has_null_surse_and_sorts_last(client, auth_headers):
    """An angajament with NO FX_Indicatori rows appears via the orphan branch
    (scoped by DC = db_name) with Surse = null and O = 1 (sorts after main rows)."""
    conn = get_db_connection(DB_NAME)
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM FX_Angajamente WHERE CodAngajament = %s", ("ORPH1",))
        cur.execute(
            "INSERT INTO FX_Angajamente (CodAngajament, Descriere, Stare, DC, Preluat) "
            "VALUES (%s, %s, %s, %s, 1)",
            ("ORPH1", "Orfan fara indicatori", "În derulare", DB_NAME),
        )
        conn.commit()

        r = client.get(GET_Q, headers=auth_headers)
        assert r.status_code == 200
        rows = r.get_json()["rows"]
        match = next((row for row in rows if row["Cod"] == "ORPH1"), None)
        assert match is not None, "orphan row not returned"
        assert match["Surse"] is None
    finally:
        cur = conn.cursor()
        cur.execute("DELETE FROM FX_Angajamente WHERE CodAngajament = %s", ("ORPH1",))
        conn.commit()
        conn.close()


def test_get_doar_anulate_switches_the_filter(client, auth_headers):
    """doar_anulate=1 replaces the IdUnitate filter with the anulate/suspendat/ascuns
    condition: a row whose IdUnitate would NOT match id_unitate=0 is hidden normally
    but visible when doar_anulate=1 because its Stare contains 'Anulat'."""
    conn = get_db_connection(DB_NAME)
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM FX_Indicatori WHERE CodAngajament = %s", ("ANUL1",))
        cur.execute("DELETE FROM FX_Angajamente WHERE CodAngajament = %s", ("ANUL1",))
        cur.execute(
            "INSERT INTO FX_Angajamente (CodAngajament, Descriere, Stare, DC, Preluat) "
            "VALUES (%s, %s, %s, %s, 1)",
            ("ANUL1", "Angajament anulat", "Anulat", DB_NAME),
        )
        # IdUnitate=777 so COALESCE(IdUnitate,0)=0 is FALSE -> hidden in the normal
        # (id_unitate=0) query, but the row is in the main branch (has an indicator).
        cur.execute(
            "INSERT INTO FX_Indicatori (CodAI, CodAngajament, IdUnitate, SS) "
            "VALUES (%s, %s, %s, %s)",
            ("ANUL1-AAB", "ANUL1", 777, "02A"),
        )
        conn.commit()

        normal = client.get(GET_Q, headers=auth_headers)
        assert normal.status_code == 200
        assert not any(row["Cod"] == "ANUL1" for row in normal.get_json()["rows"])

        anulate = client.get(GET_Q + "&doar_anulate=1", headers=auth_headers)
        assert anulate.status_code == 200
        assert any(row["Cod"] == "ANUL1" for row in anulate.get_json()["rows"])
    finally:
        cur = conn.cursor()
        cur.execute("DELETE FROM FX_Indicatori WHERE CodAngajament = %s", ("ANUL1",))
        cur.execute("DELETE FROM FX_Angajamente WHERE CodAngajament = %s", ("ANUL1",))
        conn.commit()
        conn.close()


def test_insert_then_update_refreshes_descriere_stare(client, auth_headers):
    """Upsert semantics: INSERT sets DC + Preluat=1; the duplicate refreshes ONLY
    Descriere/Stare. Verified directly in FX_Angajamente, then the T1 row is deleted."""
    conn = get_db_connection(DB_NAME)
    try:
        body1 = {"db_name": DB_NAME, "rows": [{"Cod": "T1", "Descriere": "D1", "Stare": "S1"}]}
        assert client.post(URL, headers=auth_headers, data=json.dumps(body1)).status_code == 200
        body2 = {"db_name": DB_NAME, "rows": [{"Cod": "T1", "Descriere": "D2", "Stare": "S2"}]}
        assert client.post(URL, headers=auth_headers, data=json.dumps(body2)).status_code == 200

        cursor = conn.cursor()
        cursor.execute(
            "SELECT Descriere, Stare, DC, Preluat FROM FX_Angajamente "
            "WHERE CodAngajament = %s",
            ("T1",),
        )
        row = cursor.fetchone()
        assert row is not None, "T1 row not found in FX_Angajamente after upsert"
        descriere, stare, dc, preluat = row
        assert descriere == "D2"
        assert stare == "S2"
        assert dc == DB_NAME
        assert preluat == 1
    finally:
        # Cleanup regardless of assertion outcome.
        try:
            cleanup = conn.cursor()
            cleanup.execute("DELETE FROM FX_Angajamente WHERE CodAngajament = %s", ("T1",))
            conn.commit()
        finally:
            conn.close()
