# Offline unit tests for scripts/clasificatii_sursa.py (slice 0075-00).
# Run from the PYTHON folder:
#   python -m pytest tests/test_clasificatii_sursa.py
#
# WHAT THEY GUARD. The script turns Clasificatii.Sector, Sursa and SS from GENERATED
# columns into written ones, on the template and on every unit database. It cannot be tried
# out: the developer has no MariaDB. So everything that can be decided without a server is
# decided here -- the SQL text, which databases are picked, and every refusal that must stop
# a run before it touches a table.
#
# Nothing in this file connects anywhere: `connect`, `query` and `execute` are replaced.
import sys
import types

try:                                                # pragma: no cover - on the host
    import config                                   # noqa: F401
except ImportError:
    _stub = types.ModuleType("config")
    _stub.DB_CONFIG = {"host": "unused", "port": 3306,
                       "user": "unused", "password": "unused"}
    _stub.DB_CONFIG_NEW = dict(_stub.DB_CONFIG)
    _stub.API_KEY = "unused"
    sys.modules["config"] = _stub

import pytest                                       # noqa: E402

from scripts import clasificatii_sursa as cs        # noqa: E402


# --------------------------------------------------------------- helpers

ALL_THREE = ("Sector", "Sursa", "SS")

# Where the three sit in the real table, so the ALTER can put them back.
POSITIONS = {"Sector": "ClsfX", "Sursa": "Sector", "SS": "Sursa"}


def _state(**kw):
    base = dict(db="000_DEMO", exists=True, rows=10, generated=ALL_THREE,
                ss_fk_name="Clasificatii__DefaSS", ss_index_name="idx_SS",
                leftovers=(), positions=dict(POSITIONS))
    base.update(kw)
    return cs.TableState(**base)


SCHEMATA = ["AVACONT_COMUN", "AVACONT_SURSA", "000_DEMO", "006_GR35", "050_GRSA",
            "mysql", "information_schema", "performance_schema", "sys", "FX_TEST"]


def _fake_query(schemata=None):
    """Stands in for schema_common.query: answers by the SQL it is given."""
    schemata = SCHEMATA if schemata is None else schemata

    def run(conn, sql, params=()):
        if "information_schema.SCHEMATA" in sql:
            return [{"name": n} for n in sorted(schemata)]
        raise AssertionError(f"unexpected query: {sql}")
    return run


# --------------------------------------------------------------- the ALTER text

def test_single_alter_drops_all_three_generated_columns():
    sql = cs.alter_statements(_state())[0]
    for column in ALL_THREE:
        assert f"DROP COLUMN `{column}`" in sql


def test_foreign_key_goes_before_the_column_it_points_at():
    sql = cs.alter_statements(_state())[0]
    assert sql.index("DROP FOREIGN KEY") < sql.index("DROP COLUMN `SS`")
    assert sql.index("DROP COLUMN `SS`") < sql.index("CHANGE COLUMN `SS_w`")
    assert sql.index("CHANGE COLUMN `SS_w`") < sql.index("ADD CONSTRAINT")


def test_no_generated_expression_is_created():
    # The whole point of the rework: MariaDB 10.11 refused `concat(<case>, Sursa)` with
    # error 1901, in a FRESH table, so nothing the script writes may be GENERATED.
    sql = cs.alter_statements(_state())[0]
    assert "GENERATED" not in sql.upper()
    assert "STORED" not in sql.upper()
    assert " AS (" not in sql


def test_the_three_columns_get_their_declared_types():
    sql = cs.alter_statements(_state())[0]
    assert "CHANGE COLUMN `Sector_w` `Sector` varchar(2) NOT NULL DEFAULT ''" in sql
    assert "CHANGE COLUMN `Sursa_w` `Sursa` char(1) NOT NULL DEFAULT 'A'" in sql
    assert "CHANGE COLUMN `SS_w` `SS` varchar(3) NOT NULL" in sql


def test_ss_gets_no_default_so_a_writer_that_forgets_it_fails_loudly():
    sql = cs.alter_statements(_state())[0]
    ss_clause = [c for c in sql.split(", ") if "`SS_w` `SS`" in c][0]
    assert "DEFAULT" not in ss_clause


def test_columns_are_put_back_where_they_were():
    sql = cs.alter_statements(_state())[0]
    assert "`Sector` varchar(2) NOT NULL DEFAULT '' AFTER `ClsfX`" in sql
    assert "`Sursa` char(1) NOT NULL DEFAULT 'A' AFTER `Sector`" in sql
    assert "`SS` varchar(3) NOT NULL AFTER `Sursa`" in sql


def test_a_table_with_a_different_column_order_is_respected():
    # The unit databases were not all built at once; the column before Sector may differ.
    sql = cs.alter_statements(_state(positions={"Sector": "Clsf", "Sursa": "Sector",
                                                "SS": "Sursa"}))[0]
    assert "`Sector` varchar(2) NOT NULL DEFAULT '' AFTER `Clsf`" in sql


def test_a_position_naming_a_dropped_column_falls_back_to_the_template_layout():
    # Sector sitting after SS would name a column the same statement drops.
    sql = cs.alter_statements(_state(positions={"Sector": "SS", "Sursa": "Sector",
                                                "SS": "Sursa"}))[0]
    assert "`Sector` varchar(2) NOT NULL DEFAULT '' AFTER `ClsfX`" in sql


def test_the_index_and_the_foreign_key_come_back():
    sql = cs.alter_statements(_state())[0]
    assert "ADD KEY `idx_SS` (`SS`)" in sql
    assert "`AVACONT_COMUN`.`DefaSursaSector` (`SursaSector`)" in sql


def test_split_alter_is_the_same_clauses_in_three_statements():
    one = cs.alter_statements(_state())[0]
    three = cs.alter_statements(_state(), split=True)
    assert len(three) == 3
    for clause in ("DROP FOREIGN KEY", "CHANGE COLUMN `SS_w`", "ADD CONSTRAINT"):
        assert clause in one
        assert any(clause in s for s in three)


def test_missing_index_on_ss_drops_no_key():
    sql = cs.alter_statements(_state(ss_index_name=None))[0]
    assert "DROP KEY" not in sql
    assert "ADD KEY `idx_SS`" in sql          # it is added back either way


def test_index_and_key_are_dropped_under_the_names_the_server_uses():
    sql = cs.alter_statements(_state(ss_index_name="SS_2",
                                     ss_fk_name="Clasificatii_ibfk_4"))[0]
    assert "DROP KEY `SS_2`" in sql
    assert "DROP FOREIGN KEY `Clasificatii_ibfk_4`" in sql


def test_no_foreign_key_on_ss_is_refused():
    with pytest.raises(cs.MigrationError) as err:
        cs.alter_statements(_state(ss_fk_name=None))
    assert "cheie străină" in str(err.value)


# --------------------------------------------------------------- the copy step

def test_temp_columns_are_nullable_while_they_are_temporary():
    add, update = cs.temp_column_statements("000_DEMO")
    for column in ALL_THREE:
        assert f"ADD COLUMN `{column}_w`" in add
        assert f"`{column}_w` = `{column}`" in update
    assert "NOT NULL" not in add               # filled by the UPDATE, tightened by the ALTER


# --------------------------------------------------------------- verification SQL

def test_verify_compares_all_three_columns_null_safely():
    _, _, diff = cs.verify_sql("000_DEMO")
    assert "c.Sector <=> s.Sector" in diff
    assert "c.Sursa <=> s.Sursa" in diff
    assert "c.SS <=> s.SS" in diff
    assert "`000_DEMO`.`_snap_clsf`" in diff


# --------------------------------------------------------------- target list

def test_template_comes_first_then_the_unit_databases(monkeypatch):
    monkeypatch.setattr(cs, "query", _fake_query())
    dbs = cs.list_databases(object())
    assert dbs[0] == "AVACONT_SURSA"
    assert dbs[1:] == ["000_DEMO", "006_GR35", "050_GRSA"]


def test_common_and_system_databases_are_never_targets(monkeypatch):
    monkeypatch.setattr(cs, "query", _fake_query())
    dbs = cs.list_databases(object())
    for forbidden in ("AVACONT_COMUN", "mysql", "sys", "information_schema",
                      "performance_schema", "FX_TEST"):
        assert forbidden not in dbs


def test_missing_template_stops_the_run(monkeypatch):
    monkeypatch.setattr(cs, "query", _fake_query(schemata=["000_DEMO", "mysql"]))
    with pytest.raises(cs.MigrationError) as err:
        cs.list_databases(object())
    assert "AVACONT_SURSA" in str(err.value)


# --------------------------------------------------------------- refusals

def _no_write(*_a, **_k):
    raise AssertionError("migrate_database must not write in this case")


def test_database_without_the_table_is_skipped(monkeypatch):
    monkeypatch.setattr(cs, "inspect", lambda conn, db: _state(db=db, exists=False))
    monkeypatch.setattr(cs, "dump_table", _no_write)
    monkeypatch.setattr(cs, "execute", _no_write)
    assert cs.migrate_database(object(), "000_DEMO", "backup", False,
                               lambda _m: None) == "skipped"


def test_already_migrated_database_is_skipped(monkeypatch):
    monkeypatch.setattr(cs, "inspect", lambda conn, db: _state(db=db, generated=()))
    monkeypatch.setattr(cs, "dump_table", _no_write)
    monkeypatch.setattr(cs, "execute", _no_write)
    assert cs.migrate_database(object(), "000_DEMO", "backup", False,
                               lambda _m: None) == "skipped"


def test_half_migrated_database_stops_the_run(monkeypatch):
    # One of the three still generated: nobody knows what happened in between, so the
    # script refuses rather than guessing.
    monkeypatch.setattr(cs, "inspect", lambda conn, db: _state(db=db, generated=("SS",)))
    monkeypatch.setattr(cs, "dump_table", _no_write)
    monkeypatch.setattr(cs, "execute", _no_write)
    with pytest.raises(cs.MigrationError) as err:
        cs.migrate_database(object(), "000_DEMO", "backup", False, lambda _m: None)
    assert "intermediar" in str(err.value)


def test_leftovers_stop_the_database_unless_asked_to_clean(monkeypatch):
    monkeypatch.setattr(cs, "inspect",
                        lambda conn, db: _state(db=db, leftovers=("SS_w", "_snap_clsf")))
    monkeypatch.setattr(cs, "dump_table", _no_write)
    monkeypatch.setattr(cs, "execute", _no_write)
    with pytest.raises(cs.MigrationError) as err:
        cs.migrate_database(object(), "000_DEMO", "backup", False, lambda _m: None)
    assert "--clean-leftovers" in str(err.value)


def test_cleanup_drops_the_temporary_columns_and_the_snapshot():
    stmts = cs.cleanup_statements(_state(leftovers=("Sector_w", "SS_w", "_snap_clsf")))
    joined = "\n".join(stmts)
    assert "DROP COLUMN `Sector_w`" in joined
    assert "DROP COLUMN `SS_w`" in joined
    assert "DROP TABLE `000_DEMO`.`_snap_clsf`" in joined
    # Never the real columns.
    for column in ALL_THREE:
        assert f"DROP COLUMN `{column}`" not in joined


# --------------------------------------------------------------- the happy path

class _Recorder:
    """Collects every statement `execute` is given, and answers the three counts."""

    def __init__(self, rows=7, diff=0):
        self.statements = []
        self._rows = rows
        self._diff = diff

    def execute(self, conn, sql, params=()):
        self.statements.append(sql)

    def query(self, conn, sql, params=()):
        if "NOT (" in sql:
            return [{"n": self._diff}]
        return [{"n": self._rows}]


def test_successful_run_snapshots_copies_alters_verifies_and_drops(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(cs, "inspect", lambda conn, db: _state(db=db))
    monkeypatch.setattr(cs, "dump_table", lambda db, d, say: f"{d}/{db}.sql")
    monkeypatch.setattr(cs, "execute", rec.execute)
    monkeypatch.setattr(cs, "query", rec.query)

    assert cs.migrate_database(object(), "000_DEMO", "backup", False,
                               lambda _m: None) == "migrated"
    joined = "\n".join(rec.statements)
    assert "CREATE TABLE `000_DEMO`.`_snap_clsf`" in joined
    assert "ADD COLUMN `SS_w`" in joined
    assert "`SS_w` = `SS`" in joined
    assert "DROP FOREIGN KEY" in joined
    # The snapshot goes only at the very end, after the verification passed.
    assert rec.statements[-1] == "DROP TABLE `000_DEMO`.`_snap_clsf`"


def test_a_single_differing_row_keeps_the_snapshot_and_stops(monkeypatch):
    rec = _Recorder(diff=1)
    monkeypatch.setattr(cs, "inspect", lambda conn, db: _state(db=db))
    monkeypatch.setattr(cs, "dump_table", lambda db, d, say: f"{d}/{db}.sql")
    monkeypatch.setattr(cs, "execute", rec.execute)
    monkeypatch.setattr(cs, "query", rec.query)

    with pytest.raises(cs.MigrationError) as err:
        cs.migrate_database(object(), "000_DEMO", "backup", False, lambda _m: None)
    message = str(err.value)
    assert "verificarea a eșuat" in message
    assert "Restaurare:" in message             # the operator is told how to go back
    assert "DROP TABLE `000_DEMO`.`_snap_clsf`" not in rec.statements


def test_restore_command_never_shows_the_password():
    line = cs.restore_command("000_DEMO", "backup/000_DEMO_Clasificatii_x.sql")
    assert "--password=***" in line
    assert cs.DB_CONFIG_NEW["password"] not in line
