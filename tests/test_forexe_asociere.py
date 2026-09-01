# Tests for the always-available R <-> H association editor -- slice 0048-04.
#
# OFFLINE. The lock rule, the shape of `comenzi`, the lock enforcement and the in-memory
# chain projection are all pure: they take plain dictionaries and no database. The two
# functions that need a cursor get the same scripted fake the ingest tests use.
#
# What is NOT covered here and cannot be: whether `_BLOCAJE_SQL` returns what the comment
# above it claims on a real MariaDB. `FX_ORD.IDRR` / `IDRH` are 0 on every row of the
# Access export, so the ordonantare half of the rule has never been seen finding anything.
from datetime import datetime

import pytest

try:
    import routes.forexe.asociere as A
    import routes.forexe.prelucrare_asociere as P
except Exception as e:                              # pragma: no cover - broken install
    pytest.skip(f"imports unavailable: {e}", allow_module_level=True)

DecizieInvalida = P.DecizieInvalida
InstantaneuBlocat = A.InstantaneuBlocat
COD = "AAB37CNBK95"


def dt(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")


def inst(idrh, data_h, total, idrr=0, indicatori=("AAB",), stergere=False,
         ignorat=False, blocat=False):
    return {
        "idrh": idrh,
        "rand_istoric": idrh,
        "idrr": idrr,
        "idh": 0,
        "data_h": dt(data_h),
        "descriere": "Plata fact.",
        "total": float(total),
        "tip_receptie": "",
        "stergere": stergere,
        "ignorat": ignorat,
        "blocat": blocat,
        "motive": ["blocat"] if blocat else [],
        "linii": [{"cod_indicator": c, "cod_ai": f"{COD}-{c}", "cod_ssi": "",
                   "id_clsf": 1, "valoare": float(total)} for c in indicatori],
    }


def rec(idrr, data_r, suma, indicatori=("AAB",)):
    return {
        "idrr": idrr, "nr_crt": idrr, "data_r": dt(data_r),
        "suma_antet": float(suma), "descriere": "Plata fact.",
        "sters": False, "reconstituit": False,
        "rhr": [{"cod_indicator": c, "cod_ai": f"{COD}-{c}", "cod_ssi": "",
                 "credit_bugetar": 10502.19, "valoare": float(suma),
                 "valoare_n": 0.0} for c in indicatori],
    }


def cmd(idrh, actiune, idrr=None, eticheta=None):
    c = {"idrh": idrh, "actiune": actiune}
    if idrr is not None:
        c["idrr"] = idrr
    if eticheta is not None:
        c["receptie_noua"] = eticheta
    return c


# ===========================================================================
# motive_blocare -- the rule itself, as a pure function
# ===========================================================================
def blocaj(ord_h=0, ord_h_nr=None, ord_r=0, ord_r_data=None, plati=0, plati_data=None):
    return {"IDRH": 1, "ord_h": ord_h, "ord_h_nr": ord_h_nr, "ord_r": ord_r,
            "ord_r_data": ord_r_data, "plati": plati, "plati_data": plati_data}


def test_nimic_nu_blocheaza_o_legatura_curata():
    assert A.motive_blocare(blocaj()) == []


def test_ordonantare_pe_instantaneu_numeste_numarul():
    motive = A.motive_blocare(blocaj(ord_h=2, ord_h_nr="14, 15"))
    assert len(motive) == 1
    assert "14, 15" in motive[0]


def test_ordonantare_pe_instantaneu_fara_numar_spune_totusi_ceva():
    motive = A.motive_blocare(blocaj(ord_h=1, ord_h_nr=None))
    assert motive == ["Pe acest instantaneu s-a construit o ordonanțare."]


def test_ordonantare_pe_receptie_poarta_data():
    motive = A.motive_blocare(blocaj(ord_r=1, ord_r_data=dt("2026-04-07 00:00:00")))
    assert "07.04.2026" in motive[0]


def test_ordonantare_fara_data_blocheaza_si_o_spune():
    """O ordonanțare fara data nu poate fi dovedita anterioara -> ramura conservatoare."""
    motive = A.motive_blocare(blocaj(ord_r=1, ord_r_data=None))
    assert len(motive) == 1
    assert "fără dată" in motive[0]


def test_platile_de_la_datah_incolo_blocheaza():
    motive = A.motive_blocare(blocaj(plati=3, plati_data=dt("2026-02-28 08:24:14")))
    assert len(motive) == 1
    assert "3 plăți" in motive[0] and "28.02.2026" in motive[0]


def test_motivele_vin_de_la_specific_la_general():
    """Operatorul citeste primul mesaj; el trebuie sa fie cel care spune cel mai mult."""
    motive = A.motive_blocare(blocaj(
        ord_h=1, ord_h_nr="14",
        ord_r=1, ord_r_data=dt("2026-04-07 00:00:00"),
        plati=5, plati_data=dt("2026-01-31 08:01:01")))
    assert len(motive) == 3
    assert "ordonanțarea nr. 14" in motive[0]
    assert "Recepția are o ordonanțare" in motive[1]
    assert "plăți" in motive[2]


# ===========================================================================
# Shape of `comenzi`
# ===========================================================================
def test_comenzile_trebuie_sa_fie_lista():
    with pytest.raises(DecizieInvalida):
        A.normalizeaza_comenzi({"idrh": 1})


def test_lista_goala_e_refuzata():
    """O salvare fara nicio comanda nu e o salvare partiala, e o greseala de client."""
    with pytest.raises(DecizieInvalida):
        A.normalizeaza_comenzi([])


def test_acelasi_instantaneu_de_doua_ori_e_refuzat():
    with pytest.raises(DecizieInvalida, match="de două ori"):
        A.normalizeaza_comenzi([cmd(10, "desprins"), cmd(10, "ignorat")])


def test_actiune_necunoscuta_e_refuzata():
    with pytest.raises(DecizieInvalida, match="nu este cunoscută"):
        A.normalizeaza_comenzi([cmd(10, "muta")])


def test_desprins_nu_poate_purta_receptie():
    with pytest.raises(DecizieInvalida, match="nu poate purta o recepție"):
        A.normalizeaza_comenzi([cmd(10, "desprins", idrr=5)])


def test_asociat_cere_exact_o_tinta():
    with pytest.raises(DecizieInvalida, match="exact una"):
        A.normalizeaza_comenzi([cmd(10, "asociat")])
    with pytest.raises(DecizieInvalida, match="exact una"):
        A.normalizeaza_comenzi([cmd(10, "asociat", idrr=5, eticheta="A")])


def test_idrr_zero_inseamna_niciuna():
    """Clientul VB trimite 0 pentru «gol» (Integer nu e nullable); 0 devine None."""
    with pytest.raises(DecizieInvalida, match="exact una"):
        A.normalizeaza_comenzi([cmd(10, "asociat", idrr=0)])


def test_reconstituirea_nu_accepta_idrr():
    with pytest.raises(DecizieInvalida, match="încă nu există"):
        A.normalizeaza_comenzi([cmd(10, "reconstituire", idrr=5, eticheta="A")])


def test_comanda_valida_capata_aliasul_rand_istoric():
    """Aliasul e ce ne lasa sa refolosim NESCHIMBATE functiile din ingestie."""
    out = A.normalizeaza_comenzi([cmd(10, "asociat", idrr=5)])
    assert out[0]["idrh"] == 10 and out[0]["rand_istoric"] == 10
    assert out[0]["idrr"] == 5 and out[0]["receptie_noua"] is None


# ===========================================================================
# Lock enforcement
# ===========================================================================
def test_o_comanda_pe_o_legatura_blocata_ridica():
    instantanee = [inst(10, "2026-01-19 10:00:00", 100, idrr=5)]
    with pytest.raises(InstantaneuBlocat, match="19.01.2026"):
        A.verifica_blocajele([{"idrh": 10, "actiune": "desprins"}],
                             instantanee, {10: ["are plăți"]})


def test_un_instantaneu_neasezat_nu_poate_fi_blocat():
    """
    Asimetria deliberata: blocajul pazeste EDITAREA unei legaturi, nu ATASAREA uneia noi.
    Access facea la fel -- verificarea traia doar in btnDel_Click. Fara asta, formularul
    de ingestie s-ar bloca in prima zi, fiindca fiecare instantaneu istoric are plati
    dupa el.
    """
    instantanee = [inst(10, "2026-01-19 10:00:00", 100, idrr=0)]
    A.verifica_blocajele([{"idrh": 10, "actiune": "asociat"}],
                         instantanee, {10: ["are plăți"]})


def test_o_comanda_pe_un_instantaneu_strain_ridica():
    with pytest.raises(DecizieInvalida, match="nu există"):
        A.verifica_blocajele([{"idrh": 99, "actiune": "desprins"}], [], {})


def test_mesajul_de_blocare_numeste_toate_instantaneele_lovite():
    instantanee = [inst(10, "2026-01-19 10:00:00", 100, idrr=5),
                   inst(11, "2026-02-16 10:00:00", 200, idrr=5)]
    with pytest.raises(InstantaneuBlocat) as e:
        A.verifica_blocajele(
            [{"idrh": 10, "actiune": "desprins"}, {"idrh": 11, "actiune": "desprins"}],
            instantanee, {10: ["a"], 11: ["b"]})
    assert "19.01.2026" in str(e.value) and "16.02.2026" in str(e.value)


# ===========================================================================
# In-memory chain projection
# ===========================================================================
def _tinta_directa(c):
    return c["idrr"]


def test_desprinderea_scoate_instantaneul_din_lant():
    instantanee = [inst(10, "2026-01-19 10:00:00", 100, idrr=5),
                   inst(11, "2026-02-16 10:00:00", 200, idrr=5)]
    lanturi = A._lanturi_rezultate(
        [{"idrh": 11, "actiune": "desprins", "idrr": None}],
        instantanee, _tinta_directa)
    assert set(lanturi) == {5}
    assert [x["idrh"] for x in lanturi[5]] == [10]


def test_mutarea_apare_in_amandoua_lanturile():
    instantanee = [inst(10, "2026-01-19 10:00:00", 100, idrr=5),
                   inst(11, "2026-02-16 10:00:00", 200, idrr=6)]
    lanturi = A._lanturi_rezultate(
        [{"idrh": 11, "actiune": "asociat", "idrr": 5}],
        instantanee, _tinta_directa)
    assert set(lanturi) == {5, 6}
    assert [x["idrh"] for x in lanturi[5]] == [10, 11]
    assert lanturi[6] == []


def test_receptiile_neatinse_nu_se_valideaza():
    """
    O incalcare veche, deja in baza, nu are voie sa blocheze o corectie fara legatura cu
    ea. De-asta proiectia intoarce DOAR lanturile schimbate.
    """
    instantanee = [inst(10, "2026-01-19 10:00:00", 100, idrr=5),
                   inst(20, "2026-03-01 10:00:00", 999, idrr=7)]
    lanturi = A._lanturi_rezultate(
        [{"idrh": 10, "actiune": "desprins", "idrr": None}],
        instantanee, _tinta_directa)
    assert set(lanturi) == {5}


def test_ignoratul_scoate_la_fel_ca_desprinsul():
    instantanee = [inst(10, "2026-01-19 10:00:00", 100, idrr=5)]
    lanturi = A._lanturi_rezultate(
        [{"idrh": 10, "actiune": "ignorat", "idrr": None}],
        instantanee, _tinta_directa)
    assert lanturi[5] == []


# ===========================================================================
# F15 demoted to a warning -- and only F15
# ===========================================================================
def test_f15_avertizeaza_in_loc_sa_ridice():
    lanturi = {5: [inst(10, "2026-01-19 10:00:00", 100, idrr=5)]}
    receptii = {5: rec(5, "2026-01-01 08:00:00", 250)}
    avertismente = []
    P.valideaza_plasarile(lanturi, receptii, f15_ca_avertisment=True,
                          avertismente=avertismente)
    assert len(avertismente) == 1
    assert "nu se închide" in avertismente[0]


def test_f15_ramane_veto_in_ingestie():
    """Implicitul nu se schimba: calea de ingestie ridica, exact ca inainte."""
    lanturi = {5: [inst(10, "2026-01-19 10:00:00", 100, idrr=5)]}
    receptii = {5: rec(5, "2026-01-01 08:00:00", 250)}
    with pytest.raises(DecizieInvalida, match="nu se închide"):
        P.valideaza_plasarile(lanturi, receptii)


def test_f13_nu_mai_e_veto_ci_semn():
    """
    RETRAS pe 31.08.2026. `DataR` nu spune cand a aparut receptia -- e un camp tastat pe
    site, schimbabil dupa aceea, iar tabelul nu are nicio coloana cu momentul crearii
    (F29). Comparatia a ramas ca semn, pe amandoua caile.
    """
    lanturi = {5: [inst(10, "2026-01-19 10:00:00", 100, idrr=5)]}
    receptii = {5: rec(5, "2026-03-01 08:00:00", 100)}
    avertismente = []
    P.valideaza_plasarile(lanturi, receptii, f15_ca_avertisment=True,
                          avertismente=avertismente)          # nu ridica
    assert any("mai vechi decât data recepției" in a for a in avertismente)


def test_f14_ramane_veto_si_in_editor():
    lanturi = {5: [inst(10, "2026-01-19 10:00:00", 100, idrr=5, indicatori=("AA9",))]}
    receptii = {5: rec(5, "2026-01-01 08:00:00", 100, indicatori=("AAB",))}
    with pytest.raises(DecizieInvalida, match="AA9"):
        P.valideaza_plasarile(lanturi, receptii, f15_ca_avertisment=True,
                              avertismente=[])


def test_f16_ramane_veto_si_in_editor():
    lanturi = {5: [inst(10, "2026-01-19 10:00:00", 100, idrr=5,
                        indicatori=("AAB", "AA2")),
                   inst(11, "2026-02-16 10:00:00", 100, idrr=5,
                        indicatori=("AAB",))]}
    receptii = {5: rec(5, "2026-01-01 08:00:00", 100, indicatori=("AAB", "AA2"))}
    with pytest.raises(DecizieInvalida, match="pierde indicatorii"):
        P.valideaza_plasarile(lanturi, receptii, f15_ca_avertisment=True,
                              avertismente=[])


def test_avertizarea_fara_lista_e_o_greseala_de_programare():
    """Fara lista, semnalarea s-ar pierde in tacere -- exact ce nu vrem de la F15."""
    with pytest.raises(ValueError, match="avertismente"):
        P.valideaza_plasarile({}, {}, f15_ca_avertisment=True)


# ===========================================================================
# Chain end skipped for a deletion, in the editor too
# ===========================================================================
def test_lantul_terminat_in_stergere_nu_se_masoara():
    lanturi = {5: [inst(10, "2026-01-19 10:00:00", 100, idrr=5),
                   inst(11, "2026-02-16 10:00:00", 100, idrr=5, stergere=True,
                        indicatori=())]}
    receptii = {5: rec(5, "2026-01-01 08:00:00", 999)}
    avertismente = []
    P.valideaza_plasarile(lanturi, receptii, f15_ca_avertisment=True,
                          avertismente=avertismente)
    assert avertismente == []


# ===========================================================================
# Rutele, cu o baza falsa -- si cu cursorul luat in serios
# ===========================================================================
# DE CE EXISTA SECTIUNEA ASTA: toate testele de mai sus hranesc functiile cu dictionare
# gata facute, deci nu au cum sa vada CUM cere ruta randurile de la baza. Ruta le-a cerut
# la inceput cu un cursor obisnuit -- care in mysql.connector intoarce TUPLURI -- iar
# fiecare r["IDRH"] de dupa a murit cu «tuple indices must be integers or slices, not
# str», in fata operatorului, la prima deschidere a formularului.
#
# De-asta cursorul fals de aici respecta steagul `dictionary` exact ca cel adevarat:
# dictionare cand e cerut, tupluri cand nu. Daca ruta se intoarce vreodata la cursorul
# obisnuit, testele astea cad cu aceeasi eroare, aici, in loc de pe ecranul operatorului.
import json as _json

from flask import Flask as _Flask

try:
    from routes.forexe import forexe_bp as _forexe_bp
    from routes.auth.session_store import STORE as _STORE
except Exception as e:                              # pragma: no cover - broken install
    pytest.skip(f"blueprint imports unavailable: {e}", allow_module_level=True)

_app = _Flask(__name__)
_app.register_blueprint(_forexe_bp)
DB_NAME = "000_DEMO"
URL = "/api/forexe/asociere"

# Amprenta unui angajament cu o recepție si un instantaneu. Valorile nu conteaza in sine;
# conteaza ca sunt STABILE, fiindca POST-ul compara.
_AMPRENTA = {"ic": 1, "im": 7, "id_": "2026-02-10", "rc": 1, "rm": 3,
             "hc": 1, "hm": 5, "hn": 0}


class FakeCursor:
    """Cursor mysql.connector in miniatura: `dictionary` decide forma randurilor."""

    def __init__(self, conn, dictionary):
        self.conn = conn
        self.dictionary = dictionary
        self._rows = []
        self.rowcount = 0

    def execute(self, sql, params=None):
        self.conn.executed.append((sql, params))
        self._rows = self.conn.rows_for(sql)

    def _shape(self, rand):
        # Cursorul adevarat intoarce tupluri cand nu i s-a cerut altceva -- si tocmai
        # forma asta face r["coloana"] sa ridice. Dictionarele pastreaza ordinea
        # cheilor, deci tuplul iese in ordinea coloanelor, ca la baza.
        return dict(rand) if self.dictionary else tuple(rand.values())

    def fetchall(self):
        return [self._shape(r) for r in self._rows]

    def fetchone(self):
        return self._shape(self._rows[0]) if self._rows else None

    def close(self):
        pass


class FakeConnection:
    def __init__(self, **tabele):
        self.tabele = tabele
        self.executed = []
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def rows_for(self, sql):
        if "(SELECT COUNT(*) FROM FX_Istoric" in sql:
            return [dict(_AMPRENTA)]
        if sql.startswith("SELECT IDRR, CodIndicator"):
            return self.tabele.get("rhr", [])
        if sql.startswith("SELECT IDRR, NRCRT"):
            return self.tabele.get("receptii", [])
        if sql.startswith("SELECT H.IDRH"):
            return self.tabele.get("blocaje", [])
        if sql.startswith("SELECT IDRH, CodIndicator"):
            return self.tabele.get("linii", [])
        if sql.startswith("SELECT IDRH, IDRR"):
            return self.tabele.get("instantanee", [])
        if sql.startswith("SELECT Data_plata"):
            return self.tabele.get("plati", [])
        raise AssertionError("unexpected SQL: " + sql)       # pragma: no cover - guard

    def cursor(self, dictionary=False):
        return FakeCursor(self, dictionary)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def baza_cu_un_lant():
    """O recepție, un instantaneu asezat pe ea, o plata. Nimic blocat."""
    return FakeConnection(
        receptii=[{"IDRR": 3, "NRCRT": 1, "DataR": dt("2026-02-10 00:00:00"),
                   "SumaAntet": 1000.0, "Descriere": "Plata fact.", "Sters": 0,
                   "Reconstituit": 0, "ReconstituitNesigur": 0}],
        rhr=[{"IDRR": 3, "CodIndicator": "AAB", "CodAI": COD + "-AAB", "CodSSI": "",
              "CreditBugetar": 10502.19, "Valoare": 1000.0, "ValoareN": 0.0}],
        instantanee=[{"IDRH": 5, "IDRR": 3, "IDH": 9, "DataH": dt("2026-02-11 00:00:00"),
                      "Total": 1000.0, "Descriere": "Plata fact.", "TipReceptie": "",
                      "Sters": 0, "EsteStergere": 0}],
        linii=[{"IDRH": 5, "CodIndicator": "AAB", "CodAI": COD + "-AAB", "CodSSI": "",
                "IdClsf": 1, "Valoare": 1000.0}],
        plati=[{"Data_plata": dt("2026-03-01 00:00:00"), "Suma": 400.0, "NrOP": "112"}],
    )


@pytest.fixture
def client():
    _app.config["TESTING"] = True
    with _app.test_client() as c:
        yield c


@pytest.fixture
def auth_headers():
    token, _ = _STORE.create(username="pytest-op", password="unused",
                             id_unitate=0, db_name=DB_NAME,
                             ctx={"DbName": DB_NAME}, pcname="PYTEST")
    yield {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
    _STORE.revoke(token)


@pytest.fixture
def conn(monkeypatch):
    c = baza_cu_un_lant()
    monkeypatch.setattr(A, "get_kbot_connection", lambda db=None: c)
    return c


def test_get_citeste_tabloul_cu_cursor_pe_dictionar(client, auth_headers, conn):
    r = client.get(URL + "?cod=" + COD, headers=auth_headers)
    assert r.status_code == 200, r.get_data(as_text=True)
    date = r.get_json()
    assert date["cod"] == COD
    assert [x["idrr"] for x in date["receptii"]] == [3]
    assert [x["idrh"] for x in date["instantanee"]] == [5]
    assert date["instantanee"][0]["idrr"] == 3
    assert date["instantanee"][0]["linii"][0]["cod_indicator"] == "AAB"
    assert date["instantanee"][0]["blocat"] is False
    assert date["plati"][0]["nr_op"] == "112"
    assert date["amprenta"]
    assert conn.closed


def test_get_cere_explicit_cursor_pe_dictionar(client, auth_headers, conn):
    """Paza directa: un cursor pe tupluri e chiar defectul care a ajuns la operator."""
    cerute = []
    original = conn.cursor

    def spion(dictionary=False):
        cerute.append(dictionary)
        return original(dictionary=dictionary)

    conn.cursor = spion
    client.get(URL + "?cod=" + COD, headers=auth_headers)
    assert cerute == [True]


def test_post_cere_si_el_cursor_pe_dictionar(client, auth_headers, conn):
    """Amprenta veche => 409, dar numai dupa ce randul amprentei a fost citit pe nume."""
    corp = _json.dumps({"cod": COD, "amprenta": "amprenta-veche",
                        "comenzi": [cmd(5, A.ACTIUNE_DESPRINS)]})
    r = client.post(URL, data=corp, headers=auth_headers)
    assert r.status_code == 409
    assert r.get_json()["reason"] == P.REASON_STARE_MODIFICATA
    assert conn.rolled_back


def test_un_instantaneu_blocat_ajunge_la_client_cu_motive(client, auth_headers,
                                                          monkeypatch):
    c = baza_cu_un_lant()
    c.tabele["blocaje"] = [{"IDRH": 5, "ord_h": 0, "ord_h_nr": None, "ord_r": 0,
                            "ord_r_data": None, "plati": 2,
                            "plati_data": dt("2026-03-01 00:00:00")}]
    monkeypatch.setattr(A, "get_kbot_connection", lambda db=None: c)

    date = client.get(URL + "?cod=" + COD, headers=auth_headers).get_json()
    inst5 = date["instantanee"][0]
    assert inst5["blocat"] is True
    assert "01.03.2026" in inst5["motive"][0]


def test_post_pe_o_legatura_blocata_da_409(client, auth_headers, monkeypatch):
    c = baza_cu_un_lant()
    c.tabele["blocaje"] = [{"IDRH": 5, "ord_h": 1, "ord_h_nr": "77", "ord_r": 0,
                            "ord_r_data": None, "plati": 0, "plati_data": None}]
    monkeypatch.setattr(A, "get_kbot_connection", lambda db=None: c)

    # Amprenta buna, ca sa treaca de paza de concurenta si sa cada exact pe blocaj.
    amp = P.amprenta(c.cursor(dictionary=True), COD)
    c.executed.clear()

    corp = _json.dumps({"cod": COD, "amprenta": amp,
                        "comenzi": [cmd(5, A.ACTIUNE_DESPRINS)]})
    r = client.post(URL, data=corp, headers=auth_headers)
    assert r.status_code == 409
    date = r.get_json()
    assert date["reason"] == A.REASON_INSTANTANEU_BLOCAT
    assert "77" in date["error"]
    assert c.rolled_back
    assert not c.committed
