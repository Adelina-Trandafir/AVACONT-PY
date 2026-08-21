"""
schema_diff.py -- compare AVACONT_SURSA against a target and emit DDL.

Replaces proc_SchemaDiff_DDL and proc_SchemaDiff_CreateTable. Every rule
from those procedures is here, plus the collation repair, plus the
charset fixes they were missing.

Two things the SQL version got wrong that are fixed here:

  * Column definitions are emitted WITH their charset and collation.
    The procedures used COLUMN_TYPE alone, which carries no charset, so
    every added, renamed or recreated column silently took the target
    table's default -- which is the mismatch the collation repair exists
    to remove.

  * CREATE TABLE now carries ENGINE, DEFAULT CHARSET and ROW_FORMAT.
    Without ENGINE=InnoDB a foreign key added to the table later is
    accepted and ignored by some engines.

CREATE TABLE carries no foreign keys of its own. They are emitted as
separate ADD CONSTRAINT statements at priority 11, because tables are
created in alphabetical order and a key written inline can reference a
table the batch has not reached yet.

SAFE vs FORCE is unchanged: SAFE adds only. FORCE also modifies and
drops. Collation repair runs in BOTH -- it is a correctness fix, and
leaving a mismatch in place makes later key creation fail.
"""

from dataclasses import dataclass

from .schema_common import SOURCE_DB, SchemaSyncError, priority_of, query
from .schema_introspect import read_schema

# Rules that never route through the diff.
SYSTEM_SCHEMAS = {"information_schema", "performance_schema", "mysql", "sys"}

# How much detail the blocking-data report carries per key. A unit with a
# genuinely broken table can have tens of thousands of orphan rows, and a
# report nobody can open helps nobody -- so the COUNT is always exact and
# only the listing is capped.
MAX_DETAIL_ROWS = 500


def same_schema(a: str, b: str) -> bool:
    """True when two schema names denote the same schema.

    Compared case-insensitively on purpose. information_schema reports
    REFERENCED_TABLE_SCHEMA in whatever case the server stores it, and a
    server running with lower_case_table_names=1 folds it -- so an exact
    match against the SOURCE_DB constant silently fails there. What that
    failure costs is severe and quiet: the self-schema rewrite stops
    firing, and a unit ends up with a foreign key pointing into
    AVACONT_SURSA, the template every unit is cloned from.
    """
    if a is None or b is None:
        return False
    return a.lower() == b.lower()


@dataclass
class Statement:
    target_db: str
    table_name: str
    object_name: str
    object_type: str
    action_type: str
    ddl_sql: str
    is_destructive: bool = False
    error_msg: str = None

    @property
    def priority(self) -> int:
        return priority_of(self.object_type, self.action_type)


# ---------------------------------------------------------------------
# Fragment builders
# ---------------------------------------------------------------------

def q(name: str) -> str:
    """Backtick-quote an identifier, escaping embedded backticks."""
    return "`" + str(name).replace("`", "``") + "`"


def quote_literal(value: str) -> str:
    """Single-quote a string literal for DDL."""
    return "'" + str(value).replace("\\", "\\\\").replace("'", "''") + "'"


# Defaults that are functions, not literals, and must never be quoted.
_FUNCTION_DEFAULTS = {
    "current_timestamp", "current_timestamp()", "now()",
    "current_date", "current_date()", "curdate()",
    "current_time", "current_time()", "curtime()",
    "utc_timestamp", "utc_timestamp()", "utc_date()", "utc_time()",
    "uuid()", "null",
}

_NUMERIC_TYPES = {
    "int", "bigint", "smallint", "tinyint", "mediumint", "decimal", "numeric",
    "float", "double", "bit", "year",
}


def format_default(col) -> str:
    """Render the DEFAULT clause for one column, or '' if there is none.

    This is what fn_sql_default did on the server side, with the
    expression-vs-raw distinction handled explicitly instead of guessed.
    """
    if col.is_auto_increment:
        return ""
    if col.is_generated:
        return ""

    raw = col.default

    if raw is None:
        # No default at all. A nullable column defaults to NULL anyway;
        # spelling it out keeps generated DDL comparable.
        return " DEFAULT NULL" if col.is_nullable else ""

    text = str(raw)

    if col.expr_defaults:
        # MariaDB 10.2.7+: already a valid expression. The one case to
        # normalise is the literal string "NULL", which means DEFAULT NULL.
        if text.upper() == "NULL":
            return " DEFAULT NULL"
        return f" DEFAULT {text}"

    # Older servers hand back raw values.
    lowered = text.lower().strip()
    if lowered in _FUNCTION_DEFAULTS:
        return " DEFAULT NULL" if lowered == "null" else f" DEFAULT {text}"
    if col.data_type in _NUMERIC_TYPES:
        return f" DEFAULT {text}"
    return f" DEFAULT {quote_literal(text)}"


def format_charset(col) -> str:
    """CHARACTER SET / COLLATE for a character column, else ''.

    Always emitted. This is the fix for the whole class of bugs where a
    column inherits the wrong charset from its table.
    """
    if not col.charset:
        return ""
    return f" CHARACTER SET {col.charset} COLLATE {col.collation}"


def column_definition(col, name: str = None, comment: str = None) -> str:
    """Full column definition as it appears in CREATE or ALTER.

    `name` overrides the column name (used by CHANGE COLUMN).
    `comment` overrides the comment (used to strip a rename: marker).
    """
    parts = [q(name or col.name), " ", col.column_type]
    parts.append(format_charset(col))
    parts.append("" if col.is_nullable else " NOT NULL")
    if col.is_nullable and col.default is None and not col.is_generated:
        pass  # format_default emits DEFAULT NULL below
    parts.append(format_default(col))
    if col.extra:
        parts.append(f" {col.extra}")
    final_comment = col.effective_comment if comment is None else comment
    if final_comment:
        parts.append(f" COMMENT {quote_literal(final_comment)}")
    return "".join(parts)


def columns_differ(src, tgt) -> bool:
    """True when anything other than charset differs.

    effective_comment, not comment: a rename: marker is an instruction,
    and comparing it raw would make a renamed column look permanently
    out of sync with itself.
    """
    return (src.column_type != tgt.column_type
            or src.is_nullable != tgt.is_nullable
            or _norm_default(src) != _norm_default(tgt)
            or (src.extra or "") != (tgt.extra or "")
            or src.effective_comment != tgt.effective_comment)


def _norm_default(col) -> str:
    """Normalise a default so 'NULL' and None compare equal."""
    if col.default is None:
        return None
    text = str(col.default)
    return None if text.upper() == "NULL" else text


def charset_differs(src, tgt) -> bool:
    if not src.charset or not tgt.charset:
        return False
    return (src.charset != tgt.charset or src.collation != tgt.collation)


def is_narrowing(src, tgt) -> bool:
    """True when the target loses representational range.

    utf8mb4 -> anything else drops supplementary-plane characters.
    Romanian diacritics are BMP and survive; emoji and rare CJK do not.
    """
    return tgt.charset == "utf8mb4" and src.charset != "utf8mb4"


# ---------------------------------------------------------------------
# The diff
# ---------------------------------------------------------------------

class SchemaDiff:
    """Compares one target against the source and produces Statements."""

    def __init__(self, source, target, target_db, mode, logger, conn=None):
        self.src = source
        self.tgt = target
        self.db = target_db
        # Only needed by the referential-integrity check below, which is
        # the one part of the diff that has to look at DATA, not schema.
        self.conn = conn
        self.force = (mode.upper() == "FORCE")
        self.mode = mode.upper()
        self.log = logger
        self.statements = []
        # (table, column) pairs whose charset or collation is being
        # changed -- by the collation repair OR by a FORCE-mode MODIFY /
        # RENAME that carries a charset change with it. All three are
        # blocked by a foreign key in exactly the same way, so all three
        # must register here or the unblocking pass misses them.
        self._recollated = set()
        # (table, fk_name) already scheduled, so the unblocking pass
        # below does not emit a second DROP or a second ADD.
        self._fk_dropped = set()
        self._fk_created = set()
        # (table, fk_name) -> the definition each ADD CONSTRAINT will use.
        self._fk_defs = {}
        # (schema, table) -> {column: (charset, collation)}, for schemas
        # this run does not touch (AVACONT_COMUN). Cached: the FK checks
        # ask for the same handful of parent tables over and over.
        self._ext_columns = {}
        # One record per key that cannot be built, with the offending
        # values spelled out. Read by schema_generate, written as JSON.
        self.blocks = []

    # -- helper ------------------------------------------------------
    def _emit(self, table, obj_name, obj_type, action, sql,
              destructive=False, error=None):
        self.statements.append(Statement(
            target_db=self.db, table_name=table, object_name=obj_name,
            object_type=obj_type, action_type=action, ddl_sql=sql,
            is_destructive=destructive, error_msg=error))

    def _t(self, table_name) -> str:
        return f"{q(self.db)}.{q(table_name)}"

    # -- entry point -------------------------------------------------
    def run(self) -> list:
        self._tables()
        for name, src_tbl in sorted(self.src.tables.items()):
            tgt_tbl = self.tgt.tables.get(name)
            if tgt_tbl is None:
                continue          # handled by CREATE TABLE
            self._columns(src_tbl, tgt_tbl)
            self._primary_key(src_tbl, tgt_tbl)
            self._indexes(src_tbl, tgt_tbl)
            self._foreign_keys(src_tbl, tgt_tbl)
        self._unblock_collation()
        self._validate_foreign_keys()
        return self.statements

    # -- 1 / 2: tables -----------------------------------------------
    def _tables(self):
        for name in sorted(set(self.tgt.tables) - set(self.src.tables)):
            self._emit(name, None, "TABLE", "DROP",
                       f"DROP TABLE {self._t(name)};", destructive=True)

        for name in sorted(set(self.src.tables) - set(self.tgt.tables)):
            self._emit(name, None, "TABLE", "CREATE",
                       self._create_table(self.src.tables[name]))

    def _create_table(self, tbl) -> str:
        # A brand-new table has nothing to rename FROM, so the rename:
        # marker must not be written into it -- otherwise the marker
        # outlives the cleanup pass and the column carries a stale
        # instruction as its comment for good.
        lines = ["  " + column_definition(c, comment=c.comment_after_rename)
                 for c in tbl.columns_in_order()]

        pk = tbl.primary_key
        if pk:
            cols = ", ".join(q(c) for c in pk.columns)
            lines.append(f"  PRIMARY KEY ({cols})")

        for idx in sorted(tbl.indexes.values(), key=lambda i: i.name):
            if idx.is_primary:
                continue
            cols = ", ".join(q(c) for c in idx.columns)
            unique = "UNIQUE " if idx.unique else ""
            lines.append(f"  {unique}INDEX {q(idx.name)} ({cols})")

        # The foreign keys are deliberately NOT part of this statement.
        # Tables are created in alphabetical order, so a key inside
        # CREATE TABLE can point at a table that does not exist yet --
        # FX_Extrase referencing FX_Extrase_H is exactly that, and it
        # fails with errno 1005 / 150 before the second table is ever
        # created. Emitted separately at priority 11 instead, once every
        # table in the batch stands.
        for fk in sorted(tbl.foreign_keys.values(), key=lambda f: f.name):
            self._create_fk(tbl.name, fk.name, fk)

        # ENGINE / CHARSET / ROW_FORMAT taken from the source table, not
        # left to the server default: the keys added later need InnoDB,
        # and the charset must not be inherited from the target database.
        engine = tbl.engine or "InnoDB"
        charset = tbl.charset or "utf8"
        collation = tbl.collation or "utf8_general_ci"
        row_format = tbl.row_format or "Dynamic"

        return (f"CREATE TABLE {self._t(tbl.name)} (\n"
                + ",\n".join(lines)
                + f"\n) ENGINE={engine} DEFAULT CHARSET={charset} "
                  f"COLLATE={collation} ROW_FORMAT={row_format};")

    def _ref_schema(self, fk) -> str:
        """Where the key actually points once written into this target.

        A reference into the source schema means "this unit's own
        schema" -- rewritten. A reference elsewhere (AVACONT_COMUN) is
        left alone: that IS a cross-database link, on purpose.
        """
        return (self.db if same_schema(fk.ref_schema, SOURCE_DB)
                else fk.ref_schema)

    def _fk_clause(self, fk) -> str:
        cols = ", ".join(q(c) for c in fk.columns)
        ref_cols = ", ".join(q(c) for c in fk.ref_columns)
        # A reference into the source schema means "this unit's own
        # schema" -- rewrite it. A reference elsewhere (AVACONT_COMUN)
        # is left alone.
        ref_schema = self._ref_schema(fk)
        return (f"CONSTRAINT {q(fk.name)} FOREIGN KEY ({cols}) "
                f"REFERENCES {q(ref_schema)}.{q(fk.ref_table)} ({ref_cols}) "
                f"ON DELETE {fk.delete_rule} ON UPDATE {fk.update_rule}")

    # -- 3 / 4: columns ----------------------------------------------
    def _columns(self, src_tbl, tgt_tbl):
        # Old names referenced by a rename: marker -- must not be dropped.
        rename_sources = {c.rename_from for c in src_tbl.columns.values()
                          if c.rename_from}

        for col in src_tbl.columns_in_order():
            if col.is_generated:
                continue                       # generated columns skipped
            old_name = col.rename_from
            tgt_col = tgt_tbl.columns.get(col.name)

            # A rename is only OUTSTANDING while the new name is absent
            # from the target. Once it exists -- because the rename ran
            # earlier, or the table was created fresh -- the column is
            # an ordinary column and must go through the normal checks,
            # or its collation would never be repaired.
            if old_name and tgt_col is None:
                self._rename_column(src_tbl, tgt_tbl, col, old_name)
                continue

            if tgt_col is None:
                self._add_column(src_tbl, col)
            elif columns_differ(col, tgt_col):
                if self.force:
                    self._modify_column(src_tbl, col, tgt_col)
            elif charset_differs(col, tgt_col):
                self._repair_collation(src_tbl, col, tgt_col)

        # DROP COLUMN: in the target, absent from the source, and not the
        # old half of a pending rename.
        for name in sorted(set(tgt_tbl.columns) - set(src_tbl.columns)):
            if name in rename_sources:
                continue
            if tgt_tbl.columns[name].is_generated:
                continue
            self._emit(tgt_tbl.name, name, "COLUMN", "DROP",
                       f"ALTER TABLE {self._t(tgt_tbl.name)} "
                       f"DROP COLUMN {q(name)};", destructive=True)

    def _add_column(self, tbl, col):
        sql = (f"ALTER TABLE {self._t(tbl.name)} "
               f"ADD COLUMN {column_definition(col)}")
        # An auto_increment column must be a key from the moment it
        # exists. If the table already has a PK, replace it in the same
        # statement -- MariaDB will not tolerate the gap.
        if col.is_auto_increment:
            tgt_tbl = self.tgt.tables.get(tbl.name)
            has_pk = tgt_tbl is not None and tgt_tbl.primary_key is not None
            if has_pk:
                sql += f", DROP PRIMARY KEY, ADD PRIMARY KEY ({q(col.name)})"
            else:
                sql += " PRIMARY KEY"
        self._emit(tbl.name, col.name, "COLUMN", "ADD", sql + ";")

    def _modify_column(self, tbl, col, tgt_col=None):
        self._emit(tbl.name, col.name, "COLUMN", "MODIFY",
                   f"ALTER TABLE {self._t(tbl.name)} "
                   f"MODIFY COLUMN {column_definition(col)};")
        # A MODIFY that also moves the charset is blocked by a foreign
        # key just as the collation repair is -- errno 1832 on the
        # referencing side, 1833 on the referenced one. Register it so
        # the unblocking pass drops the key first.
        if tgt_col is not None and charset_differs(col, tgt_col):
            self._recollated.add((tbl.name, col.name))

    def _repair_collation(self, tbl, src_col, tgt_col):
        """Charset-only change. Runs in SAFE as well as FORCE.

        The definition is otherwise the TARGET's, so nothing but charset
        and collation moves. Type changes stay the job of MODIFY COLUMN.
        """
        definition = column_definition(tgt_col)
        # Swap the target's charset clause for the source's.
        old_clause = format_charset(tgt_col)
        new_clause = format_charset(src_col)
        definition = definition.replace(old_clause, new_clause, 1)
        self._emit(tbl.name, src_col.name, "COLLATION", "MODIFY",
                   f"ALTER TABLE {self._t(tbl.name)} "
                   f"MODIFY COLUMN {definition};",
                   destructive=is_narrowing(src_col, tgt_col))
        self._recollated.add((tbl.name, src_col.name))

    def _rename_column(self, src_tbl, tgt_tbl, col, old_name):
        """CHANGE COLUMN old -> new, driven by a rename: comment.

        Only called when the new name is absent from the target.
        """
        if old_name not in tgt_tbl.columns:
            self._emit(tgt_tbl.name, col.name, "COLUMN", "RENAME", "",
                       error=(f"RENAME imposibil: coloana veche "
                              f"`{old_name}` nu există în "
                              f"`{self.db}`.`{tgt_tbl.name}`"))
            return
        definition = column_definition(col, comment=col.comment_after_rename)
        self._emit(tgt_tbl.name, col.name, "COLUMN", "RENAME",
                   f"ALTER TABLE {self._t(tgt_tbl.name)} "
                   f"CHANGE COLUMN {q(old_name)} {definition};")
        # Same block as a MODIFY when the rename also moves the charset.
        # Registered under the OLD name: that is the name any existing
        # foreign key still refers to at the moment the key is dropped.
        if charset_differs(col, tgt_tbl.columns[old_name]):
            self._recollated.add((tgt_tbl.name, old_name))

    # -- 5 / 6 / 7: primary key --------------------------------------
    def _primary_key(self, src_tbl, tgt_tbl):
        src_pk = src_tbl.primary_key
        tgt_pk = tgt_tbl.primary_key

        if src_pk is None and tgt_pk is not None:
            self._emit(tgt_tbl.name, "PRIMARY", "PK", "DROP",
                       f"ALTER TABLE {self._t(tgt_tbl.name)} "
                       f"DROP PRIMARY KEY;", destructive=True)
            return

        if src_pk is None:
            return

        # An ADD COLUMN ... auto_increment already carried its own PK
        # clause; adding another here would be a duplicate.
        if self._pk_handled_by_add_column(src_tbl, tgt_tbl, src_pk):
            return

        cols = ", ".join(q(c) for c in src_pk.columns)

        if tgt_pk is None:
            self._emit(src_tbl.name, "PRIMARY", "PK", "CREATE",
                       f"ALTER TABLE {self._t(src_tbl.name)} "
                       f"ADD PRIMARY KEY ({cols});")
        elif src_pk.columns != tgt_pk.columns and self.force:
            # Combined: MariaDB will not leave an auto_increment column
            # keyless, even momentarily.
            self._emit(src_tbl.name, "PRIMARY", "PK", "MODIFY",
                       f"ALTER TABLE {self._t(src_tbl.name)} "
                       f"DROP PRIMARY KEY, ADD PRIMARY KEY ({cols});",
                       destructive=True)

    def _pk_handled_by_add_column(self, src_tbl, tgt_tbl, src_pk) -> bool:
        """True when the PK will be created by an ADD COLUMN statement."""
        for name in src_pk.columns:
            col = src_tbl.columns.get(name)
            if col is None:
                continue
            if col.is_auto_increment and name not in tgt_tbl.columns:
                return True
        return False

    # -- 8 / 9 / 10 / 11: indexes ------------------------------------
    def _indexes(self, src_tbl, tgt_tbl):
        src_idx = {n: i for n, i in src_tbl.indexes.items() if not i.is_primary}
        tgt_idx = {n: i for n, i in tgt_tbl.indexes.items() if not i.is_primary}

        for name in sorted(set(tgt_idx) - set(src_idx)):
            self._emit(tgt_tbl.name, name, "INDEX", "DROP",
                       f"DROP INDEX {q(name)} ON {self._t(tgt_tbl.name)};",
                       destructive=True)

        for name in sorted(src_idx):
            s = src_idx[name]
            t = tgt_idx.get(name)
            if t is None:
                self._emit(src_tbl.name, name, "INDEX", "CREATE",
                           self._create_index(src_tbl.name, s))
            elif (s.columns != t.columns or s.unique != t.unique) and self.force:
                self._emit(src_tbl.name, name, "INDEX", "DROP",
                           f"DROP INDEX {q(name)} ON {self._t(src_tbl.name)};",
                           destructive=True)
                self._emit(src_tbl.name, name, "INDEX", "CREATE",
                           self._create_index(src_tbl.name, s))

    def _create_index(self, table_name, idx) -> str:
        cols = ", ".join(q(c) for c in idx.columns)
        unique = "UNIQUE " if idx.unique else ""
        return (f"CREATE {unique}INDEX {q(idx.name)} "
                f"ON {self._t(table_name)} ({cols});")

    # -- 12 / 13 / 14 / 15: foreign keys -----------------------------
    def _foreign_keys(self, src_tbl, tgt_tbl):
        for name in sorted(set(tgt_tbl.foreign_keys)
                           - set(src_tbl.foreign_keys)):
            self._drop_fk(tgt_tbl.name, name)

        for name in sorted(src_tbl.foreign_keys):
            s = src_tbl.foreign_keys[name]
            t = tgt_tbl.foreign_keys.get(name)
            if t is None:
                self._create_fk(src_tbl.name, name, s)
            elif s.signature() != t.signature() and self.force:
                self._drop_fk(src_tbl.name, name)
                self._create_fk(src_tbl.name, name, s)

    def _drop_fk(self, table_name, fk_name):
        if (table_name, fk_name) in self._fk_dropped:
            return
        self._fk_dropped.add((table_name, fk_name))
        self._emit(table_name, fk_name, "FK", "DROP",
                   f"ALTER TABLE {self._t(table_name)} "
                   f"DROP FOREIGN KEY {q(fk_name)};", destructive=True)

    def _create_fk(self, table_name, fk_name, fk):
        if (table_name, fk_name) in self._fk_created:
            return
        self._fk_created.add((table_name, fk_name))
        self._fk_defs[(table_name, fk_name)] = fk
        self._emit(table_name, fk_name, "FK", "CREATE",
                   f"ALTER TABLE {self._t(table_name)} "
                   f"ADD {self._fk_clause(fk)};")

    # -- unblocking a collation change locked under a foreign key ----
    def _unblock_collation(self):
        """Drop and recreate any FK that would block a collation change.

        MariaDB refuses to alter the charset of a column that takes part
        in a foreign key -- errno 1832, "Cannot change column: used in a
        foreign key constraint", on the referencing side, and errno 1833
        on the referenced one. Both verified on 10.3.32, not assumed.

        The ordinary FK diff only drops keys that DIFFER from the source.
        A key that is identical on both sides is never dropped, and it
        blocks the repair. So every FK touching a recollated column --
        on either side of the reference -- gets a DROP at priority 1 and
        an ADD back at priority 11, with the collation change at 8 in
        between.
        """
        if not self._recollated:
            return

        for tbl in self.tgt.tables.values():
            src_tbl = self.src.tables.get(tbl.name)
            if src_tbl is None:
                # The table itself is being dropped (priority 5). Its
                # keys go with it, and an ADD CONSTRAINT at priority 11
                # would land on a table that no longer exists -- errno
                # 1146.
                continue
            for name, fk in tbl.foreign_keys.items():
                if not self._fk_blocks(fk):
                    continue
                # The DROP is what actually unblocks the column, so ask
                # for it unconditionally. _drop_fk is idempotent, so a
                # key the ordinary diff already dropped is not dropped
                # twice.
                self._drop_fk(fk.table, name)
                src_fk = src_tbl.foreign_keys.get(name)
                if src_fk is None:
                    # A key the source does not have. The ordinary diff
                    # dropped it deliberately; putting it back here
                    # would resurrect it, silently and without error.
                    continue
                # Rebuilt from the SOURCE definition, so the key comes
                # back in its intended shape.
                self._create_fk(fk.table, name, src_fk)

    # -- referential integrity of the keys about to be created -------
    def _validate_foreign_keys(self):
        """Refuse to build a key the DATA cannot support.

        This is the one place the diff looks at rows instead of at
        structure, and it exists because of what failure costs. An
        ADD CONSTRAINT whose child rows have no parent fails with errno
        1452 in the middle of the batch. When the key is one the
        unblocking pass dropped in order to repair a collation, the run
        would then end with that key GONE and no way back -- the tool
        would have left the database less consistent than it found it.

        So every planned key is checked first, against the schema it
        will actually point at (AVACONT_COMUN stays AVACONT_COMUN; a
        self-reference is rewritten to this target). What fails is
        marked with an error and left pending: fetch_pending skips rows
        carrying an error_msg, so nothing half-done reaches the server.
        """
        if self.conn is None:
            return
        for (table, name), fk in sorted(self._fk_defs.items()):
            try:
                problem = self._fk_problem(table, fk)
            except Exception as exc:
                # Not swallowed -- re-raised with the one detail the bare
                # MariaDB error lacks: WHICH key was being checked. A
                # naked "[1054] Unknown column 'c.IdPartener'" in the
                # middle of 22 databases says nothing about where to look.
                raise SchemaSyncError(
                    f"Verificarea cheii `{name}` de pe "
                    f"`{self.db}`.`{table}` a eșuat: {exc}") from exc
            if problem:
                problem["baza"] = self.db
                problem["tabel"] = table
                problem["cheie"] = name
                self.blocks.append(problem)
                self._block_fk_group(table, name, fk, problem["motiv"])

    def _fk_problem(self, table, fk) -> dict:
        """Why this key cannot be created, or None when it can.

        Returns a record, not a sentence: the sentence goes in the log and
        in error_msg, the rest goes into the JSON report, where it can name
        every offending row instead of three examples.
        """
        ref_schema = self._ref_schema(fk)
        external = not same_schema(ref_schema, self.db)

        # 1. Where does the other end stand -- and WHEN?
        #    Inside this target, the referenced column is being brought
        #    to the source's shape by this very batch (priority 8, before
        #    the key is rebuilt at 11), so the comparison has to be
        #    against what it WILL be. An external schema is never
        #    synchronised by this program, so there the live state is the
        #    final state.
        if external:
            ref_col = self._referenced_column(ref_schema, fk.ref_table,
                                              fk.ref_columns[0])
            if ref_col is None:
                return self._block(
                    fk, ref_schema, "structura_lipsa",
                    f"tabelul referit `{ref_schema}`.`{fk.ref_table}` "
                    f"sau coloana `{fk.ref_columns[0]}` nu există. "
                    f"`{ref_schema}` nu este niciodată sincronizată de "
                    f"acest program — trebuie corectată separat")
            ref_charset, ref_collation = ref_col["cs"], ref_col["co"]
        else:
            planned = self._planned_column(fk.ref_table, fk.ref_columns[0])
            if planned is None:
                return self._block(
                    fk, ref_schema, "structura_lipsa",
                    f"tabelul referit `{fk.ref_table}` nu există nici în "
                    f"țintă, nici în sursă")
            ref_charset, ref_collation = planned.charset, planned.collation

        # 2. Would the charsets match? A key between columns with
        #    different character sets is refused with errno 3780.
        mine = self._planned_column(table, fk.columns[0])
        if (mine is not None and mine.charset and ref_charset
                and (mine.charset != ref_charset
                     or mine.collation != ref_collation)):
            extra = ""
            if external:
                extra = (f" `{ref_schema}` nu este sincronizată de acest "
                         f"program, deci diferența nu se poate repara de aici")
            return self._block(
                fk, ref_schema, "charset_diferit",
                f"`{fk.columns[0]}` ajunge {mine.charset} / "
                f"{mine.collation}, dar `{ref_schema}`.`{fk.ref_table}`."
                f"`{fk.ref_columns[0]}` este {ref_charset} / "
                f"{ref_collation} — cheia ar fi refuzată cu eroarea "
                f"3780.{extra}",
                charset_local=f"{mine.charset} / {mine.collation}",
                charset_referit=f"{ref_charset} / {ref_collation}")

        # 3. Do the rows agree? Only askable once every column involved
        #    actually EXISTS. A column that this same batch is still
        #    going to add (priority 7) cannot be counted over -- the
        #    query would come back with errno 1054, and did.
        if not self._countable(table, fk, ref_schema, external):
            return None
        n, sample = self._orphan_rows(table, fk, ref_schema)
        if n:
            cols = ", ".join("`" + c + "`" for c in fk.columns)
            shown = ""
            if sample:
                shown = " (de exemplu " + ", ".join(
                    "«" + v + "»" for v in sample) + ")"
            if n == 1:
                head = (f"1 rând din `{self.db}`.`{table}` are în {cols} o "
                        f"valoare care nu există în `{ref_schema}`.")
            else:
                head = (f"{n} rânduri din `{self.db}`.`{table}` au în {cols} "
                        f"valori care nu există în `{ref_schema}`.")
            record = self._block(
                fk, ref_schema, "date_orfane",
                head + f"`{fk.ref_table}`{shown}. "
                       f"Datele trebuie corectate întâi",
                randuri_afectate=n)
            record.update(self._orphan_detail(table, fk, ref_schema, n))
            return record
        return None

    def _block(self, fk, ref_schema, tip, motiv, **extra) -> dict:
        """One blocking record. `motiv` is what the operator reads."""
        record = {
            "tip": tip,
            "motiv": motiv,
            "coloane": list(fk.columns),
            "refera": {
                "baza": ref_schema,
                "tabel": fk.ref_table,
                "coloane": list(fk.ref_columns),
            },
        }
        record.update(extra)
        return record

    def _orphan_detail(self, table, fk, ref_schema, total) -> dict:
        """Every offending value, with the primary keys that carry it.

        Three examples in a log line tell the operator that something is
        wrong. This tells them WHICH rows -- by primary key, so they can be
        looked at one by one -- and hands back the SELECT that lists them.
        """
        tgt_tbl = self.tgt.tables.get(table)
        pk = tgt_tbl.primary_key if tgt_tbl else None
        pk_cols = list(pk.columns) if pk else []

        picked = ", ".join(f"c.{q(col)}" for col in pk_cols + list(fk.columns))
        on = " AND ".join(f"p.{q(rc)} = c.{q(c)}"
                          for c, rc in zip(fk.columns, fk.ref_columns))
        not_null = " AND ".join(f"c.{q(c)} IS NOT NULL" for c in fk.columns)
        base = (f"FROM {q(self.db)}.{q(table)} c "
                f"LEFT JOIN {q(ref_schema)}.{q(fk.ref_table)} p ON {on} "
                f"WHERE {not_null} AND p.{q(fk.ref_columns[0])} IS NULL")

        rows = query(self.conn,
                     f"SELECT {picked} {base} LIMIT {MAX_DETAIL_ROWS}")

        grouped = {}
        for r in rows:
            value = tuple(r[c] for c in fk.columns)
            entry = grouped.setdefault(value, {"randuri": 0, "chei_primare": []})
            entry["randuri"] += 1
            if pk_cols:
                entry["chei_primare"].append({c: r[c] for c in pk_cols})

        valori = []
        for value, entry in sorted(grouped.items(),
                                   key=lambda kv: -kv[1]["randuri"]):
            valori.append({
                "valoare": dict(zip(fk.columns, value)),
                "randuri_in_esantion": entry["randuri"],
                "chei_primare": entry["chei_primare"],
            })

        return {
            "cheie_primara": pk_cols,
            "valori": valori,
            "esantion_limitat": total > len(rows),
            "randuri_listate": len(rows),
            "sql_inspectare": f"SELECT {picked} {base};",
        }

    def _external_columns(self, schema, table) -> dict:
        """{column: (charset, collation)} for a schema we do not sync."""
        key = (schema.lower(), table.lower())
        if key not in self._ext_columns:
            rows = query(self.conn,
                         "SELECT COLUMN_NAME cn, CHARACTER_SET_NAME cs, "
                         "COLLATION_NAME co FROM information_schema.COLUMNS "
                         "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s",
                         (schema, table))
            self._ext_columns[key] = {r["cn"]: (r["cs"], r["co"])
                                      for r in rows}
        return self._ext_columns[key]

    def _referenced_column(self, schema, table, column) -> dict:
        """charset / collation of a referenced column, or None if absent."""
        found = self._external_columns(schema, table).get(column)
        if found is None:
            return None
        return {"cs": found[0], "co": found[1]}

    def _countable(self, table, fk, ref_schema, external) -> bool:
        """True when both ends already have every column the key needs.

        Answered from the schemas already in memory for this target --
        no extra query -- and from the cached column list for an
        external schema.
        """
        child = self.tgt.tables.get(table)
        if child is None or not set(fk.columns) <= set(child.columns):
            return False
        if external:
            parent = set(self._external_columns(ref_schema, fk.ref_table))
        else:
            ref_tbl = self.tgt.tables.get(fk.ref_table)
            parent = set(ref_tbl.columns) if ref_tbl else set()
        return bool(parent) and set(fk.ref_columns) <= parent

    def _planned_column(self, table, column):
        """The column as it will BE after this run -- the source wins."""
        src_tbl = self.src.tables.get(table)
        if src_tbl is not None and column in src_tbl.columns:
            return src_tbl.columns[column]
        tgt_tbl = self.tgt.tables.get(table)
        return tgt_tbl.columns.get(column) if tgt_tbl else None

    def _orphan_rows(self, table, fk, ref_schema) -> tuple:
        """(count, up to three offending values) for one planned key."""
        on = " AND ".join(f"p.{q(rc)} = c.{q(c)}"
                          for c, rc in zip(fk.columns, fk.ref_columns))
        not_null = " AND ".join(f"c.{q(c)} IS NOT NULL" for c in fk.columns)
        base = (f"FROM {q(self.db)}.{q(table)} c "
                f"LEFT JOIN {q(ref_schema)}.{q(fk.ref_table)} p ON {on} "
                f"WHERE {not_null} AND p.{q(fk.ref_columns[0])} IS NULL")
        n = query(self.conn, f"SELECT COUNT(*) AS n {base}")[0]["n"]
        if not n:
            return 0, []
        cols = ", ".join(f"c.{q(c)}" for c in fk.columns)
        rows = query(self.conn, f"SELECT DISTINCT {cols} {base} LIMIT 3")
        return n, ["/".join(str(v) for v in r.values()) for r in rows]

    def _block_fk_group(self, table, name, fk, reason):
        """Mark the key -- and anything that depended on it -- as blocked.

        When the key already exists in the target it is only being
        dropped so that a charset can change. Cancelling the ADD without
        cancelling the DROP would destroy it, so the whole group stands
        down together: the drop, the charset changes on both sides, and
        the re-add.
        """
        message = f"Cheia `{name}` nu poate fi creată: {reason}."
        self._mark(table, name, "FK", "CREATE", message)

        # Not logged here: schema_generate reports every statement that
        # carries an error_msg, so logging again would print the same
        # sentence once per marked row.
        existing = (table in self.tgt.tables
                    and name in self.tgt.tables[table].foreign_keys)
        if not existing:
            return

        held = (f"Amânat: cheia `{name}` de pe `{table}` nu se poate reface "
                f"({reason}), deci nu se șterge și nu se schimbă setul de "
                f"caractere sub ea — altfel cheia s-ar pierde definitiv.")
        self._mark(table, name, "FK", "DROP", held)
        for col in fk.columns:
            self._mark(table, col, "COLLATION", "MODIFY", held)
            self._mark(table, col, "COLUMN", "MODIFY", held)
        if same_schema(self._ref_schema(fk), self.db):
            for col in fk.ref_columns:
                self._mark(fk.ref_table, col, "COLLATION", "MODIFY", held)
                self._mark(fk.ref_table, col, "COLUMN", "MODIFY", held)

    def _mark(self, table, obj_name, obj_type, action, message):
        """Attach an error to a statement, so it is recorded but skipped."""
        for st in self.statements:
            if (st.table_name == table and st.object_name == obj_name
                    and st.object_type == obj_type
                    and st.action_type == action and not st.error_msg):
                st.error_msg = message

    def _fk_blocks(self, fk) -> bool:
        """True when this FK touches a column whose charset is changing.

        Both directions matter: the referencing column and the
        referenced one are equally locked.
        """
        for col in fk.columns:
            if (fk.table, col) in self._recollated:
                return True
        # A key pointing INTO a recollated column, within this database.
        if (same_schema(fk.ref_schema, self.db)
                or same_schema(fk.ref_schema, SOURCE_DB)):
            for col in fk.ref_columns:
                if (fk.ref_table, col) in self._recollated:
                    return True
        return False


# ---------------------------------------------------------------------
# Rename cleanup and the top-level driver
# ---------------------------------------------------------------------

def rename_cleanup(source, mode) -> list:
    """Strip rename: markers from AVACONT_SURSA once every target is done.

    Emitted once, not once per target, and run last (priority 99).
    """
    out = []
    for tbl in sorted(source.tables.values(), key=lambda t: t.name):
        for col in tbl.columns_in_order():
            if not col.rename_from:
                continue
            definition = column_definition(
                col, comment=col.comment_after_rename)
            # An empty comment must be written explicitly to clear it.
            if not col.comment_after_rename:
                definition += " COMMENT ''"
            out.append(Statement(
                target_db=SOURCE_DB, table_name=tbl.name,
                object_name=col.name, object_type="RENAME_CLEANUP",
                action_type="MODIFY",
                ddl_sql=(f"ALTER TABLE {q(SOURCE_DB)}.{q(tbl.name)} "
                         f"MODIFY COLUMN {definition};")))
    return out


def build_diff(conn, targets, mode, logger, blocks=None) -> list:
    """Compare every target against the source. Returns Statements in
    execution order.

    `blocks`, when given, collects one record per key that cannot be built
    -- the material for the JSON report of what has to be fixed by hand.
    """
    from .schema_common import server_version

    is_mariadb, version = server_version(conn)
    logger.info("Server: %s (defaults ca %s).", version,
                "expresii" if is_mariadb else "valori brute")

    source = read_schema(conn, SOURCE_DB, expr_defaults=is_mariadb)
    logger.info("Sursa %s: %d tabele.", SOURCE_DB, len(source.tables))

    statements = []
    for db in targets:
        if db in SYSTEM_SCHEMAS:
            raise ValueError(f"Schema de sistem ca țintă: {db}")
        target = read_schema(conn, db, expr_defaults=is_mariadb)
        if not target.exists:
            logger.warning("Baza `%s` nu există — ignorată.", db)
            continue
        diff = SchemaDiff(source, target, db, mode, logger, conn)
        produced = diff.run()
        if produced:
            logger.info("  %s: %d instrucțiuni.", db, len(produced))
        else:
            logger.info("  %s: nicio diferență față de sursă.", db)
        statements.extend(produced)
        if blocks is not None:
            blocks.extend(diff.blocks)

    statements.extend(rename_cleanup(source, mode))
    statements.sort(key=lambda s: s.priority)
    return statements
