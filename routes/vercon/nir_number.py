"""NIR date changes force a decimal number only when a later NIR exists."""
from . import documents as d, protocol, receipt_number
from .nomenclatoare import NomError


def _fraction(rows, day):
    before = [(row['Numar'], row['sub']) for row in rows if row['day'] and row['day'] <= day]
    after = [(row['Numar'], row['sub']) for row in rows if row['day'] and row['day'] > day]
    upper = min(after)
    number, sub = max(before, default=(max(1, upper[0] - 1), 0))
    occupied = {(row['Numar'], row['sub']) for row in rows}
    # Continue tenths (26,1 -> 26,2), or hundredths when those are in use.
    # A narrower gap may require a hundredth (26,1 < 26,11 < 26,2).
    preferred = sub + (10 if sub % 10 == 0 else 1)
    candidates = [preferred, *range(sub + 1, 100)]
    for fraction in candidates:
        pair = (number, fraction)
        if 0 < fraction <= 99 and pair < upper and pair not in occupied:
            return pair
    message = 'Nu există un subnumăr zecimal liber pentru Data NIR aleasă în seria comună NIR/FCS.'
    raise NomError('NIR_CHRONOLOGY', message, 422, {'h.Data': message})


def check(cursor, ctx, raw=None, exclude=None, sub=0, on=None):
    day = protocol.to_date(on, 'Data NIR') if on else protocol.to_date('%s-12-31' % ctx['year'], 'Data NIR')
    rows = receipt_number.series(cursor, ctx, day)
    active = [row for row in rows if row['IdIntrare'] != exclude]
    locked = any(row['day'] and row['day'] > day and not row['storno'] for row in active)
    empty = raw in (None, '')
    if locked:
        number, fraction = _fraction(active, day)
        valid = True
    else:
        # A supplied number/fraction stays untouched when no later NIR exists.
        # Only an empty new form needs an initial whole-number suggestion.
        number = max((row['Numar'] for row in rows), default=0) + 1 if empty else d._int(raw)
        fraction = 0 if empty else sub
        valid = number is not None and number > 0 and 0 <= fraction <= 99 and (not empty or not sub)
    duplicate = bool(valid and any((row['Numar'], row['sub']) == (number, fraction) for row in active))
    return {'suggested': number, 'suggested_sub': fraction, 'number_locked': locked,
            'valid': valid, 'duplicate': duplicate, 'reserved': False}


def assign(cursor, ctx, raw, sub, day, exclude=None):
    d._one(cursor, 'SELECT GestiuneId FROM VC_Gestiuni WHERE SubunitId=%s AND GestiuneId=%s FOR UPDATE',
           (ctx['subunit'], ctx['gestiune']))
    result = check(cursor, ctx, raw, exclude, sub, day.isoformat())
    if not result['valid'] or result['duplicate']:
        message = 'Numărul NIR este invalid sau deja folosit în seria comună NIR/FCS.'
        raise NomError('INVALID', message, 422, {'h.Numar': message})
    if result['suggested_sub'] and not d._numarsub_column(cursor, ctx):
        message = 'Subnumerele NIR nu sunt disponibile în această bază de date.'
        raise NomError('INVALID', message, 422, {'h.Numar': message})
    return result['suggested'], result['suggested_sub']
