"""Selected BC variant: complete warehouse quantities through the bon date."""
from decimal import Decimal, localcontext
from . import stock, stock_source
from .documents import _one, _scope
from .nomenclatoare import NomError


def summary(cursor, ctx, item, variant, on, exclude=None):
    if exclude is not None and not _one(cursor,
            'SELECT IdBonConsum FROM VC_Iesiri_H WHERE SubunitId=%s AND GestiuneId=%s '
            'AND ExercitiuId=%s AND IdBonConsum=%s AND COALESCE(Retur,0)=0', (*_scope(ctx), exclude)):
        raise NomError('INVALID', 'Bonul exclus nu aparține contextului curent.', 422)
    movements = stock_source.warehouse(cursor, ctx, [variant], ('bc', exclude) if exclude else None)
    incoming, outgoing = Decimal(0), Decimal(0)
    with localcontext() as precision:
        precision.prec = 60
        for movement in movements:
            if stock.day_of(movement['d']) > on:
                continue
            if movement['q'] is None:
                raise NomError('REPORT_SOURCE', 'O mișcare nu are cantitate.', 409)
            quantity = Decimal(str(movement['q']))
            incoming += max(quantity, 0)
            outgoing += max(-quantity, 0)
        closing = incoming - outgoing
    return {'variant': variant, 'product': item['product'], 'name': item['name'], 'um': item['um'],
            'price': format(item['price'] or 0, 'f'), 'date': on.isoformat(), 'zec': ctx['zec'],
            'incoming': format(incoming, 'f'), 'outgoing': format(outgoing, 'f'),
            'closing': format(closing, 'f'), 'excluded_bc': exclude}
