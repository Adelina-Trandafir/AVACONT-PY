"""Read-only date bounds of the documents in the current report scope."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from .context import HISTORY_TYPES
from .oi import SPEC
from .stock_source import WAREHOUSE


def movement_statements(include_custody):
    """Use actual movement lines and their effective dates, including returns/storno."""
    statements = []
    for _, lines, alias, header, head, key, day, _, _, valid, _ in WAREHOUSE:
        statements.append(
            f'SELECT {day} AS d FROM {lines} {alias} JOIN {header} {head} '
            f'ON {head}.SubunitId={alias}.SubunitId AND {head}.GestiuneId={alias}.GestiuneId '
            f'AND {head}.ExercitiuId={alias}.ExercitiuId AND {head}.{key}={alias}.{key} '
            f'WHERE {alias}.SubunitId=%s AND {alias}.GestiuneId=%s AND {alias}.ExercitiuId=%s AND {valid}')
    if include_custody:
        for spec in SPEC.values():
            statements.append(
                f"SELECT DATE({spec['date']}) AS d FROM {spec['table']} "
                'WHERE SubunitId=%s AND GestiuneId=%s AND ExercitiuId=%s AND Anulat=0')
    return statements


def catalog_period(cursor, ctx):
    history = ctx['type'] in HISTORY_TYPES
    # A food-list date or an empty/cancelled header is not a stock movement.
    statements = movement_statements(include_custody=history)
    cursor.execute('SELECT YEAR(d) AS year, MIN(d) AS min_date, MAX(d) AS max_date FROM ('
                   + ' UNION ALL '.join(statements)
                   + ') history WHERE d IS NOT NULL GROUP BY YEAR(d) ORDER BY year',
                   (ctx['subunit'], ctx['gestiune'], ctx['exercitiu']) * len(statements))
    rows = cursor.fetchall()
    if not history:
        rows = [row for row in rows if row['year'] == ctx['an']]
    periods = [{'year': int(row['year']), 'min': row['min_date'].isoformat(), 'max': row['max_date'].isoformat(),
                'last_movement_month': row['max_date'].month}
               for row in rows if row['year'] and row['min_date'] and row['max_date']]
    defaults = {}
    if history:
        try:
            today = datetime.now(ZoneInfo('Europe/Bucharest')).date()
        except ZoneInfoNotFoundError:
            today = datetime.now(timezone.utc).date()
        start, finish = f'{today.year}-01-01', today.isoformat()
        first_date = min((item['min'] for item in periods), default=start)
        defaults = {'default_start': start, 'default_end': finish, 'current_year': today.year,
                    'latest_data_year': max((item['year'] for item in periods), default=None),
                    'all_years': {'start': min(first_date, finish), 'end': finish}}
        if not any(item['year'] == today.year for item in periods):
            periods.append({'year': today.year, 'min': start, 'max': finish, 'last_movement_month': 0})
            periods.sort(key=lambda item: item['year'])
    if not periods:
        year = ctx['an']
        periods = [{'year': year, 'min': f'{year}-01-01', 'max': f'{year}-12-31', 'last_movement_month': 0}]
    return {'years': [item['year'] for item in periods], 'periods': periods,
            'min_date': periods[0]['min'], 'max_date': periods[-1]['max'],
            'latest_year': periods[-1]['year'], **defaults}
