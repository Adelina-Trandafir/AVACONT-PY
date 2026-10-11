"""Neutral db_name validator, shared by modern routes and the legacy admin module.

A database name cannot be passed as a %s parameter to connect(), so it is validated
whitelist-only. Lives here (not in routes.admin) so modern routes do not depend on a
blueprint that is being retired (security plan, S2).
"""
import re

DB_NAME_REGEX = re.compile(r'^[A-Za-z0-9_]+$')


def validate_db_name(db_name):
    if not isinstance(db_name, str):
        raise ValueError("db_name invalid")
    db_name = db_name.strip()
    if not db_name or not DB_NAME_REGEX.fullmatch(db_name):
        raise ValueError("db_name invalid")
    return db_name
