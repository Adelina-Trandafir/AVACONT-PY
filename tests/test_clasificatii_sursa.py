# Offline unit tests for scripts/clasificatii_sursa.py (slice 0075-00).
# Run from the PYTHON folder:
#   python -m pytest tests/test_clasificatii_sursa.py
#
# WHAT THEY GUARD. The script turns Clasificatii.Sursa from a GENERATED column into a
# written one, on the template and on every unit database. It cannot be tried out: the
# developer has no MariaDB. So everything that can be decided without a server is
# decided here -- the SQL text, which databases are picked, and every refusal that
# must stop a run before it touches a table.
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

def _state(**kw):
    base = dict(db="000_DEMO", exists=True, rows=10, sursa_generated=True,
                sector_extended=False, ss_fk_name="Clasificatii__DefaSS",
                ss_index_name="idx_SS", leftovers=())
    base.update(kw)
    return cs.TableState(**base)


SCHEMATA = ["AVACONT_COMUN", "AVACONT_SURSA", "000_DEMO", "001_GR23", "101_CCDP",
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

def test_single_alter_has_every_clause_in_order():
    stmts = cs.alter_statements(_state())
    assert len(stmts) == 1
    sql = stmts[0]
    # The order matters: the foreign key must go before the column it points at.
    assert sql.index("DROP FOREIGN KEY") < sql.index("DROP COLUMN `SS`")
    assert sql.index("DROP COLUMN `SS`") < sql.index("DROP COLUMN `Sursa`")
    assert sql.index("DROP COLUMN `Sursa`") < sql.index("CHANGE COLUMN `Sursa_w`")
    assert sql.index("CHANGE COLUMN `Sursa_w`") < sql.index("ADD COLUMN `SS`")
    assert sql.index("ADD COLUMN `SS`") < sql.index("ADD CONSTRAINT")


def test_sursa_becomes_not_null_char_with_default_a():
    sql = cs.alter_statements(_state())[0]
    assert "CHANGE COLUMN `Sursa_w` `Sursa` char(1) NOT NULL DEFAULT 'A'" in sql


def test_ss_stays_generated_and_keeps_its_foreign_key():
    sql = cs.alter_statements(_state())[0]
    assert "ADD COLUMN `SS` varchar(3) AS (concat(" in sql
    assert "STORED" in sql
    assert "`AVACONT_COMUN`.`DefaSursaSector` (`SursaSector`)" in sql


def test_sector_case_covers_the_eight_endings():
    sql = cs.alter_statements(_state())[0]
    # The four historical endings keep their old mapping ...
    for ending, sector in (("'00'", "'01'"), ("'01'", "'01'"),
                           ("'02'", "'02'"), ("'10'", "'02'")):
        assert f"when {ending} then {sector}" in sql
    # ... and the four new ones map to themselves.
    for ending in ("'03'", "'04'", "'05'", "'08'"):
        assert f"when {ending} then {ending}" in sql


def test_split_alter_is_the_same_clauses_in_three_statements():
    one = cs.alter_statements(_state())[0]
    three = cs.alter_statements(_state(), split=True)
    assert len(three) == 3
    for clause in ("DROP FOREIGN KEY", "CHANGE COLUMN `Sursa_w`", "ADD CONSTRAINT"):
        assert clause in one
        assert any(clause in s for s in three)


def test_missing_index_on_ss_drops_no_key():
    sql = cs.alter_statements(_state(ss_index_name=None))[0]
    assert "DROP KEY" not in sql
    assert "ADD KEY `idx_SS`" in sql          # it is added back either way


def test_index_is_dropped_under_the_name_the_server_uses():
    sql = cs.alter_statements(_state(ss_index_name="SS_2"))[0]
    assert "DROP KEY `SS_2`" in sql


def test_foreign_key_is_dropped_under_the_name_the_server_uses():
    sql = cs.alter_statements(_state(ss_fk_name="Clasificatii_ibfk_4"))[0]
    assert "DROP FOREIGN KEY `Clasificatii_ibfk_4`" in sql


def test_no_foreign_key_on_ss_is_refused():
    with pytest.raises(cs.MigrationError) as err:
        cs.alter_statements(_state(ss_fk_name=None))
    assert "cheie străină" in str(err.value)


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
    assert dbs[1:] == ["000_DEMO", "001_GR23", "101_CCDP"]


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
    assert cs.migrate_database(object(), "000_DEMO", "backup", False, lambda _m: None) == "skipped"


def test_already_migrated_database_is_skipped(monkeypatch):
    monkeypatch.setattr(cs, "inspect",
                        lambda conn, db: _state(db=db, sursa_generated=False,
                                                sector_extended=True))
    monkeypatch.setattr(cs, "dump_table", _no_write)
    monkeypatch.setattr(cs, "execute", _no_write)
    assert cs.migrate_database(object(), "000_DEMO", "backup", False, lambda _m: None) == "skipped"


def test_leftovers_from_a_failed_run_stop_the_database(monkeypatch):
    monkeypatch.setattr(cs, "inspect",
                        lambda conn, db: _state(db=db, leftovers=("_snap_clsf",)))
    monkeypatch.setattr(cs, "dump_table", _no_write)
    monkeypatch.setattr(cs, "execute", _no_write)
    with pytest.raises(cs.MigrationError) as err:
        cs.migrate_database(object(), "000_DEMO", "backup", False, lambda _m: None)
    assert "_snap_clsf" in str(err.value)


def test_half_migrated_database_stops_the_run(monkeypatch):
    # Sursa already written but Sector still on the old CASE: nobody knows what happened
    # in between, so the script refuses rather than guessing.
    monkeypatch.setattr(cs, "inspect",
                        lambda conn, db: _state(db=db, sursa_generated=False,
                                                sector_extended=False))
    monkeypatch.setattr(cs, "dump_table", _no_write)
    monkeypatch.setattr(cs, "execute", _no_write)
    with pytest.raises(cs.MigrationError) as err:
        cs.migrate_database(object(), "000_DEMO", "backup", False, lambda _m: None)
    assert "intermediar" in str(err.value)


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
    assert "ADD COLUMN `Sursa_w` char(1) NULL" in joined
    assert "SET `Sursa_w` = `Sursa`" in joined
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
