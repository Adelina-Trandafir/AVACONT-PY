"""MF receipt editing keeps assets with warehouse exits immutable and present in the NIR."""
from decimal import Decimal

from . import protocol
from .documents import _int


def annotate(cursor, ctx, lines):
    if ctx['type'] != 'MF':
        return
    variants = sorted({line['variant'] for line in lines if line.get('variant') is not None})
    issued = set()
    if variants:
        cursor.execute(
            'SELECT DISTINCT o.IdProdus FROM VC_Iesiri o JOIN VC_Iesiri_H h '
            'ON h.SubunitId=o.SubunitId AND h.GestiuneId=o.GestiuneId '
            'AND h.ExercitiuId=o.ExercitiuId AND h.IdBonConsum=o.IdBonConsum '
            'JOIN VC_ProduseP pp ON pp.SubunitId=o.SubunitId AND pp.GestiuneId=o.GestiuneId AND pp.IdProdus=o.IdProdus '
            'JOIN VC_Produse p ON p.SubunitId=pp.SubunitId AND p.GestiuneId=pp.GestiuneId AND p.ID=pp.ID '
            'WHERE o.SubunitId=%s AND o.GestiuneId=%s AND h.IdMasina=p.IdMasina '
            'AND COALESCE(h.Anulat,0)=0 AND COALESCE(h.Retur,0)=0 AND o.Cant>0 '
            'AND o.IdProdus IN (' + ','.join(['%s'] * len(variants)) + ')',
            (ctx['subunit'], ctx['gestiune'], *variants))
        issued = {row['IdProdus'] for row in cursor.fetchall()}
        for table in ('VC_OI_Predare', 'VC_OI_Transfer', 'VC_OI_Casare'):
            cursor.execute(
                'SELECT DISTINCT IdProdus FROM ' + table +
                ' WHERE SubunitId=%s AND GestiuneId=%s AND Anulat=0 AND IdProdus IN (' +
                ','.join(['%s'] * len(variants)) + ')', (ctx['subunit'], ctx['gestiune'], *variants))
            issued.update(row['IdProdus'] for row in cursor.fetchall())
    for line in lines:
        line['has_warehouse_exit'] = line.get('variant') in issued


def preserved(repo, old, key):
    line = {'key': key, 'id': old['id'], 'preserve': True, 'has_warehouse_exit': True,
            'variant': old['variant'], 'product': old['product'], 'IDTVA': old['IDTVA'],
            'name': old['name'], 'invoice_line': old.get('invoice_line'), 'COD': old.get('code'),
            'data_intrarii': protocol.to_date(old['data_intrarii'], 'Data intrării') if old.get('data_intrarii') else None,
            'DNF': old.get('DNF'), 'DF': old.get('DF'),
            'ValoareLuna': Decimal(old['ValoareLuna']) if old.get('ValoareLuna') is not None else None,
            'v': {field: Decimal(old[field]) if old.get(field) is not None else None
                  for field in ('Cant', 'Pret', 'PUTVA', 'Valoare', 'TVA', 'ValoareCUTVA')}}
    rates = repo.select('VC_TVA', {'IdTVA': old['IDTVA']})
    line['rate'] = Decimal(rates[0]['TVA'] or 0) if rates else None
    if old.get('calc_mode') == 'GROSS':
        line['calculation'] = {'mode': 'GROSS', 'gross': Decimal(old['gross_total']),
                               'input_price': Decimal(old['input_price']), 'origin': old.get('auto_origin')}
    return line


def partition(repo, raw_lines, stored, errors):
    from .receipts import unchanged
    locked = {identity: old for identity, old in (stored or {}).items() if old.get('has_warehouse_exit')}
    editable, protected, seen = [], [], set()
    for raw in raw_lines:
        identity = _int(raw.get('id')) if isinstance(raw, dict) else None
        if identity not in locked:
            editable.append(raw)
            continue
        old = locked[identity]
        key = str(raw.get('key') or 'saved:%s' % identity)
        if (identity in seen or raw.get('new_product') is not None or not unchanged(raw, old, True)
                or _int(raw.get('invoice_line')) != old.get('invoice_line')):
            errors['l|%s|product' % key] = 'Mijlocul fix are ieșire din contul de intrare și nu poate fi modificat.'
        if identity not in seen:
            protected.append(preserved(repo, old, key))
        seen.add(identity)
    # Absence from the editable grid is never a request to delete an issued asset.
    for identity, old in locked.items():
        if identity not in seen:
            protected.append(preserved(repo, old, 'saved:%s' % identity))
    return editable, protected
