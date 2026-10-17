"""Usage of shared nomenclatures across all exercises; annual category statement."""
from datetime import date
from decimal import Decimal

from .nomenclatoare import NomError

MOVEMENT_TABLES = ('VC_Intrari', 'VC_IntrariStorno', 'VC_Iesiri', 'VC_IesiriRetur',
                   'VC_OI_Predare', 'VC_OI_Transfer', 'VC_OI_Casare', 'VC_OI_Produse')


def blocked_ids(cursor, subunit, gestiune, kind):
    if kind == 'produse':
        parts = [
            'SELECT DISTINCT v.ID AS id FROM VC_ProduseP v JOIN {table} m '
            'ON m.SubunitId=v.SubunitId AND m.GestiuneId=v.GestiuneId AND m.IdProdus=v.IdProdus '
            'WHERE v.SubunitId=%s AND v.GestiuneId=%s'.format(table=table)
            for table in MOVEMENT_TABLES]
        cursor.execute(' UNION '.join(parts), (subunit, gestiune) * len(parts))
    elif kind == 'categorii':
        # Do not restrict to the visible year, active products or uncancelled documents:
        # the existence of any movement protects the category's history.
        parts = [
            'SELECT DISTINCT p.IdMasina AS id FROM VC_Produse p '
            'JOIN VC_ProduseP v ON v.SubunitId=p.SubunitId AND v.GestiuneId=p.GestiuneId AND v.ID=p.ID '
            'JOIN {table} m ON m.SubunitId=v.SubunitId AND m.GestiuneId=v.GestiuneId AND m.IdProdus=v.IdProdus '
            'WHERE p.SubunitId=%s AND p.GestiuneId=%s AND p.IdMasina IS NOT NULL'.format(table=table)
            for table in MOVEMENT_TABLES]
        cursor.execute(' UNION '.join(parts), (subunit, gestiune) * len(parts))
    elif kind == 'tva':
        # VAT belongs to receipt product lines in the current schema, not VC_Produse.
        # Also recognise migrated lines that retain only the numeric VAT rate.
        cursor.execute(
            'SELECT DISTINCT t.IdTVA AS id FROM VC_TVA t JOIN VC_Intrari i '
            'ON i.SubunitId=t.SubunitId AND i.GestiuneId=t.GestiuneId '
            'AND (i.IDTVA=t.IdTVA OR (i.IDTVA IS NULL AND i.TVA=t.TVA)) '
            'WHERE t.SubunitId=%s AND t.GestiuneId=%s AND i.IdProdus IS NOT NULL',
            (subunit, gestiune))
    else:
        return set()
    return {row['id'] for row in cursor.fetchall()}


def annotate(cursor, subunit, gestiune, kind, rows):
    blocked = blocked_ids(cursor, subunit, gestiune, kind)
    if kind == 'produse':
        return [{**row, 'account_locked': row['id'] in blocked} for row in rows]
    message = {'categorii': 'Categoria are mișcări și nu poate fi dezactivată.',
               'tva': 'Cota TVA este folosită de produse și nu poate fi dezactivată.'}.get(kind)
    return [{**row, 'deactivate_reason': message if row['id'] in blocked else None} for row in rows]


def require_deactivation(cursor, ctx, kind, row_id):
    if row_id in blocked_ids(cursor, ctx['subunit'], ctx['gestiune'], kind):
        message = ('Categoria are mișcări și nu poate fi dezactivată.' if kind == 'categorii'
                   else 'Cota TVA este folosită de produse și nu poate fi dezactivată.')
        raise NomError('NOM_IN_USE', message, 409)


def category_statement(cursor, vctx, category):
    from . import protocol, stock

    sel, gest = vctx['selection'], vctx['gestiune']
    cursor.execute('SELECT IdMasina FROM VC_Masini WHERE SubunitId=%s AND GestiuneId=%s AND IdMasina=%s',
                   (sel['subunit'], sel['gestiune'], category))
    if not cursor.fetchone():
        raise NomError('NOT_FOUND', 'Categoria nu mai există.', 404)
    exercise = protocol.exercise_of(cursor, sel['subunit'], sel['gestiune'], gest['type'], sel['year'])
    ctx = {'subunit': sel['subunit'], 'gestiune': sel['gestiune'], 'exercitiu': exercise['id'],
           'type': gest['type'], 'scale': int(gest.get('scara', 4)), 'zec': int(gest.get('zec', 4))}
    year = sel['year']
    if not isinstance(year, int):
        raise NomError('NO_EXERCISE', 'Alegeți anul fișei contului.', 409)
    data = stock.read(cursor, ctx, date(year, 1, 1), date(year, 12, 31), category=category)
    totals = data['value_totals']
    incoming = sum((Decimal(totals[key]) for key in stock.POSITIVE), Decimal(0))
    outgoing = sum((Decimal(totals[key]) for key in stock.NEGATIVE), Decimal(0))
    # Initial-stock receipts dated in the selected year form SI, not purchases.
    cursor.execute('SELECT IdIntrare FROM VC_NIR WHERE SubunitId=%s AND GestiuneId=%s '
                   "AND ExercitiuId=%s AND TipDoc='STOC_INITIAL' AND Anulat=0",
                   (ctx['subunit'], ctx['gestiune'], ctx['exercitiu']))
    initial_ids = {row['IdIntrare'] for row in cursor.fetchall()}
    initial = sum((Decimal(row['value']) for row in data['statement']
                   if row['kind'] == 'nir' and row['document_id'] in initial_ids), Decimal(0))
    opening = Decimal(totals['opening']) + initial
    incoming -= initial
    return {'category': category, 'year': year, 'opening': format(opening, 'f'),
            'incoming': format(incoming, 'f'), 'outgoing': format(outgoing, 'f'),
            'closing': totals['closing'], 'zec': ctx['zec']}
