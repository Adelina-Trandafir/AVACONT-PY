"""Report catalog, filters, snapshots and exact exports for all VERCON types."""
from decimal import Decimal
from flask import Response, current_app, g, request
from . import report_catalog, report_data, report_output, report_service, report_excel, report_period, report_selection, protocol, stock
from .nomenclatoare import NomError
from . import report_identity
from . import report_mf
from .stock_routes import optional_id


def selected_ids(key):
    raw = request.args.get(key + '_ids')
    if raw is None:
        return None
    try:
        parts = raw.split(',')
        if not 1 <= len(parts) <= 1000 or any(not part.isdecimal() for part in parts):
            raise ValueError()
        values = list(dict.fromkeys(int(part) for part in parts))
        if any(value <= 0 for value in values):
            raise ValueError()
        return values
    except (ValueError, TypeError):
        raise NomError('INVALID', 'Selecția %s este invalidă.' % key, 422) from None


def selected_receipts():
    raw = request.args.get('receipt_selection')
    if raw is None:
        return None
    try:
        items = raw.split(',')
        if not 1 <= len(items) <= 1000:
            raise ValueError()
        chosen = set()
        for item in items:
            parts = item.split(':')
            if len(parts) != 4 or parts[0] not in ('receipt-product', 'receipt-document-product'):
                raise ValueError()
            values = []
            for index, value in enumerate(parts[1:]):
                if value == 'none' and (index == 0 or index == 1 and parts[0] == 'receipt-product'):
                    values.append(None)
                elif value.isdecimal() and int(value) > 0:
                    values.append(int(value))
                else:
                    raise ValueError()
            chosen.add((parts[0], *values))
        return chosen
    except (ValueError, TypeError):
        raise NomError('INVALID', 'Selecția produselor de la furnizori este invalidă.', 422) from None


def selected_outgoing():
    raw = request.args.get('outgoing_selection')
    if raw is None:
        return None
    try:
        items = raw.split(',')
        if not 1 <= len(items) <= 1000:
            raise ValueError()
        chosen = set()
        for item in items:
            parts = item.split(':')
            if len(parts) != 5 or parts[0] != 'outgoing-item' or parts[1] not in ('account', 'receiver', 'all'):
                raise ValueError()
            scope, scope_id, document, product = parts[1:]
            if scope_id != 'none' and (not scope_id.isdecimal() or int(scope_id) <= 0):
                raise ValueError()
            if scope == 'all' and scope_id != 'none':
                raise ValueError()
            if not document.isdecimal() or int(document) <= 0:
                raise ValueError()
            if product != 'all' and (not product.isdecimal() or int(product) <= 0):
                raise ValueError()
            chosen.add((scope, None if scope_id == 'none' else int(scope_id), int(document), None if product == 'all' else int(product)))
        return chosen
    except (ValueError, TypeError):
        raise NomError('INVALID', 'Selecția bonurilor este invalidă.', 422) from None


def register(bp, guard, scoped, session, build):
    def selection():
        params = {'start': protocol.to_date(request.args.get('start'), 'De la'),
                  'end': protocol.to_date(request.args.get('end'), 'Până la')}
        if params['start'] > params['end']:
            raise NomError('REPORT_PERIOD', 'Data de început trebuie să fie înaintea datei de sfârșit.', 422)
        params['receipt_selection'] = selected_receipts()
        params['outgoing_selection'] = selected_outgoing()
        params['mf_selection'] = report_mf.parse_selection(request.args.get('mf_selection'))
        for key in ('document', 'variant', 'category', 'supplier', 'receiver'):
            params[key] = optional_id(key)
            values = selected_ids(key)
            if values is not None:
                if params[key] is not None:
                    raise NomError('REPORT_FILTER', 'Folosiți o singură formă de selecție pentru ' + key + '.', 422)
                if len(values) == 1:
                    params[key] = values[0]
                else:
                    params[key + '_ids'] = values
        for key in ('show_without_movements', 'page_break_accounts', 'group_by_receivers'):
            value = request.args.get(key, '0')
            if value not in ('0', '1'):
                raise NomError('REPORT_OPTION', 'Opțiunea de afișare este invalidă: ' + key + '.', 422)
            params[key] = value == '1'
        return params

    def owner(ctx):
        return (g.portal['email'], g.portal['db_name'], ctx['subunit'], ctx['gestiune'], ctx['exercitiu'])

    def cache():
        return current_app.extensions.setdefault('vercon_report_snapshots', report_service.Snapshots())

    @bp.get('/api/vercon/reports/identity')
    @guard('read')
    def identity():
        # Local printouts of an already loaded document need only its read right.
        vctx = scoped('read')
        with session(False) as cursor:
            ctx, _ = build(cursor, vctx, None, False)
            return report_identity.read(cursor, g.portal['db_name'], ctx)

    @bp.get('/api/vercon/reports/catalog')
    @guard('raport')
    def catalog():
        vctx = scoped('raport')
        with session(False) as cursor:
            ctx, _ = build(cursor, vctx, None, False)
            period = report_period.catalog_period(cursor, ctx)
        return {'reports': report_catalog.catalog(vctx['gestiune']['type']), **period}

    @bp.get('/api/vercon/reports/<name>/selection')
    @guard('raport')
    def lookups(name):
        vctx = scoped('raport')
        spec = report_catalog.spec_of(name, vctx['gestiune']['type'])
        params = selection()
        with session(False) as cursor:
            ctx, _ = build(cursor, vctx, None, False)
            source = report_data.Source(cursor, g.portal['db_name'], ctx, params)
            branch = request.args.get('branch')
            if branch is not None or spec.get('mf_selection'):
                return report_selection.selection_branch(source, spec, branch or 'root', recursive=request.args.get('recursive') == '1',
                                                         documents=request.args.get('selection_mode') == 'documents',
                                                         receivers=request.args.get('selection_mode') == 'receivers')
            variants = list(source.metadata().values())
            categories = {r['category']: r['account'] for r in variants}
            documents, suppliers, receivers = {}, {}, {}
            if spec['kind'] == 'food':
                for row in source.rows('food_history'):
                    if params['start'] <= stock.day_of(row['date']) <= params['end']:
                        documents[row['IdLista']] = f"{row['NumarLA']} / {row['date']}"
            else:
                custody = spec.get('ledger') == 'custody'
                oi_document = spec.get('family') in ('bp', 'bt', 'bs')
                query = 'oi_documents' if custody or oi_document else 'consumption' if spec.get('family') in ('bc', 'br') or spec['kind'].startswith('consumption') else 'receipts'
                if custody or oi_document:
                    categories = {r['category']: r['account'] for r in source.rows('oi_accounts')}
                    receivers = {r['receiver']: r['party'] for r in source.rows('oi_holders')}
                for row in source.rows(query):
                    if row['annulled'] or spec.get('family') and row['family'] != spec['family']:
                        continue
                    if row.get('supplier') is not None:
                        suppliers[row['supplier']] = row['party'] or str(row['supplier'])
                    if row.get('receiver') is not None:
                        receivers[row['receiver']] = row['party'] or str(row['receiver'])
                    day = row.get('movement_date') or row['date']
                    if day is not None and params['start'] <= stock.day_of(day) <= params['end']:
                        documents[row['document']] = f"{row['family'].upper()} {row['number']} / {day} / {row.get('party') or ''}"
        def options(items):
            return [{'id': key, 'label': value} for key, value in items.items() if key is not None]
        return {'variants': [{'id': r['variant'], 'product': r['product'], 'category': r['category'], 'account': r['account'],
                             'name': r['name'], 'um': r['um'],
                             'label': (r['name'] or '') + ' · ' + str(r['code'] or r['variant']),
                             'variant_label': str(r['code'] or r['variant']) + ' / ' + (stock.quantity_text(Decimal(r['price']), ctx['scale']) if r['price'] is not None else '—') + ' lei'} for r in variants],
                'categories': options(categories), 'suppliers': options(suppliers), 'receivers': options(receivers),
                'documents': options(documents), 'spec': spec,
                'account_filter': bool(spec.get('ledger') == 'custody' or spec.get('family') in ('bp', 'bt', 'bs'))}

    @bp.get('/api/vercon/reports/<name>/generate')
    @guard('raport')
    def generate(name):
        vctx = scoped('raport')
        params = selection()
        with session(False) as cursor:
            ctx, _ = build(cursor, vctx, None, False)
            model = report_service.build(cursor, g.portal['db_name'], ctx, name, params, vctx['gestiune'].get('name', ''))
        return {**model, 'snapshot': cache().put(owner(ctx), model)}

    @bp.get('/api/vercon/reports/export/<token>/<format_name>')
    @guard('raport')
    def export(token, format_name):
        if format_name not in ('pdf', 'xlsx'):
            raise NomError('REPORT_FORMAT', 'Format de export necunoscut.', 404)
        vctx = scoped('raport')
        with session(False) as cursor:
            ctx, _ = build(cursor, vctx, None, False)
            model = cache().get(owner(ctx), token)
            if model.get('letterhead_version') != report_identity.VERSION:
                raise NomError('REPORT_EXPIRED', 'Generați din nou raportul pentru antetul actualizat.', 409)
            report_catalog.spec_of(model['report_name'], ctx['type'])
            widths = report_excel.read_widths(cursor, model) if format_name == 'xlsx' else None
        try:
            data = report_output.pdf(model) if format_name == 'pdf' else report_output.xlsx(model, widths)
        except ImportError as error:
            raise NomError('REPORT_DEPENDENCY', 'Exportul necesită dependențele din requirements-reporting.txt pe server.', 503) from error
        mime = 'application/pdf' if format_name == 'pdf' else 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        filename = ''.join(c if c.isalnum() or c in '_-.' else '_' for c in model['filename']) + '.' + format_name
        return Response(data, mimetype=mime, headers={'Content-Disposition': f'attachment; filename="{filename}"'})
