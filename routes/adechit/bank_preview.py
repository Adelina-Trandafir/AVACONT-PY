"""Reviewable proposals for recurring bank receipts; no writes until explicit selection."""
import re

from .domain import require
from .bank_associations import available, default_explanation, associate
from .bank_targets import reviewed_target


def fiscal(value):
    text = re.sub(r'\s+', '', str(value or '')).upper()
    return text[2:] if text.startswith('RO') else text


def account(value):
    return re.sub(r'\s+', '', str(value or '')).upper()


def preview(repo, args):
    reference = args.get('IDL', '')
    require(str(reference).isdigit(), 'FILTER', 'Alegeți luna pentru previzualizare.', 400)
    month_id = int(reference)
    month = repo.get('LunaD', month_id)
    movements = available(repo, args)
    defaults = default_explanation(repo, month_id)
    children = {r['IDP']: r for r in repo.rows('Platitori')}
    groups = {r['IDG']: r for r in repo.rows('Grupe')}
    parents = repo.rows('Platitori_sub')
    active = {r['IDP']: r for r in parents if r.get('Activ')}
    targets = []
    for child in children.values():
        parent = active.get(child['IDP'])
        group = groups.get(child['IDG'], {})
        if not parent or not group or group.get('Ascunsa') and args.get('include_hidden') != '1':
            continue
        targets.append({'child_id': child['IDP'], 'child_version': child['Version'],
                        'child': child['Nume'], 'group': group.get('Grupa') or '',
                        'month_id': month_id, 'month_version': month['Version'],
                        'payer_id': parent['IDS'], 'payer_version': parent['Version'],
                        'payer': parent['Nume']})
    target_by_child = {t['child_id']: t for t in targets}
    # Count matches in ALL subunits, including restricted ones. Only this subunit's names are returned.
    # A shared parent account must never silently choose one of their children or schools.
    known = {}
    for row in repo.query('SELECT p.IDP,p.SubunitId,p.CUI,p.Cont FROM AD_Platitori_sub p '
                          'JOIN AD_Platitori c ON c.IDP=p.IDP AND c.SubunitId=p.SubunitId '
                          'JOIN AD_Subunits s ON s.SubunitId=p.SubunitId '
                          'WHERE p.Activ<>0 AND s.Active<>0 AND COALESCE(c.Plecat,0)=0'):
        key = (fiscal(row['CUI']), account(row['Cont']))
        if all(key):
            known.setdefault(key, set()).add((row['SubunitId'], row['IDP']))
    rows = []
    for source in movements['rows']:
        key = (fiscal(source['platitor_cui']), account(source['platitor_iban']))
        matches = known.get(key, set()) if all(key) else set()
        target = None
        if len(matches) == 1:
            subunit, child_id = next(iter(matches))
            if subunit == repo.scope():
                target = target_by_child.get(child_id)
        if target:
            status = 'Potrivire unică: Cod fiscal + Cont'
        elif len(matches) > 1:
            status = 'Potrivire ambiguă: alegeți explicit copilul'
        elif matches:
            status = 'Copil în altă subunitate sau într-o grupă ascunsă'
        else:
            status = 'Fără potrivire: alegeți explicit copilul'
        rows.append({**source, 'target': target, 'status': status, 'explanation': defaults})
    return {'rows': rows, 'targets': sorted(targets, key=lambda t: (t['group'], t['child'])),
            'next': movements['next'], 'month': month}


def save_selected(repo, db_name, body, email):
    items = body.get('items')
    require(isinstance(items, list) and 0 < len(items) <= 150,
            'VALUES', 'Bifați între 1 și 150 de încasări.', 400)
    require(all(isinstance(item, dict) and type(item.get('extract_id')) is int for item in items),
            'REQUEST', 'Selecție invalidă.', 400)
    require(len({item['extract_id'] for item in items}) == len(items), 'DUPLICATE', 'Același extras este bifat de două ori.', 400)
    # Validate the reviewed targets before renewing any payer. Several receipts may name the same parent.
    for item in items:
        reviewed_target(repo, item)
    renewed = {}
    saved = []
    for item in items:
        command = dict(item)
        # Only follow a renewal made by an earlier item of THIS reviewed transaction.
        if item['payer_id'] in renewed:
            command['payer_id'] = renewed[item['payer_id']]['IDS']
        # Parent identity reconciliation can update portal-code versions on another reviewed payer.
        # All reviewed versions were checked above, before any write, under the unit-wide lock.
        command['payer_version'] = repo.get('Platitori_sub', command['payer_id'])['Version']
        # A prior receipt can rewrite a closed-month snapshot and thus the attendance version.
        if item.get('attendance_id') is not None:
            command['attendance_version'] = repo.get('Prezenta', item['attendance_id'])['Version']
        result = associate(repo, db_name, command, email)
        renewed[item['payer_id']] = result['payer']
        saved.append(result)
    return {'rows': saved, 'count': len(saved)}
