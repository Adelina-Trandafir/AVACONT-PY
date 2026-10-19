"""DC-wide receipt catalog and transactional destination reservations. No DDL."""
import secrets
from contextlib import contextmanager
from decimal import Decimal

from flask import request

from .documents import _all, _one, _int
from .nomenclatoare import NomError
from .receipt_products import warehouse


def catalog(cursor, current):
    rows = _all(cursor, 'SELECT g.SubunitId,g.GestiuneId,g.Name,g.Type,s.Name AS SubunitName,g.Zec '
                'FROM VC_Gestiuni g JOIN VC_Subunitati s ON s.SubunitId=g.SubunitId '
                'WHERE g.Active=1 AND s.Active=1 ORDER BY s.Name,g.Name,g.GestiuneId')
    destinations, products, taxes, accounts, inventory = [], [], [], [], {}
    for row in rows:
        s, g = row['SubunitId'], row['GestiuneId']
        if current['type'] in ('MF', 'OI') and (s, g) != (current['subunit'], current['gestiune']):
            continue
        identity = '%s:%s' % (s, g)
        label = row['Name'] if s == current['subunit'] else '%s / %s' % (row['SubunitName'], row['Name'])
        destinations.append({'id': identity, 'subunit': s, 'gestiune': g, 'name': label, 'type': row['Type'],
                             'zec': row['Zec'], 'current': (s, g) == (current['subunit'], current['gestiune'])})
        accounts.extend({'id': a['IdMasina'], 'label': a['Masina'] or '', 'destination': identity}
                        for a in _all(cursor, 'SELECT IdMasina,Masina,TIP FROM VC_Masini WHERE SubunitId=%s AND GestiuneId=%s AND Activ=1 ORDER BY Masina', (s, g))
                        if warehouse(row['Type'], a['TIP']))
        products.extend({'id': p['ID'], 'name': p['DenumireP'] or '', 'um': p['UM'] or '', 'category': p['IdMasina'],
                         'destination': identity, 'type': row['Type'], 'dnf': p['DNF'], 'cc': p['CC'],
                         'dnf_min': p['DNF_MIN'], 'dnf_max': p['DNF_MAX']}
                        for p in _all(cursor, 'SELECT p.ID,p.DenumireP,p.UM,p.IdMasina,p.DNF,p.CC,c.DNF_MIN,c.DNF_MAX FROM VC_Produse p '
                                      'LEFT JOIN AVACONT_COMUN.VC_ClasificariMF c ON c.Cod=p.CC '
                                      'WHERE p.SubunitId=%s AND p.GestiuneId=%s AND p.Activ=1 ORDER BY p.DenumireP,p.ID', (s, g)))
        if row['Type'] == 'MF':
            inventory[identity] = [str(p['COD']).strip() for p in _all(cursor,
                'SELECT COD FROM VC_ProduseP WHERE SubunitId=%s AND GestiuneId=%s AND COD IS NOT NULL', (s, g))]
        taxes.extend({'id': t['IdTVA'], 'percent': format((t['TVA'] or Decimal(0)) * 100, 'f'), 'destination': identity}
                     for t in _all(cursor, 'SELECT IdTVA,TVA FROM VC_TVA WHERE SubunitId=%s AND GestiuneId=%s AND Activ=1 ORDER BY TVA', (s, g)))
    destinations.sort(key=lambda d: not d['current'])
    return {'destinations': destinations, 'accounts': accounts, 'products': products, 'vat': taxes, 'inventory': inventory}


def selections(cursor, anchor, data, scoped, build, repo_of):
    available = catalog(cursor, anchor)
    wanted = data.get('accounts')
    if not isinstance(wanted, list) or not wanted or len(wanted) > 500:
        raise NomError('INVALID', 'Bifați cel puțin un cont pentru recepție.', 422, {'h.Accounts': 'Alegeți conturile.'})
    account_ids = {_int(value) for value in wanted}
    accounts = {a['id']: a for a in available['accounts'] if a['id'] in account_ids}
    if set(accounts) != account_ids:
        raise NomError('CONFLICT', 'Un cont selectat nu mai este disponibil în DC/an.', 409)
    targets = {a['destination'] for a in accounts.values()}
    choices = {p['id']: p for p in available['products'] if p['category'] in accounts}
    raw_lines = data.get('lines')
    if not isinstance(raw_lines, list) or len(raw_lines) > 500:
        raise NomError('INVALID', 'Lista produselor este invalidă (maximum 500 de rânduri).', 422)
    grouped = {destination: [] for destination in targets}
    for raw in raw_lines:
        product = choices.get(_int(raw.get('product'))) if isinstance(raw, dict) else None
        if isinstance(raw, dict) and isinstance(raw.get('new_product'), dict) and (_int(raw.get('product')) or 0) < 0:
            chosen = accounts.get(_int(raw['new_product'].get('IdMasina')))
            product = {'destination': chosen['destination']} if chosen else None
        if not product:
            raise NomError('INVALID', 'Un produs nu aparține conturilor bifate sau nu mai este activ.', 422,
                           {'l|%s|product' % (raw.get('key', '') if isinstance(raw, dict) else ''): 'Alegeți produsul din conturile bifate.'})
        grouped[product['destination']].append(raw)
    result = []
    for destination in sorted(available['destinations'], key=lambda d: (d['subunit'], d['gestiune'])):
        if destination['id'] not in targets:
            continue
        # Read resolution validates this DC's actual hierarchy/year without borrowing the current root's revision.
        source = {'subunit': destination['subunit'], 'gestiune': destination['gestiune'],
                  'year': None if destination['type'] in ('OI', 'MF') else anchor['year']}
        verified = scoped('read', source, version='')
        ctx, exercise = build(cursor, verified, 'nir', False)
        result.append({'ctx': ctx, 'exercise': exercise, 'repo': repo_of(cursor, ctx), 'destination': destination,
                       'lines': grouped[destination['id']], 'accounts': {a for a, item in accounts.items() if item['destination'] == destination['id']}})
    return result


def commission(cursor, anchor, target, identity):
    if identity in (None, ''):
        return None
    members = _one(cursor, 'SELECT Receptie1,Receptie2,Receptie3,Receptie4 FROM VC_ComisieNIR '
                   'WHERE SubunitId=%s AND GestiuneId=%s AND IdComisie=%s', (anchor['subunit'], anchor['gestiune'], _int(identity)))
    if not members:
        raise NomError('INVALID', 'Comisia selectată nu există.', 422, {'h.IdComisie': 'Alegeți comisia.'})
    match = _one(cursor, 'SELECT IdComisie FROM VC_ComisieNIR WHERE SubunitId=%s AND GestiuneId=%s '
                 'AND Receptie1 <=> %s AND Receptie2 <=> %s AND Receptie3 <=> %s AND Receptie4 <=> %s '
                 'AND Activ=1 ORDER BY IdComisie LIMIT 1', (target['subunit'], target['gestiune'],
                 *(members[field] for field in ('Receptie1', 'Receptie2', 'Receptie3', 'Receptie4'))))
    if not match:
        raise NomError('COMMITTEE_DESTINATION', 'Comisia aleasă trebuie configurată cu aceiași membri în fiecare destinație.', 422,
                       {'h.IdComisie': 'Comisia nu este disponibilă în una dintre destinații.'})
    return match['IdComisie']


@contextmanager
def reserve(cursor, anchor, groups):
    """Additional contexts are occupied only during the atomic save; another tab's lease is never taken over."""
    temporary = []
    contexts = {(anchor['subunit'], anchor['gestiune']): anchor}
    contexts.update({(g['ctx']['subunit'], g['ctx']['gestiune']): g['ctx'] for g in groups})
    for (s, g), ctx in sorted(contexts.items()):
        _one(cursor, 'SELECT GestiuneId FROM VC_Gestiuni WHERE SubunitId=%s AND GestiuneId=%s FOR UPDATE', (s, g))
        held = _one(cursor, 'SELECT HolderEmail,SessionToken,ExpiresAt<NOW() AS Expired FROM VC_ContextLock '
                    'WHERE SubunitId=%s AND GestiuneId=%s FOR UPDATE', (s, g))
        current = (s, g) == (anchor['subunit'], anchor['gestiune'])
        if current:
            if not held or held['Expired'] or held['HolderEmail'] != ctx['email'] or held['SessionToken'] != request.headers.get('X-Vercon-Lock'):
                raise NomError('LOCK_LOST', 'Ocuparea destinației curente a expirat.', 409)
        else:
            if held and not held['Expired']:
                raise NomError('DESTINATION_BUSY', 'O destinație selectată este ocupată. Nu s-a salvat niciun NIR.', 409)
            token = secrets.token_hex(32)
            cursor.execute('DELETE FROM VC_ContextLock WHERE SubunitId=%s AND GestiuneId=%s', (s, g))
            cursor.execute('INSERT INTO VC_ContextLock (SubunitId,GestiuneId,HolderEmail,SessionToken,ExpiresAt) '
                           'VALUES (%s,%s,%s,%s,NOW()+INTERVAL 120 SECOND)', (s, g, ctx['email'], token))
            temporary.append((s, g, token))
    try:
        yield
    finally:
        for s, g, token in temporary:
            cursor.execute('DELETE FROM VC_ContextLock WHERE SubunitId=%s AND GestiuneId=%s AND SessionToken=%s', (s, g, token))
