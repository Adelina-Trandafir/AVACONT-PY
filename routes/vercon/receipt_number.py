"""Read-only number proposals. Saving still allocates/checks inside its transaction."""
from . import documents


def check(cursor, ctx, raw=None, exclude=None, sub=0):
    cursor.execute('SELECT LastNumber FROM VC_Numbering WHERE SubunitId=%s AND GestiuneId=%s '
                   'AND ExercitiuId=%s AND Kind=%s', (*documents._scope(ctx), 'NIR'))
    counter = cursor.fetchone()
    proposed = int(counter['LastNumber'] if counter else 0) + 1
    while documents._nir_number_taken(cursor, ctx, proposed, False, exclude):
        proposed += 1
    manual = documents._int(raw)
    empty = raw in (None, '')
    sub = sub if isinstance(sub, int) else 0
    valid = (empty and not sub) or manual is not None and manual > 0 and 0 <= sub <= 99
    duplicate = bool(valid and not empty and documents._nir_number_taken(cursor, ctx, manual, False, exclude, sub))
    return {'suggested': proposed, 'valid': valid, 'duplicate': duplicate, 'reserved': False}
