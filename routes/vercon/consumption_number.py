"""Read-only BC number suggestions; allocation remains in the existing save path."""
from . import bon_number, consumption_numbering
from .documents import _all, _one, _scope, _int, _bon_number_taken


def check(cursor, ctx, raw=None, exclude=None, account=None):
    """`account` = the account chosen on the draft; it matters only when the gestiune numbers its BC per account."""
    scoped = account if account is not None and consumption_numbering.per_account(cursor, ctx) else None
    rows = _all(cursor, 'SELECT NumarBC FROM VC_Iesiri_H WHERE SubunitId=%s AND GestiuneId=%s '
                'AND ExercitiuId=%s AND COALESCE(Retur,0)=0' + (' AND IdMasina=%s' if scoped is not None else ''),
                (*_scope(ctx), *([scoped] if scoped is not None else [])))
    counter = _one(cursor, 'SELECT LastNumber FROM VC_Numbering WHERE SubunitId=%s AND GestiuneId=%s '
                   'AND ExercitiuId=%s AND Kind=%s', (*_scope(ctx), consumption_numbering.kind(scoped)))
    top = max([int((counter or {}).get('LastNumber') or 0),
               *[number for row in rows if (number := bon_number.parse('bc', row['NumarBC'])) is not None]])
    manual = raw not in (None, '')
    number = _int(raw) if manual else None
    valid = not manual or number is not None and number > 0
    return {'suggested': top + 1, 'valid': valid, 'per_account': scoped is not None,
            'duplicate': bool(manual and valid and _bon_number_taken(cursor, ctx, 'bc', number, exclude, scoped))}
