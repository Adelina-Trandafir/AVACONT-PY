"""MF selection identities and date-specific asset/holder catalogue. Read-only."""
from collections import defaultdict
from decimal import Decimal
from . import stock
from .nomenclatoare import NomError


MODES = {'MF_Bon_BP': 'bons', 'MF_Bon_BT': 'bons', 'MF_Bon_BS': 'bons',
         'MF_Balanta': 'balance', 'MF_Balanta_Subinv': 'balance',
         'MF_LISTA_INVENTAR': 'inventory', 'Amortizare': 'depreciation'}


def parse_selection(raw):
    if raw is None:
        return None
    try:
        items = raw.split(',')
        if not 1 <= len(items) <= 1000:
            raise ValueError()
        chosen = set()
        for item in items:
            parts = item.split(':')
            if len(parts) != 6 or parts[0] != 'mf-item' or parts[1] not in MODES.values():
                raise ValueError()
            values = []
            for value in parts[2:]:
                if value == 'none':
                    values.append(None)
                elif value.isdecimal() and int(value) > 0:
                    values.append(int(value))
                else:
                    raise ValueError()
            category, receiver, document, variant = values
            if variant is None or (parts[1] == 'bons') != (document is not None):
                raise ValueError()
            if parts[1] == 'bons' and category is not None or parts[1] == 'balance' and receiver is not None:
                raise ValueError()
            chosen.add((parts[1], *values))
        return chosen
    except (ValueError, TypeError):
        raise NomError('REPORT_FILTER', 'Selecția mijloacelor fixe este invalidă.', 422) from None


def identity(spec, row):
    mode = spec['mf_selection']
    return (mode, None if mode == 'bons' else row.get('category'),
            None if mode == 'balance' else row.get('receiver'),
            row.get('document') if mode == 'bons' else None, row['variant'])


def item_id(spec, row):
    return 'mf-item:' + ':'.join('none' if value is None else str(value) for value in identity(spec, row))


def accepted(spec, row, params):
    chosen = params.get('mf_selection')
    return not chosen or identity(spec, row) in chosen


def depreciation_options(source):
    """One selection membership per asset/holder at the report date.

    Unassigned or written-off assets retain a 'Fără primitor' membership, so
    switching grouping never drops assets from the existing depreciation report.
    The accounting category comes from product metadata; CC is its classification.
    """
    if '_mf_depreciation_options' in source.cache:
        return source.cache['_mf_depreciation_options']
    source.cursor.execute(
        'SELECT DISTINCT pp.IdProdus AS variant,pr.CC AS classification,c.Denumire AS classification_name '
        'FROM VC_ProduseM m JOIN VC_ProduseP pp ON pp.SubunitId=m.SubunitId '
        'AND pp.GestiuneId=m.GestiuneId AND pp.IdProdus=m.IdProdus '
        'LEFT JOIN VC_Produse pr ON pr.SubunitId=pp.SubunitId AND pr.GestiuneId=pp.GestiuneId AND pr.ID=pp.ID '
        'LEFT JOIN AVACONT_COMUN.VC_ClasificariMF c ON c.Cod=pr.CC '
        'WHERE m.SubunitId=%s AND m.GestiuneId=%s', (source.ctx['subunit'], source.ctx['gestiune']))
    classifications = {row['variant']: row for row in source.cursor.fetchall()}
    holders, quantities = {}, defaultdict(Decimal)
    if source.params.get('group_by_receivers'):
        holders = {row['receiver']: row['party'] for row in source.rows('oi_holders')}
        for movement in source.rows('custody_ledger'):
            if stock.day_of(movement['d']) <= source.params['end']:
                if movement['q'] is None:
                    raise NomError('REPORT_SOURCE', 'O mișcare MF nu are cantitate.', 409)
                quantities[(movement['p'], movement['holder'])] += Decimal(str(movement['q']))
    memberships = defaultdict(list)
    for (variant, holder), quantity in quantities.items():
        if quantity > 0:
            memberships[variant].append(holder)
    metadata = source.metadata()
    rows = []
    for variant, classification in classifications.items():
        info = metadata.get(variant)
        if info is None:
            raise NomError('REPORT_SOURCE', 'Mijlocul fix nu are metadate în gestiunea curentă.', 409)
        receivers = memberships.get(variant, [None]) if source.params.get('group_by_receivers') else [None]
        for receiver in receivers:
            rows.append({**info, **classification, 'receiver': receiver,
                         'party': holders.get(receiver) or (str(receiver) if receiver is not None else 'Fără primitor')})
    source.cache['_mf_depreciation_options'] = rows
    return rows
