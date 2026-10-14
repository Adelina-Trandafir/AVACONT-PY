"""SLICE-ADE6-06: atomic group histories and audited child form commands."""
from datetime import date, datetime
from .domain import require, validate_cnp, group_educators
from .service import validate_values, validate_educator, prepare_attendance, month_order


def iso_day(value):
    return str(value)[:10] if value else None


def catalog_data(repo, include_hidden=False, month_id=None):
    if month_id is not None:
        repo.get('LunaD', month_id)
        groups = repo.month_groups(month_id, include_hidden)
    else:
        groups = repo.rows('Grupe') if include_hidden else repo.rows('Grupe', Ascunsa=0)
    history = repo.rows('Grupe_Educator')
    for group in groups:
        group['Educators'] = group_educators(group, history, date.today().isoformat()) or ''
    return {'groups': groups}


def set_group_hidden(repo, body):
    require(type(body.get('hidden')) is bool, 'BOOLEAN', 'Alegeți Da sau Nu.', 400)
    group = repo.get('Grupe', body.get('id'))
    require(group['Version'] == body.get('version'), 'CONFLICT', 'Grupa s-a modificat. Reîncărcați datele.')
    if body['hidden'] and not group.get('Ascunsa'):
        active_children = sum(not child.get('Plecat') for child in repo.rows('Platitori', IDG=group['IDG']))
        require(not active_children or body.get('confirm_active') is True, 'GROUP_ACTIVE_CHILDREN',
                f'Grupa are {active_children} copii fără bifa Plecat. Confirmați ascunderea grupei.')
    return repo.update('Grupe', group, {'Ascunsa': body['hidden']})


def save_group(repo, body):
    values = body.get('values')
    require(isinstance(values, dict) and set(values) <= {'Grupa', 'InchisaDinAn'},
            'FIELDS', 'Datele grupei sunt invalide.', 400)
    values = validate_values('Grupe', values)
    require(bool((values.get('Grupa') or '').strip()), 'NAME', 'Numele grupei este obligatoriu.', 400)
    closed_year = values.get('InchisaDinAn')
    require(closed_year is None or 1900 <= closed_year <= 9999, 'YEAR', 'Anul închiderii este invalid.', 400)
    existing = repo.get('Grupe', body['id']) if body.get('id') is not None else None
    if existing:
        require(existing['Version'] == body.get('version'), 'CONFLICT', 'Grupa s-a modificat. Reîncărcați datele.')
    educators = body.get('educators', [])
    require(isinstance(educators, list) and len(educators) <= 100, 'VALUES', 'Lista educatorilor este invalidă.', 400)
    normalized = []
    identities = set()
    for item in educators:
        require(isinstance(item, dict) and isinstance(item.get('values'), dict), 'VALUES', 'Educator invalid.', 400)
        fields = item['values']
        fields = validate_educator(fields)
        start, end = iso_day(fields.get('DeLa')), iso_day(fields.get('PanaLa'))
        require(bool(start), 'DATE', 'Completați data «Începând cu».', 400)
        require(not end or start <= end, 'DATE', 'Data «Până la» nu poate fi înaintea începerii.', 400)
        old = repo.get('Grupe_Educator', item['id']) if item.get('id') is not None else None
        if old:
            require(existing and old['IDG'] == existing['IDG'], 'PARENT', 'Educatorul nu aparține acestei grupe.', 400)
            require(old['IDGE'] not in identities, 'VALUES', 'Educator trimis de două ori.', 400)
            require(old['Version'] == item.get('version'), 'CONFLICT', 'Perioada educatorului s-a modificat.')
            identities.add(old['IDGE'])
        normalized.append((old, fields))
    # Validate each educator's periods against edited and untouched rows; several educators may coexist.
    periods = [fields for _, fields in normalized]
    if existing:
        periods.extend(row for row in repo.rows('Grupe_Educator')
                       if row['IDG'] == existing['IDG'] and row['IDGE'] not in identities)
    for index, first in enumerate(periods):
        for second in periods[index + 1:]:
            if (first.get('Educator') or '').strip().casefold() != (second.get('Educator') or '').strip().casefold():
                continue
            require((iso_day(first.get('PanaLa')) or '9999-12-31') < (iso_day(second.get('DeLa')) or '0001-01-01')
                    or (iso_day(second.get('PanaLa')) or '9999-12-31') < (iso_day(first.get('DeLa')) or '0001-01-01'),
                    'PERIOD', 'Perioadele aceluiași educator nu pot fi suprapuse.', 400)
    group = repo.update('Grupe', existing, values) if existing else repo.insert('Grupe', {**values, 'Tip': 'NORMALA'})
    for old, fields in normalized:
        if old:
            repo.update('Grupe_Educator', old, fields)
        else:
            repo.insert('Grupe_Educator', {**fields, 'IDG': group['IDG']})
    return group


def save_child_with_payer(repo, body, username, may_transfer):
    """A new child and its first parent in ONE transaction: a refused parent leaves no child behind."""
    from .service import save_catalog
    payer = body.get('payer')
    require(isinstance(body.get('child'), dict) and isinstance(payer, dict) and isinstance(payer.get('values'), dict),
            'FIELDS', 'Datele copilului și ale părintelui sunt obligatorii.', 400)
    require(str(payer['values'].get('Nume') or '').strip(), 'NAME', 'Numele plătitorului este obligatoriu.', 400)
    child = save_child(repo, {'id': None, 'values': body['child']}, username, may_transfer)
    parent = save_catalog(repo, 'Platitori_sub', {'id': None, 'values': {**payer['values'], 'IDP': child['IDP']}})
    return {'child': child, 'payer': parent}


def save_child(repo, body, username, may_transfer):
    values = body.get('values')
    allowed = {'Nume', 'CNP', 'IDG', 'DataIntrare', 'Plecat', 'DataIesire'}
    require(isinstance(values, dict) and set(values) <= allowed, 'FIELDS', 'Datele copilului sunt invalide.', 400)
    values = validate_values('Platitori', values)
    existing = repo.get('Platitori', body['id']) if body.get('id') is not None else None
    if existing:
        require(existing['Version'] == body.get('version'), 'CONFLICT', 'Copilul s-a modificat. Reîncărcați datele.')
    merged = {**(existing or {}), **values}
    require(bool((merged.get('Nume') or '').strip()), 'NAME', 'Numele copilului este obligatoriu.', 400)
    if 'CNP' in values:
        validate_cnp(values['CNP'])
    require(merged.get('IDG') is not None, 'PARENT', 'Selectați grupa copilului.', 400)
    group = repo.get('Grupe', merged['IDG'])
    require(not group.get('InchisaDinAn') or group['IDG'] == (existing or {}).get('IDG'),
            'CLOSED_GROUP', 'Nu se poate înscrie un copil într-o grupă închisă.', 400)
    entry, leaving = iso_day(merged.get('DataIntrare')), iso_day(merged.get('DataIesire'))
    require(not entry or not leaving or entry <= leaving, 'DATE', 'Data ieșirii nu poate fi înaintea intrării.', 400)
    require(not merged.get('Plecat') or leaving, 'DATE', 'Completați data ieșirii pentru copilul plecat.', 400)
    require(merged.get('Plecat') or not leaving, 'DATE', 'Bifați Plecat sau ștergeți data ieșirii.', 400)
    moving = existing and existing['IDG'] != merged['IDG']
    leaving_changed = existing and bool(existing.get('Plecat')) != bool(merged.get('Plecat'))
    if moving or leaving_changed:
        require(may_transfer, 'FORBIDDEN', 'Nu aveți dreptul de a muta copilul sau de a schimba starea Plecat.', 403)
    saved = repo.update('Platitori', existing, values) if existing else repo.insert('Platitori', values)
    if not saved.get('Plecat'):
        from .parent_identity import provision_existing
        provision_existing(repo)
    events = []
    if not existing:
        events.append(('INTRARE', entry))
        # A new child goes straight into the attendance of the month open now (the latest open one): no extra step.
        open_months = [row for row in repo.rows('LunaD') if not row['Inchisa']]
        if open_months and not saved.get('Plecat'):
            current = max(open_months, key=month_order)
            prepare_attendance(repo, {'month_id': current['IDL'], 'group_id': saved['IDG'], 'person_id': saved['IDP']})
    if moving:
        events.append(('MUTARE', None))
        # Closed attendance and snapshots retain their original group.
        open_ids = {row['IDL'] for row in repo.rows('LunaD') if not row['Inchisa']}
        for attendance in repo.rows('Prezenta'):
            if attendance['IDP'] == saved['IDP'] and attendance['IDL'] in open_ids:
                repo.update('Prezenta', attendance, {'IDG': saved['IDG']})
    if leaving_changed or not existing and merged.get('Plecat'):
        events.append(('PLECARE' if merged.get('Plecat') else 'REVENIRE', leaving if merged.get('Plecat') else None))
    elif existing and iso_day(existing.get('DataIesire')) != iso_day(merged.get('DataIesire')):
        events.append(('IESIRE_DEFINITIVA', leaving))
    for kind, day in events:
        repo.insert('Platitori_Istoric', {'IDP': saved['IDP'], 'Data': day or datetime.now().isoformat(timespec='seconds'),
                    'Tip': kind, 'IDG_Vechi': existing.get('IDG') if existing else None, 'IDG_Nou': saved['IDG'],
                    'Dedus': False, 'Utilizator': username})
    return saved
