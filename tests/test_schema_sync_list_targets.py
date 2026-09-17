# Offline unit tests for the target listing
# (routes/schema_sync/schema_common.list_unit_databases and the
# --list-targets branch of schema_sync.main).
# Run from the PYTHON folder:
#   python -m pytest tests/test_schema_sync_list_targets.py
#
# WHY THIS EXISTS: AvacontPush fills its database list by running
# `schema_sync --list-targets` over SSH and parsing stdout. Two things must
# hold or that list silently lies to the operator:
#
#   1. The list comes from the SERVER (information_schema), not from the CAI
#      registry, with a flag saying whether CAI knows each database -- a unit
#      missing from the registry has to be VISIBLE, not absent.
#   2. Listing must happen before ensure_control_table / check_prerequisites.
#      Merely asking what exists must not create a control table, and must not
#      fail on a server whose sql_mode would refuse a real sync.
import sys
import types

# Same stub pattern as test_schema_sync_exempt_columns.py: schema_common imports
# config.DB_CONFIG for its connect(), which these tests never reach. The stub
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

from routes.schema_sync import schema_common          # noqa: E402
from routes.schema_sync import schema_sync            # noqa: E402


# --------------------------------------------------------------- helpers

SCHEMATA = [
    "AVACONT_COMUN", "AVACONT_SURSA", "000_DEMO", "001_PRIMARIA",
    "007_Scoala", "FX_TEST", "mysql", "information_schema",
    "performance_schema", "sys", "12_prea_scurt", "202_unitate_noua",
]

CAI = ["000_DEMO", "001_primaria", "007_Scoala", "099_INEXISTENTA"]


def _fake_query(schemata=None, cai=None):
    """Stands in for schema_common.query: answers by the SQL it is given."""
    schemata = SCHEMATA if schemata is None else schemata
    cai = CAI if cai is None else cai

    def run(conn, sql, params=()):
        if "information_schema.SCHEMATA" in sql:
            return [{"name": n} for n in sorted(schemata)]
        if "CAI" in sql:
            return [{"DbName": n} for n in cai]
        raise AssertionError(f"unexpected query: {sql}")

    return run


# --------------------------------------------------------------- the list

def test_only_three_digit_prefixes_are_listed(monkeypatch):
    monkeypatch.setattr(schema_common, "query", _fake_query())
    listed = schema_common.list_unit_databases(object())
    on_server = [d["name"] for d in listed if d["exists"]]
    assert on_server == ["000_DEMO", "001_PRIMARIA", "007_Scoala",
                         "202_unitate_noua"]


def test_template_registry_and_server_schemas_are_never_listed(monkeypatch):
    monkeypatch.setattr(schema_common, "query", _fake_query())
    names = {d["name"] for d in schema_common.list_unit_databases(object())}
    assert names.isdisjoint(schema_common.FORBIDDEN_TARGETS)
    assert "FX_TEST" not in names          # no three-digit prefix
    assert "12_prea_scurt" not in names    # only two digits


def test_cai_membership_is_reported_case_insensitively(monkeypatch):
    monkeypatch.setattr(schema_common, "query", _fake_query())
    flags = {d["name"]: d["in_cai"]
             for d in schema_common.list_unit_databases(object())}
    assert flags["000_DEMO"] is True
    assert flags["001_PRIMARIA"] is True   # CAI spells it "001_primaria"
    assert flags["007_Scoala"] is True
    # On the server but absent from the registry: listed, and flagged.
    assert flags["202_unitate_noua"] is False


def test_database_in_cai_but_absent_from_the_server_is_listed_as_missing(monkeypatch):
    # Shown, never silently dropped, and marked so the caller can disable it:
    # a CAI row pointing at nothing is the thing worth seeing.
    monkeypatch.setattr(schema_common, "query", _fake_query())
    listed = {d["name"]: d for d in schema_common.list_unit_databases(object())}
    assert listed["099_INEXISTENTA"]["exists"] is False
    assert listed["099_INEXISTENTA"]["in_cai"] is True
    # Everything the server really has stays selectable.
    assert listed["000_DEMO"]["exists"] is True


def test_a_cai_row_naming_a_non_unit_schema_is_not_reported_missing(monkeypatch):
    monkeypatch.setattr(schema_common, "query",
                        _fake_query(cai=["AVACONT_COMUN", "000_DEMO"]))
    listed = {d["name"]: d for d in schema_common.list_unit_databases(object())}
    assert "AVACONT_COMUN" not in listed


# --------------------------------------------------------------- the CLI

class _FakeConn:
    def is_connected(self):
        return True

    def close(self):
        pass


def _arm_cli(monkeypatch, listed):
    """--list-targets must reach nothing but connect() and the listing."""
    monkeypatch.setattr(schema_sync, "connect", lambda: _FakeConn())
    monkeypatch.setattr(schema_sync, "list_unit_databases", lambda conn: listed)

    def refuse(*args, **kwargs):
        raise AssertionError("--list-targets must not touch the database")

    monkeypatch.setattr(schema_sync, "ensure_control_table", refuse)
    monkeypatch.setattr(schema_sync, "check_prerequisites", refuse)
    monkeypatch.setattr(schema_sync, "generate", refuse)


def test_list_targets_prints_one_marked_line_per_database(monkeypatch, capsys):
    _arm_cli(monkeypatch,
             [{"name": "000_DEMO", "in_cai": True, "exists": True},
              {"name": "202_unitate_noua", "in_cai": False, "exists": True},
              {"name": "099_INEXISTENTA", "in_cai": True, "exists": False}])

    assert schema_sync.main(["--list-targets"]) == 0

    lines = [ln for ln in capsys.readouterr().out.splitlines()
             if ln.startswith(schema_sync.TARGET_LINE_PREFIX + "\t")]
    assert lines == ["DB\t000_DEMO\tCAI\tEXISTS",
                     "DB\t202_unitate_noua\t-\tEXISTS",
                     "DB\t099_INEXISTENTA\tCAI\tMISSING"]


def test_list_targets_on_an_empty_server_still_succeeds(monkeypatch, capsys):
    _arm_cli(monkeypatch, [])
    assert schema_sync.main(["--list-targets"]) == 0
    assert [ln for ln in capsys.readouterr().out.splitlines()
            if ln.startswith("DB\t")] == []
