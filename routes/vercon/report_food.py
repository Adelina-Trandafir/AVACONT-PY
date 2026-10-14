"""Lista zilnică: one list -> many BCs, three meal sections and two food columns."""
from decimal import Decimal
from . import report_body, report_data, report_model, report_layouts, stock
from .nomenclatoare import NomError

MEALS = (('md', 'Mic dejun', 'MicDejun', 'VMD'), ('d', 'Dejun', 'Dejun', 'VD'), ('c', 'Cină / Gustare', 'Cina', 'VC'))


def build(source, spec, layout):
    headers = source.rows('food_header')
    if len(headers) != 1:
        raise NomError('NOT_FOUND', 'Lista selectată nu există sau are un rezumat duplicat.', 409)
    head, ctx = headers[0], source.ctx
    if head['Anulat']:
        raise NomError('REPORT_CANCELLED', 'Lista selectată este anulată.', 409)
    day = stock.day_of(head['DATALA'])
    if not source.params['start'] <= day <= source.params['end']:
        raise NomError('REPORT_PERIOD', 'Lista selectată nu este în intervalul raportului.', 422)
    zec, scale = ctx['zec'], ctx['scale']
    sections = {**report_layouts.food_sections(), **layout.get('sections', {})}
    def section(name, rows, totals=None):
        return report_body.table(sections[name], rows, zec, totals)
    items = source.rows('food_items')
    if spec['id'] in ('MicDejun subreport', 'Dejun subreport', 'Cina subreport'):
        key = {'MicDejun subreport': 'md', 'Dejun subreport': 'd', 'Cina subreport': 'c'}[spec['id']]
        return [section('feluri', [r for r in items if r['meal'] == key])]
    metadata = source.metadata()
    lines = []
    for raw in source.rows('food_consumption'):
        if raw['variant'] not in metadata:
            raise NomError('REPORT_SOURCE', 'Un aliment nu are variantă în gestiunea curentă.', 409)
        lines.append({**metadata[raw['variant']], **raw, **{key: report_data.exact(raw[key], scale) for key in ('quantity', 'price', 'value')}})
    lines = report_data.aggregate(lines, ('variant', 'price', 'name', 'um'))
    if spec['id'] == 'BC subreport 2013':
        return [section('alimente', lines, ('value',))]
    summary, meal_columns = [], []
    for key, label, prefix, price in MEALS:
        summary.append({'meal': label, 'portions': head[prefix + '_P'],
                        'allocation': report_data.exact(head[price], scale),
                        'value': report_data.exact(head[prefix + '_V'], scale),
                        'distributed': head[prefix + '_D']})
        meal_columns.append([{'t': 'group', 'text': label}, section('feluri', [row for row in items if row['meal'] == key])])
    # Web DEP = list minus bons; Access QDEP uses bons minus list.
    prior = [row for row in source.rows('food_history') if stock.day_of(row['date']) < day]
    if any(row['difference'] is None for row in prior):
        raise NomError('REPORT_SOURCE', 'O listă anterioară nu are diferența păstrată în antet.', 409)
    initial = next((int(row['IntValue']) for row in source.rows('settings') if row['SettingKey'] == 'SI_LA'), 0)
    before = initial - sum(int(row['difference']) for row in prior)
    current = None if head['DEP'] is None else -int(head['DEP'])
    def difference(label, amount):
        return {'period': label, 'economy': report_data.exact(-amount, scale) if amount is not None and amount < 0 else '',
                'overrun': report_data.exact(amount, scale) if amount is not None and amount >= 0 else ''}
    recap = [difference('De la începutul perioadei', before), difference('Pe ziua curentă', current),
             difference('La finele perioadei', None if current is None else before + current)]
    bons = [{**row, 'date': report_body.date(row['date']), 'value': report_data.exact(row['value'], scale)} for row in source.rows('food_bons')]
    if any(row['annulled'] for row in bons):
        raise NomError('REPORT_SOURCE', 'Lista este legată de un bon anulat.', 409)
    midpoint = (len(lines) + 1) // 2
    return [report_body.text(str(head['NumarLA']) + ' / ' + report_body.date(day)),
        {'t': 'columns', 'children': [
            [section('drepturi', summary)], [section('recapitulatie', recap)], [section('portii', summary)]]},
        {'t': 'sign', 'heads': ['Aprobat,', 'Vizat compartiment financiar contabil,']},
        {'t': 'columns', 'children': meal_columns}, {'t': 'group', 'text': 'ALIMENTE'},
        {'t': 'columns', 'children': [[section('alimente', lines[:midpoint])], [section('alimente', lines[midpoint:])]]},
        report_body.text('TOTAL ALIMENTE: ' + report_model.number(report_model.total(lines, 'value'), zec) + ' lei'),
        {'t': 'group', 'text': 'Bonuri de consum asociate'},
        section('bonuri', bons),
        report_body.text(head.get('Comentarii') or ''), report_body.text('PRODUSELE CORESPUND D.P.D.V. ORGANOLEPTIC.'),
        {'t': 'sign', 'heads': ['Întocmit,', 'Confirmat,', 'Gestionar,', 'Bucătar,']}]
