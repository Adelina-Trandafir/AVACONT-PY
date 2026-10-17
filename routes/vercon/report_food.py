"""Lista zilnică: one list -> many BCs, three meal sections and two food columns."""
from decimal import Decimal
from . import food_balance, report_body, report_data, report_model, report_layouts, stock
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
    # This printed grid is filled with a pen, including when using an older layout.
    sections['portii'] = report_layouts.food_sections()['portii']
    # Apply the recap presentation to saved layouts as well as shipped defaults.
    # Only printed values are rounded; calculations retain the stored precision.
    decimals = {'allocation': 0, 'value': 2, 'economy': 2, 'overrun': 2}
    for name in ('drepturi', 'recapitulatie', 'portii'):
        sections[name] = {**sections[name], 'columns': [
            {**column, 'size': max(5, column.get('size', layout['font_size']) - 1),
             **({'decimals': decimals[column['key']]} if column['key'] in decimals else {})}
            for column in sections[name]['columns']]}
    sections['portii']['columns'] = [
        {**column, 'header_background': '#ececec', 'bold': True,
         'align': 'l' if column['key'] == 'meal' else 'c'}
        for column in sections['portii']['columns']]
    sections['feluri'] = {**sections['feluri'], 'show_column_header': False}
    sections['alimente'] = {**sections['alimente'], 'columns': [
        {**column, 'header_size': max(5, column.get('size', layout['font_size']) - 1),
         'size': max(5, column.get('size', layout['font_size']) - 2),
         **({'type': 'decimal', 'decimals': 2} if column['type'] in ('decimal', 'int') else {})}
        for column in sections['alimente']['columns']]}
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
    before = food_balance.before(source, day)
    current = None if head['DEP'] is None else -Decimal(head['DEP'])
    def difference(label, amount):
        return {'period': label, **food_balance.columns(amount, scale)}
    recap = [difference('De la începutul perioadei', before), difference('Pe ziua curentă', current),
             difference('La finele perioadei', None if current is None else before + current)]
    bons = [{**row, 'date': report_body.date(row['date']), 'value': report_data.exact(row['value'], scale)} for row in source.rows('food_bons')]
    if any(row['annulled'] for row in bons):
        raise NomError('REPORT_SOURCE', 'Lista este legată de un bon anulat.', 409)
    midpoint = (len(lines) + 1) // 2
    rights_table = section('drepturi', summary)
    footer_cells = rights_table['rows'][-1]['cells']
    rights_table['rows'][-1]['cells'] = [
        {'t': footer_cells[0], 'span': len(rights_table['cols']) - 1, 'background': '#ececec', 'bold': True},
        {'t': footer_cells[-1], 'background': '#ececec', 'bold': True}]
    recap_table = section('recapitulatie', recap)
    recap_table['rows'].append({'cells': [{'t': '', 'span': len(recap_table['cols'])}]})
    manual_meals = section('portii', [{'meal': label, 'md': '', 'd': '', 'c': ''}
                                    for label in ('Efectiv la hrană', 'Porții preparate', 'Rămase', 'Distribuite')], ())
    return [{**report_body.text(str(head['NumarLA']) + ' / ' + report_body.date(day)), 'align': 'c'},
        {'t': 'columns', 'children': [
            [rights_table], [recap_table], [manual_meals]], 'grid_width': .75},
        {'t': 'sign', 'heads': ['Aprobat,', 'Vizat compartiment financiar contabil,'], 'box_width_ratio': .5},
        {'t': 'columns', 'children': meal_columns, 'space_after': 4},
        {'t': 'group', 'text': 'ALIMENTE', 'space_after': 0, 'keep_with_next': True},
        {'t': 'columns', 'children': [[section('alimente', lines[:midpoint])], [section('alimente', lines[midpoint:])]],
         'footer': {'label': 'TOTAL ALIMENTE', 'value': report_model.number(report_model.total(lines, 'value'), 2),
                    'size': max(5, layout['font_size'] - 2)}},
        report_body.text(head.get('Comentarii') or ''), report_body.text('PRODUSELE CORESPUND D.P.D.V. ORGANOLEPTIC.'),
        {'t': 'sign', 'heads': ['Întocmit,', 'Confirmat,', 'Gestionar,', 'Bucătar,'], 'anchor': 'bottom'}]
