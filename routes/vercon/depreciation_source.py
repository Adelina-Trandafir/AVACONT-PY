"""VC08-02: MF register and documents read separately, never as a multiplying JOIN."""
from .nomenclatoare import NomError


def load(cursor, ctx, variant=None):
    if ctx['type'] != 'MF':
        raise NomError('FORM_NOT_AVAILABLE', 'Amortizarea există numai pentru gestiuni MF.', 403)
    params = {'s': ctx['subunit'], 'g': ctx['gestiune'], 'e': ctx['exercitiu']}
    extra = ''
    if variant is not None:
        extra = ' AND m.IdProdus=%(variant)s'
        params['variant'] = variant
    cursor.execute(
        'SELECT m.RowId,m.IdProdus,m.DataIntrare,m.DNF,m.DF,m.ValoareLuna,m.ValoareLunara,m.Version, '
        'p.PUTVA AS price,p.COD AS code,p.Version AS price_version,pr.DenumireP AS name,pr.UM AS um, '
        'pr.IdMasina AS category,pr.CC AS account '
        'FROM VC_ProduseM m LEFT JOIN VC_ProduseP p ON p.SubunitId=m.SubunitId '
        'AND p.GestiuneId=m.GestiuneId AND p.IdProdus=m.IdProdus '
        'LEFT JOIN VC_Produse pr ON pr.SubunitId=p.SubunitId AND pr.GestiuneId=p.GestiuneId AND pr.ID=p.ID '
        'WHERE m.SubunitId=%(s)s AND m.GestiuneId=%(g)s' + extra + ' ORDER BY m.IdProdus,m.RowId', params)
    assets = {}
    for row in cursor.fetchall():
        key = row['IdProdus'] if row['IdProdus'] is not None else 'row:%s' % row['RowId']
        if key in assets:
            assets[key].setdefault('duplicate_rows', []).append(row['RowId'])
        else:
            assets[key] = row
    if variant is not None and not assets:
        raise NomError('NOT_FOUND', 'Mijlocul fix nu există în gestiunea curentă.', 404)
    listed = ','.join(str(key) for key in assets if isinstance(key, int))
    if not listed:
        return assets, {}
    # No lower/upper date bound: the first BP and chronological stopping point must
    # remain the same when the report date changes. Aggregation happens once per asset.
    parts = []
    for kind, table, key, day in (
        ('handover', 'VC_OI_Predare', 'IDBP', 'DATABP'),
        ('transfer', 'VC_OI_Transfer', 'IDBT', 'DATABT'),
        ('scrapping', 'VC_OI_Casare', 'IDBC', 'DATABC'),
    ):
        parts.append("SELECT IdProdus,'{kind}' AS kind,DATE({day}) AS `date`,Cant AS quantity,"
                     "{key} AS document_id,ID AS line_id FROM {table} "
                     "WHERE SubunitId=%(s)s AND GestiuneId=%(g)s AND ExercitiuId=%(e)s AND Anulat=0 "
                     "AND IdProdus IN ({listed})".format(kind=kind, day=day, key=key, table=table, listed=listed))
    for kind, table, predicate in (
        ('receipt', 'VC_Intrari', 'COALESCE(n.Storno,0)=0'),
        ('storno', 'VC_IntrariStorno', 'i.Anulat=0'),
    ):
        parts.append("SELECT i.IdProdus,'{kind}' AS kind,DATE(COALESCE(n.DataFc,n.Data)) AS `date`,"
                     "i.Cant AS quantity,n.IdIntrare AS document_id,i.ID AS line_id FROM {table} i "
                     "JOIN VC_NIR n ON n.SubunitId=i.SubunitId AND n.GestiuneId=i.GestiuneId "
                     "AND n.ExercitiuId=i.ExercitiuId AND n.IdIntrare=i.IdIntrare "
                     "WHERE i.SubunitId=%(s)s AND i.GestiuneId=%(g)s AND i.ExercitiuId=%(e)s "
                     "AND n.Anulat=0 AND {predicate} AND i.IdProdus IN ({listed})".format(
                         kind=kind, table=table, predicate=predicate, listed=listed))
    cursor.execute(' UNION ALL '.join(parts), {key: params[key] for key in ('s', 'g', 'e')})
    events = {}
    for row in cursor.fetchall():
        if row['IdProdus'] in assets:
            events.setdefault(row['IdProdus'], []).append(row)
    return assets, events
