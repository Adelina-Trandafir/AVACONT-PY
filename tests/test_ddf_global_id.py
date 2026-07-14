# Server-side tests for the interim global IDDF / IDREV logic (Access coexistence).
#
# Run from the PYTHON folder:  python -m pytest tests/test_ddf_global_id.py
#
# The scan list arrives from the client on the confirm payload as `db_asoc`
# (comma-separated DbNames the operator's accdb spans). The server unions the
# current db_name, validates every name, and takes MAX(id)+1 across the set.
#
# Two layers:
#   * Pure-logic tests (1-3) use a scripted fake cursor and run ANYWHERE — they
#     validate _q_db / _parse_scan_dbs / _next_global_id without a DB.
#   * Integration tests (4-6) drive the real commit path. They auto-discover a
#     second live tenant DB (NNN_* with an FX_DDF table) to scan alongside the
#     primary, and SKIP cleanly when no live DB / second tenant is present, so
#     the suite is always collectable.
import sys
import types
import uuid

import pytest


# ---------------------------------------------------------------------------
# Make routes.ddf.staging importable even without a real config.py / DB driver.
# The pure helpers under test don't need either, but importing the ddf package
# pulls utils.database -> config and mysql.connector. On the Flask host both
# exist and are used as-is; on a bare dev box we stub them so imports succeed.
# ---------------------------------------------------------------------------
def _ensure_importable():
    if 'config' not in sys.modules:
        try:
            import config  # noqa: F401
        except Exception:
            cfg = types.ModuleType('config')
            cfg.DB_CONFIG = {}
            cfg.API_KEY = 'test'
            sys.modules['config'] = cfg
    if 'mysql.connector' not in sys.modules:
        try:
            import mysql.connector  # noqa: F401
        except Exception:
            mysql_mod = types.ModuleType('mysql')
            conn_mod = types.ModuleType('mysql.connector')

            class _Err(Exception):
                pass

            conn_mod.Error = _Err

            def _no_connect(**_kw):
                raise RuntimeError('no db driver')

            conn_mod.connect = _no_connect
            mysql_mod.connector = conn_mod
            sys.modules['mysql'] = mysql_mod
            sys.modules['mysql.connector'] = conn_mod


_ensure_importable()

from routes.ddf import staging  # noqa: E402
from utils.database import get_db_connection  # noqa: E402


# ---------------------------------------------------------------------------
# Scripted fake cursor for the pure-logic tests.
# Records every executed SQL string; returns pre-queued fetchone/fetchall rows.
# Rows are dicts (matches the real dictionary=True cursors used in staging.py).
# ---------------------------------------------------------------------------
class FakeCursor:
    def __init__(self, fetchone_queue=None, fetchall_queue=None):
        self.executed = []
        self._fetchone = list(fetchone_queue or [])
        self._fetchall = list(fetchall_queue or [])

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return self._fetchone.pop(0)

    def fetchall(self):
        return self._fetchall.pop(0)

    @property
    def last_sql(self):
        return self.executed[-1][0]


# ===========================================================================
# 1) _q_db validation
# ===========================================================================
def test_q_db_accepts_valid_tenant_name():
    assert staging._q_db('000_DEMO') == '`000_DEMO`'
    assert staging._q_db('123_A_B_2024') == '`123_A_B_2024`'


@pytest.mark.parametrize('bad', [
    '000_DE`MO',       # backtick injection
    '000_DE MO',       # space
    "000_DE'MO",       # quote
    'DEMO',            # missing 3-digit prefix
    '00_DEMO',         # only 2 digits
    '000-DEMO',        # hyphen not allowed
    '000_DE;DROP',     # semicolon
    '',                # empty
    None,              # not a string
    123,               # not a string
])
def test_q_db_rejects_malformed_names(bad):
    with pytest.raises(ValueError):
        staging._q_db(bad)


# ===========================================================================
# 2) _parse_scan_dbs
# ===========================================================================
@pytest.mark.parametrize('db_asoc', [None, '', '   ', ',,', ' , , '])
def test_parse_scan_dbs_empty_returns_none(db_asoc):
    # Missing / effectively-empty db_asoc -> fall back to AUTO_INCREMENT.
    assert staging._parse_scan_dbs('000_DEMO', db_asoc) is None


def test_parse_scan_dbs_unions_current_db():
    assert staging._parse_scan_dbs('000_DEMO', '005_CEVM') == ['000_DEMO', '005_CEVM']


def test_parse_scan_dbs_dedupes_and_sorts_and_trims():
    got = staging._parse_scan_dbs('000_DEMO', ' 005_CEVM , 000_DEMO ,005_CEVM')
    assert got == ['000_DEMO', '005_CEVM']


def test_parse_scan_dbs_current_included_even_if_not_listed():
    got = staging._parse_scan_dbs('000_DEMO', '005_CEVM,009_X')
    assert got == ['000_DEMO', '005_CEVM', '009_X']


@pytest.mark.parametrize('db_asoc', ['bad name', '005_CEVM; DROP', '005_CEVM,xx', '00_SHORT'])
def test_parse_scan_dbs_malformed_entry_raises(db_asoc):
    with pytest.raises(ValueError):
        staging._parse_scan_dbs('000_DEMO', db_asoc)


def test_parse_scan_dbs_malformed_db_name_raises():
    with pytest.raises(ValueError):
        staging._parse_scan_dbs('bad name', '005_CEVM')


def test_parse_scan_dbs_non_string_raises():
    with pytest.raises(ValueError):
        staging._parse_scan_dbs('000_DEMO', ['005_CEVM'])


# ===========================================================================
# 3) _next_global_id shape
# ===========================================================================
def test_next_global_id_builds_union_all_over_scan_dbs():
    cur = FakeCursor(fetchone_queue=[{'nxt': 42}])
    nxt = staging._next_global_id(cur, ['000_A', '000_B'], 'FX_DDF', 'IDDF')

    assert nxt == 42
    sql = cur.last_sql
    # db-qualified, backtick-quoted MAX() per db, UNION ALL'd, wrapped in COALESCE+1
    assert 'SELECT MAX(IDDF) AS m FROM `000_A`.FX_DDF' in sql
    assert 'SELECT MAX(IDDF) AS m FROM `000_B`.FX_DDF' in sql
    assert 'UNION ALL' in sql
    assert 'COALESCE(MAX(m), 0) + 1 AS nxt' in sql


def test_next_global_id_single_db_no_union():
    cur = FakeCursor(fetchone_queue=[{'nxt': 7}])
    nxt = staging._next_global_id(cur, ['000_A'], 'FX_DDF_REV', 'IDREV')
    assert nxt == 7
    assert 'UNION ALL' not in cur.last_sql
    assert 'SELECT MAX(IDREV) AS m FROM `000_A`.FX_DDF_REV' in cur.last_sql


def test_next_global_id_empty_raises():
    with pytest.raises(ValueError):
        staging._next_global_id(FakeCursor(), [], 'FX_DDF', 'IDDF')


def test_next_global_id_validates_db_names():
    # A malformed scan-db name must be rejected by _q_db before any query runs.
    cur = FakeCursor(fetchone_queue=[{'nxt': 1}])
    with pytest.raises(ValueError):
        staging._next_global_id(cur, ['000_A', 'bad name'], 'FX_DDF', 'IDDF')


# ===========================================================================
# 3b) _existing_scan_dbs — missing databases are skipped, not fatal
# ===========================================================================
def test_existing_scan_dbs_drops_missing():
    # SCHEMATA reports only 000_DEMO present; 005_GONE must be dropped, not raise.
    cur = FakeCursor(fetchall_queue=[[{'SCHEMA_NAME': '000_DEMO'}]])
    got = staging._existing_scan_dbs(cur, ['000_DEMO', '005_GONE'])
    assert got == ['000_DEMO']
    # the existence probe is parameterized against information_schema.SCHEMATA
    assert 'information_schema.SCHEMATA' in cur.executed[0][0]
    assert tuple(cur.executed[0][1]) == ('000_DEMO', '005_GONE')


def test_existing_scan_dbs_keeps_all_present():
    cur = FakeCursor(fetchall_queue=[[{'SCHEMA_NAME': '000_DEMO'}, {'SCHEMA_NAME': '005_CEVM'}]])
    got = staging._existing_scan_dbs(cur, ['000_DEMO', '005_CEVM'])
    assert got == ['000_DEMO', '005_CEVM']


def test_existing_scan_dbs_passthrough_when_empty():
    # None / [] pass straight through without touching the DB.
    cur = FakeCursor()
    assert staging._existing_scan_dbs(cur, None) is None
    assert staging._existing_scan_dbs(cur, []) == []
    assert cur.executed == []


# ===========================================================================
# Live-environment helpers for integration tests (4-6)
# ===========================================================================
def _dict_cursor(conn):
    return conn.cursor(dictionary=True)


def _primary_db():
    return "000_DEMO"


def _discover_second_tenant(cur, primary):
    """Return another NNN_* database that has an FX_DDF table, or None.

    Used to build a real cross-DB scan (db_asoc) alongside the primary.
    """
    cur.execute("""
        SELECT DISTINCT TABLE_SCHEMA AS db
        FROM information_schema.TABLES
        WHERE TABLE_NAME = 'FX_DDF'
          AND TABLE_SCHEMA <> %s
          AND TABLE_SCHEMA REGEXP '^[0-9]{3}_'
        ORDER BY TABLE_SCHEMA
    """, (primary,))
    for r in cur.fetchall():
        db = r['db'] if isinstance(r, dict) else r[0]
        if staging._DBNAME_RE.match(db):
            return db
    return None


def _live_primary_conn():
    primary = _primary_db()
    try:
        conn = get_db_connection(primary)
    except Exception as e:
        pytest.skip(f"no live connection to {primary}: {e}")
    return primary, conn


# stg_DocFund / stg_Revizii carry the same business columns that flow into
# FX_DDF / FX_DDF_REV. Copying a real row via INSERT ... SELECT guarantees every
# NOT NULL column is satisfied without this test hard-coding the schema.
def _seed_add_stg(cur, token):
    """Seed staging for an ADD by copying a real FX_DDF + FX_DDF_REV row.
    Returns False (caller skips) if there's nothing to copy."""
    cur.execute("SELECT COUNT(*) AS n FROM FX_DDF")
    if not cur.fetchone()['n']:
        return False
    cur.execute("SELECT COUNT(*) AS n FROM FX_DDF_REV")
    if not cur.fetchone()['n']:
        return False
    cur.execute("""
        INSERT INTO stg_DocFund
            (Token, TipOperatie, IDDF,
             CodAngajament, Cual, DataCreare, DataDef, ObiectDDF, Program, Comp, Stare,
             PartAng, DC, Incarcat, Preluat, Salarii, CodFiscal, NumePartener)
        SELECT %s, 'ADD', 0,
             CodAngajament, Cual, DataCreare, DataDef, ObiectDDF, Program, Comp, Stare,
             PartAng, DC, Incarcat, Preluat, Salarii, CodFiscal, NumePartener
        FROM FX_DDF LIMIT 1
    """, (token,))
    cur.execute("""
        INSERT INTO stg_Revizii
            (Token, IDREV, IDDF, NumarRev, DataRev,
             Desc_Scurta, Desc_Lunga, Desc_Lunga_ANSI, DC, CodAngajament, Tip, Incarcat, Preluat)
        SELECT %s, 0, 0, NumarRev, DataRev,
             Desc_Scurta, Desc_Lunga, Desc_Lunga_ANSI, DC, CodAngajament, Tip, Incarcat, Preluat
        FROM FX_DDF_REV LIMIT 1
    """, (token,))
    return True


def _seed_mod_stg(cur, token, existing_iddf):
    """Seed staging for a MOD (new revision, IDREV<=0) on an existing DDF.
    Copies the target FX_DDF row (so the UPDATE is a no-op set) and a real
    FX_DDF_REV row. Returns False (caller skips) if there's no revision to copy."""
    cur.execute("SELECT COUNT(*) AS n FROM FX_DDF_REV")
    if not cur.fetchone()['n']:
        return False
    cur.execute("""
        INSERT INTO stg_DocFund
            (Token, TipOperatie, IDDF,
             CodAngajament, Cual, DataCreare, DataDef, ObiectDDF, Program, Comp, Stare,
             PartAng, DC, Incarcat, Preluat, Salarii, CodFiscal, NumePartener)
        SELECT %s, 'MOD', IDDF,
             CodAngajament, Cual, DataCreare, DataDef, ObiectDDF, Program, Comp, Stare,
             PartAng, DC, Incarcat, Preluat, Salarii, CodFiscal, NumePartener
        FROM FX_DDF WHERE IDDF = %s
    """, (token, existing_iddf))
    cur.execute("""
        INSERT INTO stg_Revizii
            (Token, IDREV, IDDF, NumarRev, DataRev,
             Desc_Scurta, Desc_Lunga, Desc_Lunga_ANSI, DC, CodAngajament, Tip, Incarcat, Preluat)
        SELECT %s, 0, %s, NumarRev, DataRev,
             Desc_Scurta, Desc_Lunga, Desc_Lunga_ANSI, DC, CodAngajament, Tip, Incarcat, Preluat
        FROM FX_DDF_REV LIMIT 1
    """, (token, existing_iddf))
    return True


# ===========================================================================
# 4) Add path — explicit global id = MAX+1 across the scan set
# ===========================================================================
def test_add_path_uses_global_max_plus_one():
    primary, conn = _live_primary_conn()
    try:
        cur = _dict_cursor(conn)
        other = _discover_second_tenant(cur, primary)
        if not other:
            pytest.skip("no second NNN_* tenant DB with FX_DDF to scan")
        scan_dbs = sorted({primary, other})

        # Expected next ids from the real cross-DB scan (same helper the code uses).
        expected_iddf = staging._next_global_id(cur, scan_dbs, 'FX_DDF', 'IDDF')
        expected_idrev = staging._next_global_id(cur, scan_dbs, 'FX_DDF_REV', 'IDREV')

        token = str(uuid.uuid4())
        if not _seed_add_stg(cur, token):
            pytest.skip("FX_DDF / FX_DDF_REV empty — nothing to copy for the staging seed")
        conn.commit()
        try:
            new_ids = staging._commit_staging_add(cur, token, scan_dbs)
            assert new_ids['IDDF'] == expected_iddf
            assert new_ids['IDREV'] == expected_idrev
            conn.rollback()  # never keep the real FX rows the test created
        finally:
            cur.execute("DELETE FROM stg_DocFund WHERE Token = %s", (token,))
            conn.commit()
    finally:
        conn.close()


# ===========================================================================
# 5) Add path — no db_asoc -> AUTO_INCREMENT fallback (current behavior)
# ===========================================================================
def test_add_path_fallback_uses_autoincrement():
    primary, conn = _live_primary_conn()
    try:
        cur = _dict_cursor(conn)

        cur.execute("SELECT COALESCE(MAX(IDDF), 0) AS m FROM FX_DDF")
        local_max_before = cur.fetchone()['m']

        token = str(uuid.uuid4())
        if not _seed_add_stg(cur, token):
            pytest.skip("FX_DDF / FX_DDF_REV empty — nothing to copy for the staging seed")
        conn.commit()
        try:
            # scan_dbs=None -> AUTO_INCREMENT path (what _parse_scan_dbs returns
            # for an absent db_asoc).
            new_ids = staging._commit_staging_add(cur, token, None)
            # AUTO id is assigned locally: strictly greater than the prior local MAX.
            assert new_ids['IDDF'] > local_max_before
            conn.rollback()
        finally:
            cur.execute("DELETE FROM stg_DocFund WHERE Token = %s", (token,))
            conn.commit()
    finally:
        conn.close()


# ===========================================================================
# 7) Add path — a non-existent db in the scan set is skipped, commit survives
#    (needs only the primary DB: the missing name is intentional)
# ===========================================================================
def test_add_path_skips_nonexistent_scan_db():
    primary, conn = _live_primary_conn()
    try:
        cur = _dict_cursor(conn)
        # '999_NOPE_DDF' is a validly-shaped name that does not exist on the server.
        bogus = '999_NOPE_DDF'
        cur.execute(
            "SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = %s",
            (bogus,),
        )
        if cur.fetchone():
            pytest.skip(f"{bogus} unexpectedly exists on this server")

        scan_dbs = sorted({primary, bogus})
        # After filtering the bogus db, the scan collapses to the primary only.
        expected_iddf = staging._next_global_id(cur, [primary], 'FX_DDF', 'IDDF')

        token = str(uuid.uuid4())
        if not _seed_add_stg(cur, token):
            pytest.skip("FX_DDF / FX_DDF_REV empty — nothing to copy for the staging seed")
        conn.commit()
        try:
            # Must NOT raise despite bogus in the scan set; id comes from primary.
            new_ids = staging._commit_staging_add(cur, token, scan_dbs)
            assert new_ids['IDDF'] == expected_iddf
            conn.rollback()
        finally:
            cur.execute("DELETE FROM stg_DocFund WHERE Token = %s", (token,))
            conn.commit()
    finally:
        conn.close()


# ===========================================================================
# 6) Mod path — new revision (IDREV <= 0) gets an explicit global id
# ===========================================================================
def test_mod_path_new_revision_uses_global_id():
    primary, conn = _live_primary_conn()
    try:
        cur = _dict_cursor(conn)
        other = _discover_second_tenant(cur, primary)
        if not other:
            pytest.skip("no second NNN_* tenant DB with FX_DDF to scan")
        scan_dbs = sorted({primary, other})

        # Need an existing FX_DDF to attach the new revision to.
        cur.execute("SELECT MAX(IDDF) AS m FROM FX_DDF")
        existing_iddf = cur.fetchone()['m']
        if not existing_iddf:
            pytest.skip("no existing FX_DDF row to attach a new revision to")

        expected_idrev = staging._next_global_id(cur, scan_dbs, 'FX_DDF_REV', 'IDREV')

        token = str(uuid.uuid4())
        # IDREV <= 0 (set inside _seed_mod_stg) marks a brand-new revision.
        if not _seed_mod_stg(cur, token, existing_iddf):
            pytest.skip("FX_DDF_REV empty — nothing to copy for the staging seed")
        conn.commit()
        try:
            new_ids = staging._commit_staging_mod(cur, token, scan_dbs)
            assert new_ids['IDREV'] == expected_idrev
            conn.rollback()
        finally:
            cur.execute("DELETE FROM stg_DocFund WHERE Token = %s", (token,))
            conn.commit()
    finally:
        conn.close()
