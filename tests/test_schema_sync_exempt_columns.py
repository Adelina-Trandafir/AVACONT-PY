# Offline unit tests for the AUTO_INCREMENT exemption list
# (routes/schema_sync/schema_common.EXEMPT_COLUMNS + is_exempt_column).
# Run from the PYTHON folder:
#   python -m pytest tests/test_schema_sync_exempt_columns.py
#
# WHY THIS EXISTS (slice 0048, plan docs/PLAN_ForexeIngest.md 3.1 / 3.2):
# seven primary keys are plain `INT NOT NULL` in AVACONT_SURSA and
# `INT NOT NULL AUTO_INCREMENT` in every migrated unit database. The reference
# keeps them plain deliberately -- during a migration, a row arriving with a
# missing/NULL/zero id must RAISE rather than silently receive a fabricated key.
# So every migrated database differs from the reference on exactly these seven
# columns, forever, and schema_sync must neither report nor rewrite that.
#
# These tests pin the LIST ITSELF. If someone adds an eighth pair, or widens the
# rule into "skip anything auto_increment", the count assertion fails and they
# have to come and read 3.2 -- which is the point.
import sys
import types

# Same stub pattern as test_schema_diff_columns.py: schema_common imports
# config.DB_CONFIG for its connect(), which this test never reaches. A stub
# stands in ONLY when there is no real config, so on the host the genuine one is
# still what gets imported.
try:                                                # pragma: no cover - on host
    import config                                   # noqa: F401
except ImportError:
    _stub = types.ModuleType("config")
    _stub.DB_CONFIG = {"host": "unused", "port": 3306,
                       "user": "unused", "password": "unused"}
    _stub.DB_CONFIG_NEW = dict(_stub.DB_CONFIG)
    _stub.API_KEY = "unused"
    sys.modules["config"] = _stub

from routes.schema_sync.schema_common import (          # noqa: E402
    EXEMPT_COLUMNS,
    is_exempt_column,
)

# The seven pairs, written out again here on purpose. This is a PIN, not a
# re-import: if the production list changes, this literal must be changed too,
# by someone who has read plan 3.2.
EXPECTED = {
    ("FX_Istoric",      "ID"),
    ("FX_Receptii_R",   "IDRR"),
    ("FX_Receptii_H",   "IDRH"),
    ("FX_Receptii",     "IDR"),
    ("FX_Receptii_RHR", "IDRHR"),
    ("FX_Plati",        "IdPlataFX"),
    ("FX_Rezervari",    "IDRZ"),
}


class TestTheListItself:
    def test_exactly_seven_pairs(self):
        assert len(EXEMPT_COLUMNS) == 7

    def test_the_pairs_are_the_documented_ones(self):
        assert EXEMPT_COLUMNS == EXPECTED


class TestIsExemptColumn:
    def test_every_listed_pair_is_exempt(self):
        for table, column in EXPECTED:
            assert is_exempt_column(table, column) is True

    def test_matching_is_case_insensitive(self):
        # information_schema reports names in whatever case the server stores
        # them, and lower_case_table_names=1 folds them.
        assert is_exempt_column("fx_istoric", "id") is True
        assert is_exempt_column("FX_ISTORIC", "ID") is True
        assert is_exempt_column("Fx_Receptii_Rhr", "idrhr") is True

    def test_the_right_column_on_the_wrong_table_is_not_exempt(self):
        # IDR belongs to FX_Receptii only. Seeing it elsewhere is a real
        # difference and must still be reported.
        assert is_exempt_column("FX_Receptii_H", "IDR") is False
        assert is_exempt_column("FX_Istoric", "IDRR") is False

    def test_an_ordinary_column_on_a_listed_table_is_not_exempt(self):
        # The exemption is per COLUMN, not per table -- FX_Istoric.Observatii
        # must still be diffed normally.
        assert is_exempt_column("FX_Istoric", "Observatii") is False
        assert is_exempt_column("FX_Plati", "Referinta_TREZOR") is False

    def test_unrelated_table_is_not_exempt(self):
        assert is_exempt_column("Clasificatii", "IDClsf") is False

    def test_none_is_not_exempt(self):
        # Never raise on a missing name; just answer "not exempt".
        assert is_exempt_column(None, "ID") is False
        assert is_exempt_column("FX_Istoric", None) is False
        assert is_exempt_column(None, None) is False


class TestTheVbListAgrees:
    """The SAME seven pairs exist twice, in two languages, describing one decision.

    KBot.Migrator applies the ALTER; schema_sync exempts the result. If the two
    lists ever drift, one database gets a key the sync then tries to "repair" --
    exactly the failure the exemption exists to prevent. KBot.Migrator has no
    test project of its own, so the pin lives here: reading the .vb file needs
    no compiler and no MariaDB.
    """

    VB_PATH = ("../../src/KBot.Migrator/MariaDb/AutoIncrementStep.vb")

    def _vb_pairs(self):
        import os
        import re

        here = os.path.dirname(os.path.abspath(__file__))
        path = os.path.normpath(os.path.join(here, self.VB_PATH))
        if not os.path.exists(path):
            import pytest
            pytest.skip(f"AutoIncrementStep.vb not found at {path}")
        with open(path, encoding="utf-8") as fh:
            source = fh.read()
        # New AutoIncrementTarget("FX_Istoric", "ID")
        rx = re.compile(r'New\s+AutoIncrementTarget\(\s*"([^"]+)"\s*,\s*"([^"]+)"\s*\)')
        return {(t, c) for t, c in rx.findall(source)}

    def test_vb_declares_the_same_seven_pairs(self):
        assert self._vb_pairs() == EXPECTED


class TestTheDiffUsesIt:
    def test_columns_pass_is_wired_to_the_helper(self):
        # A cheap structural guard: the exemption is worthless if _columns()
        # stops calling it. Reading the source is enough to catch a deletion,
        # and it needs no database.
        import inspect

        from routes.schema_sync import schema_diff

        src = inspect.getsource(schema_diff.SchemaDiff._columns)
        assert "is_exempt_column" in src
