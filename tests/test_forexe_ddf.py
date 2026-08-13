# Server-side unit test for GET /api/forexe/ddf (slice 0020-01 — vederea DDF).
# Run on the Flask host, from the PYTHON folder:  python -m pytest tests/test_forexe_ddf.py
#
# Preconditions (same shape as test_forexe_plati.py):
#   1) FX_DDF / FX_DDF_REV / FX_DDF_REV_SA / Clasificatii / Unitati exist on 000_DEMO;
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
URL = "/api/forexe/ddf"

COD = "DDF1"          # angajament cu 1 antet + 3 revizii (una cu 3 linii de sectiune A)
COD_GOL = "DDF0"      # angajament fara niciun FX_DDF -> toate cele trei liste goale

# Chei de test din afara plajei reale, ca sa nu poata coliziona cu date de productie
# si ca sa se poata sterge fara ambiguitate.
IDDF = 9900201
IDREV_MULTI = 9900211      # 3 linii de sectiune A -> pinul de regresie al SUM-ului
IDREV_UNA = 9900212        # o singura linie
IDREV_FARA = 9900213       # ZERO linii -> TotalRevizie trebuie sa fie 0.0 (ramura COALESCE)
CLSF_ACC = 990020

# Valorile celor 3 linii ale reviziei multi-linie. Alese ca SUMA (600) sa NU fie egala cu
# niciuna dintre ele — exact asa se prinde defectul Access „ValCur AS TotalRevizie".
VAL_MULTI = (100.0, 200.0, 300.0)
SUM_MULTI = 600.0

ANTET_KEYS = (
    "iddf", "cod_angajament", "cual", "obiect_ddf", "comp", "program",
    "data_creare", "data_def", "stare", "part_ang", "cod_fiscal", "nume_partener",
    "salarii", "incarcat", "preluat",
)
REVIZIE_KEYS = (
    "idrev", "iddf", "numar_rev", "data_rev", "desc_scurta", "desc_lunga",
    "tip", "incarcat", "preluat", "semnatura", "total_revizie",
)
LINIE_KEYS = (
    "id_sec_a", "idrev", "id_clsf", "clsf", "ss", "element_fund", "parametrii_fund",
    "val_prec", "val_cur", "val_tot",
)
SECTIUNEB_KEYS = (
    "id_sec_b", "idrev", "cod_angajament", "cod_indicator", "cod_ssi",
    "ca_anterior", "inf1", "cb_anterior", "inf2",
)
ATASAMENT_KEYS = (
    "id_rev_att", "idrev", "cale_fisier", "prt_scr", "date_fisier",
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
    # Ordinea conteaza chiar daca FK-urile sunt ON DELETE CASCADE: stergem explicit de la
    # frunza spre radacina, ca testul sa nu depinda de comportamentul cascadei.
    cur.execute("DELETE FROM FX_DDF_REV_SA WHERE IDDF = %s", (IDDF,))
    cur.execute("DELETE FROM FX_DDF_REV_SB WHERE IDDF = %s", (IDDF,))
    cur.execute("DELETE FROM FX_DDF_REV_ATT WHERE IDDF = %s", (IDDF,))
    cur.execute("DELETE FROM FX_DDF_REV WHERE IDDF = %s", (IDDF,))
    cur.execute("DELETE FROM FX_DDF WHERE IDDF = %s", (IDDF,))
    cur.execute("DELETE FROM FX_DDF WHERE CodAngajament IN (%s,%s)", (COD, COD_GOL))
    cur.execute("DELETE FROM Clasificatii WHERE IdClsfAcc = %s", (CLSF_ACC,))


@pytest.fixture
def demo_rows():
    """Seeds one DDF header with three revisions, yields the connection, then removes it.

    DDF1 (IDDF 9900201, CUAL 3) are TREI revizii, alese ca sa acopere exact deciziile feliei:
      - revizia 0 (IDREV 9900211): TREI linii de sectiune A (100/200/300) -> TotalRevizie
        trebuie sa fie 600, adica SUMA, nu vreuna dintre valorile individuale. Acesta este
        pinul de regresie pentru defectul Access `SA.ValCur AS TotalRevizie` (§2.1).
        Una dintre linii are Clsf GOL, ca sa se exercite caderea pe nomenclator.
      - revizia 1 (IDREV 9900212): o singura linie (50) -> TotalRevizie = 50.
      - revizia 2 (IDREV 9900213): ZERO linii -> TotalRevizie = 0.0 (ramura COALESCE), iar
        revizia TOT trebuie sa apara (nu are voie sa dispara pentru ca nu are sectiune A).
    DDF0 : cod fara niciun FX_DDF -> toate cele trei liste goale.

    FX_DDF_REV_SA.IdClsf este FK spre Clasificatii.IDClsf (CHEIA PRIMARA, nu IdClsfAcc —
    invers fata de FX_Indicatori), deci fixture-ul insereaza intai clasificatia si CITESTE
    IDClsf-ul generat. A hardcoda un id ar pica pe constrangere.
    """
    conn = get_db_connection(DB_NAME)
    cur = conn.cursor()
    id_unitate = _unitate(cur)
    try:
        _cleanup(cur)

        # Clsf/Titlu/SS sunt coloane GENERATED, deci NU se scriu. Componentele trebuie sa
        # existe in nomenclatoarele AVACONT_COMUN.Defa* (FK-uri pe coloanele generate); se
        # folosesc aceleasi valori reale ca la Sumar/Rezervari/Recepții/Plăți.
        cur.execute(
            "INSERT INTO Clasificatii (IdClsfAcc, IdUnitate, Capitol, Subcapitol, "
            "Articol, Alineat, Denumire) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (CLSF_ACC, id_unitate, "65.02", "04.02", "20.01", "03", "Clasificație test"),
        )
        # PK-ul generat al nomenclatorului — ACESTA merge in FX_DDF_REV_SA.IdClsf.
        id_clsf = cur.lastrowid

        cur.execute(
            "INSERT INTO FX_DDF (IDDF, CodAngajament, CUAL, Comp, Salarii, DataCreare, DC, "
            "Program, DataDef, Incarcat, Preluat, ObiectDDF, Stare, PartAng, CodFiscal, "
            "NumePartener) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (IDDF, COD, 3, "contabilitate", 0, "2026-01-18", DB_NAME,
             "0000002510", "2026-01-18", 1, 1, "Obiect DDF de test", "În derulare",
             1, "46877331", "PARTENER TEST SRL"),
        )

        # (IDREV, NumarRev, DataRev, Desc_Scurta)
        revizii = [
            (IDREV_MULTI, 0, "2026-01-18", "Revizie inițială"),
            (IDREV_UNA, 1, "2026-01-30", "Mărire"),
            (IDREV_FARA, 2, "2026-02-11", "Revizie fără secțiune A"),
        ]
        for (idrev, numar_rev, data_rev, desc) in revizii:
            cur.execute(
                "INSERT INTO FX_DDF_REV (IDREV, IDDF, CodAngajament, Tip, NumarRev, DataRev, "
                "Desc_Scurta, Desc_Lunga_ANSI, Incarcat, Preluat, DC, Semnatura) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (idrev, IDDF, COD, "În derulare", numar_rev, data_rev, desc, desc,
                 1, 0, DB_NAME, None),
            )

        # Trei linii pe revizia multi-linie. A treia are Clsf GOL -> cade pe nomenclator.
        # IdClsfAcc e NOT NULL in schema, deci se completeaza, dar NU e cheia de citire.
        for i, val in enumerate(VAL_MULTI):
            clsf_text = "" if i == 2 else "65.02.04.02.20.01.03"
            cur.execute(
                "INSERT INTO FX_DDF_REV_SA (IDDF, IDREV, CodAngajament, CodIndicator, "
                "IdClsfAcc, IdClsf, Clsf, ElementFund, ParametriiFund, ValPrec, ValCur, "
                "ValTot) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (IDDF, IDREV_MULTI, COD, f"IND-{i}", CLSF_ACC, id_clsf, clsf_text,
                 f"Element {i}", f"Parametru {i}", 0.0, val, val),
            )

        cur.execute(
            "INSERT INTO FX_DDF_REV_SA (IDDF, IDREV, CodAngajament, CodIndicator, "
            "IdClsfAcc, IdClsf, Clsf, ElementFund, ParametriiFund, ValPrec, ValCur, ValTot) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (IDDF, IDREV_UNA, COD, "IND-U", CLSF_ACC, id_clsf, "65.02.04.02.20.01.03",
             "Element unic", "Parametru unic", 600.0, 50.0, 650.0),
        )

        # O linie de sectiune B pe revizia multi-linie (necesara PDF-ului, §2.8) si un
        # atasament (DateFisier = base64), ambele pentru testele cu pentru_generare=1.
        cur.execute(
            "INSERT INTO FX_DDF_REV_SB (IDDF, IDREV, CodAngajament, CodIndicator, "
            "IdClsfAcc, IdClsf, CodSSI, CA_Anterior, Inf1, CB_Anterior, Inf2) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (IDDF, IDREV_MULTI, COD, "IND-B", CLSF_ACC, id_clsf, "01A",
             1000.0, 200.0, 3000.0, 400.0),
        )
        cur.execute(
            "INSERT INTO FX_DDF_REV_ATT (IDDF, IDREV, CaleFisier, PrtScr, DateFisier) "
            "VALUES (%s,%s,%s,%s,%s)",
            (IDDF, IDREV_MULTI, "dovada.pdf", 0, "AAECAwQ="),
        )

        conn.commit()
        yield conn
    finally:
        cur = conn.cursor()
        _cleanup(cur)
        conn.commit()
        conn.close()


def _body(resp):
    return resp.get_json()


def _revizii_by_id(resp):
    return {r["idrev"]: r for r in _body(resp)["revizii"]}


# ---------------------------------------------------------------------------
# Guard + validare parametri
# ---------------------------------------------------------------------------

def test_missing_token_is_rejected(client):
    """Fara sesiune -> 401 cu motiv, nu 200 cu liste goale."""
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

def test_unknown_cod_returns_empty_arrays_not_404(client, auth_headers):
    """§3, testul 4: un angajament fara DDF e legitim -> 200 cu liste goale.

    Apelantul trebuie sa distinga „nu are DDF" de „a cazut transportul"; un 404 le-ar
    amesteca. Cu pentru_generare=1 cerem si sectiuneb/atasamente, tot goale.
    """
    resp = client.get(f"{URL}?cod=NUEXISTA-XYZ&pentru_generare=1", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["antet"] == []
    assert body["revizii"] == []
    assert body["linii"] == []
    assert body["sectiuneb"] == []
    assert body["atasamente"] == []
    assert body["cod"] == "NUEXISTA-XYZ"


def test_row_shapes_match_contract(client, auth_headers, demo_rows):
    """Cele trei liste poarta EXACT cheile promise (oglindesc POCO-urile VB.NET)."""
    body = _body(client.get(f"{URL}?cod={COD}", headers=auth_headers))
    assert body["antet"] and body["revizii"] and body["linii"]
    for a in body["antet"]:
        assert set(a.keys()) == set(ANTET_KEYS)
    for r in body["revizii"]:
        assert set(r.keys()) == set(REVIZIE_KEYS)
    for l in body["linii"]:
        assert set(l.keys()) == set(LINIE_KEYS)


# ---------------------------------------------------------------------------
# Deciziile feliei
# ---------------------------------------------------------------------------

def test_one_row_per_idrev(client, auth_headers, demo_rows):
    """§3, testul 1: numarul de revizii intoarse == COUNT(*) din FX_DDF_REV.

    Garda anti-fan-out: sectiunea A (3 linii pe o revizie) NU are voie sa multiplice
    revizia — exact ce face qFX_MAIN_DDF_TREE in Access.
    """
    conn = demo_rows
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM FX_DDF_REV WHERE IDDF IN "
        "(SELECT IDDF FROM FX_DDF WHERE CodAngajament = %s)", (COD,))
    expected = cur.fetchone()[0]
    revizii = _body(client.get(f"{URL}?cod={COD}", headers=auth_headers))["revizii"]
    assert len(revizii) == expected == 3
    assert len({r["idrev"] for r in revizii}) == 3      # chei distincte, nu repetate


def test_total_revizie_is_the_real_sum_not_a_single_valcur(client, auth_headers, demo_rows):
    """§3, testul 2 — PINUL DE REGRESIE AL FELIEI.

    Revizia multi-linie are 100 + 200 + 300. `total_revizie` trebuie sa fie 600 (SUMA) si
    sa NU fie egala cu niciuna dintre valorile individuale. Access aliaza `SA.ValCur AS
    TotalRevizie` si afiseaza o linie ARBITRARA; daca cineva „simplifica" interogarea
    inapoi la un join, acest test pica.
    """
    by = _revizii_by_id(client.get(f"{URL}?cod={COD}", headers=auth_headers))
    total = by[IDREV_MULTI]["total_revizie"]
    assert total == SUM_MULTI
    assert total not in VAL_MULTI, "total_revizie este valoarea unei singure linii (defectul Access)"
    assert by[IDREV_UNA]["total_revizie"] == 50.0


def test_revision_without_section_a_survives_with_zero_total(client, auth_headers, demo_rows):
    """COALESCE -> 0.0, si revizia NU dispare pentru ca nu are linii de sectiune A
    (un INNER JOIN pe FX_DDF_REV_SA, ca in Access, ar sterge-o)."""
    by = _revizii_by_id(client.get(f"{URL}?cod={COD}", headers=auth_headers))
    assert IDREV_FARA in by
    assert by[IDREV_FARA]["total_revizie"] == 0.0


def test_clsf_falls_back_to_nomenclator_when_column_is_blank(client, auth_headers, demo_rows):
    """§3, testul 3: niciun `clsf` gol cand FX_DDF_REV_SA.IdClsf rezolva in Clasificatii.

    Doua linii au coloana denormalizata populata, a treia o are GOALA -> COALESCE/NULLIF
    cade pe `Clasificatii.Clsf` (coloana GENERATED PERSISTENT). Cheia e IDClsf = PK, deci
    fara predicat IdUnitate si fara fan-out (capcana 0011-03 NU se aplica aici).
    """
    linii = _body(client.get(f"{URL}?cod={COD}", headers=auth_headers))["linii"]
    ale_reviziei = [l for l in linii if l["idrev"] == IDREV_MULTI]
    assert len(ale_reviziei) == 3
    assert all(l["clsf"] == "65.02.04.02.20.01.03" for l in ale_reviziei), \
        "linia cu Clsf gol nu a cazut pe nomenclator"


def test_linii_are_one_row_per_id_sec_a(client, auth_headers, demo_rows):
    """Subinterogarea scalara pe nomenclator nu are voie sa multiplice linia."""
    conn = demo_rows
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM FX_DDF_REV_SA WHERE IDDF = %s", (IDDF,))
    expected = cur.fetchone()[0]
    linii = _body(client.get(f"{URL}?cod={COD}", headers=auth_headers))["linii"]
    assert len(linii) == expected == 4
    assert len({l["id_sec_a"] for l in linii}) == 4


def test_second_cual_row_does_not_fan_out_revisions(client, auth_headers, demo_rows):
    """ABATERE DE LA PLAN, pin de regresie: PK-ul FX_DDF e COMPUS (IDDF, CUAL), deci acelasi
    IDDF poate purta doua randuri CUAL. Planul (§3) cerea `FX_DDF_REV joined to its FX_DDF
    header`; un asemenea INNER JOIN ar DUBLA fiecare revizie aici. Filtrul e `IN (SELECT...)`,
    deci reviziile raman 3 si doar `antet` creste la 2.
    """
    conn = demo_rows
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO FX_DDF (IDDF, CodAngajament, CUAL, Comp, DataCreare, ObiectDDF, Stare) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s)",
        (IDDF, COD, 4, "contabilitate", "2026-01-18", "Al doilea CUAL", "În derulare"),
    )
    conn.commit()

    body = _body(client.get(f"{URL}?cod={COD}", headers=auth_headers))
    assert len(body["antet"]) == 2, "al doilea CUAL trebuie sa apara in antet"
    assert len(body["revizii"]) == 3, "fan-out: reviziile s-au multiplicat pe CUAL"
    assert len(body["linii"]) == 4, "fan-out: liniile s-au multiplicat pe CUAL"


def test_antet_carries_pdf_path_inputs(client, auth_headers, demo_rows):
    """Antetul trebuie sa poarte tot ce compune calea PDF-ului (§2.5): CUAL, PartAng,
    NumePartener. Serverul NU stie nimic despre PDF-uri (coloanele ArePDFDDF/CalePDFDDF/
    AreDDF/CaleDDF nu exista in MariaDB) — clientul compune calea si scaneaza discul.
    """
    a = _body(client.get(f"{URL}?cod={COD}", headers=auth_headers))["antet"][0]
    assert a["cual"] == 3
    assert a["part_ang"] is True
    assert a["nume_partener"] == "PARTENER TEST SRL"
    assert a["salarii"] is False
    assert a["obiect_ddf"] == "Obiect DDF de test"


def test_revisions_ordered_by_data_then_numar(client, auth_headers, demo_rows):
    """ORDER BY DataRev, NumarRev: stabil intre refresh-uri; clientul se bazeaza pe el
    pentru ordinea cronologica a lunilor si a frunzelor."""
    first = _body(client.get(f"{URL}?cod={COD}", headers=auth_headers))["revizii"]
    second = _body(client.get(f"{URL}?cod={COD}", headers=auth_headers))["revizii"]
    assert [r["idrev"] for r in first] == [r["idrev"] for r in second]
    dates = [r["data_rev"] for r in first]
    assert dates == sorted(dates)


def test_money_is_zero_not_null(client, auth_headers, demo_rows):
    """Coloanele de bani vin 0-ate server-side (niciodata null)."""
    linii = _body(client.get(f"{URL}?cod={COD}", headers=auth_headers))["linii"]
    assert all(isinstance(l["val_prec"], float) for l in linii)
    assert all(isinstance(l["val_cur"], float) for l in linii)
    assert all(isinstance(l["val_tot"], float) for l in linii)


def test_parametrii_fund_is_carried_for_the_xml_builder(client, auth_headers, demo_rows):
    """ParametriiFund NU se afiseaza in grila (decizia 4) dar se poarta pe fir: felia 05 il
    scrie in Cell4 al sectiunii A. Adaugat acum ca sa nu se descopere abia atunci."""
    linii = _body(client.get(f"{URL}?cod={COD}", headers=auth_headers))["linii"]
    assert any(l["parametrii_fund"] == "Parametru unic" for l in linii)


# ---------------------------------------------------------------------------
# Felia 05: sectiunea B + atasamente (opt-in) + ss pe linii
# ---------------------------------------------------------------------------

def test_ss_is_carried_on_linii(client, auth_headers, demo_rows):
    """`ss` (Cell3 al form1 + codSSI din NOTAFD) vine de pe linie sau din nomenclator
    (Clasificatii.SS = generat Sector+Sursa). Clasificatia de test are Capitol 65.02 ->
    Sector 02, Sursa A -> SS «02A»."""
    linii = _body(client.get(f"{URL}?cod={COD}", headers=auth_headers))["linii"]
    assert all("ss" in l for l in linii)
    assert any(l["ss"] == "02A" for l in linii), "ss nu a fost rezolvat din nomenclator"


def test_generare_arrays_are_empty_without_the_flag(client, auth_headers, demo_rows):
    """Fara pentru_generare=1, sectiuneb si atasamente sunt GOALE (§2.8 — vederea nu poarta
    date pe care nu le arata), chiar daca exista randuri in baza."""
    body = _body(client.get(f"{URL}?cod={COD}", headers=auth_headers))
    assert body["sectiuneb"] == []
    assert body["atasamente"] == []


def test_sectiuneb_is_returned_with_the_flag(client, auth_headers, demo_rows):
    """Cu pentru_generare=1, sectiuneb poarta exact coloanele citite de constructorul de XML."""
    body = _body(client.get(f"{URL}?cod={COD}&pentru_generare=1", headers=auth_headers))
    assert len(body["sectiuneb"]) == 1
    sb = body["sectiuneb"][0]
    assert set(sb.keys()) == set(SECTIUNEB_KEYS)
    assert sb["cod_indicator"] == "IND-B"
    assert sb["cod_ssi"] == "01A"
    assert sb["ca_anterior"] == 1000.0
    assert sb["inf1"] == 200.0
    assert sb["cb_anterior"] == 3000.0
    assert sb["inf2"] == 400.0


def test_atasamente_are_returned_with_the_flag(client, auth_headers, demo_rows):
    """Cu pentru_generare=1, atasamente poarta base64-ul brut (DateFisier)."""
    body = _body(client.get(f"{URL}?cod={COD}&pentru_generare=1", headers=auth_headers))
    assert len(body["atasamente"]) == 1
    att = body["atasamente"][0]
    assert set(att.keys()) == set(ATASAMENT_KEYS)
    assert att["cale_fisier"] == "dovada.pdf"
    assert att["date_fisier"] == "AAECAwQ="
    assert att["prt_scr"] is False


# ---------------------------------------------------------------------------
# §3, punctul 5 — CONSTATARE, nu poarta de trecere/picare
# ---------------------------------------------------------------------------

def test_report_duplicate_cod_angajament_count(client, auth_headers, capsys):
    """Cate CodAngajament-uri au MAI MULT de un rand FX_DDF pe baza reala?

    §2.7 spune ca schema o permite (PK compus, fara constrangere unica) dar ca fiecare
    interogare Access presupune ca nu se intampla. Acest test NU pica niciodata — doar
    raporteaza numarul, ca sa se poata scrie in worklog daca §2.7 e teoretic sau real.
    """
    conn = get_db_connection(DB_NAME)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM (SELECT CodAngajament FROM FX_DDF "
            "GROUP BY CodAngajament HAVING COUNT(*) > 1) t")
        duplicate_codes = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM FX_DDF")
        total = cur.fetchone()[0]
    finally:
        conn.close()

    with capsys.disabled():
        print(f"\n[§3.5 CONSTATARE] FX_DDF pe {DB_NAME}: {total} randuri, "
              f"{duplicate_codes} CodAngajament cu >1 rand de antet.")
    assert duplicate_codes >= 0      # constatare, nu poarta
