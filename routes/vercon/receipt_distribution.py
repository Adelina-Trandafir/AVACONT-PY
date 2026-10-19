"""One receipt editor, one atomic supplier invoice, one NIR per stock destination."""
from decimal import Decimal

from flask import request

from . import receipt_destinations as destinations, receipts, receipt_rules, receipt_auto, receipt_number, receipt_adjustment, invoices, protocol
from .documents import _dec, _int, _scope
from .nomenclatoare import NomError


def total(parsed):
    return sum((line['v']['ValoareCUTVA'] for line in parsed['lines']), Decimal(0))


def body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise NomError('INVALID', 'Cerere invalidă.', 400)
    return data


def parse_groups(cursor, anchor, groups, data):
    header = data.get('header') if isinstance(data.get('header'), dict) else {}
    for group in groups:
        ctx = group['ctx']
        protocol.ensure_open(group['exercise'])
        local_header = {**header, 'IdMasina': None, 'Valoare': None,
                        'IdComisie': destinations.commission(cursor, anchor, ctx, header.get('IdComisie'))}
        local = {**data, 'header': local_header, 'accounts': list(group['accounts']), 'lines': group['lines'], 'id': None, 'undo_tokens': []}
        if data.get('action') == 'adjust':
            for raw in local['lines']:
                if not data.get('line_key') or str(raw.get('key')) == str(data['line_key']):
                    raw['recalculate'] = True
        group['body'] = local
        group['parsed'] = receipts._clean_nir(cursor, group['repo'], ctx, group['exercise'], local, 'nir', allow_empty=True)
        if header.get('TipDoc') != 'STOC_INITIAL':
            verdict = receipt_number.check(cursor, ctx, None if len(groups) > 1 else header.get('Numar'),
                                           sub=0 if len(groups) > 1 else _int(header.get('NumarSub')) or 0, on=header.get('Data'))
            if not verdict['valid'] or verdict['duplicate']:
                raise NomError('INVALID', 'Numărul NIR nu este disponibil în destinația selectată.', 422, {'h.Numar': 'Număr invalid sau deja folosit.'})
    return header


def preview(cursor, groups, data):
    action = data.get('action', 'validate')
    if action in ('adjust', 'undo_adjustment'):
        selected = str(data.get('line_key') or '')
        choices = groups
        if action == 'adjust':
            choices = sorted(groups, key=lambda group: not any(row['key'] == selected for row in group['parsed']['lines']))
        chosen = next((group for group in choices if any(
            receipt_adjustment.eligible(row) if action == 'adjust' else row['key'] == selected
            for row in group['parsed']['lines'])), None)
        if chosen is None:
            if action == 'adjust':
                raise NomError('ADJUST_LINE', receipt_adjustment.unavailable_message(
                    [row for group in groups for row in group['parsed']['lines']]), 422)
            raise NomError('ADJUST_LINE', 'Alegeți un rând pentru corecție.', 422)
        if action == 'adjust':
            target = _dec((data.get('header') or {}).get('Valoare'))
            if target is None:
                raise NomError('INVALID', 'Introduceți valoarea facturii.', 422, {'h.Valoare': 'Valoarea țintă este obligatorie.'})
            if any(row.get('calculation') for group in groups for row in group['parsed']['lines']):
                raise NomError('AUTO_ROW_LOCKED', 'Ștergeți corecția existentă înainte de o nouă ajustare.', 422)
            chosen['parsed']['declared'] = total(chosen['parsed']) + target - sum((total(group['parsed']) for group in groups), Decimal(0))
            chosen['parsed'] = receipt_rules.adjust(chosen['parsed'], chosen['ctx'], selected or None)
        else:
            chosen['parsed'] = receipt_auto.undo(chosen['parsed'], selected, chosen['ctx'], None, None)
    lines, result, grand = [], {}, Decimal(0)
    for group in groups:
        parsed, ctx = group['parsed'], group['ctx']
        receipt_rules.simulate(cursor, group['repo'], ctx, parsed)
        encoded = receipt_rules.serialize(parsed, ctx)
        lines.extend({**line, 'destination': group['destination']['id']} for line in encoded['lines'])
        grand += total(parsed)
        if encoded.get('undo_token'):
            result['undo_token'] = encoded['undo_token']
    return {**result, 'lines': lines, 'total': format(grand, 'f')}


def write(cursor, anchor, groups, header, data, key, request_id):
    # Parsing and stock checks happen for the entire set before the first write.
    if not any(group['parsed']['lines'] for group in groups):
        raise NomError('INVALID', 'Adăugați produsele recepționate.', 422)
    grand = sum((total(group['parsed']) for group in groups), Decimal(0))
    declared = _dec(header.get('Valoare'))
    if header.get('Valoare') not in (None, '') and (declared is None or declared < 0 or declared != grand):
        raise NomError('NIR_TOTAL', 'Totalul produselor trebuie să fie exact valoarea facturii. Folosiți corecția sau completați rândurile.', 422,
                       {'h.Valoare': 'Total produse: %s.' % format(grand, 'f')})
    for group in groups:
        receipt_rules.simulate(cursor, group['repo'], group['ctx'], group['parsed'])
    active = [group for group in groups if group['parsed']['lines']]
    invoice_id = None
    if header.get('TipDoc') != 'STOC_INITIAL':
        cols = active[0]['parsed']['cols']
        cursor.execute('INSERT INTO VC_FACTURI (PARTENER_ID,NUMAR,DATA,TOTAL,TOTAL_CONFIRMAT,LOCALA,CREAT_DE) '
                       'VALUES (%s,%s,%s,%s,%s,%s,%s)',
                       (cols['IdPartener'], cols['NrFact'], cols['DataFc'], declared, int(declared is not None), int(len(active) == 1), anchor['email']))
        invoice_id = cursor.lastrowid
        protocol.audit(cursor, anchor, 'invoice.distribution', 'VC_FACTURI', invoice_id, None,
                       {'receipts': len(active)}, key, request_id)
        for group in active:
            for line in group['parsed']['lines']:
                cursor.execute('INSERT INTO VC_FACTURI_LINII (FACTURA_ID,DENUMIRE,CANT,COTA,VALOARE) VALUES (%s,%s,%s,%s,%s)',
                               (invoice_id, line.get('name') or 'Produs', line['v']['Cant'], line['rate'], line['v']['ValoareCUTVA']))
                line['invoice_line'] = cursor.lastrowid
    results, document = [], None
    for group in active:
        ctx, parsed = group['ctx'], group['parsed']
        parsed['declared'] = total(parsed)
        local_header = {**group['body']['header'], 'FacturaId': invoice_id, 'Valoare': format(parsed['declared'], 'f')}
        if len(groups) > 1:
            local_header.update({'Numar': None, 'NumarSub': 0})
        result = receipts.create_nir(cursor, group['repo'], ctx, group['exercise'],
                                     {**group['body'], 'header': local_header}, key, request_id, parsed=parsed)
        results.append({**result, 'subunit': ctx['subunit'], 'gestiune': ctx['gestiune'], 'name': group['destination']['name']})
        if (ctx['subunit'], ctx['gestiune']) == (anchor['subunit'], anchor['gestiune']):
            document = receipts.saved_document(cursor, group['repo'], ctx, result['id'])
    if invoice_id:
        # Local helpers may refresh the single-NIR total; the supplier's original declaration remains authoritative.
        cursor.execute('UPDATE VC_FACTURI SET TOTAL=%s,TOTAL_CONFIRMAT=%s,LOCALA=%s WHERE ID=%s', (declared, int(declared is not None), int(len(active) == 1), invoice_id))
        invoices.refresh_state(cursor, invoice_id)
        if document:
            document = invoices.enrich(cursor, anchor, document)
    return {'document': document, 'receipts': results, 'invoice_id': invoice_id, 'total': format(grand, 'f')}


def register(bp, guard, scoped, session, build, repo_of, need):
    @bp.get('/api/vercon/doc/nir/destinations')
    @guard('read')
    def get_destinations():
        need('nir')
        verified = scoped('read')
        with session(False) as cursor:
            ctx, _ = build(cursor, verified, 'nir', False)
            return destinations.catalog(cursor, ctx)

    @bp.post('/api/vercon/doc/nir/distribution/validate')
    @guard('read')
    def validate():
        need('nir')
        data = body()
        verified = scoped('read', data)
        with session(False) as cursor:
            invoices.require(cursor)
            anchor, _ = build(cursor, verified, 'nir', False)
            groups = destinations.selections(cursor, anchor, data, scoped, build, repo_of)
            parse_groups(cursor, anchor, groups, data)
            return preview(cursor, groups, data)

    @bp.post('/api/vercon/doc/nir/distribution')
    @guard('receptie')
    def save():
        need('nir')
        data = body()
        key = str(data.get('request_key') or '')[:64]
        if not key:
            raise NomError('REQUEST', 'Lipsește cheia cererii.', 400)
        verified = scoped('receptie', data)
        with session(True) as cursor:
            invoices.require(cursor)
            anchor, _ = build(cursor, verified, 'nir', False)
            fingerprint = protocol.fingerprint('nir.distribution', data)
            replay = protocol.replayed(cursor, anchor, key, fingerprint)
            if replay is not None:
                return replay
            groups = destinations.selections(cursor, anchor, data, scoped, build, repo_of)
            with destinations.reserve(cursor, anchor, groups):
                replay = protocol.replayed(cursor, anchor, key, fingerprint)
                if replay is not None:
                    return replay
                header = parse_groups(cursor, anchor, groups, data)
                result = write(cursor, anchor, groups, header, data, key, request.headers.get('X-Request-Id'))
                protocol.remember(cursor, anchor, key, fingerprint, result)
                return result
