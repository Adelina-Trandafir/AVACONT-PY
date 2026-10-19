"""Read-only date bounds of the documents in the current report scope."""
from .context import HISTORY_TYPES
from .oi import SPEC


def catalog_period(cursor, ctx):
    sources = [('VC_NIR', 'Data'), ('VC_NIR', 'DataFc'), ('VC_Iesiri_H', 'DataBC')]
    if ctx['type'] in HISTORY_TYPES:
        sources.extend((spec['table'], spec['date']) for spec in SPEC.values())
    if ctx['type'] == 'ALIMENTE':
        sources.append(('VC_LABC', 'DATALA'))
    # Table/column names come only from the fixed document definitions above.
    statements = [
        f'SELECT DATE({column}) AS d FROM {table} '
        f'WHERE SubunitId=%s AND GestiuneId=%s AND ExercitiuId=%s AND {column} IS NOT NULL'
        for table, column in sources
    ]
    cursor.execute('SELECT YEAR(d) AS year, MIN(d) AS min_date, MAX(d) AS max_date FROM ('
                   + ' UNION ALL '.join(statements)
                   + ') history WHERE d IS NOT NULL GROUP BY YEAR(d) ORDER BY year',
                   (ctx['subunit'], ctx['gestiune'], ctx['exercitiu']) * len(sources))
    rows = cursor.fetchall()
    if ctx['type'] not in HISTORY_TYPES:
        rows = [row for row in rows if row['year'] == ctx['an']]
    periods = [{'year': int(row['year']), 'min': row['min_date'].isoformat(), 'max': row['max_date'].isoformat()}
               for row in rows if row['year'] and row['min_date'] and row['max_date']]
    if not periods:
        year = ctx['year'] if ctx['type'] in HISTORY_TYPES else ctx['an']
        periods = [{'year': year, 'min': f'{year}-01-01', 'max': f'{year}-12-31'}]
    return {'years': [item['year'] for item in periods], 'periods': periods,
            'min_date': periods[0]['min'], 'max_date': periods[-1]['max'],
            'latest_year': periods[-1]['year']}
