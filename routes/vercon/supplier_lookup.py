"""Read-only lookups for the supplier tools of VERCON: the info card of a supplier (tree menu) and the ANAF check of the
suppliers in a «Parteneri dublati» group.

    GET  /api/vercon/suppliers/info?cf=...   registered partner data + how many documents the supplier has
    POST /api/vercon/suppliers/anaf          {codes: [...]}: what ANAF says about each fiscal code (nothing is stored)
"""
from flask import g, request
from utils import vercon_suppliers as suppliers

from .documents import _one
from .nomenclatoare import NomError
from .supplier_merge import usage

MAX_CODES = 10


def info(cursor, code):
    partner = suppliers.find(cursor, code)
    receipts, invoices, einvoices = usage(cursor, [code])[code]
    name = partner['DenumirePartener'] if partner else None
    if not name:
        row = _one(cursor, "SELECT MIN(NULLIF(FurnizorV,'')) AS name FROM VC_NIR WHERE CodPartener=%s", (code,))
        name = row['name'] if row else None
    return {'cf': code, 'name': name or '', 'registered': bool(partner), 'hidden': bool(partner and partner['Ascuns']),
            'address': (partner or {}).get('Adresa') or '', 'iban': (partner or {}).get('ContIBAN') or '',
            'bank': (partner or {}).get('Banca') or '', 'type': (partner or {}).get('Tip') or '',
            'receipts': receipts, 'invoices': invoices, 'einvoices': einvoices}


def anaf_check(codes):
    """{code: {found, denumire, adresa, nr_reg_com, reason}}; a dead ANAF stops the whole check (no half answers)."""
    from routes.inregistrare import anaf
    out = {}
    for code in codes:
        digits = anaf.normalize_cf(code)
        if digits != code or not anaf.is_valid_cf(digits):
            out[code] = {'found': False, 'reason': 'Cod fiscal invalid (trebuie să aibă între 2 și 10 cifre).'}
            continue
        try:
            found = anaf.lookup(digits, missing_is_404=True)
        except anaf.AnafNotFound:
            out[code] = {'found': False, 'reason': 'ANAF nu cunoaște acest cod fiscal.'}
        except anaf.AnafIncomplete:
            out[code] = {'found': False, 'reason': 'ANAF a găsit codul, dar nu a dat denumirea.'}
        except anaf.AnafUnavailable:
            raise NomError('ANAF_UNAVAILABLE', 'ANAF nu a putut fi contactat acum. Încercați mai târziu.', 502) from None
        else:
            out[code] = {'found': True, 'denumire': found['denumire'], 'adresa': found['adresa'], 'nr_reg_com': found['nr_reg_com']}
    return out


def register(bp, guard, scoped, session, build):
    @bp.get('/api/vercon/suppliers/info')
    @guard('read')
    def supplier_info():
        scoped('read')
        code = suppliers.code(request.args.get('cf'))
        if not code or len(code) > 50:
            raise NomError('INVALID', 'Cod fiscal de furnizor invalid.', 422)
        with session(False) as cursor:
            return info(cursor, code)

    @bp.post('/api/vercon/suppliers/anaf')
    @guard('read')
    def supplier_anaf():
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or not isinstance(data.get('codes'), list) or not 1 <= len(data['codes']) <= MAX_CODES:
            raise NomError('INVALID', 'Cerere invalidă.', 400)
        scoped('read', data)
        if 'receptie' not in g.vercon_operations:
            raise NomError('FORBIDDEN', 'Nu aveți dreptul necesar pentru această operație VERCON.', 403)
        codes = list(dict.fromkeys(suppliers.code(value) for value in data['codes']))
        if '' in codes:
            raise NomError('INVALID', 'Cod fiscal invalid.', 422)
        return {'items': anaf_check(codes)}
