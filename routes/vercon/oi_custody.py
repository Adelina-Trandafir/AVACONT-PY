"""Dated custody batches, separated by holder, origin, variant and use account.

The registry is derived from documents. Dates come from those documents, never
from DataSalvare. All reads use the context supplied by the transaction owner.
TC accounts are inherited through IDBO from the initial BP register receipt.
"""
from collections import defaultdict
from decimal import Decimal
from .oi_accounts import Accounts
from .nomenclatoare import NomError

SPECS = (('bp', 'VC_OI_Predare', 'IDBP', 'DATABP'),
         ('bt', 'VC_OI_Transfer', 'IDBT', 'DATABT'),
         ('bs', 'VC_OI_Casare', 'IDBC', 'DATABC'))
REGISTER_COLUMNS = 'IDOI,IDBP,IDBT,IDBC,IDBO,IDM,IDC_PTC,IdProdus,Cant,PUTVA'


def day(value):
    return value.date() if hasattr(value, 'hour') else value


def document_where(family):
    """Document selector for the gestiune-level registry; IDBC is shared by all families."""
    return {
        'bp': 'IDBP=%s AND COALESCE(IDBT,0)=0',
        'bt': 'IDBT=%s AND COALESCE(IDBP,0)=0',
        'bs': 'IDBC=%s AND COALESCE(IDBP,0)=0 AND COALESCE(IDBT,0)=0',
    }[family]


def document_of(row):
    """BP/BT markers determine the family; with neither marker IDBC identifies a BS.

    Legacy VBA left IDBC empty on BP/BT. A populated IDBC must agree with the marker.
    """
    bp, bt, current = row['IDBP'], row['IDBT'], row['IDBC']
    position = row['IDOI']
    if bp and bt:
        raise NomError('OI_ORIGIN', 'Poziția OI %s indică simultan o predare și un transfer.' % position, 409)
    if bp or bt:
        family, document = ('bp', bp) if bp else ('bt', bt)
        if current and current != document:
            raise NomError('OI_ORIGIN', 'Poziția OI %s are IDBC=%s diferit de ID%s=%s. '
                           'Legăturile documentului trebuie corectate.' %
                           (position, current, 'BP' if bp else 'BT', document), 409)
        return family, document
    if not current:
        raise NomError('OI_ORIGIN', 'Poziția OI %s nu identifică bonul curent.' % position, 409)
    return 'bs', current


def movement(row):
    """The received lot is the current BP/BT; a withdrawal takes from IDBO.

    IDBO remains the source document on both sides of a transfer. It must not
    replace the current transfer as the receiver's new lot identity.
    """
    family, document = document_of(row)
    quantity = Decimal(row['Cant'] or 0)
    if family == 'bp' and row['IDBO']:
        raise NomError('OI_ORIGIN', 'Poziția OI %s este o predare cu proveniență IDBO completată.' % row['IDOI'], 409)
    if family != 'bp' and quantity and not row['IDBO']:
        raise NomError('OI_ORIGIN', 'Lipsește proveniența transferului/casării din poziția OI %s.' % row['IDOI'], 409)
    origin = document if quantity > 0 else row['IDBO']
    if not origin and quantity:
        raise NomError('OI_ORIGIN', 'Lipsește proveniența ieșirii din poziția OI %s.' % row['IDOI'], 409)
    return {**row, 'family': family, 'document': document, 'quantity': quantity, 'origin': origin}


def register_rows(cursor, ctx, holder=None, document=None):
    """Read scoped registry rows using the same family rules in every document operation."""
    params = [ctx['subunit'], ctx['gestiune']]
    where = ''
    if holder is not None:
        where += ' AND IDC_PTC=%s'
        params.append(holder)
    if document is not None:
        where += ' AND ' + document_where(document[0])
        params.append(document[1])
    cursor.execute('SELECT ' + REGISTER_COLUMNS + ' FROM VC_OI_Produse '
                   'WHERE SubunitId=%s AND GestiuneId=%s' + where + ' ORDER BY IDOI', params)
    rows = cursor.fetchall()
    if not rows:
        return []
    if holder is None and document is None:
        sources = rows
    else:
        cursor.execute('SELECT ' + REGISTER_COLUMNS + ' FROM VC_OI_Produse '
                       'WHERE SubunitId=%s AND GestiuneId=%s AND Cant>0 '
                       'AND (COALESCE(IDBP,0)<>0 OR COALESCE(IDBT,0)<>0) ORDER BY IDOI',
                       (ctx['subunit'], ctx['gestiune']))
        sources = cursor.fetchall()
    accounts = Accounts(sources, movement)
    return [accounts.normalized(row) for row in rows]


def register_values(family, document, row):
    """Canonical links for a new registry row, including the generic current IDBC."""
    if family not in {spec[0] for spec in SPECS}:
        raise ValueError('Unknown OI document family')
    values = {**row, 'IDBP': document if family == 'bp' else 0,
              'IDBT': document if family == 'bt' else 0, 'IDBC': document,
              'IDBO': row.get('IDBO') if family != 'bp' else None}
    movement({**values, 'IDOI': '(nouă)'})
    return values


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
    result = []
    for row in register_rows(cursor, ctx, holder):
        family, document = row['family'], row['document']
        if exclude == (family, document):
            continue
        movement_day = dates.get((family, document, row['IdProdus']))
        if movement_day is None:
            if not row['IdProdus']:
                raise NomError('OI_ORIGIN', 'Poziția OI %s nu are produsul completat. Completați-l din «Reglare registru OI», '
                               'butonul din pagina bonurilor de predare.' % row['IDOI'], 409)
            raise NomError('OI_ORIGIN', 'Poziția OI %s se leagă de bonul %s (%s), dar acel bon nu are nicio linie activă cu produsul %s '
                           'în gestiunea și exercițiul curent.' % (row['IDOI'], document, family.upper(), row['IdProdus']), 409)
        if row['IDBO']:
            sources = [(f, dates[(f, row['IDBO'], row['IdProdus'])]) for f in ('bp', 'bt')
                       if (f, row['IDBO'], row['IdProdus']) in dates]
            if len(sources) != 1:
                detail = 'nu identifică o predare sau un transfer activ' if not sources else 'identifică atât o predare, cât și un transfer'
                raise NomError('OI_ORIGIN', 'Proveniența IDBO=%s din poziția OI %s %s pentru produsul %s. '
                               'Legăturile documentelor trebuie corectate.' %
                               (row['IDBO'], row['IDOI'], detail, row['IdProdus']), 409)
            source_family, source_day = sources[0]
            if (source_family, row['IDBO']) == (family, document):
                raise NomError('OI_ORIGIN', 'Poziția OI %s indică propriul bon ca proveniență IDBO.' % row['IDOI'], 409)
            if source_day > movement_day:
                raise NomError('OI_DATE', 'Proveniența IDBO=%s din poziția OI %s este ulterioară bonului curent.' %
                               (row['IDBO'], row['IDOI']), 409)
        result.append({**row, 'date': movement_day})
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


def check_timeline(cursor, ctx, products=None):
    """Fail when a holding goes negative; `products` limits the check to what the operation touches."""
    grouped = defaultdict(lambda: defaultdict(Decimal))
    for event in events(cursor, ctx):
        if products is not None and event['IdProdus'] not in products:
            continue
        key = (event['IDC_PTC'], event['origin'], event['IdProdus'], event['IDM'], event['PUTVA'])
        grouped[key][event['date']] += event['quantity']
    for quantities in grouped.values():
        running = Decimal(0)
        for movement_day in sorted(quantities):
            running += quantities[movement_day]
            if running < 0:
                raise NomError('OI_NEGATIVE', 'Operația ar produce o deținere negativă la data %s. '
                               'Corectați mai întâi documentele dependente.' % movement_day.strftime('%d.%m.%Y'), 409)
