"""Independent P/T/C number suggestions; read-only until the document is saved."""
from . import bon_number
from .documents import _int, _one, _scope


def check(cursor, ctx, family, raw=None, exclude=None):
    from .oi import SPEC
    spec = SPEC[family]
    cursor.execute('SELECT DISTINCT {number} AS number,{identity} AS identity FROM {table} '
                   'WHERE SubunitId=%s AND GestiuneId=%s AND ExercitiuId=%s'.format(
                       number=spec['num'], identity=spec['key'], table=spec['table']), _scope(ctx))
    rows = [(bon_number.parse(family, row['number']), row['identity']) for row in cursor.fetchall()]
    counter = _one(cursor, 'SELECT LastNumber FROM VC_Numbering WHERE SubunitId=%s AND GestiuneId=%s '
                   'AND ExercitiuId=%s AND Kind=%s', (*_scope(ctx), spec['kind']))
    top = max([int((counter or {}).get('LastNumber') or 0), *[number for number, _ in rows if number is not None]])
    manual = raw not in (None, '')
    number = _int(raw) if manual else None
    return {'suggested': top + 1, 'valid': not manual or number is not None and number > 0,
            'duplicate': manual and number is not None and any(number == saved and identity != exclude for saved, identity in rows)}
