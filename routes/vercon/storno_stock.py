"""Receipt-scoped FCS choices, capped by warehouse stock at the FCS date."""
from decimal import Decimal

from . import numeric, protocol


def source_lines(cursor, ctx, nir_id):
    from .documents import _all, _scope, _name_join, _money
    rows = _all(cursor,
        'SELECT i.IdProdus, pp.ID AS product, pr.DenumireP, pr.UM, pr.IdMasina, '
        'i.Cant, i.Pret, i.IDTVA, i.PUTVA, t.TVA AS rate FROM VC_Intrari i ' + _name_join('i') +
        ' LEFT JOIN VC_TVA t ON t.SubunitId=i.SubunitId AND t.GestiuneId=i.GestiuneId AND t.IdTVA=i.IDTVA '
        'WHERE i.SubunitId=%s AND i.GestiuneId=%s AND i.ExercitiuId=%s AND i.IdIntrare=%s ORDER BY i.ID',
        (*_scope(ctx), nir_id))
    result = {}
    for row in rows:
        variant = row['IdProdus']
        if variant not in result:
            result[variant] = {
                'variant': variant, 'product': row['product'], 'name': row['DenumireP'] or '', 'um': row['UM'] or '',
                'category': row['IdMasina'], 'qty': Decimal(0), 'Pret': _money(row['Pret'], ctx['scale']),
                'IDTVA': row['IDTVA'], 'vat_percent': format(Decimal(row['rate']) * 100, 'f') if row['rate'] is not None else None,
                'PUTVA': _money(row['PUTVA'], ctx['scale']),
            }
        result[variant]['qty'] += Decimal(row['Cant'] or 0)
    return list(result.values())


def eligible(cursor, ctx, nir_id, exclude=None, on=None, balances=None):
    from .documents import _all, _one, _scope
    on = on or protocol.to_date('%s-12-31' % ctx['year'], 'Data FCS')
    source = _one(cursor,
        'SELECT IdIntrare FROM VC_NIR WHERE SubunitId=%s AND GestiuneId=%s AND ExercitiuId=%s '
        'AND IdIntrare=%s AND Anulat=0 AND COALESCE(Storno,0)=0 '
        'AND DATE(Data)<=%s AND DATE(COALESCE(DataFc,Data))<=%s', (*_scope(ctx), nir_id, on, on))
    if not source:
        return []
    rows = source_lines(cursor, ctx, nir_id)
    if not rows:
        return []
    # Future FCS already reserve their quantities from this receipt as well.
    done = {r['IdProdus']: Decimal(r['q'] or 0) for r in _all(cursor,
        'SELECT x.IdProdus, SUM(x.Cant) AS q FROM VC_IntrariStorno x JOIN VC_NIR n '
        'ON n.SubunitId=x.SubunitId AND n.GestiuneId=x.GestiuneId AND n.ExercitiuId=x.ExercitiuId '
        'AND n.IdIntrare=x.IdIntrare WHERE x.SubunitId=%s AND x.GestiuneId=%s AND x.ExercitiuId=%s '
        'AND n.IdStorno=%s AND n.Anulat=0 AND x.Anulat=0 AND n.IdIntrare<>%s GROUP BY x.IdProdus',
        (*_scope(ctx), nir_id, exclude or 0))}
    if balances is None:
        balances = protocol.available(cursor, ctx, on, [r['variant'] for r in rows], ('storno', exclude) if exclude else None)
    out = []
    for row in rows:
        used = done.get(row['variant'], Decimal(0))
        stock = Decimal(balances.get(row['variant'], 0))
        left = min(row['qty'] - used, stock)
        if left > 0:
            out.append({**row, 'qty': format(row['qty'], 'f'), 'done': format(used, 'f'),
                        'left': format(left, 'f'), 'avail': format(stock, 'f')})
    return out


def sources(cursor, ctx, on, exclude=None):
    from .documents import list_documents, _all, _scope
    balances = protocol.available(cursor, ctx, on, exclude=('storno', exclude) if exclude else None)
    quantities = _all(cursor,
        'SELECT i.IdIntrare, i.IdProdus, SUM(i.Cant) AS qty FROM VC_Intrari i JOIN VC_NIR n '
        'ON n.SubunitId=i.SubunitId AND n.GestiuneId=i.GestiuneId AND n.ExercitiuId=i.ExercitiuId AND n.IdIntrare=i.IdIntrare '
        'WHERE i.SubunitId=%s AND i.GestiuneId=%s AND i.ExercitiuId=%s AND n.Anulat=0 AND COALESCE(n.Storno,0)=0 '
        'AND DATE(n.Data)<=%s AND DATE(COALESCE(n.DataFc,n.Data))<=%s GROUP BY i.IdIntrare,i.IdProdus',
        (*_scope(ctx), on, on))
    done = {(row['source'], row['variant']): Decimal(row['qty'] or 0) for row in _all(cursor,
        'SELECT n.IdStorno AS source, x.IdProdus AS variant, SUM(x.Cant) AS qty FROM VC_IntrariStorno x JOIN VC_NIR n '
        'ON n.SubunitId=x.SubunitId AND n.GestiuneId=x.GestiuneId AND n.ExercitiuId=x.ExercitiuId AND n.IdIntrare=x.IdIntrare '
        'WHERE x.SubunitId=%s AND x.GestiuneId=%s AND x.ExercitiuId=%s AND n.Anulat=0 AND x.Anulat=0 AND n.IdIntrare<>%s '
        'GROUP BY n.IdStorno,x.IdProdus', (*_scope(ctx), exclude or 0))}
    allowed = {row['IdIntrare'] for row in quantities
               if min(Decimal(row['qty'] or 0) - done.get((row['IdIntrare'], row['IdProdus']), Decimal(0)),
                      Decimal(balances.get(row['IdProdus'], 0))) > 0}
    return [row for row in list_documents(cursor, ctx, 'nir') if row['id'] in allowed]


def enrich_lines(cursor, ctx, source, lines):
    """FCS stores gross price; net price and VAT come from its source receipt."""
    entries = {row['variant']: row for row in source_lines(cursor, ctx, source)}
    for line in lines:
        entry = entries.get(line['variant'])
        if not entry:
            continue
        for name in ('product', 'category', 'Pret', 'IDTVA', 'vat_percent'):
            line[name] = entry[name]
        quantity = Decimal(line['Cant']) if line.get('Cant') is not None else None
        net = numeric.calc_round(quantity * Decimal(entry['Pret']), ctx['zec']) if quantity is not None and entry['Pret'] is not None else None
        gross = Decimal(line['ValoareCUTVA']) if line.get('ValoareCUTVA') is not None else None
        line['Valoare'] = format(net, 'f') if net is not None else None
        line['TVA'] = format(gross - net, 'f') if gross is not None and net is not None else None
    return lines
