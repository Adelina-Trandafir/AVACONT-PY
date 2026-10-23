"""Reverse chronological cancellation and full food-list edits.

The route holds the gestiune row lock for writes. Cancelled records are skipped;
within one day the larger primary key is the last record. No transaction is opened here.
"""
from datetime import date

from .nomenclatoare import NomError
from . import consumption_period

SPECS = {
    'bc': ('VC_Iesiri_H', 'IdBonConsum', 'DataBC', 'NumarBC', ' AND COALESCE(Retur,0)=0', 'bon de consum'),
    'lista': ('VC_LABC', 'IdLista', 'DATALA', 'NumarLA', '', 'listă de alimente'),
}


def latest(cursor, ctx, family, exclude=None):
    table, pk, day, number, extra, _ = SPECS[family]
    sql = (f'SELECT {pk} AS id, DATE({day}) AS day, {number} AS number FROM {table} '
           'WHERE SubunitId=%s AND GestiuneId=%s AND ExercitiuId=%s AND Anulat=0' + extra)
    params = [ctx['subunit'], ctx['gestiune'], ctx['exercitiu']]
    if exclude is not None:
        sql += f' AND {pk}<>%s'
        params.append(exclude)
    cursor.execute(sql + f' ORDER BY DATE({day}) DESC, {pk} DESC LIMIT 1', params)
    return cursor.fetchone()


def message(family, last):
    label = SPECS[family][-1]
    suffix = (' (nr. %s, %s)' % (last['number'], last['day'].strftime('%d.%m.%Y') if last['day'] else 'fără dată')) if last else ''
    if family == 'bc':
        return 'Anularea este permisă numai pentru ultimul bon de consum%s.' % suffix
    return ('Modificarea și anularea sunt permise numai în ordine inversă cronologică. '
            'Se poate modifica sau anula doar ultimul document de tip %s%s.' % (label, suffix))


def require_latest(cursor, ctx, family, doc_id):
    last = latest(cursor, ctx, family)
    if not last or last['id'] != doc_id:
        raise NomError('DOCUMENT_ORDER', message(family, last), 409)


def require_position(cursor, ctx, family, doc_id, day):
    """Changing the latest record's date must not move it before its predecessor."""
    previous = latest(cursor, ctx, family, exclude=doc_id)
    if previous and (day, doc_id) < (previous['day'] or date.min, previous['id']):
        raise NomError('DOCUMENT_ORDER', 'Data modificată trebuie să păstreze documentul ultimul în ordine cronologică.',
                       422, {'h.Data': 'Data este înaintea documentului precedent.'})


def annotate_rows(cursor, ctx, family, rows):
    if family in SPECS:
        last = latest(cursor, ctx, family)
        for row in rows:
            row['order_locked'] = not last or row['id'] != last['id']
            row['order_message'] = message(family, last) if row['order_locked'] else ''
    if family == 'bc':
        consumption_period.annotate(cursor, ctx, rows)
    return rows


def annotate(cursor, ctx, family, doc):
    annotate_rows(cursor, ctx, family, [doc])
    if family == 'bc' and doc.get('linked') and not doc.get('order_locked'):
        last = latest(cursor, ctx, 'lista')
        cursor.execute('SELECT IdLista FROM VC_LegBCLA WHERE SubunitId=%s AND GestiuneId=%s '
                       'AND ExercitiuId=%s AND IdBonConsum=%s',
                       (ctx['subunit'], ctx['gestiune'], ctx['exercitiu'], doc['id']))
        links = cursor.fetchall()
        if any(not last or row['IdLista'] != last['id'] for row in links):
            doc['order_locked'] = True
            doc['order_message'] = 'Bonul este asociat unei liste mai vechi. ' + message('lista', last)
    return doc
