"""Lazy report filter branches, read through the existing scoped SELECT sources."""
from decimal import Decimal
from utils import vercon_suppliers as suppliers
from . import stock, report_incoming_selection, report_outgoing_selection, report_mf_selection
from .nomenclatoare import NomError
from .report_order import document_order


def account_filter(spec):
    return spec.get('ledger') == 'custody' or spec.get('family') in ('bp', 'bt', 'bs')


def roots(spec):
    nodes = []
    if account_filter(spec) and 'category' in spec['filters']:
        nodes.append(branch('selection:use-accounts', 'Conturi în folosință'))
    if 'variant' in spec['filters']:
        nodes.append(branch('selection:products', 'Produse'))
    for key, label in [('receiver', 'Primitori'), ('document', 'Documente')]:
        if key not in spec['filters']:
            continue
        if key == 'receiver':
            label = 'Predători' if spec.get('family') == 'bs' else 'Deținători' if spec.get('ledger') == 'custody' else label
        nodes.append(branch('selection:' + key, label))
    return nodes


def branch(node_id, label):
    return {'id': node_id, 'label': label, 'lazy': True}


def options(items, prefix):
    return [{'id': f'{prefix}:{key}', 'label': label} for key, label in items.items() if key is not None]


def product_nodes(source, node_id, recursive):
    parts = node_id.split(':')
    filters = {}
    if parts[0] in ('category', 'product'):
        if len(parts) != (2 if parts[0] == 'category' else 3):
            raise NomError('REPORT_BRANCH', 'Ramură de produse invalidă.', 422)
        try:
            filters['category'] = None if parts[1] == 'none' else int(parts[1])
            if parts[0] == 'product':
                filters['product'] = int(parts[2])
        except ValueError:
            raise NomError('REPORT_BRANCH', 'Ramură de produse invalidă.', 422) from None
    rows = list(source.metadata(result_filters=filters).values())

    def variants(items):
        return [{'id': f"variant:{row['variant']}", 'sortType': 'number', 'sortValue': str(row['price']) if row['price'] is not None else None,
                 'sortThen': [{'type': 'number', 'value': row['variant']}], 'label': str(row['code'] or row['variant']) + ' / '
                 + (stock.quantity_text(Decimal(row['price']), source.ctx['scale']) if row['price'] is not None else '—') + ' lei'}
                for row in items]

    def products(items, category):
        grouped = {}
        for row in items:
            grouped.setdefault(row['product'], []).append(row)
        nodes = []
        for product, group in grouped.items():
            row = group[0]
            node = branch(f'product:{category}:{product}', (row['name'] or '') + (' / ' + row['um'] if row['um'] else ''))
            if recursive:
                node.pop('lazy')
                node['children'] = variants(group)
            nodes.append(node)
        return nodes

    if parts[0] == 'product':
        return variants(rows)
    if parts[0] == 'category':
        return products(rows, parts[1])
    grouped = {}
    for row in rows:
        grouped.setdefault(row['category'], []).append(row)
    nodes = []
    for category, group in grouped.items():
        key = str(category) if category is not None else 'none'
        node = branch(f'category:{key}', group[0]['account'] or 'Fără încadrare')
        if recursive:
            node.pop('lazy')
            node['children'] = products(group, key)
        nodes.append(node)
    return nodes


def document_suppliers(source, node_id):
    supplier_key = None
    if node_id.startswith('document-supplier:'):
        supplier_key = node_id.split(':', 1)[1]
        if supplier_key != 'none' and (suppliers.code(supplier_key) != supplier_key or not 0 < len(supplier_key) <= 50):
            raise NomError('REPORT_BRANCH', 'Furnizor invalid.', 422)
    groups, documents, ordering = {}, {}, {}
    for row in source.rows('receipts'):
        day = row.get('movement_date') or row['date']
        if row['annulled'] or day is None or not source.params['start'] <= stock.day_of(day) <= source.params['end']:
            continue
        supplier = str(row['supplier']) if row.get('supplier') is not None else 'none'
        if supplier_key is not None and supplier != supplier_key:
            continue
        groups.setdefault(supplier, branch('document-supplier:' + supplier, row.get('party') or supplier if row.get('supplier') else 'Fără cod fiscal'))
        if supplier_key is not None:
            documents[row['document']] = f"{row['family'].upper()} {row['number']} / {day} / {row.get('party') or ''}"
            ordering[f"document:{row['document']}"] = document_order(day, row['number'], row['document'])
    if supplier_key is None:
        return {'nodes': sorted(groups.values(), key=lambda group: (suppliers.name_key(group['label']), group['id'])), 'documents': []}
    return {'nodes': [{**node, **ordering[node['id']]} for node in options(documents, 'document')],
            'documents': [{'id': item, 'label': label} for item, label in documents.items() if item is not None]}


def selection_branch(source, spec, node_id, *, recursive=False, documents=False, receivers=False):
    if spec.get('mf_selection'):
        return report_mf_selection.read(source, spec, node_id, recursive=recursive)
    if spec.get('outgoing_selection'):
        mode = 'receivers' if receivers else 'documents' if documents else 'accounts'
        return report_outgoing_selection.read(source, spec, node_id, recursive=recursive, mode=mode)
    allowed = {node['id'] for node in roots(spec)}
    if node_id.startswith('document-supplier:') or node_id == 'selection:document' and spec.get('document_options'):
        if not spec.get('document_options') or 'selection:document' not in allowed:
            raise NomError('REPORT_BRANCH', 'Ramura nu este disponibilă pentru acest raport.', 422)
        return document_suppliers(source, node_id)
    if node_id == 'root':
        if documents:
            if not spec.get('document_options'):
                raise NomError('REPORT_BRANCH', 'Modul documente nu este disponibil pentru acest raport.', 422)
            return {'spec': spec, **document_suppliers(source, 'selection:document')}
        if spec.get('incoming_selection'):
            return {'spec': spec, **report_incoming_selection.read(source, spec, node_id)}
        # Accounts are visible immediately; their products and variants stay lazy.
        nodes = []
        for node in roots(spec):
            if node['id'] == 'selection:document' and spec.get('document_options'):
                continue
            if node['id'] == 'selection:products':
                nodes.extend(product_nodes(source, node['id'], False))
            else:
                nodes.append(node)
        return {'spec': spec, 'nodes': nodes, 'documents': []}
    if node_id.startswith(('receipt-supplier:', 'nir-supplier:', 'receipt-account:', 'receipt-document:')):
        if not spec.get('incoming_selection'):
            raise NomError('REPORT_BRANCH', 'Ramura nu este disponibilă pentru acest raport.', 422)
        return report_incoming_selection.read(source, spec, node_id, recursive=recursive)
    if node_id.startswith(('category:', 'product:')):
        if 'selection:products' not in allowed:
            raise NomError('REPORT_BRANCH', 'Filtrul de produse nu este disponibil.', 422)
        return {'nodes': product_nodes(source, node_id, recursive)}
    if node_id not in allowed:
        raise NomError('REPORT_BRANCH', 'Ramura nu este disponibilă pentru acest raport.', 422)
    if node_id == 'selection:products':
        return {'nodes': product_nodes(source, node_id, recursive)}
    if node_id == 'selection:use-accounts':
        return {'nodes': options({row['category']: row['account'] for row in source.rows('oi_accounts')}, 'category-filter')}
    if node_id == 'selection:receiver' and account_filter(spec):
        return {'nodes': options({row['receiver']: row['party'] for row in source.rows('oi_holders')}, 'receiver')}
    items, ordering = {}, {}
    key = node_id.split(':', 1)[1]
    if spec['kind'] == 'food':
        for row in source.rows('food_history'):
            day = stock.day_of(row['date'])
            if day is not None and source.params['start'] <= day <= source.params['end']:
                items[row['IdLista']] = f"{row['NumarLA']} / {row['date']}"
                ordering[f"document:{row['IdLista']}"] = document_order(row['date'], row['NumarLA'], row['IdLista'])
    else:
        query = 'oi_documents' if account_filter(spec) else 'consumption' if spec.get('family') in ('bc', 'br') or spec['kind'].startswith('consumption') else 'receipts'
        for row in source.rows(query):
            if row['annulled'] or spec.get('family') and row['family'] != spec['family']:
                continue
            if key in ('supplier', 'receiver'):
                if row.get(key) is not None:
                    items[row[key]] = row['party'] or str(row[key])
            else:
                day = row.get('movement_date') or row['date']
                if day is not None and source.params['start'] <= stock.day_of(day) <= source.params['end']:
                    items[row['document']] = f"{row['family'].upper()} {row['number']} / {day} / {row.get('party') or ''}"
                    ordering[f"document:{row['document']}"] = document_order(day, row['number'], row['document'])
    result = {'nodes': [{**node, **ordering.get(node['id'], {})} for node in options(items, key)]}
    if key == 'document':
        result['documents'] = [{'id': item, 'label': label} for item, label in items.items() if item is not None]
    return result
