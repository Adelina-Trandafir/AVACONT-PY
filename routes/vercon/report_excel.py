"""VERCON identifiers for the application-independent Excel width contract."""
import re
from reporting.excel_widths import configured_widths, column_key, CursorRepository


def report_name(name):
    return 'VERCON_' + re.sub(r'[^A-Z0-9_]', '_', name.upper())


def key(column, index, columns):
    # Explicit, stable field keys, independent of the output Excel letter.
    # Repeated quantity fields (required/received) have separate width keys.
    field = column['key'].upper()
    occurrence = sum(c['key'] == column['key'] for c in columns[:index + 1])
    field += '_' + str(occurrence) if occurrence > 1 else ''
    return column_key({**column, 'key': field}, index)


def read_widths(cursor, model):
    return configured_widths(CursorRepository(cursor), {'report_name': report_name(model['report_name'])})
