"""ADE commands. The caller owns the transaction and unit-wide mutation lock."""
import hashlib
import json
import math
import re
from calendar import monthrange
from datetime import date, datetime
from decimal import Decimal
from .calculations import calculate, active, total
from .domain import SCHEMA, EDITABLE, require, nz, access_long, update_situation, DomainError, validate_cnp

MONTHS = ('ianuarie februarie martie aprilie mai iunie iulie august septembrie octombrie noiembrie decembrie').split()


def next_period(month, year):
    return (1, year + 1) if month == 12 else (month + 1, year)


def month_days(month):
    """Working days of a month row; months created before the column existed fall back to the calendar rule."""
    return month['ZileLuna'] if month.get('ZileLuna') is not None else work_days(month['Luna'], month['Anul'])


def work_days(month, year):
    return sum(date(year, month, day).weekday() < 5 for day in range(1, monthrange(year, month)[1] + 1))


def validate_values(table, values):
    result = dict(values)
    for key, value in values.items():
        require(key in SCHEMA[table]['fields'], 'FIELD', 'Câmp necunoscut.', 400)
        field = SCHEMA[table]['fields'][key]
        if value is None:
            continue
        kind = field['type']
        if kind in ('Long', 'Double', 'AutoNumber'):
            require(type(value) in (int, float) and math.isfinite(value), 'NUMBER', 'Valoare numerică invalidă.', 400)
            if kind != 'Double':
                require(value == int(value) and -2147483648 <= value <= 2147483647, 'INTEGER', 'Este necesar un număr întreg Access Long.', 400)
        elif kind == 'Yes/No':
            require(type(value) is bool or type(value) is int and value in (0, 1, -1), 'BOOLEAN', 'Alegeți Da sau Nu.', 400)
            result[key] = bool(value)
        elif kind == 'Date/Time':
            require(isinstance(value, str), 'DATE', 'Dată invalidă.', 400)
            try:
                datetime.fromisoformat(value)
            except ValueError as error:
                raise DomainError('DATE', 'Dată invalidă.', 400) from error
        else:
            require(isinstance(value, str) and (not field['size'] or len(value) <= field['size']), 'TEXT', 'Text prea lung sau invalid.', 400)
    return result


def validate_educator(values):
    """Optional educator contacts belong to the web catalog, not parent identities."""
    from .parent_identity import normalize_email
    require(set(values) <= {'Educator', 'EMail', 'CNP', 'DeLa', 'PanaLa'},
            'FIELDS', 'Câmpuri educator invalide.', 400)
    fields = validate_values('Grupe_Educator', values)
    require(bool((fields.get('Educator') or '').strip()), 'NAME', 'Numele educatorului este obligatoriu.', 400)
    fields['Educator'] = fields['Educator'].strip()
    if 'EMail' in fields:
        fields['EMail'] = normalize_email(fields['EMail']) or None
    if 'CNP' in fields:
        fields['CNP'] = (fields['CNP'] or '').strip() or None
        validate_cnp(fields['CNP'])
    return fields


def save_catalog(repo, table, body, previous_active=()):
    require(table in EDITABLE and table != 'Prezenta', 'TABLE', 'Catalogul nu este editabil.', 400)
    changes = body.get('values', {})
    require(isinstance(changes, dict) and bool(changes), 'VALUES', 'Nu există modificări.', 400)
    creating = body.get('id') is None
    allowed = set(EDITABLE[table]) | ({'IDG'} if table == 'Platitori' and creating else set())
    require(set(changes) <= allowed, 'READ_ONLY', 'Câmp calculat sau operație care necesită comandă separată.', 400)
    changes = validate_values(table, changes)
    for field in ('CNP', 'CNP_Platitor'):
        if field in changes:
            validate_cnp(changes[field])
    row = None if creating else repo.get(table, body['id'])
    if row:
        require(row['Version'] == body.get('version'), 'CONFLICT', 'Datele s-au modificat. Reîncărcați rândul.')
    merged = {**(row or {}), **changes}
    for field, parent in {'IDG': 'Grupe', 'IDV': 'ValoriTaxe', 'IDP': 'Platitori', 'IDS': 'Platitori_sub'}.items():
        if field in changes or creating and field in SCHEMA[table]['fields'] and field != SCHEMA[table]['key']:
            require(merged.get(field) is not None, 'PARENT', 'Selectați înregistrarea asociată.', 400)
            repo.get(parent, merged[field])
    if 'Nume' in SCHEMA[table]['fields']:
        require(bool((merged.get('Nume') or '').strip()), 'NAME', 'Numele este obligatoriu.', 400)
    if table == 'Grupe':
        require(bool((merged.get('Grupa') or '').strip()), 'NAME', 'Denumirea grupei este obligatorie.', 400)
    if table == 'Platitori_sub':
        from .parent_identity import prepare_parent
        prepare_parent(repo, changes, row)
        if creating:
            changes['Activ'] = True
            merged['Activ'] = True
        if merged.get('Activ'):
            for other in repo.rows(table):
                if other['IDP'] == merged['IDP'] and other['IDS'] != body.get('id') and other.get('Activ'):
                    repo.update(table, other, {'Activ': False})
    if table == 'ValoriTaxe':
        require(nz(merged.get('TaxaZilnica')) > 0, 'TAX', 'Valoarea taxei trebuie să fie mai mare decât zero.', 400)
        # A tax change reaches a month only when that month is opened: a tax some month already uses keeps its value.
        # (a tax with no value yet may always be filled in)
        if row and nz(row.get('TaxaZilnica')) and 'TaxaZilnica' in changes and Decimal(str(changes['TaxaZilnica'])) != Decimal(str(row['TaxaZilnica'])):
            used = sorted(((month['Anul'], month['Luna']) for month in repo.rows('LunaD') if month.get('IDV') == row['IDV']))
            require(not used, 'TAX_USED',
                    f'Taxa este folosită de {len(used)} {"lună" if len(used) == 1 else "luni"} '
                    f'(de la {MONTHS[used[0][1] - 1]} {used[0][0]}) și valoarea ei nu mai poate fi modificată. '
                    'Adăugați o taxă nouă; ea se aplică de la luna următoare deschisă.', 409)
        require(bool((merged.get('Expl') or '').strip()), 'TAX', 'Explicația taxei este obligatorie.', 400)
        start = merged.get('DeLa')
        require((not creating and not start) or isinstance(start, str) and re.fullmatch(r'[1-9][0-9]{3}-(0[1-9]|1[0-2])', start),
                'TAX_PERIOD', 'Completați începutul taxei în format lună/an.', 400)
        require(not merged.get('PanaLa') or not start or start <= merged['PanaLa'],
                'TAX_PERIOD', 'Începutul taxei nu poate depăși sfârșitul.', 400)
        if creating or 'DeLa' in changes and start != row.get('DeLa'):
            require(start and start > max((item.get('DeLa') or '' for item in repo.rows(table)
                                          if item['IDV'] != body.get('id')), default=''),
                    'TAX_PERIOD', 'Taxa nouă trebuie să înceapă după taxele existente.', 400)
        if creating or merged.get('Activ'):
            previous_end = None
            if start:
                year, month = map(int, start.split('-'))
                previous_end = f'{year - 1:04d}-12' if month == 1 else f'{year:04d}-{month - 1:02d}'
            for other in repo.rows(table):
                if other['IDV'] == body.get('id'):
                    continue
                if other.get('Activ') or creating and (other['IDV'] in previous_active or other.get('DeLa') and not other.get('PanaLa')):
                    require(not start or not other.get('DeLa') or other['DeLa'] < start,
                            'TAX_PERIOD', 'Perioada taxei se suprapune cu taxa anterioară.', 400)
                    values = {'Activ': False}
                    if previous_end:
                        values['PanaLa'] = previous_end
                    repo.update(table, other, values)
            if merged.get('Activ'):
                changes['PanaLa'] = None
    return repo.insert(table, changes) if creating else repo.update(table, row, changes)


def save_catalog_batch(repo, body):
    """Save a modal's drafts atomically under the caller's transaction."""
    items = body.get('items')
    require(isinstance(items, list) and 0 < len(items) <= 500,
            'VALUES', 'Trimiteți între 1 și 500 de modificări.', 400)
    for item in items:
        require(isinstance(item, dict) and item.get('table') in ('Platitori', 'Platitori_sub', 'ValoriTaxe'),
                'TABLE', 'Catalogul nu este editabil în această fereastră.', 400)
        require(isinstance(item.get('values'), dict), 'VALUES', 'Modificări invalide.', 400)
    require(sum(bool(item['values'].get('Activ')) for item in items if item['table'] == 'ValoriTaxe') <= 1,
            'TAX', 'Selectați o singură taxă activă.', 400)
    # Activating a tax updates the old active row's version; process other edits first.
    items = sorted(items, key=lambda item: bool(item['values'].get('Activ')) if item['table'] == 'ValoriTaxe' else False)
    saved = []
    previous_active = [row['IDV'] for row in repo.rows('ValoriTaxe') if row.get('Activ')]
    for item in items:
        saved.append(save_catalog(repo, item['table'], item, previous_active))
        if item['table'] == 'ValoriTaxe' and item.get('id') is None:
            previous_active = []
    return {'rows': saved}


def save_attendance(repo, body, edit_closed=False):
    """`edit_closed` = the caller holds the AD_EDIT_INCHISA role (operation edit_closed, granted per account in
    KBotAdmin): then the days of the LAST closed month may still be changed, and the saved situation is rewritten."""
    row = repo.get('Prezenta', body['id'])
    month = repo.get('LunaD', row['IDL'])
    if month['Inchisa']:
        require(edit_closed, 'CLOSED', 'Prezența nu poate fi modificată într-o lună închisă.')
        closed = [r for r in repo.rows('LunaD') if r.get('Inchisa')]
        require(month['IDL'] == max(r['IDL'] for r in closed), 'NOT_LAST_CLOSED',
                'Prezența se poate modifica numai în ultima lună închisă.')
    require(row['Version'] == body.get('version'), 'CONFLICT', 'Prezența s-a modificat. Reîncărcați rândul.')
    changes = body.get('values', {})
    require(set(changes) == {'ZilePrezenta'}, 'READ_ONLY', 'Se editează numai zilele de prezență.', 400)
    days = changes['ZilePrezenta']
    require(type(days) is int and 0 <= days <= month_days(month), 'DAYS', 'Zilele trebuie să fie între zero și zilele lunii.', 400)
    tax = repo.get('ValoriTaxe', row['IDV'])
    require(tax.get('TaxaZilnica') is not None, 'TAX', 'Taxa zilnică lipsește.')
    # Prezenta.cIDV update path, without the excluded discounts.
    value = days * tax['TaxaZilnica']
    changes.update(ValoareContract=value, ValoareTotala=access_long(value))
    updated = repo.update('Prezenta', row, changes)
    if month['Inchisa']:
        rewrite_snapshot(repo, updated)
    return updated


def prepare_attendance(repo, body):
    month = repo.get('LunaD', body['month_id'])
    require(not month['Inchisa'], 'CLOSED', 'Copiii nu pot fi preluați într-o lună închisă.')
    group = repo.get('Grupe', body['group_id'])
    person_id = body.get('person_id')
    people = [row for row in repo.rows('Platitori')
              if row['IDG'] == group['IDG'] and not row.get('Plecat')
              and (person_id is None or row['IDP'] == person_id)]
    existing = {row['IDP'] for row in repo.rows('Prezenta')
                if row['IDL'] == month['IDL'] and row['IDG'] == group['IDG']}
    pending = [row for row in people if row['IDP'] not in existing]
    require(bool(pending), 'NO_NEW_CHILDREN', 'Nu există copii nepreluați pentru grupa selectată.')
    created = [repo.insert('Prezenta', {'IDG': person['IDG'], 'IDP': person['IDP'],
               'IDL': month['IDL'], 'IDV': month['IDV'],
               'ZilePrezenta': 0}) for person in pending]
    return {'created': created, 'count': len(created)}


def month_order(month):
    """Local order of a month inside its subunit. Access compared surrogate IDL values; the server now allocates
    IDL, so the order the rule needs lives in LunaD.Ordine (imported = the Access IDL, gaps included)."""
    return month['Ordine'] if month.get('Ordine') is not None else month['IDL']


def temporal(repo, month_id):
    require(month_id is not None, 'OLD_MONTH', 'Luna este prea veche pentru inserarea sau anularea documentului.')
    order = month_order(repo.get('LunaD', month_id))
    rows = repo.query('SELECT MAX(COALESCE(l.Ordine, l.IDL)) AS latest FROM AD_Prezenta p JOIN AD_LunaD l '
                      'ON l.IDL=p.IDL AND l.SubunitId=p.SubunitId WHERE p.SubunitId=%s', (repo.scope(),))
    latest = nz(rows[0]['latest']) if rows else 0
    require(order + 1 >= latest, 'OLD_MONTH', 'Luna este prea veche pentru inserarea sau anularea documentului.')


def document_date(value, month):
    try:
        day = date.fromisoformat(value)
    except (ValueError, TypeError) as error:
        raise DomainError('DATE', 'Introduceți o dată validă.', 400) from error
    # M04 confirmed by the user on 2026-10-08: preserve AND.
    require(not (day.month != month['Luna'] and day.year != month['Anul']), 'DATE_MONTH', 'Data nu corespunde lunii selectate.', 400)
    return day.isoformat()


def school_year_bounds(day):
    """First day of the school year that contains `day` and of the next one (ISO strings): September to August.
    Mirrors AD_Chitante.AnScolar (sql/AD_13), so a receipt number is unique per series and school year."""
    value = date.fromisoformat(day)
    start = value.year if value.month >= 9 else value.year - 1
    return f'{start}-09-01', f'{start + 1}-09-01'


def receipt_number_taken(repo, serie, number, day):
    """True when `serie`/`number` (without a sub-number) is already used by a receipt of the school year of `day` (cancelled receipts count).
    The web application hands out whole numbers; 751 and 751,1 are two different numbers (AD_Chitante.NumarSub)."""
    first, after = school_year_bounds(day)
    return bool(repo.query('SELECT IDC FROM AD_Chitante WHERE SubunitId=%s AND Serie=%s AND Numar=%s AND NumarSub=0 AND Data>=%s AND Data<%s',
                           (repo.scope(), serie, number, first, after)))


def rewrite_snapshot(repo, attendance):
    snapshots = [r for r in repo.rows('SS_Buget') if r['IDZ'] == attendance['IDZ']]
    require(len(snapshots) == 1, 'SNAPSHOT', 'Situația salvată lipsește sau este duplicată; operația a fost anulată.')
    snapshot = snapshots[0]
    payments = [r for r in repo.rows('Plati') if r['IDZ'] == attendance['IDZ'] and active(r, 'Anulata')]
    refunds = [r for r in repo.rows('Retur') if r['IDZ'] == attendance['IDZ'] and active(r, 'Anulat')]
    current = dict(snapshot)
    # The attendance values too: for a closed month whose days were edited (AD_EDIT_INCHISA) they changed; for a
    # payment or a cancellation they are the same values the snapshot already holds.
    for field in ('ZilePrezenta', 'ValoareContract', 'ValoareTotala'):
        current[field] = attendance[field]
    current['Plata'] = nz(total([r for r in payments if r.get('TIP') == 2], 'Plata'))
    current['Plati'] = access_long(nz(total([r for r in payments if r.get('TIP') is not None and r['TIP'] != 2], 'Plata')))
    current['Retur'] = access_long(nz(total(refunds, 'Suma')))
    current = update_situation(current, snapshot=True)
    # M02 approved: canceled documents also rewrite the saved child's values.
    details = []
    payment_ids = {r['IDPL'] for r in payments}
    for row in repo.rows('Chitante'):
        if row['IDPL'] in payment_ids:
            details.append('Ch:' + str(row['Numar']))
    for row in repo.rows('AlteDoc'):
        if row['IDPL'] in payment_ids:
            details.append('VI:' + (row.get('NrDoc') or ''))
    details.extend('Re:' + (r.get('NrDoc') or '') for r in refunds)
    current['Detalii'] = ';'.join(details)
    fields = ('ZilePrezenta', 'ValoareContract', 'ValoareTotala', 'Plata', 'Plati', 'Retur', 'Compensare',
              'Anticipat', 'SFD', 'SFC', 'Detalii')
    return repo.update('SS_Buget', snapshot, {k: current[k] for k in fields})


def receipt_explanation(config, month):
    """The configured receipt text for a month; '' when the template uses variables other than [LA] (M07)."""
    template = config['Explicatie']
    if '[' in template.replace('[LA]', '') or ']' in template.replace('[LA]', ''):
        return ''
    return template.replace('[LA]', f"{MONTHS[month['Luna'] - 1]} {month['Anul']}")


def emit_document(repo, db_name, body):
    kind = body.get('kind')
    require(kind in ('receipt', 'other', 'refund'), 'KIND', 'Tip de document invalid.', 400)
    attendance = repo.get('Prezenta', body['attendance_id'])
    month = repo.get('LunaD', attendance['IDL'])
    temporal(repo, month['IDL'])
    person = repo.get('Platitori_sub', body['payer_id'])
    require(person['IDP'] == attendance['IDP'] and bool(person['Activ']), 'PAYER', 'Selectați o persoană activă asociată copilului.', 400)
    day = document_date(body.get('date'), month)
    amount = body.get('amount')
    require(type(amount) in (int, float) and math.isfinite(amount) and amount != 0, 'AMOUNT', 'Suma trebuie să fie numerică și diferită de zero.', 400)
    if kind != 'refund':
        amount = access_long(amount)
        require(amount != 0, 'AMOUNT', 'Suma convertită este zero.', 400)
        if kind == 'receipt':
            require(-32768 <= amount <= 32767, 'CINT', 'Suma depășește limita CInt din formularul Access.', 400)
    links = {k: attendance[k] for k in ('IDP', 'IDZ', 'IDL')}
    provenance = {'OriginMonth': month['Luna'], 'OriginYear': month['Anul']}
    links.update(IDS=person['IDS'], Data=day)
    if kind == 'refund':
        values = {**links, **provenance, 'Suma': amount, 'NrDoc': body.get('number'), 'Explicatie': body.get('explanation'), 'Anulat': False}
        document = repo.insert('Retur', validate_values('Retur', values))
    else:
        config = None
        if kind == 'receipt':
            config = repo.receipt_config(lock=True)
            require(config is not None, 'CONFIG', 'Configurația chitanțelor nu a fost importată pentru această subunitate.')
            require(config['Numar'] > 0 and bool(config['Serie']), 'CONFIG', 'Seria sau următorul număr este invalid.')
            require(not receipt_number_taken(repo, config['Serie'], config['Numar'], day), 'NUMBER_USED',
                    'Numărul configurat este deja folosit în acest an școlar. Verificați importul și configurația.')
            explanation = (body.get('explanation') or '').strip() or receipt_explanation(config, month)
            require(bool(explanation.strip()), 'EXPLANATION', 'Explicația chitanței este obligatorie.', 400)
        else:
            require(bool(body.get('number')), 'DOCUMENT', 'Completați numărul documentului.', 400)
        payment = repo.insert('Plati', {**links, **provenance, 'Plata': amount,
                              'TIP': 2 if kind == 'receipt' else 1, 'Anulata': False, 'Valid': True})
        if config:
            document = repo.insert('Chitante', validate_values('Chitante', {'IDPL': payment['IDPL'], 'Data': day, 'Serie': config['Serie'],
                       'Numar': config['Numar'], 'Explicatie': explanation, 'IDL': month['IDL'], 'Anulata': False}))
            document = {**document, 'Valoare': amount}  # the amount lives on the payment; the response still shows it
            repo.execute('UPDATE AD_ReceiptConfig SET Numar=Numar+1, Version=Version+1 WHERE SubunitId=%s', (repo.scope(),))
        else:
            document = repo.insert('AlteDoc', validate_values('AlteDoc', {'IDPL': payment['IDPL'], 'NrDoc': body['number'],
                       'DataDoc': day, 'IDL': month['IDL'], 'Anulata': False,
                       'Explicatie': (body.get('explanation') or '').strip() or None}))
    if month['Inchisa']:
        rewrite_snapshot(repo, attendance)
    return document


def cancel_document(repo, body):
    table = {'receipt': 'Chitante', 'other': 'AlteDoc', 'refund': 'Retur'}.get(body.get('kind'))
    require(table is not None, 'KIND', 'Tip de document invalid.', 400)
    require(table != 'Retur', 'M03', 'Anularea restituirii așteaptă decizia privind salvarea motivului.')
    document = repo.get(table, body['id'])
    require(document['Version'] == body.get('version'), 'CONFLICT',
            'Documentul s-a modificat. Reîncărcați înainte de anulare.')
    payment = repo.get('Plati', document['IDPL'])
    require(not document['Anulata'] and not payment['Anulata'], 'CANCELED', 'Documentul este deja anulat.')
    temporal(repo, payment['IDL'])
    reason = body.get('reason')
    require(isinstance(reason, str) and 0 < len(reason.strip()) <= 255, 'REASON', 'Motivul anulării este obligatoriu (maximum 255 caractere).', 400)
    repo.update('Plati', payment, {'Anulata': True, 'Motivul': reason})
    document = repo.update(table, document, {'Anulata': True})
    attendance = repo.get('Prezenta', payment['IDZ'])
    if repo.get('LunaD', payment['IDL'])['Inchisa']:
        rewrite_snapshot(repo, attendance)
    return document


def close_month(repo, body, username=''):
    month = repo.get('LunaD', body['id'])
    require(not month['Inchisa'], 'CLOSED', 'Luna este deja închisă.')
    annual_state = None
    if month['Luna'] == 8:
        from . import annual
        annual_state = annual.validate(repo, month, body.get('annual'))
    else:
        require('annual' not in body, 'ANNUAL_MONTH', 'Planul anual se aplică numai în august.', 400)
    require(not any(r['IDL'] == month['IDL'] for r in repo.rows('SS_Buget')), 'SNAPSHOT', 'Există deja situații salvate pentru această lună.')
    rows, _ = calculate(repo.data(), month['IDL'])
    for row in rows:
        repo.insert('SS_Buget', {k: value for k, value in row.items() if k in SCHEMA['SS_Buget']['fields']})
    closed = repo.update('LunaD', month, {'Inchisa': True})
    if annual_state is not None:
        annual.apply(repo, month, body['annual'], annual_state, username)
    opened, reattached = create_next_month(repo, closed)
    return {'closed': closed, 'opened': opened, 'reattached': reattached}


def create_next_month(repo, closed):
    from .working_days import configured_days
    month_number, year = next_period(closed['Luna'], closed['Anul'])
    existing = [row for row in repo.rows('LunaD')
                if (row['Luna'], row['Anul']) == (month_number, year)]
    require(not existing, 'NEXT_MONTH', 'Luna următoare există deja.')
    taxes = [row for row in repo.rows('ValoriTaxe') if row.get('Activ')]
    require(len(taxes) == 1, 'ACTIVE_TAX', 'Trebuie să existe exact un set activ de taxe.')
    month = repo.insert('LunaD', {
        'IDV': taxes[0]['IDV'], 'Luna': month_number, 'Anul': year,
        'LunaT': MONTHS[month_number - 1], 'LA': f'{month_number:02d}{year}', 'Inchisa': False,
        'ZileLuna': configured_days(repo, month_number, year),
        'Ordine': max((month_order(row) for row in repo.rows('LunaD')), default=0) + 1,
    })
    attendance_by_person = {}
    snapshots = {row['IDP']: row for row in repo.rows('SS_Buget') if row['IDL'] == closed['IDL']}
    for person in repo.rows('Platitori'):
        group_id = person['IDG']
        if person.get('Plecat'):
            snapshot = snapshots.get(person['IDP'])
            if not snapshot or not (nz(snapshot.get('SFD')) or nz(snapshot.get('SFC'))):
                continue
            departed = [row for row in repo.rows('Grupe') if row.get('Tip') == 'PLECATI']
            require(len(departed) <= 1, 'GROUP', 'Există mai multe grupe speciale pentru copiii plecați.')
            group_id = (departed[0] if departed else repo.insert('Grupe', {'Grupa': 'Copii plecați', 'Tip': 'PLECATI', 'Ascunsa': True}))['IDG']
        attendance = repo.insert('Prezenta', {
            'IDP': person['IDP'], 'IDL': month['IDL'], 'IDV': month['IDV'], 'IDG': group_id,
            'ZilePrezenta': 0,
        })
        attendance_by_person[person['IDP']] = attendance
    reattached = {'Plati': 0, 'Retur': 0}
    for table in ('Plati', 'Retur'):
        for movement in repo.rows(table):
            if movement.get('IDL') is not None or (movement.get('OriginMonth'), movement.get('OriginYear')) != (month_number, year):
                continue
            attendance = attendance_by_person.get(movement['IDP'])
            require(attendance is not None, 'ORPHAN_PERSON', 'O mișcare orfană aparține unui copil care nu poate fi preluat în luna recreată.')
            repo.update(table, movement, {'IDL': month['IDL'], 'IDZ': attendance['IDZ']})
            reattached[table] += 1
    return month, reattached


def _movement_rows(repo, month_ids):
    return {table: [row for row in repo.rows(table) if row.get('IDL') in month_ids]
            for table in ('Plati', 'Retur')}


def reopen_month(repo, body):
    month = repo.get('LunaD', body['id'])
    require(bool(month['Inchisa']), 'OPEN', 'Luna este deja deschisă.')
    closed = [row for row in repo.rows('LunaD') if row.get('Inchisa')]
    require(closed and month['IDL'] == max(row['IDL'] for row in closed), 'NOT_LAST_CLOSED',
            'Poate fi redeschisă numai ultima lună închisă.')
    expected = next_period(month['Luna'], month['Anul'])
    following = [row for row in repo.rows('LunaD')
                 if not row.get('Inchisa') and (row['Luna'], row['Anul']) == expected]
    require(len(following) == 1, 'NEXT_MONTH', 'Luna deschisă următoare lipsește sau este duplicată.')
    removed = following[0]
    movements = _movement_rows(repo, {month['IDL'], removed['IDL']})
    movement_count = sum(len(rows) for rows in movements.values())
    blocked = str(repo.setting('BlockReopenWithMovements', '1')).strip().lower() in ('1', 'true', 'yes', 'da')
    require(not blocked or movement_count == 0, 'REOPEN_MOVEMENTS',
            'Redeschiderea este blocată deoarece există mișcări în luna redeschisă sau în luna eliminată.')

    orphaned = {'Plati': 0, 'Retur': 0}
    for table, rows in movements.items():
        for movement in rows:
            source = month if movement['IDL'] == month['IDL'] else removed
            provenance = {
                'OriginMonth': movement.get('OriginMonth') or source['Luna'],
                'OriginYear': movement.get('OriginYear') or source['Anul'],
            }
            if movement['IDL'] == removed['IDL']:
                provenance.update(IDL=None, IDZ=None)
                orphaned[table] += 1
            repo.update(table, movement, provenance)

    # Composite keys cannot SET NULL (SubunitId must stay): clear the document-to-month links of the removed month here.
    for table in ('Chitante', 'AlteDoc'):
        for document in [row for row in repo.rows(table) if row.get('IDL') == removed['IDL']]:
            repo.update(table, document, {'IDL': None})
    for attendance in [row for row in repo.rows('Prezenta') if row['IDL'] == removed['IDL']]:
        repo.delete('Prezenta', attendance)
    for snapshot in [row for row in repo.rows('SS_Buget') if row['IDL'] == month['IDL']]:
        repo.delete('SS_Buget', snapshot)
    repo.delete('LunaD', removed)
    reopened = repo.update('LunaD', month, {'Inchisa': False})
    return {'reopened': reopened, 'removed': {'IDL': removed['IDL'], 'Luna': removed['Luna'], 'Anul': removed['Anul']},
            'orphaned': orphaned}


def idempotent(repo, key, operation, payload, function):
    require(isinstance(key, str) and 8 <= len(key) <= 100, 'REQUEST_KEY', 'Cheia operației lipsește sau este invalidă.', 400)
    # The subunit is part of the identity: the same key in another subunit never returns this result.
    fingerprint = hashlib.sha256(json.dumps([operation, payload, repo.scope()], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    # Operations recorded before AD_05 carry the fingerprint without the subunit: still the same request.
    legacy = hashlib.sha256(json.dumps([operation, payload], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    found = repo.query('SELECT * FROM AD_Operations WHERE SubunitId=%s AND RequestKey=%s', (repo.scope(), key))
    if found:
        require(found[0]['Fingerprint'] in (fingerprint, legacy), 'KEY_REUSED', 'Cheia operației a fost refolosită cu alte date.')
        return json.loads(found[0]['Result'])
    result = function()
    encoded = json.dumps(result, ensure_ascii=False, default=lambda value: value.isoformat())
    repo.execute('INSERT INTO AD_Operations (SubunitId, RequestKey, Fingerprint, Result) VALUES (%s,%s,%s,%s)',
                 (repo.scope(), key, fingerprint, encoded))
    return json.loads(encoded)
