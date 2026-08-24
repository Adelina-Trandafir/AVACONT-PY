# Offline unit tests for the column renderer of the schema tool
# (routes/schema_sync/schema_diff.column_definition). Run from the PYTHON folder:
#   python -m pytest tests/test_schema_diff_columns.py
#
# No config.py, no MariaDB: a Column is a plain dataclass filled from
# information_schema, and rendering it is pure.
#
# The failure they exist for (2026-08-23), on the first real run of
# POST /api/migrare/schema-sync against 000_DEMO:
#   [errno=1064] ... near 'STORED GENERATED,
#     `Trim1` double DEFAULT NULL, ...' at line 5
# from
#   CREATE TABLE `000_DEMO`.`Clasificatii_Buget` (
#     ...
#     `TOTAL` double STORED GENERATED,
#
# EXTRA reads "STORED GENERATED" for a generated column. That is a DESCRIPTION,
# and it was being appended as if it were syntax, the way auto_increment and
# "on update current_timestamp()" legitimately are -- while
# GENERATION_EXPRESSION, the only part that says what to generate, was never
# written at all. One table failed, the run stopped on the first error, and the
# other 88 statements never ran.
#
# The package README says generated columns are ignored "la comparație și la
# creare". Half of that was true: _columns() skips them on the ALTER path,
# _create_table() did not.

import sys
import types

# schema_diff reaches config.py only because schema_common imports DB_CONFIG for
# its connect(). Rendering a column touches neither, so this test has no business
# being host-only -- and a pure test that can only run on the production server is
# a test nobody runs. A stub stands in ONLY when there is no real config, so on
# the host the genuine one is still what gets imported.
#
# Deliberately local to this module rather than in conftest.py: the host-only
# tests guard themselves by catching the ImportError this would remove, and a
# global stub would turn their clean skip into a run against a server that is not
# there.
try:                                                # pragma: no cover - on host
    import config                                   # noqa: F401
except ImportError:
    _stub = types.ModuleType("config")
    _stub.DB_CONFIG = {"host": "unused", "port": 3306,
                       "user": "unused", "password": "unused"}
    _stub.API_KEY = "unused"
    sys.modules["config"] = _stub

from routes.schema_sync.schema_diff import column_definition  # noqa: E402
from routes.schema_sync.schema_introspect import Column       # noqa: E402


def col(**overrides):
    """A Column with the shape information_schema actually returns."""
    meta = dict(
        table="Clasificatii_Buget",
        name="TOTAL",
        ordinal=4,
        column_type="double",
        data_type="double",
        is_nullable=True,
        default=None,
        extra="",
        comment="",
        charset=None,
        collation=None,
        generation_expr="",
        expr_defaults=True,
    )
    meta.update(overrides)
    return Column(**meta)


# ---------------------------------------------------------------------
# Generated columns -- the regression
# ---------------------------------------------------------------------

def test_stored_generated_column_is_valid_ddl():
    sql = column_definition(col(
        extra="STORED GENERATED",
        generation_expr="`Trim1` + `Trim2` + `Trim3` + `Trim4`"))
    assert sql == ("`TOTAL` double GENERATED ALWAYS AS "
                   "(`Trim1` + `Trim2` + `Trim3` + `Trim4`) STORED")


def test_virtual_generated_column_keeps_its_kind():
    sql = column_definition(col(
        extra="VIRTUAL GENERATED", generation_expr="`Trim1` + `Trim2`"))
    assert sql.endswith(") VIRTUAL")


def test_persistent_is_read_as_stored():
    # MariaDB's older spelling for the same thing.
    sql = column_definition(col(extra="PERSISTENT",
                                generation_expr="`Trim1`"))
    assert sql.endswith(") STORED")


def test_generated_column_never_emits_extra_verbatim():
    # The exact 1064. "STORED GENERATED" must appear nowhere as syntax.
    sql = column_definition(col(extra="STORED GENERATED",
                                generation_expr="`Trim1`"))
    assert "STORED GENERATED" not in sql
    assert "GENERATED ALWAYS AS" in sql


def test_generated_column_takes_no_default_and_no_nullability():
    # Neither fits MariaDB's grammar between the type and the AS clause,
    # and a generated column's value never comes from a default.
    sql = column_definition(col(extra="STORED GENERATED", is_nullable=False,
                                generation_expr="`Trim1`"))
    assert "DEFAULT" not in sql
    assert "NOT NULL" not in sql


def test_generated_column_keeps_its_comment():
    sql = column_definition(col(extra="STORED GENERATED",
                                generation_expr="`Trim1`",
                                comment="suma trimestrelor"))
    assert sql.endswith(" COMMENT 'suma trimestrelor'")


# ---------------------------------------------------------------------
# Ordinary columns -- unchanged, and staying that way
# ---------------------------------------------------------------------
# These three are the other columns of the same failing CREATE TABLE, and
# they render exactly as the server accepted them before the fix.

def test_plain_nullable_column():
    assert column_definition(col(name="Trim1")) == "`Trim1` double DEFAULT NULL"


def test_auto_increment_still_appends_extra():
    sql = column_definition(col(name="IdBuget", column_type="int(11)",
                                data_type="int", is_nullable=False,
                                extra="auto_increment"))
    assert sql == "`IdBuget` int(11) NOT NULL auto_increment"


def test_on_update_still_appends_extra():
    sql = column_definition(col(name="DataModificare", column_type="datetime",
                                data_type="datetime",
                                extra="on update current_timestamp()"))
    assert sql == ("`DataModificare` datetime DEFAULT NULL "
                   "on update current_timestamp()")


def test_character_column_carries_charset():
    sql = column_definition(col(name="Denumire", column_type="varchar(255)",
                                data_type="varchar", charset="utf8mb3",
                                collation="utf8mb3_general_ci"))
    assert sql == ("`Denumire` varchar(255) CHARACTER SET utf8mb3 "
                   "COLLATE utf8mb3_general_ci DEFAULT NULL")
