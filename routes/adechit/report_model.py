"""Translate ADE evidence blocks to the shared VERCON report contract."""
from copy import deepcopy
from reporting.excel_widths import column_key

TINTS = {'d': '#f9eded', 'c': '#e6edd7', 'z': '#dfe5ed'}


def shared_model(model, chosen=None):
    from .report_excel import table_schemas
    schemas = table_schemas(model)
    built = deepcopy(model)
    blocks = []
    for block in built['blocks']:
        kind = block['t']
        if kind == 'letterhead':
            elements = [{'text': text, 'x': 0, 'y': index * 13, 'w': .6, 'h': 13, 'size': 9}
                        for index, text in enumerate(block['left'])]
            if block.get('right'):
                elements.append({'text': block['right'], 'x': .6, 'y': 0, 'w': .4, 'h': 13, 'size': 11})
            blocks.append({'t': 'positioned', 'elements': elements, 'full_width': True})
            continue
        if kind == 'title':
            block.update(align='c', full_width=True)
            blocks.append(block)
            blocks.extend({'t': 'text', 'text': text, 'align': 'c', 'full_width': True} for text in block.pop('sub', []))
            continue
        if kind == 'box' and block.get('title'):
            blocks.append({'t': 'group', 'text': block.pop('title')})
        if kind == 'text':
            block['full_width'] = True
            block['align'] = {'center': 'c', 'right': 'r', 'left': 'l'}.get(block.get('align'), block.get('align', 'l'))
        if kind == 'table':
            columns = block['cols']
            schema = schemas.index(tuple(column['label'] for column in columns))
            kept = [index for index in range(len(columns)) if chosen is None or f'{schema}:{index}' in chosen]
            if not kept:
                continue
            block['show_column_header'] = block.get('head', True)
            if block.get('payment_notice'):
                block['grid_width'] = .7
            for index, column in enumerate(columns):
                column['key'] = column_key(column, index)
                column['numeric'] = column.get('align') == 'r'
                if column.get('tint'):
                    column['header_background'] = TINTS[column['tint']]
                if block.get('payment_notice') and index == len(columns) - 1:
                    column['bold'] = True
            for row in block['rows']:
                source, cells = 0, []
                for cell in row['cells']:
                    value = cell['t'] if isinstance(cell, dict) else cell
                    span = cell.get('span', 1) if isinstance(cell, dict) else 1
                    overlap = [index for index in kept if source <= index < source + span]
                    if overlap:
                        item = {'t': value, 'span': len(overlap)}
                        if columns[source].get('tint'):
                            item['background'] = TINTS[columns[source]['tint']]
                        cells.append(item)
                    source += span
                row['cells'] = cells
                if row.get('cls') == 'sub':
                    row['cls'] = 'total'
            if block.get('formula'):
                block['formula'] = [block['formula'][index] for index in kept]
            block['cols'] = [columns[index] for index in kept]
        blocks.append(block)
    footer = model.get('footer') or ''
    built.update(blocks=blocks, layout={'landscape': model['landscape'], 'font_size': model.get('font', 8),
                                     'title_size': 14, 'elements': []},
                 identity={'name': '', 'fiscal_code': '', 'address': '', 'header': '', 'footer': footer,
                           'elements': ([{'band': 'footer', 'text': footer, 'x': 0, 'y': 0, 'w': .8, 'h': 12, 'size': 7},
                                         {'band': 'footer', 'text': 'Pagina: {PAGINA}', 'x': .8, 'y': 0,
                                          'w': .2, 'h': 12, 'size': 7}] if footer else [])},
                 gestiune_name='', period_text='')
    return built
