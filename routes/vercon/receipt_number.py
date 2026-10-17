"""Calendar-year receipt series. Cancelled numbers retain their positions."""
from . import documents as d, protocol
from .nomenclatoare import NomError


def series(cursor, ctx, day):
    return d._all(cursor, 'SELECT IdIntrare, ABS(Numar) AS Numar, COALESCE(NumarSub,0) AS sub, DATE(Data) AS day FROM VC_NIR '
                  "WHERE SubunitId=%s AND GestiuneId=%s AND YEAR(Data)=%s AND Numar<>0 AND TipDoc<>'STOC_INITIAL'",
                  (ctx['subunit'], ctx['gestiune'], day.year))


def suggestion(rows, day):
    """Place an inserted FCS after the last earlier number, using hundredths."""
    before = [r for r in rows if r['day'] and r['day'] <= day]
    after = [r for r in rows if r['day'] and r['day'] > day]
    if not after:
        return max((r['Numar'] for r in rows), default=0) + 1, 0
    upper = min((r['Numar'], r['sub']) for r in after)
    lower = max(((r['Numar'], r['sub']) for r in before), default=(max(1, upper[0] - 1), 0))
    occupied = {(r['Numar'], r['sub']) for r in rows}
    if not before and lower not in occupied and lower < upper:
        return lower
    # Prefer tenths (12,1); use a hundredth when a tenth would cross the next number.
    candidates = [(lower[0], k) for k in range(lower[1] + 1, 100)]
    candidates.sort(key=lambda pair: (pair[1] % 10 != 0, pair[1]))
    for pair in candidates:
        if lower < pair < upper and pair not in occupied:
            return pair
    message = 'Nu mai există un sub-număr liber la data aleasă în seria comună NIR/FCS.'
    raise NomError('NIR_CHRONOLOGY', message, 422, {'h.Data': message})


def check(cursor, ctx, raw=None, exclude=None, sub=0, on=None, insert=False):
    day = protocol.to_date(on, 'Data NIR') if on else protocol.to_date('%s-12-31' % ctx['year'], 'Data NIR')
    rows = series(cursor, ctx, day)
    active = [r for r in rows if r['IdIntrare'] != exclude]
    proposed, proposed_sub = suggestion(active, day) if insert and raw in (None, '') else (max((r['Numar'] for r in rows), default=0) + 1, 0)
    manual = d._int(raw)
    empty = raw in (None, '')
    valid = (empty and not sub) or manual is not None and manual > 0 and 0 <= sub <= 99
    duplicate = bool(valid and not empty and any(r['IdIntrare'] != exclude and (r['Numar'], r['sub']) == (manual, sub) for r in rows))
    bounds = {}
    if valid and not duplicate:
        from .receipt_rules import chronology
        bounds = chronology(cursor, ctx, proposed if empty else manual, proposed_sub if empty else sub, day, exclude)
    return {'suggested': proposed, 'suggested_sub': proposed_sub, 'valid': valid, 'duplicate': duplicate, 'reserved': False, **bounds}


def assign_pair(cursor, ctx, raw, sub, day, exclude=None):
    """FCS auto-allocation includes the fractional part in the same locked series."""
    d._one(cursor, 'SELECT GestiuneId FROM VC_Gestiuni WHERE SubunitId=%s AND GestiuneId=%s FOR UPDATE',
           (ctx['subunit'], ctx['gestiune']))
    result = check(cursor, ctx, raw, exclude, sub, day.isoformat(), insert=raw in (None, ''))
    if not result['valid'] or result['duplicate']:
        message = 'Numărul este invalid sau deja folosit în seria comună NIR/FCS din acest an.'
        raise NomError('INVALID', message, 422, {'h.Numar': message})
    number, fraction = (result['suggested'], result['suggested_sub']) if raw in (None, '') else (d._int(raw), sub)
    if fraction and not d._numarsub_column(cursor, ctx):
        message = 'Sub-numerele NIR/FCS nu sunt disponibile în această bază de date.'
        raise NomError('INVALID', message, 422, {'h.Numar': message})
    return number, fraction


def assign(cursor, ctx, raw, sub, day, exclude=None):
    d._one(cursor, 'SELECT GestiuneId FROM VC_Gestiuni WHERE SubunitId=%s AND GestiuneId=%s FOR UPDATE',
           (ctx['subunit'], ctx['gestiune']))
    result = check(cursor, ctx, raw, exclude, sub, day.isoformat())
    if not result['valid'] or result['duplicate']:
        message = 'Numărul NIR/FCS este invalid sau deja folosit în acest an.'
        raise NomError('INVALID', message, 422, {'h.Numar': message})
    return result['suggested'] if raw in (None, '') else d._int(raw)
