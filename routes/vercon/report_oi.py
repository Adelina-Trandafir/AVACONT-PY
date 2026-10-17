"""OI reports: use accounts belong to movements, warehouse accounts to products."""
from decimal import Decimal
from . import stock
from .report_data import exact, selected, accepted, aggregate
from .nomenclatoare import NomError


def documents(source, spec):
    metadata, params, scale = source.metadata(), source.params, source.ctx['scale']
    rows = []
    for raw in source.rows('oi_documents'):
        if raw['annulled'] or raw['family'] != spec['family']:
            continue
        info = metadata.get(raw['variant'])
        if info is None:
            raise NomError('REPORT_SOURCE', 'O linie OI nu are produsul în nomenclatorul contextului.', 409)
        row = {**info, **raw}
        if not accepted(row, params):
            continue
        rows.append({**row, 'date': stock.day_of(row['date']).isoformat(),
                     **{key: exact(row[key], scale) for key in ('quantity', 'price', 'value')}})
    rows.sort(key=lambda row: (row['date'], row['document'], row['line_id']))
    if spec['kind'] == 'document_summary':
        return aggregate(rows, ('document', 'category', 'receiver', 'giver'), ('value',))
    return rows


def balances(source, spec):
    metadata, params, scale = source.metadata(), source.params, source.ctx['scale']
    accounts = {row['category']: row['account'] for row in source.rows('oi_accounts')}
    holders = {row['receiver']: row['party'] for row in source.rows('oi_holders')}
    movements = [row for row in source.rows('custody_ledger')
                 if selected(row['p'], params, 'variant') and selected(row['category'], params, 'category')
                 and selected(row['holder'], params, 'receiver')]
    for row in movements:
        if row['p'] not in metadata or row['q'] is None:
            raise NomError('REPORT_SOURCE', 'O mișcare OI are produsul sau cantitatea lipsă.', 409)
    grouped, statements = stock.balance(movements, params['start'], params['end'], ('holder', 'category', 'p'))

    def identity(key):
        holder, category, variant = key
        info = metadata[variant]
        if info['price'] is None or info['price'] < 0:
            raise NomError('STOCK_PRICE_INVALID', 'Prețul produsului nu permite evaluarea deținerii.', 409)
        return {**info, 'warehouse_category': info['category'], 'warehouse_account': info['account'],
                'category': category, 'account': accounts.get(category) or 'Fără cont',
                'receiver': holder, 'party': holders.get(holder) or str(holder), 'inventory': info['code'] or '',
                'price': exact(info['price'], scale)}

    final = spec['kind'] in ('final', 'final_total', 'inventory', 'inventory_total')
    rows = []
    for key, quantities in grouped.items():
        if final and quantities['closing'] == 0:
            continue
        info = identity(key)
        amounts = {**quantities, 'incoming': sum(quantities[b] for b in stock.POSITIVE),
                   'outgoing': sum(quantities[b] for b in stock.NEGATIVE)}
        rows.append({**info, **{k: exact(v, scale) for k, v in amounts.items()},
                     **{k + '_value': stock.value_text(v, metadata[key[-1]]['price'], scale) for k, v in amounts.items()}})
    if spec['kind'] in ('final_total', 'inventory_total'):
        amounts = ('opening', 'incoming', 'outgoing', 'closing', 'opening_value', 'incoming_value', 'outgoing_value', 'closing_value')
        rows = aggregate(rows, ('category', 'variant'), amounts)
        for row in rows:
            row['receiver'], row['party'] = None, ''
    if spec['kind'] == 'custody_statement':
        opening = [{**row, 'date': params['start'].isoformat(), 'number': 'SOLD INIȚIAL',
                    'incoming': '', 'outgoing': '', 'closing': row['opening'],
                    'closing_value': row['opening_value']} for row in rows]
        details = []
        for movement in statements:
            info = identity(movement['key'])
            quantity = Decimal(movement['q'])
            details.append({**info, 'date': movement['d'], 'number': movement['kind'].upper() + ' ' + str(movement['number'] or ''),
                            'incoming': exact(quantity, scale) if movement['kind'] in ('bp', 'bt_in') else '',
                            'outgoing': exact(-quantity, scale) if movement['kind'] in ('bt_out', 'bs') else '',
                            'closing': exact(movement['balance'], scale),
                            'closing_value': stock.value_text(movement['balance'], metadata[movement['p']]['price'], scale)})
        rows = opening + details
    return sorted(rows, key=lambda row: (row['account'], row['party'], row['name'] or '', row['variant'],
                                         row.get('date', ''), 0 if row.get('number') == 'SOLD INIȚIAL' else 1))


def dataset(source, spec):
    return documents(source, spec) if spec.get('family') in ('bp', 'bt', 'bs') else balances(source, spec)
