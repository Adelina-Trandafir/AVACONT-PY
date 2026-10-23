"""BC edit permissions for the active month; cancellation keeps its own rule.

All writes using these guards already hold the gestiune lock. Permission reads
are repeated at save time; annotations are only hints for the document editor.
"""
from .nomenclatoare import NomError


def active_month(cursor, ctx):
    if ctx['type'] == 'ALIMENTE':
        cursor.execute('SELECT MAX(DATE(DATALA)) AS day FROM VC_LABC '
                       'WHERE SubunitId=%s AND GestiuneId=%s AND Anulat=0',
                       (ctx['subunit'], ctx['gestiune']))
        row = cursor.fetchone()
        if row and row['day']:
            return str(row['day'])[:7]
    # Materiale, or an Alimente gestiune with no active food list yet.
    # Search all exercises so selecting an old year cannot reopen its month.
    cursor.execute('SELECT MAX(DATE(DataBC)) AS day FROM VC_Iesiri_H '
                   'WHERE SubunitId=%s AND GestiuneId=%s AND Anulat=0 AND COALESCE(Retur,0)=0',
                   (ctx['subunit'], ctx['gestiune']))
    row = cursor.fetchone()
    return str(row['day'])[:7] if row and row['day'] else None


def month_message(month):
    suffix = ' (%s)' % month if month else ''
    return 'Bonurile de consum se pot modifica numai în luna activă%s.' % suffix


def later_exercise(cursor, ctx):
    # The stock ledger is scoped to one exercise. Do not alter an earlier year
    # once a later exercise has warehouse documents whose carry-forward may depend on it.
    cursor.execute('SELECT e.ExercitiuId FROM VC_Exercitii e WHERE e.SubunitId=%s '
                   'AND e.GestiuneId=%s AND e.An>%s AND ('
                   'EXISTS (SELECT 1 FROM VC_NIR n WHERE n.SubunitId=e.SubunitId '
                   'AND n.GestiuneId=e.GestiuneId AND n.ExercitiuId=e.ExercitiuId AND n.Anulat=0) OR '
                   'EXISTS (SELECT 1 FROM VC_Iesiri_H h WHERE h.SubunitId=e.SubunitId '
                   'AND h.GestiuneId=e.GestiuneId AND h.ExercitiuId=e.ExercitiuId AND h.Anulat=0)) LIMIT 1',
                   (ctx['subunit'], ctx['gestiune'], ctx['an']))
    return cursor.fetchone() is not None


def dependencies(cursor, ctx):
    scope = (ctx['subunit'], ctx['gestiune'], ctx['exercitiu'])
    cursor.execute('SELECT IdRetur FROM VC_Iesiri_H WHERE SubunitId=%s AND GestiuneId=%s '
                   'AND ExercitiuId=%s AND Anulat=0 AND COALESCE(Retur,0)=1 AND IdRetur IS NOT NULL', scope)
    returned = {row['IdRetur'] for row in cursor.fetchall()}
    cursor.execute('SELECT l.IdBonConsum,h.IdLista,DATE(h.DATALA) AS day,h.Anulat '
                   'FROM VC_LegBCLA l LEFT JOIN VC_LABC h ON h.SubunitId=l.SubunitId '
                   'AND h.GestiuneId=l.GestiuneId AND h.ExercitiuId=l.ExercitiuId AND h.IdLista=l.IdLista '
                   'WHERE l.SubunitId=%s AND l.GestiuneId=%s AND l.ExercitiuId=%s', scope)
    linked = {}
    for row in cursor.fetchall():
        linked.setdefault(row['IdBonConsum'], []).append(row)
    return returned, linked


def issue(row, month, returned, linked, later=False):
    if row.get('annulled'):
        return 'CANCELLED', 'Documentul este anulat și nu mai poate fi modificat.'
    day = row.get('date') or (row.get('header') or {}).get('Data') or ''
    if not month or day[:7] != month:
        return 'BC_MONTH', month_message(month)
    if later:
        return 'BC_LATER_EXERCISE', 'Există documente de stoc într-un exercițiu ulterior. Bonurile anului anterior nu se mai pot modifica.'
    if row['id'] in returned:
        return 'HAS_RETUR', 'Bonul are retur: anulați mai întâi returul.'
    for item in linked.get(row['id'], []):
        if item['IdLista'] is None or item['Anulat'] or not item['day']:
            return 'LINKED_LIST', 'Lista asociată nu mai este disponibilă.'
        if str(item['day'])[:7] != month:
            return 'LINKED_LIST', 'Bonul este asociat unei liste din afara lunii active. ' + month_message(month)
    return None


def annotate(cursor, ctx, rows):
    month = active_month(cursor, ctx)
    returned, linked = dependencies(cursor, ctx)
    later = later_exercise(cursor, ctx)
    for row in rows:
        blocked = issue(row, month, returned, linked, later)
        row.update({'edit_locked': blocked is not None, 'edit_message': blocked[1] if blocked else '',
                    'edit_month': month})
    return rows


def require_edit(cursor, ctx, doc_id):
    cursor.execute('SELECT IdBonConsum AS id,DATE(DataBC) AS day,Anulat FROM VC_Iesiri_H '
                   'WHERE SubunitId=%s AND GestiuneId=%s AND ExercitiuId=%s '
                   'AND IdBonConsum=%s AND COALESCE(Retur,0)=0',
                   (ctx['subunit'], ctx['gestiune'], ctx['exercitiu'], doc_id))
    row = cursor.fetchone()
    if not row:
        raise NomError('NOT_FOUND', 'Bonul nu mai există.', 404)
    month = active_month(cursor, ctx)
    returned, linked = dependencies(cursor, ctx)
    blocked = issue({'id': row['id'], 'date': str(row['day'])[:10] if row['day'] else '',
                     'annulled': bool(row['Anulat'])}, month, returned, linked, later_exercise(cursor, ctx))
    if blocked:
        raise NomError(blocked[0], blocked[1], 409)


def require_date(cursor, ctx, day):
    month = active_month(cursor, ctx)
    if not month or str(day)[:7] != month:
        raise NomError('BC_MONTH', month_message(month), 422,
                       {'h.Data': 'Data bonului trebuie să rămână în luna activă.'})
