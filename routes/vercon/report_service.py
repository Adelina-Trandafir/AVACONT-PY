"""Build once; screen and exports share a short-lived, user-scoped snapshot."""
from datetime import date, datetime
from utils.clock import utc_now
from decimal import Decimal
import hashlib
import json
from . import report_balance, report_body, report_catalog, report_data, report_food, report_store, report_output, report_reception, report_identity
from .nomenclatoare import NomError
from reporting.snapshots import Snapshots as ReportSnapshots


def primitive(value):
    if isinstance(value, Decimal):
        return format(value, 'f')
    if isinstance(value, (date, datetime)):
        return value.isoformat()[:10]
    if isinstance(value, dict):
        return {key: primitive(item) for key, item in value.items()}
    if isinstance(value, list):
        return [primitive(item) for item in value]
    return value


def build(cursor, unit, ctx, name, params, gestiune_name, *, source=None, issuer=None):
    spec = report_catalog.spec_of(name, ctx['type'])
    if params.get('mf_selection') and any(item[0] != spec.get('mf_selection') for item in params['mf_selection']):
        raise NomError('REPORT_FILTER', 'Selecția MF nu corespunde raportului ales.', 422)
    if params.get('receipt_selection') and not spec.get('incoming_selection'):
        raise NomError('REPORT_FILTER', 'Raportul nu folosește selecția produselor pe furnizori.', 422)
    if params.get('outgoing_selection') and not (spec.get('outgoing_selection') or spec.get('outgoing_filters')):
        raise NomError('REPORT_FILTER', 'Raportul nu folosește selecția bonurilor pe conturi/primitori.', 422)
    unsupported = [key for key in ('document', 'variant', 'category', 'supplier', 'receiver')
                   if (params.get(key) is not None or params.get(key + '_ids') is not None) and key not in spec['filters']]
    if unsupported:
        raise NomError('REPORT_FILTER', 'Raportul nu folosește filtrele: ' + ', '.join(unsupported) + '.', 422)
    for field in ('document', 'variant', 'supplier'):
        if spec.get(field + '_required') and params.get(field) is None:
            raise NomError('REPORT_SELECTION', 'Raportul necesită o singură selecție: ' + field + '.', 422)
    if params['start'] > params['end'] or ctx['type'] not in ('OI', 'MF') and (params['start'].year != ctx['an'] or params['end'].year != ctx['an']):
        raise NomError('REPORT_PERIOD', 'Alegeți un interval în exercițiul curent.', 422)
    layout = report_store.read(unit, 'layout', spec['id'])
    identity = report_store.read(unit, 'unit')
    report_store.validate('layout', layout['value'])
    report_store.validate('unit', identity['value'])
    issuer = report_identity.read(cursor, unit, ctx, issuer=issuer)
    identity = {**identity, 'value': report_identity.configured(identity['value'], issuer, unit)}
    layout = {**layout, 'value': report_identity.display_definition(layout['value'], unit, issuer['name'])}
    if spec['id'] in ('Centralizator_Furnizori', 'nou_Furnizori', 'Bon_Consum', 'Centralizator_BC'):
        layout['value']['title'] = spec['label']
    layout['value']['elements'] = [e for e in layout['value'].get('elements', []) if e['band'] != 'header']
    source = source if source is not None else report_data.Source(cursor, unit, ctx, params)
    notes = []
    receptions = []
    if spec['kind'] == 'food':
        blocks = report_food.build(source, spec, layout['value'])
    else:
        rows = report_data.dataset(source, spec)
        if spec['kind'] == 'balance':
            rows = report_balance.prepare(rows, params)
            layout = {**layout, 'value': report_balance.display_layout(layout['value'], params)}
        if (params.get('document') is not None or params.get('document_ids') is not None) and not rows:
            raise NomError('NOT_FOUND', 'Documentul nu este disponibil în selecția raportului.', 404)
        committees = {row['IdComisie']: row for row in source.rows('committee')} if spec.get('family') in ('nir', 'storno') else {}
        if layout['value'].get('form') == 'nir-classic' and spec.get('family') in ('nir', 'storno'):
            receptions = report_reception.documents(rows, committees)
        blocks = report_body.build(spec, layout['value'], rows, ctx, identity['value'], committees,
                                   page_break_accounts=spec['kind'] == 'balance' and params.get('page_break_accounts', False))
        if spec['kind'] == 'depreciation':
            notes.append('Amortizare la data de sfârșit selectată, cu parametrii curenți din registru și prețul curent al variantei. '
                         'Calculul folosește întregul istoric; data de început nu limitează amortizarea cumulată. '
                         'Observațiile din tabel indică activele care necesită corectarea datelor.')
        if spec.get('ledger') == 'custody' or spec['kind'] in ('balance', 'product', 'accounts', 'final', 'inventory', 'statement'):
            notes.append('Stoc evaluat la prețul curent al variantei; documentele păstrează valorile proprii.')
        if (params.get('supplier') is not None or params.get('supplier_ids') is not None) and spec['kind'] == 'statement':
            notes.append('Furnizorul selectează variantele recepționate de la acesta. Fișa păstrează toate mișcările variantelor; consumurile nu au o alocare istorică pe furnizori.')
    title = layout['value']['title']
    period = ('La data ' + report_body.date(params['end']) if spec['kind'] == 'depreciation'
              else report_body.date(params['start']) + ' — ' + report_body.date(params['end']))
    values = {'UNITATE': identity['value']['name'], 'CUI': identity['value']['fiscal_code'],
              'ADRESA': identity['value']['address'], 'GESTIUNE': gestiune_name, 'SUBUNITATE': issuer['subunit_name'],
              'ANTET': identity['value']['header'], 'SUBSOL': identity['value']['footer'],
              'PERIOADA': period, 'RAPORT': title}
    def band(name):
        elements = [{**e, 'text': report_output.tokens(report_output.tokens(e['text'], values), values)}
                    for e in layout['value'].get('elements', []) if e['band'] == name]
        return [{'t': 'positioned', 'elements': elements}] if elements else []
    heading = [{'t': 'title', 'text': title}]
    if spec['id'] != 'ListaAlimente2015':
        heading.append({'t': 'text', 'text': period})
    model = {'title': title, 'report_name': spec['id'], 'layout': layout['value'], 'identity': identity['value'],
             'letterhead_version': report_identity.VERSION,
             'gestiune_name': gestiune_name, 'period_text': period,
             'filename': spec['id'].replace(' ', '_') + '_' + params['end'].isoformat(),
             'blocks': heading + band('before') + blocks + band('after'),
             'notes': notes, 'context': {key: ctx[key] for key in ('subunit', 'gestiune', 'exercitiu', 'type', 'scale', 'zec')},
             'versions': {'layout': layout['version'], 'unit': identity['version'], 'queries': source.versions},
             'generated_at': utc_now().isoformat(timespec='seconds')}
    if receptions:
        model['receptions'] = receptions
    model = primitive(model)
    model['data_hash'] = hashlib.sha256(json.dumps(model, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()
    return model


class Snapshots(ReportSnapshots):
    def __init__(self):
        super().__init__(report_store.ROOT / 'snapshots', NomError)
