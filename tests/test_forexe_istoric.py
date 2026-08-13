# Server-side unit test for GET /api/forexe/istoric (slice 0022 — vederea Istoric).
# Run on the Flask host, from the PYTHON folder:  python -m pytest tests/test_forexe_istoric.py
#
# Preconditions (same shape as test_forexe_ddf.py / test_forexe_plati.py):
#   1) FX_Istoric / FX_Indicatori / FX_Angajamente / Clasificatii / Unitati exist on 000_DEMO,
#      plus the AVACONT_COMUN nomenclator tables the classification join reads
#      (DefaClsfF / DefaArticol — see the host-verification note in routes/forexe/istoric.py);
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
URL = "/api/forexe/istoric"

COD = "IST1"          # angajament cu 4 randuri de istoric pe o clasificatie
COD_GOL = "IST0"      # angajament fara niciun rand -> randuri [] si clasificatii []

# IdClsfAcc-uri din afara plajei reale, ca sa nu poata coliziona cu date de productie si ca
# sa se poata sterge fara ambiguitate. CLSF_ACC e referit de randurile de istoric; CLSF_OTHER
# exista in nomenclator dar NU e referit -> testul de domeniu il asteapta ABSENT.
CLSF_ACC = 990222
CLSF_OTHER = 990229

# Cheile pe care contractul de fir le promite (oglindesc IstoricRand pe partea VB.NET, §2.1).
ROW_KEYS = (
    "id", "data_fx", "clsf", "id_clsf", "tip_rand", "cod_indicator", "cod_ai",
    "descriere", "observatii",
    "val_rezervare_i", "val_rezervare_d", "val_rezervare_ant", "val_rezervare_dif",
    "val_ang_leg", "val_receptie", "val_plata",
    "id_trezor", "doc", "idrev",
)
# Coloane FX_Istoric care NU au voie sa apara pe fir (§2.1).
EXCLUDED_KEYS = ("utilizator", "hash", "prelucrat", "dtq", "val_receptie_t", "rez_ord")

CLSF_KEYS = (
    "id_clsf", "clsf", "capitol", "subcapitol", "articol", "alineat",
    "den_subcapitol", "den_articol", "den_alineat",
)

# Componentele reale ale clasificatiei de test — aceleasi ca la Sumar/Rezervari/Recepții/
# Plăți/DDF, ca sa satisfaca FK-urile pe coloanele GENERATE (Titlu/ClsfF/ClsfE/SS).
CAP, SUBCAP, ART, ALIN = "65.02", "04.02", "20.01", "03"
CLSF_TEXT = "65.02.04.02.20.01.03"


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


def _unitate(cur):
    """Primul IdUnitate REAL al bazei conectate, citit din Unitati.

    Nu se hardcodeaza 0: Clasificatii are FOREIGN KEY (IdUnitate) -> Unitati, deci un 0
    inventat pica insertul din fixture cu o eroare de constrangere, nu cu una de logica.
    """
    cur.execute("SELECT IdUnitate FROM Unitati ORDER BY IdUnitate LIMIT 1")
    row = cur.fetchone()
    if not row:
        raise AssertionError("Unitati este gol — fixture-ul nu are IdUnitate valid.")
    return row[0]


def _cleanup(cur):
    for cod in (COD, COD_GOL):
        cur.execute("DELETE FROM FX_Istoric WHERE CodAngajament = %s", (cod,))
        cur.execute("DELETE FROM FX_Indicatori WHERE CodAngajament = %s", (cod,))
        cur.execute("DELETE FROM FX_Angajamente WHERE CodAngajament = %s", (cod,))
    cur.execute("DELETE FROM Clasificatii WHERE IdClsfAcc IN (%s,%s)", (CLSF_ACC, CLSF_OTHER))


@pytest.fixture
def demo_rows():
    """Seeds an angajament with four FX_Istoric rows, yields the connection, then removes it.

    IST1 are PATRU randuri, alese ca sa acopere exact deciziile feliei:
      - doua randuri in aceeasi zi (17 ian) cu Clsf diferit -> pinul ordonarii DataFX, Clsf;
      - un rand cu Val_Rezervare_Dif NEGATIV -> semnul se pastreaza pe fir;
      - un rand-plata cu ora reala 19:21:32 -> data_fx pastreaza componenta de timp (§2.3);
      - Descriere/Observatii cu diacritice -> ensure_ascii=False.
    Toate randurile au IdClsf = CLSF_ACC (id ACCESS), deci clasificatia se rezolva prin
    IdClsfAcc, NU prin IDClsf (cheia inversa fata de DDF — §2.5).

    Nomenclatorul e insamantat DELIBERAT cu DOUA randuri pe acelasi IdClsfAcc (duplicat real,
    ca la 0011-03), ca sa dovedeasca dedup-ul: endpoint-ul trebuie sa intoarca O SINGURA
    intrare pentru CLSF_ACC, nu doua. CLSF_OTHER exista dar NU e referit de niciun rand de
    istoric -> testul de domeniu il asteapta absent.

    Clsf/Titlu/SS sunt coloane GENERATED, deci NU se scriu.
    """
    conn = get_db_connection(DB_NAME)
    cur = conn.cursor()
    id_unitate = _unitate(cur)
    try:
        _cleanup(cur)

        # Nomenclator: duplicat real pe (IdClsfAcc, IdUnitate) -> pinul dedup-ului.
        for _ in range(2):
            cur.execute(
                "INSERT INTO Clasificatii (IdClsfAcc, IdUnitate, Capitol, Subcapitol, "
                "Articol, Alineat, Denumire) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (CLSF_ACC, id_unitate, CAP, SUBCAP, ART, ALIN, "Clasificație test"),
            )
        # PK-ul generat al ultimului rand — folosit ca sa dovedim IDClsf != IdClsfAcc (§2.5).
        id_clsf_pk = cur.lastrowid
        # Vecin prezent in nomenclator dar NEreferit de istoric -> trebuie sa lipseasca.
        cur.execute(
            "INSERT INTO Clasificatii (IdClsfAcc, IdUnitate, Capitol, Subcapitol, "
            "Articol, Alineat, Denumire) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (CLSF_OTHER, id_unitate, CAP, SUBCAP, ART, "04", "Clasificație nereferită"),
        )

        for cod, descriere in ((COD, "Angajament istoric"), (COD_GOL, "Angajament gol")):
            cur.execute(
                "INSERT INTO FX_Angajamente (CodAngajament, Descriere, Stare, DC, "
                "DataCreare, ASCUNS, Incarcat, Preluat) VALUES (%s,%s,%s,%s,%s,0,1,1)",
                (cod, descriere, "În derulare", DB_NAME, "2026-01-17"),
            )

        # FX_Indicatori: poarta IdUnitate + IdClsf (id ACCESS = IdClsfAcc) pentru predicatul
        # de unitate al ierarhiei de clasificatii (exact ca bFilter_Click din Access).
        cod_ai = f"{COD}-AI-A"
        cur.execute(
            "INSERT INTO FX_Indicatori (CodAI, CodAngajament, CodIndicator, IdClsf, "
            "IdUnitate, NrCrt, SS) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (cod_ai, COD, "IND-A", CLSF_ACC, id_unitate, 1, "02A"),
        )

        # (ID, DataFX, Clsf, TipRand, val_rez_i, val_rez_dif, val_receptie, val_plata, descr, obs)
        randuri = [
            (9902201, "2026-01-17 08:00:00", "65.02.04.02.20.01.01", "Rez_Initiala",
             3065.12, 0.0, 0.0, 0.0, "Inițializare angajament", "Rând contract diacritică ă"),
            (9902202, "2026-01-17 08:00:00", "65.02.04.02.20.01.30", "Rez_Initiala+",
             700.0, 0.0, 0.0, 0.0, "Angajament nou", "Contract nou"),
            (9902203, "2026-01-18 11:00:30", CLSF_TEXT, "Receptie",
             0.0, -50.0, 700.0, 0.0, "Salvare recepție", "Recepție parțială"),
            (9902204, "2026-01-19 19:21:32", CLSF_TEXT, "PLATA_PLATA",
             0.0, 0.0, 0.0, 700.0, "Înregistrare Plată", "Plată document 48"),
        ]
        for (rid, data_fx, clsf, tip, vri, vdif, vrec, vpl, descr, obs) in randuri:
            cur.execute(
                "INSERT INTO FX_Istoric (ID, IDREV, IdClsf, CodAI, CodAngajament, "
                "CodIndicator, DataFX, Utilizator, Descriere, Observatii, "
                "Val_Rezervare_I, Val_Rezervare_D, Val_AngLeg, Val_Rezervare_Ant, "
                "Val_Rezervare_Dif, Val_Receptie, Val_Plata, TipRand, IdTrezor, Doc, "
                "Prelucrat, Rez_Ord) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (rid, None, CLSF_ACC, cod_ai, COD, "IND-A", data_fx,
                 "pytest-op", descr, obs, vri, 0.0, 0.0, 0.0, vdif, vrec, vpl, tip,
                 "", "", 1, 0),
            )

        conn.commit()
        # Expunem PK-ul generat testului de directie a cheii.
        conn._istoric_test_id_clsf_pk = id_clsf_pk
        yield conn
    finally:
        cur = conn.cursor()
        _cleanup(cur)
        conn.commit()
        conn.close()


def _body(resp):
    return resp.get_json()


# ---------------------------------------------------------------------------
# 1-2. Guard + validare parametri
# ---------------------------------------------------------------------------

def test_missing_token_is_rejected(client):
    """§8.1: fara sesiune -> 401 cu motiv, nu 200 cu liste goale."""
    resp = client.get(f"{URL}?cod={COD}")
    assert resp.status_code == 401
    assert resp.get_json().get("reason")


def test_missing_and_blank_cod_return_400(client, auth_headers):
    """§8.2: cod lipsa -> 400; cod alb -> 400."""
    r1 = client.get(URL, headers=auth_headers)
    assert r1.status_code == 400
    assert "cod" in r1.get_json()["error"]
    r2 = client.get(f"{URL}?cod=%20%20", headers=auth_headers)
    assert r2.status_code == 400


# ---------------------------------------------------------------------------
# 3-7. Contract de raspuns
# ---------------------------------------------------------------------------

def test_unknown_cod_returns_empty_arrays_not_404(client, auth_headers):
    """§8.3: un angajament fara istoric e legitim -> 200 cu ambele liste goale."""
    resp = client.get(f"{URL}?cod=NUEXISTA-XYZ", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["randuri"] == []
    assert body["clasificatii"] == []
    assert body["cod"] == "NUEXISTA-XYZ"


def test_row_shape_matches_contract(client, auth_headers, demo_rows):
    """§8.4: fiecare rand poarta EXACT cheile promise, si NICIUNA dintre cele excluse."""
    body = _body(client.get(f"{URL}?cod={COD}", headers=auth_headers))
    assert body["randuri"]
    for r in body["randuri"]:
        assert set(r.keys()) == set(ROW_KEYS)
        for bad in EXCLUDED_KEYS:
            assert bad not in r, f"coloana exclusa {bad} a ajuns pe fir"


def test_ordered_by_datafx_then_clsf(client, auth_headers, demo_rows):
    """§8.5: ORDER BY DataFX, Clsf — determinist si stabil intre refresh-uri.

    Cele doua randuri din 17 ian au acelasi DataFX dar Clsf diferit (...01 < ...30),
    deci ordonarea secundara pe Clsf le fixeaza in aceeasi ordine de fiecare data.
    """
    first = _body(client.get(f"{URL}?cod={COD}", headers=auth_headers))["randuri"]
    second = _body(client.get(f"{URL}?cod={COD}", headers=auth_headers))["randuri"]
    assert [r["id"] for r in first] == [r["id"] for r in second]
    keys = [(r["data_fx"], r["clsf"]) for r in first]
    assert keys == sorted(keys)
    # Randurile din aceeasi zi: cel cu Clsf ...01 inaintea celui cu ...30.
    assert first[0]["id"] == 9902201
    assert first[1]["id"] == 9902202


def test_negative_values_keep_their_sign(client, auth_headers, demo_rows):
    """§8.6: o valoare negativa (Val_Rezervare_Dif = -50) isi pastreaza semnul pe fir."""
    randuri = _body(client.get(f"{URL}?cod={COD}", headers=auth_headers))["randuri"]
    by_id = {r["id"]: r for r in randuri}
    assert by_id[9902203]["val_rezervare_dif"] == -50.0
    assert all(isinstance(r["val_rezervare_dif"], float) for r in randuri)


def test_data_fx_carries_time_component(client, auth_headers, demo_rows):
    """§8.7: data_fx pastreaza ORA (guard impotriva copierii trunchierii la zi din DDF)."""
    randuri = _body(client.get(f"{URL}?cod={COD}", headers=auth_headers))["randuri"]
    by_id = {r["id"]: r for r in randuri}
    dfx = by_id[9902204]["data_fx"]
    assert "T" in dfx, "data_fx nu e ISO datetime (lipseste separatorul de timp)"
    assert dfx.endswith("19:21:32"), "ora a fost trunchiata (ca in _iso-ul din DDF)"


# ---------------------------------------------------------------------------
# 8-11. Ierarhia de clasificatii
# ---------------------------------------------------------------------------

def test_clasificatii_scoped_to_present_idclsf_and_unit(client, auth_headers, demo_rows):
    """§8.8: `clasificatii` = doar IdClsf-urile prezente in FX_Istoric pentru cod, si doar
    ale unitatii angajamentului. CLSF_OTHER exista in nomenclator dar nu e referit -> absent."""
    clasificatii = _body(client.get(f"{URL}?cod={COD}", headers=auth_headers))["clasificatii"]
    ids = {c["id_clsf"] for c in clasificatii}
    assert CLSF_ACC in ids
    assert CLSF_OTHER not in ids, "o clasificatie nereferita de istoric a intrat in meniu"
    for c in clasificatii:
        assert set(c.keys()) == set(CLSF_KEYS)


def test_no_fan_out_one_entry_per_id_clsf(client, auth_headers, demo_rows):
    """§8.9: nomenclatorul are DOUA randuri pe CLSF_ACC -> exact O intrare, nu doua.

    E o lista, nu un lookup scalar, deci LIMIT 1 nu se aplica: GROUP BY IdClsfAcc.
    """
    clasificatii = _body(client.get(f"{URL}?cod={COD}", headers=auth_headers))["clasificatii"]
    ids = [c["id_clsf"] for c in clasificatii]
    assert ids.count(CLSF_ACC) == 1
    assert len(ids) == len(set(ids)), "fan-out: un id_clsf apare de mai multe ori"


def test_key_resolves_via_idclsfacc_not_idclsf(client, auth_headers, demo_rows):
    """§8.10: intrarea se rezolva prin IdClsfAcc, NU prin IDClsf (cheia inversa fata de DDF).

    FX_Istoric.IdClsf = CLSF_ACC (id ACCESS). PK-ul generat (IDClsf) e diferit, deci o
    cheie inversata ar intoarce ZERO intrari — nu o eroare. Construim exact acest caz.
    """
    conn = demo_rows
    id_clsf_pk = conn._istoric_test_id_clsf_pk
    assert id_clsf_pk != CLSF_ACC, "fixture invalid: IDClsf coincide cu IdClsfAcc"
    clasificatii = _body(client.get(f"{URL}?cod={COD}", headers=auth_headers))["clasificatii"]
    ids = {c["id_clsf"] for c in clasificatii}
    assert CLSF_ACC in ids, "cheia s-a rezolvat pe IDClsf, nu pe IdClsfAcc -> meniu gol"
    assert id_clsf_pk not in ids, "id_clsf poarta PK-ul nomenclatorului, nu id-ul Access"


def test_diacritics_are_literal(client, auth_headers, demo_rows):
    """§8.11: ensure_ascii=False — diacriticele romanesti ajung literale, nu \\uXXXX.

    Acopera atat corpul de date (Descriere) cat si mesajul de eroare (« lipsă »).
    """
    body_text = client.get(f"{URL}?cod={COD}", headers=auth_headers).get_data(as_text=True)
    assert "Inițializare" in body_text
    err_text = client.get(URL, headers=auth_headers).get_data(as_text=True)
    assert "lipsă" in err_text


# ---------------------------------------------------------------------------
# 12. Finding — nu pica niciodata, doar raporteaza (§8.12, Status_migrare_5 §9)
# ---------------------------------------------------------------------------

def test_report_duplicate_clsf_finding(client, auth_headers, capsys):
    """§8.12: cate (IdUnitate, IdClsfAcc) au MAI MULT de un Clsf distinct pe baza reala?

    Zero -> duplicatele sunt zgomot si fiecare LIMIT 1 din cod e sigur; ne-zero -> un id
    Access se mapeaza la doua clasificatii diferite, iar cifrele de bani din cele patru vederi
    livrate sunt afectate. Testul NU pica niciodata — doar raporteaza, ca sa se scrie in
    worklog. Inchide punctul deschis din Status_migrare_5 §9 la fiecare rulare pe host.
    """
    conn = get_db_connection(DB_NAME)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM ("
            "  SELECT IdUnitate, IdClsfAcc FROM Clasificatii "
            "  GROUP BY IdUnitate, IdClsfAcc HAVING COUNT(DISTINCT Clsf) > 1) t")
        conflicting = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM Clasificatii")
        total = cur.fetchone()[0]
    finally:
        conn.close()

    with capsys.disabled():
        print(f"\n[§8.12 CONSTATARE] Clasificatii pe {DB_NAME}: {total} randuri, "
              f"{conflicting} (IdUnitate, IdClsfAcc) cu >1 Clsf distinct.")
    assert conflicting >= 0      # constatare, nu poarta
