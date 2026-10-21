"""Monthly working-day settings, scoped to the ADE subunit. No schema changes."""
import hashlib
import json
from calendar import monthrange
from .domain import require, DomainError
from .service import MONTHS, month_days, work_days


def year_of(value):
    require(str(value).isdigit() and 1900 <= int(value) <= 2200,
            'YEAR', 'Introduceți un an între 1900 și 2200.', 400)
    return int(value)


def setting_key(year):
    return 'WorkingDays:' + str(year)


def stored(repo, year):
    raw = repo.setting(setting_key(year))
    if raw is None:
        return None, {'revision': 0, 'days': [work_days(month, year) for month in range(1, 13)]}
    try:
        data = json.loads(raw)
        require(isinstance(data, dict) and type(data.get('revision')) is int
                and isinstance(data.get('days'), list) and len(data['days']) == 12
                and all(type(days) is int and 0 <= days <= 31 for days in data['days']),
                'WORKING_DAYS_CONFIG', 'Configurarea zilelor lucrătoare este invalidă.', 409)
        for key in ('legal', 'extra'):
            require(key not in data or (isinstance(data[key], list) and len(data[key]) == 12
                    and all(type(days) is int and 0 <= days <= 31 for days in data[key])),
                    'WORKING_DAYS_CONFIG', 'Configurarea zilelor libere este invalidă.', 409)
        return raw, data
    except (ValueError, TypeError) as error:
        if isinstance(error, DomainError):
            raise
        raise DomainError('WORKING_DAYS_CONFIG', 'Configurarea zilelor lucrătoare este invalidă.', 409) from error


def configured_days(repo, month, year):
    return stored(repo, year)[1]['days'][month - 1]


def configuration(repo, year):
    year = year_of(year)
    raw, data = stored(repo, year)
    months = repo.rows('LunaD', Anul=year)
    require(len({row['Luna'] for row in months}) == len(months), 'MONTH_DUPLICATE',
            'Există luni duplicate în evidență. Configurarea nu poate fi modificată.')
    existing = {row['Luna']: row for row in months}
    rows = []
    for month in range(1, 13):
        saved = existing.get(month)
        weekdays = work_days(month, year)
        days = month_days(saved) if saved else data['days'][month - 1]
        legal = data.get('legal', [0] * 12)[month - 1]
        extra = data.get('extra', [0] * 12)[month - 1]
        rows.append({'month': month, 'name': MONTHS[month - 1].capitalize(),
                     'calendar_days': monthrange(year, month)[1], 'weekdays': weekdays,
                     'deducted': legal, 'extra': extra, 'days': days,
                     'closed': bool(saved and saved['Inchisa']), 'existing': saved is not None})
    revision = [repo.scope(), year, raw,
                [(row['IDL'], row['Version'], row.get('ZileLuna'), row['Inchisa']) for row in months]]
    version = hashlib.sha256(json.dumps(revision, sort_keys=True).encode()).hexdigest()
    return {'year': year, 'months': rows, 'version': version, 'subunit': repo.subunit_name}


def save(repo, body):
    require(set(body) <= {'year', 'days', 'legal', 'extra', 'version'}, 'FIELDS', 'Câmpuri necunoscute.', 400)
    year = year_of(body.get('year'))
    current = configuration(repo, year)
    require(body.get('version') == current['version'], 'CONFLICT',
            'Lunile sau configurarea au fost modificate. Reîncărcați anul înainte de salvare.')
    values = body.get('days')
    require(isinstance(values, list) and len(values) == 12, 'DAYS', 'Completați toate cele 12 luni.', 400)
    legal, extra = body.get('legal'), body.get('extra')
    for counts in (legal, extra):
        require(isinstance(counts, list) and len(counts) == 12
                and all(type(value) is int and 0 <= value <= 31 for value in counts),
                'DAYS', 'Completați zilele libere pentru toate lunile. Reîncărcați pagina dacă este necesar.', 400)
    months = {row['Luna']: row for row in repo.rows('LunaD', Anul=year)}
    attendance = repo.rows('Prezenta')
    for row, days, legal_days, extra_days in zip(current['months'], values, legal, extra):
        require(type(days) is int, 'DAYS', 'Zilele lucrătoare trebuie să fie numere întregi.', 400)
        require(legal_days <= row['weekdays'], 'DAYS', f"{row['name']}: prea multe zile libere legale.", 400)
        if row['closed']:
            require(days == row['days'] and extra_days == row['extra'], 'CLOSED',
                    f"{row['name']} este închisă și nu poate fi modificată.")
            continue
        # Preserve legacy month totals until their components are edited or imported.
        unchanged = days == row['days'] and legal_days == row['deducted'] and extra_days == row['extra']
        require(unchanged or (extra_days <= row['weekdays'] - legal_days
                             and days == row['weekdays'] - legal_days - extra_days),
                'DAYS', f"{row['name']}: zilele de prezență trebuie recalculate din zilele libere.", 400)
        require(0 <= days <= row['calendar_days'], 'DAYS',
                f"{row['name']}: introduceți între 0 și {row['calendar_days']} zile reale.", 400)
        month = months.get(row['month'])
        if month and days != row['days']:
            maximum = max((entry.get('ZilePrezenta') or 0 for entry in attendance if entry['IDL'] == month['IDL']), default=0)
            require(days >= maximum, 'ATTENDANCE_DAYS',
                    f"{row['name']}: există deja prezențe de {maximum} zile. Corectați prezențele înainte de reducerea la {days}.")
    raw, data = stored(repo, year)
    encoded = json.dumps({'revision': data['revision'] + 1, 'days': values,
                          'legal': legal, 'extra': extra}, separators=(',', ':'))
    # Compact month counts fit the existing AD_Settings.SettingValue VARCHAR(255).
    require(len(encoded) <= 255, 'WORKING_DAYS_CONFIG', 'Configurarea este prea mare.', 400)
    if raw is None:
        repo.execute('INSERT INTO AD_Settings (SubunitId,SettingKey,SettingValue) VALUES (%s,%s,%s)',
                     (repo.scope(), setting_key(year), encoded))
    else:
        repo.execute('UPDATE AD_Settings SET SettingValue=%s WHERE SubunitId=%s AND SettingKey=%s',
                     (encoded, repo.scope(), setting_key(year)))
    for row, days in zip(current['months'], values):
        month = months.get(row['month'])
        if month and not row['closed'] and month.get('ZileLuna') != days:
            repo.update('LunaD', month, {'ZileLuna': days})
    return configuration(repo, year)
