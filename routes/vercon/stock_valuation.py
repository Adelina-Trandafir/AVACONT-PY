"""Read historical signed prices exactly; missing prices need an identified correction."""
from decimal import Decimal, InvalidOperation
from .nomenclatoare import NomError


def price(info):
    raw = info.get('price')
    try:
        value = Decimal(str(raw)) if raw is not None else None
    except InvalidOperation:
        value = None
    if value is not None and value.is_finite():
        # Migration deliberately preserves negative prices. Reading a report must
        # not reject them, take abs(), or alter the historical valuation.
        return value
    name = info.get('name') or 'Produs fără denumire'
    variant = info.get('variant', info.get('id', '—'))
    code = ' / cod ' + str(info['code']) if info.get('code') else ''
    account = info.get('account') or info.get('category_name') or 'Fără cont'
    raise NomError('STOCK_PRICE_INVALID',
                   f'Preț de evaluare lipsă sau nevalid: {name}; varianta {variant}{code}; cont {account}. '
                   'Identificați produsul în Configurări → Produse și varianta în fișa prețurilor. '
                   'Prețul acestei variante trebuie completat din datele sursă; apoi regenerați raportul. '
                   'Un preț lipsă nu poate fi înlocuit automat cu zero.', 409,
                   {'variant': variant, 'product': info.get('product'), 'code': info.get('code'),
                    'price': str(raw) if raw is not None else None})
