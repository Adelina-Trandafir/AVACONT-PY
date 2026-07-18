# Server-side unit test for GET /api/forexe/tree (slice 0008 — MainForm tree).
# Run on the Flask host, from the PYTHON folder:  python -m pytest tests/test_forexe_tree.py
#
# Preconditions (same shape as test_forexe_angajamente.py):
#   1) FX_Angajamente / FX_Indicatori / FX_DDF / FX_ORD exist on the 000_DEMO MariaDB;
#   2) config.py is present on the host (utils.database needs it);
#   3) every fixture row is cleaned up again, pass or fail.
#
# Scope: the endpoint takes NO db_name — one database = one unit, so the base comes
# from the session (g.session.db_name). The tests mint a session on 000_DEMO in the
# in-process STORE, which is the same object the guard reads via app.test_client().
import pytest

# Host-only module: `main` pulls the blueprints, which need config.py (absent on a dev
# station). Without this guard the import fails at COLLECTION and an offline run looks
# broken when the tests are merely inapplicable.
try:
    from main import app
    from routes.auth.session_store import STORE
    from utils.database import get_db_connection
except Exception as e:                              # pragma: no cover - off-host
    pytest.skip(f"host-only test (config.py / app imports unavailable): {e}",
                allow_module_level=True)

DB_NAME = "000_DEMO"
URL = "/api/forexe/tree"

# The fixtures below are pinned to one year/SS so they cannot collide with real data.
AN = 2026
SS = "02A"
Q = f"{URL}?an={AN}&ss={SS}"

# Every key the wire contract promises (mirrors GetTreeRow on the VB.NET side).
ROW_KEYS = (
    "CodAngajament", "IDDF", "Descriere", "Stare", "DataCreare", "DataDefinitivare",
    "Incarcat", "Preluat", "Salarii", "Ascuns", "Surse",
    "AreIndicatori", "AreIstoric", "AreRevizii", "AreRezervari", "AreReceptii",
    "ArePlati", "AreDDF", "ArePartener", "AreOrd",
)


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.fixture
def auth_headers():
    """Sesiune valida pe 000_DEMO; token revocat la finalul testului."""
    token, _ = STORE.create(username="pytest-op", password="unused",
                            id_unitate=0, db_name=DB_NAME,
                            ctx={"DbName": DB_NAME}, pcname="PYTEST")
    yield {"Authorization": f"Bearer {token}"}
    STORE.revoke(token)


def _cleanup(cur, cod):
    cur.execute("DELETE FROM FX_ORD WHERE IDDF IN "
                "(SELECT IDDF FROM FX_DDF WHERE CodAngajament = %s)", (cod,))
    cur.execute("DELETE FROM FX_DDF WHERE CodAngajament = %s", (cod,))
    cur.execute("DELETE FROM FX_Indicatori WHERE CodAngajament = %s", (cod,))
    cur.execute("DELETE FROM FX_Angajamente WHERE CodAngajament = %s", (cod,))


@pytest.fixture
def demo_rows():
    """Sems a small known set, yields the connection, then always removes it.

    TREE1 : DataCreare in AN, SS matches, has an indicator -> visible.
    TREEH : same but ASCUNS=1 -> hidden unless include_hidden=1.
    TREEA : same but Stare='Anulat' -> never visible.
    TREEN : DataCreare NULL -> always visible regardless of :an.
    TREEO : DataCreare in AN, ZERO indicators (orphan) -> always visible (SS escape).
    TREEX : DataCreare in AN, indicators ALL under a DIFFERENT SS -> hidden by SS.
    """
    conn = get_db_connection(DB_NAME)
    cur = conn.cursor()
    codes = ("TREE1", "TREEH", "TREEA", "TREEN", "TREEO", "TREEX")
    try:
        for cod in codes:
            _cleanup(cur, cod)
        cur.execute(
            "INSERT INTO FX_Angajamente (CodAngajament, Descriere, Stare, DC, "
            "DataCreare, ASCUNS, Preluat) VALUES "
            "(%s,%s,%s,%s,%s,%s,1)",
            ("TREE1", "Angajament vizibil", "În derulare", DB_NAME, f"{AN}-03-01", 0),
        )
        cur.execute(
            "INSERT INTO FX_Angajamente (CodAngajament, Descriere, Stare, DC, "
            "DataCreare, ASCUNS, Preluat) VALUES (%s,%s,%s,%s,%s,%s,1)",
            ("TREEH", "Angajament ascuns", "În derulare", DB_NAME, f"{AN}-03-02", 1),
        )
        cur.execute(
            "INSERT INTO FX_Angajamente (CodAngajament, Descriere, Stare, DC, "
            "DataCreare, ASCUNS, Preluat) VALUES (%s,%s,%s,%s,%s,%s,1)",
            ("TREEA", "Angajament anulat", "Anulat", DB_NAME, f"{AN}-03-03", 0),
        )
        cur.execute(
            "INSERT INTO FX_Angajamente (CodAngajament, Descriere, Stare, DC, "
            "DataCreare, ASCUNS, Preluat) VALUES (%s,%s,%s,%s,NULL,%s,1)",
            ("TREEN", "Angajament fără dată", "În derulare", DB_NAME, 0),
        )
        cur.execute(
            "INSERT INTO FX_Angajamente (CodAngajament, Descriere, Stare, DC, "
            "DataCreare, ASCUNS, Preluat) VALUES (%s,%s,%s,%s,%s,%s,1)",
            ("TREEO", "Angajament orfan", "În derulare", DB_NAME, f"{AN}-03-04", 0),
        )
        cur.execute(
            "INSERT INTO FX_Angajamente (CodAngajament, Descriere, Stare, DC, "
            "DataCreare, ASCUNS, Preluat) VALUES (%s,%s,%s,%s,%s,%s,1)",
            ("TREEX", "Angajament alt SS", "În derulare", DB_NAME, f"{AN}-03-05", 0),
        )
        # SS per angajament: TREE1..TREEN pe SS-ul testat; TREEX pe alt SS (deci filtrul
        # SS îl scoate); TREEO NU primește niciun indicator (orfan -> escape-ul SS îl
        # ține vizibil). Mapping-ul face intenția explicită și decuplată de ordine.
        indicator_ss = {
            "TREE1": SS,
            "TREEH": SS,
            "TREEA": SS,
            "TREEN": SS,
            "TREEX": "99Z",     # indicatori doar sub alt SS -> ascuns de filtrul SS
            # TREEO: intenționat fără indicatori -> orfan, rămâne vizibil (escape SS)
        }
        for i, (cod, ind_ss) in enumerate(indicator_ss.items()):
            cur.execute(
                "INSERT INTO FX_Indicatori (CodAI, CodAngajament, IdUnitate, SS) "
                "VALUES (%s,%s,%s,%s)", (f"{cod}-AI{i}", cod, 0, ind_ss),
            )
        conn.commit()
        yield conn
    finally:
        cur = conn.cursor()
        for cod in codes:
            _cleanup(cur, cod)
        conn.commit()
        conn.close()


def _codes(resp):
    return [r["CodAngajament"] for r in resp.get_json()["rows"]]


def test_missing_token_is_rejected(client):
    assert client.get(Q).status_code == 401


def test_missing_an_returns_400(client, auth_headers):
    assert client.get(f"{URL}?ss={SS}", headers=auth_headers).status_code == 400


def test_missing_ss_returns_400(client, auth_headers):
    assert client.get(f"{URL}?an={AN}", headers=auth_headers).status_code == 400


def test_bad_an_returns_400(client, auth_headers):
    assert client.get(f"{URL}?an=abc&ss={SS}", headers=auth_headers).status_code == 400


def test_returns_envelope_and_full_row_contract(client, auth_headers, demo_rows):
    r = client.get(Q, headers=auth_headers)
    assert r.status_code == 200
    body = r.get_json()
    assert body["db_name"] == DB_NAME
    assert body["count"] == len(body["rows"])
    row = next(x for x in body["rows"] if x["CodAngajament"] == "TREE1")
    for key in ROW_KEYS:
        assert key in row, f"lipsește cheia {key} din contractul de wire"


def test_diacritics_survive_as_literal_utf8(client, auth_headers, demo_rows):
    """ensure_ascii=False end to end: «În derulare» must arrive as real UTF-8."""
    r = client.get(Q, headers=auth_headers)
    row = next(x for x in r.get_json()["rows"] if x["CodAngajament"] == "TREE1")
    assert row["Stare"] == "În derulare"
    assert "\\u" not in r.get_data(as_text=True)


def test_anulat_is_always_excluded(client, auth_headers, demo_rows):
    assert "TREEA" not in _codes(client.get(Q, headers=auth_headers))
    # nici macar cu include_hidden=1 — sunt filtre independente
    assert "TREEA" not in _codes(client.get(Q + "&include_hidden=1", headers=auth_headers))


def test_include_hidden_toggles_ascuns_rows(client, auth_headers, demo_rows):
    assert "TREEH" not in _codes(client.get(Q, headers=auth_headers))
    assert "TREEH" in _codes(client.get(Q + "&include_hidden=1", headers=auth_headers))


def test_null_datacreare_is_always_returned(client, auth_headers, demo_rows):
    """Randurile fara DataCreare nu sunt inca descarcate din FOREXE: se arata
    indiferent de :an, deci si pentru un an in care nu exista nimic altceva."""
    assert "TREEN" in _codes(client.get(Q, headers=auth_headers))
    other = client.get(f"{URL}?an={AN - 50}&ss={SS}", headers=auth_headers)
    assert "TREEN" in _codes(other)
    assert "TREE1" not in _codes(other)      # acela are data in AN, deci dispare


def test_ss_filter_narrows_rows(client, auth_headers, demo_rows):
    r = client.get(f"{URL}?an={AN}&ss=ZZZ-inexistent", headers=auth_headers)
    assert r.status_code == 200
    assert "TREE1" not in _codes(r)


def test_orphan_no_indicators_is_shown(client, auth_headers, demo_rows):
    """Escape orfani: un angajament FĂRĂ niciun indicator (creat de curând, „rânduri
    doar în FX_Angajamente") rămâne vizibil — filtrul SS îngustează DOAR angajamentele
    care chiar au indicatori. Oglindește ramura UNION ALL a orfanilor din legacy."""
    assert "TREEO" in _codes(client.get(Q, headers=auth_headers))


def test_indicators_all_other_ss_is_hidden(client, auth_headers, demo_rows):
    """Contra-proba: un angajament ale cărui indicatori sunt TOȚI sub alt SS NU apare —
    escape-ul orfanilor nu slăbește filtrul SS pentru cele care AU indicatori. Pinuit ca
    nimeni să nu „repare" mai târziu escape-ul într-un OR care lasă totul să treacă."""
    assert "TREEX" not in _codes(client.get(Q, headers=auth_headers))


def test_flags_without_ddf_are_false(client, auth_headers, demo_rows):
    """TREE1 has an indicator but no DDF/ORD/receptii/plati: AreIndicatori is true,
    the DDF-derived flags are false — not null, not missing."""
    r = client.get(Q, headers=auth_headers)
    row = next(x for x in r.get_json()["rows"] if x["CodAngajament"] == "TREE1")
    assert row["AreIndicatori"] is True
    assert row["Surse"] == SS
    assert row["IDDF"] is None
    for flag in ("AreDDF", "AreOrd", "ArePartener", "AreReceptii", "ArePlati"):
        assert row[flag] is False, f"{flag} ar trebui fals fara DDF"


def test_ddf_and_ord_flags_follow_the_chain(client, auth_headers, demo_rows):
    """AreDDF comes from FX_Angajamente.IDDF; ArePartener from FX_DDF.PartAng;
    AreOrd from FX_ORD joined on the DDF's IDDF (angajament -> DDF -> ORD)."""
    conn = demo_rows
    cur = conn.cursor()
    cur.execute("SELECT COALESCE(MAX(IDDF),0)+1 FROM FX_DDF")
    iddf = int(cur.fetchone()[0])
    cur.execute(
        "INSERT INTO FX_DDF (IDDF, CodAngajament, PartAng, DC) VALUES (%s,%s,%s,%s)",
        (iddf, "TREE1", 1, DB_NAME),
    )
    cur.execute("UPDATE FX_Angajamente SET IDDF = %s WHERE CodAngajament = %s",
                (iddf, "TREE1"))
    cur.execute("SELECT COALESCE(MAX(IDORD),0)+1 FROM FX_ORD")
    idord = int(cur.fetchone()[0])
    cur.execute("INSERT INTO FX_ORD (IDORD, IDDF, DC) VALUES (%s,%s,%s)",
                (idord, iddf, DB_NAME))
    conn.commit()

    r = client.get(Q, headers=auth_headers)
    row = next(x for x in r.get_json()["rows"] if x["CodAngajament"] == "TREE1")
    assert row["IDDF"] == iddf
    assert row["AreDDF"] is True
    assert row["ArePartener"] is True
    assert row["AreOrd"] is True
