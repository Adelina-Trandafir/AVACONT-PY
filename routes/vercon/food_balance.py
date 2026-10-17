"""Food-list opening balance, shared by the document page and its report."""
from decimal import Decimal, localcontext

from . import report_data, stock
from .nomenclatoare import NomError


def before(source, day, exclude=None):
    # Web DEP = list minus bons; the report uses the opposite sign.
    prior = [row for row in source.rows('food_history')
             if stock.day_of(row['date']) < day
             and (exclude is None or str(row['IdLista']) != str(exclude))]
    if any(row['difference'] is None for row in prior):
        raise NomError('REPORT_SOURCE', 'O listă anterioară nu are diferența păstrată în antet.', 409)
    initial = next((Decimal(row['IntValue']) for row in source.rows('settings')
                    if row['SettingKey'] == 'SI_LA'), Decimal(0))
    with localcontext() as context:
        context.prec = 60
        return initial - sum((Decimal(row['difference']) for row in prior), Decimal(0))


def columns(amount, scale):
    return {'economy': report_data.exact(-amount, scale) if amount is not None and amount < 0 else '',
            'overrun': report_data.exact(amount, scale) if amount is not None and amount >= 0 else ''}
