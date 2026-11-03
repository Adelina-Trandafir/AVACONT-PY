"""BC edits start from the closing stock of the month before the active month.

The caller holds the warehouse lock and has already checked the active month.
Older movements contribute to opening stock; their intermediate balances are
not validated again. The active month's and later movements remain chronological.
"""
from datetime import timedelta
from decimal import Decimal, localcontext

from . import protocol, stock, stock_source
from .nomenclatoare import NomError


def require_month(cursor, ctx, variants, month_start):
    variants = {int(variant) for variant in variants if variant is not None}
    if not variants:
        return
    movements = stock_source.warehouse(cursor, ctx, variants)
    opening = {variant: Decimal(0) for variant in variants}
    current = {variant: [] for variant in variants}
    with localcontext() as precision:
        precision.prec = 60
        for movement in movements:
            day, variant = stock.day_of(movement['d']), movement['p']
            if day < month_start:
                opening[variant] += movement['q']
            else:
                current[variant].append((day, movement['q']))
        for variant in sorted(variants):
            balance = opening[variant]
            if balance < 0:
                closed_day = month_start - timedelta(days=1)
                raise NomError('STOCK_NEGATIVE',
                               'Stocul la sfârșitul lunii anterioare este negativ pentru produsul %s: %s la data %s. '
                               'Corectați soldul de pornire înainte de modificarea bonului.' % (
                                   protocol._label(cursor, ctx, variant), protocol._plain(balance, ctx['scale']),
                                   closed_day.strftime('%d.%m.%Y')), 409,
                               {'product': variant, 'date': closed_day.isoformat(),
                                'stock_period_start': month_start.isoformat()})
            for day, quantity in protocol._by_day(current[variant]):
                balance += quantity
                if balance < 0:
                    raise NomError('STOCK_NEGATIVE',
                                   'Stoc negativ interzis: produsul %s ar ajunge la %s la data %s. '
                                   'Calculul pornește de la stocul ultimei luni închise.' % (
                                       protocol._label(cursor, ctx, variant), protocol._plain(balance, ctx['scale']),
                                       day.strftime('%d.%m.%Y')), 409,
                                   {'product': variant, 'date': day.isoformat(),
                                    'stock_period_start': month_start.isoformat()})
