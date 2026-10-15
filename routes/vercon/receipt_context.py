"""Read-only, scoped context cards beside the inline receipt editor.

Supplier card = saved invoices/storno in this exercise, not an unpaid balance.
Product card = warehouse statement of the product's received price variant.
Neither read creates a price variant or incorporates an unsaved draft.
"""
from datetime import date
from decimal import Decimal

from flask import request

from . import numeric, protocol, stock
from .documents import _all, _int, _iso, _one, _scope
from .nomenclatoare import NomError


def _id(name, required=True):
    raw = request.args.get(name)
    value = _int(raw)
    if not required and raw in (None, ''):
        return None
    if value is None or value <= 0:
        raise NomError('INVALID', 'Selecția %s este invalidă.' % name, 422)
    return value


def supplier_statement(cursor, ctx, supplier, on):
    partner = _one(cursor, 'SELECT IdPartener, DenumirePartener, CodFiscal FROM Parteneri WHERE IdPartener=%s', (supplier,))
    if not partner:
        raise NomError('NOT_FOUND', 'Furnizorul nu mai există.', 404)
    # The picker deduplicates fiscal codes; include documents of the other identities of that same supplier.
    rows = _all(cursor,
        'SELECT n.IdIntrare AS id, COALESCE(n.DataFc,n.Data) AS day, n.NrFact AS invoice, '
        'n.Valoare AS amount, COALESCE(n.Storno,0) AS storno '
        'FROM VC_NIR n LEFT JOIN Parteneri p ON p.IdPartener=n.IdPartener '
        'WHERE n.SubunitId=%s AND n.GestiuneId=%s AND n.ExercitiuId=%s '
        'AND n.Anulat=0 AND DATE(COALESCE(n.DataFc,n.Data))<=%s '
        "AND (n.IdPartener=%s OR (NULLIF(TRIM(%s),'') IS NOT NULL AND TRIM(p.CodFiscal)=TRIM(%s))) "
        'ORDER BY day,n.IdIntrare', (*_scope(ctx), on, supplier, partner['CodFiscal'], partner['CodFiscal']))
    total = Decimal(0)
    entries = []
    for row in rows:
        amount = Decimal(row['amount']) if row['amount'] is not None else None
        if amount is not None:
            total += -amount if row['storno'] else amount
        entries.append({'key': str(row['id']), 'date': _iso(row['day']), 'invoice': row['invoice'] or '—',
                        'kind': 'Storno' if row['storno'] else 'Factură',
                        'value': None if amount is None else format(-amount if row['storno'] else amount, 'f')})
    return {'supplier': supplier, 'name': partner['DenumirePartener'] or '', 'fiscal_code': partner['CodFiscal'] or '',
            'date': on.isoformat(), 'rows': list(reversed(entries[-12:])), 'count': len(entries), 'total': format(total, 'f'),
            'missing_values': sum(row['amount'] is None for row in rows), 'zec': ctx['zec']}


def product_statement(cursor, ctx, product, net_price, vat, on, preferred=None):
    item = _one(cursor, 'SELECT ID, DenumireP, UM FROM VC_Produse WHERE SubunitId=%s AND GestiuneId=%s AND ID=%s',
                (ctx['subunit'], ctx['gestiune'], product))
    tax = _one(cursor, 'SELECT TVA FROM VC_TVA WHERE SubunitId=%s AND GestiuneId=%s AND IdTVA=%s',
               (ctx['subunit'], ctx['gestiune'], vat))
    if not item or not tax or tax['TVA'] is None:
        raise NomError('INVALID', 'Produsul sau cota TVA nu aparține gestiunii curente.', 422)
    price = numeric.exact_decimal(net_price)
    if not price.is_finite() or price < 0:
        raise NomError('INVALID', 'Completați un preț valid.', 422)
    price = numeric.to_stored(price, ctx['scale'])
    gross = numeric.calc_round(price * (1 + Decimal(tax['TVA'])), ctx['zec'])
    variants = _all(cursor,
        'SELECT IdProdus FROM VC_ProduseP WHERE SubunitId=%s AND GestiuneId=%s AND ID=%s '
        'AND PUTVA=%s AND Activ=1 ORDER BY IdProdus', (ctx['subunit'], ctx['gestiune'], product, gross))
    ids = [row['IdProdus'] for row in variants]
    # MF identities are distinct assets even when their product and price coincide.
    variant = preferred if preferred in ids else (min(ids) if ids and ctx['type'] != 'MF' else None)
    result = {'product': product, 'name': item['DenumireP'] or '', 'um': item['UM'] or '',
              'price': format(gross, 'f'), 'variant': variant, 'date': on.isoformat(),
              'rows': [], 'count': 0, 'closing': '0', 'zec': ctx['zec']}
    if variant is None:
        return result
    start = date(min(int(ctx['year'] or on.year), on.year), 1, 1)
    data = stock.read(cursor, ctx, start, on, variant=variant)
    movements = data['statement']
    result.update({'rows': [{**row, 'key': '%s:%s:%s' % (row['kind'], row['document_id'], row['line_id'])}
                           for row in reversed(movements[-20:])], 'count': len(movements),
                   'closing': data['rows'][0]['quantities']['closing'] if data['rows'] else '0'})
    return result


def register(bp, guard, scoped, session, build, need):
    @bp.get('/api/vercon/doc/nir/supplier-statement')
    @guard('read')
    def receipt_supplier_statement():
        need('nir')
        vctx = scoped('read')
        supplier = _id('supplier')
        on = protocol.to_date(request.args.get('date'), 'Data fișei')
        with session(False) as cursor:
            ctx, _ = build(cursor, vctx, 'nir', False)
            return supplier_statement(cursor, ctx, supplier, on)

    @bp.get('/api/vercon/doc/nir/product-statement')
    @guard('read')
    def receipt_product_statement():
        need('nir')
        vctx = scoped('read')
        product, vat, preferred = _id('product'), _id('vat'), _id('variant', False)
        on = protocol.to_date(request.args.get('date'), 'Data fișei')
        try:
            price = numeric.exact_decimal(request.args.get('price'))
            if not price.is_finite() or price < 0:
                raise ValueError()
            numeric.to_stored(price)
        except (ValueError, ArithmeticError):
            raise NomError('INVALID', 'Completați un preț valid.', 422) from None
        with session(False) as cursor:
            ctx, _ = build(cursor, vctx, 'nir', False)
            return product_statement(cursor, ctx, product, price, vat, on, preferred)
