# Server-side unit test for GET /api/forexe/sumar (slice 0011 — vederea Sumar).
# Run on the Flask host, from the PYTHON folder:  python -m pytest tests/test_forexe_sumar.py
#
# Preconditions (same shape as test_forexe_tree.py):
#   1) FX_Angajamente / FX_Indicatori / FX_Istoric / FX_Rezervari / FX_Receptii /
#      FX_Plati / Clasificatii exist on the 000_DEMO MariaDB;
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
URL = "/api/forexe/sumar"

# Literalul Access, CU punct final — daca dispare punctul, INNER JOIN-ul pe FX_Istoric
# nu mai gaseste nimic si sumarul iese gol. Testat explicit prin fixture.
ISTORIC_NOU = "Angajament nou."

COD = "SUM1"          # angajament complet: 2 indicatori, agregate pe primul
COD_GOL = "SUM0"      # angajament fara indicatori -> header null, rows []

# IdClsfAcc al clasificatiei de test — valoare din afara plajei reale, ca sa nu
# poata coliziona cu date de productie si ca sa se poata sterge fara ambiguitate.
CLSF_ACC = 990001

# Cheile pe care contractul de fir le promite (oglindesc SumarRow pe partea VB.NET).
ROW_KEYS = (
    "clsf", "cod_indicator", "partener",
    "total_rezervari", "total_receptii", "total_plati",
    "total_revizii", "total_ordonantari",
)
HEADER_KEYS = (
    "cod_angajament", "data_fx", "data_creare", "data_definitivare",
    "descriere", "stare", "incarcat", "preluat",
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


def _unitati(cur, count):
    """Primele `count` IdUnitate REALE ale bazei conectate, citite din Unitati.

    Nu se hardcodeaza 0: Clasificatii are FOREIGN KEY (IdUnitate) -> Unitati
    (`Clasificatii_ibfk_6`), deci un 0 inventat pica insertul din fixture cu o eroare
    de constrangere, nu cu una de logica. Pe 000_DEMO exista 8 unitati (48, 75, 76,
    121, 123, 135, 136, 157) — dar se CITESC, nu se presupun.
    (Atentie: `Unitati` exista si in AVACONT_COMUN, cu alte coloane — aici se
    citeste cea din baza per-unitate, cea pe care e deschisa conexiunea.)
    """
    cur.execute("SELECT IdUnitate FROM Unitati ORDER BY IdUnitate LIMIT %s", (count,))
    rows = [r[0] for r in cur.fetchall()]
    if not rows:
        raise AssertionError("Unitati este gol — fixture-ul nu are IdUnitate valid.")
    return rows


def _cleanup_clsf(cur):
    """Sterge clasificatiile de test. IdClsfAcc-ul de test e din afara plajei reale,
    deci stergerea pe el nu poate atinge date de productie ale niciunei unitati."""
    cur.execute("DELETE FROM Clasificatii WHERE IdClsfAcc = %s", (CLSF_ACC,))


def _cleanup(cur, cod):
    cur.execute("DELETE FROM FX_Rezervari WHERE CodAngajament = %s", (cod,))
    cur.execute("DELETE FROM FX_Receptii WHERE CodAngajament = %s", (cod,))
    cur.execute("DELETE FROM FX_Plati WHERE CodAngajament = %s", (cod,))
    cur.execute("DELETE FROM FX_Istoric WHERE CodAngajament = %s", (cod,))
    cur.execute("DELETE FROM FX_Indicatori WHERE CodAngajament = %s", (cod,))
    cur.execute("DELETE FROM FX_Angajamente WHERE CodAngajament = %s", (cod,))


@pytest.fixture
def demo_rows():
    """Seeds a known angajament, yields the connection, then always removes it.

    SUM1 are DOI indicatori, alesi ca sa acopere exact deciziile feliei:
      IND-A : IdClsf -> o clasificatie REALA  -> Clsf populat;
              are rezervari (100+50) si receptii (DIF 7+3) -> totaluri non-zero.
      IND-B : IdClsf = 0 (nicio clasificatie) -> trebuie sa APARA cu Clsf gol;
              NU are rezervari/receptii/plati -> totalurile trebuie sa fie 0, nu null.
    SUM0 : angajament fara niciun indicator -> header null, rows [].

    Nomenclatorul e insamantat DELIBERAT „murdar” (0011-03), ca fan-out-ul sa fie
    reprodus, nu presupus:
      - aceeasi (IdClsfAcc, IdUnitate) inserata de DOUA ori  -> duplicat real;
      - acelasi IdClsfAcc sub ALTA IdUnitate                 -> vecin de alta unitate.
    Cu un JOIN, IND-A ar aparea de 2 ori (sau de 3, fara predicatul IdUnitate).
    Cu subinterogarea scalara + LIMIT 1, exact o data.
    """
    conn = get_db_connection(DB_NAME)
    cur = conn.cursor()
    codes = (COD, COD_GOL)
    unitati = _unitati(cur, 2)
    id_unitate = unitati[0]
    # A doua unitate (daca baza are una): serveste testul de izolare pe unitate.
    id_unitate_alt = unitati[1] if len(unitati) > 1 else None
    try:
        for cod in codes:
            _cleanup(cur, cod)
        _cleanup_clsf(cur)

        # Clsf/Titlu/SS sunt coloane GENERATED, deci NU se scriu.
        # Valorile componente nu sunt arbitrare: Clasificatii are FK-uri catre
        # AVACONT_COMUN.Defa* pe coloanele GENERATE (Titlu, Articol, ClsfE, ClsfF, SS),
        # deci combinatia trebuie sa existe in nomenclatoare. S-a ales una reala,
        # luata din FX_System_Export/TABLES/FX_Receptii.md:54
        # (Clsf 65.02.04.02.20.01.03, CodSSI 02A650402200103).
        #
        # Se insereaza de DOUA ORI pentru aceeasi unitate: nomenclatorul chiar are
        # duplicate in productie ((75,79), (75,84), (75,90), (75,92), (75,93) — fiecare
        # de doua ori), iar un JOIN ar multiplica randul-indicator. Fixtura reproduce
        # defectul, ca testul sa demonstreze ca subinterogarea scalara il rezolva.
        for _ in range(2):
            cur.execute(
                "INSERT INTO Clasificatii (IdClsfAcc, IdUnitate, Capitol, Subcapitol, "
                "Articol, Alineat, Denumire) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (CLSF_ACC, id_unitate, "65.02", "04.02", "20.01", "03", "Clasificație test"),
            )
        # ACELASI IdClsfAcc sub ALTA unitate: nu are voie sa produca un rand in plus.
        if id_unitate_alt is not None:
            cur.execute(
                "INSERT INTO Clasificatii (IdClsfAcc, IdUnitate, Capitol, Subcapitol, "
                "Articol, Alineat, Denumire) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (CLSF_ACC, id_unitate_alt, "65.02", "04.02", "20.01", "03",
                 "Clasificație altă unitate"),
            )

        for cod, descriere in ((COD, "Angajament sumar"), (COD_GOL, "Angajament gol")):
            cur.execute(
                "INSERT INTO FX_Angajamente (CodAngajament, Descriere, Stare, DC, "
                "DataCreare, ASCUNS, Incarcat, Preluat) VALUES (%s,%s,%s,%s,%s,0,1,1)",
                (cod, descriere, "În derulare", DB_NAME, "2026-03-01"),
            )
            # Randul de istoric pe care se face INNER JOIN — exact unul per angajament.
            cur.execute(
                "INSERT INTO FX_Istoric (ID, CodAngajament, DataFX, Descriere) "
                "VALUES (%s,%s,%s,%s)",
                (hash(cod) % 100000, cod, "2026-03-05", ISTORIC_NOU),
            )

        cur.execute(
            "INSERT INTO FX_Indicatori (CodAI, CodAngajament, CodIndicator, IdClsf, "
            "IdUnitate, SS) VALUES (%s,%s,%s,%s,%s,%s)",
            # ATENTIE: FX_Indicatori.IdClsf tine ID-UL ACCESS, deci se umple cu
            # IdClsfAcc, NU cu PK-ul MariaDB (lastrowid). Numele coloanei minte —
            # asta a fost defectul 0011-03.
            (f"{COD}-AI-A", COD, "IND-A", CLSF_ACC, id_unitate, "02A"),
        )
        cur.execute(
            "INSERT INTO FX_Indicatori (CodAI, CodAngajament, CodIndicator, IdClsf, "
            "IdUnitate, SS) VALUES (%s,%s,%s,%s,%s,%s)",
            (f"{COD}-AI-B", COD, "IND-B", 0, id_unitate, "02A"),
        )

        # Agregate DOAR pe IND-A. Doua randuri fiecare, ca testul sa dovedeasca
        # insumarea, nu doar prezenta unei valori.
        for idrz, val in ((910001, 100.0), (910002, 50.0)):
            cur.execute(
                "INSERT INTO FX_Rezervari (IDRZ, CodAngajament, CodIndicator, "
                "R_Valoare) VALUES (%s,%s,%s,%s)", (idrz, COD, "IND-A", val),
            )
        # TotalReceptii = SUM(DIF), NU SUM(Valoare) — Valoare e pusa deliberat diferit
        # ca testul sa pice daca cineva „repara" query-ul catre Valoare.
        for idr, valoare, dif in ((920001, 1000.0, 7.0), (920002, 2000.0, 3.0)):
            cur.execute(
                "INSERT INTO FX_Receptii (IDR, CodAngajament, CodIndicator, "
                "Valoare, DIF) VALUES (%s,%s,%s,%s,%s)",
                (idr, COD, "IND-A", valoare, dif),
            )
        cur.execute(
            "INSERT INTO FX_Plati (IdPlataFX, CodAngajament, CodIndicator, Suma) "
            "VALUES (%s,%s,%s,%s)", (930001, COD, "IND-A", 25.0),
        )
        conn.commit()
        yield conn
    finally:
        cur = conn.cursor()
        for cod in codes:
            _cleanup(cur, cod)
        # FX_Indicatori se sterge inaintea clasificatiei (deja facut de _cleanup),
        # altfel un FK ar bloca stergerea randurilor din Clasificatii.
        _cleanup_clsf(cur)
        conn.commit()
        conn.close()


def _rows_by_indicator(resp):
    return {r["cod_indicator"]: r for r in resp.get_json()["rows"]}


# ---------------------------------------------------------------------------
# Guard + validare parametri
# ---------------------------------------------------------------------------

def test_missing_token_is_rejected(client):
    """Fara sesiune -> 401 cu motiv, nu 200 cu sumar gol."""
    resp = client.get(f"{URL}?cod={COD}")
    assert resp.status_code == 401
    assert resp.get_json().get("reason")


def test_missing_cod_returns_400(client, auth_headers):
    resp = client.get(URL, headers=auth_headers)
    assert resp.status_code == 400
    assert "cod" in resp.get_json()["error"]


def test_blank_cod_returns_400(client, auth_headers):
    assert client.get(f"{URL}?cod=%20%20", headers=auth_headers).status_code == 400


def test_error_message_keeps_literal_diacritics(client, auth_headers):
    """ensure_ascii=False: operatorul vede «lipsă», nu «lips\\u0103»."""
    resp = client.get(URL, headers=auth_headers)
    assert "lipsă" in resp.get_data(as_text=True)


# ---------------------------------------------------------------------------
# Contract de raspuns
# ---------------------------------------------------------------------------

def test_unknown_cod_returns_empty_not_404(client, auth_headers):
    """Un angajament fara indicatori e legitim -> 200, header null, rows []."""
    resp = client.get(f"{URL}?cod=NUEXISTA-XYZ", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["header"] is None
    assert body["rows"] == []


def test_angajament_without_indicators_is_empty(client, auth_headers, demo_rows):
    body = client.get(f"{URL}?cod={COD_GOL}", headers=auth_headers).get_json()
    assert body["header"] is None
    assert body["rows"] == []


def test_header_is_hoisted_once(client, auth_headers, demo_rows):
    """Antetul apare O SINGURA data, desi Access il repeta pe fiecare rand."""
    body = client.get(f"{URL}?cod={COD}", headers=auth_headers).get_json()
    header = body["header"]
    assert set(header.keys()) == set(HEADER_KEYS)
    assert header["cod_angajament"] == COD
    assert header["descriere"] == "Angajament sumar"
    assert header["stare"] == "În derulare"
    assert header["incarcat"] is True and header["preluat"] is True
    # Date ISO 'YYYY-MM-DD', fara ora, sau None.
    assert header["data_fx"] == "2026-03-05"
    assert header["data_creare"] == "2026-03-01"
    assert header["data_definitivare"] is None


def test_row_shape_matches_contract(client, auth_headers, demo_rows):
    rows = client.get(f"{URL}?cod={COD}", headers=auth_headers).get_json()["rows"]
    assert rows, "sumarul trebuie sa aiba randuri"
    for r in rows:
        assert set(r.keys()) == set(ROW_KEYS)


def test_one_row_per_indicator(client, auth_headers, demo_rows):
    """Granulatia e per indicator; INNER JOIN pe FX_Istoric nu trebuie sa dubleze."""
    by_ind = _rows_by_indicator(client.get(f"{URL}?cod={COD}", headers=auth_headers))
    assert set(by_ind) == {"IND-A", "IND-B"}


# ---------------------------------------------------------------------------
# Deciziile feliei
# ---------------------------------------------------------------------------

def test_indicator_without_classification_still_appears(client, auth_headers, demo_rows):
    """Decizia 4 (LEFT JOIN Clasificatii) — motivul principal al feliei.

    Cu INNER JOIN-ul din Access, IND-B ar disparea complet din sumar.
    """
    by_ind = _rows_by_indicator(client.get(f"{URL}?cod={COD}", headers=auth_headers))
    assert "IND-B" in by_ind
    assert not by_ind["IND-B"]["clsf"]      # None sau '' — dar randul EXISTA


def test_classification_is_the_generated_dotted_code(client, auth_headers, demo_rows):
    """Ramura A: Clsf vine direct din coloana GENERATED (Capitol.Subcapitol.Articol.Alineat)."""
    by_ind = _rows_by_indicator(client.get(f"{URL}?cod={COD}", headers=auth_headers))
    assert by_ind["IND-A"]["clsf"] == "65.02.04.02.20.01.03"


def test_missing_aggregates_are_zero_not_null(client, auth_headers, demo_rows):
    """COALESCE(...,0): grila arata «0,00», nu gol. IND-B nu are niciun agregat."""
    b = _rows_by_indicator(client.get(f"{URL}?cod={COD}", headers=auth_headers))["IND-B"]
    for key in ("total_rezervari", "total_receptii", "total_plati",
                "total_revizii", "total_ordonantari"):
        assert b[key] == 0, f"{key} trebuie 0, nu {b[key]!r}"


def test_aggregates_are_summed(client, auth_headers, demo_rows):
    a = _rows_by_indicator(client.get(f"{URL}?cod={COD}", headers=auth_headers))["IND-A"]
    assert a["total_rezervari"] == 150.0     # 100 + 50
    assert a["total_plati"] == 25.0


def test_receptii_sums_dif_not_valoare(client, auth_headers, demo_rows):
    """Decizia 5: TotalReceptii = SUM(DIF) = 7+3 = 10, NU SUM(Valoare) = 3000."""
    a = _rows_by_indicator(client.get(f"{URL}?cod={COD}", headers=auth_headers))["IND-A"]
    assert a["total_receptii"] == 10.0


def test_duplicate_classification_does_not_fan_out(client, auth_headers, demo_rows):
    """0011-03: nomenclatorul are duplicate reale pe (IdClsfAcc, IdUnitate).

    Fixtura insereaza clasificatia de DOUA ori. Cu `LEFT JOIN Clasificatii`, IND-A
    ar aparea de doua ori si sumarul ar minti. Subinterogarea scalara cu LIMIT 1
    garanteaza un rand per indicator — SI Clsf tot populat.
    """
    rows = client.get(f"{URL}?cod={COD}", headers=auth_headers).get_json()["rows"]
    ind_a = [r for r in rows if r["cod_indicator"] == "IND-A"]
    assert len(ind_a) == 1, f"IND-A a aparut de {len(ind_a)} ori (fan-out)"
    assert ind_a[0]["clsf"] == "65.02.04.02.20.01.03"


def test_other_unit_classification_does_not_add_a_row(client, auth_headers, demo_rows):
    """0011-03: baza per-unitate tine nomenclatorul pentru MAI MULTE unitati.

    Acelasi IdClsfAcc exista si sub alta IdUnitate. Fara predicatul
    `C.IdUnitate = I.IdUnitate` ar aparea un rand in plus (fan-out multi-unitate).
    Regula «drop IdUnitate» se aplica DOAR tabelelor FX_, nu nomenclatoarelor.
    """
    rows = client.get(f"{URL}?cod={COD}", headers=auth_headers).get_json()["rows"]
    assert len(rows) == 2, f"asteptam exact 2 randuri (IND-A, IND-B), am primit {len(rows)}"
    assert sorted(r["cod_indicator"] for r in rows) == ["IND-A", "IND-B"]


def test_row_count_equals_indicator_count(client, auth_headers, demo_rows):
    """Plasa generala impotriva fan-out-ului: sumarul nu poate avea mai multe randuri
    decat are angajamentul indicatori. Prinde orice join viitor care multiplica."""
    conn = demo_rows
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM FX_Indicatori WHERE CodAngajament = %s", (COD,))
    expected = cur.fetchone()[0]

    rows = client.get(f"{URL}?cod={COD}", headers=auth_headers).get_json()["rows"]
    assert len(rows) == expected


def test_rows_are_deterministically_ordered(client, auth_headers, demo_rows):
    """ORDER BY adaugat fata de Access, ca grila sa fie stabila intre refresh-uri."""
    first = client.get(f"{URL}?cod={COD}", headers=auth_headers).get_json()["rows"]
    second = client.get(f"{URL}?cod={COD}", headers=auth_headers).get_json()["rows"]
    assert [r["cod_indicator"] for r in first] == [r["cod_indicator"] for r in second]


def test_debug_dump(client, auth_headers):
    import json
    resp = client.get("/api/forexe/sumar?cod=AAB2EF2MCP4", headers=auth_headers)
    data = resp.get_json()
    print("\nSTATUS:", resp.status_code)
    print("HEADER:", json.dumps(data.get("header"), ensure_ascii=False, indent=2))
    print("NR RANDURI:", len(data.get("rows") or []))
    print(json.dumps(data.get("rows"), ensure_ascii=False, indent=2)[:1500])