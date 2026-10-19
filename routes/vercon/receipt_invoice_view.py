"""Read-only invoice context for NIR screens. Stock documents and reports stay local."""
from decimal import Decimal

from .documents import _all, _scope, _iso


def plain(value):
    return None if value is None else format(Decimal(value), 'f')


def other_lines(cursor, ctx, doc, invoice_id):
    rows = _all(cursor,
        'SELECT f.ID AS receipt_link,f.SUBUNIT_ID AS subunit,f.GESTIUNE_ID AS gestiune, '
        'f.EXERCITIU_ID AS exercise,f.NIR_ID AS nir,n.Data AS day,i.ID AS id,i.IdProdus AS variant, '
        'pp.ID AS product,pp.COD AS code,p.DenumireP AS name,p.UM AS um,p.IdMasina AS category, '
        'i.Cant,i.Pret,i.IDTVA,i.PUTVA,i.Valoare,i.TVA,i.ValoareCUTVA,t.TVA AS rate, '
        'm.DataIntrare,m.DNF,m.DF,m.ValoareLuna,g.Zec AS zec,g.Type AS type, '
        's.Name AS subunit_name,g.Name AS gestiune_name,c.TOTAL_BRUT,c.PRET_INITIAL '
        'FROM VC_NIR_FACTURI f JOIN VC_NIR n ON n.SubunitId=f.SUBUNIT_ID AND n.GestiuneId=f.GESTIUNE_ID '
        'AND n.ExercitiuId=f.EXERCITIU_ID AND n.IdIntrare=f.NIR_ID '
        'JOIN VC_Intrari i ON i.SubunitId=n.SubunitId AND i.GestiuneId=n.GestiuneId '
        'AND i.ExercitiuId=n.ExercitiuId AND i.IdIntrare=n.IdIntrare '
        'LEFT JOIN VC_ProduseP pp ON pp.SubunitId=i.SubunitId AND pp.GestiuneId=i.GestiuneId AND pp.IdProdus=i.IdProdus '
        'LEFT JOIN VC_Produse p ON p.SubunitId=pp.SubunitId AND p.GestiuneId=pp.GestiuneId AND p.ID=pp.ID '
        'LEFT JOIN VC_TVA t ON t.SubunitId=i.SubunitId AND t.GestiuneId=i.GestiuneId AND t.IdTVA=i.IDTVA '
        'LEFT JOIN VC_ProduseM m ON m.SubunitId=i.SubunitId AND m.GestiuneId=i.GestiuneId AND m.IdProdus=i.IdProdus '
        'LEFT JOIN VC_NIR_CALCULE c ON c.SUBUNIT_ID=i.SubunitId AND c.GESTIUNE_ID=i.GestiuneId '
        'AND c.EXERCITIU_ID=i.ExercitiuId AND c.INTRARE_ID=i.ID '
        'JOIN VC_Subunitati s ON s.SubunitId=f.SUBUNIT_ID '
        'JOIN VC_Gestiuni g ON g.SubunitId=f.SUBUNIT_ID AND g.GestiuneId=f.GESTIUNE_ID '
        'WHERE f.FACTURA_ID=%s AND n.Anulat=0 '
        'AND NOT (f.SUBUNIT_ID=%s AND f.GESTIUNE_ID=%s AND f.EXERCITIU_ID=%s AND f.NIR_ID=%s) '
        'ORDER BY f.SUBUNIT_ID,f.GESTIUNE_ID,f.EXERCITIU_ID,f.NIR_ID,i.ID',
        (invoice_id, *_scope(ctx), doc['id']))
    result = []
    for row in rows:
        line = {key: row[key] for key in ('id', 'variant', 'product', 'code', 'name', 'um', 'category', 'IDTVA', 'zec', 'type')}
        line.update({key: plain(row[key]) for key in ('Cant', 'Pret', 'PUTVA', 'Valoare', 'TVA', 'ValoareCUTVA', 'DNF', 'DF', 'ValoareLuna')})
        line.update({'key': 'invoice:%s:%s' % (row['receipt_link'], row['id']), 'external': True,
                     'destination': '%s:%s' % (row['subunit'], row['gestiune']),
                     'destination_label': '%s / %s' % (row['subunit_name'], row['gestiune_name']),
                     'vat_percent': plain((row['rate'] or Decimal(0)) * 100), 'data_intrarii': _iso(row['DataIntrare']),
                     'context': {'subunit': row['subunit'], 'gestiune': row['gestiune'],
                                 'year': '' if row['type'] in ('OI', 'MF') else row['day'].year if row['day'] else ctx['year']}})
        if row['TOTAL_BRUT'] is not None:
            line.update({'calc_mode': 'GROSS', 'gross_total': plain(row['TOTAL_BRUT']), 'input_price': plain(row['PRET_INITIAL'])})
        result.append(line)
    from .receipt_mf_edit import annotate
    contexts = {}
    for line in result:
        if line['type'] == 'MF':
            context = line['context']
            contexts.setdefault((context['subunit'], context['gestiune']), []).append(line)
    for (subunit, gestiune), lines in contexts.items():
        annotate(cursor, {'type': 'MF', 'subunit': subunit, 'gestiune': gestiune}, lines)
    return result


def summary_totals(cursor, ctx, summaries):
    """One batched lookup per opened tree branch, without changing local stock totals."""
    if not summaries:
        return summaries
    from . import invoices
    if not invoices.ready(cursor):
        return summaries
    ids = [row['id'] for row in summaries]
    rows = _all(cursor,
        'SELECT nf.NIR_ID AS id,COALESCE(f.TOTAL, '
        '(SELECT SUM(n.Valoare) FROM VC_NIR_FACTURI x JOIN VC_NIR n '
        'ON n.SubunitId=x.SUBUNIT_ID AND n.GestiuneId=x.GESTIUNE_ID '
        'AND n.ExercitiuId=x.EXERCITIU_ID AND n.IdIntrare=x.NIR_ID '
        'WHERE x.FACTURA_ID=f.ID AND n.Anulat=0)) AS total '
        'FROM VC_NIR_FACTURI nf JOIN VC_FACTURI f ON f.ID=nf.FACTURA_ID '
        'WHERE nf.SUBUNIT_ID=%s AND nf.GESTIUNE_ID=%s AND nf.EXERCITIU_ID=%s '
        'AND f.LOCALA=0 AND nf.NIR_ID IN (' + ','.join(['%s'] * len(ids)) + ')', (*_scope(ctx), *ids))
    totals = {row['id']: plain(row['total']) for row in rows if row['total'] is not None}
    for row in summaries:
        if row['id'] in totals:
            row['local_total'], row['total'] = row['total'], totals[row['id']]
    return summaries
