"""Products drafted inside a receipt. Validation is read-only; inserts share its transaction."""
from utils.clock import utc_now
from decimal import Decimal

from . import nomenclatoare as nom, numeric, protocol


def warehouse(kind, tip):
    return kind not in ('MF', 'OI') or str(tip or '').strip().upper() in (
        {'MF': 'MFM', 'OI': 'OIM'}[kind], 'MAGAZIE')


def account(repo, ctx, identity):
    rows = repo.select('VC_Masini', {'IdMasina': identity, 'Activ': 1})
    return bool(rows and warehouse(ctx['type'], rows[0].get('TIP')))


def duration_message(code, minimum, maximum):
    low = format((Decimal(str(minimum)) * 12).normalize(), 'f')
    high = format((Decimal(str(maximum)) * 12).normalize(), 'f')
    return 'Pentru clasificarea %s durata (in luni) de folosire minima este %s si maxima %s.' % (code, low, high)


def draft(cursor, repo, ctx, raw, errors, key):
    values = raw.get('new_product')
    if values is None:
        return None
    try:
        cleaned = nom.clean(nom.SPECS['produse'], ctx['type'], values, ctx['scale'], repo)
        if not account(repo, ctx, cleaned['IdMasina']):
            raise nom.NomError('INVALID', 'Alegeți un cont MAGAZIE din gestiunea curentă.', 422)
        if cleaned.get('CPV'):
            from .cpv_catalog import validate
            validate(cursor, cleaned['CPV'])
        if ctx['type'] == 'MF':
            from .mf_classifications import detail
            classification = detail(cursor, cleaned.get('CC'))
            low, high = classification['dnf_min'], classification['dnf_max']
            duration = cleaned.get('DNF')
            if low is None or high is None:
                raise nom.NomError('INVALID', 'Alegeți o clasificare MF cu limite de durată.', 422)
            if duration is None or not low * 12 <= duration <= high * 12:
                raise nom.NomError('INVALID', duration_message(classification['code'], low, high), 422)
        return cleaned
    except nom.NomError as error:
        errors['l|%s|product' % key] = str(error)
        return None


def classification(cursor, repo, ctx, product, values=None):
    if values:
        cursor.execute('SELECT Cod AS code,DNF_MIN,DNF_MAX FROM AVACONT_COMUN.VC_ClasificariMF WHERE Cod=%s', (values.get('CC'),))
    else:
        cursor.execute('SELECT c.Cod AS code,c.DNF_MIN,c.DNF_MAX FROM VC_Produse p JOIN AVACONT_COMUN.VC_ClasificariMF c ON c.Cod=p.CC '
                       'WHERE p.SubunitId=%s AND p.GestiuneId=%s AND p.ID=%s',
                       (ctx['subunit'], ctx['gestiune'], product))
    return cursor.fetchone()


def monthly(line):
    # Access FC: Round((Pret + Pret * TVA) / DNF, 4). Corrected gross stays authoritative.
    gross = line['v']['ValoareCUTVA'] if line.get('calculation') else line['v']['Pret'] * (1 + line['rate'])
    return numeric.calc_round(gross / line['DNF'], 4)


def materialize(cursor, repo, ctx, line, created):
    values = line.get('new_product')
    if values is None:
        return
    identity = line['product']
    if identity not in created:
        created[identity] = repo.insert('VC_Produse', {**values, 'DataSalvare': utc_now(), 'Activ': 1})
        protocol.audit(cursor, ctx, 'nom.create', 'VC_Produse', created[identity], None, values, None, None)
    line['product'] = created[identity]
