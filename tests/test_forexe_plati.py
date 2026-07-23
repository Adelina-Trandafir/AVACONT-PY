# Server-side unit test for GET /api/forexe/plati (slice 0017-02 — vederea Plăți).
# Run on the Flask host, from the PYTHON folder:  python -m pytest tests/test_forexe_plati.py
#
# Preconditions (same shape as test_forexe_receptii.py):
#   1) FX_Angajamente / FX_Indicatori / FX_Plati / FX_Extrase / FX_ORD_TBL_REC /
#      Clasificatii / Unitati exist on the 000_DEMO MariaDB;
#   2) config.py is present on the host (utils.database needs it);
#   3) every fixture row is cleaned up again, pass or fail.
#
# Scope: the endpoint takes NO db_name — one database = one unit, so the base comes from the
# session (g.session.db_name). The tests mint a session on 000_DEMO in the in-process STORE,
# which is the same object the guard reads via app.test_client().
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
URL = "/api/forexe/plati"

COD = "PLT1"          # angajament cu 3 plati (2 ordonantate, 1 nu; una cu extras bancar)
COD_GOL = "PLT0"      # angajament fara nicio plata -> plati []

# IdClsfAcc al clasificatiei de test — valoare din afara plajei reale, ca sa nu poata
# coliziona cu date de productie si ca sa se poata sterge fara ambiguitate.
CLSF_ACC = 990017

# Cheile pe care contractul de fir le promite (oglindesc PlataRow pe partea VB.NET).
ROW_KEYS = (
    "id_plata_fx", "id_clsf", "cod_ai", "cod_indicator", "nr_op",
    "data_plata", "suma", "tip", "incarcat", "preluat", "referinta_trezor",
    "clsf", "denumire", "clsf_plata", "are_ord",
    "idfxe", "data_banca", "data_doc", "nr_doc_extras", "referinta",
    "platitor_nume", "platitor_cui", "platitor_iban",
    "suma_debit", "suma_credit", "explicatii",
)

# Referinte trezor ale celor 3 plati (a treia are un extras bancar in FX_Extrase).
REF1 = "TZ9900170001"
REF2 = "TZ9900170002"
REF3 = "TZ9900170003"


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
    # Ordonantarile trimit spre plati; extrasele spre referinte; le stergem intai.
    cur.execute(
        "DELETE FROM FX_ORD_TBL_REC WHERE IdPlataFX IN "
        "(SELECT IdPlataFX FROM FX_Plati WHERE CodAngajament = %s)", (cod,))
    cur.execute("DELETE FROM FX_Extrase WHERE Referinta IN (%s,%s,%s)", (REF1, REF2, REF3))
    cur.execute("DELETE FROM FX_Plati WHERE CodAngajament = %s", (cod,))
    cur.execute("DELETE FROM FX_Indicatori WHERE CodAngajament = %s", (cod,))
    cur.execute("DELETE FROM FX_Angajamente WHERE CodAngajament = %s", (cod,))


@pytest.fixture
def demo_rows():
    """Seeds a known angajament with plati, yields the connection, then removes it.

    PLT1 are TREI plati, alese ca sa acopere exact deciziile feliei:
      - plata 1 (IdPlataFX 9900171): PLATA, Incarcat=1, ordonantata (are_ord True);
      - plata 2 (IdPlataFX 9900172): INCASARE, Preluat=1, ordonantata (are_ord True);
      - plata 3 (IdPlataFX 9900173): PLATA, ne-ordonantata (are_ord False) -> «+»; cea mai
        veche zi ne-ordonantata; are un extras bancar (FX_Extrase pe REF3).
      - un singur indicator (IND-A) cu clasificatie reala -> clsf populat pe fiecare rand.
    PLT0 : angajament fara nicio plata -> plati [].

    Nomenclatorul e insamantat DELIBERAT „murdar" (ca la Sumar 0011-03 / Recepții 0015),
    ca fan-out-ul sa fie reprodus, nu presupus: aceeasi (IdClsfAcc, IdUnitate) de DOUA ori,
    plus acelasi IdClsfAcc sub ALTA unitate. Cu un JOIN, fiecare linie ar aparea de 2-3
    ori; cu subinterogarea scalara + LIMIT 1, exact o data.
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

        # Clsf/Titlu/SS sunt coloane GENERATED, deci NU se scriu. Componentele trebuie sa
        # existe in nomenclatoarele AVACONT_COMUN.Defa* (FK-uri pe coloanele generate); se
        # folosesc aceleasi valori reale ca la Sumar/Rezervari/Recepții.
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

        for cod, descriere in ((COD, "Angajament plati"), (COD_GOL, "Angajament gol")):
            cur.execute(
                "INSERT INTO FX_Angajamente (CodAngajament, Descriere, Stare, DC, "
                "DataCreare, ASCUNS, Incarcat, Preluat) VALUES (%s,%s,%s,%s,%s,0,1,1)",
                (cod, descriere, "În derulare", DB_NAME, "2026-03-01"),
            )

        cod_ai = f"{COD}-AI-A"
        cur.execute(
            "INSERT INTO FX_Indicatori (CodAI, CodAngajament, CodIndicator, IdClsf, "
            "IdUnitate, NrCrt, SS) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            # FX_Indicatori.IdClsf tine ID-UL ACCESS -> se umple cu IdClsfAcc, nu cu PK.
            (cod_ai, COD, "IND-A", CLSF_ACC, id_unitate, 1, "02A"),
        )

        # (IdPlataFX, NrOP, Data_plata, Suma, Tip, Incarcat, Preluat, Referinta_TREZOR, Clsf)
        plati = [
            (9900171, "39", "2026-01-31 08:01:01", 1331.0, "PLATA", 1, 0, REF1,
             "65.02.04.02.20.01.03"),
            (9900172, "85", "2026-02-04 18:31:13", -23.0, "INCASARE", 0, 1, REF2,
             "65.02.04.02.20.01.03"),
            (9900173, "137", "2026-01-19 19:21:32", 3065.12, "PLATA", 0, 0, REF3,
             "65.02.04.02.20.01.03"),
        ]
        for (idp, nrop, data_plata, suma, tip, incarcat, preluat, ref, clsf) in plati:
            cur.execute(
                "INSERT INTO FX_Plati (IdPlataFX, CodAngajament, CodAI, CodIndicator, NrOP, "
                "Data_plata, Suma, Tip, Incarcat, Preluat, Referinta_TREZOR, Clsf, IdUnitate) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (idp, COD, cod_ai, "IND-A", nrop, data_plata, suma, tip, incarcat, preluat,
                 ref, clsf, id_unitate),
            )

        # Plata 1 si 2 sunt ordonantate; plata 3 NU (cea mai veche zi ne-ordonantata -> «+»).
        # FX_ORD_TBL_REC PK = IDORDRECP (AUTO_INCREMENT), deci nu se scrie; IdPlataFX leaga.
        for idp in (9900171, 9900172):
            cur.execute(
                "INSERT INTO FX_ORD_TBL_REC (IdPlataFX, Valoare) VALUES (%s,%s)", (idp, 1.0))

        # Un singur extras bancar, pe REF3 (plata 3). FX_Extrase PK = IDFXE (nu AUTO) -> se da.
        cur.execute(
            "INSERT INTO FX_Extrase (IDFXE, DataBanca, DataDoc, NrDoc, Referinta, "
            "platitor_nume, platitor_cui, platitor_iban, suma_debit, suma_credit, "
            "Explicatii, IdUnitate) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (9900173, "2026-01-19", "19.01.2026", "0100088028", REF3,
             "FURNIZOR TEST SRL", "23308833", "RO21BRDE450SV39876344500",
             3065.12, 0.0, "Explicație extras", id_unitate),
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


def _plati(resp):
    return resp.get_json()["plati"]


def _by_id(resp):
    return {r["id_plata_fx"]: r for r in _plati(resp)}


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
    """Un angajament fara plati e legitim -> 200, plati []."""
    resp = client.get(f"{URL}?cod=NUEXISTA-XYZ", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["plati"] == []
    assert body["cod"] == "NUEXISTA-XYZ"


def test_angajament_without_plati_is_empty(client, auth_headers, demo_rows):
    assert _plati(client.get(f"{URL}?cod={COD_GOL}", headers=auth_headers)) == []


def test_row_shape_matches_contract(client, auth_headers, demo_rows):
    rows = _plati(client.get(f"{URL}?cod={COD}", headers=auth_headers))
    assert rows, "trebuie sa existe plati"
    for r in rows:
        assert set(r.keys()) == set(ROW_KEYS)


# ---------------------------------------------------------------------------
# Deciziile feliei
# ---------------------------------------------------------------------------

def test_row_count_equals_fx_plati_count(client, auth_headers, demo_rows):
    """Garda anti-fan-out: LEFT JOIN FX_Extrase NU are voie sa dubleze plata (operatorul
    afirma 1:1 pe Referinta). Numarul de randuri intoarse == COUNT(*) din FX_Plati."""
    conn = demo_rows
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM FX_Plati WHERE CodAngajament = %s", (COD,))
    expected = cur.fetchone()[0]
    rows = _plati(client.get(f"{URL}?cod={COD}", headers=auth_headers))
    assert len(rows) == expected == 3


def test_are_ord_true_and_false_cases(client, auth_headers, demo_rows):
    """are_ord: True pentru platile din FX_ORD_TBL_REC, False pentru cea ne-ordonantata."""
    by = _by_id(client.get(f"{URL}?cod={COD}", headers=auth_headers))
    assert by[9900171]["are_ord"] is True
    assert by[9900172]["are_ord"] is True
    assert by[9900173]["are_ord"] is False


def test_are_ord_not_duplicated_by_multiple_ord_lines(client, auth_headers, demo_rows):
    """O plata pe DOUA linii de ordonantare NU trebuie sa dubleze randul-plata (derivat
    DISTINCT). Adaugam o a doua linie pentru plata 1 si cerem din nou."""
    conn = demo_rows
    cur = conn.cursor()
    cur.execute("INSERT INTO FX_ORD_TBL_REC (IdPlataFX, Valoare) VALUES (%s,%s)", (9900171, 2.0))
    conn.commit()
    rows = _plati(client.get(f"{URL}?cod={COD}", headers=auth_headers))
    assert len(rows) == 3
    assert sum(1 for r in rows if r["id_plata_fx"] == 9900171) == 1


def test_classification_is_the_generated_dotted_code(client, auth_headers, demo_rows):
    """clsf vine din coloana GENERATED (Capitol.Subcapitol.Articol.Alineat), prin
    FX_Indicatori (nu FX_Plati.IdClsf) — drumul verificat in 0011-03. clsf_plata = coloana
    bruta FX_Plati.Clsf."""
    r = _by_id(client.get(f"{URL}?cod={COD}", headers=auth_headers))[9900171]
    assert r["clsf"] == "65.02.04.02.20.01.03"
    assert r["denumire"] == "Clasificație test"
    assert r["clsf_plata"] == "65.02.04.02.20.01.03"
    assert r["cod_indicator"] == "IND-A"


def test_duplicate_classification_does_not_fan_out(client, auth_headers, demo_rows):
    """Nomenclatorul are duplicate reale pe (IdClsfAcc, IdUnitate) + un vecin de alta
    unitate. Cu un JOIN, fiecare plata ar aparea de 2-3 ori; subinterogarea scalara cu
    LIMIT 1 + predicatul IdUnitate garanteaza exact trei randuri, cu clsf populat."""
    rows = _plati(client.get(f"{URL}?cod={COD}", headers=auth_headers))
    assert len(rows) == 3, f"fan-out: {len(rows)} randuri in loc de 3"
    assert all(r["clsf"] == "65.02.04.02.20.01.03" for r in rows)


def test_bank_statement_present_and_absent(client, auth_headers, demo_rows):
    """FX_Extrase LEFT JOIN: plata 3 are extras (idfxe populat), plata 1 nu (idfxe None,
    dar plata TOT apare)."""
    by = _by_id(client.get(f"{URL}?cod={COD}", headers=auth_headers))
    assert by[9900173]["idfxe"] == 9900173
    assert by[9900173]["platitor_nume"] == "FURNIZOR TEST SRL"
    assert by[9900173]["data_doc"] == "19.01.2026"      # TEXT, nu data ISO
    assert by[9900171]["idfxe"] is None
    assert by[9900171]["platitor_nume"] is None


def test_money_is_zero_not_null(client, auth_headers, demo_rows):
    """Coloanele de bani vin 0-ate server-side (niciodata null), inclusiv pe plata fara
    extras (suma_debit/suma_credit = 0.0)."""
    by = _by_id(client.get(f"{URL}?cod={COD}", headers=auth_headers))
    assert by[9900171]["suma"] == 1331.0
    assert by[9900171]["suma_debit"] == 0.0
    assert by[9900171]["suma_credit"] == 0.0


def test_tip_and_flags(client, auth_headers, demo_rows):
    """Tip + Incarcat/Preluat ies neschimbate (clientul deriva iconita + verdele INCASARE)."""
    by = _by_id(client.get(f"{URL}?cod={COD}", headers=auth_headers))
    assert by[9900171]["tip"] == "PLATA"
    assert by[9900171]["incarcat"] is True and by[9900171]["preluat"] is False
    assert by[9900172]["tip"] == "INCASARE"
    assert by[9900172]["incarcat"] is False and by[9900172]["preluat"] is True


def test_rows_ordered_by_data_plata(client, auth_headers, demo_rows):
    """ORDER BY Data_plata, IdPlataFX: stabil intre refresh-uri; clientul se bazeaza pe el
    pentru ordinea lunilor/zilelor si pentru cea mai veche zi ne-ordonantata."""
    first = _plati(client.get(f"{URL}?cod={COD}", headers=auth_headers))
    second = _plati(client.get(f"{URL}?cod={COD}", headers=auth_headers))
    assert [r["id_plata_fx"] for r in first] == [r["id_plata_fx"] for r in second]
    dates = [r["data_plata"] for r in first]
    assert dates == sorted(dates)
