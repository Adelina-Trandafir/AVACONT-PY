"""SLICE-VC07-04: the fixed-asset register (MF).

An asset is a price variant (VC_ProduseP: inventory number in COD, entry value in PUTVA) with ONE register row
(VC_ProduseM: entry date, normal duration DNF in months, used duration DF, monthly value). The assets are created by the
receipt (VC06-02); here they are listed with where they are (which holder has them, how many were written off) and the
register data is kept up to date.

Rules: the register of a variant is changed only with the revision the page saw; the monthly value is recomputed from
what the receipts say the asset cost (the sum of the total with VAT of its receipt lines) divided by DNF, rounded once at
ZEC; the used duration cannot pass the normal one; nothing of a closed year is touched (OI/MF have one multi-year history
that is always open). The identity of an asset never changes: editing its register never re-creates the variant.
"""
from decimal import Decimal

from . import numeric, protocol
from .documents import _all, _fail, _int, _money, _one, _iso, _scope
from .nomenclatoare import NomError


def assets(cursor, ctx):
    s, g, e = _scope(ctx)
    scale = ctx['scale']
    rows = _all(cursor, 'SELECT m.RowId, m.IdProdus, m.DataIntrare, m.DNF, m.DF, m.ValoareLuna, m.Version, pp.COD, pr.DenumireP, pr.UM, '
                        '(SELECT COALESCE(SUM(i.ValoareCUTVA),0) FROM VC_Intrari i JOIN VC_NIR n ON n.SubunitId=i.SubunitId AND n.GestiuneId=i.GestiuneId AND n.ExercitiuId=i.ExercitiuId AND n.IdIntrare=i.IdIntrare '
                        ' WHERE i.SubunitId=m.SubunitId AND i.GestiuneId=m.GestiuneId AND i.IdProdus=m.IdProdus AND n.Anulat=0 AND COALESCE(n.Storno,0)=0) AS Valoare, '
                        '(SELECT COALESCE(SUM(c.Cant),0) FROM VC_OI_Casare c WHERE c.SubunitId=m.SubunitId AND c.GestiuneId=m.GestiuneId AND c.IdProdus=m.IdProdus AND c.Anulat=0) AS Casat '
                        'FROM VC_ProduseM m LEFT JOIN VC_ProduseP pp ON pp.SubunitId=m.SubunitId AND pp.GestiuneId=m.GestiuneId AND pp.IdProdus=m.IdProdus '
                        'LEFT JOIN VC_Produse pr ON pr.SubunitId=pp.SubunitId AND pr.GestiuneId=pp.GestiuneId AND pr.ID=pp.ID WHERE m.SubunitId=%s AND m.GestiuneId=%s '
                        'ORDER BY pr.DenumireP, pp.COD', (s, g))
    holders = {}
    for r in _all(cursor, 'SELECT o.IdProdus, o.IDC_PTC, c.Denumire, SUM(o.Cant) AS q FROM VC_OI_Produse o LEFT JOIN VC_Clienti c ON c.SubunitId=o.SubunitId AND c.GestiuneId=o.GestiuneId AND c.IDC=o.IDC_PTC '
                  'WHERE o.SubunitId=%s AND o.GestiuneId=%s GROUP BY o.IdProdus, o.IDC_PTC, c.Denumire HAVING SUM(o.Cant)<>0', (s, g)):
        holders.setdefault(r['IdProdus'], []).append({'holder': r['Denumire'] or '', 'qty': _money(Decimal(r['q']), scale)})
    out = []
    for r in rows:
        out.append({'id': r['IdProdus'], 'version': r['Version'], 'name': r['DenumireP'], 'um': r['UM'], 'code': r['COD'], 'entry_date': _iso(r['DataIntrare']), 'DNF': r['DNF'], 'DF': r['DF'],
                    'value': _money(Decimal(r['Valoare'] or 0), scale), 'monthly': _money(r['ValoareLuna'], scale), 'written_off': _money(Decimal(r['Casat'] or 0), scale),
                    'holders': holders.get(r['IdProdus'], [])})
    return out


def update(cursor, repo, ctx, variant, body, request_id):
    rows = repo.select('VC_ProduseM', {'IdProdus': variant}, for_update=True)
    if not rows:
        raise NomError('NOT_FOUND', 'Mijlocul fix nu mai există.', 404)
    before = rows[0]
    version = _int(body.get('version'))
    if version is None or before['Version'] != version:
        raise NomError('CONFLICT', 'Evidența a fost modificată de altcineva. Reîncărcați lista.', 409, {'version': before['Version']})
    errors = {}
    dnf, df = _int(body.get('DNF')), _int(body.get('DF') if body.get('DF') not in (None, '') else 0)
    if dnf is None or dnf < 1:
        errors['DNF'] = 'Durata normală trebuie să fie de cel puțin 1 lună.'
    if df is None or df < 0:
        errors['DF'] = 'Durata folosită nu poate fi negativă.'
    elif dnf is not None and dnf >= 1 and df > dnf:
        errors['DF'] = 'Durata folosită nu poate depăși durata normală.'
    entry = protocol.to_date(body.get('DataIntrare'), 'Data intrării', None, errors, 'DataIntrare')
    _fail(errors)
    cost = _one(cursor, 'SELECT COALESCE(SUM(i.ValoareCUTVA),0) AS v FROM VC_Intrari i JOIN VC_NIR n ON n.SubunitId=i.SubunitId AND n.GestiuneId=i.GestiuneId AND n.ExercitiuId=i.ExercitiuId AND n.IdIntrare=i.IdIntrare '
                         'WHERE i.SubunitId=%s AND i.GestiuneId=%s AND i.IdProdus=%s AND n.Anulat=0 AND COALESCE(n.Storno,0)=0', (ctx['subunit'], ctx['gestiune'], variant))
    monthly = before['ValoareLuna']
    if cost and Decimal(cost['v']):
        monthly = numeric.to_stored(numeric.calc_round(numeric.from_stored(Decimal(cost['v']), ctx['scale']) / Decimal(dnf), ctx['zec']), ctx['scale'])
    if repo.update('VC_ProduseM', {'DataIntrare': entry, 'DNF': dnf, 'DF': df, 'ValoareLuna': monthly}, {'IdProdus': variant, 'Version': version}, bump_version=True) != 1:
        raise NomError('CONFLICT', 'Evidența a fost modificată de altcineva. Reîncărcați lista.', 409)
    protocol.audit(cursor, ctx, 'mf.update', 'VC_ProduseM', variant, {'DNF': before['DNF'], 'DF': before['DF'], 'DataIntrare': before['DataIntrare'], 'ValoareLuna': before['ValoareLuna']},
                   {'DNF': dnf, 'DF': df, 'DataIntrare': entry, 'ValoareLuna': monthly}, None, request_id)
    return {'id': variant}
