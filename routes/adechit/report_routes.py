"""ADE report routes using the shared generation/snapshot/export lifecycle."""
import os
from pathlib import Path
from flask import current_app, g, request, Response, jsonify
from werkzeug.utils import secure_filename
from reporting.snapshots import Snapshots
from .domain import DomainError, require
from . import reports, report_render, report_excel


def register(bp, guard):
    def owner(repo):
        return (g.portal['email'], g.portal['db_name'], repo.scope())

    def cache():
        root = Path(os.environ.get('ADECHIT_REPORT_CONFIG_DIR',
                    str(Path(current_app.instance_path) / 'adechit-reports')))
        return current_app.extensions.setdefault('adechit_report_snapshots', Snapshots(root / 'snapshots', DomainError))

    def export(repo, built, output):
        require(output in ('pdf', 'print', 'xlsx'), 'OUTPUT', 'Format de raport necunoscut.', 404)
        if output == 'print':
            response = Response(report_render.html_page(built), mimetype='text/html')
            response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; font-src 'self'; frame-ancestors 'none'; base-uri 'none'"
        else:
            data = (report_render.pdf_bytes(built) if output == 'pdf' else
                    report_excel.xlsx_bytes(built, request.args, report_excel.configured_widths(repo, built)))
            mime = 'application/pdf' if output == 'pdf' else report_excel.MIMETYPE
            filename = secure_filename(built['filename'] + '.' + output)
            response = Response(data, mimetype=mime, headers={'Content-Disposition': f'attachment; filename="{filename}"'})
        response.headers.update({'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})
        return response

    @bp.get('/api/adechit/reports/<kind>/generate')
    @guard('read')
    def generate(repo, kind):
        built = reports.build(repo, g.portal['db_name'], kind, request.args)
        response = jsonify(snapshot=cache().put(owner(repo), built), title=built['title'],
                           filename=built['filename'], columns=report_excel.column_options(built))
        response.headers['Cache-Control'] = 'no-store'
        return response

    @bp.get('/api/adechit/reports/export/<token>/<output>')
    @guard('read')
    def saved_output(repo, token, output):
        return export(repo, cache().get(owner(repo), token), output)

    # Existing integrations can still request an output directly.
    @bp.get('/api/adechit/reports/<kind>/<output>')
    @guard('read')
    def direct_output(repo, kind, output):
        require(output in ('pdf', 'print', 'xlsx'), 'OUTPUT', 'Format de raport necunoscut.', 404)
        return export(repo, reports.build(repo, g.portal['db_name'], kind, request.args), output)
