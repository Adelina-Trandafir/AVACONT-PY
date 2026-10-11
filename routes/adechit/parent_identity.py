"""SLICE-AD11: unit-wide parent identity, serialized by AD_Lock(0)."""
import re
import secrets
from .domain import require


def normalize_email(value):
    email = str(value or '').strip().lower()
    require(not email or len(email) <= 255 and re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email),
            'EMAIL', 'Introduceți o adresă de email validă.', 400)
    return email


def contact_rows(repo):
    return repo.query('SELECT p.*,c.Nume AS ChildName,c.IDG,c.Plecat FROM AD_Platitori_sub p '
                      'LEFT JOIN AD_Platitori c ON c.IDP=p.IDP AND c.SubunitId=p.SubunitId')


def contact_conflicts(rows):
    """Legacy ambiguity disables every affected identity, including departed associations."""
    by_email, by_cnp = {}, {}
    for row in rows:
        cnp = str(row.get('CNP_Platitor') or '').strip()
        email = str(row.get('EMail') or '').strip().lower()
        if email:
            by_email.setdefault(email, set()).add(cnp)
            if cnp:
                by_cnp.setdefault(cnp, set()).add(email)
    return {email for email, cnps in by_email.items() if len(cnps) > 1}, {
        cnp for cnp, emails in by_cnp.items() if len(emails) > 1}


def identity_conflicted(repo, cnp):
    rows = contact_rows(repo)
    emails, cnps = contact_conflicts(rows)
    return cnp in cnps or any(str(r.get('CNP_Platitor') or '').strip() == cnp
                             and str(r.get('EMail') or '').strip().lower() in emails for r in rows)


def portal_issues(repo):
    rows = contact_rows(repo)
    emails, cnps = contact_conflicts(rows)
    result = []
    for row in rows:
        if row['SubunitId'] != repo.scope():
            continue
        cnp = str(row.get('CNP_Platitor') or '').strip()
        email = str(row.get('EMail') or '').strip().lower()
        reasons = []
        if email in emails:
            reasons.append('Email folosit de CNP-uri diferite')
        if cnp in cnps:
            reasons.append('Același CNP are emailuri diferite')
        if reasons:
            result.append({'id': row['IDS'], 'child_id': row['IDP'], 'group_id': row['IDG'],
                           'message': f"{row['Nume']} — CNP {cnp or '(necompletat)'}, email {email}, "
                                      f"copil {row['ChildName'] or '(necompletat)'}: " + '; '.join(reasons)})
    return sorted(result, key=lambda item: item['message'].casefold())


def reconcile_contacts(repo, rows, own_id=None):
    """Keep imported contacts untouched and publish only unambiguous portal emails."""
    bad_emails, bad_cnps = contact_conflicts(rows)
    identities = {r['CNP']: r for r in repo.query('SELECT * FROM AD_PortalParents')}
    groups = {}
    for row in rows:
        cnp = str(row.get('CNP_Platitor') or '').strip()
        if re.fullmatch(r'[0-9]{13}', cnp):
            groups.setdefault(cnp, []).append(row)
    # Release emails before assigning corrected contacts; the unique key remains enforced.
    repo.execute('UPDATE AD_PortalParents SET Email=NULL')
    codes = {}
    for cnp, parents in groups.items():
        emails = {str(p.get('EMail') or '').strip().lower() for p in parents} - {''}
        email = next(iter(emails)) if len(emails) == 1 else None
        if cnp in bad_cnps or emails & bad_emails:
            email = None
        identity = identities.get(cnp)
        code = identity['CodAccesPortal'] if identity else None
        if not code and any(not p.get('Plecat') for p in parents):
            code = secrets.token_hex(16)
        codes[cnp] = code
        if identity:
            repo.execute('UPDATE AD_PortalParents SET Email=%s,CodAccesPortal=%s WHERE CNP=%s', (email, code, cnp))
        else:
            repo.execute('INSERT INTO AD_PortalParents (CNP,Email,CodAccesPortal) VALUES (%s,%s,%s)', (cnp, email, code))
    for row in rows:
        code = codes.get(str(row.get('CNP_Platitor') or '').strip())
        if row.get('IDS') != own_id and row.get('CodAccesPortal') != code:
            repo.execute('UPDATE AD_Platitori_sub SET CodAccesPortal=%s,Version=Version+1 WHERE IDS=%s', (code, row['IDS']))
    return codes


PERSON_SELECT = ('SELECT p.IDS,p.Nume,p.CNP_Platitor,p.EMail,p.SubunitId,c.IDP,c.IDG,c.Nume AS ChildName,c.Plecat,g.Grupa AS GroupName '
                 'FROM AD_Platitori_sub p '
                 'LEFT JOIN AD_Platitori c ON c.IDP=p.IDP AND c.SubunitId=p.SubunitId '
                 'LEFT JOIN AD_Grupe g ON g.IDG=c.IDG AND g.SubunitId=c.SubunitId ')


def open_subunits(repo):
    """Subunits of the DC this account may open: the only ones whose people are named or linked."""
    return {s['SubunitId']: s['Name'] for s in repo.subunits if s['Active'] and s['Allowed']}


def person_detail(repo, row):
    """A parent/child/group the page names as links; another subunit is linkable when the account may open it."""
    opened = open_subunits(repo)
    return {'parent_id': row['IDS'], 'parent': row['Nume'], 'cnp': row['CNP_Platitor'] or '', 'email': row['EMail'] or '',
            'child_id': row['IDP'], 'child': row['ChildName'] or '', 'left': bool(row.get('Plecat')),
            'group_id': row['IDG'], 'group': row['GroupName'] or '',
            'subunit_id': row['SubunitId'], 'subunit': opened.get(row['SubunitId'], ''), 'show_subunit': len(opened) > 1,
            'linkable': row['SubunitId'] in opened}


def similar_contacts(repo, cnp, email, exclude_id):
    """Other parents with this CNP / this email anywhere in the DC (every subunit the account may open, departed children
    included): information for the operator while typing, not a refusal."""
    opened = list(open_subunits(repo))
    result = {'cnp': [], 'email': []}
    if not opened:
        return result
    marks = ','.join(['%s'] * len(opened))
    base = PERSON_SELECT + f'WHERE p.IDS<>%s AND p.SubunitId IN ({marks}) AND '
    if re.fullmatch(r'[0-9]{13}', cnp):
        result['cnp'] = [person_detail(repo, r) for r in repo.query(base + 'TRIM(p.CNP_Platitor)=%s', (exclude_id, *opened, cnp))]
    if email:
        result['email'] = [person_detail(repo, r) for r in repo.query(base + 'LOWER(TRIM(p.EMail))=%s', (exclude_id, *opened, email))]
    return result


def prepare_parent(repo, values, existing=None):
    changing_email = existing and 'EMail' in values
    merged = {**(existing or {}), **values}
    email = normalize_email(merged.get('EMail'))
    cnp = str(merged.get('CNP_Platitor') or '').strip()
    own_id = (existing or {}).get('IDS', -1)
    old_cnp = str((existing or {}).get('CNP_Platitor') or '').strip()
    if old_cnp and old_cnp != cnp:
        remaining = repo.query('SELECT IDS FROM AD_Platitori_sub WHERE TRIM(CNP_Platitor)=%s AND IDS<>%s', (old_cnp,own_id))
        if not remaining:
            # Release a corrected identity's email and invalidate its previous login context.
            repo.execute('DELETE FROM AD_PortalParents WHERE CNP=%s', (old_cnp,))
    contact_changed = not existing or cnp != old_cnp or email != str(existing.get('EMail') or '').strip().lower()
    if email and contact_changed:
        duplicates = repo.query(PERSON_SELECT +
            'WHERE LOWER(TRIM(p.EMail))=%s AND p.IDS<>%s AND COALESCE(TRIM(p.CNP_Platitor),\'\')<>%s '
            'AND p.SubunitId=%s AND COALESCE(c.Plecat,0)=0', (email, own_id, cnp, repo.scope()))   # active children, current subunit only
        details = [person_detail(repo, r) for r in duplicates]
        require(not duplicates, 'EMAIL_CONFLICT', 'Adresa de email este deja utilizată de ' + '; '.join(
            f"părintele {r['Nume']}, CNP {r['CNP_Platitor'] or '(necompletat)'}, copil {r['ChildName'] or '(necompletat)'}"
            + (f", grupa {r['GroupName']}" if r['GroupName'] else '')
            for r in duplicates) + '.', 409, details)
        if not existing or old_cnp != cnp:
            other_contacts = repo.query('SELECT p.EMail FROM AD_Platitori_sub p '
                                        'LEFT JOIN AD_Platitori c ON c.IDP=p.IDP AND c.SubunitId=p.SubunitId '
                                        'WHERE TRIM(p.CNP_Platitor)=%s AND p.IDS<>%s AND p.SubunitId=%s AND COALESCE(c.Plecat,0)=0 '
                                        "AND TRIM(COALESCE(p.EMail,''))<>'' AND LOWER(TRIM(p.EMail))<>%s", (cnp, own_id, repo.scope(), email))
            require(not other_contacts, 'EMAIL_CONFLICT',
                    'Acest CNP are deja alt email. Corectați înregistrările existente ale părintelui.', 409)
    values['EMail'] = email or None
    values['CNP_Platitor'] = cnp or None
    identities = repo.query('SELECT * FROM AD_PortalParents WHERE CNP=%s', (cnp,))
    identity = identities[0] if identities else None
    # An edit of the same identity changes its email across all child associations.
    changing_email = changing_email and contact_changed and old_cnp == cnp
    if identity and not existing and not changing_email:
        require(not email or not identity['Email'] or email == identity['Email'], 'EMAIL_CONFLICT',
                'Acest CNP are deja alt email. Modificați emailul în înregistrarea existentă a părintelui.', 409)
        email = email or identity['Email'] or ''
        values['EMail'] = email or None
    if changing_email:
        repo.execute('UPDATE AD_Platitori_sub SET EMail=%s,Version=Version+1 WHERE TRIM(CNP_Platitor)=%s AND IDS<>%s',
                     (email or None, cnp, own_id))
    child = repo.get('Platitori', merged['IDP'])
    rows = [r for r in contact_rows(repo) if r['IDS'] != own_id]
    rows.append({**merged, **values, 'IDS': own_id, 'Plecat': child.get('Plecat')})
    values['CodAccesPortal'] = reconcile_contacts(repo, rows, own_id).get(cnp)


def provision_existing(repo):
    """Provision imported parents, never accepting credentials from a source extract."""
    reconcile_contacts(repo, contact_rows(repo))
