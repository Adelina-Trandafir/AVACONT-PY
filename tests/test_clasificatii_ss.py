# Offline unit tests for routes/clasificatii_ss.py (slice 0075-00).
# Run from the PYTHON folder:
#   python -m pytest tests/test_clasificatii_ss.py
#
# WHAT THEY GUARD. Sector/Sursa/SS stopped being GENERATED columns, so this module is now
# the only thing standing between a write site and a wrong sector-source. Two promises are
# tested here:
#
#   1. Called with a capitol alone -- which is all any pre-0075 writer has -- it reproduces
#      the OLD generated expression exactly. Those routes must behave as they always did.
#   2. Called with a source letter it reaches all fourteen DefaSursaSector values, which is
#      the whole reason for the change.
#
# Nothing here touches a database or imports Flask.
from routes.clasificatii_ss import derive_ss, sector_of, source_of, ss_values


# --------------------------------------------------------- the old behaviour, preserved

def test_capitolele_vechi_dau_exact_ce_dadea_coloana_generata():
    # The generated column knew four capitol endings and two letters. A writer that passes
    # only the capitol must still get those answers, or every legacy row changes meaning.
    assert derive_ss("65.00") == "01A"
    assert derive_ss("65.01") == "01A"
    assert derive_ss("65.02") == "02A"
    assert derive_ss("65.10") == "02E"


def test_capitolul_necunoscut_da_o_valoare_pe_care_cheia_straina_o_refuza():
    # Not a plausible wrong value: a bare letter, refused by the foreign key with 1452.
    assert derive_ss("65.99") == "A"
    assert sector_of("65.99") == ""


def test_capitol_absent_sau_scurt_nu_arunca():
    # A write site can hand over anything; this module must answer, and the foreign key
    # decides. Raising here would turn a bad row into a 500.
    assert derive_ss(None) == "A"
    assert derive_ss("") == "A"
    assert derive_ss("6") == "A"


# --------------------------------------------------------- the new reach

def test_toate_cele_paisprezece_valori_sunt_accesibile():
    # DefaSursaSector on the live server, 22.09.2026. Before this change only three of them
    # could ever be produced.
    wanted = {
        ("65.01", "A"): "01A", ("65.01", "D"): "01D", ("65.01", "E"): "01E",
        ("65.01", "F"): "01F", ("65.01", "G"): "01G",
        ("65.02", "A"): "02A", ("65.02", "C"): "02C", ("65.02", "D"): "02D",
        ("65.02", "G"): "02G",
        ("65.03", "A"): "03A", ("65.04", "A"): "04A", ("65.05", "A"): "05A",
        ("65.08", "A"): "08A",
    }
    for (capitol, sursa), expected in wanted.items():
        assert derive_ss(capitol, sursa) == expected, f"{capitol} + {sursa}"
    # The fourteenth, 02E, is the one the capitol decides by itself.
    assert derive_ss("65.10") == "02E"


def test_capitolul_xx10_este_E_orice_ar_spune_apelantul():
    # The capitol is what the rest of the row is built on; a caller saying otherwise is
    # wrong about its own row.
    assert source_of("65.10", "A") == "E"
    assert derive_ss("65.10", "F") == "02E"


def test_sursa_se_normalizeaza_la_o_singura_litera_mare():
    # The column is char(1); cutting here beats a 1406 at INSERT under strict mode.
    assert source_of("65.01", " d ") == "D"
    assert source_of("65.01", "g") == "G"
    assert source_of("65.01", "CD") == "C"
    assert source_of("65.01", "") == "A"
    assert source_of("65.01", None) == "A"


# --------------------------------------------------------- the shape write sites use

def test_ss_values_intoarce_tripletul_in_ordinea_din_insert():
    assert ss_values("65.01", "F") == ("01", "F", "01F")
    assert ss_values("65.10") == ("02", "E", "02E")
    assert ss_values("65.99") == ("", "A", "A")


def test_ss_values_este_acelasi_lucru_cu_cele_trei_functii():
    # One source of truth: a write site that uses the triple cannot drift from a write site
    # that calls the pieces.
    for capitol in ("65.00", "65.01", "65.02", "65.10", "65.03", "65.99", None):
        for sursa in (None, "A", "F"):
            assert ss_values(capitol, sursa) == (
                sector_of(capitol), source_of(capitol, sursa), derive_ss(capitol, sursa))
