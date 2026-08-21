# Offline unit tests for the slice 0044 row selection (routes/migrare/routing.py).
#   python -m pytest tests/test_migrare_routing.py
#
# No config.py, no MariaDB, no Access file: the key sets are built by hand, which
# is the whole point of keeping the selection rule separate from the reading.
#
# The rules under test are the ones that lose or misplace data when they are wrong:
#   * a row of ANOTHER unit in the same file is skipped, silently and on purpose;
#   * a key that exists nowhere in the file is rejected WITH a reason, never dropped;
#   * a row with two parents that disagree is a hard error, not a fallback.

import pytest

from routes.migrare import routing, tables


def plan(db_name="005_CEVM", single_unit=False):
    """Unitatea 75 e a bazei alese; 48 e a altei unitati din acelasi fisier."""
    sets = dict((name, routing.KeySet()) for name in
                routing.FAMILIES)
    sets["unit"].add(75, True)
    sets["unit"].add(48, False)
    sets["commitment"].add("aab-002", True)
    sets["commitment"].add("aab-001", False)
    sets["reservation"].add(11, True)
    sets["reservation"].add(10, False)
    sets["receipt_r"].add(21, True)
    sets["receipt_r"].add(20, False)
    sets["receipt_h"].add(31, True)
    sets["receipt_h"].add(30, False)
    sets["statement"].add(41, True)
    sets["statement"].add(40, False)
    sets["statement_h"].add(51, True)
    sets["statement_h"].add(50, False)
    sets["ddf"].add(61, True)
    sets["ddf"].add(60, False)
    sets["rev"].add(71, True)
    sets["rev"].add(70, False)
    sets["ord"].add(81, True)
    sets["ord"].add(80, False)
    return routing.UnitPlan(db_name, sets, {75}, {48, 75}, single_unit)


def selector(table_name, **kwargs):
    return plan(**kwargs).selector_for(tables.by_name(table_name))


# --- DC propriu, apoi IdUnitate ----------------------------------------------

def test_dc_propriu_bate_idunitate():
    keep, reject = selector("FX_Angajamente").keep(
        {"CodAngajament": "AAB-001", "DC": "005_CEVM", "IdUnitate": 48})
    assert (keep, reject) == (True, None)


def test_dc_propriu_al_altei_unitati_e_sarit_fara_motiv():
    # Nu e o problema: fisierul poarta mai multe unitati, se scrie doar una.
    keep, reject = selector("FX_Angajamente").keep(
        {"CodAngajament": "AAB-001", "DC": "045_CTER", "IdUnitate": 75})
    assert (keep, reject) == (False, None)


def test_fara_dc_propriu_se_cade_pe_idunitate():
    keep, reject = selector("FX_Angajamente").keep(
        {"CodAngajament": "AAB-002", "IdUnitate": 75})
    assert (keep, reject) == (True, None)


def test_idunitate_a_altei_unitati_e_sarit():
    keep, reject = selector("FX_Indicatori").keep({"CodAI": "X", "IdUnitate": 48})
    assert (keep, reject) == (False, None)


def test_idunitate_lipsa_intr_un_fisier_cu_mai_multe_unitati_e_respins():
    keep, reject = selector("FX_Indicatori").keep({"CodAI": "X"})
    assert keep is False
    assert "IdUnitate lipsește" in reject and "48" in reject


def test_idunitate_lipsa_intr_un_fisier_cu_o_unitate_e_al_nostru():
    keep, reject = selector("FX_Indicatori", single_unit=True).keep({"CodAI": "X"})
    assert (keep, reject) == (True, None)


# --- prin angajament ----------------------------------------------------------

def test_copilul_urmeaza_angajamentul_indiferent_de_litere():
    # Codurile din Access vin cu majuscule amestecate; multimea e pe litere mici.
    keep, reject = selector("FX_Istoric").keep({"ID": 1, "CodAngajament": "AaB-002"})
    assert (keep, reject) == (True, None)


def test_copilul_altei_unitati_e_sarit_nu_respins():
    keep, reject = selector("FX_Istoric").keep({"ID": 2, "CodAngajament": "AAB-001"})
    assert (keep, reject) == (False, None)


def test_angajamentul_inexistent_e_respins_nu_pierdut():
    keep, reject = selector("FX_Istoric").keep({"ID": 3, "CodAngajament": "ZZZ-999"})
    assert keep is False
    assert "ZZZ-999" in reject and "FX_Angajamente" in reject


def test_codangajament_lipsa_e_respins():
    keep, reject = selector("FX_Istoric").keep({"ID": 4})
    assert keep is False
    assert "CodAngajament" in reject


# --- prin rezervare -----------------------------------------------------------

def test_imaginea_urmeaza_rezervarea():
    keep, reject = selector("FX_Rezervarii_IMG").keep({"IDRZC": 1, "IDRZ": 11})
    assert (keep, reject) == (True, None)


def test_imaginea_rezervarii_altei_unitati_e_sarita():
    keep, reject = selector("FX_Rezervarii_IMG").keep({"IDRZC": 2, "IDRZ": 10})
    assert (keep, reject) == (False, None)


def test_rezervarea_inexistenta_e_respinsa():
    keep, reject = selector("FX_Rezervarii_IMG").keep({"IDRZC": 3, "IDRZ": 77})
    assert keep is False
    assert "FX_Rezervari" in reject


# --- doi parinti --------------------------------------------------------------

def test_amandoi_parintii_ai_unitatii_alese():
    keep, reject = selector("FX_Receptii_IMG").keep({"IDRDC": 1, "IDRR": 21, "IDRH": 31})
    assert (keep, reject) == (True, None)


def test_al_doilea_parinte_decide_cand_primul_lipseste():
    keep, reject = selector("FX_Receptii_IMG").keep({"IDRDC": 2, "IDRH": 31})
    assert (keep, reject) == (True, None)


def test_parinti_care_nu_sunt_de_acord_opresc_migrarea():
    # Nu retragere si nici ghiceala: legaturile din Access se contrazic, iar o
    # alegere ar muta randul in baza gresita.
    with pytest.raises(routing.RoutingError) as info:
        selector("FX_Receptii_IMG").keep({"IDRDC": 3, "IDRR": 21, "IDRH": 30})
    assert "nu sunt de acord" in str(info.value)


def test_amandoi_parintii_ai_altei_unitati_sunt_sariti():
    keep, reject = selector("FX_Receptii_IMG").keep({"IDRDC": 4, "IDRR": 20, "IDRH": 30})
    assert (keep, reject) == (False, None)


def test_niciun_parinte_din_fisier_e_respins():
    keep, reject = selector("FX_Receptii_IMG").keep({"IDRDC": 5, "IDRR": 88, "IDRH": 99})
    assert keep is False
    assert "IDRR" in reject and "IDRH" in reject


def test_ordinea_parintilor_difera_intre_cele_doua_tabele():
    assert tables.by_name("FX_Receptii_IMG").key_column == "IDRR"
    assert tables.by_name("FX_Receptii_Plati").key_column == "IDRH"


# --- extrasele ----------------------------------------------------------------

def test_liniile_extrasului_urmeaza_antetul():
    keep, reject = selector("FX_Extrase_F").keep({"IDEXF": 41})
    assert (keep, reject) == (True, None)


def test_extrasul_altei_unitati_e_sarit():
    keep, reject = selector("FX_Extrase_F").keep({"IDEXF": 40})
    assert (keep, reject) == (False, None)


def test_extrasul_fara_antet_e_respins():
    keep, reject = selector("FX_Extrase_F").keep({"IDEXF": 42})
    assert keep is False
    assert "FX_Extrase_H" in reject


# --- liniile de extras (FX_Extrase): IdUnitate propriu, apoi antetul ----------

def test_linia_de_extras_cu_idunitate_propriu_il_foloseste():
    keep, reject = selector("FX_Extrase").keep({"IDFXE": 1, "IdUnitate": 75, "IDFXH": 50})
    assert (keep, reject) == (True, None)


def test_linia_de_extras_fara_idunitate_urmeaza_antetul():
    # Cazul care a respins pe nedrept 3110 randuri: IdUnitate e NULL pe linie,
    # dar IDFXH duce la un antet al unitatii alese.
    keep, reject = selector("FX_Extrase").keep({"IDFXE": 2, "IDFXH": 51})
    assert (keep, reject) == (True, None)


def test_linia_de_extras_a_altei_unitati_e_sarita_prin_antet():
    keep, reject = selector("FX_Extrase").keep({"IDFXE": 3, "IDFXH": 50})
    assert (keep, reject) == (False, None)


def test_linia_de_extras_cu_antet_inexistent_e_respinsa():
    keep, reject = selector("FX_Extrase").keep({"IDFXE": 4, "IDFXH": 99})
    assert keep is False
    assert "FX_Extrase_H" in reject


def test_linia_de_extras_fara_nimic_intr_un_fisier_multiunitate_e_respinsa():
    keep, reject = selector("FX_Extrase").keep({"IDFXE": 5})
    assert keep is False
    assert "IdUnitate" in reject and "IDFXH" in reject


def test_linia_de_extras_fara_nimic_intr_un_fisier_cu_o_unitate_e_a_noastra():
    keep, reject = selector("FX_Extrase", single_unit=True).keep({"IDFXE": 6})
    assert (keep, reject) == (True, None)


# --- nimic din cale.accdb -----------------------------------------------------

def test_nu_mai_exista_nicio_urma_de_cale_accdb():
    # Hartile [Cai] si planul care le cerea au disparut cu totul: unitatea unui
    # rand se afla din fisierul FOREXE insusi.
    assert not hasattr(routing, "build_maps")
    assert not hasattr(routing, "resolve_plan")
    assert not hasattr(routing, "RoutingMaps")
    assert not hasattr(routing, "RowRouter")

    from routes.migrare import storage
    assert not hasattr(storage, "cai_file_name")


# --- setul de tabele ----------------------------------------------------------

def test_setul_are_ordinea_ceruta_de_operator():
    nume = [t.name for t in tables.ALL]
    assert len(nume) == 27
    # Ordinea numerotata de operator (2026-08-21): cele 23, apoi cele patru
    # ramase in afara numerotarii, cu parintii mereu inaintea copiilor.
    assert nume[:23] == [
        "FX_Angajamente", "FX_Indicatori", "FX_Istoric", "FX_Rezervari",
        "FX_Receptii_R", "FX_Receptii_RHR", "FX_Receptii_H", "FX_Receptii",
        "FX_Plati", "FX_Extrase_F", "FX_Extrase_H", "FX_Extrase",
        "FX_DDF", "FX_DDF_REV", "FX_DDF_REV_SA", "FX_DDF_REV_SB",
        "FX_DDF_REV_ATT", "FX_DDF_REV_PRT",
        "FX_ORD", "FX_ORD_PART", "FX_ORD_TBL", "FX_ORD_DOC", "FX_ORD_ATT"]
    assert nume.index("FX_Rezervari") < nume.index("FX_Rezervarii_IMG")
    assert nume.index("FX_Receptii_R") < nume.index("FX_Receptii_IMG")
    assert nume.index("FX_Receptii_H") < nume.index("FX_Receptii_Plati")
    assert nume.index("FX_Angajamente") < nume.index("FX_Salarii")


def test_tabel_necunoscut_arunca_nu_intoarce_tacut_nimic():
    with pytest.raises(KeyError):
        tables.by_name("FX_Parteneri")


def test_bifele_se_intorc_in_ordinea_operatorului():
    # Ordinea trimisa ESTE ordinea de scriere: migratorul lasa tabelele sa fie
    # rearanjate, iar serverul o respecta intocmai.
    alese = tables.selected(["FX_Rezervarii_IMG", "FX_Angajamente", "FX_Rezervari"])
    assert [t.name for t in alese] == ["FX_Rezervarii_IMG", "FX_Angajamente", "FX_Rezervari"]


def test_un_tabel_trimis_de_doua_ori_arunca():
    with pytest.raises(KeyError):
        tables.selected(["FX_Angajamente", "FX_Angajamente"])


def test_bifa_pe_un_tabel_strain_arunca():
    with pytest.raises(KeyError):
        tables.selected(["FX_Parteneri"])


def test_fara_bife_inseamna_toate():
    assert len(tables.selected(None)) == 27


# --- familiile DDF / ORD ------------------------------------------------------

def test_ddf_cu_dc_propriu_il_foloseste():
    keep, reject = selector("FX_DDF").keep({"IDDF": 1, "DC": "005_CEVM", "IdUnitate": 48})
    assert (keep, reject) == (True, None)


def test_revizia_urmeaza_ddf_ul():
    keep, reject = selector("FX_DDF_REV").keep({"IDREV": 1, "IDDF": 61})
    assert (keep, reject) == (True, None)


def test_copilul_reviziei_urmeaza_revizia():
    keep, reject = selector("FX_DDF_REV_SA").keep({"ID": 1, "IDREV": 71})
    assert (keep, reject) == (True, None)


def test_revizia_altei_unitati_e_sarita():
    keep, reject = selector("FX_DDF_REV_SB").keep({"ID": 2, "IDREV": 70})
    assert (keep, reject) == (False, None)


def test_ord_urmeaza_angajamentul():
    keep, reject = selector("FX_ORD").keep({"IDORD": 1, "CodAngajament": "AAB-002"})
    assert (keep, reject) == (True, None)


def test_copilul_ord_urmeaza_ord_ul():
    keep, reject = selector("FX_ORD_PART").keep({"IDORDPART": 1, "IDORD": 81})
    assert (keep, reject) == (True, None)


def test_copilul_ord_inexistent_e_respins():
    keep, reject = selector("FX_ORD_DOC").keep({"IDORDDOC": 1, "IDORD": 999})
    assert keep is False
    assert "FX_ORD" in reject


def test_cheia_primara_e_raportata_pentru_lista_de_respinse():
    s = selector("FX_Istoric")
    assert s.primary_key_of({"ID": 17}) == "17"
    assert s.primary_key_of({}) == "?"


# --- construirea planului din fisierul insusi ---------------------------------
# accdb.iter_rows e inlocuit cu un dictionar de tabele: aici se verifica REGULA,
# nu mdbtools.

FISIER_CU_DOUA_UNITATI = {
    "FX_Angajamente": [
        {"CodAngajament": "AAB-001", "IdUnitate": 48, "DC": "000_DEMO"},
        {"CodAngajament": "AAB-002", "IdUnitate": 75, "DC": "005_CEVM"},
        {"CodAngajament": "AAB-003", "IdUnitate": 75},
    ],
    "FX_Indicatori": [
        {"CodAI": "AAB-004-A", "CodAngajament": "AAB-004", "IdUnitate": 75},
    ],
    "FX_Rezervari": [
        {"IDRZ": 10, "CodAngajament": "AAB-001"},
        {"IDRZ": 11, "CodAngajament": "AAB-002"},
    ],
    "FX_Receptii_R": [{"IDRR": 21, "CodAngajament": "AAB-003"}],
    "FX_Receptii_H": [{"IDRH": 31, "CodAngajament": "AAB-002"}],
    "FX_Extrase_H": [
        {"IDEXH": 1, "IDEXF": 40, "IdUnitate": 48},
        {"IDEXH": 2, "IDEXF": 41, "IdUnitate": 75},
    ],
}


def fisier(monkeypatch, continut):
    from routes.migrare import accdb

    def iter_rows(path, table, timeout=3600):
        if table not in continut:
            raise accdb.AccdbError("tabelul «%s» nu există în fișier" % table)
        for row in continut[table]:
            yield row

    monkeypatch.setattr(accdb, "iter_rows", iter_rows)


def test_planul_afla_unitatea_din_fx_angajamente(monkeypatch):
    fisier(monkeypatch, FISIER_CU_DOUA_UNITATI)
    p = routing.build_plan("fără-fișier.accdb", "005_CEVM")

    assert p.units == [75]
    assert p.all_units == [48, 75]
    assert p.single_unit is False
    # AAB-003 nu are DC scris pe rand, dar are IdUnitate 75; AAB-004 vine din
    # FX_Indicatori, care e a doua sursa de IdUnitate.
    assert p.sets["commitment"].ours == {"aab-002", "aab-003", "aab-004"}
    assert p.sets["reservation"].ours == {11}
    assert p.sets["receipt_r"].ours == {21}
    assert p.sets["receipt_h"].ours == {31}
    assert p.sets["statement"].ours == {41}
    assert p.sets["statement_h"].ours == {2}


def test_planul_pentru_cealalta_unitate_alege_altceva(monkeypatch):
    fisier(monkeypatch, FISIER_CU_DOUA_UNITATI)
    p = routing.build_plan("fără-fișier.accdb", "000_DEMO")
    assert p.units == [48]
    assert p.sets["commitment"].ours == {"aab-001"}
    assert p.sets["statement"].ours == {40}


def test_baza_care_nu_apare_in_fisier_opreste_cu_unitatile_numite(monkeypatch):
    fisier(monkeypatch, FISIER_CU_DOUA_UNITATI)
    with pytest.raises(routing.RoutingError) as info:
        routing.build_plan("fără-fișier.accdb", "045_CTER")
    mesaj = str(info.value)
    assert "045_CTER" in mesaj and "48" in mesaj and "75" in mesaj


def test_fisierul_cu_o_singura_unitate_merge_oricum_in_baza_aleasa(monkeypatch):
    fisier(monkeypatch, {
        "FX_Angajamente": [{"CodAngajament": "AAB-001", "IdUnitate": 75}],
        "FX_Indicatori": [],
    })
    p = routing.build_plan("fără-fișier.accdb", "045_CTER")
    assert p.single_unit is True
    assert p.units == [75]
    assert p.sets["commitment"].ours == {"aab-001"}
