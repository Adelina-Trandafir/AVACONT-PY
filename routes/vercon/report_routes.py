"""Report catalog, filters, snapshots and exact exports for Materiale/Alimente."""
from decimal import Decimal
from flask import Response, current_app, g, request
from . import report_catalog, report_data, report_output, report_service, report_excel, protocol, stock
from .nomenclatoare import NomError
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


def register(bp, guard, scoped, session, build):
    def selection():
        params = {'start': protocol.to_date(request.args.get('start'), 'De la'),
                  'end': protocol.to_date(request.args.get('end'), 'Până la')}
        if params['start'] > params['end']:
            raise NomError('REPORT_PERIOD', 'Data de început trebuie să fie înaintea datei de sfârșit.', 422)
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
        return params

    def owner(ctx):
        return (g.portal['email'], g.portal['db_name'], ctx['subunit'], ctx['gestiune'], ctx['exercitiu'])

    def cache():
        return current_app.extensions.setdefault('vercon_report_snapshots', report_service.Snapshots())

    @bp.get('/api/vercon/reports/catalog')
    @guard('raport')
    def catalog():
        vctx = scoped('raport')
        return {'reports': report_catalog.catalog(vctx['gestiune']['type'])}

    @bp.get('/api/vercon/reports/<name>/selection')
    @guard('raport')
    def lookups(name):
        vctx = scoped('raport')
        spec = report_catalog.spec_of(name, vctx['gestiune']['type'])
        params = selection()
        with session(False) as cursor:
            ctx, _ = build(cursor, vctx, None, False)
            source = report_data.Source(cursor, g.portal['db_name'], ctx, params)
            variants = list(source.metadata().values())
            categories = {r['category']: r['account'] for r in variants}
            documents, suppliers, receivers = {}, {}, {}
            if spec['kind'] == 'food':
                for row in source.rows('food_history'):
                    if params['start'] <= stock.day_of(row['date']) <= params['end']:
                        documents[row['IdLista']] = f"{row['NumarLA']} / {row['date']}"
            else:
                query = 'consumption' if spec.get('family') in ('bc', 'br') or spec['kind'].startswith('consumption') else 'receipts'
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
                'documents': options(documents), 'spec': spec}

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
            report_catalog.spec_of(model['report_name'], ctx['type'])
            widths = report_excel.read_widths(cursor, model) if format_name == 'xlsx' else None
        try:
            data = report_output.pdf(model) if format_name == 'pdf' else report_output.xlsx(model, widths)
        except ImportError as error:
            raise NomError('REPORT_DEPENDENCY', 'Exportul necesită dependențele din requirements-reporting.txt pe server.', 503) from error
        mime = 'application/pdf' if format_name == 'pdf' else 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        filename = ''.join(c if c.isalnum() or c in '_-.' else '_' for c in model['filename']) + '.' + format_name
        return Response(data, mimetype=mime, headers={'Content-Disposition': f'attachment; filename="{filename}"'})
