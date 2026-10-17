"""Immutable correction rows and signed undo operations, including unsaved corrections."""
from copy import deepcopy
from decimal import Decimal

from flask import g
import secrets

from routes.auth.session_store import STORE

from .nomenclatoare import NomError


class BadData(ValueError):
    pass


class _TokenNotes:
    """Use the portal's shared server-side note store; no new deployment secret is needed."""
    @staticmethod
    def owner():
        return [g.portal['db_name'], g.portal['email']]

    def dumps(self, payload):
        token = secrets.token_urlsafe(32)
        STORE.put_note(token, 'vercon_receipt_auto', {'owner': self.owner(), 'payload': payload}, 8 * 3600)
        return token

    def loads(self, token):
        note = STORE.get_note(token, 'vercon_receipt_auto') if token else None
        if not note or note.get('owner') != self.owner():
            raise BadData()
        return note['payload']


def signer():
    return _TokenNotes()


def identity(ctx, doc_id):
    return [ctx['subunit'], ctx['gestiune'], ctx['exercitiu'], doc_id]


def canonical(row):
    result = {}
    for field in ('id', 'product', 'IDTVA', 'Cant', 'Pret', 'gross_total', 'input_price', 'invoice_line', 'DNF', 'DF'):
        value = row.get(field)
        result[field] = None if value in (None, '') else format(Decimal(str(value)).normalize(), 'f')
    for field in ('code', 'data_intrarii'):
        result[field] = str(row.get(field) or '')
    result['origin'] = row.get('auto_origin')
    return result


def sign_row(ctx, doc_id, row):
    return signer().dumps({'scope': identity(ctx, doc_id), 'row': canonical(row)})


def verify_row(ctx, doc_id, row):
    try:
        payload = signer().loads(row.get('auto_token') or '')
        if payload != {'scope': identity(ctx, doc_id), 'row': canonical(row)}:
            raise ValueError()
    except (BadData, ValueError, ArithmeticError):
        raise NomError('AUTO_ROW_LOCKED', 'Rândul auto-calculat nu se editează. Folosiți ștergerea cu revenire în grupul de origine.', 422) from None


def permitted_undos(ctx, doc_id, version, tokens):
    ids = set()
    if not isinstance(tokens, list) or len(tokens) > 500:
        raise NomError('INVALID', 'Operații de revenire invalide.', 422)
    for token in tokens:
        try:
            payload = signer().loads(token)
            if payload['scope'] != identity(ctx, doc_id) or payload['version'] != version:
                raise ValueError()
            ids.add(payload['undo'])
        except (BadData, ValueError, KeyError, TypeError):
            raise NomError('AUTO_UNDO_INVALID', 'Revenirea corecției nu mai corespunde documentului. Reîncărcați documentul.', 409) from None
    return ids


def undo(parsed, key, ctx, doc_id, version):
    from .documents import receipt_line
    result = deepcopy(parsed)
    chosen = next((line for line in result['lines'] if line['key'] == str(key)), None)
    if not chosen or not chosen.get('calculation') or not chosen['calculation'].get('origin'):
        raise NomError('AUTO_UNDO_INVALID', 'Rândul nu are un grup de origine pentru revenire.', 422)
    origin = chosen['calculation']['origin']
    parent = next((line for line in result['lines'] if line is not chosen and
                   ((origin.get('parent_id') and line.get('id') == origin['parent_id']) or line['key'] == origin.get('parent_key'))), None)
    if parent and (parent.get('product') != origin['product'] or parent['IDTVA'] != origin['IDTVA'] or
                   parent.get('calculation') or parent['v']['Pret'] != Decimal(origin['Pret'])):
        raise NomError('AUTO_ORIGIN_CHANGED', 'Grupul de origine a fost modificat. Restabiliți produsul, prețul și cota înainte de revenire.', 409)
    quantity = chosen['v']['Cant'] + (parent['v']['Cant'] if parent else 0)
    if parent is None:
        parent = {**chosen, 'id': chosen.get('id') if not origin.get('split') else None,
                  'key': chosen['key'] + '-restored', 'product': origin['product'], 'variant': origin.get('variant'),
                  'IDTVA': origin['IDTVA'], 'rate': Decimal(origin['rate'])}
        result['lines'].append(parent)
    parent['v'] = receipt_line(quantity, Decimal(origin['Pret']), Decimal(origin['rate']), ctx['zec'], ctx['scale'])
    parent.pop('calculation', None)
    parent.pop('preserve', None)
    result['lines'].remove(chosen)
    token = signer().dumps({'scope': identity(ctx, doc_id), 'version': version, 'undo': chosen.get('id')})
    result['undo_token'] = token
    return result
