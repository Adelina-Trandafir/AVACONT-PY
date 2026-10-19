"""Lazy supplier trees used only by Magazie / Intrări reports."""
from . import stock
from .nomenclatoare import NomError


def key(value):
    return 'none' if value is None else str(value)


def branch(node_id, label):
    return {'id': node_id, 'label': label, 'lazy': True}


def read(source, spec, node_id, *, recursive=False):
    nir = spec['id'] == 'NIR'
    supplier_prefix = 'nir-supplier' if nir else 'receipt-supplier'
    parts = node_id.split(':')
    expected = {supplier_prefix: 2, 'receipt-account': 3, 'receipt-document': 3}
    if node_id != 'root':
        if parts[0] not in expected or len(parts) != expected[parts[0]]:
            raise NomError('REPORT_BRANCH', 'Ramură de intrări invalidă.', 422)
        if parts[0] == ('receipt-account' if nir else 'receipt-document'):
            raise NomError('REPORT_BRANCH', 'Ramura nu este disponibilă pentru acest raport.', 422)
        if any(part != 'none' and (not part.isdecimal() or int(part) <= 0) for part in parts[1:]):
            raise NomError('REPORT_BRANCH', 'Identificator de intrări invalid.', 422)
    rows = []
    for row in source.rows('receipts'):
        day = row.get('movement_date') or row['date']
        if row['annulled'] or day is None or not source.params['start'] <= stock.day_of(day) <= source.params['end']:
            continue
        if node_id != 'root' and key(row.get('supplier')) != parts[1]:
            continue
        rows.append(row)

    def products(items, supplier, category=None, document=None):
        grouped = {}
        for row in items:
            identity = row['product']
            prefix = f'receipt-document-product:{supplier}:{document}' if document is not None else f'receipt-product:{supplier}:{category}'
            grouped.setdefault(identity, {'id': f'{prefix}:{identity}',
                                         'label': (row['name'] or '') + (' / ' + row['um'] if row['um'] else '')})
        return sorted(grouped.values(), key=lambda node: (node['label'].casefold(), node['id']))

    def children(items, supplier, recursive):
        if nir:
            documents = {}
            for row in items:
                documents.setdefault(row['document'], []).append(row)
            nodes = []
            for document, lines in sorted(documents.items(), key=lambda item: (str(item[1][0]['date']), str(item[0])), reverse=True):
                row = lines[0]
                label = f"{row['family'].upper()} {row['number']} / {row['date']}"
                node = branch(f'receipt-document:{supplier}:{document}', label)
                if recursive:
                    node.pop('lazy')
                    node['children'] = products(lines, supplier, document=document)
                nodes.append(node)
            return nodes
        categories = {}
        for row in items:
            categories.setdefault(row['category'], []).append(row)
        nodes = []
        for category, lines in categories.items():
            node = branch(f'receipt-account:{supplier}:{key(category)}', lines[0]['account'] or 'Fără încadrare')
            if recursive:
                node.pop('lazy')
                node['children'] = products(lines, supplier, category=key(category))
            nodes.append(node)
        return sorted(nodes, key=lambda node: (node['label'].casefold(), node['id']))

    # Supplier roots need no product metadata or loaded descendants.
    if node_id == 'root':
        suppliers = {}
        for row in rows:
            supplier = key(row.get('supplier'))
            suppliers.setdefault(supplier, branch(f'{supplier_prefix}:{supplier}', row.get('party') or 'Fără furnizor'))
        return {'nodes': sorted(suppliers.values(), key=lambda node: (node['label'].casefold(), node['id'])), 'documents': []}
    if nir and parts[0] == supplier_prefix and not recursive:
        return {'nodes': children(rows, parts[1], False)}
    metadata = source.metadata()
    joined = []
    for row in rows:
        info = metadata.get(row['variant'])
        if info is None:
            raise NomError('REPORT_SOURCE', 'Linia de intrare nu are o variantă în contextul raportului.', 409)
        joined.append({**info, **row})
    supplier = parts[1]
    if parts[0] == 'receipt-account':
        return {'nodes': products([row for row in joined if key(row['category']) == parts[2]], supplier, category=parts[2])}
    if parts[0] == 'receipt-document':
        return {'nodes': products([row for row in joined if str(row['document']) == parts[2]], supplier, document=parts[2])}
    return {'nodes': children(joined, supplier, recursive)}
