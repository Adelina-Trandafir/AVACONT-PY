"""Grouped report bands, headers and signatures from editable layout data."""
from collections import OrderedDict
from decimal import Decimal, ROUND_CEILING, localcontext
from . import report_model, report_voucher


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


def doc_heading(spec, row, unit, committees, ctx, gestiune_name):
    if report_voucher.applies(spec):
        blocks = [report_voucher.header(spec, row, ctx, gestiune_name)]
        if row.get('explanation'):
            blocks.append({'t': 'text', 'text': 'Explicații: ' + str(row['explanation']), 'keep_with_next': True})
        return blocks
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
    record['cls'] = 'grand'
    block['rows'] = printed + ([record] if len(months) > 1 else [])
    return block


def build(spec, layout, rows, ctx, unit, committees=None, *, page_break_accounts=False, gestiune_name=''):
    zec, blocks = ctx['zec'], []
    outer_group_count = 0
    rows = [{**row, 'ordinal': index + 1} for index, row in enumerate(rows)]
    groups = layout.get('groups', [])
    if spec['kind'] in ('statement', 'custody_statement'):
        # Opening first, then chronological movements; subtotal at every month.
        rows.sort(key=lambda row: (row['variant'], row.get('date', ''), 0 if row.get('number') == 'SOLD INIȚIAL' else 1))
    def group_key(row, key):
        # Names alone cannot distinguish suppliers/accounts with identical labels.
        identity = row.get('category') if key == 'account' else row.get('supplier') if key == 'party' and 'supplier' in row else row.get('receiver') if key == 'party' else row.get(key)
        return identity, row.get(key)

    def walk(items, level):
        nonlocal outer_group_count
        if level == len(groups):
            blocks.append(statement_table(layout, items, zec) if spec['kind'] in ('statement', 'custody_statement') else table(layout, items, zec))
            return
        key = groups[level]
        buckets = OrderedDict()
        for row in items:
            buckets.setdefault(group_key(row, key), []).append(row)
        if level == 0:
            outer_group_count = len(buckets)
        for index, ((_, value), children) in enumerate(buckets.items()):
            row = children[0]
            if key == 'account' and page_break_accounts and index:
                blocks.append({'t': 'break', 'space_after': 0})
            if key == 'document' and spec['kind'] == 'documents':
                if any(b['t'] == 'table' for b in blocks):
                    blocks.append({'t': 'break'})
                document_spec = {**spec, 'family': row['family']} if spec.get('include_returns') else spec
                if spec.get('include_returns') and not report_voucher.applies(document_spec):
                    blocks.append({'t': 'group', 'text': 'BON DE RETUR' if row['family'] == 'br' else 'BON DE CONSUM'})
                blocks.extend(doc_heading(document_spec, row, unit, committees or {}, ctx, gestiune_name))
            elif key == 'variant':
                blocks.append({'t': 'box', 'rows': [('Categoria', row.get('account') or ''), ('Materialul (produsul)', row.get('name') or ''),
                    ('Codul produsului', row.get('code') or row['variant']), ('UM', row.get('um') or ''),
                    ('Preț unitar', report_model.number(row.get('price'), zec))]})
            else:
                blocks.append({'t': 'group', 'text': (('Categorie: ' if key == 'account' else '') + str(value or 'Fără încadrare'))})
            walk(children, level + 1)
            if level + 1 < len(groups):
                totals = [k for k in layout.get('totals', []) if k.endswith('_value') or k in ('value', 'vat')]
                child_groups = {group_key(child, groups[level + 1]) for child in children}
                if totals and len(child_groups) > 1:
                    # One child already carries this same total. Otherwise continue
                    # the last detail table, without another header/footer table.
                    summary = table(layout, children, zec, totals)['rows'][-1]
                    last_table = next(block for block in reversed(blocks) if block['t'] == 'table')
                    last_table['rows'].append(summary)
            if key == 'document' and spec['kind'] == 'documents' and layout.get('signatures'):
                declared = row.get('header_value')
                if declared is not None and report_model.number(declared, zec) != report_model.number(report_model.total(children, 'value'), zec):
                    blocks.append(text('Valoare păstrată în antet: ' + report_model.number(declared, zec) + ' lei. Totalul tabelului reprezintă suma liniilor.'))
                standard_reception = spec['family'] in ('nir', 'storno') and layout['signatures'][0] == 'CERTIFICARE COMISIA DE RECEPȚIE'
                signature = reception_signatures(row, committees or {}) if standard_reception else {'t': 'sign', 'heads': layout['signatures']}
                blocks.extend([{'t': 'spacer'}, signature])
    walk(rows, 0)
    if rows and outer_group_count > 1 and spec['kind'] not in ('statement', 'custody_statement', 'documents') and layout.get('totals'):
        overall = table(layout, rows, zec)
        summary = overall['rows'][-1]
        if summary.get('cls') == 'total':
            summary['cells'][0] = 'TOTAL GENERAL'
            summary['cls'] = 'grand'
            # Grand totals continue the existing columns without a separate band or header.
            last_table = next(block for block in reversed(blocks) if block['t'] == 'table')
            last_table['rows'].append(summary)
    if not rows:
        blocks.append(text('Nu există înregistrări pentru selecția curentă.'))
    if spec['kind'] == 'purchases' and spec['id'].startswith('Referat_'):
        blocks.insert(0, text('PENTRU BUNA DESFĂȘURARE A ACTIVITĂȚII ÎN CADRUL UNITĂȚII ' + unit.get('name', '') + ', ESTE NECESAR A SE ACHIZIȚIONA URMĂTOARELE PRODUSE / SERVICII:'))
        blocks.append({'t': 'spacer', 'height': 90})
        blocks.append(text('MENȚIONEZ CĂ PRODUSELE / SERVICIILE / LUCRĂRILE DESCRISE MAI SUS SUNT CUPRINSE ÎN PLANUL ANUAL DE ACHIZIȚII AL UNITĂȚII.'))
        estimated = report_model.total(rows, 'value')
        if estimated is not None:
            with localcontext() as context:
                context.prec = 60
                estimated = Decimal(estimated).quantize(Decimal('1E1'), rounding=ROUND_CEILING)
        blocks.append(text('VALOARE ESTIMATĂ: ' + report_model.number(estimated, 0) + ' LEI CU TVA INCLUS.'))
    if spec['kind'] != 'documents' and layout.get('signatures'):
        blocks += [{'t': 'spacer'}, {'t': 'sign', 'heads': layout['signatures']}]
    if spec['id'] == 'BUConsum_F':
        from copy import deepcopy
        blocks += [{'t': 'group', 'text': '✂ — EXEMPLAR 2 —'}] + deepcopy(blocks)
    return blocks
