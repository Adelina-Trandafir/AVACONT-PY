# Offline unit tests for the SNM statement import (slice 0057).
#
# These exercise the PURE half of routes/forexe/extrase.py -- date parsing, the cont_misc
# reader, and the two Access hash formulas. No database, no Flask client: the route itself
# is covered by the host-only tests at the bottom, which skip off-host.
#
# Why the hash tests matter more than they look. `Extrase_Add` in Access computed a hash,
# searched for it... and never wrote it, so the column is empty on every migrated row and
# the old system deduplicated nothing. We do write it, which means these are the FIRST
# values that column will ever hold -- and the shape has to be Access's, not a new one, or
# the day someone compares the two sets they will not line up.
import json

import pytest

try:
    from routes.forexe.extrase import (
        _cheie_suma,
        _cstr_double_vba,
        _data_doc_text,
        hash_fisier,
        hash_operatiune,
        parse_cont_misc,
        parse_data_extras,
        parse_data_yyyymmdd,
    )
except Exception as e:                              # pragma: no cover - off-host
    pytest.skip(f"host-only import (config.py unavailable): {e}",
                allow_module_level=True)

from datetime import date, datetime
from xml.etree import ElementTree


# ---------------------------------------------------------------------------
# parse_data_extras -- portul lui FX_ParseDataExtraseF
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text,asteptat", [
    ("31.12.2025", datetime(2025, 12, 31)),
    ("31/12/2025", datetime(2025, 12, 31)),
    ("31-12-2025", datetime(2025, 12, 31)),
    # Nume de luna, RO si EN, abreviat si intreg -- toate erau in MonthNameToNumber.
    ("29-Ian-2026", datetime(2026, 1, 29)),
    ("29-IANUARIE-2026", datetime(2026, 1, 29)),
    ("29-January-2026", datetime(2026, 1, 29)),
    ("  13.01.2026  ", datetime(2026, 1, 13)),
])
def test_parse_data_extras_formate_valide(text, asteptat):
    assert parse_data_extras(text) == asteptat


@pytest.mark.parametrize("text", [
    None, "", "   ",
    "20251231",          # fara separator: VBA cadea pe ramura seriala, nu pe asta
    "31.12",             # doua parti
    "31.13.2025",        # luna 13
    "32.12.2025",        # ziua 32
    "31.Necunoscuta.2025",
    "31.12.99",          # an sub 100
    "31.02.2025",        # zi valida ca numar, data inexistenta
])
def test_parse_data_extras_respinge_ce_nu_intelege(text):
    # Nu se inventeaza o data. None inseamna «extrasul asta nu se poate identifica».
    assert parse_data_extras(text) is None


# ---------------------------------------------------------------------------
# parse_data_yyyymmdd -- portul lui ParseXmlDate_YYYYMMDD
# ---------------------------------------------------------------------------
def test_parse_data_yyyymmdd():
    assert parse_data_yyyymmdd("20251230") == date(2025, 12, 30)
    assert parse_data_yyyymmdd(" 20251230 ") == date(2025, 12, 30)
    assert parse_data_yyyymmdd("2025-12-30") is None   # 10 caractere, nu 8
    assert parse_data_yyyymmdd("2025123") is None
    assert parse_data_yyyymmdd("2025AB30") is None
    assert parse_data_yyyymmdd(None) is None
    assert parse_data_yyyymmdd("20250231") is None     # 31 februarie


def test_data_doc_pleaca_spre_baza_ca_text_romanesc():
    # `FX_Extrase.DataDoc` e varchar, iar randurile scrise de Access poarta formatul
    # scurt al masinii romanesti. Confirmat in exportul tabelei: "30.12.2025".
    assert _data_doc_text(date(2025, 12, 30)) == "30.12.2025"
    assert _data_doc_text(None) is None


# ---------------------------------------------------------------------------
# parse_cont_misc -- portul lui ParseContMiscNodeToDict
# ---------------------------------------------------------------------------
def test_parse_cont_misc_ia_atribute_copii_si_atributele_copiilor():
    nod = ElementTree.fromstring(
        '<cont_misc tip="incasare">'
        '  <databan>20251230</databan>'
        '  <nrdoc cod="X">0100088028</nrdoc>'
        '  <gol></gol>'
        '  <complex><a>1</a><b>2</b></complex>'
        '</cont_misc>'
    )
    d = parse_cont_misc(nod)

    assert d["tip"] == "incasare"                 # atributul nodului
    assert d["databan"] == "20251230"             # copil cu text
    assert d["nrdoc"] == "0100088028"
    assert d["nrdoc.cod"] == "X"                  # atributul copilului, sub "copil.atribut"
    assert d["gol"] is None                       # copil gol -> None (Null in VBA)
    assert "<a>1</a>" in d["complex"]             # copil cu descendenti -> XML brut


# ---------------------------------------------------------------------------
# Hash-uri
# ---------------------------------------------------------------------------
def test_hash_fisier_e_stabil_si_depinde_de_ambele_campuri():
    a = hash_fisier("x.pdf", "31.12.2025 13:27:00")
    assert a == hash_fisier("x.pdf", "31.12.2025 13:27:00")   # determinist
    assert len(a) == 64 and a == a.upper()                    # hex majuscule, ca BytesToHex
    assert a != hash_fisier("y.pdf", "31.12.2025 13:27:00")
    assert a != hash_fisier("x.pdf", "31.12.2025 13:28:00")


def test_cstr_double_taie_la_15_cifre_ca_vba():
    # ASTA e motivul functiei. 100 * round(0.07, 2) da 7.000000000000001 in virgula
    # mobila; VBA tipareste "7", Python `str()` tipareste toate zecimalele. Fara
    # taietura, hash-ul ar depinde de limbajul care l-a calculat.
    assert _cstr_double_vba(100 * round(0.07, 2)) == "7"
    assert _cstr_double_vba(76.0) == "76"
    assert _cstr_double_vba(1.5) == "1,5"          # separatorul masinii romanesti


def test_cheie_suma_reproduce_int_si_zecimalele():
    assert _cheie_suma(792.0) == "792_0"
    assert _cheie_suma(651104.76) == "651104_76"
    assert _cheie_suma(0.07) == "0_7"
    # `Int` in VBA e floor, nu trunchiere: -5,25 da -6 si 75 de sutimi, nu -5 si -25.
    assert _cheie_suma(-5.25) == "-6_75"


def test_hash_operatiune_pastreaza_cheia_iban_goala():
    # In Access, cheia "IBAN" se alimenta din `vIbanPlat`, o variabila care nu era
    # NICIODATA atribuita in acea procedura. A intrat mereu goala in hash. Se reproduce
    # ca atare: un hash «reparat» aici ar fi un sir nou, care nu s-ar mai potrivi cu
    # nimic. Testul apara reproducerea, nu greseala.
    h = hash_operatiune("30.12.2025", "0100088028", "23308833", 0.0, 792.0)
    assert h == hash_operatiune("30.12.2025", "0100088028", "23308833", 0.0, 792.0)
    assert len(h) == 64
    # Sumele intra prin _cheie_suma, deci 792 si 792,00 sunt acelasi rand.
    assert h == hash_operatiune("30.12.2025", "0100088028", "23308833", 0.00, 792.00)
    # ...iar o suma diferita e alt rand.
    assert h != hash_operatiune("30.12.2025", "0100088028", "23308833", 0.0, 793.0)


# ---------------------------------------------------------------------------
# Rutele -- host-only (cer config.py + baza)
# ---------------------------------------------------------------------------
try:
    from main import app
    from routes.auth.session_store import STORE
    _HOST = True
except Exception:                                   # pragma: no cover - off-host
    _HOST = False

pytestmark_host = pytest.mark.skipif(not _HOST, reason="host-only (main/app indisponibil)")

DB_NAME = "000_DEMO"
URL_IMPORT = "/api/forexe/extrase/import"
URL_ULTIMA = "/api/forexe/extrase/ultima"


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.fixture
def auth_headers():
    token, _ = STORE.create(username="pytest-op", password="unused",
                            id_unitate=0, db_name=DB_NAME,
                            ctx={"DbName": DB_NAME}, pcname="PYTEST")
    yield {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    STORE.revoke(token)


@pytestmark_host
def test_importul_cere_token(client):
    r = client.post(URL_IMPORT, headers={"Content-Type": "application/json"},
                    data=json.dumps({"extrase": []}))
    assert r.status_code == 401


@pytestmark_host
def test_ultima_cere_token(client):
    assert client.get(URL_ULTIMA).status_code == 401


@pytestmark_host
def test_importul_fara_extrase_e_400(client, auth_headers):
    r = client.post(URL_IMPORT, headers=auth_headers, data=json.dumps({}))
    assert r.status_code == 400


@pytestmark_host
def test_importul_cere_lista(client, auth_headers):
    r = client.post(URL_IMPORT, headers=auth_headers,
                    data=json.dumps({"extrase": "nu e listă"}))
    assert r.status_code == 400
