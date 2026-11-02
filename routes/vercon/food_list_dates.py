"""Food-list date corrections that retain the list's chronological position."""
from . import protocol
from .documents import _all, _iso, _scope
from .nomenclatoare import NomError


def neighbours(cursor, ctx):
    # The list number is the order (the internal ID never is): a list may sit anywhere between the dates of
    # the lists just before and after it by number, even when its current date is out of line.
    rows = _all(cursor, 'SELECT IdLista AS id, DATE(DATALA) AS day, NumarLA AS number FROM VC_LABC '
                'WHERE SubunitId=%s AND GestiuneId=%s AND ExercitiuId=%s AND Anulat=0 '
                'ORDER BY NumarLA, IdLista', _scope(ctx))
    return {row['id']: (rows[index - 1] if index else None,
                        rows[index + 1] if index + 1 < len(rows) else None)
            for index, row in enumerate(rows)}


def annotate(cursor, ctx, rows):
    bounds = neighbours(cursor, ctx)
    for row in rows:
        previous, following = bounds.get(row['id'], (None, None))
        row.update({'date_editable': not row.get('annulled') and row['id'] in bounds,
                    'date_minimum': _iso(previous['day']) if previous else None,
                    'date_maximum': _iso(following['day']) if following else None})
    return rows


def clean(cursor, ctx, before, body):
    day = protocol.to_date(body.get('date', _iso(before['DATALA'])), 'Data listei', {'an': ctx['an']})
    if day == protocol.to_date(_iso(before['DATALA']), 'Data listei'):
        return day
    bounds = neighbours(cursor, ctx)
    if before['IdLista'] not in bounds:
        raise NomError('CONFLICT', 'Lista nu mai este activă. Reîncărcați lista.', 409)
    previous, following = bounds[before['IdLista']]
    if (previous and previous['day'] and day < previous['day']
            or following and following['day'] and day > following['day']):
        low = previous['day'].strftime('%d.%m.%Y') if previous and previous['day'] else 'începutul anului'
        high = following['day'].strftime('%d.%m.%Y') if following and following['day'] else 'sfârșitul anului'
        message = 'Data listei trebuie să fie între %s și %s, păstrând ordinea numerelor.' % (low, high)
        raise NomError('DOCUMENT_ORDER', message, 422, {'h.Data': message})
    return day


def update(cursor, repo, ctx, before, day, request_id):
    from . import recalculation_audit

    prior_balances = recalculation_audit.food_snapshot(cursor, ctx)
    if repo.update('VC_LABC', {'DATALA': day},
                   {'IdLista': before['IdLista'], 'Version': before['Version']}, bump_version=True) != 1:
        raise NomError('CONFLICT', 'Lista a fost modificată. Reîncărcați lista.', 409)
    protocol.audit(cursor, ctx, 'lista.date_update', 'VC_LABC', before['IdLista'],
                   {'date': before['DATALA']}, {'date': day}, None, request_id)
    origin = recalculation_audit.cause('lista', before['IdLista'], before['NumarLA'], day, before['Version'] + 1)
    recalculation_audit.record(cursor, ctx, prior_balances, recalculation_audit.food_snapshot(cursor, ctx),
                               origin, 'lista.balance_recalculated', 'VC_LABC', request_id)
    return {'id': before['IdLista'], 'number': before['NumarLA']}
