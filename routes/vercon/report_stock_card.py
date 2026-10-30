"""SLICE-VC09-11: product-name links to a read-only warehouse card in the app.

Only rows identifying exactly one price variant get a link. URLs freeze the
report context and period; the existing stock endpoint rechecks access rights.
"""
from copy import deepcopy
from urllib.parse import urlencode


def without_links(model):
    """Export a copy without app-only actions; keep the cached preview intact."""
    built = deepcopy(model)
    built.pop('stock_card', None)

    def visit(blocks):
        for block in blocks:
            if block['t'] == 'columns':
                for child in block['children']:
                    visit(child)
            elif block['t'] == 'table':
                for row in block['rows']:
                    for cell in row['cells']:
                        if isinstance(cell, dict):
                            cell.pop('href', None)

    visit(built['blocks'])
    for document in built.get('receptions', []):
        for row in document['lines']:
            row.pop('stock_card_href', None)
    return built


def attach(model, ctx, params):
    context = {'subunit': ctx['subunit'], 'gestiune': ctx['gestiune'], 'year': ctx['an'],
               'start': params['start'].isoformat(), 'end': params['end'].isoformat(),
               'ledger': 'warehouse', 'report_card': '1'}
    variants = {}

    def link(row):
        variant = row.get('stock_variant', row.get('variant'))
        if not isinstance(variant, int) or isinstance(variant, bool) or variant <= 0 or not row.get('name'):
            return None
        variants[str(variant)] = {'name': row['name'], 'code': row.get('code'), 'um': row.get('um')}
        return '/api/vercon/stock/balance?' + urlencode({**context, 'variant': variant})

    def visit(blocks):
        for block in blocks:
            if block['t'] == 'columns':
                for child in block['children']:
                    visit(child)
            if block['t'] != 'table':
                continue
            name_columns = {index for index, column in enumerate(block['cols']) if column.get('key') == 'name'}
            if not name_columns:
                continue
            raw = block.get('raw', [])
            for index, printed in enumerate(block['rows']):
                if index >= len(raw) or printed.get('cls') in ('total', 'grand', 'cancelled'):
                    continue
                href = link(raw[index])
                if not href:
                    continue
                column_index = 0
                for cell_index, cell in enumerate(printed['cells']):
                    span = cell.get('span', 1) if isinstance(cell, dict) else 1
                    if column_index in name_columns and span == 1:
                        printed['cells'][cell_index] = {**cell, 'href': href} if isinstance(cell, dict) else {'t': cell, 'href': href}
                    column_index += span

    visit(model['blocks'])
    # NIR/FCS use their own PDF form renderer instead of the shared table renderer.
    for document in model.get('receptions', []):
        for row in document['lines']:
            href = link(row)
            if href:
                row['stock_card_href'] = href
    if variants:
        model['stock_card'] = {'context': context, 'variants': variants}
