# routes/inregistrare/provizionare_drepturi.py
"""
Preflight of the provisioning job: does the provisioning account hold the rights the clone needs?

Step 2 clones every template table. A foreign key that points OUTSIDE the template (the dictionary
tables of AVACONT_COMUN) needs the REFERENCES right on the referenced table, and MariaDB reports a
missing one only when that CREATE TABLE runs -- after the database was already created, and only for
the FIRST table that fails. This check reads the same fact beforehand, from information_schema,
writes nothing, and lists EVERY table that is missing, so one GRANT round fixes them all.
"""
import re


def check_references(conn, template_db):
    """
    (problem, lines). `problem` is "" when every cross-schema foreign key of the template is covered;
    otherwise the sentence for the operator, and `lines` the GRANT statements to run as root.
    """
    cur = conn.cursor(buffered=True)
    cur.execute("SELECT CURRENT_USER()")
    account = _text(cur.fetchone()[0])
    user, _, host = account.partition("@")
    grantee = f"'{user}'@'{host}'"

    cur.execute(
        "SELECT DISTINCT REFERENCED_TABLE_SCHEMA, REFERENCED_TABLE_NAME "
        "FROM information_schema.KEY_COLUMN_USAGE "
        "WHERE TABLE_SCHEMA = %s AND REFERENCED_TABLE_SCHEMA IS NOT NULL "
        "AND REFERENCED_TABLE_SCHEMA <> %s ORDER BY 1, 2",
        (template_db, template_db),
    )
    needed = [(_text(r[0]), _text(r[1])) for r in cur.fetchall()]
    if not needed:
        return "", []

    cur.execute("SELECT 1 FROM information_schema.USER_PRIVILEGES "
                "WHERE GRANTEE = %s AND PRIVILEGE_TYPE = 'REFERENCES'", (grantee,))
    if cur.fetchone() is not None:
        return "", []

    cur.execute("SELECT TABLE_SCHEMA FROM information_schema.SCHEMA_PRIVILEGES "
                "WHERE GRANTEE = %s AND PRIVILEGE_TYPE = 'REFERENCES'", (grantee,))
    schema_patterns = [_text(r[0]) for r in cur.fetchall()]

    cur.execute("SELECT TABLE_SCHEMA, TABLE_NAME FROM information_schema.TABLE_PRIVILEGES "
                "WHERE GRANTEE = %s AND PRIVILEGE_TYPE = 'REFERENCES'", (grantee,))
    on_tables = {(_text(r[0]), _text(r[1])) for r in cur.fetchall()}

    cur.execute("SELECT DISTINCT TABLE_SCHEMA, TABLE_NAME FROM information_schema.COLUMN_PRIVILEGES "
                "WHERE GRANTEE = %s AND PRIVILEGE_TYPE = 'REFERENCES'", (grantee,))
    on_columns = {(_text(r[0]), _text(r[1])) for r in cur.fetchall()}

    missing = [
        (schema, table) for schema, table in needed
        if (schema, table) not in on_tables
        and (schema, table) not in on_columns
        and not any(_schema_matches(p, schema) for p in schema_patterns)
    ]
    if not missing:
        return "", []

    names = ", ".join(f"{schema}.{table}" for schema, table in missing)
    problem = (f"Contul de provizionare {account} nu are dreptul REFERENCES pe {len(missing)} "
               f"tabel(e) din care șablonul depinde: {names}. Fără el, pasul 2 (crearea bazei) eșuează. "
               "Dați dreptul (comenzile GRANT de mai sus, ca root) și revalidați cererea.")
    lines = [f"LIPSEȘTE dreptul REFERENCES pe {schema}.{table}" for schema, table in missing]
    lines.append("De rulat ca root pe server:")
    lines.extend(f"  GRANT SELECT, REFERENCES ON `{schema}`.`{table}` TO {grantee};"
                 for schema, table in missing)
    return problem, lines


def _schema_matches(pattern, name) -> bool:
    """A schema-level grant is stored as a pattern: `\\` escapes, `%` any run, `_` any one character."""
    regex = []
    chars = iter(pattern)
    for ch in chars:
        if ch == "\\":
            regex.append(re.escape(next(chars, "\\")))
        elif ch == "%":
            regex.append(".*")
        elif ch == "_":
            regex.append(".")
        else:
            regex.append(re.escape(ch))
    return re.fullmatch("".join(regex), name) is not None


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8")
    return "" if value is None else str(value)
