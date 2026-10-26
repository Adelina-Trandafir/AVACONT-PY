"""Routes for bringing bank statements in: manual upload and the mailbox (SLICE-AD13-01).

Same rights as the association window itself (collect + catalog): whoever may associate a payment may
also load the statements it is made from. These routes work on the whole unit, not on a subunit, so they
use the guard without an ADE transaction (see `guard(plain=True)`); the work is in extras_intake.
"""
from flask import g, request

from .domain import require
from . import extras_intake


def register(bp, guard):
    def unit():
        require('catalog' in g.ade_operations, 'FORBIDDEN',
                'Încărcarea extraselor necesită drepturile de încasare și de modificare a plătitorilor.', 403)
        return g.portal['db_name']

    @bp.get('/api/adechit/extracts/mail')
    @guard('collect', plain=True)
    def mail_settings():
        return extras_intake.settings_view(unit())

    @bp.post('/api/adechit/extracts/mail')
    @guard('collect', plain=True)
    def mail_settings_save():
        body = request.get_json(silent=True)
        require(isinstance(body, dict), 'JSON', 'Este necesară o cerere JSON.', 400)
        return extras_intake.save_settings(unit(), body, g.portal['email'])

    @bp.post('/api/adechit/extracts/mail/test')
    @guard('collect', plain=True)
    def mail_test():
        return extras_intake.test_mailbox(unit())

    @bp.post('/api/adechit/extracts/mail/fetch')
    @guard('collect', plain=True)
    def mail_fetch():
        return extras_intake.fetch_mailbox(unit(), g.portal['email'])

    @bp.post('/api/adechit/extracts/upload')
    @guard('collect', plain=True)
    def upload():
        sent = request.files.getlist('files')
        require(bool(sent), 'UPLOAD', 'Alegeți cel puțin un fișier PDF.', 400)
        return extras_intake.upload(unit(), [(item.filename, item.read()) for item in sent], g.portal['email'])

    @bp.get('/api/adechit/extracts/history')
    @guard('collect', plain=True)
    def history():
        return extras_intake.history(unit())
