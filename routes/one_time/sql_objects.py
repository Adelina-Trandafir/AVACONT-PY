"""
sql_objects.py -- what the statements of a query change in the STRUCTURE of a database, and whether the
database really has it (KBotAdmin, page «Interogare pe SURSA»; sliceless).

READ. `describe(statements)` turns the statements into a list of objects, one per table / column / index /
primary key / foreign key a statement creates, changes or drops. A statement that is not structure (INSERT,
UPDATE, ...) becomes one object of kind DATA that names the table when it can. Nothing is guessed: a
statement the reader does not understand is kept as DATA with an empty table.

CHECK. `verify(conn, db, objects)` replays the objects in order to get the FINAL state each one should have
(a column added and dropped in the same file ends absent) and asks information_schema whether `db` is in
that state. Result per object: ok | missing | still_there | unchecked (an index or key without a name cannot
be looked up).

Object fields (ASCII keys on the wire):
    stmt    1-based number of the statement
    kind    TABLE | COLUMN | INDEX | PK | FK | DATA
    action  CREATE | ALTER | DROP | RENAME (TABLE); ADD | MODIFY | DROP | RENAME (the rest); the first word (DATA)
    table   the table (never qualified with a database)
    name    the column / index / key; for a table RENAME the new name; empty otherwise
    old     the old name for a RENAME of a column or index
    sql     the statement, on one line, cut at 200 characters
"""

import re

MAX_SQL_SHOWN = 200

_ID = r"(?:`[^`]+`|[A-Za-z0-9_$]+)"
_TABLE = r"(?:" + _ID + r"\s*\.\s*)?(" + _ID + r")"

_RX_CREATE_TABLE = re.compile(
    r"^CREATE\s+(?:OR\s+REPLACE\s+)?(?:TEMPORARY\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?" + _TABLE + r"\s*", re.I)
_RX_DROP_TABLE = re.compile(r"^DROP\s+(?:TEMPORARY\s+)?TABLE\s+(?:IF\s+EXISTS\s+)?(.+)$", re.I | re.S)
_RX_ALTER = re.compile(
    r"^ALTER\s+(?:ONLINE\s+)?(?:IGNORE\s+)?TABLE\s+(?:IF\s+EXISTS\s+)?" + _TABLE + r"\s+(.*)$", re.I | re.S)
_RX_CREATE_INDEX = re.compile(
    r"^CREATE\s+(?:OR\s+REPLACE\s+)?(?:UNIQUE\s+|FULLTEXT\s+|SPATIAL\s+)?INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?("
    + _ID + r")\s+(?:USING\s+\w+\s+)?ON\s+" + _TABLE, re.I)
_RX_DROP_INDEX = re.compile(r"^DROP\s+INDEX\s+(?:IF\s+EXISTS\s+)?(" + _ID + r")\s+ON\s+" + _TABLE, re.I)
_RX_DML_TABLE = re.compile(
    r"^(?:INSERT(?:\s+IGNORE)?\s+INTO|REPLACE\s+INTO|UPDATE(?:\s+IGNORE)?|DELETE\s+FROM|TRUNCATE(?:\s+TABLE)?)\s+"
    + _TABLE, re.I)

_RX_CONSTRAINT = re.compile(
    r"^CONSTRAINT\s+(?:(" + _ID + r")\s+)?(PRIMARY\s+KEY|UNIQUE|FOREIGN\s+KEY|CHECK)\b", re.I)
_RX_PRIMARY = re.compile(r"^PRIMARY\s+KEY\b", re.I)
_RX_FOREIGN = re.compile(r"^FOREIGN\s+KEY\s*(" + _ID + r")?\s*\(", re.I)
_RX_INDEX_ITEM = re.compile(
    r"^(?:UNIQUE(?:\s+(?:KEY|INDEX))?|(?:FULLTEXT|SPATIAL)(?:\s+(?:KEY|INDEX))?|KEY|INDEX)\b\s*"
    r"(?:IF\s+NOT\s+EXISTS\s+)?(" + _ID + r")?\s*(?:USING\s+\w+\s*)?\(", re.I)
_RX_SKIP_ITEM = re.compile(r"^(?:CHECK|LIKE|PERIOD)\b", re.I)
_RX_FIRST_WORD = re.compile(r"^[A-Za-z]+")


def _ident(token):
    token = (token or "").strip()
    if len(token) >= 2 and token.startswith("`") and token.endswith("`"):
        return token[1:-1].replace("``", "`")
    return token


def _split_top(text):
    """`text` cut at the commas that are not inside parentheses or quotes."""
    parts, buffer, depth, quote, i = [], [], 0, None, 0
    while i < len(text):
        c = text[i]
        if quote:
            buffer.append(c)
            if c == "\\" and quote != "`" and i + 1 < len(text):
                buffer.append(text[i + 1])
                i += 2
                continue
            if c == quote:
                quote = None
        elif c in ("'", '"', "`"):
            quote = c
            buffer.append(c)
        elif c == "(":
            depth += 1
            buffer.append(c)
        elif c == ")":
            depth -= 1
            buffer.append(c)
        elif c == "," and depth == 0:
            parts.append("".join(buffer).strip())
            buffer = []
        else:
            buffer.append(c)
        i += 1
    tail = "".join(buffer).strip()
    if tail:
        parts.append(tail)
    return [p for p in parts if p]


def _matching(text, start):
    """Index of the `)` that closes the `(` at `start`, or -1."""
    depth, quote, i = 0, None, start
    while i < len(text):
        c = text[i]
        if quote:
            if c == "\\" and quote != "`":
                i += 2
                continue
            if c == quote:
                quote = None
        elif c in ("'", '"', "`"):
            quote = c
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _obj(n, kind, action, table, name="", old=""):
    return {"stmt": n, "kind": kind, "action": action, "table": table, "name": name, "old": old, "sql": ""}


# ---------------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------------

def _element(n, table, text, out):
    """One element of a CREATE TABLE body or of an ALTER ... ADD: a column or a constraint."""
    text = text.strip()
    if not text:
        return
    m = _RX_CONSTRAINT.match(text)
    if m:
        word = re.sub(r"\s+", " ", m.group(2).upper())
        name = _ident(m.group(1)) if m.group(1) else ""
        if word == "PRIMARY KEY":
            out.append(_obj(n, "PK", "ADD", table, "PRIMARY"))
        elif word == "FOREIGN KEY":
            out.append(_obj(n, "FK", "ADD", table, name))
        elif word == "UNIQUE":
            out.append(_obj(n, "INDEX", "ADD", table, name))
        return
    if _RX_PRIMARY.match(text):
        out.append(_obj(n, "PK", "ADD", table, "PRIMARY"))
        return
    m = _RX_FOREIGN.match(text)
    if m:
        out.append(_obj(n, "FK", "ADD", table, _ident(m.group(1)) if m.group(1) else ""))
        return
    m = _RX_INDEX_ITEM.match(text)
    if m:
        out.append(_obj(n, "INDEX", "ADD", table, _ident(m.group(1)) if m.group(1) else ""))
        return
    if _RX_SKIP_ITEM.match(text):
        return
    if text.startswith("`"):
        close = text.find("`", 1)
        name = _ident(text[:close + 1]) if close > 0 else ""
    else:
        found = re.match(r"[A-Za-z0-9_$]+", text)
        name = found.group(0) if found else ""
    if name:
        out.append(_obj(n, "COLUMN", "ADD", table, name))


def _create_table(n, statement, match, out):
    table = _ident(match.group(1))
    out.append(_obj(n, "TABLE", "CREATE", table))
    rest = statement[match.end():]
    if rest.startswith("("):
        close = _matching(rest, 0)
        if close > 0:
            for item in _split_top(rest[1:close]):
                _element(n, table, item, out)


def _alter_add(n, table, body, out):
    body = body.lstrip()
    found = re.match(r"^COLUMN\b\s*", body, re.I)
    if found:
        body = body[found.end():]
    found = re.match(r"^IF\s+NOT\s+EXISTS\s+", body, re.I)
    if found:
        body = body[found.end():]
    if body.startswith("("):
        close = _matching(body, 0)
        if close > 0:
            for item in _split_top(body[1:close]):
                _element(n, table, item, out)
        return
    _element(n, table, body, out)


def _alter_spec(n, table, spec, out):
    m = re.match(r"^(ADD|DROP|MODIFY|CHANGE|RENAME|ALTER)\b\s*(.*)$", spec, re.I | re.S)
    if not m:
        out.append(_obj(n, "TABLE", "ALTER", table))
        return
    verb, body = m.group(1).upper(), m.group(2).strip()
    if verb == "ADD":
        _alter_add(n, table, body, out)
    elif verb == "DROP":
        if re.match(r"^PRIMARY\s+KEY\b", body, re.I):
            out.append(_obj(n, "PK", "DROP", table, "PRIMARY"))
            return
        found = re.match(r"^FOREIGN\s+KEY\s+(?:IF\s+EXISTS\s+)?(" + _ID + r")", body, re.I)
        if found:
            out.append(_obj(n, "FK", "DROP", table, _ident(found.group(1))))
            return
        found = re.match(r"^(?:INDEX|KEY)\s+(?:IF\s+EXISTS\s+)?(" + _ID + r")", body, re.I)
        if found:
            out.append(_obj(n, "INDEX", "DROP", table, _ident(found.group(1))))
            return
        found = re.match(r"^CONSTRAINT\s+(?:IF\s+EXISTS\s+)?(" + _ID + r")", body, re.I)
        if found:
            out.append(_obj(n, "FK", "DROP", table, _ident(found.group(1))))
            return
        if re.match(r"^CHECK\b", body, re.I):
            return
        found = re.match(r"^(?:COLUMN\s+)?(?:IF\s+EXISTS\s+)?(" + _ID + r")", body, re.I)
        if found:
            out.append(_obj(n, "COLUMN", "DROP", table, _ident(found.group(1))))
    elif verb == "MODIFY":
        found = re.match(r"^(?:COLUMN\s+)?(?:IF\s+EXISTS\s+)?(" + _ID + r")", body, re.I)
        if found:
            out.append(_obj(n, "COLUMN", "MODIFY", table, _ident(found.group(1))))
    elif verb == "CHANGE":
        found = re.match(r"^(?:COLUMN\s+)?(?:IF\s+EXISTS\s+)?(" + _ID + r")\s+(" + _ID + r")", body, re.I)
        if found:
            out.append(_obj(n, "COLUMN", "RENAME", table, _ident(found.group(2)), _ident(found.group(1))))
    elif verb == "RENAME":
        found = re.match(r"^COLUMN\s+(" + _ID + r")\s+TO\s+(" + _ID + r")", body, re.I)
        if found:
            out.append(_obj(n, "COLUMN", "RENAME", table, _ident(found.group(2)), _ident(found.group(1))))
            return
        found = re.match(r"^(?:INDEX|KEY)\s+(" + _ID + r")\s+TO\s+(" + _ID + r")", body, re.I)
        if found:
            out.append(_obj(n, "INDEX", "RENAME", table, _ident(found.group(2)), _ident(found.group(1))))
            return
        found = re.match(r"^(?:TO\s+|AS\s+)?(?:" + _ID + r"\s*\.\s*)?(" + _ID + r")\s*$", body, re.I)
        if found:
            out.append(_obj(n, "TABLE", "RENAME", table, _ident(found.group(1))))
    else:
        found = re.match(r"^COLUMN\s+(" + _ID + r")", body, re.I)
        if found:
            out.append(_obj(n, "COLUMN", "MODIFY", table, _ident(found.group(1))))
        else:
            out.append(_obj(n, "TABLE", "ALTER", table))


def _parse_one(n, statement, out):
    text = statement.strip()
    m = _RX_CREATE_TABLE.match(text)
    if m:
        _create_table(n, text, m, out)
        return
    m = _RX_DROP_TABLE.match(text)
    if m:
        for part in _split_top(m.group(1)):
            found = re.match(r"^(?:" + _ID + r"\s*\.\s*)?(" + _ID + r")", part.strip())
            if found:
                out.append(_obj(n, "TABLE", "DROP", _ident(found.group(1))))
        return
    m = _RX_ALTER.match(text)
    if m:
        table = _ident(m.group(1))
        before = len(out)
        for spec in _split_top(m.group(2)):
            _alter_spec(n, table, spec, out)
        if len(out) == before:
            out.append(_obj(n, "TABLE", "ALTER", table))
        return
    m = _RX_CREATE_INDEX.match(text)
    if m:
        out.append(_obj(n, "INDEX", "ADD", _ident(m.group(2)), _ident(m.group(1))))
        return
    m = _RX_DROP_INDEX.match(text)
    if m:
        out.append(_obj(n, "INDEX", "DROP", _ident(m.group(2)), _ident(m.group(1))))
        return
    word = _RX_FIRST_WORD.match(text)
    dml = _RX_DML_TABLE.match(text)
    out.append(_obj(n, "DATA", word.group(0).upper() if word else "?", _ident(dml.group(1)) if dml else ""))


def describe(statements):
    """The objects the statements touch, in statement order."""
    out = []
    for n, statement in enumerate(statements, start=1):
        before = len(out)
        _parse_one(n, statement, out)
        shown = " ".join(statement.split())[:MAX_SQL_SHOWN]
        for item in out[before:]:
            item["sql"] = shown
    return out


# ---------------------------------------------------------------------------------
# Checking
# ---------------------------------------------------------------------------------

def _entry(item, expect, table, name, unchecked=False):
    return {"stmt": item["stmt"], "kind": item["kind"], "table": table, "name": name,
            "expect": expect, "unchecked": unchecked}


def final_state(objects):
    """(kind, table, name) in lower case -> what the database should hold at the end of the file."""
    state = {}
    unnamed = 0

    def children(table):
        return [k for k in state if k[1] == table and k[0] != "TABLE"]

    for item in objects:
        kind, action = item["kind"], item["action"]
        if kind == "DATA":
            continue
        table = item["table"].lower()
        name = item["name"]
        if kind == "TABLE":
            if action == "CREATE":
                state[("TABLE", table, "")] = _entry(item, "present", item["table"], "")
            elif action == "ALTER":
                state.setdefault(("TABLE", table, ""), _entry(item, "present", item["table"], ""))
            elif action == "DROP":
                for key in children(table):
                    del state[key]
                state[("TABLE", table, "")] = _entry(item, "absent", item["table"], "")
            elif action == "RENAME":
                new = name.lower()
                for key in children(table):
                    moved = state.pop(key)
                    moved["table"] = name
                    state[(key[0], new, key[2])] = moved
                state[("TABLE", table, "")] = _entry(item, "absent", item["table"], "")
                state[("TABLE", new, "")] = _entry(item, "present", name, "")
            continue
        gone = state.get(("TABLE", table, ""))
        if gone is not None and gone["expect"] == "absent":
            continue
        if kind == "COLUMN":
            if action in ("ADD", "MODIFY"):
                state[("COLUMN", table, name.lower())] = _entry(item, "present", item["table"], name)
            elif action == "DROP":
                state[("COLUMN", table, name.lower())] = _entry(item, "absent", item["table"], name)
            elif action == "RENAME":
                state[("COLUMN", table, item["old"].lower())] = _entry(item, "absent", item["table"], item["old"])
                state[("COLUMN", table, name.lower())] = _entry(item, "present", item["table"], name)
            continue
        # INDEX, PK, FK
        if action == "ADD":
            if name:
                state[(kind, table, name.lower())] = _entry(item, "present", item["table"], name)
            else:
                unnamed += 1
                state[(kind, table, "#%d" % unnamed)] = _entry(item, "present", item["table"], "(fără nume)", True)
        elif action == "DROP":
            state[(kind, table, name.lower())] = _entry(item, "absent", item["table"], name)
        elif action == "RENAME":
            state[(kind, table, item["old"].lower())] = _entry(item, "absent", item["table"], item["old"])
            state[(kind, table, name.lower())] = _entry(item, "present", item["table"], name)
    return state


def _pairs(query, conn, sql, db, tables):
    """{(table, name)} in lower case from a query that returns columns t and n, kept for the tables given (lower
    case); empty when no tables. The query filters on the schema only: a filter on TABLE_NAME is case sensitive on
    a server with lower_case_table_names=0 (a lower-cased name finds nothing), so the names are compared here."""
    if not tables:
        return set()
    rows = query(conn, sql, [db])
    return {(r["t"].lower(), r["n"].lower()) for r in rows if r["t"].lower() in tables}


def verify(query, conn, db, objects):
    """The final state of each object, with `state` ok | missing | still_there | unchecked."""
    state = final_state(objects)
    existing = {r["n"].lower() for r in query(
        conn, "SELECT TABLE_NAME AS n FROM information_schema.TABLES WHERE TABLE_SCHEMA = %s", (db,))}
    present = {k[1] for k in state if k[1] in existing}
    columns = _pairs(query, conn,
                     "SELECT TABLE_NAME AS t, COLUMN_NAME AS n FROM information_schema.COLUMNS "
                     "WHERE TABLE_SCHEMA = %s", db, present)
    indexes = _pairs(query, conn,
                     "SELECT DISTINCT TABLE_NAME AS t, INDEX_NAME AS n FROM information_schema.STATISTICS "
                     "WHERE TABLE_SCHEMA = %s", db, present)
    keys = _pairs(query, conn,
                  "SELECT TABLE_NAME AS t, CONSTRAINT_NAME AS n FROM information_schema.TABLE_CONSTRAINTS "
                  "WHERE CONSTRAINT_SCHEMA = %s AND CONSTRAINT_TYPE = 'FOREIGN KEY'",
                  db, present)
    checks = []
    for (kind, table, name), item in state.items():
        if item["unchecked"]:
            result = "unchecked"
        else:
            if kind == "TABLE":
                exists = table in existing
            elif kind == "COLUMN":
                exists = (table, name) in columns
            elif kind == "FK":
                exists = (table, name) in keys
            else:                                   # INDEX and PK
                exists = (table, name) in indexes
            if item["expect"] == "present":
                result = "ok" if exists else "missing"
            else:
                result = "ok" if not exists else "still_there"
        checks.append({"stmt": item["stmt"], "kind": kind, "table": item["table"], "name": item["name"],
                       "expect": item["expect"], "state": result})
    checks.sort(key=lambda c: (c["stmt"], c["table"].lower(), c["kind"], c["name"].lower()))
    return checks
