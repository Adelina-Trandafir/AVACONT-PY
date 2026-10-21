"""
admin_demo.py -- empty 000_DEMO and clone another unit database into it (KBotAdmin, slice ME01-04).

    demo-clone-plan --from DB     READ   what would happen: tables, row counts, the columns that get rewritten, warnings
    demo-clone      --from DB     WRITE  dump 000_DEMO, empty it, copy DB into it, rewrite the identity columns

The identity of 000_DEMO stays in AVACONT_COMUN (Unitati, Unitati_Ani, Unitati_Utilizatori, CAI ...) and is never
touched here. What changes is only inside the 000_DEMO schema:

  * every column named DC holds '000_DEMO' (NULL stays NULL), whatever the source held;
  * the tax code of the source unit becomes the tax code of 000_DEMO (AVACONT_COMUN.Unitati.CF) in every
    column that holds a tax code (name = CF / CUI / CIF / CodFiscal* / *_cui, text type), wherever the value
    equals the source unit's code -- a supplier's or a client's code is NOT touched. Two columns are about
    the unit itself and are set whatever they hold: EF_Furnizor.CodFiscal keeps the value 000_DEMO had before
    the wipe, and EF_Mesaje.CuiUnitate (the unit that received the message) takes the 000_DEMO code.
    A value written with the «RO» prefix keeps it.

Everything runs in ONE transaction: a failure (a key clash, a column that does not fit) leaves 000_DEMO exactly
as it was. The dump of 000_DEMO is taken first and the run stops if it cannot be taken.

Not rewritten (no honest way to do it): codes inside XML / PDF / image blobs and inside free text.
Not copied: the three lock tables (they hold the other database's live locks, nothing else).

Identifiers from the database are quoted; the only identifier from the command line is the source name, which
is resolved against the server's own list of unit databases first.
"""
import re

from routes.admin_cli.admin_common import AdminError, _journal, _q, _quiet_logger, _resolve_db, _rows
from routes.schema_sync.schema_common import COMMON_DB, connect

TARGET_DB = "000_DEMO"
BACKUP_DIR = "backup"
LOCK_NAME = "kbot_demo_clone"

# Transient lock tables of the application: a copy would only carry the source's live locks.
SKIP_DATA = {"fx_numberlock", "ad_lock", "vc_contextlock"}

# Names of the columns that hold a tax code (the type is checked too).
_RX_CF_NAME = re.compile(r"^(cf|cui|cif|cod_?fiscal\w*|cui\w*|cif\w*|\w+_cui)$", re.IGNORECASE)
_TEXT_TYPES = {"char", "varchar", "tinytext", "text", "mediumtext", "longtext"}

# Columns that describe the unit itself: set whatever they hold (not only where they equal the source code).
OWN_BY_VALUE = {("ef_mesaje", "cuiunitate")}          # takes the 000_DEMO code
OWN_KEEP_DEMO = {("ef_furnizor", "codfiscal")}        # keeps what 000_DEMO had before the wipe


def _digits(cf):
    """A tax code reduced to what is compared: upper case, no spaces, no «RO» prefix."""
    return re.sub(r"^RO", "", re.sub(r"\s+", "", (cf or "").upper()))


def _norm_sql(column):
    return "REGEXP_REPLACE(UPPER(TRIM(%s)),'^RO','')" % _q(column)


# ---------------------------------------------------------------------------
# Reading the two schemas
# ---------------------------------------------------------------------------
def _unit_cf(conn, dc):
    rows = _rows(conn, "SELECT CF FROM `%s`.`Unitati` WHERE DC = %%s" % COMMON_DB, (dc,))
    return (rows[0]["CF"] or "").strip() if rows else ""


def _tables(conn, schema):
    """{lower name: (name, engine)} of the base tables of a schema."""
    return {r["n"].casefold(): (r["n"], r["e"] or "") for r in _rows(
        conn, "SELECT TABLE_NAME AS n, ENGINE AS e FROM information_schema.TABLES "
              "WHERE TABLE_SCHEMA = %s AND TABLE_TYPE = 'BASE TABLE'", (schema,))}


def _columns(conn, schema):
    """{lower table: [column dicts in table order]}."""
    out = {}
    for r in _rows(conn,
                   "SELECT TABLE_NAME AS t, COLUMN_NAME AS c, DATA_TYPE AS d, IS_NULLABLE AS nl, COLUMN_DEFAULT AS df, "
                   "EXTRA AS x FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = %s "
                   "ORDER BY TABLE_NAME, ORDINAL_POSITION", (schema,)):
        out.setdefault(r["t"].casefold(), []).append(
            {"name": r["c"], "type": (r["d"] or "").lower(), "nullable": r["nl"] == "YES", "default": r["df"],
             "extra": (r["x"] or "").lower()})
    return out


def _count(conn, schema, table):
    return int(_rows(conn, "SELECT COUNT(*) AS n FROM %s.%s" % (_q(schema), _q(table)))[0]["n"])


def _is_cf_column(table, column):
    key = (table.casefold(), column["name"].casefold())
    if key in OWN_BY_VALUE or key in OWN_KEEP_DEMO:
        return True
    return column["type"] in _TEXT_TYPES and bool(_RX_CF_NAME.match(column["name"]))


# ---------------------------------------------------------------------------
# The plan (also the first half of the run)
# ---------------------------------------------------------------------------
def build_plan(conn, source):
    """Everything the run will do, with nothing written. Raises AdminError when the run must not start."""
    names = _resolve_db(conn, source)
    if len(names) != 1:
        raise AdminError("Se alege o singura baza sursa.")
    src = names[0]
    if src.casefold() == TARGET_DB.casefold():
        raise AdminError("Sursa nu poate fi chiar %s." % TARGET_DB)
    if TARGET_DB.casefold() not in [n.casefold() for n in _resolve_db(conn, "ALL")]:
        raise AdminError("Baza %s nu exista pe server." % TARGET_DB)

    demo_cf, src_cf = _unit_cf(conn, TARGET_DB), _unit_cf(conn, src)
    if not _digits(demo_cf):
        raise AdminError("%s nu are cod fiscal in %s.Unitati: nu am ce pastra." % (TARGET_DB, COMMON_DB))
    rewrite_cf = bool(_digits(src_cf)) and _digits(src_cf) != _digits(demo_cf)
    warnings = []
    if not _digits(src_cf):
        warnings.append("Baza %s nu are cod fiscal in %s.Unitati: codurile fiscale nu se pot inlocui." % (src, COMMON_DB))

    src_tables, dst_tables = _tables(conn, src), _tables(conn, TARGET_DB)
    src_cols, dst_cols = _columns(conn, src), _columns(conn, TARGET_DB)

    plan_tables, rewrites = [], []
    for key in sorted(dst_tables):
        dst_name, engine = dst_tables[key]
        if engine.upper() != "INNODB":
            raise AdminError("Tabela %s din %s nu e InnoDB (%s): nu pot garanta ca o eroare lasa baza neschimbata."
                             % (dst_name, TARGET_DB, engine or "?"))
        item = {"table": dst_name, "demo_rows": _count(conn, TARGET_DB, dst_name), "source_rows": 0, "copy": False,
                "note": "", "columns": []}
        if key in SKIP_DATA:
            item["note"] = "tabela de blocari: ramane goala"
        elif key not in src_tables:
            item["note"] = "nu exista in sursa: se goleste"
        else:
            have = {c["name"].casefold(): c for c in src_cols.get(key, [])}
            columns, missing = [], []
            for col in dst_cols.get(key, []):
                if "generated" in col["extra"]:
                    continue
                if col["name"].casefold() in have and "generated" not in have[col["name"].casefold()]["extra"]:
                    columns.append(col)
                elif not col["nullable"] and col["default"] is None and "auto_increment" not in col["extra"]:
                    missing.append(col["name"])
            if missing:
                raise AdminError("Tabela %s: coloanele %s sunt obligatorii in %s dar lipsesc din %s. Aduceti baza la "
                                 "schema SURSA (pagina Sincronizare) si reincercati." % (dst_name, ", ".join(missing), TARGET_DB, src))
            dropped = [c["name"] for c in src_cols.get(key, []) if c["name"].casefold() not in {x["name"].casefold() for x in dst_cols.get(key, [])}]
            if dropped:
                item["note"] = "coloane doar in sursa, necopiate: " + ", ".join(dropped)
            item["copy"] = True
            item["source_rows"] = _count(conn, src, src_tables[key][0])
            item["columns"] = columns
            for col in columns:
                low = col["name"].casefold()
                if low == "dc" and col["type"] in _TEXT_TYPES:
                    rewrites.append({"table": dst_name, "column": col["name"], "kind": "DC", "rows": 0})
                elif _is_cf_column(dst_name, col):
                    own_value, own_keep = (key, low) in OWN_BY_VALUE, (key, low) in OWN_KEEP_DEMO
                    if own_keep:
                        rewrites.append({"table": dst_name, "column": col["name"], "kind": "CF propriu (pastrat din %s)" % TARGET_DB, "rows": 0})
                    elif own_value:
                        rewrites.append({"table": dst_name, "column": col["name"], "kind": "CF propriu", "rows": 0})
                    elif rewrite_cf:
                        rewrites.append({"table": dst_name, "column": col["name"], "kind": "CF", "rows": 0})
        plan_tables.append(item)

    # rows each rewrite will change (counted in the SOURCE, where the values still are)
    for rw in rewrites:
        item = next(t for t in plan_tables if t["table"] == rw["table"])
        if not item["copy"]:
            continue
        src_name = src_tables[rw["table"].casefold()][0]
        target = "%s.%s" % (_q(src), _q(src_name))
        col = rw["column"]
        if rw["kind"] == "DC":
            where = "%s IS NOT NULL AND %s <> %%s" % (_q(col), _q(col))
            params = (TARGET_DB,)
        elif rw["kind"].startswith("CF propriu"):
            where = "%s IS NOT NULL AND TRIM(%s) <> ''" % (_q(col), _q(col))
            params = ()
        else:
            where = "%s = %%s" % _norm_sql(col)
            params = (_digits(src_cf),)
        rw["rows"] = int(_rows(conn, "SELECT COUNT(*) AS n FROM %s WHERE %s" % (target, where), params)[0]["n"])

    # the sector-sources of the source that 000_DEMO has no login combination for
    try:
        have_ss = {r["SS"].casefold() for r in _rows(conn, "SELECT SS FROM `%s`.`Unitati_Ani` WHERE DC = %%s" % COMMON_DB, (TARGET_DB,))}
        for r in _rows(conn, "SELECT DISTINCT SursaSector AS ss FROM %s.`Unitati`" % _q(src)):
            if (r["ss"] or "").casefold() not in have_ss:
                warnings.append("Sursa are datele sectorului-sursa %s, dar %s nu il are in %s.Unitati_Ani: nu se poate alege la login."
                                % (r["ss"], TARGET_DB, COMMON_DB))
    except Exception as err:   # a warning must never block the plan; the message says why it is missing
        warnings.append("Nu am putut compara sectoarele-sursa cu %s.Unitati_Ani: %s" % (COMMON_DB, err))

    return {"source": src, "target": TARGET_DB, "demo_cf": demo_cf, "source_cf": src_cf, "rewrite_cf": rewrite_cf,
            "tables": plan_tables, "rewrites": rewrites, "warnings": warnings}


def _public(plan):
    """The plan without the column objects (they are for the run)."""
    return {
        "source": plan["source"], "target": plan["target"], "demo_cf": plan["demo_cf"], "source_cf": plan["source_cf"],
        "rewrite_cf": plan["rewrite_cf"], "warnings": plan["warnings"], "rewrites": plan["rewrites"],
        "tables": [{k: v for k, v in t.items() if k != "columns"} for t in plan["tables"]],
        "demo_total": sum(t["demo_rows"] for t in plan["tables"]),
        "source_total": sum(t["source_rows"] for t in plan["tables"] if t["copy"]),
    }


def cmd_demo_clone_plan(args):
    conn = connect()
    try:
        return _public(build_plan(conn, args.source))
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------
def _select_expr(table, col, plan, params):
    """The SELECT expression for one column; params gets its %s values appended in order."""
    name = col["name"]
    low = name.casefold()
    key = (table.casefold(), low)
    demo_digits, src_digits = _digits(plan["demo_cf"]), _digits(plan["source_cf"])
    if low == "dc" and col["type"] in _TEXT_TYPES:
        params.append(TARGET_DB)
        return "IF(%s IS NULL, NULL, %%s)" % _q(name)
    if key in OWN_KEEP_DEMO:
        return _q(name)                         # set after the copy, from the value saved before the wipe
    if not _is_cf_column(table, col):
        return _q(name)
    keep_prefix = "IF(UPPER(TRIM(%s)) REGEXP '^RO', CONCAT('RO', %%s), %%s)" % _q(name)
    if key in OWN_BY_VALUE:
        params.extend([demo_digits, demo_digits])
        return "IF(%s IS NULL OR TRIM(%s) = '', %s, %s)" % (_q(name), _q(name), _q(name), keep_prefix)
    if not plan["rewrite_cf"]:
        return _q(name)
    params.extend([src_digits, demo_digits, demo_digits])
    return "CASE WHEN %s = %%s THEN %s ELSE %s END" % (_norm_sql(name), keep_prefix, _q(name))


def cmd_demo_clone(args):
    from routes.schema_sync.schema_execute import dump_database
    conn = connect()
    done = connect()
    got_lock = False
    try:
        plan = build_plan(conn, args.source)
        src = plan["source"]
        cur = conn.cursor()
        cur.execute("SELECT GET_LOCK(%s, 0)", (LOCK_NAME,))
        got_lock = cur.fetchone()[0] == 1
        cur.close()
        if not got_lock:
            raise AdminError("O clonare in %s ruleaza deja pe server." % TARGET_DB)

        # 1. the safety copy; without it nothing is touched
        dump_path = dump_database(TARGET_DB, BACKUP_DIR, _quiet_logger())

        # 2. what 000_DEMO's own tax code is in EF_Furnizor, saved before the wipe
        keep_furnizor = None
        if any(t["table"].casefold() == "ef_furnizor" for t in plan["tables"]):
            rows = _rows(conn, "SELECT CodFiscal FROM %s.`EF_Furnizor` LIMIT 1" % _q(TARGET_DB))
            keep_furnizor = (rows[0]["CodFiscal"] or "").strip() if rows else None
        furnizor_value = keep_furnizor or plan["demo_cf"]

        # 3. one transaction: empty everything, copy, rewrite, verify
        conn.autocommit = False
        cur = conn.cursor()
        cur.execute("SET SESSION foreign_key_checks = 0")
        cur.execute("SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED")
        results = []
        try:
            for t in plan["tables"]:
                cur.execute("DELETE FROM %s.%s" % (_q(TARGET_DB), _q(t["table"])))
            for t in plan["tables"]:
                if not t["copy"]:
                    results.append({"table": t["table"], "rows": 0, "expected": 0})
                    continue
                params = []
                cols = t["columns"]
                exprs = [_select_expr(t["table"], c, plan, params) for c in cols]
                sql = "INSERT INTO %s.%s (%s) SELECT %s FROM %s.%s" % (
                    _q(TARGET_DB), _q(t["table"]), ", ".join(_q(c["name"]) for c in cols), ", ".join(exprs),
                    _q(src), _q(t["table"]))
                cur.execute(sql, params)
                if cur.rowcount != t["source_rows"]:
                    raise AdminError("Tabela %s: s-au copiat %d randuri din %d." % (t["table"], cur.rowcount, t["source_rows"]))
                results.append({"table": t["table"], "rows": cur.rowcount, "expected": t["source_rows"]})
            if any(t["table"].casefold() == "ef_furnizor" and t["copy"] for t in plan["tables"]):
                cur.execute("UPDATE %s.`EF_Furnizor` SET CodFiscal = %%s" % _q(TARGET_DB), (furnizor_value,))
            _verify(cur, plan)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()

        _journal(done, "ADMIN_DEMO_CLONE", TARGET_DB, src,
                 "%s golita si clonata din %s: %d randuri; copie %s" % (TARGET_DB, src, sum(r["rows"] for r in results), dump_path))
        out = _public(plan)
        out.update({"backup": dump_path, "results": results, "copied_total": sum(r["rows"] for r in results),
                    "furnizor_cf": furnizor_value})
        return out
    finally:
        if got_lock:
            try:
                c = conn.cursor()
                c.execute("SELECT RELEASE_LOCK(%s)", (LOCK_NAME,))
                c.fetchall()
                c.close()
            except Exception:   # the lock goes with the connection anyway
                pass
        conn.close()
        done.close()


def _verify(cur, plan):
    """Inside the transaction, before the commit: no foreign DC and no leftover source tax code anywhere."""
    src_digits = _digits(plan["source_cf"])
    for rw in plan["rewrites"]:
        item = next(t for t in plan["tables"] if t["table"] == rw["table"])
        if not item["copy"]:
            continue
        target = "%s.%s" % (_q(TARGET_DB), _q(rw["table"]))
        col = rw["column"]
        if rw["kind"] == "DC":
            cur.execute("SELECT COUNT(*) FROM %s WHERE %s IS NOT NULL AND %s <> %%s" % (target, _q(col), _q(col)), (TARGET_DB,))
        elif rw["kind"] == "CF" and src_digits:
            cur.execute("SELECT COUNT(*) FROM %s WHERE %s = %%s" % (target, _norm_sql(col)), (src_digits,))
        else:
            continue
        left = int(cur.fetchone()[0])
        if left:
            raise AdminError("Verificare esuata: %s.%s mai are %d randuri nerescrise. Nu s-a schimbat nimic." % (rw["table"], col, left))


def register(sub):
    for verb, fn in (("demo-clone-plan", cmd_demo_clone_plan), ("demo-clone", cmd_demo_clone)):
        p = sub.add_parser(verb)
        p.add_argument("--from", dest="source", required=True)
        p.set_defaults(fn=fn)
