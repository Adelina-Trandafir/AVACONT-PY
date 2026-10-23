"""Shared lazy OI/MF trees, with the same identities as report generation."""
from urllib.parse import quote
import re
from . import report_mf, report_model, report_oi
from .nomenclatoare import NomError
from .report_order import document_order


def read(source, spec, node_id, *, recursive=False):
    mode = spec['mf_selection']
    dimensions = {'bons': ('receiver', 'document'), 'balance': ('category',),
                  'inventory': ('category', 'receiver'),
                  'depreciation': ('receiver' if source.params.get('group_by_receivers') else 'category', 'classification')}[mode]
    if spec.get('tree_group') == 'PRIMITORI':
        dimensions = ('receiver', 'category')
    path = []
    if node_id != 'root':
        parts = node_id.split(':')
        if len(parts) < 3 or parts[:2] != ['mf-branch', mode] or len(parts) - 2 > len(dimensions):
            raise NomError('REPORT_BRANCH', 'Ramură OI / MF invalidă.', 422)
        path = parts[2:]

    def token(value):
        return 'none' if value is None or value == '' else quote(str(value), safe='')

    if mode == 'depreciation':
        rows = report_mf.depreciation_options(source)
    elif mode == 'bons':
        rows = report_oi.documents(source, spec)
    else:
        rows = report_oi.balances(source, spec)
    for field, value in zip(dimensions, path):
        rows = [row for row in rows if token(row.get(field)) == value]

    details = {}

    def products(items):
        nodes = {}
        for row in items:
            node_id = report_mf.item_id(spec, row)
            label = (row['name'] or '') + (' / ' + str(row['code']) if row.get('code') else '')
            label += ' / ' + row['um'] if row.get('um') else ''
            nodes.setdefault(node_id, {'id': node_id, 'label': label})
        return sorted(nodes.values(), key=lambda node: (node['label'].casefold(), node['id']))

    def children(items, parent, expand):
        if len(parent) == len(dimensions):
            return products(items)
        field = dimensions[len(parent)]
        groups = {}
        for row in items:
            groups.setdefault(token(row.get(field)), []).append(row)
        nodes = []
        for value, group in groups.items():
            row = group[0]
            if field == 'document':
                title = {'bp': 'Bon Predare', 'bt': 'Bon Transfer', 'bs': 'Bon Casare'}[spec['family']]
                date = row['date'].split('-')
                label = f"{title} {row['number']} / {'.'.join(reversed(date))} / "
                label += report_model.number(report_model.total(group, 'value'), source.ctx['zec']) + ' lei'
                details[row['document']] = {'id': row['document'], 'label': label}
            elif field == 'classification':
                label = str(row.get('classification') or 'Fără clasificare')
                if row.get('classification_name'):
                    label += ' / ' + row['classification_name']
            else:
                label = (row.get('account') or 'Fără cont') if field == 'category' else row.get('party') or 'Fără primitor'
            current = [*parent, value]
            node = {'id': 'mf-branch:' + mode + ':' + ':'.join(current), 'label': label, 'lazy': True}
            if field == 'document':
                node.update(document_order(row['date'], row['number'], row['document']))
            if expand:
                node.pop('lazy')
                node['children'] = children(group, current, True)
            nodes.append(node)
        if field == 'document':
            def number_key(value):
                return tuple((1, int(part)) if part.isdecimal() else (0, part.casefold())
                             for part in re.split(r'(\d+)', str(value or '')))
            dates = {token(row['document']): (row['date'], number_key(row['number']), row['document']) for row in items}
            nodes.sort(key=lambda node: dates[node['id'].split(':')[-1]], reverse=True)
        else:
            nodes.sort(key=lambda node: (node['label'].casefold(), node['id']))
        return nodes

    result = {'nodes': children(rows, path, recursive), 'documents': list(details.values())}
    if node_id == 'root':
        result['spec'] = spec
    return result
