# Server-side unit test for GET /api/forexe/receptii (slice 0015 — vederea Recepții).
# Run on the Flask host, from the PYTHON folder:  python -m pytest tests/test_forexe_receptii.py
#
# Preconditions (same shape as test_forexe_rezervari.py):
#   1) FX_Angajamente / FX_Indicatori / FX_Receptii_R / FX_Receptii_H / FX_Receptii /
#      FX_Plati / Clasificatii / Unitati exist on the 000_DEMO MariaDB;
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
URL = "/api/forexe/receptii"

COD = "REC1"          # angajament cu doua receptii (o incarcata, una preluata) + plati
COD_GOL = "REC0"      # angajament fara nicio receptie -> receptii []

# IdClsfAcc al clasificatiei de test — valoare din afara plajei reale, ca sa nu poata
# coliziona cu date de productie si ca sa se poata sterge fara ambiguitate.
CLSF_ACC = 990015

# Cheile pe care contractul de fir le promite (oglindesc ReceptieRow pe partea VB.NET).
ROW_KEYS = (
    "idrr", "nrcrt_r", "data_r", "suma_antet", "incarcat", "preluat",
    "idrh", "nrcrt_h", "data_h", "total", "difh", "sters_h", "descriere_h",
    "idr", "id_clsf", "cod_indicator", "clsf", "denumire", "nrcrt_ind", "valoare", "dif",
)
PLATA_KEYS = ("data_plata", "suma")


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
    cur.execute("DELETE FROM FX_Plati WHERE CodAngajament = %s", (cod,))
    cur.execute("DELETE FROM FX_Receptii WHERE CodAngajament = %s", (cod,))
    cur.execute("DELETE FROM FX_Receptii_H WHERE CodAngajament = %s", (cod,))
    cur.execute("DELETE FROM FX_Receptii_R WHERE CodAngajament = %s", (cod,))
    cur.execute("DELETE FROM FX_Indicatori WHERE CodAngajament = %s", (cod,))
    cur.execute("DELETE FROM FX_Angajamente WHERE CodAngajament = %s", (cod,))


@pytest.fixture
def demo_rows():
    """Seeds a known angajament with receptii, yields the connection, then removes it.

    REC1 are DOUA receptii, alese ca sa acopere exact deciziile feliei:
      - receptia 1 (IDRR 970001): Preluat=1 -> iconita „jos"; un antet cu o linie;
      - receptia 2 (IDRR 970002): Incarcat=1 -> iconita „sus"; un antet cu o linie;
      - un singur indicator (IND-A) cu clasificatie reala -> Clsf populat pe fiecare linie;
      - doua plati (Ianuarie + Februarie) -> `plati` are forma corecta si e ordonata.
    REC0 : angajament fara nicio receptie -> receptii [].

    Nomenclatorul e insamantat DELIBERAT „murdar" (ca la Sumar 0011-03 / Rezervari 0014),
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

        # Clsf/Titlu/SS sunt coloane GENERATED, deci NU se scriu. Componentele trebuie
        # sa existe in nomenclatoarele AVACONT_COMUN.Defa* (FK-uri pe coloanele generate);
        # se folosesc aceleasi valori reale ca la Sumar/Rezervari (65.02.04.02.20.01.03).
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

        for cod, descriere in ((COD, "Angajament receptii"), (COD_GOL, "Angajament gol")):
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

        # (IDRR, NRCRT, DataR, SumaAntet, Incarcat, Preluat)
        receptii_r = [
            (970001, 1, "2026-01-19", 2864.12, 0, 1),   # Preluat -> „jos"
            (970002, 2, "2026-02-16", 3480.43, 1, 0),   # Incarcat -> „sus"
        ]
        for (idrr, nrcrt, data_r, suma, incarcat, preluat) in receptii_r:
            cur.execute(
                "INSERT INTO FX_Receptii_R (IDRR, NRCRT, CodAngajament, Tip, DataR, "
                "SumaAntet, Descriere, TipReceptie, Incarcat, Preluat) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (idrr, nrcrt, COD, "Final", data_r, suma, "Plata factura", "NOU",
                 incarcat, preluat),
            )

        # (IDRH, IDRR, DataH, Total, DIFH, NrCrt, Descriere, Sters)
        receptii_h = [
            (980001, 970001, "2026-01-19", 2864.12, 2864.12, 1, "Plata factura", 0),
            (980002, 970002, "2026-02-16", 3480.43, 616.31, 1, "Plata factura", 0),
        ]
        for (idrh, idrr, data_h, total, difh, nrcrt, descr, sters) in receptii_h:
            cur.execute(
                "INSERT INTO FX_Receptii_H (IDRH, IDRR, CodAngajament, DataH, Total, "
                "DIFH, NrCrt, Descriere, TipReceptie, Sters) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (idrh, idrr, COD, data_h, total, difh, nrcrt, descr, "Final", sters),
            )

        # (IDR, IDRH, Valoare, DIF)
        receptii = [
            (990001, 980001, 2864.12, 2864.12),
            (990002, 980002, 3480.43, 616.31),
        ]
        for (idr, idrh, valoare, dif) in receptii:
            cur.execute(
                "INSERT INTO FX_Receptii (IDR, IDRH, IdClsf, CodAI, CodAngajament, "
                "CodIndicator, Clsf, Valoare, DIF, IdUnitate) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (idr, idrh, CLSF_ACC, cod_ai, COD, "IND-A", "65.02.04.02.20.01.03",
                 valoare, dif, id_unitate),
            )

        # (IdPlataFX, Data_plata, Suma)
        plati = [
            (990101, "2026-01-25", 1000.00),
            (990102, "2026-02-20", 500.00),
        ]
        for (idp, data_plata, suma) in plati:
            cur.execute(
                "INSERT INTO FX_Plati (IdPlataFX, CodAngajament, CodIndicator, "
                "Data_plata, Suma) VALUES (%s,%s,%s,%s,%s)",
                (idp, COD, "IND-A", data_plata, suma),
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


def _receptii(resp):
    return resp.get_json()["receptii"]


def _plati(resp):
    return resp.get_json()["plati"]


def _by_idrr(resp):
    return {r["idrr"]: r for r in _receptii(resp)}


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
    """Un angajament fara receptii e legitim -> 200, receptii [] si plati []."""
    resp = client.get(f"{URL}?cod=NUEXISTA-XYZ", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["receptii"] == []
    assert body["plati"] == []
    assert body["cod"] == "NUEXISTA-XYZ"


def test_angajament_without_receptii_is_empty(client, auth_headers, demo_rows):
    assert _receptii(client.get(f"{URL}?cod={COD_GOL}", headers=auth_headers)) == []


def test_row_shape_matches_contract(client, auth_headers, demo_rows):
    rows = _receptii(client.get(f"{URL}?cod={COD}", headers=auth_headers))
    assert rows, "trebuie sa existe receptii"
    for r in rows:
        assert set(r.keys()) == set(ROW_KEYS)


def test_plati_shape_matches_contract(client, auth_headers, demo_rows):
    plati = _plati(client.get(f"{URL}?cod={COD}", headers=auth_headers))
    assert len(plati) == 2
    for p in plati:
        assert set(p.keys()) == set(PLATA_KEYS)
    # ORDER BY Data_plata: crescator.
    dates = [p["data_plata"] for p in plati]
    assert dates == sorted(dates)


# ---------------------------------------------------------------------------
# Deciziile feliei
# ---------------------------------------------------------------------------

def test_two_receptii_with_parents_populated(client, auth_headers, demo_rows):
    """Doua antete (cate o linie) -> doua randuri, fiecare cu parintii R + H populati."""
    by = _by_idrr(client.get(f"{URL}?cod={COD}", headers=auth_headers))
    assert set(by.keys()) == {970001, 970002}
    r1 = by[970001]
    assert r1["idrh"] == 980001
    assert r1["data_r"] == "2026-01-19"
    assert r1["data_h"] == "2026-01-19"
    assert r1["idr"] == 990001
    assert r1["descriere_h"] == "Plata factura"


def test_root_icon_state_flags_are_bools(client, auth_headers, demo_rows):
    """Incarcat/Preluat ies ca True/False (TINYINT) — clientul deriva sus/jos/neutru."""
    by = _by_idrr(client.get(f"{URL}?cod={COD}", headers=auth_headers))
    r_preluat = by[970001]
    r_incarcat = by[970002]
    assert r_preluat["incarcat"] is False and r_preluat["preluat"] is True
    assert r_incarcat["incarcat"] is True and r_incarcat["preluat"] is False


def test_sters_flag_is_bool(client, auth_headers, demo_rows):
    """sters_h iese ca bool — pass 0015-02 exclude anteturile sterse din cumulul DIFH."""
    r = _by_idrr(client.get(f"{URL}?cod={COD}", headers=auth_headers))[970001]
    assert isinstance(r["sters_h"], bool)
    assert r["sters_h"] is False


def test_classification_is_the_generated_dotted_code(client, auth_headers, demo_rows):
    """Clsf vine din coloana GENERATED (Capitol.Subcapitol.Articol.Alineat), prin
    FX_Indicatori (nu FX_Receptii.IdClsf) — drumul verificat in 0011-03. NrCrt din
    FX_Indicatori insoteste linia."""
    r = _by_idrr(client.get(f"{URL}?cod={COD}", headers=auth_headers))[970001]
    assert r["clsf"] == "65.02.04.02.20.01.03"
    assert r["denumire"] == "Clasificație test"
    assert r["cod_indicator"] == "IND-A"
    assert r["nrcrt_ind"] == 1


def test_duplicate_classification_does_not_fan_out(client, auth_headers, demo_rows):
    """Nomenclatorul are duplicate reale pe (IdClsfAcc, IdUnitate) + un vecin de alta
    unitate. Cu un JOIN, fiecare linie ar aparea de 2-3 ori; subinterogarea scalara cu
    LIMIT 1 + predicatul IdUnitate garanteaza exact doua randuri, cu Clsf populat."""
    rows = _receptii(client.get(f"{URL}?cod={COD}", headers=auth_headers))
    assert len(rows) == 2, f"fan-out: {len(rows)} randuri in loc de 2"
    assert all(r["clsf"] == "65.02.04.02.20.01.03" for r in rows)


def test_rows_are_deterministically_ordered(client, auth_headers, demo_rows):
    """ORDER BY (R.NRCRT, R.DataR, H.NrCrt, H.DataH, Rc.IDR): stabil intre refresh-uri."""
    first = _receptii(client.get(f"{URL}?cod={COD}", headers=auth_headers))
    second = _receptii(client.get(f"{URL}?cod={COD}", headers=auth_headers))
    assert [r["idr"] for r in first] == [r["idr"] for r in second]
    nrcrt_r = [r["nrcrt_r"] for r in first]
    assert nrcrt_r == sorted(nrcrt_r)


def test_dif_and_difh_are_floats(client, auth_headers, demo_rows):
    """DIF (per linie) + DIFH (per antet) ies ca float — reconciliation pass 0015-02."""
    r = _by_idrr(client.get(f"{URL}?cod={COD}", headers=auth_headers))[970002]
    assert r["dif"] == 3480.43
    assert r["difh"] == 616.31
