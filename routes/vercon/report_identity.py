"""User-facing report identity; database names are never issuer names."""
from copy import deepcopy
import re
from .nomenclatoare import NomError

VERSION = 2


def read(cursor, unit, ctx, *, issuer=None):
    # Explicit fixture identity is accepted by the local preview only. Production
    # reads the same unit records used by ADECHIT, in the current read session.
    if issuer is None:
        if cursor is None:
            raise NomError('REPORT_IDENTITY', 'Datele unității pentru raport nu sunt disponibile.', 409)
        cursor.execute(
            'SELECT u.NumeUnitate AS name,u.CF AS fiscal_code,d.Adresa,d.Orasul,d.Judetul,'
            's.Name AS subunit_name FROM AVACONT_COMUN.Unitati u '
            'LEFT JOIN AVACONT_COMUN.Unitati_Date d ON d.DC=u.DC '
            'JOIN VC_Subunitati s ON s.SubunitId=%s AND s.Active=1 WHERE u.DC=%s',
            (ctx['subunit'], unit))
        rows = cursor.fetchall()
        if len(rows) != 1:
            raise NomError('REPORT_IDENTITY', 'Datele unității sau ale subunității active nu sunt disponibile.', 409)
        row = rows[0]
        issuer = {**row, 'address': ', '.join(str(row[key]) for key in ('Adresa', 'Orasul', 'Judetul') if row.get(key))}
    result = {key: str(issuer.get(key) or '').strip()
              for key in ('name', 'fiscal_code', 'address', 'subunit_name')}
    if not result['name'] or not result['subunit_name'] or result['name'].casefold() == str(unit).casefold():
        raise NomError('REPORT_IDENTITY', 'Completați denumirea unității și a subunității active pentru raport.', 409)
    return result


def display_definition(value, unit, name):
    """Old saved definitions may contain the former database-name fallback.

    Adapt presentation text in memory; do not rewrite owner configuration or
    replace values in business data, numeric cells, column keys or identifiers.
    """
    database = re.compile(r'(?<!\w)(?:DC\s*[:=]?\s*)?' + re.escape(str(unit)) + r'(?!\w)', re.IGNORECASE)
    display_keys = {'text', 'title', 'label', 'name', 'header', 'footer', 'manager'}

    def visit(item):
        if isinstance(item, list):
            return [visit(child) for child in item]
        if isinstance(item, dict):
            return {key: clean(child) if key in display_keys and isinstance(child, str) else visit(child)
                    for key, child in item.items()}
        return item

    def clean(text):
        text = re.sub(r'\{(?:DC|DB_NAME|DATABASE)\}', '', text, flags=re.IGNORECASE)
        text = re.sub(r'\bDC\s*[:=]\s*', '', text, flags=re.IGNORECASE)
        return database.sub(lambda _match: name, text)

    return visit(value)


def configured(stored, issuer, unit):
    identity = display_definition(deepcopy(stored), unit, issuer['name'])
    identity.update(issuer)
    identity['header'] = issuer['name']
    left = [issuer['name']]
    if issuer['fiscal_code']:
        left.append('Cod fiscal: ' + issuer['fiscal_code'])
    if issuer['address']:
        left.append(issuer['address'])
    identity['letterhead'] = {'left': left, 'right': issuer['subunit_name']}
    # Mandatory common header, independent of legacy/custom layout header bands.
    identity['elements'] = [
        {'id': 'report-issuer', 'band': 'header', 'text': '\n'.join(left),
         'x': 0, 'y': 0, 'w': .6, 'h': 13, 'size': 9, 'full_width': True},
        {'id': 'report-subunit', 'band': 'header', 'text': identity['letterhead']['right'],
         'x': .6, 'y': 0, 'w': .4, 'h': 13, 'size': 11, 'full_width': True, 'align': 'r'},
        *[element for element in identity.get('elements', []) if element['band'] == 'footer'],
    ]
    return identity
