# routes/forexe/print_count_column.py
"""
Slice 0099 -- the total print count of a DDF revision / an ordonantare, for the LIST routes
(GET /api/forexe/ddf, GET /api/forexe/ord): the print list shown on a month / «Toate» node.

Total prints of a document = the count on its PDF row + the count on its document row
(sql/0099_print_count.sql). A database the DDL has not reached must still list its documents,
so the column is probed (once per database and family, only a True answer is remembered, like
ddf_stare.are_stare_trimitere) and the SQL fragment is the literal 0 when the columns are not there.

No imports from the sibling route modules: ddf.py and ord.py both import this file.
"""

COLUMN = "PrintCount"

_SQL_COUNT = (
    "SELECT COUNT(*) FROM information_schema.COLUMNS "
    " WHERE TABLE_SCHEMA = %s AND COLUMN_NAME = %s AND TABLE_NAME IN (%s, %s)"
)

_PRESENT = {}


def _has_columns(cursor, db_name, family, pdf_table, doc_table):
    key = (db_name, family)
    if _PRESENT.get(key):
        return True
    cursor.execute(_SQL_COUNT, (db_name, COLUMN, pdf_table, doc_table))
    row = cursor.fetchone()
    value = next(iter(row.values())) if isinstance(row, dict) else (row[0] if row else 0)
    present = int(value or 0) == 2
    if present:
        _PRESENT[key] = True
    return present


def ddf_print_sql(cursor, db_name):
    """SQL expression (aliases `p` = FX_DDF_PDF, `r` = FX_DDF_REV) for the total prints of a revision."""
    if _has_columns(cursor, db_name, "ddf", "FX_DDF_PDF", "FX_DDF_REV"):
        return "(COALESCE(p.PrintCount, 0) + COALESCE(r.PrintCount, 0))"
    return "0"


def ord_print_sql(cursor, db_name):
    """SQL expression (aliases `p` = FX_ORD_PDF, `o` = FX_ORD) for the total prints of an ordonantare."""
    if _has_columns(cursor, db_name, "ord", "FX_ORD_PDF", "FX_ORD"):
        return "(COALESCE(p.PrintCount, 0) + COALESCE(o.PrintCount, 0))"
    return "0"
