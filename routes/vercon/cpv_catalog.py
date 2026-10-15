"""Read-only CPV catalogue: direct branches and bounded flat search, no schema writes."""
from collections import OrderedDict
import re
from threading import Lock
from time import monotonic

from .nomenclatoare import NomError

_LEVEL = 'CASE ' + ' '.join("WHEN NULLIF(L%d,'') IS NOT NULL THEN %d" % (level, level)
                           for level in range(1, 8)) + ' END'
_NODES = 'WITH NODES AS (SELECT CPV, Expl, ' + _LEVEL + ' AS LVL FROM AVACONT_COMUN.CPV WHERE CPV IS NOT NULL) '
_CACHE = OrderedDict()
_LOCK = Lock()


def _branch(cursor, parent):
    with _LOCK:
        cached = _CACHE.get(parent)
        if cached and monotonic() < cached[0]:
            _CACHE.move_to_end(parent)
            return cached[1]
    params = ()
    condition = 'c.LVL=1'
    if parent:
        if not re.fullmatch(r'\d{8}-\d', parent):
            raise NomError('INVALID', 'Cod CPV invalid.', 422)
        cursor.execute('SELECT CPV, ' + _LEVEL + ' AS LVL FROM AVACONT_COMUN.CPV WHERE CPV=%s LIMIT 1', (parent,))
        node = cursor.fetchone()
        if not node or node['LVL'] is None:
            raise NomError('NOT_FOUND', 'Codul CPV nu mai există.', 404)
        level = int(node['LVL'])
        # A skipped level attaches to the closest existing ancestor, not to an invented node.
        condition = ('c.LVL>%s AND c.CPV LIKE %s AND NOT EXISTS '
                     '(SELECT 1 FROM NODES a WHERE a.LVL>%s AND a.LVL<c.LVL '
                     'AND LEFT(a.CPV,a.LVL+1)=LEFT(c.CPV,a.LVL+1))')
        params = (level, parent[:level + 1] + '%', level)
    cursor.execute(_NODES +
                   'SELECT c.CPV, c.Expl, c.LVL, EXISTS(SELECT 1 FROM NODES d '
                   'WHERE d.LVL>c.LVL AND LEFT(d.CPV,c.LVL+1)=LEFT(c.CPV,c.LVL+1)) AS HAS_CHILDREN '
                   'FROM NODES c WHERE ' + condition + ' ORDER BY c.CPV', params)
    result = {'items': [{'code': row['CPV'], 'name': row['Expl'] or '', 'has_children': bool(row['HAS_CHILDREN'])}
                        for row in cursor.fetchall()], 'mode': 'tree'}
    with _LOCK:
        _CACHE[parent] = (monotonic() + 300, result)
        _CACHE.move_to_end(parent)
        while len(_CACHE) > 512:
            _CACHE.popitem(last=False)
    return result


def read(cursor, parent=None, name='', code=''):
    name, code = name.strip(), code.strip()
    if max(len(name), len(code)) > 160:
        raise NomError('INVALID', 'Căutarea poate avea cel mult 160 de caractere pe câmp.', 422)
    if not name and not code:
        return _branch(cursor, parent or None)
    terms, params = ['CPV IS NOT NULL'], []
    for column, value in (('Expl', name), ('CPV', code)):
        if value:
            terms.append(column + " LIKE %s ESCAPE '!'")
            params.append('%' + value.replace('!', '!!').replace('%', '!%').replace('_', '!_') + '%')
    where = ' AND '.join(terms)
    cursor.execute('SELECT COUNT(*) AS TOTAL FROM AVACONT_COMUN.CPV WHERE ' + where, params)
    total = int(cursor.fetchone()['TOTAL'])
    cursor.execute('SELECT CPV, Expl FROM AVACONT_COMUN.CPV WHERE ' + where + ' ORDER BY CPV LIMIT 200', params)
    items = [{'code': row['CPV'], 'name': row['Expl'] or '', 'has_children': False} for row in cursor.fetchall()]
    return {'items': items, 'mode': 'search', 'total': total, 'remaining': max(0, total - len(items))}


def detail(cursor, code):
    cursor.execute('SELECT CPV, Expl FROM AVACONT_COMUN.CPV WHERE CPV=%s LIMIT 1', (code,))
    row = cursor.fetchone()
    if not row:
        raise NomError('NOT_FOUND', 'Codul CPV nu mai există în nomenclator.', 404)
    return {'code': row['CPV'], 'name': row['Expl'] or ''}


def validate(cursor, code):
    cursor.execute('SELECT CPV FROM AVACONT_COMUN.CPV WHERE CPV=%s LIMIT 1', (code,))
    if not cursor.fetchone():
        raise NomError('INVALID', 'Alegeți un cod existent în nomenclatorul CPV.', 422, {'CPV': 'Cod CPV inexistent.'})
