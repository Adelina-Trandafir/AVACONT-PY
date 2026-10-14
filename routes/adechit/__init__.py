"""SLICE-ADE1/4/10: ADE blueprint using portal authentication and common infrastructure.

The ADE context is DC (X-Ade-Unit) + subunit (X-Ade-Subunit); both are checked on every request."""
import logging
from functools import wraps
from flask import Blueprint, Response, current_app, g, jsonify, request, send_from_directory, render_template, send_file
from io import BytesIO
from werkzeug.utils import secure_filename
from .domain import SCHEMA, EDITABLE, DomainError, require
from .repository import transaction
from .calculations import calculate
from . import service
from . import importer
from . import catalog_forms
from routes.portal import drepturi

SECTION = "AD"

logger = logging.getLogger(__name__)


def create_blueprint(authenticate=None, repository_factory=None, rights=None):
    bp = Blueprint('adechit', __name__, template_folder='../../templates')
    # rights(context) -> the set of operations the signed-in user may do in this unit. Default: the
    # AD roles of AVACONT_COMUN (slice AD10-01); the local preview passes its own.
    if rights is None:
        def rights(context):
            return drepturi.operations_of(context["email"], context["db_name"], SECTION)

    def guard(operation):
        def decorate(function):
            @wraps(function)
            def guarded(*args, **kwargs):
                def run():
                    try:
                        context = g.portal
                        is_context = request.path.endswith('/context')
                        require(request.headers.get('X-Ade-Unit') == context['db_name'] or is_context,
                                'CONTEXT_CHANGED', 'Unitatea s-a schimbat. Reîncărcați pagina.', 409)
                        with transaction(context['db_name'], repository_factory, request.headers.get('X-Ade-Subunit'),
                                         optional_subunit=is_context, email=context['email']) as repo:
                            g.ade_operations = rights(context)
                            require(operation in g.ade_operations, 'FORBIDDEN', 'Nu aveți dreptul necesar pentru această operație ADE.', 403)
                            result = function(repo, *args, **kwargs)
                        response = result if isinstance(result, Response) else jsonify(result)
                        logger.info('ADE operation=%s method=%s path=%s outcome=success', operation, request.method, request.path)
                        return response
                    except DomainError as error:
                        logger.warning('ADE operation=%s refusal=%s', operation, error.reason)
                        body = {'error': str(error), 'reason': error.reason}
                        if error.details is not None:
                            body['details'] = error.details
                        return jsonify(body), error.status
                    except (KeyError, TypeError) as error:
                        logger.warning('ADE malformed request: %s', type(error).__name__)
                        return jsonify(error='Cerere incompletă sau invalidă.', reason='REQUEST'), 400
                    except Exception as error:
                        logger.exception('ADE operation=%s method=%s path=%s failed', operation, request.method, request.path)
                        reading = request.method in ('GET', 'HEAD')
                        if getattr(error, 'errno', None) in (1205, 1213):
                            message = ('Baza de date este ocupată. Reîncercați încărcarea datelor.' if reading else
                                       'Baza de date este ocupată. Reîncercați salvarea cu aceeași cheie.')
                            return jsonify(error=message, reason='DATABASE_BUSY'), 503
                        message = ('Datele nu au putut fi încărcate. Reîncercați.' if reading else
                                   'Operația nu a fost finalizată. Reîncercați cu aceeași cheie.')
                        return jsonify(error=message, reason='SERVER'), 500
                if authenticate:
                    return authenticate(run)()
                from routes.portal.portal import require_portal_session
                from utils.timing import timed
                return require_portal_session(timed('ade.' + operation)(run))()
            return guarded
        return decorate

    @bp.get('/adechit')
    def page():
        response = send_from_directory(current_app.static_folder, 'adechit.html')
        if repository_factory is not None:
            response.direct_passthrough = False
            response.set_data(response.get_data(as_text=True).replace('<html lang="ro">', '<html lang="ro" data-ade-preview="true">', 1))
        response.headers.update({'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
                                 'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'"})
        return response

    @bp.get('/api/adechit/context')
    @guard('read')
    def context(repo):
        active = [{'id': row['SubunitId'], 'name': row['Name']} for row in repo.subunits if row['Active'] and row['Allowed']]
        return {'unit': g.portal['db_name'], 'email': g.portal['email'], 'schema': SCHEMA,
                'subunits': active,
                'subunit': {'id': repo.subunit_id, 'name': repo.subunit_name} if repo.subunit_id is not None else None,
                'editable': EDITABLE,
                'settings': {'allowCancel': 'cancel' in g.ade_operations},   # the right is per account (role AD_ANULARE), no longer per subunit
                'permissions': sorted(g.ade_operations),
                'preview': repository_factory is not None,
                'limitations': ['Paritatea completă Access nu este validată.', 'Rapoartele sunt scrise local și nu au fost probate pe server.']}

    @bp.get('/api/adechit/years')
    @guard('read')
    def years(repo):
        return {'years': repo.years()}

    @bp.get('/api/adechit/receipt-defaults')
    @guard('read')
    def receipt_defaults(repo):
        month_id = request.args.get('IDL', '')
        require(month_id.isdigit(), 'FILTER', 'Filtru invalid.', 400)
        config = repo.receipt_config()
        if config is None:
            return {'explanation': ''}
        return {'explanation': service.receipt_explanation(config, repo.get('LunaD', int(month_id)))}

    @bp.get('/api/adechit/catalog-data')
    @guard('read')
    def catalog_data(repo):
        month_id = request.args.get('IDL')
        require(month_id is None or month_id.isdigit(), 'FILTER', 'Filtru de lună invalid.', 400)
        return catalog_forms.catalog_data(repo, include_hidden=request.args.get('include_hidden') == '1',
                                         month_id=int(month_id) if month_id is not None else None)

    @bp.get('/api/adechit/rows/<table>')
    @guard('read')
    def rows(repo, table):
        filters = {}
        for key in ('IDL', 'IDG', 'IDP', 'IDZ', 'Anul'):
            if key in request.args:
                require(request.args[key].isdigit(), 'FILTER', 'Filtru invalid.', 400)
                filters[key] = int(request.args[key])
        return {'rows': repo.rows(table, **filters)}

    def command(repo, operation, function):
        body = request.get_json(silent=True)
        require(isinstance(body, dict), 'JSON', 'Este necesară o cerere JSON.', 400)
        return service.idempotent(repo, request.headers.get('Idempotency-Key'), operation, body, lambda: function(body))

    @bp.get('/api/adechit/bank-extracts')
    @guard('read')
    def bank_extracts(repo):
        from .bank_associations import available
        return available(repo, request.args)

    @bp.get('/api/adechit/bank-extracts/periods')
    @guard('read')
    def bank_extract_periods(repo):
        from .bank_associations import available_periods
        return available_periods(repo)

    @bp.get('/api/adechit/bank-extracts/payers')
    @guard('read')
    def bank_extract_payers(repo):
        from .bank_associations import payer_history
        child_id = request.args.get('IDP', '')
        require(child_id.isdigit(), 'FILTER', 'Filtru de copil invalid.', 400)
        return {'rows': payer_history(repo, int(child_id))}

    @bp.post('/api/adechit/bank-extracts/associate')
    @guard('collect')
    def bank_extract_associate(repo):
        from .bank_associations import associate
        require('catalog' in g.ade_operations, 'FORBIDDEN',
                'Asocierea necesită drepturile de încasare și de modificare a plătitorilor.', 403)
        return command(repo, 'bank-extract-associate', lambda body: associate(repo, g.portal['db_name'], body, g.portal['email']))

    @bp.get('/api/adechit/bank-extracts/defaults')
    @guard('read')
    def bank_extract_defaults(repo):
        from .bank_associations import default_explanation
        month_id = request.args.get('IDL', '')
        require(month_id.isdigit(), 'FILTER', 'Filtru de lună invalid.', 400)
        return {'explanation': default_explanation(repo, int(month_id))}

    @bp.get('/api/adechit/bank-extracts/preview')
    @guard('read')
    def bank_extract_preview(repo):
        from .bank_preview import preview
        return preview(repo, request.args)

    @bp.post('/api/adechit/bank-extracts/selected')
    @guard('collect')
    def bank_extract_selected(repo):
        from .bank_preview import save_selected
        require('catalog' in g.ade_operations, 'FORBIDDEN',
                'Generarea necesită drepturile de încasare și de modificare a plătitorilor.', 403)
        return command(repo, 'bank-extract-selected', lambda body: save_selected(repo, g.portal['db_name'], body, g.portal['email']))

    @bp.get('/api/adechit/payment-notice/config')
    @guard('read')
    def payment_notice_config(repo):
        from .payment_notice import configuration
        from .report_richtext import editable_html
        result = configuration(repo)
        result['preview'] = editable_html(result['html'])
        return result

    @bp.post('/api/adechit/payment-notice/config')
    @guard('catalog')
    def payment_notice_config_save(repo):
        from .payment_notice import save_configuration
        return command(repo, 'payment-notice-config', lambda body: save_configuration(repo, body))

    @bp.post('/api/adechit/payment-notice/preview')
    @guard('read')
    def payment_notice_preview(repo):
        from .payment_notice import MAX_HTML
        from .report_richtext import editable_html
        body = request.get_json(silent=True)
        require(isinstance(body, dict) and isinstance(body.get('html'), str) and len(body['html']) <= MAX_HTML,
                'REPORT_HTML', 'Textul HTML trebuie să aibă cel mult 30.000 de caractere.', 400)
        return {'preview': editable_html(body['html'])}

    @bp.post('/api/adechit/catalog/<table>')
    @guard('catalog')
    def catalog(repo, table):
        return command(repo, 'catalog/' + table, lambda body: service.save_catalog(repo, table, body))

    @bp.post('/api/adechit/catalog-save')
    @guard('catalog')
    def catalog_save(repo):
        return command(repo, 'catalog-save', lambda body: service.save_catalog_batch(repo, body))

    @bp.post('/api/adechit/group-save')
    @guard('catalog')
    def group_save(repo):
        return command(repo, 'group-save', lambda body: catalog_forms.save_group(repo, body))

    @bp.post('/api/adechit/group-hidden')
    @guard('catalog')
    def group_hidden(repo):
        return command(repo, 'group-hidden', lambda body: catalog_forms.set_group_hidden(repo, body))

    @bp.post('/api/adechit/child-save')
    @guard('catalog')
    def child_save(repo):
        return command(repo, 'child-save', lambda body: catalog_forms.save_child(repo, body, g.portal['email'], 'transfer' in g.ade_operations))

    @bp.post('/api/adechit/child-with-payer')
    @guard('catalog')
    def child_with_payer(repo):
        return command(repo, 'child-with-payer', lambda body: catalog_forms.save_child_with_payer(repo, body, g.portal['email'], 'transfer' in g.ade_operations))

    @bp.get('/api/adechit/parents/similar')
    @guard('read')
    def parent_similar(repo):
        from .parent_identity import similar_contacts
        exclude = request.args.get('exclude', '')
        require(exclude == '' or exclude.isdigit(), 'FILTER', 'Filtru invalid.', 400)
        return similar_contacts(repo, request.args.get('cnp', '').strip(), request.args.get('email', '').strip().lower(),
                                int(exclude) if exclude else -1)

    @bp.get('/api/adechit/parents/portal-issues')
    @guard('read')
    def parent_portal_issues(repo):
        from .parent_identity import portal_issues
        return {'issues': portal_issues(repo)}

    @bp.post('/api/adechit/parents/<int:parent_id>/send-access')
    @guard('catalog')
    def send_parent_access(repo, parent_id):
        from .parent_portal import send_access
        return send_access(repo, parent_id, g.portal['db_name'], current_app.config.get('ADE_PARENT_DELIVER'))

    @bp.post('/api/adechit/attendance')
    @guard('attendance')
    def attendance(repo):
        return command(repo, 'attendance', lambda body: service.save_attendance(repo, body, 'edit_closed' in g.ade_operations))

    @bp.post('/api/adechit/attendance/prepare')
    @guard('attendance')
    def prepare_attendance(repo):
        return command(repo, 'attendance/prepare', lambda body: service.prepare_attendance(repo, body))

    @bp.get('/api/adechit/situation/<int:month_id>')
    @guard('read')
    def situation(repo, month_id):
        group_id = request.args.get('IDG')
        require(group_id is None or group_id.isdigit(), 'FILTER', 'Filtru invalid.', 400)
        group_id = int(group_id) if group_id is not None else None
        month = repo.get('LunaD', month_id)
        if month['Inchisa']:
            filters = {'IDL': month_id, **({'IDG': group_id} if group_id is not None else {})}
            return {'rows': repo.rows('SS_Buget', **filters), 'saved': True}
        data = repo.situation_data(month_id, group_id) if group_id is not None else repo.data()
        calculated, _ = calculate(data, month_id, group_id)
        attendance = {row['IDZ']: row for row in data['Prezenta'] if row['IDL'] == month_id}
        for row in calculated:
            source = attendance[row['IDZ']]
            row.update(Version=source['Version'], ZileLuna=service.month_days(month), IDV=source['IDV'])
        return {'rows': calculated, 'saved': False, 'parity_verified': False}

    @bp.get('/api/adechit/ledger/<int:attendance_id>')
    @guard('read')
    def ledger(repo, attendance_id):
        month_id = request.args.get('IDL', '')
        kind = request.args.get('kind', '')
        require(month_id.isdigit(), 'FILTER', 'Filtru de lună invalid.', 400)
        require(kind in ('receipt', 'other', 'refund'), 'FILTER', 'Tip de document invalid.', 400)
        return {'rows': repo.ledger_rows(attendance_id, int(month_id), kind)}

    @bp.post('/api/adechit/documents')
    @guard('collect')
    def documents(repo):
        return command(repo, 'documents', lambda body: service.emit_document(repo, g.portal['db_name'], body))

    @bp.get('/api/adechit/receipts/<int:receipt_id>/<output>')
    @guard('read')
    def receipt_output(repo, receipt_id, output):
        from .receipt_output import receipt_data, pdf_bytes
        require(output in ('pdf', 'print'), 'OUTPUT', 'Format de chitanță necunoscut.', 404)
        data = receipt_data(repo, receipt_id, g.portal['db_name'])
        if output == 'pdf':
            name = secure_filename(f"Chitanta_{data['series']}_{data['number']}.pdf")
            response = send_file(BytesIO(pdf_bytes(data)), mimetype='application/pdf', as_attachment=True, download_name=name, max_age=0)
        else:
            response = Response(render_template('adechit/receipt.html', receipt=data), mimetype='text/html')
            response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; font-src 'self'; frame-ancestors 'none'; base-uri 'none'"
        response.headers.update({'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})
        return response

    @bp.get('/api/adechit/help/pdf')
    @guard('read')
    def help_pdf(repo):
        from .help_pdf import pdf_bytes
        response = send_file(BytesIO(pdf_bytes()), mimetype='application/pdf', as_attachment=True, download_name='Ajutor_ADECHIT.pdf', max_age=0)
        response.headers.update({'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})
        return response

    @bp.get('/api/adechit/reports/<kind>/<output>')
    @guard('read')
    def report_output(repo, kind, output):
        # SLICE-ADE9-01: every report is a read of the current subunit; nothing is written or numbered here.
        from . import reports, report_render
        require(output in ('pdf', 'print', 'xlsx'), 'OUTPUT', 'Format de raport necunoscut.', 404)
        built = reports.build(repo, g.portal['db_name'], kind, request.args)
        if output == 'pdf':
            response = send_file(BytesIO(report_render.pdf_bytes(built)), mimetype='application/pdf', as_attachment=True,
                                 download_name=secure_filename(built['filename'] + '.pdf'), max_age=0)
        elif output == 'xlsx':
            from . import report_excel
            response = send_file(BytesIO(report_excel.xlsx_bytes(built, request.args, report_excel.configured_widths(repo, built))), mimetype=report_excel.MIMETYPE,
                                 as_attachment=True, download_name=secure_filename(built['filename'] + '.xlsx'), max_age=0)
        else:
            response = Response(report_render.html_page(built), mimetype='text/html')
            response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; font-src 'self'; frame-ancestors 'none'; base-uri 'none'"
        response.headers.update({'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})
        return response

    @bp.post('/api/adechit/cancel')
    @guard('cancel')
    def cancel(repo):
        return command(repo, 'cancel', lambda body: service.cancel_document(repo, body))

    @bp.post('/api/adechit/close')
    @guard('close')
    def close(repo):
        def run(body):
            if repo.get('LunaD', body['id'])['Luna'] == 8:
                require({'catalog', 'transfer'} <= set(g.ade_operations), 'FORBIDDEN',
                        'Închiderea anuală necesită drepturile pentru catalog și transfer.', 403)
            return service.close_month(repo, body, g.portal['email'])
        return command(repo, 'close', run)

    @bp.get('/api/adechit/annual/<int:month_id>')
    @guard('close')
    def annual_preview(repo, month_id):
        from . import annual
        require({'catalog', 'transfer'} <= set(g.ade_operations), 'FORBIDDEN',
                'Închiderea anuală necesită drepturile pentru catalog și transfer.', 403)
        return annual.preview(repo, month_id)

    @bp.post('/api/adechit/reopen')
    @guard('reopen')
    def reopen(repo):
        return command(repo, 'reopen', lambda body: service.reopen_month(repo, body))

    @bp.post('/api/adechit/transfer')
    @guard('transfer')
    def transfer(repo):
        raise DomainError('M06', 'Transferul așteaptă clarificarea efectului asupra istoricului.')

    @bp.post('/api/adechit/import')
    @guard('import')
    def import_data(repo):
        body = request.get_json(silent=True)
        require(isinstance(body, dict) and isinstance(body.get('dataset'), dict),
                'IMPORT_FORMAT', 'Este necesar un extract ADE valid.', 400)
        return importer.import_dataset(repo, body['dataset'], g.portal['db_name'],
                                       str(body.get('source_file') or 'uploaded.json'))

    @bp.post('/api/adechit/reconcile')
    @guard('import')
    def reconcile(repo):
        body = request.get_json(silent=True)
        require(isinstance(body, dict) and isinstance(body.get('dataset'), dict),
                'IMPORT_FORMAT', 'Este necesar un extract ADE valid.', 400)
        return importer.reconcile_dataset(repo, body['dataset'], g.portal['db_name'])

    return bp


adechit_bp = create_blueprint()
