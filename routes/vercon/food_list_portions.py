"""Restricted date corrections and portion corrections in the latest food-list month."""
from decimal import Decimal

from . import food_list_dates, numeric, protocol
from .documents import _int, _iso, _one, _scope
from .nomenclatoare import NomError


def latest_month(cursor, ctx):
    # Search the whole gestiune: opening an older exercise must not reopen its month.
    row = _one(cursor, 'SELECT MAX(DATE(DATALA)) AS day FROM VC_LABC '
                       'WHERE SubunitId=%s AND GestiuneId=%s AND Anulat=0',
               (ctx['subunit'], ctx['gestiune']))
    return _iso(row['day'])[:7] if row and row['day'] else None


def message(month):
    suffix = ' (%s)' % month if month else ''
    return ('Numărul copiilor se poate modifica numai în ultima lună cu liste de alimente%s.' % suffix)


def annotate(cursor, ctx, rows):
    month = latest_month(cursor, ctx)
    for row in rows:
        day = row.get('date') or (row.get('header') or {}).get('Data') or ''
        allowed = not row.get('annulled') and bool(month) and day[:7] == month
        row.update({'portions_editable': allowed, 'portions_month': month,
                    'portions_message': '' if allowed else message(month)})
    return food_list_dates.annotate(cursor, ctx, rows)


def require_month(cursor, ctx, *days):
    month = latest_month(cursor, ctx)
    if not month or any(not day or _iso(day)[:7] != month for day in days):
        raise NomError('LIST_PORTIONS_MONTH', message(month), 409)


def update(cursor, repo, ctx, doc_id, body, request_id):
    from .lista import MEALS, _bons_total
    from . import recalculation_audit

    heads = repo.select('VC_LABC', {'IdLista': doc_id}, for_update=True)
    if not heads:
        raise NomError('NOT_FOUND', 'Lista nu mai există.', 404)
    before = heads[0]
    if before['Anulat']:
        raise NomError('CANCELLED', 'Lista este anulată și nu mai poate fi modificată.', 409)
    version = _int(body.get('version'))
    if version is None or version != before['Version']:
        raise NomError('CONFLICT', 'Lista a fost modificată. Reîncărcați lista.', 409)
    day = food_list_dates.clean(cursor, ctx, before, body)
    if 'portions' not in body:
        if 'date' not in body:
            raise NomError('INVALID', 'Completați data listei sau numărul copiilor.', 422)
        return food_list_dates.update(cursor, repo, ctx, before, day, request_id)
    require_month(cursor, ctx, before['DATALA'], day)
    raw = body.get('portions')
    if not isinstance(raw, dict) or set(raw) != set(MEALS):
        raise NomError('INVALID', 'Completați numărul copiilor pentru fiecare masă.', 422)
    summaries = repo.select('VC_ListaAlimente', {'IDLISTA': doc_id}, for_update=True)
    if len(summaries) != 1:
        raise NomError('LIST_SUMMARY', 'Lista nu are un rezumat unic al meselor.', 409)
    summary, values, previous, total = summaries[0], {}, {}, Decimal(0)
    for key, (_, portions, price_column, value_column, _) in MEALS.items():
        count = _int(raw[key])
        if count is None or not 0 <= count <= 2147483647:
            raise NomError('INVALID', 'Numărul copiilor trebuie să fie un întreg pozitiv sau zero.',
                           422, {'m.%s.P' % key: 'Introduceți un număr întreg pozitiv sau zero.'})
        if summary.get(price_column) is None and count:
            raise NomError('LIST_PRICE', 'Lista nu are prețul păstrat pentru fiecare masă.', 409)
        try:
            price = numeric.from_stored(summary[price_column], ctx['scale']) if summary.get(price_column) is not None else Decimal(0)
            if price < 0:
                raise NomError('LIST_PRICE', 'Prețul unei mese nu poate fi negativ.', 409)
            amount = numeric.to_stored(numeric.calc_round(Decimal(count) * price, ctx['zec']), ctx['scale'])
        except numeric.NumericError as error:
            raise NomError('INVALID', str(error), 422) from error
        previous[portions], previous[value_column] = summary.get(portions), summary.get(value_column)
        values[portions], values[value_column] = count, amount
        total += amount
    try:
        total = numeric.to_stored(total, ctx['scale'])
    except numeric.NumericError as error:
        raise NomError('INVALID', str(error), 422) from error
    bons_total = _bons_total(cursor, ctx, doc_id)
    header = {'DATALA': day, 'ValoareLista': total, 'ValoareBon': bons_total, 'DEP': total - bons_total}
    prior_balances = recalculation_audit.food_snapshot(cursor, ctx)
    repo.update('VC_ListaAlimente', values, {'IDLISTA': doc_id})
    if repo.update('VC_LABC', header, {'IdLista': doc_id, 'Version': version}, bump_version=True) != 1:
        raise NomError('CONFLICT', 'Lista a fost modificată. Reîncărcați lista.', 409)
    protocol.audit(cursor, ctx, 'lista.portions_update', 'VC_LABC', doc_id,
                   {'meals': previous, **{key: before[key] for key in header}},
                   {'meals': values, **header}, None, request_id)
    origin = recalculation_audit.cause('lista', doc_id, before['NumarLA'], day, version + 1)
    recalculation_audit.record(cursor, ctx, prior_balances, recalculation_audit.food_snapshot(cursor, ctx),
                               origin, 'lista.balance_recalculated', 'VC_LABC', request_id)
    return {'id': doc_id, 'number': before['NumarLA']}
