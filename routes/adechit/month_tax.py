"""SLICE-AD14-02: choose a fee for an open month under the existing ADE transaction."""
import math

from . import service
from .domain import access_long, nz, require


def change(repo, body):
    for key in ('month_id', 'tax_id'):
        require(type(body.get(key)) is int and body[key] > 0,
                'REQUEST', 'Alegeți luna și taxa zilnică.', 400)
    month = repo.get('LunaD', body['month_id'])
    require(not month['Inchisa'], 'CLOSED', 'Taxa nu se poate schimba într-o lună închisă.', 409)
    require(month['Version'] == body.get('version'), 'CONFLICT',
            'Luna s-a modificat. Reîncărcați înainte de a schimba taxa.', 409)
    tax = repo.get('ValoriTaxe', body['tax_id'])
    require(tax['Version'] == body.get('tax_version'), 'CONFLICT',
            'Taxa s-a modificat. Reîncărcați lista taxelor.', 409)
    rate = tax.get('TaxaZilnica')
    require(type(rate) in (int, float) and math.isfinite(rate) and rate > 0,
            'TAX', 'Alegeți o taxă zilnică pozitivă.', 400)
    if month.get('IDV') == tax['IDV']:
        return {'month': month, 'count': 0}
    # The choice applies to this whole month, including children in groups outside the current grid.
    # Reuse the attendance formula and Access conversion; entered days and payments are preserved.
    attendance = repo.rows('Prezenta', IDL=month['IDL'])
    for row in attendance:
        value = nz(row.get('ZilePrezenta')) * rate
        changes = service.validate_values('Prezenta', {
            'IDV': tax['IDV'], 'ValoareContract': value, 'ValoareTotala': access_long(value),
        })
        repo.update('Prezenta', row, changes)
    month = repo.update('LunaD', month, {'IDV': tax['IDV']})
    return {'month': month, 'count': len(attendance)}
