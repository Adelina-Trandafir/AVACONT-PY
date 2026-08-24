# Offline unit tests for the Access -> MariaDB value parser
# (routes/migrare/parser.py). Run from the PYTHON folder:
#   python -m pytest tests/test_migrare_parser.py
#
# No config.py, no MariaDB, no Access file, no mdbtools: the parser answers from
# the target column's metadata alone, which is why it is its own module.
#
# The failure they exist for (2026-08-21):
#   1292 (22007): Incorrect datetime value: '04/28/26 15:28:03' for column
#   `000_DEMO`.`FX_Angajamente`.`DTQ` at row 1
# `validate._DATE_FORMATS` already ACCEPTED that string when checking, but the
# write sent the original. A checker that coerces in order to judge, next to a
# writer that does not coerce, is a checker that lies.

import datetime
import decimal

from routes.migrare import parser


def col(tip, tip_complet=None, **overrides):
    meta = {
        "nume": "X",
        "tip": tip,
        "tip_complet": tip_complet or tip,
        "lungime": 50,
        "precizie": 19,
        "scara": 4,
        "accepta_nul": True,
        "are_implicit": False,
        "auto": False,
        "extra": "",
        "cheie": "",
    }
    meta.update(overrides)
    return meta


def parsed(tip, value, tip_complet=None):
    return parser.parse_value(col(tip, tip_complet), value)[0]


# --- the reported failure ----------------------------------------------------

def test_formatul_mdbtools_devine_datetime():
    assert parsed("datetime", "04/28/26 15:28:03") == \
        datetime.datetime(2026, 4, 28, 15, 28, 3)


def test_conversia_e_raportata_ca_schimbare():
    value, note, ambiguous = parser.parse_value(col("datetime"),
                                                "04/28/26 15:28:03")
    assert note is not None
    assert ambiguous is False


# --- dates -------------------------------------------------------------------

def test_zi_prima_cu_bara():
    assert parsed("datetime", "28/04/2026") == datetime.datetime(2026, 4, 28)


def test_zi_prima_cu_punct():
    assert parsed("datetime", "28.04.2026") == datetime.datetime(2026, 4, 28)


def test_luna_prima_cu_bara():
    assert parsed("datetime", "04/28/2026") == datetime.datetime(2026, 4, 28)


def test_iso_ramane_iso():
    assert parsed("datetime", "2026-04-28 15:28:03") == \
        datetime.datetime(2026, 4, 28, 15, 28, 3)


def test_data_fara_ora_capata_miezul_noptii():
    assert parsed("datetime", "2026-04-28") == datetime.datetime(2026, 4, 28)


def test_ora_de_dupa_amiaza():
    assert parsed("datetime", "12/31/25 11:59:59 PM") == \
        datetime.datetime(2025, 12, 31, 23, 59, 59)


def test_anul_din_doua_cifre_se_pivoteaza():
    # Sub 70 -> anii 2000; de la 70 in sus -> anii 1900.
    assert parsed("datetime", "01/02/69").year == 2069
    assert parsed("datetime", "01/02/70").year == 1970


def test_coloana_date_pierde_ora():
    assert parsed("date", "04/28/26 15:28:03") == datetime.date(2026, 4, 28)


def test_coloana_time_pastreaza_doar_ora():
    assert parsed("time", "04/28/26 15:28:03") == "15:28:03"


def test_o_data_imposibila_ramane_neatinsa():
    # 31 februarie: nu se inventeaza nimic, ramane ca sa fie raportata de
    # check_value ca TIP -- constatare blocanta.
    assert parsed("datetime", "31/02/2026") == "31/02/2026"


def test_un_text_care_nu_e_data_ramane_neatins():
    assert parsed("datetime", "nu e o dată") == "nu e o dată"


def test_datetime_gata_facut_nu_se_atinge():
    when = datetime.datetime(2026, 4, 28, 15, 28, 3)
    value, note, _ = parser.parse_value(col("datetime"), when)
    assert value is when and note is None


# --- the ambiguous day/month rule -------------------------------------------

def test_ambiguu_cu_doua_cifre_si_bara_e_luna_prima():
    # Formatul lui mdbtools (%m/%d/%y): 05/04/26 = 4 mai.
    value, _, ambiguous = parser.parse_value(col("datetime"), "05/04/26")
    assert value == datetime.datetime(2026, 5, 4)
    assert ambiguous is True


def test_ambiguu_cu_patru_cifre_si_bara_e_ziua_prima():
    value, _, ambiguous = parser.parse_value(col("datetime"), "05/04/2026")
    assert value == datetime.datetime(2026, 4, 5)
    assert ambiguous is True


def test_punctul_e_intotdeauna_ziua_prima():
    value, _, ambiguous = parser.parse_value(col("datetime"), "05.04.2026")
    assert value == datetime.datetime(2026, 4, 5)
    assert ambiguous is True


def test_ce_nu_poate_fi_luna_nu_e_ambiguu():
    _, _, ambiguous = parser.parse_value(col("datetime"), "28/04/2026")
    assert ambiguous is False


# --- numbers -----------------------------------------------------------------

def test_virgula_zecimala_devine_punct():
    assert parsed("decimal", "1234,56") == decimal.Decimal("1234.56")


def test_punctul_zecimal_ramane():
    assert parsed("decimal", "1234.56") == decimal.Decimal("1234.56")


def test_negativ_cu_virgula():
    assert parsed("decimal", "-12,5") == decimal.Decimal("-12.5")


def test_virgula_zecimala_si_pe_double():
    assert parsed("double", "1234,56") == 1234.56


def test_amandoi_separatorii_nu_se_ghicesc():
    # Access nu scrie separator de mii (confirmat de operator). Un sir cu
    # amandoi separatorii nu e ce credem noi ca e, si nu exista citire sigura:
    # ramane neatins si il raporteaza check_value.
    assert parsed("decimal", "1.234,56") == "1.234,56"


def test_spatiu_intre_cifre_nu_se_ghiceste():
    assert parsed("decimal", "1 234") == "1 234"


def test_text_care_nu_e_numar_ramane_neatins():
    assert parsed("decimal", "abc") == "abc"


def test_float_intreg_devine_intreg():
    assert parsed("int", 5.0) == 5


def test_fractia_spre_o_coloana_intreaga_ramane_neatinsa():
    # MariaDB ar rotunji tacut; asa ramane sa fie raportata.
    assert parsed("int", 5.5) == 5.5


# --- booleans ----------------------------------------------------------------

def test_minus_unu_devine_unu_pe_tinyint_1():
    assert parsed("tinyint", -1, "tinyint(1)") == 1


def test_zero_ramane_zero():
    assert parsed("tinyint", 0, "tinyint(1)") == 0


def test_da_si_nu_in_cuvinte():
    assert parsed("tinyint", "Da", "tinyint(1)") == 1
    assert parsed("tinyint", "Nu", "tinyint(1)") == 0


def test_boolean_din_json():
    assert parsed("tinyint", True, "tinyint(1)") == 1
    assert parsed("tinyint", False, "tinyint(1)") == 0


def test_un_tinyint_obisnuit_nu_e_boolean():
    # -1 e un tinyint perfect valid. Pe o coloana care numara ceva, a-l face 1
    # ar fi coruptie, nu conversie.
    assert parsed("tinyint", -1, "tinyint(4)") == -1


# --- empty values ------------------------------------------------------------

def test_text_gol_devine_null_pe_coloana_numerica():
    assert parsed("int", "   ") is None


def test_text_gol_ramane_text_pe_coloana_text():
    assert parsed("varchar", "   ") == "   "


def test_null_ramane_null():
    assert parsed("datetime", None) is None


# --- the promise the whole module rests on -----------------------------------

def test_parse_row_lasa_in_pace_coloanele_fara_tinta():
    target = {"A": col("int")}
    row = {"A": "5", "NuEPeTinta": "orice"}
    out, changes = parser.parse_row(row, target)
    assert out["NuEPeTinta"] == "orice"
    assert out["A"] == 5
    assert [c.column for c in changes] == ["A"]


def test_parse_row_nu_arunca_niciodata():
    class Exploding(object):
        def __str__(self):
            raise RuntimeError("boom")
    target = {"A": col("varchar")}
    out, _ = parser.parse_row({"A": Exploding()}, target)
    assert isinstance(out["A"], Exploding)
