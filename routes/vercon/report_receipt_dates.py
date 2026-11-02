"""NIR dates govern report stock/periods; invoice dates remain invoice metadata."""
import re

from .nomenclatoare import NomError

VERSION = 1
_STRINGS = re.compile(r"'(?:''|\\.|[^'\\])*'")


def query(sql, key):
    """Adapt legacy saved sources in memory, without overwriting owner files."""
    if key not in ('ledger', 'receipts'):
        return sql
    plain = _STRINGS.sub("''", sql)
    aliases = set(re.findall(r'\{\{TABLE:VC_NIR\}\}\s+(?:AS\s+)?([A-Za-z_][A-Za-z0-9_]*)', plain, re.I))
    chunks = re.split('(' + _STRINGS.pattern + ')', sql)
    for index in range(0, len(chunks), 2):
        for alias in aliases:
            field = r'\b' + re.escape(alias) + r'\.'
            if key == 'ledger':
                # Ledger projections and predicates represent stock, never invoice metadata.
                chunks[index] = re.sub(field + r'DataFc\b', alias + '.Data', chunks[index], flags=re.I)
            else:
                chunks[index] = re.sub(
                    r'\b(?:COALESCE|IFNULL)\s*\(\s*' + field + r'DataFc\s*,\s*' + field + r'Data\s*\)',
                    alias + '.Data', chunks[index], flags=re.I)
                chunks[index] = re.sub(
                    r'\bDATE\s*\(\s*' + field + r'DataFc\s*\)(?=\s+(?:AS\s+)?movement_date\b)',
                    'DATE(' + alias + '.Data)', chunks[index], flags=re.I)
                chunks[index] = re.sub(
                    r'(?<![\w.(])' + field + r'DataFc\b(?=\s+(?:AS\s+)?movement_date\b)',
                    alias + '.Data', chunks[index], flags=re.I)
    return ''.join(chunks)


def rows(cursor, ctx, items):
    """Bind receipt dates to the actual scoped header, including legacy sources.

    Older sources used movement_date both for stock and for the printed invoice
    date. Keep those responsibilities separate even if invoice_date is absent.
    """
    documents = sorted({item['document'] for item in items})
    headers = {}
    for offset in range(0, len(documents), 1000):
        batch = documents[offset:offset + 1000]
        cursor.execute(
            'SELECT IdIntrare AS document,DATE(Data) AS date,DATE(DataFc) AS invoice_date '
            'FROM VC_NIR WHERE SubunitId=%s AND GestiuneId=%s AND ExercitiuId=%s '
            'AND IdIntrare IN (' + ','.join(['%s'] * len(batch)) + ')',
            (ctx['subunit'], ctx['gestiune'], ctx['exercitiu'], *batch))
        headers.update({row['document']: row for row in cursor.fetchall()})
    result = []
    for item in items:
        header = headers.get(item['document'])
        if header is None or header['date'] is None:
            raise NomError('REPORT_SOURCE', 'Recepția %s nu are o dată NIR disponibilă în contextul raportului.' % item['document'], 409)
        result.append({**item, 'date': header['date'], 'movement_date': header['date'],
                       'invoice_date': header['invoice_date']})
    return result
