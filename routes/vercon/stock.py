"""VC08-01: pure stock/balance/statement engine and its scoped read service.

SI + entries + returns + transfers in - issues - storno - transfers out - scrapping = SF.
Opening stock includes every movement before start, even when the display is filtered.
No float, no Forms/TempVars, no connection, commit or schema creation.
"""
from decimal import Decimal, localcontext
from utils import vercon_suppliers as suppliers

from . import stock_source, stock_valuation
from .nomenclatoare import NomError

BUCKETS = {'nir': 'entries', 'br': 'returns', 'bc': 'issues', 'storno': 'storno',
           'bp': 'entries', 'bt_in': 'transfers_in', 'bt_out': 'transfers_out', 'bs': 'scrapping'}
POSITIVE = ('entries', 'returns', 'transfers_in')
NEGATIVE = ('issues', 'storno', 'transfers_out', 'scrapping')


def day_of(raw):
    if raw is None:
        raise NomError('STOCK_DATE_MISSING', 'O mișcare de stoc nu are dată. Corectați documentul sursă înainte de calcul.', 409)
    return raw.date() if hasattr(raw, 'hour') else raw


def balance(movements, start, end, dimensions=('p',)):
    """Exact decimal quantities grouped independently by the FULL key (N09).

    Closed interval [start,end] for every family, including BS (N07).
    Supplier grouping is document provenance, never a fictional FIFO allocation.
    """
    if start > end:
        raise NomError('INVALID', 'Data de început trebuie să fie înaintea datei finale.', 422)
    groups = {}
    statements = []
    ordered = sorted(movements, key=lambda m: (
        day_of(m['d']), 0 if m['q'] >= 0 else 1, m['kind'], m['document_id'], m['line_id']))
    for movement in ordered:
        day = day_of(movement['d'])
        if day > end:
            continue
        key = tuple(movement.get(dimension) for dimension in dimensions)
        row = groups.setdefault(key, {'opening': 0, **{b: 0 for b in BUCKETS.values()}, 'closing': 0})
        quantity = Decimal(str(movement['q']))
        row['closing'] += quantity
        if day < start:
            row['opening'] += quantity
        else:
            # Migrated corrections may have a signed quantity. The document family
            # determines the bucket's sign; abs() would reverse such corrections.
            bucket = BUCKETS[movement['kind']]
            row[bucket] += quantity if bucket in POSITIVE else -quantity
            statements.append({**movement, 'd': day.isoformat(), 'key': key, 'balance': row['closing']})
    for row in groups.values():
        expected = row['opening'] + sum(row[b] for b in POSITIVE) - sum(row[b] for b in NEGATIVE)
        if expected != row['closing']:
            raise NomError('STOCK_MISMATCH', 'Mișcările nu se reconciliază pe fiecare variantă.', 409)
    return groups, statements


def available(movements, on_date, draft=None):
    """Informative availability after excluding the edited document and its draft.

    Draft quantities are direct decimals aggregated per variant, never rounded to 2.
    The write protocol still checks the complete timeline inside its transaction.
    """
    result = {}
    for movement in movements:
        if day_of(movement['d']) <= on_date:
            product = movement['p']
            result[product] = result.get(product, 0) + movement['q']
    for product, quantity in (draft or {}).items():
        if not isinstance(quantity, (int, Decimal)) or isinstance(quantity, bool) or quantity < 0:
            raise NomError('INVALID', 'Cantitatea draftului trebuie să fie pozitivă și exactă.', 422)
        result[product] = result.get(product, 0) - quantity
    return result


def quantity_text(coef, scale):
    # Explicit precision accommodates sums larger than one stored decimal value.
    with localcontext() as context:
        context.prec = max(40, len(str(abs(coef))) + scale + 4)
        return format(Decimal(str(coef)), 'f')


def value_text(quantity, price, scale):
    # Exact valuation, no rounding per movement; UI/report rounds only for display.
    with localcontext() as context:
        context.prec = 50
        return quantity_text(Decimal(str(quantity)) * Decimal(str(price)), scale)


def read(cursor, ctx, start, end, *, ledger='warehouse', product=None, variant=None,
         category=None, supplier=None, holder=None, group='variant'):
    if ledger not in ('warehouse', 'custody') or group not in ('variant', 'supplier'):
        raise NomError('INVALID', 'Tipul de stoc sau de grupare este invalid.', 422)
    if ledger == 'custody' and ctx['type'] not in ('OI', 'MF'):
        raise NomError('FORM_NOT_AVAILABLE', 'Deținerile sunt disponibile numai pentru OI/MF.', 403)
    if ledger == 'warehouse' and holder is not None:
        raise NomError('INVALID', 'Filtrul deținătorului se aplică registrului OI/MF.', 422)
    if ledger == 'custody' and (supplier is not None or group == 'supplier'):
        raise NomError('INVALID', 'Registrul deținătorilor nu atribuie mișcări furnizorilor.', 422)
    metadata = stock_source.variants(cursor, ctx)
    selected = {key: info for key, info in metadata.items()
                if (variant is None or key == variant)
                and (product is None or info['product'] == product)
                and (ledger == 'custody' or category is None or info['category'] == (None if category == 0 else category))}
    movements = (stock_source.warehouse(cursor, ctx, selected) if ledger == 'warehouse'
                 else stock_source.custody(cursor, ctx))
    # Supplier filter selects eligible VARIANTS, then keeps all their stock movements.
    # Consumption has no supplier FK: do not invent an allocation or discard its opening stock.
    if supplier is not None:
        eligible = {m['p'] for m in movements if m['supplier'] == supplier and day_of(m['d']) <= end}
        selected = {key: info for key, info in selected.items() if key in eligible}
    movements = [m for m in movements if m['p'] in selected and (holder is None or m['holder'] == holder)
                 and (ledger != 'custody' or category is None or m['category'] == (None if category == 0 else category))]
    dimensions = ('holder', 'category', 'p') if ledger == 'custody' else ('supplier', 'p') if group == 'supplier' else ('p',)
    grouped, statements = balance(movements, start, end, dimensions)
    for key in grouped:
        stock_valuation.price(selected[key[-1]])
    scale = ctx['scale']
    holders = {}
    accounts = {}
    supplier_names = {}
    if group == 'supplier':
        cursor.execute('SELECT CodFiscal,DenumirePartener FROM ' + suppliers.relation() + ' p')
        supplier_names = {row['CodFiscal']: row['DenumirePartener'] or row['CodFiscal'] for row in cursor.fetchall()}
    if ledger == 'custody':
        cursor.execute('SELECT IDC,Denumire FROM VC_Clienti WHERE SubunitId=%s AND GestiuneId=%s',
                       (ctx['subunit'], ctx['gestiune']))
        holders = {row['IDC']: row['Denumire'] or str(row['IDC']) for row in cursor.fetchall()}
        cursor.execute('SELECT IdMasina,Masina FROM VC_Masini WHERE SubunitId=%s AND GestiuneId=%s',
                       (ctx['subunit'], ctx['gestiune']))
        accounts = {row['IdMasina']: row['Masina'] for row in cursor.fetchall()}

    def identity(key):
        info = selected[key[-1]]
        return {**info, 'price': quantity_text(info['price'] or 0, scale),
                'category': key[1] if ledger == 'custody' else info['category'],
                'category_name': accounts.get(key[1]) if ledger == 'custody' else info['category_name'],
                'holder': key[0] if ledger == 'custody' else None,
                'holder_name': holders.get(key[0], str(key[0])) if ledger == 'custody' else '',
                'supplier': key[0] if group == 'supplier' else None,
                'supplier_name': supplier_names.get(key[0], str(key[0] or '')) if group == 'supplier' else ''}

    def row_order(pair):
        key = pair[0]
        info = selected[key[-1]]
        prefix = ((suppliers.name_key(holders.get(key[0], '')), suppliers.name_key(accounts.get(key[1], '')))
                  if ledger == 'custody' else (suppliers.name_key(supplier_names.get(key[0], key[0])),)
                  if group == 'supplier' else ())
        return (*prefix, suppliers.name_key(info['name']), info['price'] or Decimal(0), suppliers.name_key(info['code']))

    rows = []
    totals = {bucket: 0 for bucket in ('opening', *dict.fromkeys(BUCKETS.values()), 'closing')}
    # Totals use direct decimal products, without per-variant display rounding.
    for key, quantities in sorted(grouped.items(), key=row_order):
        info = identity(key)
        price = selected[key[-1]]['price'] or 0
        rows.append({**info, 'quantities': {k: quantity_text(v, scale) for k, v in quantities.items()},
                     'values': {k: value_text(v, price, scale) for k, v in quantities.items()}})
        for bucket, quantity in quantities.items():
            totals[bucket] += quantity * price
    sheet = []
    for movement in statements:
        key = movement['key']
        info = identity(key)
        price = selected[key[-1]]['price'] or 0
        sheet.append({**info, 'date': movement['d'], 'kind': movement['kind'], 'number': movement['number'],
                      'document_id': movement['document_id'], 'line_id': movement['line_id'],
                      'quantity': quantity_text(movement['q'], scale), 'value': value_text(movement['q'], price, scale),
                      'balance': quantity_text(movement['balance'], scale),
                      'balance_value': value_text(movement['balance'], price, scale)})
    return {'start': start.isoformat(), 'end': end.isoformat(), 'ledger': ledger, 'group': group,
            'scale': scale, 'zec': ctx['zec'], 'valuation': 'current_variant_price',
            'supplier_mode': 'document_provenance' if group == 'supplier' else 'variant_eligibility',
            'supplier_notice': ('Grupare după furnizorul documentului. Consumul și returul fără legătură '
                                'la furnizor sunt în grupa neatribuită; aceasta nu este o repartizare a stocului pe furnizori.')
                               if group == 'supplier' else None,
            'rows': rows, 'statement': sheet,
            'categories': [{'id': key, 'label': label or 'Fără categorie'} for key, label in
                           (accounts if ledger == 'custody' else dict((info['category'], info['category_name']) for info in metadata.values())).items()],
            'value_totals': {k: quantity_text(v, scale) for k, v in totals.items()}}
