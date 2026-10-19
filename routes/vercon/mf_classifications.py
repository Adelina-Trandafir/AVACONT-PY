"""Read-only MF classification catalogue from AVACONT_COMUN; never creates tables."""
import re
from .nomenclatoare import NomError


def _rows(cursor):
    cursor.execute('SELECT Cod, Denumire, DNF_MIN, DNF_MAX FROM AVACONT_COMUN.VC_ClasificariMF '
                   'WHERE Cod IS NOT NULL ORDER BY Cod')
    return [{'code': str(row['Cod']), 'name': row['Denumire'] or '',
             'dnf_min': row['DNF_MIN'], 'dnf_max': row['DNF_MAX']} for row in cursor.fetchall()]


def read(cursor, parent=None, name='', code=''):
    name, code = name.strip(), code.strip()
    if max(len(name), len(code)) > 160:
        raise NomError('INVALID', 'Căutarea poate avea cel mult 160 de caractere pe câmp.', 422)
    rows = _rows(cursor)
    normalized = {row['code'].strip().rstrip('.'): row['code'] for row in rows}
    parents = {}
    for row in rows:
        parts = row['code'].strip().rstrip('.').split('.')
        ancestors = ('.'.join(parts[:level]) for level in range(len(parts) - 1, 0, -1))
        parents[row['code']] = next((normalized[key] for key in ancestors if key in normalized), None)
    branches = set(parents.values())
    if name or code:
        matched = [row for row in rows if name.casefold() in row['name'].casefold() and code.casefold() in row['code'].casefold()]
        return {'items': [{**row, 'has_children': False} for row in matched[:200]],
                'mode': 'search', 'total': len(matched), 'remaining': max(0, len(matched) - 200)}
    return {'items': [{**row, 'has_children': row['code'] in branches}
                      for row in rows if parents[row['code']] == (parent or None)], 'mode': 'tree'}


def detail(cursor, code):
    if not isinstance(code, str) or len(code) > 255 or re.search(r'[\x00-\x1f]', code):
        raise NomError('INVALID', 'Cod de clasificare invalid.', 422)
    cursor.execute('SELECT Cod, Denumire, DNF_MIN, DNF_MAX FROM AVACONT_COMUN.VC_ClasificariMF WHERE Cod=%s LIMIT 1', (code,))
    row = cursor.fetchone()
    if not row:
        raise NomError('INVALID', 'Alegeți o clasificare MF existentă în catalogul comun.', 422,
                       {'CC': 'Cod inexistent în VC_ClasificariMF.'})
    return {'code': str(row['Cod']), 'name': row['Denumire'] or '', 'dnf_min': row['DNF_MIN'], 'dnf_max': row['DNF_MAX']}


def validate(cursor, code):
    detail(cursor, code)
