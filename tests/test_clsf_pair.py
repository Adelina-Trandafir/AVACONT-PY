# Unit tests for utils/clsf_pair.py and routes/forexe/extrase_lista.py (slice 0080-01/02).
# Offline: no database. Written for the operator to run; not run by the author.
#
#     PYTHON/.venv/Scripts/python.exe -m pytest tests/test_clsf_pair.py
from datetime import date, datetime

from utils import clsf_pair
from routes.forexe.extrase_lista import antet_row, operatiune_row


def test_the_seven_tables_are_exactly_the_ones_that_held_the_access_id():
    assert set(clsf_pair.PAIR_TABLE_NAMES) == {
        "FX_Extrase_H", "FX_Indicatori", "FX_Istoric", "FX_Plati",
        "FX_Receptii", "FX_Receptii_RHR", "FX_Rezervari"}


def test_tables_without_an_own_unit_take_it_from_the_indicator():
    for name in ("FX_Istoric", "FX_Rezervari"):
        sql = clsf_pair.problem_sql(clsf_pair.pair_table(name))
        assert "LEFT JOIN `FX_Indicatori` I ON I.CodAI = T.CodAI" in sql
        assert "C.IdUnitate = I.IdUnitate" in sql


def test_tables_with_an_own_unit_fall_back_to_the_indicator_when_it_is_null():
    sql = clsf_pair.fill_sql(clsf_pair.pair_table("FX_Plati"), None, "IdClsfAcc_0080")
    assert "COALESCE(T.IdUnitate, I.IdUnitate)" in sql


def test_headers_and_indicators_use_only_their_own_unit():
    for name in ("FX_Extrase_H", "FX_Indicatori"):
        sql = clsf_pair.fill_sql(clsf_pair.pair_table(name), None, "IdClsfAcc_0080")
        assert "FX_Indicatori` I" not in sql
        assert "C.IdUnitate = T.IdUnitate" in sql


def test_the_access_id_is_read_from_the_column_named():
    # Before the conversion it is still in IdClsf; during it, in the temporary.
    t = clsf_pair.pair_table("FX_Istoric")
    assert "C.IdClsfAcc = T.`IdClsf`" in clsf_pair.problem_sql(t)
    fill = clsf_pair.fill_sql(t, None, "IdClsfAcc_0080")
    assert "C.IdClsfAcc = T.`IdClsfAcc_0080`" in fill
    assert "SET T.IdClsf = " in fill


def test_zero_and_null_access_ids_mean_no_classification_and_are_not_touched():
    for t in clsf_pair.PAIR_TABLES:
        cond = "T.`X` IS NOT NULL AND T.`X` <> 0"
        assert cond in clsf_pair.fill_sql(t, None, "X")
        assert cond in clsf_pair.problem_sql(t, None, "X")


def test_a_database_name_qualifies_every_table():
    sql = clsf_pair.fill_sql(clsf_pair.pair_table("FX_Istoric"), "000_DEMO", "X")
    assert "`000_DEMO`.`FX_Istoric` T" in sql
    assert "`000_DEMO`.`FX_Indicatori` I" in sql
    assert "`000_DEMO`.`Clasificatii` C" in sql


def test_the_marker_is_what_says_converted():
    assert clsf_pair.CONVERTED_TAG in clsf_pair.MARKER
    sql = clsf_pair.converted_sql()
    assert "COLUMN_COMMENT LIKE '%0080-01%'" in sql
    assert "'FX_Plati'" in sql and "'FX_Istoric'" in sql


class _Cur:
    """Answers the Resolver's two reads."""

    def __init__(self, clsf, indicatori):
        self._next = None
        self._clsf = clsf
        self._ind = indicatori

    def execute(self, sql, params=None):
        self._next = self._clsf if "Clasificatii" in sql else self._ind

    def fetchall(self):
        return self._next


def _resolver():
    clsf = [{"IDClsf": 900, "IdClsfAcc": 316, "IdUnitate": 157},
            {"IDClsf": 901, "IdClsfAcc": 316, "IdUnitate": 158},
            {"IDClsf": 902, "IdClsfAcc": 5, "IdUnitate": 157},
            {"IDClsf": 903, "IdClsfAcc": 5, "IdUnitate": 157}]      # a duplicate in 157
    ind = [{"CodAI": "AAB-AAB", "IdUnitate": 157}]
    return clsf_pair.Resolver(_Cur(clsf, ind))


def test_resolver_uses_the_row_unit_then_the_indicator():
    r = _resolver()
    plati = clsf_pair.pair_table("FX_Plati")
    istoric = clsf_pair.pair_table("FX_Istoric")
    assert r.resolve(plati, {"IdUnitate": 158, "IdPlataFX": 1}, 316) == 901
    assert r.resolve(plati, {"IdUnitate": None, "CodAI": "AAB-AAB", "IdPlataFX": 2}, 316) == 900
    assert r.resolve(istoric, {"CodAI": "AAB-AAB", "ID": 3}, "316") == 900
    assert r.problems == {}


def test_resolver_zero_is_no_classification():
    r = _resolver()
    assert r.resolve(clsf_pair.pair_table("FX_Istoric"), {"ID": 1}, 0) is None
    assert r.resolve(clsf_pair.pair_table("FX_Istoric"), {"ID": 1}, None) is None
    assert r.problems == {}


def test_resolver_records_unresolved_ambiguous_and_unitless_rows():
    r = _resolver()
    istoric = clsf_pair.pair_table("FX_Istoric")
    assert r.resolve(istoric, {"CodAI": "NU-EXISTA", "ID": 1}, 316) is None   # no unit
    assert r.resolve(istoric, {"CodAI": "AAB-AAB", "ID": 2}, 5) is None       # two rows
    assert r.resolve(istoric, {"CodAI": "AAB-AAB", "ID": 3}, 77) is None      # no row
    found = r.problems["FX_Istoric"]
    assert [p["potriviri"] for p in found] == [0, 2, 0]
    assert found[0]["id_unitate"] is None
    text = " | ".join(clsf_pair.describe_problems(r.problems))
    assert "FX_Istoric: 3 rânduri" in text and "fără unitate" in text


def test_unknown_table_is_not_a_pair_table():
    assert clsf_pair.pair_table("FX_ORD_TBL") is None
    assert clsf_pair.pair_table("fx_plati").name == "FX_Plati"


def test_header_row_travels_with_iso_dates_and_zeroed_money():
    row = antet_row((7, 3, datetime(2026, 1, 19, 0, 0), "12", 88, "65.02", "Den",
                     "RO49", "21E", None, 1.5, 0, 0, 2, 3, 4, 5))
    assert row["data_extras"] == "2026-01-19"
    assert row["sid"] == 0.0 and row["sic"] == 1.5 and row["sfc"] == 5.0
    assert row["id_clsf"] == 88 and row["clsf"] == "65.02"


def test_operation_row_sends_datadoc_as_iso_date():
    row = operatiune_row((1, 7, datetime(2026, 1, 19, 10, 0), date(2026, 1, 18), "0100",
                          "TZ1", "D1", "SRL", "123", "RO21", None, 792.0, "TZ1-EXPL",
                          "AAB", None, None, None))
    assert row["data_banca"] == "2026-01-19"
    assert row["data_doc"] == "2026-01-18"
    assert row["suma_debit"] == 0.0 and row["suma_credit"] == 792.0
    assert row["cod_contract"] == "AAB" and row["rand_contract"] is None
