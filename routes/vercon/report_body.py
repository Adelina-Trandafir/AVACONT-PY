"""Grouped report bands, headers and signatures from editable layout data."""
from collections import OrderedDict
from . import report_model


def date(value):
    text = str(value or '')[:10]
    return '.'.join(reversed(text.split('-'))) if len(text) == 10 else text


def text(value):
    return {'t': 'text', 'text': value}


def table(layout, rows, zec, totals=None):
    columns = [(c['key'], c['label'], c['type'], int(c['width'] * 1000), c.get('decimals'))
               for c in layout['columns']]
    normalized = [{**row, **{c['key']: row.get(c['key'], '') for c in layout['columns']}} for row in rows]
    normalized = [{**row, **{c['key']: date(row.get(c['key'])) for c in layout['columns'] if c['type'] == 'date' or c['key'] == 'date'}} for row in normalized]
    amounts = list(layout.get('totals', [])) if totals is None else list(totals)
    if len({row.get('um') for row in rows}) > 1:
        amounts = [key for key in amounts if key not in ('quantity', 'opening', 'incoming', 'outgoing', 'closing')]
    block = report_model.table(columns, normalized, zec, amounts)
    for target, configured in zip(block['cols'], layout['columns']):
        for key in ('size', 'header_size', 'header_background', 'bold', 'align'):
            if key in configured:
                target[key] = configured[key]
    block['headers'] = layout.get('headers', [])
    block['width'] = layout.get('table_width', 1)
    if 'show_column_header' in layout:
        block['show_column_header'] = layout['show_column_header']
    return block


def doc_heading(spec, row, unit, committees):
    if spec['family'] in ('bp', 'bt', 'bs'):
        fields = [('Număr document / Dată', str(row['number']) + ' / ' + date(row['date']))]
        if spec['family'] == 'bp':
            fields += [('Cont magazie', row.get('warehouse_account') or ''), ('Cont în folosință', row.get('account') or ''),
                       ('Predător', unit.get('manager') or ''), ('Primitor', row.get('party') or '')]
        else:
            fields.append(('Predător', row.get('giver_name') or ''))
            if spec['family'] == 'bt':
                fields.append(('Primitor', row.get('party') or ''))
        fields.append(('Explicații', row.get('explanation') or ''))
        return [{'t': 'box', 'rows': fields}]
    if spec['family'] in ('nir', 'storno'):
        committee = committees.get(row.get('committee'), {})
        header = report_model.table([('fiscal_code', 'Cod furnizor', 'text', 1000), ('invoice', 'Nr. factură /\nAviz expediere', 'text', 1700),
            ('dispatch', 'Data expedierii', 'text', 1000), ('number', 'Nota recepție\nNumăr', 'text', 900),
            ('received', 'Data', 'text', 1000), ('order', 'Nr. comandă', 'text', 1000)],
            [{**row, 'number': abs(row['number']) if isinstance(row['number'], int) else row['number'], 'dispatch': date(row.get('movement_date')), 'received': date(row['date']), 'order': row.get('order') or ''}], 0)
        return [header, {'t': 'box', 'rows': [('Furnizor', row.get('party') or ''), ('Adresă', row.get('address') or '')]},
            text('Subsemnații, membrii comisiei de recepție, am procedat la recepționarea valorilor materiale furnizate de ' + (row.get('party') or '')),
            text('Documente însoțitoare: ' + (row.get('invoice') or '')),
            text('Constatându-se următoarele: ________________________________________'),
            text('Comisia: ' + ', '.join(str(committee.get('Receptie' + str(i)) or '') for i in range(1, 5)).strip(', ')),
            text('Categoria (contul): ' + str(row.get('account') or ''))]
    return [{'t': 'box', 'rows': [('Număr document / Dată', str(row['number']) + ' / ' + date(row['date'])),
              ('Produs, lucrare (comandă)', row.get('explanation') or ''),
              ('Predător', unit.get('manager') or ''), ('Primitor', row.get('party') or ''),
              ('Nr. comandă', ('BR ' if spec['family'] == 'br' else 'BC ') + str(row['number']))]}]


def reception_signatures(row, committees):
    committee = committees.get(row.get('committee'), {})
    members = [str(committee.get('Receptie' + str(index)) or '') for index in range(1, 5)]
    block = report_model.table([
        ('name1', 'NUME ȘI PRENUME', 'text', 1800), ('sign1', 'SEMNĂTURA', 'text', 1200),
        ('name2', 'NUME ȘI PRENUME', 'text', 1800), ('sign2', 'SEMNĂTURA', 'text', 1200),
        ('date', 'DATA PRIMIRII', 'text', 1200), ('holder_sign', 'SEMNĂTURA', 'text', 1200)],
        [{'name1': members[0], 'name2': members[2], 'sign1': '', 'sign2': '', 'date': '', 'holder_sign': ''},
         {'name1': members[1], 'name2': members[3], 'sign1': '', 'sign2': '', 'date': '', 'holder_sign': ''}], 0)
    block['headers'] = [[{'label': 'CERTIFICARE COMISIA DE RECEPȚIE', 'start': 0, 'span': 4},
                         {'label': 'Gestionar', 'start': 4, 'span': 2}]]
    return block


def statement_table(layout, rows, zec):
    block = table(layout, rows, zec, ())
    months, printed = OrderedDict(), []
    for row, record in zip(rows, block['rows']):
        if row.get('number') == 'SOLD INIȚIAL':
            printed.append(record)
            continue
        months.setdefault(str(row['date'])[:7], []).append((row, record))
    for month, items in months.items():
        printed.extend(record for _, record in items)
        source = [row for row, _ in items]
        totals = {key: report_model.total(source, key) for key in ('incoming', 'outgoing')}
        summary = {'number': 'Total luna ' + month, 'incoming': totals['incoming'], 'outgoing': totals['outgoing'],
                   'closing': source[-1]['closing'], 'closing_value': source[-1].get('closing_value', '')}
        record = table(layout, [summary], zec, ())['rows'][0]
        record['cls'] = 'total'
        printed.append(record)
    movements = [row for row in rows if row.get('number') != 'SOLD INIȚIAL']
    summary = {'number': 'TOTAL GENERAL', **{key: report_model.total(movements, key) for key in ('incoming', 'outgoing')},
               'closing': rows[-1]['closing'] if rows else '', 'closing_value': rows[-1].get('closing_value', '') if rows else ''}
    record = table(layout, [summary], zec, ())['rows'][0]
    record['cls'] = 'total'
    block['rows'] = printed + [record]
    return block


def build(spec, layout, rows, ctx, unit, committees=None):
    zec, blocks = ctx['zec'], []
    rows = [{**row, 'ordinal': index + 1} for index, row in enumerate(rows)]
    groups = layout.get('groups', [])
    if spec['kind'] in ('statement', 'custody_statement'):
        # Opening first, then chronological movements; subtotal at every month.
        rows.sort(key=lambda row: (row['variant'], row.get('date', ''), 0 if row.get('number') == 'SOLD INIȚIAL' else 1))
    def walk(items, level):
        if level == len(groups):
            blocks.append(statement_table(layout, items, zec) if spec['kind'] in ('statement', 'custody_statement') else table(layout, items, zec))
            return
        key = groups[level]
        buckets = OrderedDict()
        for row in items:
            # Group by identifiers where names can collide.
            identity = row.get('category') if key == 'account' else row.get('supplier') if key == 'party' and 'supplier' in row else row.get('receiver') if key == 'party' else row.get(key)
            buckets.setdefault((identity, row.get(key)), []).append(row)
        for (_, value), children in buckets.items():
            row = children[0]
            if key == 'document' and spec['kind'] == 'documents':
                if any(b['t'] == 'table' for b in blocks):
                    blocks.append({'t': 'break'})
                blocks.extend(doc_heading(spec, row, unit, committees or {}))
            elif key == 'variant':
                blocks.append({'t': 'box', 'rows': [('Categoria', row.get('account') or ''), ('Materialul (produsul)', row.get('name') or ''),
                    ('Codul produsului', row.get('code') or row['variant']), ('UM', row.get('um') or ''),
                    ('Preț unitar', report_model.number(row.get('price'), zec))]})
            else:
                blocks.append({'t': 'group', 'text': (('Categorie: ' if key == 'account' else '') + str(value or 'Fără încadrare'))})
            walk(children, level + 1)
            if level + 1 < len(groups):
                totals = [k for k in layout.get('totals', []) if k.endswith('_value') or k in ('value', 'vat')]
                if totals:
                    blocks.append(table(layout, children, zec, totals))
                    # A group total contains no repeated details.
                    blocks[-1]['rows'] = blocks[-1]['rows'][-1:]
            if key == 'document' and spec['kind'] == 'documents' and layout.get('signatures'):
                declared = row.get('header_value')
                if declared is not None and report_model.number(declared, zec) != report_model.number(report_model.total(children, 'value'), zec):
                    blocks.append(text('Valoare păstrată în antet: ' + report_model.number(declared, zec) + ' lei. Totalul tabelului reprezintă suma liniilor.'))
                standard_reception = spec['family'] in ('nir', 'storno') and layout['signatures'][0] == 'CERTIFICARE COMISIA DE RECEPȚIE'
                signature = reception_signatures(row, committees or {}) if standard_reception else {'t': 'sign', 'heads': layout['signatures']}
                blocks.extend([{'t': 'spacer'}, signature])
    walk(rows, 0)
    if rows and groups and spec['kind'] not in ('statement', 'custody_statement', 'documents') and layout.get('totals'):
        overall = table(layout, rows, zec)
        overall['rows'] = overall['rows'][-1:]
        blocks.extend([{'t': 'group', 'text': 'TOTAL GENERAL'}, overall])
    if not rows:
        blocks.append(text('Nu există înregistrări pentru selecția curentă.'))
    if spec['kind'] == 'purchases' and spec['id'].startswith('Referat_'):
        blocks.insert(0, text('PENTRU BUNA DESFĂȘURARE A ACTIVITĂȚII ÎN CADRUL UNITĂȚII ' + unit.get('name', '') + ', ESTE NECESAR A SE ACHIZIȚIONA URMĂTOARELE PRODUSE / SERVICII:'))
        blocks.append({'t': 'spacer', 'height': 90})
        blocks.append(text('MENȚIONEZ CĂ PRODUSELE / SERVICIILE / LUCRĂRILE DESCRISE MAI SUS SUNT CUPRINSE ÎN PLANUL ANUAL DE ACHIZIȚII AL UNITĂȚII.'))
        blocks.append(text('VALOARE ESTIMATĂ: ' + report_model.number(report_model.total(rows, 'value'), zec) + ' LEI CU TVA INCLUS.'))
    if spec['kind'] != 'documents' and layout.get('signatures'):
        blocks += [{'t': 'spacer'}, {'t': 'sign', 'heads': layout['signatures']}]
    if spec['id'] == 'BUConsum_F':
        from copy import deepcopy
        blocks += [{'t': 'group', 'text': '✂ — EXEMPLAR 2 —'}] + deepcopy(blocks)
    return blocks
