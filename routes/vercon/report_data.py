"""Report data from owner-editable SQL. Exact decimals until the output boundary."""
from collections import defaultdict
from decimal import Decimal, localcontext
from . import numeric, report_query, report_store, stock, oi_accounts, stock_valuation
from .nomenclatoare import NomError

REQUIRED_FIELDS = {
    'oi_documents': 'document number date receiver party giver giver_name category account warehouse_category warehouse_account explanation annulled line_id variant quantity price value family',
    'custody_ledger': 'p d q kind document_id line_id number holder category',
    'oi_accounts': 'category account',
    'oi_holders': 'receiver party',
    'metadata': 'variant product code price name um cpv category account',
    'ledger': 'p d q kind document_id line_id number supplier',
    'receipts': 'document number invoice date movement_date supplier party fiscal_code address committee annulled line_id variant quantity price value vat family',
    'consumption': 'document number date receiver party explanation annulled line_id variant quantity price value family',
    'committee': 'IdComisie Receptie1 Receptie2 Receptie3 Receptie4',
    'food_header': 'IdLista NumarLA DATALA Anulat DEP MicDejun_P VMD MicDejun_V MicDejun_D Dejun_P VD Dejun_V Dejun_D Cina_P VC Cina_V Cina_D',
    'food_items': 'meal Id IdFel Explicatie',
    'food_bons': 'document number date value annulled receiver',
    'food_consumption': 'line_id variant quantity price value',
    'food_history': 'IdLista NumarLA date difference value',
    'settings': 'SettingKey IntValue',
}


class Source:
    def __init__(self, cursor, unit, ctx, params):
        self.cursor, self.unit, self.ctx, self.params = cursor, unit, ctx, params
        self.cache, self.versions = {}, {}

    def rows(self, key, *, result_filters=None):
        cache_key = (key, tuple(sorted(result_filters.items()))) if result_filters else key
        if cache_key not in self.cache:
            configured = report_store.read(self.unit, 'query', key)
            self.cache[cache_key] = report_query.execute(self.cursor, configured['value'], self.ctx, self.params,
                                                       result_filters=result_filters)
            required = set(REQUIRED_FIELDS[key].split())
            for row in self.cache[cache_key]:
                missing = required - row.keys()
                if missing:
                    raise NomError('REPORT_SOURCE', 'Sursa SQL ' + key + ' nu întoarce câmpurile obligatorii: ' + ', '.join(sorted(missing)) + '.', 422)
            if key in ('custody_ledger', 'oi_documents') and self.cache[cache_key]:
                if '_oi_line_accounts' not in self.cache:
                    self.cache['_oi_line_accounts'] = oi_accounts.line_accounts(self.cursor, self.ctx)
                self.cache[cache_key] = oi_accounts.apply_lines(self.cache[cache_key], self.cache['_oi_line_accounts'], key == 'oi_documents')
            self.versions[key] = configured['version']
        return self.cache[cache_key]

    def metadata(self, *, result_filters=None):
        rows = self.rows('metadata', result_filters=result_filters)
        result = {row['variant']: row for row in rows}
        if len(result) != len(rows) or None in result:
            raise NomError('REPORT_SOURCE', 'Sursa metadata trebuie să întoarcă exact un rând pentru fiecare variantă.', 409)
        return result


def exact(value, scale):
    return None if value is None else stock.quantity_text(Decimal(value), scale)


def selected(value, params, key):
    values = params.get(key + '_ids')
    if values is not None:
        return value in values
    return params.get(key) is None or value == params[key]


def accepted(row, params, *, day='date', document=True):
    date = stock.day_of(row[day])
    if not params['start'] <= date <= params['end']:
        return False
    receipts = params.get('receipt_selection')
    if receipts and (('receipt-product', row.get('supplier'), row.get('category'), row.get('product')) not in receipts
                     and ('receipt-document-product', row.get('supplier'), row.get('document'), row.get('product')) not in receipts):
        return False
    outgoing = params.get('outgoing_selection')
    if outgoing:
        scopes = (('account', row.get('category')), ('receiver', row.get('receiver')), ('all', None))
        if not any((scope, scope_id, row.get('document'), product) in outgoing
                   for scope, scope_id in scopes for product in (None, row.get('product'))):
            return False
    checks = ('variant', 'category', 'supplier', 'receiver') + (('document',) if document else ())
    return all(selected(row.get(key), params, key) for key in checks)


def storno_display(row):
    """Signed FCS report values; never mutate the source or the stored quantity.

    copy_abs/copy_negate preserve every decimal digit independently of the
    active Decimal context and keep an already-negative amount negative.
    """
    displayed = dict(row)
    for key in ('quantity', 'value', 'vat', 'header_value'):
        value = row.get(key)
        if value is not None and value != '':
            amount = Decimal(str(value)).copy_abs()
            displayed[key] = format(amount.copy_negate() if amount else amount, 'f')
    return displayed


def documents(source, family=None, *, include_returns=False):
    metadata, ctx = source.metadata(), source.ctx
    kind = 'receipts' if family in ('nir', 'storno') or family is None else 'consumption'
    out = []
    for raw in source.rows(kind):
        if raw['annulled'] or family is not None and raw['family'] != family and not (include_returns and raw['family'] == 'br'):
            continue
        info = metadata.get(raw['variant'])
        if info is None:
            raise NomError('REPORT_SOURCE', 'Linia de document nu are o variantă în contextul raportului.', 409)
        row = {**info, **raw}
        if not accepted(row, source.params, day='movement_date' if kind == 'receipts' else 'date'):
            continue
        value = raw['value']
        if value is None and raw['family'] in ('storno', 'br'):
            # These source tables have no stored line total. The derivation is
            # explicit and kept separate from historical stored NIR/BC values.
            if raw['quantity'] is None or raw['price'] is None:
                raise NomError('REPORT_SOURCE', 'Lipsesc cantitatea/prețul pentru derivarea returului sau stornoului.', 409)
            value = numeric.to_stored(numeric.calc_round(numeric.from_stored(raw['quantity'], ctx['scale']) *
                                    numeric.from_stored(raw['price'], ctx['scale']), ctx['zec']), ctx['scale'])
            row['value_origin'] = 'derived_quantity_document_price'
        else:
            row['value_origin'] = 'stored_document_line'
        row['date'] = stock.day_of(raw['date']).isoformat()
        for key in ('quantity', 'price', 'vat', 'header_value'):
            if key in row:
                row[key] = exact(row[key], ctx['scale'])
        row['value'] = exact(value, ctx['scale'])
        # Normalize only the FCS document report. Centralizers and stock paths
        # retain their existing sign handling; do not subtract storno twice.
        out.append(storno_display(row) if family == 'storno' else row)
    return out


def aggregate(rows, keys, amounts=('quantity', 'value')):
    groups = {}
    variants = {}
    with localcontext() as context:
        context.prec = 60
        for row in rows:
            key = tuple(row.get(k) for k in keys)
            variants.setdefault(key, set()).add(row.get('stock_variant', row.get('variant')))
            if key not in groups:
                groups[key] = {**row, **{a: Decimal(0) for a in amounts}}
            target = groups[key]
            for amount in amounts:
                value = row.get(amount)
                target[amount] = None if value is None or target[amount] is None else target[amount] + Decimal(str(value))
        return [{**row, 'stock_variant': next(iter(variants[key])) if len(variants[key]) == 1 else None,
                 **{a: None if row[a] is None else format(row[a], 'f') for a in amounts}} for key, row in groups.items()]


def balances(source, final=False, *, hide_empty=False):
    ctx, params, metadata = source.ctx, source.params, source.metadata()
    chosen = {key: row for key, row in metadata.items() if all(
        selected(row.get(field), params, field) for field in ('variant', 'category'))}
    ledger = source.rows('ledger')
    if any(m['q'] is None for m in ledger if m['p'] in chosen):
        raise NomError('REPORT_SOURCE', 'O mișcare selectată nu are cantitate.', 409)
    movements = [{**m, 'q': Decimal(m['q'])} for m in ledger if m['p'] in chosen]
    if params.get('supplier') is not None or params.get('supplier_ids') is not None:
        eligible = {m['p'] for m in movements if selected(m['supplier'], params, 'supplier') and stock.day_of(m['d']) <= params['end']}
        movements = [m for m in movements if m['p'] in eligible]
    if params.get('receiver') is not None or params.get('receiver_ids') is not None:
        raise NomError('REPORT_FILTER', 'Filtrul primitorului nu se aplică stocului magaziei.', 422)
    grouped, statements = stock.balance(movements, params['start'], params['end'])
    rows = []
    for (variant,), quantities in grouped.items():
        info = chosen[variant]
        if final and quantities['closing'] == 0:
            continue
        amounts = {'opening': quantities['opening'], **stock.warehouse_turnover(quantities), 'closing': quantities['closing']}
        if hide_empty and not params.get('show_without_movements') and not any(amounts.values()):
            continue
        price = stock_valuation.price(info)
        rows.append({**info, 'price': exact(price, ctx['scale']),
                     **{k: exact(v, ctx['scale']) for k, v in amounts.items()},
                     **{k + '_value': stock.value_text(v, price, ctx['scale']) for k, v in amounts.items()}})
    sheets = []
    for m in statements:
        info = chosen[m['p']]
        sheets.append({**info, **m, 'variant': m['p'], 'date': m['d'],
                       'number': (m['kind'].upper() + ' ' + str(m['number'] or '')),
                       'incoming': exact(m['q'], ctx['scale']) if m['kind'] in ('nir', 'br') else '',
                       'outgoing': exact(-m['q'], ctx['scale']) if m['kind'] in ('bc', 'storno') else '',
                       'closing': exact(m['balance'], ctx['scale']), 'price': exact(info['price'], ctx['scale'])})
    return rows, sheets


def dataset(source, spec):
    if spec['kind'] == 'depreciation':
        from . import depreciation_read, report_mf

        # Reuse the same date projection as the register, without filtering its
        # historical movements to the report period (the first handover matters).
        result = depreciation_read.read(source.cursor, source.ctx, source.params['end'], source.params.get('variant'))
        labels = {'invalid': 'Date de corectat', 'not_entered': 'Neintrată la data aleasă',
                  'not_in_use': 'Nepredat', 'active': 'În amortizare',
                  'fully_depreciated': 'Amortizat integral', 'written_off': 'Casat'}
        eligible = None
        if source.params.get('mf_selection'):
            eligible = {row['variant'] for row in report_mf.depreciation_options(source)
                        if report_mf.accepted(spec, row, source.params)}
        return [{**row, 'variant': row['id'], 'status': labels.get(row['status'], row['status']),
                 'issues_text': '; '.join(issue['message'] for issue in row.get('issues', []))}
                for row in result['assets'] if selected(row['id'], source.params, 'variant')
                and (eligible is None or row['id'] in eligible)]
    if spec.get('ledger') == 'custody' or spec.get('family') in ('bp', 'bt', 'bs'):
        from . import report_oi
        rows = report_oi.dataset(source, spec)
        if spec.get('mf_selection'):
            from . import report_mf
            rows = [row for row in rows if report_mf.accepted(spec, row, source.params)]
        return rows
    kind = spec['kind']
    if kind in ('balance', 'product', 'accounts', 'final', 'inventory', 'statement'):
        rows, statements = balances(source, kind in ('final', 'inventory'), hide_empty=kind == 'balance')
        if kind == 'accounts':
            rows = aggregate(rows, ('category', 'account'), ('opening_value', 'incoming_value', 'outgoing_value', 'closing_value'))
        if kind == 'statement':
            # Explicit opening row; the first movement's balance cannot stand in for SI.
            opening = [{**r, 'date': source.params['start'].isoformat(), 'number': 'SOLD INIȚIAL',
                        'incoming': '', 'outgoing': '', 'closing': r['opening']} for r in rows]
            rows = opening + statements
        return rows
    family = spec.get('family')
    if kind in ('documents', 'document_summary'):
        rows = documents(source, None if spec.get('include_storno') else family, include_returns=spec.get('include_returns', False))
        if spec.get('include_storno'):
            rows = [storno_display(row) if row['family'] == 'storno' else row for row in rows]
            rows.sort(key=lambda row: (row['date'], row['document']), reverse=True)
        if spec.get('include_returns') or spec.get('outgoing_selection'):
            rows.sort(key=lambda row: (row['date'], row['document']), reverse=True)
        if kind == 'document_summary':
            rows = aggregate(rows, ('document', 'category', 'account'), ('value',))
        return rows
    if kind in ('consumption', 'consumption_sheet'):
        rows = documents(source, 'bc')
        return aggregate(rows, ('receiver', 'category', 'product', 'variant', 'price', 'um')) if kind == 'consumption' else rows
    rows = documents(source)
    if kind == 'purchases':
        # Situatie/Plan source exports use Intrari only; the two referat sources
        # also include the signed storno correction.
        if spec['id'] in ('Plan_Achizitii', 'Situatie_Intrari', 'Nota_Comanda'):
            rows = [row for row in rows if row['family'] == 'nir']
        else:
            rows = [{**row, 'quantity': str(-Decimal(row['quantity'])) if row['family'] == 'storno' else row['quantity'],
                     'value': str(-Decimal(row['value'])) if row['family'] == 'storno' and row['value'] is not None else row['value']} for row in rows]
        keys = ('category', 'product', 'um', 'cpv')
        if spec['id'] in ('Nota_Comanda', 'Referat_periodic_Intrari'):
            keys += ('supplier', 'party')
        return aggregate(rows, keys)
    if kind in ('receipts_summary', 'suppliers'):
        rows = [{**row, 'value': str(-Decimal(row['value'])) if row['family'] == 'storno' and row['value'] is not None else row['value']} for row in rows]
        return aggregate(rows, ('supplier', 'party', 'fiscal_code', 'address') if kind == 'suppliers'
                         else ('category', 'account', 'supplier', 'party', 'document', 'family'), ('value',))
    raise NomError('REPORT_KIND', 'Sursă de raport necunoscută.', 422)
