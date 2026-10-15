"""Products used in the selected working year, independently of their active flag."""
from datetime import date

from . import protocol, stock_source
from .nomenclatoare import NomError


def used_products(cursor, vctx):
    sel, gest = vctx['selection'], vctx['gestiune']
    year = sel['year']
    if not isinstance(year, int):
        raise NomError('NO_EXERCISE', 'Alegeți anul produselor.', 409)
    exercise = protocol.exercise_of(cursor, sel['subunit'], sel['gestiune'], gest['type'], year)
    parts = []
    for _, lines, a, header, h, key, day, _, _, valid, _ in stock_source.WAREHOUSE:
        parts.append(
            'SELECT DISTINCT p.ID FROM {lines} {a} JOIN {header} {h} '
            'ON {h}.SubunitId={a}.SubunitId AND {h}.GestiuneId={a}.GestiuneId '
            'AND {h}.ExercitiuId={a}.ExercitiuId AND {h}.{key}={a}.{key} '
            'JOIN VC_ProduseP p ON p.SubunitId={a}.SubunitId AND p.GestiuneId={a}.GestiuneId '
            'AND p.IdProdus={a}.IdProdus WHERE {a}.SubunitId=%(s)s AND {a}.GestiuneId=%(g)s '
            'AND {a}.ExercitiuId=%(e)s AND {day}>=%(start)s AND {day}<%(end)s AND {valid}'.format(
                lines=lines, a=a, header=header, h=h, key=key, day=day, valid=valid))
    if gest['type'] in ('OI', 'MF'):
        for table, day in (('VC_OI_Predare', 'DATABP'), ('VC_OI_Transfer', 'DATABT'), ('VC_OI_Casare', 'DATABC')):
            parts.append(
                'SELECT DISTINCT p.ID FROM {table} m JOIN VC_ProduseP p '
                'ON p.SubunitId=m.SubunitId AND p.GestiuneId=m.GestiuneId AND p.IdProdus=m.IdProdus '
                'WHERE m.SubunitId=%(s)s AND m.GestiuneId=%(g)s AND m.ExercitiuId=%(e)s '
                'AND m.{day}>=%(start)s AND m.{day}<%(end)s AND m.Anulat=0'.format(table=table, day=day))
    cursor.execute(' UNION '.join(parts), {'s': sel['subunit'], 'g': sel['gestiune'], 'e': exercise['id'],
                   'start': date(year, 1, 1), 'end': date(year + 1, 1, 1)})
    return {row['ID'] for row in cursor.fetchall()}
