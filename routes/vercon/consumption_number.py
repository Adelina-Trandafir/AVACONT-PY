"""Read-only BC number suggestions; allocation remains in the existing save path."""
from .documents import _all, _one, _scope, _int, _bon_number_taken


def check(cursor, ctx, raw=None, exclude=None):
    rows = _all(cursor, 'SELECT NumarBC FROM VC_Iesiri_H WHERE SubunitId=%s AND GestiuneId=%s '
                'AND ExercitiuId=%s AND COALESCE(Retur,0)=0', _scope(ctx))
    counter = _one(cursor, 'SELECT LastNumber FROM VC_Numbering WHERE SubunitId=%s AND GestiuneId=%s '
                   "AND ExercitiuId=%s AND Kind='BC'", _scope(ctx))
    top = max([int((counter or {}).get('LastNumber') or 0),
               *[number for row in rows if (number := _int(row['NumarBC'])) is not None]])
    manual = raw not in (None, '')
    number = _int(raw) if manual else None
    valid = not manual or number is not None and number > 0
    return {'suggested': top + 1, 'valid': valid,
            'duplicate': bool(manual and valid and _bon_number_taken(cursor, ctx, str(number), False, exclude))}
