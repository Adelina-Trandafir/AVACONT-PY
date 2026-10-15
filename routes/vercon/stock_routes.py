"""VC08-01: stock HTTP contracts; reuse document session/context resolution."""
from flask import request

from . import numeric, protocol, stock, stock_source
from .nomenclatoare import NomError


def optional_id(name):
    raw = request.args.get(name)
    if raw is None or raw == '':
        return None
    if name == 'category' and raw == 'none':
        return 0
    try:
        value = int(raw)
        if value <= 0:
            raise ValueError()
        return value
    except (ValueError, TypeError):
        raise NomError('INVALID', 'Filtrul %s este invalid.' % name, 422) from None


def register(bp, guard, scoped, session, build):
    @bp.get('/api/vercon/stock/balance')
    @guard('raport')
    def stock_balance():
        vctx = scoped('raport')
        start = protocol.to_date(request.args.get('start'), 'Data de început')
        end = protocol.to_date(request.args.get('end'), 'Data finală')
        if start > end:
            raise NomError('INVALID', 'Intervalul de calcul este invalid.', 422)
        filters = {name: optional_id(name) for name in ('product', 'variant', 'category', 'supplier', 'holder')}
        with session(False) as cursor:
            ctx, _ = build(cursor, vctx, None, False)
            return stock.read(cursor, ctx, start, end, ledger=request.args.get('ledger', 'warehouse'),
                              group=request.args.get('group', 'variant'), **filters)

    @bp.post('/api/vercon/stock/draft-available')
    @guard('read')
    def stock_draft_available():
        """Read-only POST so a large draft does not enter a URL or affect saved stock."""
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or not isinstance(body.get('lines', []), list):
            raise NomError('INVALID', 'Draftul este invalid.', 422)
        vctx = scoped('read', body)
        on = protocol.to_date(body.get('date'), 'Data')
        exclude = body.get('exclude')
        if exclude is not None:
            try:
                if not isinstance(exclude, dict) or exclude['kind'] not in ('nir', 'storno', 'bc', 'br'):
                    raise ValueError()
                exclude = (exclude['kind'], int(exclude['id']))
                if exclude[1] <= 0:
                    raise ValueError()
            except (KeyError, TypeError, ValueError):
                raise NomError('INVALID', 'Documentul exclus este invalid.', 422) from None
        with session(False) as cursor:
            ctx, _ = build(cursor, vctx, None, False)
            draft = {}
            try:
                for line in body.get('lines', []):
                    if not isinstance(line, dict):
                        raise ValueError()
                    if isinstance(line.get('variant'), bool) or not isinstance(line.get('quantity'), (str, int)) or isinstance(line.get('quantity'), bool):
                        raise ValueError()
                    product = int(line['variant'])
                    quantity = numeric.to_stored(line['quantity'], ctx['scale'])
                    if product <= 0 or quantity < 0:
                        raise ValueError()
                    draft[product] = draft.get(product, 0) + quantity
            except (KeyError, TypeError, ValueError, ArithmeticError):
                raise NomError('INVALID', 'Cantitățile draftului nu sunt valide la scara gestiunii.', 422) from None
            metadata = stock_source.variants(cursor, ctx)
            if any(product not in metadata for product in draft):
                raise NomError('INVALID', 'O variantă nu aparține gestiunii curente.', 422)
            movements = stock_source.warehouse(cursor, ctx, draft, exclude)
            quantities = stock.available(movements, on, draft)
        return {'date': on.isoformat(), 'scale': ctx['scale'], 'zec': ctx['zec'], 'informative': True,
                'available': {str(key): stock.quantity_text(value, ctx['scale']) for key, value in quantities.items()}}
