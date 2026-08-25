# Unit tests for routes/forexe/prelucrare_helpers.py (slice 0048-01).
#
# These are OFFLINE tests: the module under test imports nothing but the standard
# library, so there is no config.py / database / host guard here. Run with:
#     PYTHON/.venv/Scripts/python.exe -m pytest tests/test_forexe_prelucrare_helpers.py
#
# Every literal below is taken from the real payload described in the plan
# (NOVA_WATER_SC35_resend.json, angajament AAB37CNBK95) or quoted verbatim from
# the Access VBA being ported. Nothing here is invented.
from datetime import date, datetime

from routes.forexe.prelucrare_helpers import (
    bytes_to_hex,
    cod_ai,
    extract_number_after_label,
    extract_numar_rev,
    extract_obs_value,
    extract_rezervare_definitiva,
    extract_text_after_label,
    extract_text_between,
    fx_extract_cod_indicator,
    fx_receptii_h_get_hash_ident,
    fx_receptii_istoric_get_indent,
    fx_receptii_normalize_ssi,
    fx_receptii_num_key,
    get_hash_for_row_istoric,
    get_tip_rand,
    is_initiala_descriere,
    is_rand_contract_row,
    null_if_empty,
    parse_amount,
    parse_data_zzllaaaa,
    parse_english_date,
    parse_loose_number,
    parse_timp_istoric,
    split_sector_sursa_indicator,
)

# The payment Observatie quoted in plan section 7 step 5, verbatim.
PLATA_OBS = ("Plata: Rand: AAB, document: 38, data: Feb 16, 2026 12:00:00 AM "
             "valoare: 819, IdTrezor: TZ52198479598")


# ---------------------------------------------------------------------------
# Numbers -- the shapes FOREXE really emits
# ---------------------------------------------------------------------------
class TestParseLooseNumber:
    def test_romanian_thousands_and_decimals(self):
        # dot = thousands, comma = decimal
        assert parse_loose_number("819.500,00") == 819500.0

    def test_bare_dot_is_thousands_not_decimal(self):
        # The trap: "3.587" is three thousand five hundred eighty-seven.
        assert parse_loose_number("3.587") == 3587.0

    def test_two_decimals(self):
        assert parse_loose_number("510,00") == 510.0

    def test_single_decimal_digit(self):
        # The case the VBA comment curses: FOREXE emits 123.4, not 123.40.
        assert parse_loose_number("123.4") == 123.4

    def test_plain_integer(self):
        assert parse_loose_number("210") == 210.0
        assert parse_loose_number("819") == 819.0

    def test_short_negative(self):
        # Z lands on "-": the third VBA branch.
        assert parse_loose_number("-5") == -5.0
        assert parse_loose_number("-12") == -12.0

    def test_longer_negative_takes_the_integer_branch(self):
        assert parse_loose_number("-123") == -123.0

    def test_negative_with_thousands_and_decimals(self):
        assert parse_loose_number("-1.234,56") == -1234.56

    def test_empty_and_dashes_are_zero(self):
        assert parse_loose_number("") == 0.0
        assert parse_loose_number("---") == 0.0

    def test_ron_suffix_is_stripped(self):
        assert parse_loose_number("510,00 RON") == 510.0

    def test_single_character(self):
        assert parse_loose_number("5") == 5.0


class TestParseAmount:
    def test_none_stays_none(self):
        # VBA: Null in, Null out.
        assert parse_amount(None) is None

    def test_blank_is_zero(self):
        assert parse_amount("") == 0.0
        assert parse_amount("---") == 0.0

    def test_delegates_to_loose_number(self):
        assert parse_amount("819.500,00") == 819500.0


class TestNumKey:
    def test_four_decimals_with_a_dot(self):
        assert fx_receptii_num_key(510.0) == "510.0000"
        assert fx_receptii_num_key(1234.5) == "1234.5000"

    def test_negative(self):
        assert fx_receptii_num_key(-7.25) == "-7.2500"


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------
class TestParseDataZZLLAAAA:
    def test_day_month_year_with_slashes(self):
        # 10/02/2026 is 10 February, not 2 October.
        assert parse_data_zzllaaaa("10/02/2026") == date(2026, 2, 10)

    def test_day_month_year_with_dots(self):
        assert parse_data_zzllaaaa("10.02.2026") == date(2026, 2, 10)

    def test_blank_is_none(self):
        assert parse_data_zzllaaaa("") is None
        assert parse_data_zzllaaaa(None) is None

    def test_garbage_raises(self):
        # No silent zero-date: house rule.
        try:
            parse_data_zzllaaaa("nu-i o data")
        except ValueError:
            return
        raise AssertionError("o data invalida trebuie sa ridice ValueError")


class TestParseTimpIstoric:
    def test_date_and_time_are_combined(self):
        assert parse_timp_istoric("10/02/2026 22:45:23") == datetime(2026, 2, 10, 22, 45, 23)

    def test_date_only_is_midnight(self):
        assert parse_timp_istoric("10/02/2026") == datetime(2026, 2, 10, 0, 0, 0)

    def test_ultima_modificare_shape(self):
        assert parse_timp_istoric("10/02/2026 22:46:36") == datetime(2026, 2, 10, 22, 46, 36)


class TestParseEnglishDate:
    def test_midnight_am_maps_to_zero_hours(self):
        # The exact string from the real payment Observatie.
        assert parse_english_date("Feb 16, 2026 12:00:00 AM") == datetime(2026, 2, 16, 0, 0, 0)

    def test_noon_pm_stays_twelve(self):
        assert parse_english_date("Feb 16, 2026 12:00:00 PM") == datetime(2026, 2, 16, 12, 0, 0)

    def test_afternoon_pm_adds_twelve(self):
        assert parse_english_date("Mar 3, 2026 01:30:00 PM") == datetime(2026, 3, 3, 13, 30, 0)

    def test_date_without_time(self):
        assert parse_english_date("Feb 16, 2026") == datetime(2026, 2, 16, 0, 0, 0)

    def test_unknown_month_is_none(self):
        assert parse_english_date("Foo 16, 2026") is None

    def test_blank_is_none(self):
        assert parse_english_date("") is None
        assert parse_english_date(None) is None

    def test_too_few_parts_is_none(self):
        assert parse_english_date("Feb 16") is None


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------
class TestExtractObsValue:
    def test_indicator_from_the_real_payment_line(self):
        assert extract_obs_value(PLATA_OBS, "Rand:") == "AAB"

    def test_document_number(self):
        assert extract_obs_value(PLATA_OBS, "document:") == "38"

    def test_trezor_reference_runs_to_the_end(self):
        # No comma after it, so the whole remainder is returned.
        assert extract_obs_value(PLATA_OBS, "IdTrezor:") == "TZ52198479598"

    def test_amount_before_the_comma(self):
        assert extract_obs_value(PLATA_OBS, "valoare:") == "819"

    def test_english_date_needs_an_explicit_end_key(self):
        # "data: Feb 16, 2026 ..." -- the default "," would cut at "Feb 16".
        assert extract_obs_value(PLATA_OBS, "data:", "valoare:") == "Feb 16, 2026 12:00:00 AM"

    def test_missing_key_is_blank(self):
        assert extract_obs_value(PLATA_OBS, "nuexista:") == ""

    def test_empty_end_key_returns_the_whole_remainder(self):
        assert extract_obs_value("a: bcd", "a:", "") == "bcd"

    def test_case_insensitive(self):
        assert extract_obs_value(PLATA_OBS, "RAND:") == "AAB"


class TestExtractTextBetween:
    def test_basic(self):
        assert extract_text_between("Receptie: ceva, altceva", "Receptie: ", ",") == "ceva"

    def test_missing_start_is_blank(self):
        assert extract_text_between("abc", "zz", ",") == ""

    def test_missing_end_returns_the_rest(self):
        assert extract_text_between("Rand: AAB", "Rand:", ",") == "AAB"


class TestExtractTextAfterLabel:
    def test_basic(self):
        assert extract_text_after_label("Total: 500", "Total:") == "500"

    def test_missing_label_is_blank(self):
        assert extract_text_after_label("Total: 500", "Suma:") == ""


class TestExtractNumberAfterLabel:
    def test_value_followed_by_a_comma(self):
        assert extract_number_after_label("an curent: 100, an+1: 200,", "an curent:") == 100.0

    def test_faithful_defect_no_trailing_comma_yields_zero(self):
        # The VBA guard `If P2 < P1 Then Exit Function` fires when there is no
        # comma after the label. Reproduced deliberately -- the totals already
        # in MariaDB were produced under this rule.
        assert extract_number_after_label("alti ani: 500", "alti ani:") == 0.0

    def test_na_is_zero(self):
        assert extract_number_after_label("an curent: n/a,", "an curent:") == 0.0

    def test_ron_is_stripped(self):
        assert extract_number_after_label("an curent: 100 RON,", "an curent:") == 100.0

    def test_missing_label_is_zero(self):
        assert extract_number_after_label("altceva", "an curent:") == 0.0


class TestExtractRezervareDefinitiva:
    def test_sums_the_five_buckets(self):
        obs = "an curent: 100, an+1: 200, an+2: 50, an+3: 0, alti ani: 25,"
        assert extract_rezervare_definitiva(obs) == 375.0

    def test_last_bucket_without_a_comma_is_lost(self):
        # Same faithful defect, visible at the level that matters.
        obs = "an curent: 100, an+1: 200, an+2: 50, an+3: 0, alti ani: 25"
        assert extract_rezervare_definitiva(obs) == 350.0


class TestExtractNumarRev:
    def test_basic(self):
        assert extract_numar_rev("Rezervare definitiva (REV:137)") == 137

    def test_absent_is_none(self):
        assert extract_numar_rev("fara revizie") is None

    def test_non_numeric_is_none(self):
        assert extract_numar_rev("(REV:abc)") is None

    def test_empty_is_none(self):
        assert extract_numar_rev("(REV:)") is None


class TestFxExtractCodIndicator:
    def test_rand_contract(self):
        assert fx_extract_cod_indicator("Rand contract: AAB, restul") == "AAB"

    def test_plain_rand(self):
        assert fx_extract_cod_indicator("Rand: AAB, restul") == "AAB"

    def test_payment_line_matches_via_the_rand_branch(self):
        # "Rand:" is found inside "Plata: Rand:", which is why the third VBA
        # branch is unreachable. Asserted so the fidelity note stays honest.
        assert fx_extract_cod_indicator(PLATA_OBS) == "AAB"

    def test_absent_is_none(self):
        assert fx_extract_cod_indicator("nimic relevant") is None


# ---------------------------------------------------------------------------
# Row classification
# ---------------------------------------------------------------------------
class TestGetTipRand:
    def test_receptie(self):
        assert get_tip_rand("Suma receptie: 510") == "Receptie"

    def test_receptie_t(self):
        assert get_tip_rand("Receptie: ceva (activ:true)") == "Receptie_T"

    def test_plata(self):
        assert get_tip_rand(PLATA_OBS) == "Plata"

    def test_order_matters_suma_receptie_wins(self):
        # A row carrying BOTH markers is a Receptie, not a Receptie_T, because
        # the VBA tests "suma receptie:" first.
        assert get_tip_rand("Suma receptie: 5 (activ:true)") == "Receptie"

    def test_unknown_is_blank(self):
        assert get_tip_rand("altceva") == ""

    def test_none_is_blank(self):
        assert get_tip_rand(None) == ""


class TestRowPredicates:
    def test_is_rand_contract_row(self):
        assert is_rand_contract_row("Rand contract: AAB, x") is True
        assert is_rand_contract_row("  RAND CONTRACT: AAB") is True
        assert is_rand_contract_row("Rand: AAB") is False

    def test_is_initiala_descriere(self):
        assert is_initiala_descriere("Initializare angajament") is True
        assert is_initiala_descriere("Adaugare rand.") is True
        assert is_initiala_descriere("Altceva") is False

    def test_null_if_empty(self):
        assert null_if_empty("") is None
        assert null_if_empty("   ") is None
        assert null_if_empty(None) is None
        assert null_if_empty("x") == "x"


# ---------------------------------------------------------------------------
# Classification codes
# ---------------------------------------------------------------------------
class TestSplitSectorSursaIndicator:
    def test_the_real_shape(self):
        ss, sal, e = split_sector_sursa_indicator("02E- 65. 03. 01. 20. 03. 01")
        assert ss == "02E"
        assert sal == "650301200301"     # feeds Clasificatii.ClsfSal -> IdClsfAcc
        assert e == "200301"             # feeds Clasificatii.ClsfE   -> IdUnitate (D17)

    def test_matches_the_mariadb_generated_columns(self):
        # SLICE-0045-05 reported real values for Capitol 65.01 / Articol 10.01 /
        # Alineat 01: ClsfE = 100101. Same rule, exercised here end to end.
        _ss, _sal, e = split_sector_sursa_indicator("01A- 65. 01. 04. 02. 10. 01. 01")
        assert e == "100101"

    def test_no_zero_padding_is_applied(self):
        # The plan inferred Format(x,"00") from a sibling function; the real
        # Prelucrare_Indicatori does NOT pad. A one-digit group stays one digit.
        _ss, sal, _e = split_sector_sursa_indicator("02A- 65. 3. 1")
        assert sal == "6531"

    def test_missing_dash_raises(self):
        try:
            split_sector_sursa_indicator("65.03.01")
        except ValueError:
            return
        raise AssertionError("lipsa '-' trebuie sa ridice ValueError")

    def test_blank_raises(self):
        for bad in ("", "   ", None):
            try:
                split_sector_sursa_indicator(bad)
            except ValueError:
                continue
            raise AssertionError(f"valoarea '{bad}' trebuie sa ridice ValueError")


class TestNormalizeSSI:
    def test_strips_spaces_dots_and_dashes(self):
        assert fx_receptii_normalize_ssi("02E- 65. 03. 01. 20. 03. 01") == "02E650301200301"

    def test_blank(self):
        assert fx_receptii_normalize_ssi("") == ""
        assert fx_receptii_normalize_ssi(None) == ""


class TestCodAI:
    def test_shape(self):
        assert cod_ai("AAB37CNBK95", "AAB") == "AAB37CNBK95-AAB"


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------
class TestHashing:
    def test_bytes_to_hex_is_upper_case_and_padded(self):
        # VBA Hex$ is upper case; Right$("0" & Hex$(b), 2) pads to two chars.
        assert bytes_to_hex(bytes([0x0A, 0xFF, 0x00])) == "0AFF00"

    def test_istoric_hash_is_64_upper_hex_chars(self):
        h = get_hash_for_row_istoric({
            "Timp": "10/02/2026 22:45:23",
            "Utilizator": "ADMIN",
            "Descriere": "Initializare angajament",
            "Observatii": "Rand contract: AAB, ceva",
        })
        assert len(h) == 64                    # sha256 -> 32 bytes -> 64 hex chars
        assert h == h.upper()
        assert all(c in "0123456789ABCDEF" for c in h)

    def test_istoric_hash_is_deterministic(self):
        row = {"Timp": "a", "Utilizator": "b", "Descriere": "c", "Observatii": "d"}
        assert get_hash_for_row_istoric(row) == get_hash_for_row_istoric(row)

    def test_istoric_hash_is_length_prefixed_so_shifts_do_not_collide(self):
        # The whole point of the "name=len:value|" shape: moving a character
        # from one field to the next must change the hash.
        a = get_hash_for_row_istoric(
            {"Timp": "ab", "Utilizator": "c", "Descriere": "", "Observatii": ""})
        b = get_hash_for_row_istoric(
            {"Timp": "a", "Utilizator": "bc", "Descriere": "", "Observatii": ""})
        assert a != b

    def test_missing_fields_are_treated_as_blank(self):
        assert (get_hash_for_row_istoric({})
                == get_hash_for_row_istoric(
                    {"Timp": "", "Utilizator": "", "Descriere": "", "Observatii": ""}))

    def test_receptie_header_ident_is_stable(self):
        h1 = fx_receptii_h_get_hash_ident("AAB37CNBK95", date(2026, 2, 11),
                                          "Partial", "descriere")
        h2 = fx_receptii_h_get_hash_ident("AAB37CNBK95", datetime(2026, 2, 11, 9, 30),
                                          "Partial", "descriere")
        # DataH is formatted yyyy-mm-dd, so the time part must not matter.
        assert h1 == h2
        assert len(h1) == 64

    def test_receptie_header_ident_changes_with_tip(self):
        base = ("AAB37CNBK95", date(2026, 2, 11), "Partial", "d")
        other = ("AAB37CNBK95", date(2026, 2, 11), "Final", "d")
        assert fx_receptii_h_get_hash_ident(*base) != fx_receptii_h_get_hash_ident(*other)

    def test_receptie_line_indent_integral_value_has_no_separator(self):
        # CStr(510.0) is "510" in VBA -- no decimal point, so no locale question
        # for the whole-number amounts that dominate the data.
        h = fx_receptii_istoric_get_indent("AAB37CNBK95", "AAB", date(2026, 2, 11),
                                           "02E650301200301", 510.0)
        assert len(h) == 64
