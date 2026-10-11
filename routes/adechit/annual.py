"""August rollover drafts and changes, inside the month-close transaction."""
from .domain import require
from .service import validate_values


def preview(repo, month_id):
    month = repo.get('LunaD', month_id)
    require(month['Luna'] == 8 and not month['Inchisa'], 'ANNUAL_MONTH', 'Închiderea anuală este disponibilă pentru august deschis.')
    groups = [row for row in repo.rows('Grupe') if row.get('Tip') != 'PLECATI'
              and (not row.get('InchisaDinAn') or row['InchisaDinAn'] > month['Anul'])]
    ids = {row['IDG'] for row in groups}
    children = [row for row in repo.rows('Platitori') if not row.get('Plecat')]
    require(all(row['IDG'] in ids for row in children), 'GROUP', 'Există copii activi în grupe închise sau speciale. Corectați catalogul înainte de închidere.')
    history = repo.rows('Grupe_Educator')
    day = f"{month['Anul']:04d}-09-01"
    for group in groups:
        periods = [row for row in history if row['IDG'] == group['IDG']]
        group['educators'] = list(dict.fromkeys(row['Educator'] for row in periods
            if row.get('Educator') and (not row.get('DeLa') or row['DeLa'][:10] <= day)
            and (not row.get('PanaLa') or row['PanaLa'][:10] >= day))) if periods else ([group['Educator']] if group.get('Educator') else [])
    return {'month': month, 'groups': groups, 'children': children, 'history': history}


def validate(repo, month, draft):
    require(isinstance(draft, dict), 'ANNUAL_REQUIRED', 'Completați fereastra de închidere anuală înainte de a închide august.', 400)
    current = preview(repo, month['IDL'])
    require(draft.get('version') == month['Version'], 'CONFLICT', 'Luna s-a modificat. Redeschideți fereastra anuală.')
    for key, identity in (('groups', 'IDG'), ('children', 'IDP'), ('history', 'IDGE')):
        items = draft.get(key)
        require(isinstance(items, list) and all(isinstance(row, dict) and type(row.get('id')) is int for row in items),
                'ANNUAL_VALUES', 'Planul anual este invalid.', 400)
        expected = {row[identity]: row for row in current[key]}
        require(len(items) == len(expected) and {row['id'] for row in items} == set(expected),
                'CONFLICT', 'Catalogul s-a modificat. Redeschideți fereastra anuală.')
        require(all(row.get('version') == expected[row['id']]['Version'] for row in items),
                'CONFLICT', 'Datele s-au modificat. Redeschideți fereastra anuală.')
    new_groups = draft.get('new_groups', [])
    require(isinstance(new_groups, list) and len(new_groups) <= 100
            and all(isinstance(row, dict) and type(row.get('id')) is int and row['id'] < 0 for row in new_groups),
            'GROUP', 'Lista grupelor noi este invalidă.', 400)
    require(len({row['id'] for row in new_groups}) == len(new_groups), 'GROUP', 'Grupă nouă duplicată.', 400)
    groups = {row['IDG'] for row in current['groups']} | {row['id'] for row in new_groups}
    for row in draft['groups'] + new_groups:
        validate_values('Grupe', {'Grupa': row.get('name')})
        require(isinstance(row.get('name'), str) and bool(row['name'].strip()), 'NAME', 'Completați numele tuturor grupelor.', 400)
        names = row.get('educators')
        require(isinstance(names, list) and len(names) <= 100 and all(isinstance(name, str) and name.strip() for name in names),
                'NAME', 'Lista educatorilor este invalidă.', 400)
        require(len({name.strip().casefold() for name in names}) == len(names), 'NAME', 'Un educator apare de două ori în aceeași grupă.', 400)
        for name in names:
            validate_values('Grupe_Educator', {'Educator': name.strip()})
        if row['id'] < 0:
            require(bool(names), 'EDUCATOR', 'Fiecare grupă nouă trebuie să aibă cel puțin un educator.', 400)
    names = {row['id']: row['name'].strip().casefold() for row in draft['groups'] + new_groups}
    other_names = {(row.get('Grupa') or '').strip().casefold() for row in repo.rows('Grupe') if row['IDG'] not in names}
    for row in new_groups:
        name = names[row['id']]
        require(list(names.values()).count(name) == 1 and name not in other_names,
                'NAME_UNIQUE', 'Denumirea unei grupe noi trebuie să fie unică.', 400)
    for row in draft['children']:
        require(type(row.get('departed')) is bool and type(row.get('group_id')) is int and row['group_id'] in groups,
                'GROUP', 'Alegeți grupa destinație și starea fiecărui copil.', 400)
    for row in new_groups:
        require(any(child['group_id'] == row['id'] and not child['departed'] for child in draft['children']),
                'CHILD_REQUIRED', 'Fiecare grupă nouă trebuie să aibă cel puțin un copil care rămâne.', 400)
    return current


def apply(repo, month, draft, current, username):
    end = f"{month['Anul']:04d}-08-31"
    start = f"{month['Anul']:04d}-09-01"
    groups = {row['IDG']: row for row in current['groups']}
    new_ids = {}
    for item in draft.get('new_groups', []):
        group = repo.insert('Grupe', {'Grupa': item['name'].strip(), 'Tip': 'NORMALA'})
        new_ids[item['id']] = group['IDG']
        for name in item['educators']:
            repo.insert('Grupe_Educator', {'IDG': group['IDG'], 'Educator': name.strip(), 'DeLa': start})
    for item in draft['groups']:
        group = groups[item['id']]
        if item['name'].strip() != group['Grupa']:
            repo.update('Grupe', group, {'Grupa': item['name'].strip()})
        names = [name.strip() for name in item['educators']]
        if names == group['educators']:
            continue
        periods = [row for row in current['history'] if row['IDG'] == group['IDG']]
        require(not any(row.get('DeLa') and row['DeLa'][:10] > start for row in periods),
                'EDUCATOR_PERIOD', 'Există educatori programați după 1 septembrie. Corectați perioadele în catalog înainte de închidere.', 400)
        # Preserve all earlier periods and replace assignments from September onward.
        if not periods and group.get('Educator'):
            repo.insert('Grupe_Educator', {'IDG': group['IDG'], 'Educator': group['Educator'], 'PanaLa': end})
        for row in periods:
            if row.get('DeLa') and row['DeLa'][:10] >= start:
                repo.delete('Grupe_Educator', row)
            elif not row.get('PanaLa') or row['PanaLa'][:10] >= start:
                repo.update('Grupe_Educator', row, {'PanaLa': end})
        for name in names:
            repo.insert('Grupe_Educator', {'IDG': group['IDG'], 'Educator': name, 'DeLa': start})
    children = {row['IDP']: row for row in current['children']}
    for item in draft['children']:
        child = children[item['id']]
        target = new_ids.get(item['group_id'], item['group_id'])
        changes = {'Plecat': True, 'DataIesire': end} if item['departed'] else (
            {'IDG': target} if target != child['IDG'] else {})
        if not changes:
            continue
        require(not item['departed'] or not child.get('DataIntrare') or child['DataIntrare'][:10] <= end,
                'DATE', 'Un copil are data intrării după încheierea anului.', 400)
        repo.update('Platitori', child, changes)
        repo.insert('Platitori_Istoric', {'IDP': child['IDP'], 'Data': end if item['departed'] else start,
            'Tip': 'PLECARE' if item['departed'] else 'MUTARE', 'IDG_Vechi': child['IDG'],
            'IDG_Nou': changes.get('IDG', child['IDG']), 'Dedus': False, 'Utilizator': username})
