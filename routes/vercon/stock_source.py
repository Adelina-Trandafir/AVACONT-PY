"""VC08-01: scoped stock movements shared by validation, statements and balances.

No date lower bound is applied in SQL: earlier movements are needed for opening stock.
All quantities remain direct decimals. OI custody is a separate ledger from warehouse
stock: a BP creates one warehouse BC, never a second consumption here.
"""
from decimal import Decimal

WAREHOUSE = (
    ('nir', 'VC_Intrari', 'i', 'VC_NIR', 'n', 'IdIntrare',
     'DATE(COALESCE(n.DataFc,n.Data))', 'n.NrFact', 'n.IdPartener',
     'n.Anulat=0 AND COALESCE(n.Storno,0)=0', 1),
    ('br', 'VC_IesiriRetur', 'r', 'VC_Iesiri_H', 'h', 'IdBonConsum',
     'DATE(h.DataBC)', 'h.NumarBC', 'NULL', 'h.Anulat=0', 1),
    ('bc', 'VC_Iesiri', 'o', 'VC_Iesiri_H', 'h', 'IdBonConsum',
     'DATE(h.DataBC)', 'h.NumarBC', 'NULL', 'h.Anulat=0', -1),
    ('storno', 'VC_IntrariStorno', 's', 'VC_NIR', 'n', 'IdIntrare',
     'DATE(COALESCE(n.DataFc,n.Data))', 'n.NrFact', 'n.IdPartener',
     'n.Anulat=0 AND s.Anulat=0', -1),
)


def warehouse(cursor, ctx, products=None, exclude=None):
    """Signed movements; products=None means all, an explicit empty set means none."""
    products = None if products is None else sorted({int(p) for p in products})
    if products == []:
        return []
    params = {'s': ctx['subunit'], 'g': ctx['gestiune'], 'e': ctx['exercitiu']}
    if exclude and exclude[0] not in {spec[0] for spec in WAREHOUSE}:
        raise ValueError('Unknown excluded document family')
    parts = []
    for kind, lines, a, header, h, key, day, number, supplier, valid, sign in WAREHOUSE:
        extra = ''
        if products is not None:
            extra += ' AND %s.IdProdus IN (%s)' % (a, ','.join(str(p) for p in products))
        if exclude and exclude[0] == kind:
            extra += ' AND %s.%s<>%%(exclude)s' % (h, key)
            params['exclude'] = int(exclude[1])
        parts.append(
            "SELECT {a}.IdProdus AS p, {day} AS d, {sign}*{a}.Cant AS q, "
            "'{kind}' AS kind, {h}.{key} AS document_id, {a}.ID AS line_id, "
            "{number} AS number, {supplier} AS supplier, NULL AS holder "
            "FROM {lines} {a} JOIN {header} {h} "
            "ON {h}.SubunitId={a}.SubunitId AND {h}.GestiuneId={a}.GestiuneId "
            "AND {h}.ExercitiuId={a}.ExercitiuId AND {h}.{key}={a}.{key} "
            "WHERE {a}.SubunitId=%(s)s AND {a}.GestiuneId=%(g)s "
            "AND {a}.ExercitiuId=%(e)s AND {valid}{extra}".format(
                a=a, h=h, day=day, sign=sign, kind=kind, key=key, number=number,
                supplier=supplier, lines=lines, header=header, valid=valid, extra=extra))
    cursor.execute(' UNION ALL '.join(parts), params)
    return [{**row, 'q': Decimal(row['q'] or 0)} for row in cursor.fetchall()]


def custody(cursor, ctx):
    """OI/MF: BP + at receiver, BT - at old/+ at new holder, BS - at holder.

    Read document lines directly, so a join with registry lots cannot multiply them.
    The context's single multi-year exercise is resolved by protocol.exercise_of.
    """
    parts = []
    for kind, table, key, day, number, holder, sign in (
        ('bp', 'VC_OI_Predare', 'IDBP', 'DATABP', 'NRBP', 'IDC_BP', 1),
        ('bt_in', 'VC_OI_Transfer', 'IDBT', 'DATABT', 'NRBT', 'IDC_BT', 1),
        ('bt_out', 'VC_OI_Transfer', 'IDBT', 'DATABT', 'NRBT', 'IDC_OLD', -1),
        ('bs', 'VC_OI_Casare', 'IDBC', 'DATABC', 'NRBC', 'IDC_BC', -1),
    ):
        parts.append(
            "SELECT IdProdus AS p, DATE({day}) AS d, {sign}*Cant AS q, "
            "'{kind}' AS kind, {key} AS document_id, ID AS line_id, "
            "{number} AS number, NULL AS supplier, {holder} AS holder "
            "FROM {table} WHERE SubunitId=%(s)s AND GestiuneId=%(g)s "
            "AND ExercitiuId=%(e)s AND Anulat=0".format(
                day=day, sign=sign, kind=kind, key=key, number=number,
                holder=holder, table=table))
    cursor.execute(' UNION ALL '.join(parts),
                   {'s': ctx['subunit'], 'g': ctx['gestiune'], 'e': ctx['exercitiu']})
    return [{**row, 'q': Decimal(row['q'] or 0)} for row in cursor.fetchall()]


def variants(cursor, ctx):
    """Inactive variants remain visible in historical stock; prices are current (Q9)."""
    cursor.execute(
        'SELECT p.IdProdus AS variant, p.ID AS product, p.COD AS code, p.PUTVA AS price, '
        'pr.DenumireP AS name, pr.UM AS um, pr.IdMasina AS category, m.Masina AS category_name '
        'FROM VC_ProduseP p LEFT JOIN VC_Produse pr ON pr.SubunitId=p.SubunitId '
        'AND pr.GestiuneId=p.GestiuneId AND pr.ID=p.ID '
        'LEFT JOIN VC_Masini m ON m.SubunitId=pr.SubunitId AND m.GestiuneId=pr.GestiuneId '
        'AND m.IdMasina=pr.IdMasina WHERE p.SubunitId=%s AND p.GestiuneId=%s '
        'ORDER BY pr.DenumireP,p.IdProdus', (ctx['subunit'], ctx['gestiune']))
    return {row['variant']: row for row in cursor.fetchall()}
