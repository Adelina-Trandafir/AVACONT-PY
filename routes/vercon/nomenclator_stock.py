"""Read-only warehouse cards for every saved price variant of a product."""
from datetime import date
from decimal import Decimal

from . import protocol, stock, stock_source
from .nomenclatoare import NomError


def product_statement(cursor, vctx, product):
    sel, gest = vctx['selection'], vctx['gestiune']
    subunit, gestiune, year = sel['subunit'], sel['gestiune'], sel['year']
    if not isinstance(year, int):
        raise NomError('NO_EXERCISE', 'Alegeți anul fișei de magazie.', 409)
    cursor.execute('SELECT ID, DenumireP, UM FROM VC_Produse '
                   'WHERE SubunitId=%s AND GestiuneId=%s AND ID=%s', (subunit, gestiune, product))
    item = cursor.fetchone()
    if not item:
        raise NomError('NOT_FOUND', 'Produsul nu mai există în această gestiune.', 404)
    exercise = protocol.exercise_of(cursor, subunit, gestiune, gest['type'], year)
    ctx = {'subunit': subunit, 'gestiune': gestiune, 'exercitiu': exercise['id'], 'type': gest['type'],
           'scale': int(gest.get('scara', 4)), 'zec': int(gest.get('zec', 4))}
    cursor.execute('SELECT IdProdus, PUTVA, COD, Activ FROM VC_ProduseP '
                   'WHERE SubunitId=%s AND GestiuneId=%s AND ID=%s ORDER BY IdProdus DESC',
                   (subunit, gestiune, product))
    variants = cursor.fetchall()
    cursor.execute('SELECT i.IdProdus, MIN(DATE(COALESCE(n.DataFc,n.Data))) AS first_date '
                   'FROM VC_Intrari i JOIN VC_NIR n ON n.SubunitId=i.SubunitId AND n.GestiuneId=i.GestiuneId '
                   'AND n.ExercitiuId=i.ExercitiuId AND n.IdIntrare=i.IdIntrare '
                   'JOIN VC_ProduseP p ON p.SubunitId=i.SubunitId AND p.GestiuneId=i.GestiuneId AND p.IdProdus=i.IdProdus '
                   'WHERE p.SubunitId=%s AND p.GestiuneId=%s AND p.ID=%s AND n.Anulat=0 '
                   'GROUP BY i.IdProdus', (subunit, gestiune, product))
    first_dates = {row['IdProdus']: row['first_date'].isoformat() if row['first_date'] else None
                   for row in cursor.fetchall()}
    # This card shows quantities. Reuse the stock ledger and balance algorithm
    # without requiring a valuation price for historical migrated variants.
    ledger = stock_source.warehouse(cursor, ctx, {variant['IdProdus'] for variant in variants})
    grouped, statement = stock.balance(ledger, date(year, 1, 1), date(year, 12, 31))
    balances = {key[0]: quantities for key, quantities in grouped.items()}
    movements = {}
    for row in statement:
        movements.setdefault(row['p'], []).append(row)
    cards = []
    for variant in variants:
        key = variant['IdProdus']
        rows = movements.get(key, [])
        quantities = balances.get(key, {})
        cards.append({'id': key, 'price': None if variant['PUTVA'] is None else format(Decimal(variant['PUTVA']), 'f'),
                      'code': variant['COD'] or '', 'active': bool(variant['Activ']), 'first_date': first_dates.get(key),
                      'opening': format(Decimal(quantities.get('opening', 0)), 'f'),
                      'closing': format(Decimal(quantities.get('closing', 0)), 'f'),
                      'count': len(rows), 'rows': [{'date': row['d'], 'kind': row['kind'], 'number': row['number'],
                          'document_id': row['document_id'], 'line_id': row['line_id'],
                          'quantity': format(Decimal(row['q']), 'f'), 'balance': format(Decimal(row['balance']), 'f')}
                          for row in reversed(rows[-20:])]})
    return {'product': product, 'name': item['DenumireP'] or '', 'um': item['UM'] or '',
            'year': year, 'variants': cards}
