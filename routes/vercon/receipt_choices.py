"""Receipt choices use product identities and one supplier per fiscal code."""


def products(cursor, subunit, gestiune):
    cursor.execute(
        'SELECT ID, DenumireP, UM, IdMasina FROM VC_Produse '
        'WHERE SubunitId=%s AND GestiuneId=%s AND Activ=1 ORDER BY DenumireP, ID',
        (subunit, gestiune))
    return [{'id': row['ID'], 'name': row['DenumireP'] or '', 'um': row['UM'] or '',
             'category': row['IdMasina']} for row in cursor.fetchall()]


def partners(cursor):
    # FIRST(name) is the name on the first eligible IdPartener, not MIN(name).
    cursor.execute(
        'SELECT p.IdPartener, p.DenumirePartener, first_partner.CodFiscal '
        'FROM Parteneri p JOIN ('
        'SELECT TRIM(CodFiscal) AS CodFiscal, MIN(IdPartener) AS IdPartener '
        "FROM Parteneri WHERE COALESCE(Ascuns,0)=0 AND NULLIF(TRIM(CodFiscal),'') IS NOT NULL "
        'GROUP BY TRIM(CodFiscal)) first_partner ON first_partner.IdPartener=p.IdPartener '
        'ORDER BY p.DenumirePartener, p.IdPartener')
    return [{'id': row['IdPartener'], 'name': row['DenumirePartener'] or '',
             'cf': row['CodFiscal']} for row in cursor.fetchall()]


def price_variant(repo, product, price, now, existing=None):
    """Resolve the received price without changing prices used by other documents."""
    rows = repo.select('VC_ProduseP', {'ID': product, 'PUTVA': price, 'Activ': 1},
                       columns=('IdProdus',), for_update=True)
    ids = {row['IdProdus'] for row in rows}
    if existing and existing['IdProdus'] in ids:
        return existing['IdProdus']
    if ids:
        return min(ids)
    return repo.insert('VC_ProduseP', {'ID': product, 'PUTVA': price, 'Activ': 1,
                                      'COD': None, 'DataSalvare': now})
