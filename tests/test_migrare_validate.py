# Offline unit tests for the slice 0044 validator (routes/migrare/validate.py).
# Run from the PYTHON folder:
#   python -m pytest tests/test_migrare_validate.py
#
# These need NO config.py, NO MariaDB and NO Access file: check_value() answers
# from the column metadata alone, which is exactly why it is a separate function.
# The two classes it produces are what enable or disable the migrator's buttons,
# so the class of every finding is asserted here, not only its presence.

import pytest

from routes.migrare import validate


def col(**overrides):
    meta = {
        "nume": "X",
        "tip": "varchar",
        "tip_complet": "varchar(10)",
        "lungime": 10,
        "precizie": None,
        "scara": None,
        "accepta_nul": True,
        "are_implicit": False,
        "auto": False,
        "cheie": "",
    }
    meta.update(overrides)
    return meta


# --- text --------------------------------------------------------------------

def test_text_care_incape_trece():
    assert validate.check_value(col(), "abcdefghij") is None


def test_text_prea_lung_e_dimensiune_si_e_blocant():
    fel, mesaj = validate.check_value(col(), "abcdefghijk")
    assert fel == validate.F_DIMENSIUNE
    assert validate.CLASS_OF[fel] == validate.BLOCANT
    assert "11" in mesaj and "10" in mesaj


def test_longtext_fara_limita_accepta_orice():
    meta = col(tip="longtext", tip_complet="longtext", lungime=4294967295)
    assert validate.check_value(meta, "x" * 100000) is None


# --- NULL --------------------------------------------------------------------

def test_nul_intr_o_coloana_care_il_accepta_trece():
    assert validate.check_value(col(), None) is None


def test_nul_intr_o_coloana_not_null_e_blocant():
    fel, _ = validate.check_value(col(**{"accepta_nul": False}), None)
    assert fel == validate.F_NUL_INTERZIS
    assert validate.CLASS_OF[fel] == validate.BLOCANT


def test_nul_e_iertat_daca_exista_valoare_implicita():
    meta = col(**{"accepta_nul": False})
    meta["are_implicit"] = True
    assert validate.check_value(meta, None) is None


def test_nul_e_iertat_pe_o_coloana_auto_increment():
    meta = col(**{"accepta_nul": False})
    meta["auto"] = True
    assert validate.check_value(meta, None) is None


# --- intregi -----------------------------------------------------------------

def test_text_intr_o_coloana_intreaga_e_tip():
    meta = col(tip="int", tip_complet="int(11)", lungime=None)
    fel, _ = validate.check_value(meta, "nu-i număr")
    assert fel == validate.F_TIP
    assert validate.CLASS_OF[fel] == validate.BLOCANT


def test_numar_peste_intervalul_int_e_dimensiune():
    meta = col(tip="int", tip_complet="int(11)", lungime=None)
    fel, mesaj = validate.check_value(meta, 9999999999)
    assert fel == validate.F_DIMENSIUNE
    assert "int" in mesaj


def test_negativul_intr_o_coloana_unsigned_e_dimensiune():
    meta = col(tip="int", tip_complet="int(10) unsigned", lungime=None)
    fel, _ = validate.check_value(meta, -1)
    assert fel == validate.F_DIMENSIUNE


def test_numarul_ca_text_e_acceptat():
    meta = col(tip="int", tip_complet="int(11)", lungime=None)
    assert validate.check_value(meta, "42") is None


def test_zecimalul_intr_o_coloana_intreaga_e_tip():
    # 12,5 NU e 12: nu rotunjim tacut o valoare care ar pierde din ea.
    meta = col(tip="int", tip_complet="int(11)", lungime=None)
    fel, _ = validate.check_value(meta, 12.5)
    assert fel == validate.F_TIP


# --- zecimale ----------------------------------------------------------------

def test_decimal_care_incape_trece():
    meta = col(tip="decimal", tip_complet="decimal(19,4)", lungime=None,
               precizie=19, scara=4)
    assert validate.check_value(meta, "123456.78") is None


def test_decimal_cu_prea_multe_cifre_intregi_e_dimensiune():
    meta = col(tip="decimal", tip_complet="decimal(5,2)", lungime=None,
               precizie=5, scara=2)
    fel, _ = validate.check_value(meta, "12345.67")
    assert fel == validate.F_DIMENSIUNE


def test_decimal_din_text_neconvertibil_e_tip():
    meta = col(tip="decimal", tip_complet="decimal(5,2)", lungime=None,
               precizie=5, scara=2)
    fel, _ = validate.check_value(meta, "12,34 lei")
    assert fel == validate.F_TIP


# --- date --------------------------------------------------------------------

@pytest.mark.parametrize("valoare", ["2026-08-20 10:30:00", "2026-08-20"])
def test_datele_in_formatele_asteptate_trec(valoare):
    meta = col(tip="datetime", tip_complet="datetime", lungime=None)
    assert validate.check_value(meta, valoare) is None


def test_data_ilizibila_e_tip():
    meta = col(tip="datetime", tip_complet="datetime", lungime=None)
    fel, _ = validate.check_value(meta, "20 august")
    assert fel == validate.F_TIP


# --- clasele si cele doua butoane --------------------------------------------

def test_cheile_de_integritate_sunt_fortabile():
    for fel in (validate.F_CHEIE_STRAINA,
                validate.F_CHEIE_DUBLA, validate.F_SELECTION):
        assert validate.CLASS_OF[fel] == validate.FORTABIL


def test_verificarea_id_urilor_ddf_a_disparut():
    # FX_DDF/FX_DDF_REV nu fac parte din setul migrat, deci pe o baza proaspata
    # sunt goale: verificarea id-urilor Access impotriva lor marca TOTUL ca
    # lipsa si, la rulare fortata, arunca randurile. A fost scoasa cu totul.
    assert not hasattr(validate, "F_DDF_LIPSA")
    assert not hasattr(validate, "_check_ddf_ids")

    from routes.migrare import tables
    assert not hasattr(tables, "DDF_ID_TABLE")


def test_raport_gol_lasa_ruleaza_pornit_si_forteaza_oprit():
    date = validate.Report("000_DEMO").to_dict()
    assert date["curat"] is True
    assert date["poate_rula"] is True
    assert date["poate_forta"] is False


def test_doar_constatari_fortabile_opresc_ruleaza_si_pornesc_forteaza():
    raport = validate.Report("000_DEMO")
    raport.add("FX_Istoric", "IDREV", validate.F_CHEIE_STRAINA, "17", "id absent", 999)
    date = raport.to_dict()
    assert date["poate_rula"] is False
    assert date["poate_forta"] is True
    assert date["are_blocante"] is False


def test_o_singura_constatare_blocanta_opreste_ambele_butoane():
    raport = validate.Report("000_DEMO")
    raport.add("FX_Istoric", "IDREV", validate.F_CHEIE_STRAINA, "17", "id absent", 999)
    raport.add("FX_Plati", "Explicatii", validate.F_DIMENSIUNE, "9", "prea lung", "x" * 300)
    date = raport.to_dict()
    assert date["poate_rula"] is False
    assert date["poate_forta"] is False
    assert date["are_blocante"] is True


def test_raportul_numara_tot_dar_pastreaza_doar_cateva_exemple():
    raport = validate.Report("000_DEMO")
    for i in range(validate.MAX_EXAMPLES + 40):
        raport.add("FX_Plati", "Suma", validate.F_TIP, str(i), "nu e număr", "x")
    constatare = raport.to_dict()["constatari"][0]
    assert constatare["numar"] == validate.MAX_EXAMPLES + 40
    assert len(constatare["exemple"]) == validate.MAX_EXAMPLES


# --- coloanele alese de operator ---------------------------------------------

def test_coloanele_alese_includ_mereu_cheia_primara():
    chosen = validate.chosen_columns_of(
        "FX_Angajamente", {"FX_Angajamente": ["Denumire"]}, ["CodAngajament"])
    assert chosen == {"Denumire", "CodAngajament"}


def test_tabel_fara_alegere_inseamna_toate_coloanele():
    assert validate.chosen_columns_of("FX_Plati", {"FX_Istoric": ["ID"]},
                                      ["IdPlataFX"]) is None
    assert validate.chosen_columns_of("FX_Plati", None, ["IdPlataFX"]) is None


# --- cheile scrise in aceeasi rulare -----------------------------------------

def test_formele_cheii_acopera_int_text_si_litere_mici():
    known = set()
    validate._add_key_forms(known, "AAB2DD35323")
    validate._add_key_forms(known, 42)
    assert validate._key_known(known, "aab2dd35323")
    assert validate._key_known(known, "42")
    assert validate._key_known(known, 42)
    assert not validate._key_known(known, 77)


def test_blocantele_apar_primele_in_raport():
    raport = validate.Report("000_DEMO")
    raport.add("FX_Istoric", "IDREV", validate.F_CHEIE_STRAINA, "17", "id absent", 999)
    raport.add("FX_Plati", "Explicatii", validate.F_DIMENSIUNE, "9", "prea lung", "x")
    clase = [c["clasa"] for c in raport.to_dict()["constatari"]]
    assert clase[0] == validate.BLOCANT
