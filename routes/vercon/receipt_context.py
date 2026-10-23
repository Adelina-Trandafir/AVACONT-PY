"""Read-only, scoped context cards beside the inline receipt editor.

Supplier card = saved invoices/storno in this exercise, not an unpaid balance.
Product card = warehouse statement of the product's received price variant.
Neither read creates a price variant or incorporates an unsaved draft.
"""
from datetime import date
from decimal import Decimal
from utils import vercon_suppliers as suppliers

from flask import request

from . import numeric, protocol, stock
from .documents import _all, _int, _iso, _one, _scope
from .nomenclatoare import NomError

try:
    import config
except ModuleNotFoundError as error:
    if error.name != 'config':
        raise
    config = None

_CACHE_ENABLED = getattr(config, 'VERCON_MINI_STOCK_CACHE_ENABLED', True)
_CACHE_SECONDS = getattr(config, 'VERCON_MINI_STOCK_CACHE_SECONDS', 120)
if not isinstance(_CACHE_ENABLED, bool):
    raise ValueError('VERCON_MINI_STOCK_CACHE_ENABLED din config.py trebuie să fie True sau False.')
if isinstance(_CACHE_SECONDS, bool) or not isinstance(_CACHE_SECONDS, int) or _CACHE_SECONDS < 0:
    raise ValueError('VERCON_MINI_STOCK_CACHE_SECONDS din config.py trebuie să fie un număr întreg de secunde, cel puțin 0.')


def cache_settings():
    """Only the mini-statement cache policy is sent to the authenticated editor."""
    return {'enabled': _CACHE_ENABLED and _CACHE_SECONDS > 0, 'ttl_seconds': _CACHE_SECONDS}


def _id(name, required=True):
    raw = request.args.get(name)
    value = _int(raw)
    if not required and raw in (None, ''):
        return None
    if value is None or value <= 0:
        raise NomError('INVALID', 'Selecția %s este invalidă.' % name, 422)
    return value


def supplier_statement(cursor, ctx, supplier, on):
    supplier = suppliers.code(supplier)
    partner = suppliers.find(cursor, supplier)
    if not partner:
        raise NomError('NOT_FOUND', 'Furnizorul nu mai există.', 404)
    # Every document and every picker uses the same fiscal identity.
    rows = _all(cursor,
        'SELECT n.IdIntrare AS id, COALESCE(n.DataFc,n.Data) AS day, n.NrFact AS invoice, '
        'n.Valoare AS amount, COALESCE(n.Storno,0) AS storno '
        'FROM VC_NIR n '
        'WHERE n.SubunitId=%s AND n.GestiuneId=%s AND n.ExercitiuId=%s '
        'AND n.Anulat=0 AND DATE(COALESCE(n.DataFc,n.Data))<=%s '
        'AND n.CodPartener=%s '
        'ORDER BY day,n.Numar,n.NumarSub', (*_scope(ctx), on, supplier))
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


def receiver_statement(cursor, ctx, receiver, on):
    """Saved consumption minus returns for the selected receiver and exercise."""
    client = _one(cursor, 'SELECT Denumire FROM VC_Clienti WHERE SubunitId=%s AND GestiuneId=%s AND IDC=%s',
                  (ctx['subunit'], ctx['gestiune'], receiver))
    if not client:
        raise NomError('NOT_FOUND', 'Gestionarul nu mai există în gestiunea curentă.', 404)
    rows = _all(cursor,
        'SELECT IdBonConsum AS id, NumarBC AS number, DataBC AS day, ValoareBC AS amount, COALESCE(Retur,0) AS retur '
        'FROM VC_Iesiri_H WHERE SubunitId=%s AND GestiuneId=%s AND ExercitiuId=%s '
        'AND IDC=%s AND Anulat=0 AND DATE(DataBC)<=%s ORDER BY DataBC,IdBonConsum',
        (*_scope(ctx), receiver, on))
    entries, total = [], Decimal(0)
    for row in rows:
        amount = Decimal(row['amount']) if row['amount'] is not None else None
        value = None if amount is None else -amount if row['retur'] else amount
        if value is not None:
            total += value
        entries.append({'key': str(row['id']), 'date': _iso(row['day']), 'number': row['number'],
                        'kind': 'br' if row['retur'] else 'bc', 'value': None if value is None else format(value, 'f')})
    return {'receiver': receiver, 'name': client['Denumire'] or '', 'date': on.isoformat(),
            'rows': list(reversed(entries[-12:])), 'count': len(entries), 'total': format(total, 'f'), 'zec': ctx['zec']}


def register(bp, guard, scoped, session, build, need):
    def statement_family(family):
        if family not in ('nir', 'storno', 'bc'):
            raise NomError('FAMILY_UNKNOWN', 'Fișa nu este disponibilă pentru acest document.', 404)
        need(family)

    @bp.get('/api/vercon/doc/bc/receiver-statement')
    @guard('read')
    def consumption_receiver_statement():
        need('bc')
        vctx = scoped('read')
        receiver = _id('receiver')
        on = protocol.to_date(request.args.get('date'), 'Data fișei')
        with session(False) as cursor:
            ctx, _ = build(cursor, vctx, 'bc', False)
            return receiver_statement(cursor, ctx, receiver, on)

    @bp.get('/api/vercon/doc/<family>/supplier-statement')
    @guard('read')
    def receipt_supplier_statement(family):
        if family == 'bc':
            raise NomError('FAMILY_UNKNOWN', 'Bonul de consum folosește fișa gestionarului.', 404)
        statement_family(family)
        vctx = scoped('read')
        supplier = suppliers.code(request.args.get('supplier'))
        if not supplier or len(supplier) > 50:
            raise NomError('INVALID', 'Cod fiscal de furnizor invalid.', 422)
        on = protocol.to_date(request.args.get('date'), 'Data fișei')
        with session(False) as cursor:
            ctx, _ = build(cursor, vctx, family, False)
            return supplier_statement(cursor, ctx, supplier, on)

    @bp.get('/api/vercon/doc/<family>/product-statement')
    @guard('read')
    def receipt_product_statement(family):
        statement_family(family)
        vctx = scoped('read')
        if family in ('storno', 'bc'):
            preferred = _id('variant')
            on = protocol.to_date(request.args.get('date'), 'Data fișei')
            with session(False) as cursor:
                ctx, _ = build(cursor, vctx, family, False)
                from .stock_source import variants
                item = variants(cursor, ctx).get(preferred)
                if not item:
                    raise NomError('INVALID', 'Produsul nu aparține gestiunii curente.', 422)
                if family == 'bc':
                    from .consumption_stock import summary
                    return summary(cursor, ctx, item, preferred, on, _id('exclude', False))
                data = stock.read(cursor, ctx, date(min(int(ctx['year'] or on.year), on.year), 1, 1), on, variant=preferred)
                movements = data['statement']
                return {'product': item['product'], 'name': item['name'], 'um': item['um'], 'price': format(item['price'] or 0, 'f'),
                        'variant': preferred, 'date': on.isoformat(), 'count': len(movements), 'zec': ctx['zec'],
                        'rows': [{**row, 'key': '%s:%s:%s' % (row['kind'], row['document_id'], row['line_id'])} for row in reversed(movements[-20:])],
                        'closing': data['rows'][0]['quantities']['closing'] if data['rows'] else '0'}
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
