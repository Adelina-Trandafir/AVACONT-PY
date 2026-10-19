"""Account links and application roles managed by KBotAdmin."""
from routes.admin_cli.admin_common import (
    AdminError, KNOWN_ROLES, _check_name, _exec, _journal, _resolve_db, _rows,
)
from routes.schema_sync.schema_common import COMMON_DB, connect
from routes.admin_cli.mariadb_privileges import changing


def cmd_accounts(args):
    conn = connect()
    try:
        where, params = "", []
        if args.db:
            where, params = "WHERE uu.DC = %s", [args.db]
        links = _rows(conn,
                      "SELECT uu.UN, uu.DC, uu.Rol, uu.LastSS FROM `%s`.`Unitati_Utilizatori` uu %s "
                      "ORDER BY uu.UN, uu.DC" % (COMMON_DB, where), params)
        granted = {}
        for r in _rows(conn,
                       "SELECT ur.UN, ur.DC, ro.Cod FROM `%s`.`Utilizatori_Roluri` ur "
                       "JOIN `%s`.`Roluri` ro ON ro.IdRol = ur.IdRol" % (COMMON_DB, COMMON_DB)):
            granted.setdefault((r["UN"].casefold(), r["DC"].casefold()), []).append(r["Cod"])
        out = []
        for link in links:
            out.append({"un": link["UN"], "dc": link["DC"], "rol": link["Rol"], "last_ss": link["LastSS"],
                        "roles": sorted(granted.get((link["UN"].casefold(), link["DC"].casefold()), []))})
        return {"accounts": out}
    finally:
        conn.close()


def cmd_roles(args):
    conn = connect()
    try:
        roles = _rows(conn, "SELECT IdRol, Cod, Sectiune, Denumire FROM `%s`.`Roluri` ORDER BY Sectiune, IdRol"
                      % COMMON_DB)
        ops = {}
        for r in _rows(conn, "SELECT IdRol, Operatie FROM `%s`.`Roluri_Operatii`" % COMMON_DB):
            ops.setdefault(r["IdRol"], []).append(r["Operatie"])
        return {"roles": [{"id": r["IdRol"], "cod": r["Cod"], "section": r["Sectiune"], "name": r["Denumire"],
                           "operations": sorted(ops.get(r["IdRol"], []))} for r in roles],
                "known_missing": [cod for cod in KNOWN_ROLES if cod not in {r["Cod"] for r in roles}]}
    finally:
        conn.close()


def _link_exists(conn, un, dc):
    return bool(_rows(conn, "SELECT 1 AS x FROM `%s`.`Unitati_Utilizatori` WHERE UN=%%s AND DC=%%s" % COMMON_DB,
                      (un, dc)))


def _role_id(conn, cod):
    found = _rows(conn, "SELECT IdRol, Sectiune FROM `%s`.`Roluri` WHERE Cod=%%s" % COMMON_DB, (cod,))
    if not found:
        raise AdminError("Rolul «%s» nu exista pe server." % cod)
    return found[0]["IdRol"], found[0]["Sectiune"]


def _role_codes(conn, spec):
    """'@AD' = every role of the ADECHIT section; otherwise a comma list of role codes."""
    if spec == "@AD":
        return [r["Cod"] for r in _rows(
            conn, "SELECT Cod FROM `%s`.`Roluri` WHERE Sectiune='AD' ORDER BY IdRol" % COMMON_DB)]
    codes = [c.strip() for c in spec.split(",") if c.strip()]
    if not codes:
        raise AdminError("Niciun rol dat.")
    return codes


def cmd_grant(args):
    _check_name(args.un, "Contul")
    conn = connect()
    try:
        with changing(conn, args.un) as change:
            dc = _resolve_db(conn, args.dc)[0]
            if not _link_exists(conn, args.un, dc):
                raise AdminError("Contul %s nu este legat de baza %s. Adauga-l intai la baza." % (args.un, dc))
            total = 0
            for cod in _role_codes(conn, args.cod):
                role_id, section = _role_id(conn, cod)
                if section == "KB":
                    raise AdminError("Rolurile K-BOT se schimba prin rolul legaturii cont-baza, nu prin acordare.")
                changed = _exec(conn, "INSERT IGNORE INTO `%s`.`Utilizatori_Roluri` (UN, DC, IdRol) VALUES (%%s,%%s,%%s)"
                                % COMMON_DB, (args.un, dc, role_id))
                _journal(conn, "ADMIN_GRANT", dc, args.un, cod, "OK" if changed else "DEJA")
                total += changed
            sql_privileges = change.finish()
            return {"sql_privileges": sql_privileges, "changed": total, "cod": args.cod}
    finally:
        conn.close()


def cmd_revoke(args):
    _check_name(args.un, "Contul")
    conn = connect()
    try:
        with changing(conn, args.un) as change:
            dc = _resolve_db(conn, args.dc)[0]
            total = 0
            for cod in _role_codes(conn, args.cod):
                role_id, _section = _role_id(conn, cod)
                changed = _exec(conn, "DELETE FROM `%s`.`Utilizatori_Roluri` WHERE UN=%%s AND DC=%%s AND IdRol=%%s"
                                % COMMON_DB, (args.un, dc, role_id))
                _journal(conn, "ADMIN_REVOKE", dc, args.un, cod, "OK" if changed else "NIMIC")
                total += changed
            sql_privileges = change.finish()
            return {"sql_privileges": sql_privileges, "changed": total, "cod": args.cod}
    finally:
        conn.close()


def _kb_roles(conn):
    return sorted(r["Cod"][3:] for r in _rows(
        conn, "SELECT Cod FROM `%s`.`Roluri` WHERE Sectiune='KB'" % COMMON_DB))


def _kb_role_name(conn, wanted):
    for name in _kb_roles(conn):
        if name.casefold() == (wanted or "").casefold():
            return name.title()
    raise AdminError("Rolul K-BOT «%s» nu exista. Cele valide: %s." % (wanted, ", ".join(_kb_roles(conn))))


def cmd_acct_add(args):
    _check_name(args.un, "Contul")
    conn = connect()
    try:
        with changing(conn, args.un) as change:
            dc = _resolve_db(conn, args.dc)[0]
            rol = _kb_role_name(conn, args.rol)
            if _link_exists(conn, args.un, dc):
                raise AdminError("Contul %s este deja legat de baza %s." % (args.un, dc))
            _exec(conn, "INSERT INTO `%s`.`Unitati_Utilizatori` (UN, DC, Rol) VALUES (%%s,%%s,%%s)" % COMMON_DB,
                  (args.un, dc, rol))
            _journal(conn, "ADMIN_ACCT_ADD", dc, args.un, rol)
            sql_privileges = change.finish()
            return {"sql_privileges": sql_privileges, "un": args.un, "dc": dc, "rol": rol}
    finally:
        conn.close()


def cmd_acct_role(args):
    _check_name(args.un, "Contul")
    conn = connect()
    try:
        with changing(conn, args.un) as change:
            dc = _resolve_db(conn, args.dc)[0]
            rol = _kb_role_name(conn, args.rol)
            if not _link_exists(conn, args.un, dc):
                raise AdminError("Contul %s nu este legat de baza %s." % (args.un, dc))
            _exec(conn, "UPDATE `%s`.`Unitati_Utilizatori` SET Rol=%%s WHERE UN=%%s AND DC=%%s" % COMMON_DB,
                  (rol, args.un, dc))
            _journal(conn, "ADMIN_ACCT_ROLE", dc, args.un, rol)
            sql_privileges = change.finish()
            return {"sql_privileges": sql_privileges, "un": args.un, "dc": dc, "rol": rol}
    finally:
        conn.close()


def cmd_acct_remove(args):
    _check_name(args.un, "Contul")
    conn = connect()
    try:
        with changing(conn, args.un) as change:
            dc = _resolve_db(conn, args.dc)[0]
            if not _link_exists(conn, args.un, dc):
                raise AdminError("Contul %s nu este legat de baza %s." % (args.un, dc))
            extra = _exec(conn, "DELETE FROM `%s`.`Utilizatori_Roluri` WHERE UN=%%s AND DC=%%s" % COMMON_DB,
                          (args.un, dc))
            _exec(conn, "DELETE FROM `%s`.`Unitati_Utilizatori` WHERE UN=%%s AND DC=%%s" % COMMON_DB, (args.un, dc))
            _journal(conn, "ADMIN_ACCT_REMOVE", dc, args.un, "roluri extra sterse: %d" % extra)
            sql_privileges = change.finish()
            return {"sql_privileges": sql_privileges, "un": args.un, "dc": dc, "extra_roles_removed": extra}
    finally:
        conn.close()


def cmd_privileges_sync(args):
    """Apply the full existing policy without changing a single application role."""
    _check_name(args.un, "Contul")
    conn = connect()
    try:
        with changing(conn, args.un) as change:
            return {"un": args.un, "sql_privileges": change.finish()}
    finally:
        conn.close()