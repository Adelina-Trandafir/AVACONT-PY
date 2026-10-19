"""Draft validation and common invoices. Read-only previews never reserve numbers or create variants."""
from decimal import Decimal

from flask import request

from . import documents as d, receipts, receipt_rules, receipt_number, receipt_auto, receipt_adjustment, invoices, protocol
from .nomenclatoare import NomError


def register(bp, guard, scoped, session, build, repo_of, need):
    @bp.post('/api/vercon/doc/nir/validate')
    @guard('read')
    def validate_receipt():
        need('nir')
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise NomError('INVALID', 'Ciornă invalidă.', 400)
        vctx = scoped('read', data)
        with session(False) as cursor:
            invoices.require(cursor)
            ctx, exercise = build(cursor, vctx, 'nir', False)
            repo = repo_of(cursor, ctx)
            doc_id = d._int(data.get('id'))
            stored = None
            previous = None
            if doc_id:
                previous = receipts.saved_document(cursor, repo, ctx, doc_id)
                if previous['annulled'] or d._int(data.get('version')) != previous['version']:
                    raise NomError('CONFLICT', 'Documentul este anulat sau revizia lui s-a schimbat.', 409)
                d._guard_dependents(cursor, ctx, 'nir', doc_id, 'edit')
                stored = {r['id']: r for r in previous['lines']}
            if data.get('action') == 'adjust':
                selected = str(data.get('line_key') or '')
                for raw in data.get('lines', []):
                    if not stored and (not selected or str(raw.get('key')) == selected):
                        raw['recalculate'] = True
            parsed = receipts._clean_nir(cursor, repo, ctx, exercise, data, 'nir', stored=stored, allow_empty=data.get('action') != 'adjust')
            receipt_adjustment.prepare(parsed, stored or {})
            if data.get('action') == 'adjust' and previous and previous.get('invoice') and not previous['invoice']['local']:
                target = previous['invoice']['total']
                if target is None:
                    raise NomError('INVALID', 'Introduceți valoarea facturii comune înainte de corecție.', 422)
                external = sum((Decimal(row['ValoareCUTVA'] or 0) for row in previous.get('other_lines', [])), Decimal(0))
                parsed['declared'] = Decimal(str(target)) - external
            header = data.get('header') or {}
            if parsed['cols']['TipDoc'] != 'STOC_INITIAL':
                changed = not previous or (str(header.get('Data')), str(header.get('Numar')), d._int(header.get('NumarSub')) or 0) != (
                    previous['header']['Data'], str(previous['number']), previous.get('sub_number') or 0)
                if changed:
                    verdict = receipt_number.check(cursor, ctx, header.get('Numar'), doc_id,
                                                   d._int(header.get('NumarSub')) or 0, header.get('Data'))
                    if not verdict['valid'] or verdict['duplicate']:
                        raise NomError('INVALID', 'Numărul NIR este invalid sau deja folosit.', 422,
                                       {'h.Numar': 'Numărul NIR este invalid sau deja folosit.'})
            if data.get('action') == 'adjust':
                parsed = receipt_rules.adjust(parsed, ctx, data.get('line_key'))
            elif data.get('action') == 'undo_adjustment':
                parsed = receipt_auto.undo(parsed, data.get('line_key'), ctx, doc_id, d._int(data.get('version')))
            receipt_rules.simulate(cursor, repo, ctx, parsed, doc_id)
            return receipt_rules.serialize(parsed, ctx, doc_id)

    @bp.get('/api/vercon/invoices')
    @guard('read')
    def list_invoices():
        need('nir')
        scoped('read')
        with session(False) as cursor:
            invoices.require(cursor)
            rows = invoices.candidates(cursor, d._int(request.args.get('partner')), request.args.get('number'), request.args.get('date'))
            return {'invoices': [{**r, 'date': d._iso(r['date']), 'total': None if r['total'] is None else format(r['total'], 'f')} for r in rows]}

    @bp.get('/api/vercon/invoices/<int:identity>')
    @guard('read')
    def get_invoice(identity):
        need('nir')
        scoped('read')
        with session(False) as cursor:
            invoices.require(cursor)
            return invoices.read(cursor, identity)

    def mutate(action):
        need('nir')
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise NomError('INVALID', 'Cerere invalidă.', 400)
        vctx = scoped('receptie', data)
        key = str(data.get('request_key') or '')[:64]
        if not key:
            raise NomError('REQUEST', 'Lipsește cheia cererii.', 400)
        fingerprint = protocol.fingerprint('invoice.' + action, {k: v for k, v in data.items() if k != 'request_key'})
        with session(True) as cursor:
            invoices.require(cursor)
            ctx, _ = build(cursor, vctx, 'nir', action not in ('backfill', 'merge'))
            replay = protocol.replayed(cursor, ctx, key, fingerprint)
            if replay is not None:
                return replay
            rid = request.headers.get('X-Request-Id')
            if action == 'save':
                result = invoices.save(cursor, ctx, data, rid)
            elif action == 'merge':
                result = invoices.merge(cursor, ctx, data, rid)
            elif action == 'backfill':
                result = invoices.backfill(cursor, ctx, rid)
            else:
                identity = d._int(data.get('id'))
                invoices.load(cursor, identity, data.get('version'), True)
                invoices.refresh_state(cursor, identity, strict=True)
                protocol.audit(cursor, ctx, 'invoice.finalize', 'VC_FACTURI', identity, None, {'state': 'RECONCILIATA'}, key, rid)
                result = invoices.read(cursor, identity)
            protocol.remember(cursor, ctx, key, fingerprint, result)
            return result

    @bp.post('/api/vercon/invoices/save')
    @guard('receptie')
    def save_invoice():
        return mutate('save')

    @bp.post('/api/vercon/invoices/merge')
    @guard('receptie')
    def merge_invoices():
        return mutate('merge')

    @bp.post('/api/vercon/invoices/backfill')
    @guard('receptie')
    def backfill_invoices():
        return mutate('backfill')

    @bp.post('/api/vercon/invoices/finalize')
    @guard('receptie')
    def finalize_invoice():
        return mutate('finalize')

    @bp.post('/api/vercon/invoices/receive-batch')
    @guard('receptie')
    def receive_invoice_batch():
        """An explicitly submitted set is all-or-nothing; each destination supplies its own valid lease."""
        need('nir')
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or not isinstance(data.get('receipts'), list) or not 1 <= len(data['receipts']) <= 100:
            raise NomError('INVALID', 'Lotul trebuie să conțină între 1 și 100 de NIR-uri.', 422)
        key = str(data.get('request_key') or '')[:64]
        invoice_id = d._int(data.get('invoice_id'))
        if not key or not invoice_id:
            raise NomError('INVALID', 'Factura și cheia lotului sunt obligatorii.', 422)
        prepared = []
        for item in data['receipts']:
            if not isinstance(item, dict) or not item.get('lock_token') or not item.get('context_version'):
                raise NomError('LOCK_LOST', 'Fiecare destinație cere tokenul de ocupare și revizia contextului.', 409)
            verified = scoped('receptie', item, lock_token=item['lock_token'], version=item['context_version'])
            prepared.append((item, verified))
        prepared.sort(key=lambda pair: (pair[1]['selection']['subunit'], pair[1]['selection']['gestiune'], d._int(pair[0].get('id')) or 0))
        with session(True) as cursor:
            invoices.require(cursor)
            contexts = [(item, build(cursor, verified, 'nir', True)) for item, verified in prepared]
            for subunit, gestiune in sorted({(ctx['subunit'], ctx['gestiune']) for _, (ctx, _) in contexts}):
                d._one(cursor, 'SELECT GestiuneId FROM VC_Gestiuni WHERE SubunitId=%s AND GestiuneId=%s FOR UPDATE', (subunit, gestiune))
            first_ctx = contexts[0][1][0]
            fingerprint = protocol.fingerprint('invoice.batch', data)
            replay = protocol.replayed(cursor, first_ctx, key, fingerprint)
            if replay is not None:
                return replay
            invoices.load(cursor, invoice_id, data.get('version'), True)
            results = []
            for item, (ctx, exercise) in contexts:
                header = item.get('header') if isinstance(item.get('header'), dict) else {}
                body = {**item, 'header': {**header, 'FacturaId': invoice_id}}
                doc_id = d._int(item.get('id'))
                if doc_id:
                    result = receipts.edit_nir(cursor, repo_of(cursor, ctx), ctx, exercise, doc_id, body, request.headers.get('X-Request-Id'))
                else:
                    result = receipts.create_nir(cursor, repo_of(cursor, ctx), ctx, exercise, body, key, request.headers.get('X-Request-Id'))
                results.append({**result, 'subunit': ctx['subunit'], 'gestiune': ctx['gestiune']})
            for item, _ in prepared:
                scoped('receptie', item, lock_token=item['lock_token'], version=item['context_version'])
            result = {'invoice': invoices.read(cursor, invoice_id), 'receipts': results}
            protocol.remember(cursor, first_ctx, key, fingerprint, result)
            return result
