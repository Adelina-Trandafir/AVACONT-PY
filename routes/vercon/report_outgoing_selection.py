"""Lazy account/document/product and receiver/document trees for Ieșiri."""
from . import stock
from .nomenclatoare import NomError
from .report_order import document_order


def key(value):
    return 'none' if value is None else str(value)


def branch(node_id, label):
    return {'id': node_id, 'label': label, 'lazy': True}


def document_label(row):
    family = 'BON RETUR' if row['family'] == 'br' else 'BON CONSUM'
    return f"{family} {row['number']} / {stock.day_of(row['date']).strftime('%d.%m.%Y')}"


def read(source, spec, node_id, *, recursive=False, mode='accounts'):
    summary = spec['id'] == 'Centralizator_BC'
    if mode not in ('accounts', 'documents', 'receivers') or mode == 'documents' and summary or mode == 'receivers' and not summary:
        raise NomError('REPORT_BRANCH', 'Modul de selecție nu este disponibil pentru acest raport.', 422)
    parts = node_id.split(':')
    if node_id != 'root':
        valid = len(parts) == 2 and parts[0] in ('out-account', 'out-receiver')
        valid = valid or len(parts) == 4 and parts[0] == 'out-document' and parts[1] in ('account', 'all')
        if not valid or parts[0] == 'out-receiver' and not summary or parts[0] == 'out-document' and summary:
            raise NomError('REPORT_BRANCH', 'Ramură de ieșiri invalidă.', 422)
        identifiers = parts[1:] if len(parts) == 2 else parts[2:]
        if any(value != 'none' and (not value.isdecimal() or int(value) <= 0) for value in identifiers):
            raise NomError('REPORT_BRANCH', 'Identificator de ieșiri invalid.', 422)
    rows = [row for row in source.rows('consumption') if not row['annulled']
            and (not summary or row['family'] == spec['family'])
            and row['date'] is not None and source.params['start'] <= stock.day_of(row['date']) <= source.params['end']]

    def metadata(items):
        catalog = source.metadata()
        result = []
        for row in items:
            info = catalog.get(row['variant'])
            if info is None:
                raise NomError('REPORT_SOURCE', 'Linia de bon nu are o variantă în contextul raportului.', 409)
            result.append({**info, **row})
        return result

    def products(items, scope, scope_id, document):
        grouped = {}
        for row in items:
            product = row['product']
            grouped.setdefault(product, {'id': f'outgoing-item:{scope}:{scope_id}:{document}:{product}',
                                         'label': (row['name'] or '') + (' / ' + row['um'] if row['um'] else '')})
        return sorted(grouped.values(), key=lambda node: (node['label'].casefold(), node['id']))

    def documents(items, scope, scope_id, recursive):
        grouped = {}
        for row in items:
            grouped.setdefault(row['document'], []).append(row)
        nodes, details = [], []
        for document, lines in sorted(grouped.items(), key=lambda item: (str(item[1][0]['date']), str(item[0])), reverse=True):
            label = document_label(lines[0])
            details.append({'id': document, 'label': label})
            if summary:
                node = {'id': f'outgoing-item:{scope}:{scope_id}:{document}:all', 'label': label}
            else:
                node = branch(f'out-document:{scope}:{scope_id}:{document}', label)
                if recursive:
                    node.pop('lazy')
                    node['children'] = products(lines, scope, scope_id, document)
            node.update(document_order(lines[0]['date'], lines[0]['number'], document))
            nodes.append(node)
        return {'nodes': nodes, 'documents': details}

    if node_id == 'root' and mode == 'documents':
        return documents(rows, 'all', 'none', False)
    if node_id == 'root' and mode == 'receivers':
        receivers = {}
        for row in rows:
            receiver = key(row.get('receiver'))
            receivers.setdefault(receiver, branch('out-receiver:' + receiver, row.get('party') or 'Fără primitor'))
        return {'nodes': sorted(receivers.values(), key=lambda node: (node['label'].casefold(), node['id'])), 'documents': []}
    if parts[0] == 'out-receiver':
        return documents([row for row in rows if key(row.get('receiver')) == parts[1]], 'receiver', parts[1], False)
    rows = metadata(rows)
    if node_id == 'root':
        accounts = {}
        for row in rows:
            category = key(row.get('category'))
            accounts.setdefault(category, branch('out-account:' + category, row.get('account') or 'Fără încadrare'))
        return {'nodes': sorted(accounts.values(), key=lambda node: (node['label'].casefold(), node['id'])), 'documents': []}
    if parts[0] == 'out-account':
        return documents([row for row in rows if key(row.get('category')) == parts[1]], 'account', parts[1], recursive)
    scope, scope_id, document = parts[1:]
    rows = [row for row in rows if str(row['document']) == document
            and (scope == 'all' or key(row.get('category')) == scope_id)]
    return {'nodes': products(rows, scope, scope_id, document)}
