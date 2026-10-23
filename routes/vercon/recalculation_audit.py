"""Explain changed computed balances without rewriting later document lines.

Capture before and after in the same locked transaction as the original edit.
No audit entry survives when validation or any subsequent write fails.
"""
from datetime import date
from decimal import Decimal, localcontext

from . import food_balance, protocol, report_data, stock, stock_source


def cause(family, doc_id, number, day, version):
    label = 'bonului de consum' if family == 'bc' else 'listei de alimente'
    day = str(day)[:10]
    shown_day = '.'.join(reversed(day.split('-')))
    return {'family': family, 'id': doc_id, 'number': str(number), 'date': day, 'version': version,
            'reason': 'Recalculare automată după modificarea %s %s din %s.' % (label, number, shown_day)}


def food_snapshot(cursor, ctx):
    if ctx['type'] != 'ALIMENTE':
        return {}
    cursor.execute('SELECT IdLista,NumarLA,DATE(DATALA) AS day,DEP,ValoareLista,ValoareBon FROM VC_LABC '
                   'WHERE SubunitId=%s AND GestiuneId=%s AND ExercitiuId=%s AND Anulat=0 '
                   'ORDER BY DATALA,IdLista', (ctx['subunit'], ctx['gestiune'], ctx['exercitiu']))
    rows = cursor.fetchall()
    source = report_data.Source(cursor, ctx['unit'], ctx, {
        'start': date(ctx['an'], 1, 1), 'end': date(ctx['an'], 12, 31),
    })
    result = {}
    with localcontext() as precision:
        precision.prec = 60
        for row in rows:
            day = stock.day_of(row['day'])
            opening = food_balance.before(source, day)
            current = -Decimal(row['DEP']) if row['DEP'] is not None else None
            result[row['IdLista']] = {
                'number': str(row['NumarLA']), 'date': day.isoformat(),
                'prior_balance': opening, 'daily_balance': current,
                'closing_balance': opening + current if current is not None else None,
                'list_value': row['ValoareLista'], 'bons_total': row['ValoareBon'],
            }
    return result


def stock_snapshot(cursor, ctx, variants):
    movements = stock_source.warehouse(cursor, ctx, variants)
    metadata = stock_source.variants(cursor, ctx)
    days, documents = {}, {}
    with localcontext() as precision:
        precision.prec = 60
        for row in movements:
            day, variant = stock.day_of(row['d']), row['p']
            bucket = days.setdefault(day, {})
            bucket[variant] = bucket.get(variant, Decimal(0)) + row['q']
            if row['kind'] == 'bc':
                document = documents.setdefault(row['document_id'], {
                    'number': str(row['number']), 'date': day.isoformat(), 'variants': set(),
                })
                document['variants'].add(variant)
        by_day, running = {}, {}
        for day, quantities in sorted(days.items()):
            for variant, quantity in quantities.items():
                running[variant] = running.get(variant, Decimal(0)) + quantity
            by_day[day.isoformat()] = dict(running)
    return {doc_id: {'number': doc['number'], 'date': doc['date'], 'day_closing_stock': [
        {'variant': variant, 'name': metadata.get(variant, {}).get('name') or str(variant),
         'quantity': by_day[doc['date']].get(variant, Decimal(0))}
        for variant in sorted(doc['variants'])]}
        for doc_id, doc in documents.items()}


def record(cursor, ctx, before, after, origin, operation, table, request_id):
    changed = []
    for doc_id, values in after.items():
        if doc_id not in before or before[doc_id] == values:
            continue
        if (origin['family'] == 'bc' and table == 'VC_Iesiri_H'
                or origin['family'] == 'lista' and table == 'VC_LABC') and doc_id == origin['id']:
            continue
        protocol.audit(cursor, ctx, operation, table, doc_id, before[doc_id],
                       {**values, 'cause': origin, 'reason': origin['reason']}, None, request_id)
        changed.append(doc_id)
    return changed
