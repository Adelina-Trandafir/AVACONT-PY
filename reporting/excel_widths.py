"""Shared Excel column width contract.

Applications supply a report identifier and stable column keys. Only this
module reads RAPOARTE_EXCEL_COLOANE and applies the established width policy.
It never creates tables or writes settings, and does not import any app.
"""
import re
import unicodedata


def column_key(column, index):
    if column.get('key'):
        return column['key']
    text = unicodedata.normalize('NFKD', column['label'].replace('-\n', ''))
    text = ''.join(char for char in text if not unicodedata.combining(char))
    return re.sub(r'[^A-Z0-9]+', '_', text.upper()).strip('_') or ('NR_CRT' if index == 0 else f'COLOANA_{index + 1}')


def configured_widths(repo, model):
    """Read on each export; a missing settings table keeps the existing fallback."""
    if repo.sqlite or not model.get('report_name'):
        return {}
    from mysql.connector.errors import ProgrammingError
    try:
        rows = repo.query('SELECT CHEIE_COLOANA, LATIME FROM AVACONT_COMUN.RAPOARTE_EXCEL_COLOANE WHERE NUME_RAPORT=%s',
                          (model['report_name'],))
    except ProgrammingError as error:
        if error.errno == 1146:
            return {}
        raise
    return {row['CHEIE_COLOANA']: float(row['LATIME']) for row in rows if 0 < float(row['LATIME']) <= 255}


class CursorRepository:
    """Adapt a MariaDB dictionary cursor to the read-only repository interface."""
    sqlite = False

    def __init__(self, cursor):
        self.cursor = cursor

    def query(self, sql, params):
        self.cursor.execute(sql, params)
        return self.cursor.fetchall()


class ColumnWidths:
    """Merge repeated tables by output index, keeping the original fallback.

    Configuration wins. When distinct tables occupy the same Excel column,
    the widest configured width wins. Unconfigured widths use the established
    layout fallback (14..55) and final minimum of 20 Excel character units.
    """
    def __init__(self, configured=None):
        self.configured = configured or {}
        self.dimensions = {}

    def observe(self, index, key, layout_width, current_width):
        configured = self.configured.get(key)
        if configured is not None:
            self.dimensions[index] = max(self.dimensions.get(index, 0), configured)
        return max(current_width, min(55, max(14, layout_width / 100)))

    def final(self, index, current_width):
        return self.dimensions.get(index, max(current_width, 20))
