"""The current number (the last one given) of every numbered document of the current gestiune and exercise (SLICE-VC06-19). Read-only.

Computed as the document editors compute their suggestion (the larger of the counter (VC_Numbering) and the largest number
really saved, plus one) minus one; 0 = none given yet. The NIR and the FCS share one calendar-year series. A MATERIALE gestiune that numbers its consumption
bons per account (consumption_numbering) gets one row for every account.
"""
from datetime import date

from . import bon_number, consumption_number, consumption_numbering, oi_number, receipt_number
from .documents import _all, _int, _one, _scope


def _top(cursor, ctx, kind, numbers):
    counter = _one(cursor, 'SELECT LastNumber FROM VC_Numbering WHERE SubunitId=%s AND GestiuneId=%s AND ExercitiuId=%s AND Kind=%s',
                   (*_scope(ctx), kind))
    return max([int((counter or {}).get('LastNumber') or 0), *[number for number in numbers if number is not None]])


def _receipts(cursor, ctx):
    year = ctx['year'] if isinstance(ctx['year'], int) else ctx['an']
    series = receipt_number.series(cursor, ctx, date(year, 12, 31))
    return max((row['Numar'] for row in series), default=0) + 1


def _consumption(cursor, ctx):
    if not consumption_numbering.per_account(cursor, ctx):
        return [{'key': 'bc', 'label': 'Bon Consum (BC)', 'next': consumption_number.check(cursor, ctx)['suggested']}]
    accounts = _all(cursor, 'SELECT IdMasina, Masina FROM VC_Masini WHERE SubunitId=%s AND GestiuneId=%s AND Activ=1 ORDER BY Masina',
                    (ctx['subunit'], ctx['gestiune']))
    return [{'key': 'bc:%d' % row['IdMasina'], 'label': 'Bon Consum (BC) - %s' % (row['Masina'] or 'fără nume'),
             'next': consumption_number.check(cursor, ctx, None, None, row['IdMasina'])['suggested']} for row in accounts]


def _returns(cursor, ctx):
    rows = _all(cursor, 'SELECT NumarBC FROM VC_Iesiri_H WHERE SubunitId=%s AND GestiuneId=%s AND ExercitiuId=%s AND COALESCE(Retur,0)=1', _scope(ctx))
    return _top(cursor, ctx, 'BR', [bon_number.parse('br', row['NumarBC']) for row in rows]) + 1


def _lists(cursor, ctx):
    rows = _all(cursor, 'SELECT NumarLA FROM VC_LABC WHERE SubunitId=%s AND GestiuneId=%s AND ExercitiuId=%s', _scope(ctx))
    return _top(cursor, ctx, 'LISTA', [_int(row['NumarLA']) for row in rows]) + 1


def overview(cursor, ctx):
    """[{key, label, current}] for the gestiune type of `ctx` (subunit, gestiune, exercitiu, type, year, an)."""
    return [{'key': row['key'], 'label': row['label'], 'current': row['next'] - 1} for row in _next_numbers(cursor, ctx)]


def _next_numbers(cursor, ctx):
    rows = [{'key': 'nir', 'label': 'Factură cumpărare (NIR) / Factură stornare (FCS)', 'next': _receipts(cursor, ctx)}]
    if ctx['type'] in ('MATERIALE', 'ALIMENTE'):
        rows += _consumption(cursor, ctx)
        rows.append({'key': 'br', 'label': 'Bon Retur (BX)', 'next': _returns(cursor, ctx)})
        if ctx['type'] == 'ALIMENTE':
            rows.append({'key': 'lista', 'label': 'Lista Alimente', 'next': _lists(cursor, ctx)})
    else:
        for family, label in (('bp', 'Bon Predare (BP)'), ('bt', 'Bon Transfer (BT)'), ('bs', 'Bon Casare (BS)')):
            rows.append({'key': family, 'label': label, 'next': oi_number.check(cursor, ctx, family)['suggested']})
    return rows
