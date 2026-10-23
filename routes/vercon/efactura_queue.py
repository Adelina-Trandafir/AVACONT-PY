"""SLICE-VC06-07 / VC06-08 (EV04, EV05, plan docs/EV_PLAN.md): the received e-invoices an accountant SENT to VERCON from K-BOT.

K-BOT writes EF_PrimiteVercon (the sending), EF_PrimiteVerconLinii (ALL the lines of the invoice, each with the account the accountant proposes, NULL = the
Administrator chooses) and, here, EF_PrimiteVerconNir (the NIRs made from it). They live in the same unit database as the VC_* tables, so nothing is called
between systems. This module only READS the queue, RETURNS an invoice with a reason, and CLOSES a sending when the NIR made from it is saved.

    GET  /api/vercon/doc/nir/efactura                        the sendings that wait (state TRIMISA, or PRELUATA whose NIRs are all cancelled / gone)
    GET  /api/vercon/doc/nir/efactura/<id>                   one sending: the invoice header, the matched supplier, the lines with the proposed accounts
    POST /api/vercon/doc/nir/efactura/<id>/return            {reason}: the sending goes back to the accountant (RETURNATA)

The sendings are not tied to a gestiune (the Administrator has all of them): the list is the unit's. A NIR saved with `efactura: <id>` in its body closes the sending
INSIDE the same transaction (`close`, called by receipt_distribution.save): if the sending is no longer waiting, the NIR is not saved either.

The tables belong to K-BOT (sql/EV_01_primite_vercon.sql). Without them the list is empty and says so (`available` False): nothing fails.
"""
from decimal import Decimal

from flask import request
from utils import vercon_efactura as association
from utils import vercon_suppliers as suppliers

from . import protocol, efactura_visibility as visibility
from .documents import _all, _one, _int, _scope
from .nomenclatoare import NomError

TABLES = ('EF_Primite', 'EF_PrimiteLinii', 'EF_PrimiteTVA', 'EF_PrimiteVercon', 'EF_PrimiteVerconLinii', 'EF_PrimiteVerconNir')
MAX_REASON = 500
MIN_REASON = 3

# The CUI of a partner, normalised the way EF_Primite.CuiNormalizat is (no spaces / signs, no leading «RO» before a digit); same expression as K-BOT's primite.py.
_PARTNER_CUI = association.fiscal_sql('p.CodFiscal')

# A sending waits when it is TRIMISA, or PRELUATA with no NIR left that exists and is not cancelled (the rule of K-BOT's primite_vercon.stare_efectiva).
_PENDING = ("(t.Stare = 'TRIMISA' OR (t.Stare = 'PRELUATA' AND NOT EXISTS ("
            "SELECT 1 FROM EF_PrimiteVerconNir n JOIN VC_NIR v ON v.SubunitId = n.SubunitId AND v.GestiuneId = n.GestiuneId "
            "AND v.ExercitiuId = n.ExercitiuId AND v.IdIntrare = n.IdIntrare WHERE n.IdTrimitere = t.IdTrimitere AND COALESCE(v.Anulat, 0) = 0)))")


def available(cursor):
    rows = _all(cursor, 'SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN (%s,%s,%s,%s,%s,%s)', TABLES)
    return {r['TABLE_NAME'].upper() for r in rows} == {t.upper() for t in TABLES}


def _iso(value):
    return value.isoformat() if hasattr(value, 'isoformat') else None


def _text(value):
    return None if value is None else format(Decimal(value), 'f')


def listing(cursor, summary=False, ctx=None):
    """Pending shared sendings; an inactive invoice is hidden for the entire unit."""
    if not available(cursor):
        return {'available': False, 'visibility_available': False, 'items': [], 'pending_count': 0}
    visibility_ready = visibility.ready(cursor)
    hidden = visibility.inactive_ids(cursor) if visibility_ready else set()
    mode = 'active' if summary else request.args.get('visibility', 'active')
    if mode not in ('active', 'inactive', 'all'):
        raise NomError('INVALID', 'Alegeți facturi Active, Inactive sau Toate.', 422)
    filters, params = [], []
    association_mode = not summary and ('nir' in request.args or request.args.get('include_completed') == '1')
    if association_mode:
        nir_id = _int(request.args.get('nir'))
        if ctx is None or nir_id is None or nir_id <= 0:
            raise NomError('INVALID', 'Reîncărcați pagina și deschideți asocierea din NIR-ul salvat. Lista necesită NIR-ul curent.', 422)
        supplier = association.receipt_supplier_sql(cursor)
        nir = _one(cursor, 'SELECT n.*,' + supplier + ' AS EF_CUI FROM VC_NIR n '
                   'WHERE n.SubunitId=%s AND n.GestiuneId=%s AND n.ExercitiuId=%s AND n.IdIntrare=%s', (*_scope(ctx), nir_id))
        if not nir or nir['Anulat'] or nir.get('Storno') or nir.get('TipDoc') == 'STOC_INITIAL':
            raise NomError('INVALID', 'Alegeți un NIR salvat, neanulat, de cumpărare.', 422)
        if not nir['EF_CUI']:
            raise NomError('INVOICE_SUPPLIER',
                           'NIR-ul nu are un cod fiscal disponibil pentru filtrarea eFacturilor. '
                           'Lista nu poate fi deschisă pentru toți furnizorii.', 422)
        if not nir['DataFc']:
            raise NomError('INVOICE_DATE', 'NIR-ul nu are Data facturii completată.', 422)
        filters.extend((association.fiscal_sql('p.CuiNormalizat') + '=%s', 'DATE(p.DataFact)=%s'))
        params.extend((nir['EF_CUI'], _iso(nir['DataFc'])[:10]))
    if not summary and not association_mode and request.args.get('date'):
        filters.append('p.DataFact=%s')
        params.append(request.args['date'])
    if not summary and not association_mode and request.args.get('fiscal_code'):
        filters.append(association.fiscal_sql('p.CuiNormalizat') + '=%s')
        params.append(suppliers.code(request.args['fiscal_code']))
    rows = _all(cursor,
                "SELECT t.IdTrimitere, t.IdPrimita, t.Stare, t.Observatie, t.UnTrimis, t.DataTrimis, p.NrFact, p.DataFact, p.DenumireP, p.CuiNormalizat, p.Total, p.Semn, "
                "(SELECT COUNT(*) FROM EF_PrimiteVerconLinii l WHERE l.IdTrimitere = t.IdTrimitere) AS Linii, "
                + _PENDING + ' AS PendingLegacy '
                + "FROM EF_PrimiteVercon t JOIN EF_Primite p ON p.IdPrimita = t.IdPrimita WHERE t.Stare IN ('TRIMISA','PRELUATA') "
                + ('AND ' + ' AND '.join(filters) + ' ' if filters else '')
                + "ORDER BY t.DataTrimis DESC, p.DenumireP, p.NrFact", tuple(params))
    rows = [r for r in rows if mode == 'all' or (r['IdTrimitere'] in hidden) == (mode == 'inactive')]
    states = association.progress_states(cursor) if rows else {}
    for row in rows:
        if row['IdTrimitere'] in states:
            row['effective'] = states[row['IdTrimitere']]
            row['manual_links'] = True
        else:
            row['effective'] = 'TRIMISA' if row['PendingLegacy'] else 'PRELUATA'
            row['manual_links'] = False
    pending_count = sum(r['effective'] != 'PRELUATA' for r in rows)
    if summary:
        return {'available': True, 'visibility_available': visibility_ready, 'pending_count': pending_count}
    if request.args.get('include_completed') != '1':
        # Hidden invoices must remain reachable for restoration even if meanwhile completed.
        rows = [r for r in rows if r['effective'] != 'PRELUATA' or r['IdTrimitere'] in hidden]
    items = [{'id': r['IdTrimitere'], 'invoice_id': r['IdPrimita'], 'number': r['NrFact'], 'date': _iso(r['DataFact']), 'supplier': r['DenumireP'],
              'cui': r['CuiNormalizat'], 'total': _text(Decimal(r['Total'] or 0) * int(r['Semn'] or 1)), 'lines': r['Linii'], 'note': r['Observatie'] or '',
              'sent_by': r['UnTrimis'] or '', 'sent_at': _iso(r['DataTrimis']), 'again': r['Stare'] == 'PRELUATA' and r['effective'] == 'TRIMISA',
              'state': r['effective'], 'manual_links': r['manual_links'], 'inactive': r['IdTrimitere'] in hidden} for r in rows]
    return {'available': True, 'visibility_available': visibility_ready, 'items': items, 'pending_count': pending_count}


def _pending(cursor, sending_id, lock=False):
    if lock:
        # Same lock order as the accountant's withdrawal/change-account operations.
        _one(cursor, 'SELECT p.IdPrimita FROM EF_Primite p JOIN EF_PrimiteVercon t ON t.IdPrimita=p.IdPrimita WHERE t.IdTrimitere=%s FOR UPDATE', (sending_id,))
    row = _one(cursor,
               "SELECT t.IdTrimitere, t.IdPrimita, t.Stare, t.Observatie, t.UnTrimis, t.DataTrimis FROM EF_PrimiteVercon t WHERE t.IdTrimitere = %s AND t.Stare IN ('TRIMISA','PRELUATA')"
               + (' FOR UPDATE' if lock else ''), (sending_id,))
    if row:
        progress = association.progress(cursor, sending_id)
        if progress is not None:
            if progress['state'] == 'PRELUATA':
                row = None
        elif not _one(cursor, 'SELECT 1 FROM EF_PrimiteVercon t WHERE t.IdTrimitere=%s AND ' + _PENDING, (sending_id,)):
            row = None
    if not row:
        raise NomError('NOT_FOUND', 'Factura nu mai este trimisă de contabil (a fost retrasă sau preluată).', 409)
    return row


def _rates(cursor, invoice_id):
    return _all(cursor, 'SELECT CotaTVA, Baza FROM EF_PrimiteTVA WHERE IdPrimita = %s', (invoice_id,))


def detail(cursor, sending_id):
    """The invoice and its lines as the editor of «Adaugare NIR» needs them: the taxable price per unit, the quantity, the VAT rate (the line's own, else the rate
    of the biggest taxable amount of the invoice: `rate_guessed`), and the account proposed (NULL = none)."""
    if not available(cursor):
        raise NomError('NOT_AVAILABLE', 'Facturile din e-Factura nu sunt disponibile în această bază de date.', 503)
    sending = _pending(cursor, sending_id)
    if association.progress(cursor, sending_id) is not None:
        raise NomError('EF_PARTIAL_LINKS', 'Factura folosește asocierea pe poziții. Introduceți NIR-ul manual, apoi folosiți «Asociază eFactura…» din NIR-ul salvat pentru cantitățile rămase.', 409)
    invoice = _one(cursor, 'SELECT * FROM EF_Primite WHERE IdPrimita = %s', (sending['IdPrimita'],))
    if not invoice:
        raise NomError('NOT_FOUND', 'Factura nu mai există.', 404)
    partner = suppliers.find(cursor, invoice['CuiNormalizat'], active_only=True)
    rates = _rates(cursor, invoice['IdPrimita'])
    biggest = max(rates, key=lambda r: Decimal(r['Baza'] or 0), default=None)
    fallback = Decimal(biggest['CotaTVA']) if biggest else None
    lines = _all(cursor,
                 'SELECT l.IdLinie, l.NrLinie, l.Denumire, l.Unit, l.Cant, l.Pret, l.Valoare, l.CotaTVA, v.IdMasina FROM EF_PrimiteLinii l '
                 'LEFT JOIN EF_PrimiteVerconLinii v ON v.IdTrimitere = %s AND v.IdLinie = l.IdLinie WHERE l.IdPrimita = %s ORDER BY l.IdLinie',
                 (sending_id, invoice['IdPrimita']))
    out = []
    for line in lines:
        own = line['CotaTVA']
        rate = Decimal(own) if own is not None else fallback
        out.append({'line_id': line['IdLinie'], 'nr': line['NrLinie'] or '', 'name': line['Denumire'] or '', 'um': line['Unit'] or '', 'qty': _text(line['Cant']),
                    'price': _text(line['Pret']), 'value': _text(line['Valoare']), 'rate': _text(rate), 'rate_guessed': own is None and len(rates) > 1,
                    'account': line['IdMasina']})
    sign = int(invoice['Semn'] or 1)
    return {'id': sending['IdTrimitere'], 'invoice_id': invoice['IdPrimita'], 'number': invoice['NrFact'], 'date': _iso(invoice['DataFact']),
            'supplier': invoice['DenumireP'], 'cui': invoice['CuiNormalizat'], 'total': _text(Decimal(invoice['Total'] or 0) * sign),
            'partner': {'cf': partner['CodFiscal'], 'name': partner['DenumirePartener']} if partner else None,
            'note': sending['Observatie'] or '', 'sent_by': sending['UnTrimis'] or '', 'sent_at': _iso(sending['DataTrimis']),
            'again': sending['Stare'] == 'PRELUATA', 'lines': out, 'accounts': sorted({line['IdMasina'] for line in lines if line['IdMasina'] is not None})}


def send_back(cursor, anchor, sending_id, reason, request_id=None):
    """The Administrator returns the sending to the accountant, with a reason. The row stays (RETURNATA); the accountant sees the reason and can send it again."""
    text = (reason or '').strip() if isinstance(reason, str) else ''
    if len(text) < MIN_REASON or len(text) > MAX_REASON:
        raise NomError('INVALID', 'Scrieți motivul returnării (între %d și %d de caractere).' % (MIN_REASON, MAX_REASON), 422, {'reason': 'Motivul este obligatoriu.'})
    sending = _pending(cursor, sending_id, lock=True)
    progress = association.progress(cursor, sending_id)
    if progress is not None and progress['used']:
        raise NomError('EF_PARTIAL_LINKS', 'Factura are cantități asociate unor NIR-uri. Eliminați asocierile înainte de returnare.', 409)
    cursor.execute("UPDATE EF_PrimiteVercon SET Stare = 'RETURNATA', MotivReturnare = %s, UnReturnat = %s, DataReturnat = NOW() WHERE IdTrimitere = %s",
                   (text, anchor['email'], sending_id))
    protocol.audit(cursor, anchor, 'efactura.return', 'EF_PrimiteVercon', sending_id, {'state': sending['Stare']}, {'state': 'RETURNATA', 'reason': text}, None, request_id)
    return {'id': sending_id, 'state': 'RETURNATA'}


def close(cursor, anchor, sending_id, receipts, request_id=None):
    """Called INSIDE the transaction that saves the NIR(s) made from the sending: it must still wait; it becomes PRELUATA and the NIRs are recorded.
    `receipts` = the saved NIRs ({id, subunit, gestiune, exercitiu}). A sending that no longer waits stops the whole save (nothing is written)."""
    if not available(cursor):
        raise NomError('NOT_AVAILABLE', 'Facturile din e-Factura nu sunt disponibile în această bază de date.', 503)
    sending = _pending(cursor, sending_id, lock=True)
    if association.progress(cursor, sending_id) is not None:
        raise NomError('EF_PARTIAL_LINKS', 'Asociați NIR-ul salvat pe poziții; factura folosește asocierea parțială.', 409)
    cursor.execute("UPDATE EF_PrimiteVercon SET Stare = 'PRELUATA', UnPreluat = %s, DataPreluat = NOW() WHERE IdTrimitere = %s", (anchor['email'], sending_id))
    for item in receipts:
        cursor.execute('INSERT IGNORE INTO EF_PrimiteVerconNir (IdTrimitere, SubunitId, GestiuneId, ExercitiuId, IdIntrare) VALUES (%s,%s,%s,%s,%s)',
                       (sending_id, item['subunit'], item['gestiune'], item['exercitiu'], item['id']))
    protocol.audit(cursor, anchor, 'efactura.take', 'EF_PrimiteVercon', sending_id, {'state': sending['Stare']},
                   {'state': 'PRELUATA', 'receipts': [item['id'] for item in receipts]}, None, request_id)


def register(bp, guard, scoped, session, build, need):
    @bp.get('/api/vercon/doc/nir/efactura')
    @guard('read')
    def efactura_list():
        need('nir')
        verified = scoped('read')
        with session(False) as cursor:
            ctx, _ = build(cursor, verified, 'nir', False)
            return listing(cursor, ctx=ctx)

    @bp.get('/api/vercon/doc/nir/efactura/notice')
    @guard('read')
    def efactura_notice():
        need('nir')
        verified = scoped('read')
        with session(False) as cursor:
            build(cursor, verified, 'nir', False)
            return listing(cursor, summary=True)

    @bp.get('/api/vercon/doc/nir/efactura/<int:sending_id>')
    @guard('read')
    def efactura_detail(sending_id):
        need('nir')
        scoped('read')
        with session(False) as cursor:
            return detail(cursor, sending_id)

    @bp.post('/api/vercon/doc/nir/efactura/<int:sending_id>/return')
    @guard('receptie')
    def efactura_return(sending_id):
        need('nir')
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise NomError('INVALID', 'Cerere invalidă.', 400)
        verified = scoped('receptie', data)
        with session(True) as cursor:
            if not available(cursor):
                raise NomError('NOT_AVAILABLE', 'Facturile din e-Factura nu sunt disponibile în această bază de date.', 503)
            anchor, _ = build(cursor, verified, 'nir', False)
            return send_back(cursor, anchor, sending_id, data.get('reason'), request.headers.get('X-Request-Id'))
