# Server-side unit test for GET /api/forexe/rezervari (slice 0014 — vederea Rezervari).
# Run on the Flask host, from the PYTHON folder:  python -m pytest tests/test_forexe_rezervari.py
#
# Preconditions (same shape as test_forexe_sumar.py):
#   1) FX_Angajamente / FX_Indicatori / FX_Rezervari / Clasificatii / Unitati exist on
#      the 000_DEMO MariaDB;
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
URL = "/api/forexe/rezervari"

COD = "REZ1"          # angajament cu rezervari pe doua luni, ambele tipuri, +/- AreDDF
COD_GOL = "REZ0"      # angajament fara nicio rezervare -> rows []

# IdClsfAcc al clasificatiei de test — valoare din afara plajei reale, ca sa nu poata
# coliziona cu date de productie si ca sa se poata sterge fara ambiguitate.
CLSF_ACC = 990014

# Cheile pe care contractul de fir le promite (oglindesc RezervareRow pe partea VB.NET).
ROW_KEYS = (
    "idrz", "cod_indicator", "clsf", "denumire", "data_rezervare",
    "r_credit_bug", "r_initiala", "r_valoare", "r_definitiva",
    "e_initiala", "e_marire", "e_micsorare", "are_ddf",
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

    Nu se hardcodeaza 0: Clasificatii are FOREIGN KEY (IdUnitate) -> Unitati, deci un 0
    inventat pica insertul din fixture cu o eroare de constrangere, nu cu una de logica.
    """
    cur.execute("SELECT IdUnitate FROM Unitati ORDER BY IdUnitate LIMIT %s", (count,))
    rows = [r[0] for r in cur.fetchall()]
    if not rows:
        raise AssertionError("Unitati este gol — fixture-ul nu are IdUnitate valid.")
    return rows


def _cleanup_clsf(cur):
    cur.execute("DELETE FROM Clasificatii WHERE IdClsfAcc = %s", (CLSF_ACC,))


def _cleanup(cur, cod):
    cur.execute("DELETE FROM FX_Rezervari WHERE CodAngajament = %s", (cod,))
    cur.execute("DELETE FROM FX_Indicatori WHERE CodAngajament = %s", (cod,))
    cur.execute("DELETE FROM FX_Angajamente WHERE CodAngajament = %s", (cod,))


@pytest.fixture
def demo_rows():
    """Seeds a known angajament with rezervari, yields the connection, then removes it.

    REZ1 are UN indicator (IND-A) cu clasificatie reala si CINCI rezervari, alese ca sa
    acopere exact deciziile feliei:
      - doua luni distincte (Ianuarie + Februarie 2026) -> doua foldere;
      - tipuri mixte (Initiala / Marire / Micsorare) -> mapare de icon + ordine;
      - un rand cu AreDDF=1 si altele cu AreDDF=0 -> flag-ul „+" pe grup;
      - un rand cu R_Valoare negativa -> nod rosu in client (aici doar verificam semnul).
    REZ0 : angajament fara nicio rezervare -> rows [].

    Nomenclatorul e insamantat DELIBERAT „murdar" (ca la Sumar 0011-03), ca fan-out-ul
    sa fie reprodus, nu presupus: aceeasi (IdClsfAcc, IdUnitate) de DOUA ori, plus acelasi
    IdClsfAcc sub ALTA unitate. Cu un JOIN, fiecare rezervare a lui IND-A ar aparea de
    2-3 ori; cu subinterogarea scalara + LIMIT 1, exact o data.
    """
    conn = get_db_connection(DB_NAME)
    cur = conn.cursor()
    codes = (COD, COD_GOL)
    unitati = _unitati(cur, 2)
    id_unitate = unitati[0]
    id_unitate_alt = unitati[1] if len(unitati) > 1 else None
    try:
        for cod in codes:
            _cleanup(cur, cod)
        _cleanup_clsf(cur)

        # Clsf/Titlu/SS sunt coloane GENERATED, deci NU se scriu. Componentele trebuie
        # sa existe in nomenclatoarele AVACONT_COMUN.Defa* (FK-uri pe coloanele generate);
        # se folosesc aceleasi valori reale ca la Sumar (65.02.04.02.20.01.03).
        for _ in range(2):   # duplicat real pe (IdClsfAcc, IdUnitate)
            cur.execute(
                "INSERT INTO Clasificatii (IdClsfAcc, IdUnitate, Capitol, Subcapitol, "
                "Articol, Alineat, Denumire) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (CLSF_ACC, id_unitate, "65.02", "04.02", "20.01", "03", "Clasificație test"),
            )
        if id_unitate_alt is not None:   # vecin de alta unitate
            cur.execute(
                "INSERT INTO Clasificatii (IdClsfAcc, IdUnitate, Capitol, Subcapitol, "
                "Articol, Alineat, Denumire) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (CLSF_ACC, id_unitate_alt, "65.02", "04.02", "20.01", "03",
                 "Clasificație altă unitate"),
            )

        for cod, descriere in ((COD, "Angajament rezervari"), (COD_GOL, "Angajament gol")):
            cur.execute(
                "INSERT INTO FX_Angajamente (CodAngajament, Descriere, Stare, DC, "
                "DataCreare, ASCUNS, Incarcat, Preluat) VALUES (%s,%s,%s,%s,%s,0,1,1)",
                (cod, descriere, "În derulare", DB_NAME, "2026-03-01"),
            )

        cod_ai = f"{COD}-AI-A"
        cur.execute(
            "INSERT INTO FX_Indicatori (CodAI, CodAngajament, CodIndicator, IdClsf, "
            "IdUnitate, SS) VALUES (%s,%s,%s,%s,%s,%s)",
            # FX_Indicatori.IdClsf tine ID-UL ACCESS -> se umple cu IdClsfAcc, nu cu PK.
            (cod_ai, COD, "IND-A", CLSF_ACC, id_unitate, "02A"),
        )

        # (IDRZ, DataRezervare, R_CreditBug, R_Initiala, R_Valoare, R_Definitiva,
        #  EInitiala, EMarire, EMicsorare, AreDDF)
        rezervari = [
            (960001, "2026-01-17", 112100, 3065.12, 3065.12, 0, 1, 0, 0, 0),   # Ian, Initiala, fara DDF
            (960002, "2026-01-29", 112100, 3065.12, 6704.55, 0, 0, 1, 0, 0),   # Ian, Marire, fara DDF
            (960003, "2026-02-07", 112100, 6704.55, -23.00, 0, 0, 0, 1, 0),    # Feb, Micsorare, R_Valoare<0
            (960004, "2026-02-15", 112100, 6680.55, 100.00, 0, 0, 1, 0, 1),    # Feb, Marire, ARE DDF
            (960005, "2026-02-22", 112100, 6632.55, 50.00, 0, 0, 1, 0, 0),     # Feb, Marire, fara DDF
        ]
        for (idrz, data, cbug, rin, rval, rdef, ei, em, emi, addf) in rezervari:
            cur.execute(
                "INSERT INTO FX_Rezervari (IDRZ, CodAI, CodAngajament, CodIndicator, "
                "IdClsf, DataRezervare, R_CreditBug, R_Initiala, R_Valoare, R_Definitiva, "
                "EInitiala, EMarire, EMicsorare, AreDDF) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (idrz, cod_ai, COD, "IND-A", CLSF_ACC, data, cbug, rin, rval, rdef,
                 ei, em, emi, addf),
            )
        conn.commit()
        yield conn
    finally:
        cur = conn.cursor()
        for cod in codes:
            _cleanup(cur, cod)
        _cleanup_clsf(cur)
        conn.commit()
        conn.close()


def _rows(resp):
    return resp.get_json()["rows"]


def _by_idrz(resp):
    return {r["idrz"]: r for r in _rows(resp)}


# ---------------------------------------------------------------------------
# Guard + validare parametri
# ---------------------------------------------------------------------------

def test_missing_token_is_rejected(client):
    """Fara sesiune -> 401 cu motiv, nu 200 cu lista goala."""
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
    """Un angajament fara rezervari e legitim -> 200, rows []."""
    resp = client.get(f"{URL}?cod=NUEXISTA-XYZ", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.get_json()["rows"] == []


def test_angajament_without_rezervari_is_empty(client, auth_headers, demo_rows):
    assert _rows(client.get(f"{URL}?cod={COD_GOL}", headers=auth_headers)) == []


def test_row_shape_matches_contract(client, auth_headers, demo_rows):
    rows = _rows(client.get(f"{URL}?cod={COD}", headers=auth_headers))
    assert rows, "trebuie sa existe rezervari"
    for r in rows:
        assert set(r.keys()) == set(ROW_KEYS)


def test_all_rezervari_are_returned(client, auth_headers, demo_rows):
    """Cinci rezervari insamantate -> cinci randuri (fara filtru pe R_Valoare)."""
    rows = _rows(client.get(f"{URL}?cod={COD}", headers=auth_headers))
    assert len(rows) == 5


# ---------------------------------------------------------------------------
# Deciziile feliei
# ---------------------------------------------------------------------------

def test_boolean_flags_are_bools_not_ints(client, auth_headers, demo_rows):
    """EInitiala/EMarire/EMicsorare/AreDDF ies ca True/False, nu 0/1 (TINYINT)."""
    r = _by_idrz(client.get(f"{URL}?cod={COD}", headers=auth_headers))[960001]
    for key in ("e_initiala", "e_marire", "e_micsorare", "are_ddf"):
        assert isinstance(r[key], bool), f"{key} = {r[key]!r} nu e bool"
    assert r["e_initiala"] is True and r["e_marire"] is False


def test_are_ddf_flag_distinguishes_rows(client, auth_headers, demo_rows):
    """Randul cu DDF si cele fara — clientul deriva flag-ul „+" din AreDDF."""
    by = _by_idrz(client.get(f"{URL}?cod={COD}", headers=auth_headers))
    assert by[960004]["are_ddf"] is True
    assert by[960005]["are_ddf"] is False


def test_negative_r_valoare_is_preserved(client, auth_headers, demo_rows):
    """R_Valoare negativa ajunge la client cu semn (nodul devine rosu acolo)."""
    by = _by_idrz(client.get(f"{URL}?cod={COD}", headers=auth_headers))
    assert by[960003]["r_valoare"] == -23.0


def test_classification_is_the_generated_dotted_code(client, auth_headers, demo_rows):
    """Clsf vine din coloana GENERATED (Capitol.Subcapitol.Articol.Alineat), prin
    FX_Indicatori (nu FX_Rezervari.IdClsf) — drumul verificat in 0011-03."""
    r = _by_idrz(client.get(f"{URL}?cod={COD}", headers=auth_headers))[960001]
    assert r["clsf"] == "65.02.04.02.20.01.03"
    assert r["denumire"] == "Clasificație test"


def test_duplicate_classification_does_not_fan_out(client, auth_headers, demo_rows):
    """Nomenclatorul are duplicate reale pe (IdClsfAcc, IdUnitate) + un vecin de alta
    unitate. Cu un JOIN, fiecare rezervare ar aparea de 2-3 ori; subinterogarea scalara
    cu LIMIT 1 + predicatul IdUnitate garanteaza exact cinci randuri, cu Clsf populat."""
    rows = _rows(client.get(f"{URL}?cod={COD}", headers=auth_headers))
    assert len(rows) == 5, f"fan-out: {len(rows)} randuri in loc de 5"
    assert all(r["clsf"] == "65.02.04.02.20.01.03" for r in rows)


def test_rows_are_deterministically_ordered(client, auth_headers, demo_rows):
    """ORDER BY (data, Clsf, IDRZ): stabil intre refresh-uri si crescator pe data."""
    first = _rows(client.get(f"{URL}?cod={COD}", headers=auth_headers))
    second = _rows(client.get(f"{URL}?cod={COD}", headers=auth_headers))
    assert [r["idrz"] for r in first] == [r["idrz"] for r in second]
    dates = [r["data_rezervare"] for r in first]
    assert dates == sorted(dates)


def test_data_rezervare_is_iso_date_only(client, auth_headers, demo_rows):
    """DataRezervare -> 'YYYY-MM-DD', fara ora (clientul grupeaza pe an/luna/zi)."""
    r = _by_idrz(client.get(f"{URL}?cod={COD}", headers=auth_headers))[960001]
    assert r["data_rezervare"] == "2026-01-17"
