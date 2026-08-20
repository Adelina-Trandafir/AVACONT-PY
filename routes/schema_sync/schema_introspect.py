"""
schema_introspect.py -- read a schema out of information_schema.

Everything the diff needs, in four queries per schema, turned into plain
dataclasses. Nothing here writes.

The one genuinely awkward part is COLUMN_DEFAULT, which does not mean
the same thing across servers:

  MariaDB 10.2.7+   defaults are stored as EXPRESSIONS. A string default
                    comes back already quoted ('abc'); a function comes
                    back as current_timestamp(); no default is NULL; an
                    explicit DEFAULT NULL comes back as the four-character
                    string "NULL".
  Older / MySQL     defaults come back as RAW values. A string default
                    comes back unquoted (abc) and must be quoted before
                    it can be put into DDL.

Getting this wrong produces DDL that either loses the default or doubles
its quotes, so the mode is decided once from VERSION() and carried on
every Column.
"""

from dataclasses import dataclass, field
from .schema_common import EXCLUDED_TABLES, query

@dataclass
class Column:
    table: str
    name: str
    ordinal: int
    column_type: str
    data_type: str
    is_nullable: bool
    default: str            # raw COLUMN_DEFAULT
    extra: str
    comment: str
    charset: str            # None for non-character columns
    collation: str
    generation_expr: str    # non-empty => generated column
    expr_defaults: bool     # True when the server stores defaults as expressions

    @property
    def is_auto_increment(self) -> bool:
        return "auto_increment" in (self.extra or "").lower()

    @property
    def is_generated(self) -> bool:
        return bool(self.generation_expr)

    @property
    def rename_from(self) -> str:
        """Old column name from a 'rename:Old' or 'rename:Old|note' comment."""
        c = self.comment or ""
        if not c.startswith("rename:"):
            return None
        return c[len("rename:"):].split("|", 1)[0]

    @property
    def comment_after_rename(self) -> str:
        """What the comment should become once the rename is applied."""
        c = self.comment or ""
        if not c.startswith("rename:"):
            return c
        parts = c[len("rename:"):].split("|", 1)
        return parts[1] if len(parts) > 1 else ""

    @property
    def effective_comment(self) -> str:
        """The comment as it should end up on the column.

        A rename: marker is an INSTRUCTION to this tool, not a comment
        anyone wants stored. Every comparison and every emitted
        definition uses this, so the marker never leaks into a target
        and never makes a column look different from itself.
        """
        return self.comment_after_rename


@dataclass
class Index:
    table: str
    name: str
    unique: bool
    columns: list = field(default_factory=list)

    @property
    def is_primary(self) -> bool:
        return self.name == "PRIMARY"


@dataclass
class ForeignKey:
    table: str
    name: str
    columns: list
    ref_schema: str
    ref_table: str
    ref_columns: list
    delete_rule: str
    update_rule: str

    def signature(self) -> tuple:
        """What "the same FK" means. ref_schema deliberately excluded:
        a self-schema reference is rewritten per target, so comparing it
        raw would flag every FK as changed."""
        return (tuple(self.columns), self.ref_table, tuple(self.ref_columns),
                self.delete_rule, self.update_rule)


@dataclass
class Table:
    name: str
    engine: str
    charset: str
    collation: str
    row_format: str
    columns: dict = field(default_factory=dict)     # name -> Column
    indexes: dict = field(default_factory=dict)     # name -> Index
    foreign_keys: dict = field(default_factory=dict)  # name -> ForeignKey

    @property
    def primary_key(self) -> Index:
        return self.indexes.get("PRIMARY")

    def columns_in_order(self) -> list:
        return sorted(self.columns.values(), key=lambda c: c.ordinal)


@dataclass
class Schema:
    name: str
    tables: dict = field(default_factory=dict)      # name -> Table
    exists: bool = True


def read_schema(conn, db_name: str, expr_defaults: bool) -> Schema:
    """Load one schema. Base tables only -- views are out of scope."""
    schema = Schema(name=db_name)

    rows = [r for r in query(
        conn,
        "SELECT TABLE_NAME, ENGINE, TABLE_COLLATION, ROW_FORMAT "
        "FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA = %s AND TABLE_TYPE = 'BASE TABLE'",
        (db_name,))
        if r["TABLE_NAME"] not in EXCLUDED_TABLES]
    if not rows:
        exists = query(conn,
                       "SELECT COUNT(*) n FROM information_schema.SCHEMATA "
                       "WHERE SCHEMA_NAME = %s", (db_name,))[0]["n"] > 0
        schema.exists = exists
        return schema

    for r in rows:
        coll = r["TABLE_COLLATION"] or ""
        schema.tables[r["TABLE_NAME"]] = Table(
            name=r["TABLE_NAME"],
            engine=r["ENGINE"],
            charset=coll.split("_")[0] if coll else None,
            collation=coll or None,
            row_format=r["ROW_FORMAT"],
        )

    for r in query(conn,
                   "SELECT TABLE_NAME, COLUMN_NAME, ORDINAL_POSITION, "
                   "COLUMN_TYPE, DATA_TYPE, IS_NULLABLE, COLUMN_DEFAULT, "
                   "EXTRA, COLUMN_COMMENT, CHARACTER_SET_NAME, "
                   "COLLATION_NAME, GENERATION_EXPRESSION "
                   "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = %s",
                   (db_name,)):
        t = schema.tables.get(r["TABLE_NAME"])
        if t is None:
            continue                       # a view; skipped
        t.columns[r["COLUMN_NAME"]] = Column(
            table=r["TABLE_NAME"],
            name=r["COLUMN_NAME"],
            ordinal=r["ORDINAL_POSITION"],
            column_type=r["COLUMN_TYPE"],
            data_type=r["DATA_TYPE"],
            is_nullable=(r["IS_NULLABLE"] == "YES"),
            default=r["COLUMN_DEFAULT"],
            extra=r["EXTRA"] or "",
            comment=r["COLUMN_COMMENT"] or "",
            charset=r["CHARACTER_SET_NAME"],
            collation=r["COLLATION_NAME"],
            generation_expr=r["GENERATION_EXPRESSION"] or "",
            expr_defaults=expr_defaults,
        )

    for r in query(conn,
                   "SELECT TABLE_NAME, INDEX_NAME, NON_UNIQUE, COLUMN_NAME, "
                   "SEQ_IN_INDEX FROM information_schema.STATISTICS "
                   "WHERE TABLE_SCHEMA = %s ORDER BY TABLE_NAME, INDEX_NAME, "
                   "SEQ_IN_INDEX", (db_name,)):
        t = schema.tables.get(r["TABLE_NAME"])
        if t is None:
            continue
        idx = t.indexes.get(r["INDEX_NAME"])
        if idx is None:
            idx = Index(table=r["TABLE_NAME"], name=r["INDEX_NAME"],
                        unique=(r["NON_UNIQUE"] == 0))
            t.indexes[r["INDEX_NAME"]] = idx
        idx.columns.append(r["COLUMN_NAME"])

    fk_rows = query(conn,
                    "SELECT k.TABLE_NAME, k.CONSTRAINT_NAME, k.COLUMN_NAME, "
                    "k.ORDINAL_POSITION, k.REFERENCED_TABLE_SCHEMA, "
                    "k.REFERENCED_TABLE_NAME, k.REFERENCED_COLUMN_NAME, "
                    "r.DELETE_RULE, r.UPDATE_RULE "
                    "FROM information_schema.KEY_COLUMN_USAGE k "
                    "JOIN information_schema.REFERENTIAL_CONSTRAINTS r "
                    "  ON r.CONSTRAINT_SCHEMA = k.CONSTRAINT_SCHEMA "
                    " AND r.CONSTRAINT_NAME   = k.CONSTRAINT_NAME "
                    "WHERE k.TABLE_SCHEMA = %s "
                    "AND k.REFERENCED_TABLE_NAME IS NOT NULL "
                    "ORDER BY k.TABLE_NAME, k.CONSTRAINT_NAME, "
                    "k.ORDINAL_POSITION", (db_name,))
    for r in fk_rows:
        t = schema.tables.get(r["TABLE_NAME"])
        if t is None:
            continue
        fk = t.foreign_keys.get(r["CONSTRAINT_NAME"])
        if fk is None:
            fk = ForeignKey(
                table=r["TABLE_NAME"], name=r["CONSTRAINT_NAME"], columns=[],
                ref_schema=r["REFERENCED_TABLE_SCHEMA"],
                ref_table=r["REFERENCED_TABLE_NAME"], ref_columns=[],
                delete_rule=r["DELETE_RULE"], update_rule=r["UPDATE_RULE"])
            t.foreign_keys[r["CONSTRAINT_NAME"]] = fk
        fk.columns.append(r["COLUMN_NAME"])
        fk.ref_columns.append(r["REFERENCED_COLUMN_NAME"])

    return schema
