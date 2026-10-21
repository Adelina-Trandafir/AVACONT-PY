"""
admin_cli.py -- the server half of KBotAdmin (slice ME01-01).

KBotAdmin is a desktop program that never opens a database port: it logs in over SSH and runs this
module, which prints ONE JSON object on stdout and nothing else:

    {"ok": true,  "data": ...}
    {"ok": false, "error": "..."}

Run from the project root (the log paths are relative to it):

    cd /root/AVACONT && .venv/bin/python -m routes.admin_cli.admin_cli <command> [options]

Commands (all read-only unless marked WRITE):

    databases [--no-counts]            unit databases: size, accounts, schema state, errors last 24h
    accounts [--db D]                  account <-> database links with their roles
    roles                              the roles with their operations
    grant  --un U --dc D --cod C       WRITE  give roles (C = code, a comma list, or @AD = every ADECHIT role)
    revoke --un U --dc D --cod C       WRITE  take roles away (same forms of C)
    acct-add    --un U --dc D --rol R  WRITE  link an account to a database (KB role R)
    acct-role   --un U --dc D --rol R  WRITE  change the KB role of a link
    acct-remove --un U --dc D          WRITE  unlink (and drop its extra roles)
    privileges-sync --un U             WRITE  reapply the complete MariaDB data privilege policy
    password-state                     per account: must change the password now / when it was last changed
    clear-must-change --un U           WRITE  cancel the obligation (flag off, AuthVersion untouched: open sessions stay)
    must-change-all                    WRITE  force a password change for EVERY account linked to a unit
    must-change --un U                 WRITE  force a password change at the next login (call after every reset to an initial password)
    ensure-role --cod C                WRITE  create a known extra role (see KNOWN_ROLES)
    diff --db D [--mode SAFE|FORCE]    schema difference against AVACONT_SURSA, nothing stored
    commands                           the whitelist of `command`
    command --name N --db D|ALL        run one whitelisted command
    status                             disk, memory, load, MariaDB, processes, backups
    audit [--db D] [--un U] [--actions A,B] [--hours H] [--limit N]
    logs-meta                          the log files that exist
    logs [--file F] [--db D] [--gens G] [--since ISO] [--limit N]
    logs-clear --keep all|day|week [--file F]    WRITE  empty the log files (all of them, or one), keeping nothing / the last day / the last week
    requests [--state S]               registration requests (InAsteptare, Esuata, Aprobata, Respinsa, toate)
    request --id N                     one request with the plan of its approval
    request-plan --id N [--db D] [--codes JSON]          the plan again, with the operator's edits
    request-approve --id N --db D [--codes JSON]  WRITE  approve: creates the unit database and account
    request-reject --id N --motiv T    WRITE  reject, with the reason the applicant receives
    request-link --id N                WRITE  a fresh password link for an approved request
    once-history                       the one-time queries that ran (template ledger): text, date, databases done / to do
    backups [--db D]                   the dump files and schema plans (of one database when D is given), with the reason of each dump
    backup-text --path P               the text of one dump / plan file (first 2 MB)
    backup --db D|ALL --reason T       WRITE  dump one database, or every unit database but 000_DEMO, to backup/ (admin_backup.py)
    snap-run --db D [--state-file P] [--mode fast|full]   read one database inside one consistent snapshot as a stream of JSON page records (admin_snapshot.py, ME02-04)
    demo-clone-plan --from DB          what emptying 000_DEMO and cloning DB into it would do (nothing is written)
    demo-clone --from DB               WRITE  dump 000_DEMO, empty it, clone DB into it keeping 000_DEMO's tax code and DC
    visits [--days N] [--bots]

The schema SYNC itself is not here: KBotAdmin runs routes.schema_sync.schema_sync directly, as
AvacontPush does (it streams its progress line by line).

Nothing prints secrets. Identifiers coming from the command line are checked against the server
before they reach SQL, and quoted.
"""
from utils.clock import configure_utc_process

configure_utc_process()

import argparse
import glob
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from utils.clock import utc_from_timestamp, utc_now

import mysql.connector

from routes.admin_cli import admin_logs, admin_logs_clear
from routes.schema_sync.schema_common import (COMMON_DB, FORBIDDEN_TARGETS, SOURCE_DB, SchemaSyncError, connect,
                                              list_unit_databases)

from routes.admin_cli.admin_backup import BACKUP_DIR      # the dump verbs live in admin_backup.py (ME02-02)
JOURNAL_ACTOR_DEFAULT = "scavatarsoft@gmail.com"

from routes.admin_cli.admin_common import (
    AdminError, KNOWN_ROLES, _actor, _check_name, _exec, _iso, _journal,
    _q, _quiet_logger, _resolve_db, _rows, _unit_dbs,
)
from routes.admin_cli.admin_rights import (
    cmd_accounts, cmd_roles, cmd_grant, cmd_revoke, cmd_acct_add,
    cmd_acct_role, cmd_acct_remove, cmd_privileges_sync,
)


# ---------------------------------------------------------------------------
# Databases and accounts
# ---------------------------------------------------------------------------
def _pending_requests():
    """Registration requests waiting for a decision; -1 when they cannot be counted."""
    try:
        from utils.database import get_kbot_provisioning_connection
        conn = get_kbot_provisioning_connection()
        try:
            return int(_rows(conn, "SELECT COUNT(*) AS n FROM FX_Inregistrari WHERE Stare = 'InAsteptare'")[0]["n"])
        finally:
            if conn.is_connected():
                conn.close()
    except Exception:  # a count for a badge must never break the database list
        return -1


def cmd_databases(args):
    conn = connect()
    try:
        listed = list_unit_databases(conn)
        names = {d["name"].casefold(): d for d in listed}
        units = {r["DC"].casefold(): r for r in _rows(
            conn, "SELECT DC, NumeUnitate, CF FROM `%s`.`Unitati`" % COMMON_DB)}
        users = {r["DC"].casefold(): r["n"] for r in _rows(
            conn, "SELECT DC, COUNT(*) AS n FROM `%s`.`Unitati_Utilizatori` GROUP BY DC" % COMMON_DB)}
        sizes = {}
        if listed:
            marks = ",".join(["%s"] * len(listed))
            for r in _rows(conn,
                           "SELECT TABLE_SCHEMA AS db, COUNT(*) AS tables_n, "
                           "SUM(COALESCE(DATA_LENGTH,0)+COALESCE(INDEX_LENGTH,0)) AS bytes_n, "
                           "SUM(COALESCE(TABLE_ROWS,0)) AS rows_n, MAX(UPDATE_TIME) AS last_write "
                           "FROM information_schema.TABLES WHERE TABLE_SCHEMA IN (%s) AND TABLE_TYPE='BASE TABLE' "
                           "GROUP BY TABLE_SCHEMA" % marks, [d["name"] for d in listed]):
                sizes[r["db"].casefold()] = r
        pending = {}
        try:
            for r in _rows(conn,
                           "SELECT target_db, SUM(executed_at IS NULL) AS pending, "
                           "SUM(executed_at IS NULL AND is_destructive=1) AS destructive, "
                           "SUM(error_msg IS NOT NULL AND executed_at IS NULL) AS errors "
                           "FROM `%s`.`schema_diff_log` GROUP BY target_db" % COMMON_DB):
                pending[(r["target_db"] or "").casefold()] = r
        except mysql.connector.Error:
            pending = {}
        counts = {} if args.no_counts else admin_logs.level_counts(24, 1)
        out = []
        for key, d in sorted(names.items()):
            unit = units.get(key, {})
            size = sizes.get(key, {})
            sync = pending.get(key, {})
            hits = counts.get(key, {})
            out.append({
                "db": d["name"], "name": unit.get("NumeUnitate") or "", "cf": unit.get("CF") or "",
                "exists": d["exists"], "in_cai": d["in_cai"], "accounts": int(users.get(key, 0)),
                "tables": int(size.get("tables_n") or 0), "rows": int(size.get("rows_n") or 0),
                "mb": round(float(size.get("bytes_n") or 0) / 1048576.0, 1),
                "last_write": _iso(size.get("last_write")),
                "sync_pending": int(sync.get("pending") or 0), "sync_destructive": int(sync.get("destructive") or 0),
                "sync_errors": int(sync.get("errors") or 0),
                "err24": int(hits.get("err", 0)), "warn24": int(hits.get("warn", 0)),
            })
        return {"databases": out, "errors_without_db": int(counts.get("", {}).get("err", 0)),
                "pending_requests": _pending_requests(), "server_time": _iso(utc_now())}
    finally:
        conn.close()


def cmd_must_change(args):
    _check_name(args.un, "Contul")
    conn = connect()
    try:
        _exec(conn,
              "INSERT INTO `%s`.`Conturi_Securitate` (UN, MustChangePassword, AuthVersion) VALUES (%%s, 1, 2) "
              "ON DUPLICATE KEY UPDATE MustChangePassword = 1, AuthVersion = AuthVersion + 1" % COMMON_DB,
              (args.un,))
        _journal(conn, "ADMIN_MUST_CHANGE", None, args.un, "schimbare obligatorie a parolei")
        return {"un": args.un, "must_change": True}
    finally:
        conn.close()


def cmd_password_state(args):
    conn = connect()
    try:
        cur = conn.cursor()
        try:
            cur.execute("SELECT UN, MustChangePassword, PasswordChangedAt FROM `%s`.`Conturi_Securitate`" % COMMON_DB)
            rows = cur.fetchall()
        except mysql.connector.Error as err:
            if getattr(err, "errno", None) == 1146:
                raise AdminError("Tabela Conturi_Securitate lipseste: rulati sql/SEC01_conturi_securitate.sql.")
            raise
        finally:
            cur.close()
        return {"accounts": [{"un": r[0], "must_change": bool(r[1]), "changed_at": _iso(r[2])} for r in rows]}
    finally:
        conn.close()


def cmd_clear_must_change(args):
    _check_name(args.un, "Contul")
    conn = connect()
    try:
        n = _exec(conn, "UPDATE `%s`.`Conturi_Securitate` SET MustChangePassword = 0 WHERE UN = %%s" % COMMON_DB, (args.un,))
        _journal(conn, "ADMIN_CLEAR_MUST_CHANGE", None, args.un, "obligatia de schimbare a parolei anulata")
        return {"un": args.un, "rows": n}
    finally:
        conn.close()


def cmd_must_change_all(args):
    conn = connect()
    try:
        n = _exec(conn,
                  "INSERT INTO `%s`.`Conturi_Securitate` (UN, MustChangePassword, AuthVersion) "
                  "SELECT DISTINCT UN, 1, 2 FROM `%s`.`Unitati_Utilizatori` "
                  "ON DUPLICATE KEY UPDATE MustChangePassword = 1, AuthVersion = AuthVersion + 1" % (COMMON_DB, COMMON_DB))
        _journal(conn, "ADMIN_MUST_CHANGE_ALL", None, "*", "schimbare obligatorie a parolei pentru toate conturile")
        return {"rows": n}
    finally:
        conn.close()


def cmd_ensure_role(args):
    if args.cod not in KNOWN_ROLES:
        raise AdminError("Rol necunoscut: %s." % args.cod)
    section, name, operation = KNOWN_ROLES[args.cod]
    conn = connect()
    try:
        _exec(conn, "INSERT IGNORE INTO `%s`.`Roluri` (Cod, Sectiune, Denumire) VALUES (%%s,%%s,%%s)" % COMMON_DB,
              (args.cod, section, name))
        role_id, _s = _role_id(conn, args.cod)
        _exec(conn, "INSERT IGNORE INTO `%s`.`Roluri_Operatii` (IdRol, Operatie) VALUES (%%s,%%s)" % COMMON_DB,
              (role_id, operation))
        _journal(conn, "ADMIN_ROLE_ENSURE", None, args.cod, operation)
        return {"cod": args.cod, "id": role_id, "operation": operation}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Schema difference (read-only, nothing is stored in schema_diff_log)
# ---------------------------------------------------------------------------
def cmd_diff(args):
    from routes.schema_sync.schema_diff import build_diff
    mode = (args.mode or "FORCE").upper()
    if mode not in ("SAFE", "FORCE"):
        raise AdminError("Mod necunoscut.")
    conn = connect()
    try:
        dbs = _resolve_db(conn, args.db)
        if len(dbs) != 1:
            raise AdminError("Diferenta se cere pe o singura baza.")
        statements = build_diff(conn, dbs, mode, _quiet_logger())
        rows = []
        for s in statements:
            rows.append({"table": s.table_name or "", "object": s.object_name or "", "type": s.object_type,
                         "action": s.action_type, "destructive": bool(s.is_destructive),
                         "priority": s.priority, "error": s.error_msg or "", "sql": s.ddl_sql})
        return {"db": dbs[0], "mode": mode, "count": len(rows),
                "destructive": sum(1 for r in rows if r["destructive"]), "statements": rows}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# The whitelisted commands
# ---------------------------------------------------------------------------
def _block(db, title, columns, rows, note="", raw=False):
    """raw=True: the program prints each row as one untruncated line (cells joined by « | »), not as an aligned column table."""
    return {"db": db, "title": title, "columns": columns, "rows": [[_cell(v) for v in row] for row in rows],
            "note": note, "raw": bool(raw)}


def _cell(value):
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return "" if value is None else str(value)


def _c_ping(conn, db):
    started = time.time()
    info = _rows(conn, "SELECT VERSION() AS v, CURRENT_USER() AS u, @@character_set_database AS cs, "
                       "@@collation_database AS co")[0]
    ms = (time.time() - started) * 1000.0
    return _block(db, "Conexiune", ["versiune", "cont", "charset", "collation", "ms"],
                  [[info["v"], info["u"], info["cs"], info["co"], "%.1f" % ms]])


def _c_tables(conn, db):
    data = _rows(conn,
                 "SELECT TABLE_NAME AS n, COALESCE(TABLE_ROWS,0) AS r, ENGINE AS e, TABLE_COLLATION AS c, "
                 "ROUND((COALESCE(DATA_LENGTH,0)+COALESCE(INDEX_LENGTH,0))/1048576,2) AS mb, UPDATE_TIME AS u "
                 "FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s AND TABLE_TYPE='BASE TABLE' "
                 "ORDER BY (COALESCE(DATA_LENGTH,0)+COALESCE(INDEX_LENGTH,0)) DESC", (db,))
    return _block(db, "Tabele (dupa marime)", ["tabela", "randuri~", "MB", "motor", "collation", "ultima scriere"],
                  [[r["n"], r["r"], r["mb"], r["e"], r["c"], r["u"]] for r in data],
                  "Numarul de randuri este estimat de server.")


def _c_counts(conn, db):
    tables = _rows(conn, "SELECT TABLE_NAME AS n, COALESCE(TABLE_ROWS,0) AS r FROM information_schema.TABLES "
                         "WHERE TABLE_SCHEMA=%s AND TABLE_TYPE='BASE TABLE' ORDER BY TABLE_NAME", (db,))
    out, skipped = [], 0
    for t in tables:
        if t["r"] > 3000000:
            skipped += 1
            out.append([t["n"], "(prea mare, ~%s)" % t["r"]])
            continue
        n = _rows(conn, "SELECT COUNT(*) AS n FROM %s.%s" % (_q(db), _q(t["n"])))[0]["n"]
        out.append([t["n"], n])
    return _block(db, "Randuri exacte", ["tabela", "randuri"], out,
                  "%d tabele prea mari au ramas pe estimare." % skipped if skipped else "")


def _c_processlist(conn, db):
    data = _rows(conn, "SHOW FULL PROCESSLIST")
    mine = [r for r in data if (r.get("db") or "").casefold() == db.casefold()]
    return _block(db, "Procese deschise pe baza", ["id", "utilizator", "stare", "timp s", "comanda"],
                  [[r["Id"], r["User"], r.get("State"), r["Time"], (r.get("Info") or "")[:120]] for r in mine])


def _c_journal(conn, db):
    data = _rows(conn, "SELECT Moment, UN, Actiune, Tinta, Rezultat FROM `%s`.`Jurnal` WHERE DC=%%s "
                       "ORDER BY Id DESC LIMIT 50" % COMMON_DB, (db,))
    return _block(db, "Ultimele 50 de intrari din Jurnal", ["moment", "cont", "actiune", "tinta", "rezultat"],
                  [[r["Moment"], r["UN"], r["Actiune"], r["Tinta"], r["Rezultat"]] for r in data])


def _c_charset(conn, db):
    data = _rows(conn,
                 "SELECT TABLE_NAME AS t, COLUMN_NAME AS c, COLLATION_NAME AS co FROM information_schema.COLUMNS "
                 "WHERE TABLE_SCHEMA=%s AND COLLATION_NAME IS NOT NULL AND COLLATION_NAME <> 'utf8mb3_general_ci' "
                 "ORDER BY TABLE_NAME, ORDINAL_POSITION", (db,))
    return _block(db, "Coloane care nu sunt utf8mb3_general_ci", ["tabela", "coloana", "collation"],
                  [[r["t"], r["c"], r["co"]] for r in data], "Goala = totul este utf8mb3_general_ci.")


def _c_activity(conn, db):
    data = _rows(conn, "SELECT UN, MAX(Moment) AS ultima, COUNT(*) AS n FROM `%s`.`Jurnal` WHERE DC=%%s "
                       "GROUP BY UN ORDER BY ultima DESC" % COMMON_DB, (db,))
    return _block(db, "Activitate pe conturi (din Jurnal)", ["cont", "ultima", "intrari"],
                  [[r["UN"], r["ultima"], r["n"]] for r in data])


def _c_analyze(conn, db):
    tables = [r["n"] for r in _rows(conn, "SELECT TABLE_NAME AS n FROM information_schema.TABLES "
                                          "WHERE TABLE_SCHEMA=%s AND TABLE_TYPE='BASE TABLE' ORDER BY 1", (db,))]
    out = []
    for name in tables:
        for r in _rows(conn, "ANALYZE TABLE %s.%s" % (_q(db), _q(name))):
            out.append([name, r.get("Msg_type"), r.get("Msg_text")])
    return _block(db, "ANALYZE TABLE", ["tabela", "tip", "mesaj"], out, "Se actualizeaza doar statisticile.")


_RX_COD_ANGAJAMENT = re.compile(r"^[A-Za-z0-9_./-]{1,60}$")


def _c_fisa_angajament(conn, db, param):
    """KE01-02: everything the database knows about one angajament, from the procedure AVACONT_COMUN.FX_FisaAngajament
    (sql/KE_01_02_fisa_angajament.sql, created by hand). One block, one row per line, printed untruncated."""
    cod = (param or "").strip()
    if not _RX_COD_ANGAJAMENT.match(cod):
        raise AdminError("Codul angajamentului nu este valid (litere, cifre, _ . / - , pana la 60 de caractere).")
    cur = conn.cursor()
    try:
        cur.callproc("%s.FX_FisaAngajament" % COMMON_DB, (db, cod))
        rows, columns = [], []
        for result in cur.stored_results():
            columns = list(result.column_names)
            rows = result.fetchall()
    finally:
        cur.close()
    if columns == ["Eroare"]:
        raise AdminError(rows[0][0] if rows else "Procedura a refuzat baza de date.")
    return _block(db, "Fisa angajament %s" % cod, columns, rows,
                  "%d randuri. DIAG = verificarile calculate; restul = randurile brute ale tabelelor." % len(rows), raw=True)


# name -> (title, description, writes, handler)
COMMANDS = {
    "ping": ("Conexiune", "Versiune, cont, charset si timpul de raspuns al bazei.", False, _c_ping),
    "tables": ("Tabele", "Tabelele dupa marime, cu motor si collation.", False, _c_tables),
    "counts": ("Randuri exacte", "COUNT(*) pe fiecare tabela (peste 3 milioane ramane estimat).", False, _c_counts),
    "processlist": ("Procese", "Conexiunile deschise acum pe baza.", False, _c_processlist),
    "journal": ("Jurnal", "Ultimele 50 de intrari din Jurnal pentru baza.", False, _c_journal),
    "activity": ("Activitate conturi", "Ultima actiune si numarul de actiuni pe cont.", False, _c_activity),
    "charset": ("Verificare collation", "Coloanele care nu sunt utf8mb3_general_ci.", False, _c_charset),
    "analyze": ("ANALYZE TABLE", "Actualizeaza statisticile tabelelor (scrie, dar nu schimba date).", True, _c_analyze),
    "fisa_angajament": ("Fisa angajament", "Tot ce stie baza despre un angajament, intr-un singur tabel (verificarile KE intai, apoi randurile "
                        "din toate tabelele). Cere o baza anume si codul angajamentului.", False, _c_fisa_angajament),
}

# Commands that need one typed value besides the database: name -> label of that value. They run on ONE database only.
COMMAND_PARAMS = {
    "fisa_angajament": "Cod angajament",
}


def cmd_commands(args):
    return {"commands": [{"name": k, "title": v[0], "description": v[1], "writes": v[2], "param": COMMAND_PARAMS.get(k, "")}
                         for k, v in COMMANDS.items()]}


def cmd_command(args):
    if args.name not in COMMANDS:
        raise AdminError("Comanda «%s» nu este in lista permisa." % args.name)
    handler = COMMANDS[args.name][3]
    label = COMMAND_PARAMS.get(args.name)
    param = (getattr(args, "param", "") or "").strip()
    if label:
        if (args.db or "").upper() == "ALL":
            raise AdminError("Comanda «%s» se ruleaza pe o singura baza: alege o baza in arbore, nu <TOATE BAZELE>."
                             % COMMANDS[args.name][0])
        if not param:
            raise AdminError("Scrie %s." % label.lower())
    conn = connect()
    try:
        blocks = []
        for db in _resolve_db(conn, args.db):
            try:
                conn.database = db
                blocks.append(handler(conn, db, param) if label else handler(conn, db))
            except mysql.connector.Error as err:
                blocks.append(_block(db, COMMANDS[args.name][0], ["eroare"], [[str(err)]]))
        return {"name": args.name, "blocks": blocks}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# The server itself
# ---------------------------------------------------------------------------
def _read_first(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return ""


def _meminfo():
    values = {}
    for line in _read_first("/proc/meminfo").splitlines():
        key, _sep, rest = line.partition(":")
        parts = rest.split()
        if parts and parts[0].isdigit():
            values[key] = int(parts[0]) * 1024
    return values


def _run(cmd, timeout=10):
    try:
        done = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, check=False)
        return done.stdout.decode("utf-8", "replace").strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _cert_expiry():
    out = []
    for path in sorted(glob.glob("/etc/letsencrypt/live/*/fullchain.pem")):
        text = _run(["openssl", "x509", "-enddate", "-noout", "-in", path])
        if text.startswith("notAfter="):
            try:
                end = datetime.strptime(text[len("notAfter="):].replace("  ", " "), "%b %d %H:%M:%S %Y %Z")
                out.append({"name": os.path.basename(os.path.dirname(path)), "until": end.strftime("%Y-%m-%d"),
                            "days": (end - utc_now()).days})
            except ValueError:
                out.append({"name": os.path.basename(os.path.dirname(path)), "until": text, "days": None})
    return out


def cmd_status(args):
    mem = _meminfo()
    load = _read_first("/proc/loadavg").split()
    uptime = _read_first("/proc/uptime").split()
    disks = []
    for label, path in (("/", "/"), ("proiect", os.getcwd()), ("copii", os.path.abspath(BACKUP_DIR))):
        if os.path.exists(path):
            total, used, free = shutil.disk_usage(path)
            disks.append({"label": label, "path": path, "total": total, "used": used, "free": free})
    procs = [line for line in _run(["ps", "-eo", "pid,etime,pcpu,pmem,args"]).splitlines()
             if "gunicorn" in line or "redis-server" in line or "mariadbd" in line or "mysqld" in line]
    db = {}
    conn = None
    try:
        conn = connect()
        status = {r["Variable_name"]: r["Value"] for r in _rows(
            conn, "SHOW GLOBAL STATUS WHERE Variable_name IN ('Uptime','Threads_connected','Max_used_connections',"
                  "'Questions','Slow_queries','Aborted_connects')")}
        db = {"version": _rows(conn, "SELECT VERSION() AS v")[0]["v"], **status}
    except (mysql.connector.Error, SchemaSyncError) as err:
        db = {"error": str(err)}
    finally:
        if conn is not None and conn.is_connected():
            conn.close()
    redis_state = ""
    try:
        from routes.auth.session_store import STORE
        redis_state = "sesiuni in memorie/Redis: %s" % STORE.size()
    except Exception as err:  # the store may be absent in a bare shell; the answer is the message itself
        redis_state = "indisponibil: %s" % err
    return {"time": _iso(utc_now()), "uptime_s": float(uptime[0]) if uptime else None,
            "load": load[:3], "mem_total": mem.get("MemTotal"), "mem_free": mem.get("MemAvailable"),
            "swap_total": mem.get("SwapTotal"), "swap_free": mem.get("SwapFree"), "disks": disks,
            "processes": procs, "mariadb": db, "redis": redis_state, "certificates": _cert_expiry(),
            "logs": admin_logs.file_info()}


# ---------------------------------------------------------------------------
# Audit, visits, logs, backups
# ---------------------------------------------------------------------------
def cmd_audit(args):
    since = utc_from_timestamp(time.time() - 3600 * max(1, min(args.hours, 24 * 90)))
    where, params = ["Moment >= %s"], [since]
    if args.db:
        where.append("DC = %s")
        params.append(args.db)
    if args.un:
        where.append("UN LIKE %s")
        params.append("%" + args.un + "%")
    if args.actions:
        wanted = [a for a in args.actions.split(",") if a.strip()]
        where.append("Actiune IN (%s)" % ",".join(["%s"] * len(wanted)))
        params.extend(wanted)
    limit = max(1, min(args.limit, 5000))
    conn = connect()
    try:
        rows = _rows(conn, "SELECT Id, Moment, UN, DC, Actiune, Tinta, Detalii, Rezultat, Masina, IP "
                           "FROM `%s`.`Jurnal` WHERE %s ORDER BY Id DESC LIMIT %d"
                           % (COMMON_DB, " AND ".join(where), limit), params)
        return {"rows": [{"id": r["Id"], "moment": _iso(r["Moment"]), "un": r["UN"], "dc": r["DC"] or "",
                          "action": r["Actiune"], "target": r["Tinta"] or "", "details": r["Detalii"] or "",
                          "result": r["Rezultat"] or "", "machine": r["Masina"] or "", "ip": r["IP"] or ""}
                         for r in rows]}
    finally:
        conn.close()


def cmd_visits(args):
    conn = connect()
    try:
        since = utc_from_timestamp(time.time() - 86400 * max(1, min(args.days, 365)))
        bots = "" if args.bots else "AND Bot = 0"
        rows = _rows(conn, "SELECT Prima, Ultima, IP, Tara, Mobil, Bot, Pagina, Referrer, DurataSec, Sectiuni "
                           "FROM `%s`.`Vizite_Site` WHERE Prima >= %%s %s ORDER BY Prima DESC LIMIT 2000"
                     % (COMMON_DB, bots), (since,))
        return {"rows": [{"first": _iso(r["Prima"]), "last": _iso(r["Ultima"]), "ip": r["IP"] or "",
                          "country": r["Tara"] or "", "mobile": bool(r["Mobil"]), "bot": bool(r["Bot"]),
                          "page": r["Pagina"], "referrer": r["Referrer"] or "", "seconds": r["DurataSec"],
                          "sections": r["Sectiuni"] or ""} for r in rows]}
    finally:
        conn.close()


def cmd_logs_meta(args):
    return {"files": admin_logs.file_info(), "server_time": _iso(utc_now())}


def cmd_logs(args):
    entries, unreadable = admin_logs.read_entries(args.file or "", args.db or "", max(0, min(args.gens, 5)),
                                                  args.since or None)
    total = len(entries)
    limit = max(1, min(args.limit, 5000))
    return {"rows": entries[-limit:], "total": total, "truncated": total > limit, "unreadable": unreadable,
            "server_time": _iso(utc_now())}


def cmd_logs_clear(args):
    try:
        return admin_logs_clear.clear(args.keep, args.file or "")
    except admin_logs_clear.ClearError as err:
        raise AdminError(str(err))
    except OSError as err:
        raise AdminError("Jurnalele nu au putut fi golite: %s" % err)


ONCE_LEDGER = "Interogari_Unice"


def _ledger(conn, db):
    """The rows of one database's one-time query ledger; empty when it has none yet."""
    try:
        return _rows(conn, "SELECT Hash, Nume, RulatLa, RulatDe, Randuri, Interogare FROM %s.%s ORDER BY RulatLa DESC"
                     % (_q(db), _q(ONCE_LEDGER)))
    except mysql.connector.Error as err:
        if getattr(err, "errno", None) == 1146:      # table missing: nothing ran there
            return []
        raise


def cmd_once_history(args):
    """What the one-time query runner ran: one entry per query (the template's ledger lists them all),
    with the text and the databases it reached or still has to reach."""
    conn = connect()
    try:
        units = _unit_dbs(conn)
        template = _ledger(conn, SOURCE_DB)
        done_by_db = {db: {r["Hash"] for r in _ledger(conn, db)} for db in units}
    finally:
        conn.close()
    items = []
    for row in template:
        reached = [db for db in units if row["Hash"] in done_by_db[db]]
        missing = [db for db in units if row["Hash"] not in done_by_db[db]]
        text = row["Interogare"] or ""
        items.append({"name": row["Nume"], "hash": row["Hash"], "ran_at": _iso(row["RulatLa"]),
                      "ran_by": row["RulatDe"] or "", "rows": row["Randuri"], "text": text,
                      "done": len(reached), "total": len(units), "missing": missing})
    return {"items": items, "units": len(units)}


# ---------------------------------------------------------------------------
# Registration requests (what the web page /operator used to do)
# ---------------------------------------------------------------------------
def _decider():
    return "kbotadmin:%s" % _actor()


def _journal_request(action, id_request, db, details, result="OK"):
    conn = connect()
    try:
        _journal(conn, action, db, str(id_request), (details or "")[:500], result)
    finally:
        conn.close()


def cmd_requests(args):
    from routes.admin_cli import admin_requests
    return admin_requests.list_requests(args.state, AdminError)


def cmd_request(args):
    from routes.admin_cli import admin_requests
    return admin_requests.detail(args.id, AdminError)


def cmd_request_plan(args):
    from routes.admin_cli import admin_requests
    return admin_requests.plan(args.id, admin_requests.parse_codes(args.codes, AdminError), args.db)


def cmd_request_approve(args):
    from routes.admin_cli import admin_requests
    codes = admin_requests.parse_codes(args.codes, AdminError)
    _journal_request("CERERE_APROBARE_START", args.id, args.db, "")
    try:
        result = admin_requests.approve(args.id, _decider(), codes, args.db, AdminError)
    except AdminError as err:
        _journal_request("CERERE_APROBARE_ESUATA", args.id, args.db, str(err), "EROARE")
        raise
    _journal_request("CERERE_APROBATA", args.id, args.db, "")
    return {"result": result}


def cmd_request_reject(args):
    from routes.admin_cli import admin_requests
    out = admin_requests.reject(args.id, _decider(), args.motiv, AdminError)
    _journal_request("CERERE_RESPINSA", args.id, None, args.motiv)
    return out


def cmd_request_link(args):
    from routes.admin_cli import admin_requests
    out = admin_requests.new_link(args.id, AdminError)
    _journal_request("CERERE_LINK_NOU", args.id, None, "", "OK" if out.get("link_trimis") else "EROARE")
    return out


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def _parser():
    parser = argparse.ArgumentParser(description="KBotAdmin - jumatatea de server.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("databases")
    p.add_argument("--no-counts", action="store_true")
    p.set_defaults(fn=cmd_databases)

    p = sub.add_parser("accounts")
    p.add_argument("--db", default="")
    p.set_defaults(fn=cmd_accounts)

    sub.add_parser("roles").set_defaults(fn=cmd_roles)

    for name, fn in (("grant", cmd_grant), ("revoke", cmd_revoke)):
        p = sub.add_parser(name)
        p.add_argument("--un", required=True)
        p.add_argument("--dc", required=True)
        p.add_argument("--cod", required=True)
        p.set_defaults(fn=fn)

    for name, fn in (("acct-add", cmd_acct_add), ("acct-role", cmd_acct_role)):
        p = sub.add_parser(name)
        p.add_argument("--un", required=True)
        p.add_argument("--dc", required=True)
        p.add_argument("--rol", required=True)
        p.set_defaults(fn=fn)

    p = sub.add_parser("acct-remove")
    p.add_argument("--un", required=True)
    p.add_argument("--dc", required=True)
    p.set_defaults(fn=cmd_acct_remove)

    p = sub.add_parser("privileges-sync")
    p.add_argument("--un", required=True)
    p.set_defaults(fn=cmd_privileges_sync)

    sub.add_parser("password-state").set_defaults(fn=cmd_password_state)
    p = sub.add_parser("clear-must-change")
    p.add_argument("--un", required=True)
    p.set_defaults(fn=cmd_clear_must_change)
    sub.add_parser("must-change-all").set_defaults(fn=cmd_must_change_all)

    p = sub.add_parser("must-change")
    p.add_argument("--un", required=True)
    p.set_defaults(fn=cmd_must_change)

    p = sub.add_parser("ensure-role")
    p.add_argument("--cod", required=True)
    p.set_defaults(fn=cmd_ensure_role)

    p = sub.add_parser("diff")
    p.add_argument("--db", required=True)
    p.add_argument("--mode", default="FORCE")
    p.set_defaults(fn=cmd_diff)

    sub.add_parser("commands").set_defaults(fn=cmd_commands)

    p = sub.add_parser("command")
    p.add_argument("--name", required=True)
    p.add_argument("--db", required=True)
    p.add_argument("--param", default="")
    p.set_defaults(fn=cmd_command)

    sub.add_parser("status").set_defaults(fn=cmd_status)

    p = sub.add_parser("audit")
    p.add_argument("--db", default="")
    p.add_argument("--un", default="")
    p.add_argument("--actions", default="")
    p.add_argument("--hours", type=int, default=72)
    p.add_argument("--limit", type=int, default=500)
    p.set_defaults(fn=cmd_audit)

    sub.add_parser("logs-meta").set_defaults(fn=cmd_logs_meta)

    p = sub.add_parser("logs")
    p.add_argument("--file", default="")
    p.add_argument("--db", default="")
    p.add_argument("--gens", type=int, default=1)
    p.add_argument("--since", default="")
    p.add_argument("--limit", type=int, default=1500)
    p.set_defaults(fn=cmd_logs)

    p = sub.add_parser("logs-clear")
    p.add_argument("--keep", required=True, choices=("all", "day", "week"))
    p.add_argument("--file", default="")
    p.set_defaults(fn=cmd_logs_clear)

    sub.add_parser("once-history").set_defaults(fn=cmd_once_history)

    from routes.admin_cli import admin_backup
    admin_backup.register(sub)
    from routes.admin_cli import admin_snapshot
    admin_snapshot.register(sub)

    p = sub.add_parser("requests")
    p.add_argument("--state", default="InAsteptare")
    p.set_defaults(fn=cmd_requests)

    p = sub.add_parser("request")
    p.add_argument("--id", type=int, required=True)
    p.set_defaults(fn=cmd_request)

    p = sub.add_parser("request-plan")
    p.add_argument("--id", type=int, required=True)
    p.add_argument("--db", default="")
    p.add_argument("--codes", default="")
    p.set_defaults(fn=cmd_request_plan)

    p = sub.add_parser("request-approve")
    p.add_argument("--id", type=int, required=True)
    p.add_argument("--db", required=True)
    p.add_argument("--codes", default="")
    p.set_defaults(fn=cmd_request_approve)

    p = sub.add_parser("request-reject")
    p.add_argument("--id", type=int, required=True)
    p.add_argument("--motiv", required=True)
    p.set_defaults(fn=cmd_request_reject)

    p = sub.add_parser("request-link")
    p.add_argument("--id", type=int, required=True)
    p.set_defaults(fn=cmd_request_link)

    p = sub.add_parser("visits")
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--bots", action="store_true")
    p.set_defaults(fn=cmd_visits)
    from routes.admin_cli import vercon_reports
    vercon_reports.register(sub)
    from routes.admin_cli import admin_demo
    admin_demo.register(sub)
    return parser


def _emit(payload):
    sys.stdout.buffer.write(json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"))
    sys.stdout.buffer.write(b"\n")
    sys.stdout.buffer.flush()


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        _emit({"ok": True, "data": args.fn(args)})
        return 0
    except AdminError as err:
        _emit({"ok": False, "error": str(err)})
        return 1
    except SchemaSyncError as err:
        _emit({"ok": False, "error": str(err)})
        return 1
    except mysql.connector.Error as err:
        _emit({"ok": False, "error": "Eroare MariaDB [%s]: %s" % (getattr(err, "errno", "?"), err)})
        return 1
    except Exception as err:  # the last wall: a traceback on stdout would break the JSON the caller expects
        _emit({"ok": False, "error": "%s: %s" % (type(err).__name__, err)})
        return 2


if __name__ == "__main__":
    sys.exit(main())
