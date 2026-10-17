"""Dated custody batches, separated by holder, origin, variant and use account.

The registry is derived from documents. Dates come from those documents, never
from DataSalvare. All reads use the context supplied by the transaction owner.
"""
from collections import defaultdict
from decimal import Decimal
from .nomenclatoare import NomError

SPECS = (('bp', 'VC_OI_Predare', 'IDBP', 'DATABP'),
         ('bt', 'VC_OI_Transfer', 'IDBT', 'DATABT'),
         ('bs', 'VC_OI_Casare', 'IDBC', 'DATABC'))


def day(value):
    return value.date() if hasattr(value, 'hour') else value


def events(cursor, ctx, holder=None, exclude=None):
    dates = {}
    for family, table, key, date in SPECS:
        cursor.execute('SELECT {key} AS document, IdProdus, MIN(DATE({date})) AS d, '
                       'MAX(DATE({date})) AS last_day FROM {table} WHERE SubunitId=%s '
                       'AND GestiuneId=%s AND ExercitiuId=%s AND Anulat=0 GROUP BY {key},IdProdus'.format(
                           key=key, date=date, table=table),
                       (ctx['subunit'], ctx['gestiune'], ctx['exercitiu']))
        for row in cursor.fetchall():
            if row['d'] is None or row['d'] != row['last_day']:
                raise NomError('OI_DATE', 'Un document OI are date lipsă sau neuniforme. Corectați documentul sursă.', 409)
            dates[(family, row['document'], row['IdProdus'])] = day(row['d'])
    params = [ctx['subunit'], ctx['gestiune']]
    where = ''
    if holder is not None:
        where = ' AND IDC_PTC=%s'
        params.append(holder)
    cursor.execute('SELECT IDOI,IDBP,IDBT,IDBC,IDBO,IDM,IDC_PTC,IdProdus,Cant,PUTVA '
                   'FROM VC_OI_Produse WHERE SubunitId=%s AND GestiuneId=%s' + where + ' ORDER BY IDOI', params)
    result = []
    for row in cursor.fetchall():
        refs = [(f, row[k]) for f, _, k, _ in SPECS if row[k]]
        if len(refs) != 1:
            raise NomError('OI_ORIGIN', 'O poziție din registrul OI nu identifică un singur document.', 409)
        family, document = refs[0]
        if exclude == (family, document):
            continue
        movement_day = dates.get((family, document, row['IdProdus']))
        if movement_day is None:
            raise NomError('OI_ORIGIN', 'Registrul OI conține o poziție fără document activ în contextul curent.', 409)
        quantity = Decimal(row['Cant'] or 0)
        origin = document if quantity > 0 else row['IDBO']
        if not origin and quantity:
            raise NomError('OI_ORIGIN', 'Lipsește proveniența unei ieșiri din registrul OI.', 409)
        result.append({**row, 'family': family, 'document': document, 'date': movement_day,
                       'quantity': quantity, 'origin': origin})
    return result


def batches(cursor, ctx, holder, exclude=None, on_date=None):
    grouped = defaultdict(list)
    for event in events(cursor, ctx, holder, exclude):
        grouped[(event['origin'], event['IdProdus'], event['IDM'], event['PUTVA'])].append(event)
    result = {}
    for key, items in grouped.items():
        positive = [item for item in items if item['quantity'] > 0]
        if not positive:
            continue
        first = min(positive, key=lambda item: (item['date'], item['IDOI']))
        quantities = defaultdict(Decimal)
        for item in items:
            quantities[item['date']] += item['quantity']
        at_date = sum((q for d, q in quantities.items() if on_date is None or d <= on_date), Decimal(0))
        # A backdated withdrawal must also leave stock for subsequent documents.
        capacity, running = at_date, at_date
        if on_date is not None:
            for movement_day in sorted(d for d in quantities if d > on_date):
                running += quantities[movement_day]
                capacity = min(capacity, running)
        if at_date <= 0 or first['PUTVA'] is None:
            continue
        result[key] = {'origin': key[0], 'product': key[1], 'idm': key[2],
                       'qty': at_date, 'capacity': max(Decimal(0), capacity),
                       'putva': first['PUTVA'], 'date': first['date']}
    return result


def check_timeline(cursor, ctx):
    grouped = defaultdict(lambda: defaultdict(Decimal))
    for event in events(cursor, ctx):
        key = (event['IDC_PTC'], event['origin'], event['IdProdus'], event['IDM'], event['PUTVA'])
        grouped[key][event['date']] += event['quantity']
    for quantities in grouped.values():
        running = Decimal(0)
        for movement_day in sorted(quantities):
            running += quantities[movement_day]
            if running < 0:
                raise NomError('OI_NEGATIVE', 'Operația ar produce o deținere negativă la data %s. '
                               'Corectați mai întâi documentele dependente.' % movement_day.strftime('%d.%m.%Y'), 409)
