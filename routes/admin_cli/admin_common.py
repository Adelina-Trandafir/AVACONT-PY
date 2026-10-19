"""Shared SQL plumbing for the private KBotAdmin CLI. No schema changes."""
import logging
import re
from datetime import datetime

from routes.schema_sync.schema_common import COMMON_DB, FORBIDDEN_TARGETS, list_unit_databases

JOURNAL_ACTOR_DEFAULT = "scavatarsoft@gmail.com"

# Roles created on request (ensure-role): code -> (section, name, operation).
KNOWN_ROLES = {
    "AD_EDIT_INCHISA": ("AD", "ADECHIT - editare prezenta in ultima luna inchisa", "edit_closed"),
}

_RX_NAME = re.compile(r"^[A-Za-z0-9_.@+-]{1,80}$")
_RX_DB = re.compile(r"^[A-Za-z0-9_]{1,64}$")


class AdminError(Exception):
    """A refusal the operator can read."""


# ---------------------------------------------------------------------------
# Plumbing
# ---------------------------------------------------------------------------
def _rows(conn, sql, params=()):
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(sql, params)
        return cur.fetchall()
    finally:
        cur.close()


def _exec(conn, sql, params=()):
    cur = conn.cursor()
    try:
        cur.execute(sql, params)
        return cur.rowcount
    finally:
        cur.close()


def _q(name):
    return "`" + str(name).replace("`", "``") + "`"


def _quiet_logger():
    quiet = logging.getLogger("admin_cli.quiet")
    quiet.propagate = False
    if not quiet.handlers:
        quiet.addHandler(logging.NullHandler())
    quiet.setLevel(logging.INFO)
    return quiet


def _check_name(value, what):
    if not value or not _RX_NAME.match(value):
        raise AdminError("%s invalid." % what)
    return value


def _unit_dbs(conn):
    return [d["name"] for d in list_unit_databases(conn) if d["exists"]]


def _resolve_db(conn, wanted):
    """One existing unit database, or all of them for ALL. Never the common or the source schema."""
    existing = _unit_dbs(conn)
    if (wanted or "").upper() == "ALL":
        return existing
    if not wanted or not _RX_DB.match(wanted) or wanted in FORBIDDEN_TARGETS:
        raise AdminError("Baza ceruta nu este valida.")
    for name in existing:
        if name.casefold() == wanted.casefold():
            return [name]
    raise AdminError("Baza «%s» nu exista pe server." % wanted)


def _actor():
    try:
        import config
        emails = getattr(config, "PORTAL_ADMIN_EMAILS", None) or ()
        return list(emails)[0] if emails else JOURNAL_ACTOR_DEFAULT
    except Exception:
        return JOURNAL_ACTOR_DEFAULT


def _journal(conn, action, dc, target, details, result="OK"):
    _exec(conn,
          "INSERT INTO `%s`.`Jurnal` (Moment, UN, DC, Actiune, Tinta, Detalii, Rezultat, Masina, IP) "
          "VALUES (NOW(3), %%s, %%s, %%s, %%s, %%s, %%s, %%s, %%s)" % COMMON_DB,
          (_actor(), dc, action, target, details, result, "KBotAdmin", "ssh"))


def _iso(value):
    return value.strftime("%Y-%m-%dT%H:%M:%S") if isinstance(value, datetime) else value


