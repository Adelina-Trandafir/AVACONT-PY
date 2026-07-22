# Server-side unit test for GET /api/forexe/seed/columns (slice 0012-01).
# Run on the Flask host, from the PYTHON folder:
#   python -m pytest tests/test_forexe_seed_columns.py
#
# Preconditions (same shape as test_forexe_tree.py):
#   1) the 000_DEMO MariaDB is reachable and holds at least FX_Angajamente;
#   2) config.py is present on the host (utils.database needs it).
#
# Scope: introspection only. The route is READ-ONLY — these tests write nothing and
# therefore need no fixture cleanup.
#
# Guard: X-Api-Key (legacy FOREXE fleet), NOT the bearer token. The seed routes are
# driven by VBA, so they use require_api_key. Do not mix the two guards.
import pytest

# Host-only module: `main` pulls the blueprints, which need config.py (absent on a dev
# station). Without this guard the import fails at COLLECTION and an offline run looks
# broken when the tests are merely inapplicable.
try:
    from main import app
    from config import API_KEY
except Exception as e:                              # pragma: no cover - off-host
    pytest.skip(f"host-only test (config.py / app imports unavailable): {e}",
                allow_module_level=True)

DB_NAME = "000_DEMO"
URL = "/api/forexe/seed/columns"

# Tabel din allow-list despre care stim ca exista pe 000_DEMO (il folosesc si
# test_forexe_tree.py / test_forexe_angajamente.py ca fixture).
EXISTING_TABLE = "FX_Angajamente"

# Tabel din allow-list care NU e migrat pe 000_DEMO — testul lui verifica exact ramura
# «tabel inexistent -> 200 cu lista goala». Daca migrarea il aduce candva, testul
# devine inaplicabil si se sare explicit, nu pica pe tacute.
MISSING_TABLE = "FX_Rezervarii_IMG"


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.fixture
def key_headers():
    return {"X-Api-Key": API_KEY}


def _q(db_name=DB_NAME, table=EXISTING_TABLE):
    return f"{URL}?db_name={db_name}&table={table}"


# ---------- garda ----------

def test_missing_api_key_is_rejected(client):
    """Fara X-Api-Key ruta nu raspunde deloc cu date."""
    assert client.get(_q()).status_code in (401, 403)


def test_wrong_api_key_is_rejected(client):
    assert client.get(_q(), headers={"X-Api-Key": "gresit"}).status_code in (401, 403)


# ---------- validare ----------

def test_bad_db_name_returns_400(client, key_headers):
    r = client.get(_q(db_name="DROP; --"), headers=key_headers)
    assert r.status_code == 400
    assert r.get_json()["ok"] is False


def test_missing_db_name_returns_400(client, key_headers):
    assert client.get(f"{URL}?table={EXISTING_TABLE}",
                      headers=key_headers).status_code == 400


def test_table_outside_allowlist_returns_400(client, key_headers):
    """Un tabel real, dar din afara allow-list-ului, e tot refuzat — allow-list-ul e
    granita, nu existenta tabelului."""
    r = client.get(_q(table="Clasificatii"), headers=key_headers)
    assert r.status_code == 400
    assert r.get_json()["ok"] is False


def test_missing_table_param_returns_400(client, key_headers):
    assert client.get(f"{URL}?db_name={DB_NAME}",
                      headers=key_headers).status_code == 400


def test_error_message_has_literal_diacritics(client, key_headers):
    """ensure_ascii=False capat la capat: diacriticele reale, nu secvente \\uXXXX."""
    r = client.get(_q(table="TabelInexistentInAllowList"), headers=key_headers)
    assert r.status_code == 400
    body = r.get_data(as_text=True)
    assert "\\u" not in body
    assert "permis" in body


# ---------- comportament ----------

def test_existing_table_returns_nonempty_column_list(client, key_headers):
    r = client.get(_q(), headers=key_headers)
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["table"] == EXISTING_TABLE
    assert isinstance(body["columns"], list)
    assert len(body["columns"]) > 0
    assert all(isinstance(c, str) for c in body["columns"])
    # doar numele, nu tuplurile SHOW COLUMNS
    assert "CodAngajament" in body["columns"]


def test_column_order_is_stable_between_calls(client, key_headers):
    """Ordinea e cea din tabel (SHOW COLUMNS), deci determinista — apelantul
    construieste INSERT-uri pe pozitie."""
    first = client.get(_q(), headers=key_headers).get_json()["columns"]
    second = client.get(_q(), headers=key_headers).get_json()["columns"]
    assert first == second


def test_unknown_table_returns_200_with_empty_list(client, key_headers):
    """Tabel din allow-list dar absent din baza: 200 cu lista goala, NU 404.
    Apelantul trebuie sa distinga «zero coloane» de o eroare de retea."""
    r = client.get(_q(table=MISSING_TABLE), headers=key_headers)
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["table"] == MISSING_TABLE
    if body["columns"]:
        pytest.skip(f"{MISSING_TABLE} exista acum pe {DB_NAME}: "
                    "testul de «tabel inexistent» nu mai e aplicabil")
    assert body["columns"] == []


def test_route_is_read_only_for_post(client, key_headers):
    """Ruta e declarata GET; un POST nu trebuie sa treaca."""
    assert client.post(_q(), headers=key_headers).status_code == 405
