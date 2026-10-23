"""Copy invoice CPVs to explicitly associated products inside the association transaction."""
from utils.clock import utc_now
from utils import vercon_efactura as shared

from . import protocol
from .nomenclatoare import NomError


def apply(cursor, ctx, sending_id, nir_id, source, target, allocations, request_key, request_id):
    products, prefixes = {}, set()
    for lid, nid, _, _ in allocations:
        prefix = shared.cpv(source[lid].get('Cpv'))
        if not prefix:
            # A missing/malformed invoice CPV must never erase the product's code.
            continue
        line = target[nid]
        product = line['PRODUCT_ID']
        if product in products and products[product]['prefix'] != prefix:
            raise NomError('EF_CPV_CONFLICT',
                           'Produsul „%s” este asociat unor poziții cu CPV-uri diferite. '
                           'Asociați același produs numai unor poziții cu același CPV.' % line['DenumireP'], 422)
        products[product] = {'prefix': prefix, 'before': line['CPV']}
        prefixes.add(prefix)
    if not products:
        return 0

    # Products use the catalogue's complete code, including the check digit.
    # UBL may supply only eight digits or omit the separator before the check digit.
    cursor.execute('SELECT CPV FROM AVACONT_COMUN.CPV WHERE LEFT(CPV,8) IN ('
                   + ','.join(['%s'] * len(prefixes)) + ')', tuple(sorted(prefixes)))
    codes = {}
    for row in cursor.fetchall():
        prefix = shared.cpv(row['CPV'])
        if prefix in prefixes:
            codes[prefix] = row['CPV']
    missing = prefixes - codes.keys()
    if missing:
        raise NomError('EF_CPV_UNKNOWN',
                       'CPV-ul din eFactura (%s) nu există în nomenclatorul CPV. '
                       'Asocierile nu au fost salvate.' % ', '.join(sorted(missing)), 422)

    changed = 0
    for product, values in products.items():
        code = codes[values['prefix']]
        if values['before'] == code:
            continue
        cursor.execute('UPDATE VC_Produse SET CPV=%s,DataSalvare=%s,Version=Version+1 '
                       'WHERE SubunitId=%s AND GestiuneId=%s AND ID=%s',
                       (code, utc_now(), ctx['subunit'], ctx['gestiune'], product))
        protocol.audit(cursor, ctx, 'efactura.product_cpv', 'VC_Produse', product,
                       {'CPV': values['before']}, {'CPV': code, 'efactura': sending_id, 'nir': nir_id},
                       request_key, request_id)
        changed += 1
    return changed
