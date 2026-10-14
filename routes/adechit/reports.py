"""SLICE-ADE9-01: report models of the evidence. Pure read: nothing here writes a row.

Every builder returns a model for report_render (layout from the Access reports in ACCESS_SOURCES/Reports; the Access
widths are kept as twips). Reports that the Access system filtered with a form control take the same filter as a request
parameter. Figures come from the same rows the screens show, never from a second calculation:
the open month is calculated (calculations.calculate), a closed month is read from its saved situation (AD_SS_Buget)."""
import unicodedata
from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from .calculations import calculate
from .domain import DomainError, require
from .receipt_output import amount_words, issuer_data

FOOTER = 'Raport generat în ADECHIT'
MIN_DATE = '2001-01-01'


# ---- small helpers -------------------------------------------------------------------------------------------------

def num(value, places=0):
    """Romanian figure: 1.234,50. Empty for NULL; no «-0»."""
    if value is None or value == '':
        return ''
    q = Decimal(str(value)).quantize(Decimal(1) if places == 0 else Decimal('.01'), rounding=ROUND_HALF_UP)
    if q == 0:
        q = abs(q)
    return f'{q:,.{places}f}'.replace(',', '\0').replace('.', ',').replace('\0', '.')


def dmy(value):
    text = str(value or '')[:10]
    return f'{text[8:10]}.{text[5:7]}.{text[0:4]}' if len(text) == 10 else ''


def collate(text):
    return ''.join(c for c in unicodedata.normalize('NFKD', str(text or '')) if not unicodedata.combining(c)).casefold()


def number_key(value):
    text = str(value if value is not None else '')
    return (0, int(text), '') if text.isdigit() else (1, 0, collate(text))


def need_int(args, name):
    value = args.get(name)
    require(value is not None and str(value).isdigit(), 'FILTER', 'Filtru invalid.', 400)
    return int(value)


def opt_int(args, name):
    value = args.get(name)
    if value in (None, ''):
        return None
    require(str(value).isdigit(), 'FILTER', 'Filtru invalid.', 400)
    return int(value)


def need_date(args, name):
    try:
        return date.fromisoformat(str(args.get(name) or ''))
    except ValueError as error:
        raise DomainError('FILTER', 'Data din filtru este invalidă.', 400) from error


def period(args):
    start, end = need_date(args, 'start'), need_date(args, 'end')
    require(start <= end, 'FILTER', 'Data de început trebuie să fie înaintea celei de sfârșit.', 400)
    return start, end


def month_label(month):
    return f"{str(month.get('LunaT') or month['Luna']).capitalize()} / {month['Anul']}"


def month_end(month):
    return date(month['Anul'], month['Luna'], monthrange(month['Anul'], month['Luna'])[1])


def letterhead(issuer, right=''):
    return {'t': 'letterhead', 'left': [issuer['name'], issuer['address']], 'right': right}


def col(label, w, align='l', tint=None, key=None):
    return {'label': label, 'w': w, 'align': align, 'tint': tint, **({'key': key} if key else {})}


def total_cells(label, span, values):
    return [{'t': label, 'span': span}, *values]


def model(title, filename, blocks, landscape=False, font=8, footer=FOOTER):
    return {'title': title, 'filename': filename, 'landscape': landscape, 'font': font, 'footer': footer, 'blocks': blocks}


# ---- situation (SituatieDebitori_buget, _total, _total_grp) --------------------------------------------------------

SITUATION_COLUMNS = [  # key, label, twips, align, tint, summed
    ('Nume', 'Nume', 3000, 'l', None, False), ('CNP', 'CNP', 1440, 'c', None, False),
    ('ZilePrezenta', 'Zile\nPrez.', 624, 'c', 'z', True), ('SID', 'S.I.\nDebit', 729, 'r', 'd', True),
    ('SIC', 'S.I.\nCredit', 729, 'r', 'c', True), ('ValoareContract', 'Contract\nstabilit', 864, 'r', None, True),
    ('Plata', 'Total\nchitanțe', 864, 'r', None, True), ('Plati', 'Total\nalte plăți', 864, 'r', None, True),
    ('Compensare', 'Compen-\nsare', 864, 'r', None, True), ('Anticipat', 'Achitat\nanticipat', 864, 'r', None, True),
    ('Retur', 'Retur\nalocație', 864, 'r', None, True), ('SFD', 'S.F.\nDEBIT', 729, 'r', 'd', True),
    ('SFC', 'S.F.\nCREDIT', 729, 'r', 'c', True), ('Detalii', 'Detalii', 2250, 'l', None, False),
]


def situation_rows(repo, month_id, group_id):
    """The rows of the monthly situation: saved for a closed month, calculated for the open one (as the screen does)."""
    month = repo.get('LunaD', month_id)
    if month['Inchisa']:
        filters = {'IDL': month_id, **({'IDG': group_id} if group_id is not None else {})}
        return month, repo.rows('SS_Buget', **filters)
    data = repo.situation_data(month_id, group_id) if group_id is not None else repo.data()
    rows, _ = calculate(data, month_id, group_id)
    return month, rows


def _situation_table(rows):
    keys = ('NUME', 'CNP', 'ZILE_PREZENTA', 'SID', 'SIC', 'VALOARE_CONTRACT', 'PLATA', 'PLATI',
            'COMPENSARE', 'ANTICIPAT', 'RETUR', 'SFD', 'SFC', 'DETALII')
    columns = [col('', 420, 'c', key='NR_CRT'),
               *[col(text, w, align, tint, key=key) for key, (_, text, w, align, tint, _) in zip(keys, SITUATION_COLUMNS)]]
    body = []
    for index, row in enumerate(rows, 1):
        body.append({'cells': [str(index), *[
            (str(row.get(key) if row.get(key) is not None else '') if key in ('Nume', 'CNP', 'Detalii') else num(row.get(key)))
            for key, _, _, _, _, _ in SITUATION_COLUMNS]]})
    return columns, body


def _situation_totals(rows, label):
    sums = [num(sum((r.get(key) or 0) for r in rows)) if summed else '' for key, _, _, _, _, summed in SITUATION_COLUMNS[2:]]
    return {'cells': ['', {'t': label, 'span': 2}, *sums], 'cls': 'total'}


def situation_report(repo, unit, args):
    month_id, group_id = need_int(args, 'month'), opt_int(args, 'group')
    everything, split = args.get('all') == '1', args.get('split') == '1'
    require(everything or group_id is not None, 'FILTER', 'Alegeți grupa sau bifați «Toate grupele».', 400)
    month, rows = situation_rows(repo, month_id, None if everything else group_id)
    require(bool(rows), 'NO_DATA', 'Raportul selectat nu conține date.', 404)
    issuer = issuer_data(repo, unit)
    head = [letterhead(issuer, month_label(month)), {'t': 'title', 'text': 'Situație Lunară Debitori'}]

    def banner(row):
        educator = row.get('Educator')
        return f"{row.get('Grupa') or ''} / {educator}" if educator else str(row.get('Grupa') or '')

    if not everything:
        rows = sorted(rows, key=lambda r: collate(r.get('Nume')))
        columns, body = _situation_table(rows)
        body.append(_situation_totals(rows, f"TOTAL {rows[0].get('Grupa') or ''}"))
        blocks = [*head, {'t': 'text', 'text': banner(rows[0]), 'align': 'center', 'bold': True},
                  {'t': 'table', 'cols': columns, 'rows': body}]
        return model('Situație lunară', 'Situatie_lunara', blocks, landscape=True, font=7.5, footer=None)
    if not split:
        rows = sorted(rows, key=lambda r: collate(r.get('Nume')))
        columns, body = _situation_table(rows)
        body.append(_situation_totals(rows, 'TOTAL GENERAL'))
        blocks = [*head, {'t': 'text', 'text': 'TOATE GRUPELE', 'align': 'center', 'bold': True},
                  {'t': 'table', 'cols': columns, 'rows': body}]
        return model('Situație lunară - toate grupele', 'Situatie_lunara_toate', blocks, landscape=True, font=7.5)
    blocks, groups = [], {}
    for row in rows:
        groups.setdefault(row.get('IDG'), []).append(row)
    ordered = sorted(groups.values(), key=lambda g: collate(g[0].get('Grupa')))
    for number, members in enumerate(ordered):
        members = sorted(members, key=lambda r: collate(r.get('Nume')))
        columns, body = _situation_table(members)
        body.append(_situation_totals(members, f"TOTAL {members[0].get('Grupa') or ''}"))
        if number:
            blocks.append({'t': 'break'})
        blocks += [*head, {'t': 'text', 'text': banner(members[0]), 'align': 'center', 'bold': True},
                   {'t': 'table', 'cols': columns, 'rows': body}]
    blocks.append({'t': 'table', 'cols': _situation_table([])[0], 'head': False, 'rows': [_situation_totals(rows, 'TOTAL GENERAL')]})
    return model('Situație lunară - grupe separate', 'Situatie_lunara_grupe', blocks, landscape=True, font=7.5)


# ---- cash register and bank report (RegistruCasa, RaportBanca) -----------------------------------------------------

def _day_bounds(start, end):
    return start.isoformat(), (end + timedelta(days=1)).isoformat()


def _cash_entries(repo, start, end):
    low, high = _day_bounds(start, end)
    scope = repo.scope()
    entries = [{'Grupa': r['Grupa'], 'Nume': r['Nume'], 'Data': str(r['Data'])[:10], 'Serie': r['Serie'], 'Numar': r['Numar'],
                'Incasari': r['Valoare'] or 0, 'Plati': 0} for r in repo.query(
        'SELECT g.Grupa, pl.Nume, c.Data, c.Serie, c.Numar, p.Plata AS Valoare FROM AD_Chitante c '
        'JOIN AD_Plati p ON p.IDPL=c.IDPL AND p.SubunitId=c.SubunitId '
        'JOIN AD_Platitori pl ON pl.IDP=p.IDP AND pl.SubunitId=p.SubunitId '
        'JOIN AD_Grupe g ON g.IDG=pl.IDG AND g.SubunitId=pl.SubunitId '
        'WHERE c.SubunitId=%s AND c.Data>=%s AND c.Data<%s AND c.Anulata=0 AND p.Anulata=0', (scope, low, high))]
    # A refund is a payment out of the cash desk: Access prints it with the series «Tic» and its explanation in the number column.
    entries += [{'Grupa': r['Grupa'], 'Nume': r['Nume'], 'Data': str(r['Data'])[:10], 'Serie': 'Tic', 'Numar': r['Explicatie'],
                 'Incasari': 0, 'Plati': r['Suma'] or 0} for r in repo.query(
        'SELECT g.Grupa, pl.Nume, r.Data, r.Explicatie, r.Suma FROM AD_Retur r '
        'JOIN AD_Platitori pl ON pl.IDP=r.IDP AND pl.SubunitId=r.SubunitId '
        'JOIN AD_Grupe g ON g.IDG=pl.IDG AND g.SubunitId=pl.SubunitId '
        'WHERE r.SubunitId=%s AND r.Data>=%s AND r.Data<%s AND r.Anulat=0', (scope, low, high))]
    return entries


def _bank_entries(repo, start, end):
    low, high = _day_bounds(start, end)
    return [{'Grupa': r['Grupa'], 'Nume': r['Nume'], 'Data': str(r['Data'])[:10], 'Serie': 'A.D.', 'Numar': r['NrDoc'],
             'Incasari': r['Plata'] or 0, 'Plati': 0} for r in repo.query(
        'SELECT g.Grupa, pl.Nume, p.Data, a.NrDoc, p.Plata FROM AD_Plati p '
        'JOIN AD_AlteDoc a ON a.IDPL=p.IDPL AND a.SubunitId=p.SubunitId '
        'JOIN AD_Platitori pl ON pl.IDP=p.IDP AND pl.SubunitId=p.SubunitId '
        'JOIN AD_Grupe g ON g.IDG=pl.IDG AND g.SubunitId=pl.SubunitId '
        'WHERE p.SubunitId=%s AND p.Data>=%s AND p.Data<%s AND p.Anulata=0', (repo.scope(), low, high))]


def _ledger_report(repo, unit, args, title, entries_of, filename):
    start, end = period(args)
    entries = entries_of(repo, start, end)
    require(bool(entries), 'NO_DATA', 'Raportul selectat nu are date.', 404)
    entries.sort(key=lambda e: (e['Data'], number_key(e['Numar'])))
    columns = [col('Nume', 3060), col('Grupa', 2460), col('Data', 1080, 'c'), col('Serie', 600, 'c'), col('Numar', 1620, 'c'),
               col('Incasari', 1140, 'r'), col('Plati', 1140, 'r')]
    body, day = [], []

    def close_day():
        if day:
            body.append({'cells': [{'t': dmy(day[0]['Data']), 'span': 5}, num(sum(e['Incasari'] for e in day), 2),
                                   num(sum(e['Plati'] for e in day), 2)], 'cls': 'sub'})
            day.clear()

    for entry in entries:
        if day and day[0]['Data'] != entry['Data']:
            close_day()
        day.append(entry)
        body.append({'cells': [str(entry['Nume'] or ''), str(entry['Grupa'] or ''), dmy(entry['Data']), str(entry['Serie'] or ''),
                               str(entry['Numar'] if entry['Numar'] is not None else ''), num(entry['Incasari'], 2), num(entry['Plati'], 2)]})
    close_day()
    incomes, payments = sum(e['Incasari'] for e in entries), sum(e['Plati'] for e in entries)
    body.append({'cells': [{'t': 'TOTAL', 'span': 5}, num(incomes, 2), num(payments, 2)], 'cls': 'total'})
    body.append({'cells': [{'t': 'SOLD FINAL', 'span': 5}, {'t': num(incomes - payments, 2), 'span': 2}], 'cls': 'total'})
    blocks = [letterhead(issuer_data(repo, unit)), {'t': 'title', 'text': title, 'sub': [f'{dmy(start.isoformat())} - {dmy(end.isoformat())}']},
              {'t': 'table', 'cols': columns, 'rows': body}]
    return model(title, filename, blocks, font=8)


def cash_register_report(repo, unit, args):
    return _ledger_report(repo, unit, args, 'Registru de casă', _cash_entries, 'Registru_casa')


def bank_report(repo, unit, args):
    return _ledger_report(repo, unit, args, 'Raport bancă', _bank_entries, 'Raport_banca')


# ---- cancelled documents (DocumenteAnulate) ------------------------------------------------------------------------

def cancelled_report(repo, unit, args):
    """Cancelled payments and refunds of the children who have attendance in the chosen month (Access: Prezenta.IDL = month).
    The Access query is written with an alias that was never proven (M08); this one states the same selection with plain joins."""
    month = repo.get('LunaD', need_int(args, 'month'))
    scope = repo.scope()
    entries = []
    for r in repo.query(
            'SELECT pl.Nume, g.Grupa, p.Data, p.Plata, p.Motivul, c.Numar, c.Data AS ChData, a.NrDoc, a.DataDoc '
            'FROM AD_Plati p JOIN AD_Prezenta z ON z.IDZ=p.IDZ AND z.SubunitId=p.SubunitId '
            'JOIN AD_Platitori pl ON pl.IDP=z.IDP AND pl.SubunitId=z.SubunitId '
            'JOIN AD_Grupe g ON g.IDG=z.IDG AND g.SubunitId=z.SubunitId '
            'LEFT JOIN AD_Chitante c ON c.IDPL=p.IDPL AND c.SubunitId=p.SubunitId '
            'LEFT JOIN AD_AlteDoc a ON a.IDPL=p.IDPL AND a.SubunitId=p.SubunitId '
            'WHERE p.SubunitId=%s AND z.IDL=%s AND p.Anulata<>0 AND p.Plata<>0', (scope, month['IDL'])):
        if r['Numar'] is not None:
            document, kind = f"{r['Numar']}-{dmy(r['ChData'])}", 'Chitanțe'
        elif r['NrDoc'] is not None:
            document, kind = f"{r['NrDoc']}-{dmy(r['DataDoc'])}", 'Alte plăți'
        else:
            document, kind = '', 'Bon fiscal'   # historical payment with neither a receipt nor another document
        entries.append({'Nume': r['Nume'], 'Grupa': r['Grupa'], 'Dt': str(r['Data'])[:10], 'Document': document, 'Tip': kind,
                        'Motiv': r['Motivul'] or '', 'Incasari': r['Plata'] or 0, 'Plati': 0})
    for r in repo.query(
            'SELECT pl.Nume, g.Grupa, r.Data, r.NrDoc, r.Suma, r.Explicatie FROM AD_Retur r '
            'JOIN AD_Prezenta z ON z.IDZ=r.IDZ AND z.SubunitId=r.SubunitId '
            'JOIN AD_Platitori pl ON pl.IDP=z.IDP AND pl.SubunitId=z.SubunitId '
            'JOIN AD_Grupe g ON g.IDG=z.IDG AND g.SubunitId=z.SubunitId '
            'WHERE r.SubunitId=%s AND z.IDL=%s AND r.Anulat<>0', (scope, month['IDL'])):
        entries.append({'Nume': r['Nume'], 'Grupa': r['Grupa'], 'Dt': str(r['Data'])[:10], 'Document': f"{r['NrDoc'] or ''} - {dmy(r['Data'])}",
                        'Tip': 'Restituire sumă', 'Motiv': r['Explicatie'] or '', 'Incasari': 0, 'Plati': r['Suma'] or 0})
    require(bool(entries), 'NO_DATA', 'Nu există date în raportul selectat.', 404)
    entries.sort(key=lambda e: (collate(e['Nume']), e['Dt']))
    columns = [col('Nume', 3060), col('Grupa', 2460), col('Nr. Document', 3300, 'c'), col('Tip document', 1620, 'c'),
               col('Motivul anulării', 2400, 'c'), col('Incasari', 1140, 'r'), col('Plati', 1140, 'r')]
    body = [{'cells': [str(e['Nume'] or ''), str(e['Grupa'] or ''), e['Document'], e['Tip'], e['Motiv'], num(e['Incasari'], 2), num(e['Plati'], 2)]}
            for e in entries]
    body.append({'cells': [{'t': 'TOTAL', 'span': 5}, num(sum(e['Incasari'] for e in entries), 2), num(sum(e['Plati'] for e in entries), 2)], 'cls': 'total'})
    blocks = [letterhead(issuer_data(repo, unit)), {'t': 'title', 'text': 'Documente anulate în perioada', 'sub': [month_label(month)]},
              {'t': 'table', 'cols': columns, 'rows': body}]
    return model('Documente anulate', 'Documente_anulate', blocks, landscape=True, font=8)


# ---- account sheet (FisaCont, FisaCont_s) --------------------------------------------------------------------------

def _children(repo, args):
    child, group = opt_int(args, 'child'), opt_int(args, 'group')
    if args.get('all') == '1':
        require(group is not None, 'FILTER', 'Alegeți grupa.', 400)
        people = [p for p in repo.rows('Platitori', IDG=group)]
    else:
        require(child is not None, 'FILTER', 'Selectați un copil.', 400)
        people = [repo.get('Platitori', child)]
    require(bool(people), 'NO_DATA', 'Raportul selectat nu are date.', 404)
    return sorted(people, key=lambda p: collate(p['Nume']))


def _active_payers(repo, child_id):
    payers = [p for p in repo.rows('Platitori_sub', IDP=child_id) if p.get('Activ')]
    return payers


def _account_entries(repo, child, end, months, groups):
    scope, child_id, entries = repo.scope(), child['IDP'], []
    si = child.get('SI') or 0
    entries.append({'Data': MIN_DATE, 'rank': 0, 'expl': 'Sold Inițial', 'zile': 0, 'debit': si if si > 0 else 0, 'credit': -si if si < 0 else 0, 'tip': 'S'})
    for z in repo.rows('Prezenta', IDP=child_id):
        month = months.get(z['IDL'])
        if month is None or month_end(month) > end:
            continue
        entries.append({'Data': month_end(month).isoformat(), 'rank': 1, 'expl': f"Prezență {month.get('LunaT') or month['Luna']}/{month['Anul']}",
                        'zile': z.get('ZilePrezenta') or 0, 'debit': z.get('ValoareContract') or 0, 'credit': 0, 'tip': 'Z'})
    for r in repo.query(
            'SELECT p.Data, p.Plata, c.Serie, c.Numar, a.NrDoc, a.Explicatie FROM AD_Plati p '
            'LEFT JOIN AD_Chitante c ON c.IDPL=p.IDPL AND c.SubunitId=p.SubunitId '
            'LEFT JOIN AD_AlteDoc a ON a.IDPL=p.IDPL AND a.SubunitId=p.SubunitId '
            'WHERE p.SubunitId=%s AND p.IDP=%s AND p.Anulata=0', (scope, child_id)):
        if r['Numar'] is not None:
            expl = f"Chitanță {r['Serie'] or ''}/{r['Numar']}"
        elif r['NrDoc'] is not None:
            expl = f"{r['Explicatie'] or 'Alt document'} {r['NrDoc']}"
        else:
            expl = 'Bon Fiscal'
        entries.append({'Data': str(r['Data'])[:10], 'rank': 2, 'expl': expl, 'zile': 0, 'debit': 0, 'credit': r['Plata'] or 0, 'tip': 'P'})
    for r in repo.rows('Retur', IDP=child_id):
        if r.get('Anulat'):
            continue
        entries.append({'Data': str(r['Data'])[:10], 'rank': 3, 'expl': f"Restituire: {r.get('NrDoc') or ''}/{r.get('Explicatie') or ''}",
                        'zile': 0, 'debit': r.get('Suma') or 0, 'credit': 0, 'tip': 'R'})
    for r in repo.rows('Platitori_Istoric', IDP=child_id):
        if r.get('Tip') == 'MUTARE' and r.get('IDG_Vechi') is not None and r.get('IDG_Nou') is not None:
            entries.append({'Data': str(r['Data'])[:10], 'rank': 4, 'zile': 0, 'debit': 0, 'credit': 0, 'tip': 'T',
                            'expl': f"Transfer de la {groups.get(r['IDG_Vechi'], {}).get('Grupa', '')} la {groups.get(r['IDG_Nou'], {}).get('Grupa', '')}"})
    entries = [e for e in entries if e['Data'] <= end.isoformat()]
    merged = {}
    for e in entries:   # Access sums the lines with the same date, explanation, days and kind
        key = (e['Data'], e['expl'], e['zile'], e['tip'])
        if key in merged:
            merged[key]['debit'] += e['debit']
            merged[key]['credit'] += e['credit']
        else:
            merged[key] = dict(e)
    return sorted(merged.values(), key=lambda e: (e['Data'], e['rank']))


def account_report(repo, unit, args):
    month = repo.get('LunaD', need_int(args, 'month'))
    end = month_end(month)
    months = {m['IDL']: m for m in repo.rows('LunaD')}
    groups = {g['IDG']: g for g in repo.rows('Grupe')}
    issuer = issuer_data(repo, unit)
    account = (issuer['accounts'][0].get('Cont') if issuer['accounts'] else '') or ''
    columns = [col('Data', 1320, 'c'), col('Explicație', 4020), col('DEBIT', 1020, 'r'), col('CREDIT', 1020, 'r'),
               col('Sold', 1020, 'r'), col('Tip sold', 1020, 'c')]
    blocks = []
    for number, child in enumerate(_children(repo, args)):
        payers = _active_payers(repo, child['IDP'])
        entries = _account_entries(repo, child, end, months, groups)
        balance, body = 0, []
        for e in entries:
            balance += e['debit'] - e['credit']
            expl = e['expl'] + (f" ({e['zile']} zile)" if e['tip'] == 'Z' else '')
            kind = 'Credit' if balance < 0 else 'Debit' if balance > 0 else ''
            if e['tip'] == 'T':   # a transfer moves no money: its text takes the debit and credit columns too
                body.append({'cells': [dmy(e['Data']), {'t': expl, 'span': 3}, num(abs(balance), 2), kind]})
            else:
                body.append({'cells': ['' if e['tip'] == 'S' else dmy(e['Data']), expl, num(e['debit'], 2), num(e['credit'], 2),
                                       num(abs(balance), 2), kind]})
        debit, credit = sum(e['debit'] for e in entries), sum(e['credit'] for e in entries)
        final = 'DEBIT' if debit - credit > 0 else 'CREDIT' if debit - credit < 0 else 'Fără sold'
        body.append({'cells': [{'t': f"Sold final {month_label(month).replace(' / ', '/')}", 'span': 2}, num(debit, 2), num(credit, 2),
                               num(abs(debit - credit), 2), final], 'cls': 'total'})
        if number:
            blocks.append({'t': 'break'})
        blocks += [
            {'t': 'letterhead', 'left': [issuer['name'], f"Cod fiscal: {issuer['tax_code']}", f"Adresa: {issuer['address']}", f'Contul: {account}'], 'right': ''},
            {'t': 'box', 'rows': [('Nume', child['Nume'] or ''), ('Adresa', '; '.join(p['Adresa'] for p in payers if p.get('Adresa'))),
                                  ('CNP', child.get('CNP') or ''), ('EMail', '; '.join(p['EMail'] for p in payers if p.get('EMail')))]},
            {'t': 'title', 'text': 'Fișă cont'}, {'t': 'table', 'cols': columns, 'rows': body}]
    return model('Fișă cont', 'Fisa_cont', blocks, font=8)


# ---- debtor sheet (FisaDebitor with FisaDebitor_L and FisaDebitor_C) -------------------------------------------------
# Not in the web data: the day calendar (FisaDebitor_D), fiscal receipts, and the meal / sibling / advance columns.

def debtor_report(repo, unit, args):
    month = repo.get('LunaD', need_int(args, 'month'))
    end = month_end(month)
    months = {m['IDL']: m for m in repo.rows('LunaD')}
    issuer = issuer_data(repo, unit)
    blocks = [letterhead(issuer)]
    columns = [col('Luna', 1320), col('Prezență', 960, 'r'), col('Absență', 960, 'r'), col('Contract', 1020, 'r'), col('Total', 1020, 'r'),
               col('Plătit', 1020, 'r'), col('Sold final', 1020, 'r'), col('', 2400)]
    docs = [col('Data', 1020, 'c'), col('Plată', 1020, 'r'), col('Tip plată', 1440, 'c'), col('Nr. doc.', 1020, 'c'), col('Data doc.', 1020, 'c')]
    for number, child in enumerate(_children(repo, args)):
        payers = _active_payers(repo, child['IDP'])
        if number:
            blocks.append({'t': 'break'})
        blocks.append({'t': 'title', 'text': f"Fișă debitor luna {month.get('LunaT') or month['Luna']}/{month['Anul']}"})
        rows = [('Nume copil', child['Nume'] or ''), ('CNP copil', child.get('CNP') or '')]
        for payer in payers:
            rows += [('Nume plătitor', payer.get('Nume') or ''), ('Adresa', payer.get('Adresa') or ''),
                     ('CUI/CNP plătitor', payer.get('CUI') or payer.get('CNP_Platitor') or ''), ('Cont', payer.get('Cont') or ''),
                     ('EMail', payer.get('EMail') or '')]
        blocks.append({'t': 'box', 'rows': rows})
        paid_by_attendance = {}
        for r in repo.query(
                'SELECT p.IDZ, p.Data, p.Plata, c.Serie, c.Numar, c.Data AS ChData, a.NrDoc, a.DataDoc FROM AD_Plati p '
                'LEFT JOIN AD_Chitante c ON c.IDPL=p.IDPL AND c.SubunitId=p.SubunitId '
                'LEFT JOIN AD_AlteDoc a ON a.IDPL=p.IDPL AND a.SubunitId=p.SubunitId '
                'WHERE p.SubunitId=%s AND p.IDP=%s AND p.Anulata=0 ORDER BY p.Data', (repo.scope(), child['IDP'])):
            paid_by_attendance.setdefault(r['IDZ'], []).append(r)
        attendance = [z for z in repo.rows('Prezenta', IDP=child['IDP']) if z['IDL'] in months and month_end(months[z['IDL']]) <= end]
        attendance.sort(key=lambda z: month_end(months[z['IDL']]))
        for z in attendance:
            m = months[z['IDL']]
            payments = paid_by_attendance.get(z['IDZ'], [])
            paid = sum(p['Plata'] or 0 for p in payments)
            total = z.get('ValoareTotala') if z.get('ValoareTotala') is not None else z.get('ValoareContract') or 0
            days = z.get('ZilePrezenta') or 0
            working = m.get('ZileLuna')
            absent = max(0, working - days) if working is not None else ''
            balance = total - paid
            kind = 'CREDITOR' if balance < 0 else 'DEBITOR' if balance > 0 else 'FĂRĂ SOLD'
            blocks.append({'t': 'table', 'cols': columns, 'rows': [{'cells': [month_label(m).replace(' / ', '/'), str(days), str(absent),
                           num(z.get('ValoareContract') or 0, 2), num(total, 2), num(paid, 2), num(abs(balance), 2), kind]}]})
            if payments:
                lines = []
                for p in payments:
                    if p['Numar'] is not None:
                        lines.append([dmy(p['Data']), num(p['Plata'], 2), 'Chitanță', str(p['Numar']), dmy(p['ChData'])])
                    elif p['NrDoc'] is not None:
                        lines.append([dmy(p['Data']), num(p['Plata'], 2), 'Alt document', str(p['NrDoc']), dmy(p['DataDoc'])])
                    else:
                        lines.append([dmy(p['Data']), num(p['Plata'], 2), 'Bon fiscal', '', ''])
                blocks.append({'t': 'table', 'cols': docs, 'rows': [{'cells': line} for line in lines]})
    return model('Fișă debitor', 'Fisa_debitor', blocks, font=8)


# ---- payment order (Dispozitie plata) ------------------------------------------------------------------------------

def payment_order(repo, unit, args):
    refund = repo.get('Retur', need_int(args, 'id'))
    child = repo.get('Platitori', refund['IDP'])
    amount = Decimal(str(refund.get('Suma') or 0)).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
    figures, day = f'{num(amount, 2)} lei', dmy(refund.get('Data'))
    blocks = [
        {'t': 'text', 'text': 'DISPOZIȚIE DE PLATĂ - CĂTRE CASIERIE', 'align': 'center', 'bold': True},
        {'t': 'text', 'text': f"numărul {refund.get('NrDoc') or ''}   din   {day}", 'align': 'center', 'bold': True},
        {'t': 'box', 'rows': [('Numele și prenumele', child['Nume'] or ''), ('Funcția (calitatea)', 'BENEFICIAR'),
                              ('Suma (în cifre)', num(amount, 2)), ('Suma (în litere)', amount_words(amount)),
                              ('Scopul', refund.get('Explicatie') or '')]},
        {'t': 'sign', 'heads': ['Conducătorul\nunității', 'Viza de control\nfinanciar preventiv', 'Compartiment\nfinanciar-contabil']},
        {'t': 'box', 'title': 'DATE SUPLIMENTARE PRIVIND BENEFICIARUL SUMEI:',
         'rows': [('Actul de identitate seria / numărul', ''), ('Am primit suma de (în cifre)', figures), ('Data', day), ('Semnătura', '')]},
        {'t': 'box', 'title': 'CASIER', 'rows': [('Am plătit suma de (în cifre)', figures), ('Data', day), ('Semnătura', '')]},
    ]
    return model('Dispoziție de plată', 'Dispozitie_plata', blocks, font=10, footer='Raport realizat în ADECHIT · 14-4-4')


# ---- financial state (rpt_SitFin) -----------------------------------------------------------------------------------
# Legacy report: its Access source («Situatii salvate») is not exported, so the columns are mapped to the situation fields
# by name (see the summary of SLICE-ADE9-01): achieved = receipts, anticipated = Anticipat, payments = other payments.

def financial_report(repo, unit, args):
    month, rows = situation_rows(repo, need_int(args, 'month'), None)
    require(bool(rows), 'NO_DATA', 'Raportul selectat nu conține date.', 404)
    issuer = issuer_data(repo, unit)
    numbers = {}
    for r in repo.query('SELECT p.IDZ, c.Numar FROM AD_Chitante c JOIN AD_Plati p ON p.IDPL=c.IDPL AND p.SubunitId=c.SubunitId '
                        'WHERE p.SubunitId=%s AND p.IDL=%s AND p.Anulata=0 AND c.Anulata=0 ORDER BY c.Numar', (repo.scope(), month['IDL'])):
        numbers.setdefault(r['IDZ'], []).append(str(r['Numar']))
    following = date(month['Anul'] + (month['Luna'] == 12), month['Luna'] % 12 + 1, 1)
    start_label, next_label = f"01.{month['Luna']:02d}.{month['Anul']}", following.strftime('%d.%m.%Y')
    columns = [col('NR.\nCRT.', 540, 'c'), col('Numele și prenumele', 3996), col('Nr. zile\nprez.', 750, 'c'),
               col(f'Debit\n{start_label}', 660, 'r'), col(f'Credit\n{start_label}', 660, 'r'), col('Contract\nstabilit', 900, 'r'),
               col('Valoare\nachitată', 900, 'r'), col('Achitat\nanticipat', 900, 'r'), col('Plăți', 900, 'r'),
               col(f'Debit\n{next_label}', 660, 'r'), col(f'Credit\n{next_label}', 660, 'r'), col('Număr chitanță', 3270, 'c')]
    rows = sorted(rows, key=lambda r: collate(r.get('Nume')))
    body = [{'cells': [str(i), str(r.get('Nume') or ''), str(r.get('ZilePrezenta') or 0), num(r.get('SID'), 2), num(r.get('SIC'), 2),
                       num(r.get('ValoareContract'), 2), num(r.get('Plata'), 2), num(r.get('Anticipat'), 2), num(r.get('Plati'), 2),
                       num(r.get('SFD'), 2), num(r.get('SFC'), 2), ', '.join(numbers.get(r.get('IDZ'), []))]} for i, r in enumerate(rows, 1)]

    def total(key):
        return num(sum((r.get(key) or 0) for r in rows), 2)

    body.append({'cells': [{'t': 'TOTAL UNITATE', 'span': 2}, str(sum(r.get('ZilePrezenta') or 0 for r in rows)), total('SID'), total('SIC'),
                           total('ValoareContract'), total('Plata'), total('Anticipat'), total('Plati'), total('SFD'), total('SFC'), ''], 'cls': 'total'})
    blocks = [{'t': 'title', 'text': f"Situația financiară la {issuer['name']}", 'sub': [month_label(month)]},
              {'t': 'table', 'cols': columns, 'rows': body}, {'t': 'spacer'}, {'t': 'text', 'text': 'Întocmit,', 'align': 'right', 'bold': True}]
    return model('Situația financiară', 'Situatia_financiara', blocks, landscape=True, font=7.5)


from .payment_notice import payment_notice_report

BUILDERS = {'payment-notice': payment_notice_report, 'situation': situation_report, 'cash': cash_register_report, 'bank': bank_report, 'cancelled': cancelled_report,
            'account': account_report, 'debtor': debtor_report, 'payment-order': payment_order, 'financial': financial_report}


HIDDEN = {'debtor', 'financial'}   # buttons hidden from the panel; the code stays in case they come back

REPORT_NAMES = {'payment-notice': 'ADECHIT_SITUATIE_PLATA', 'situation': 'ADECHIT_SITUATIE_DEBITORI', 'cash': 'ADECHIT_REGISTRU_CASA',
                'bank': 'ADECHIT_RAPORT_BANCA', 'cancelled': 'ADECHIT_DOCUMENTE_ANULATE',
                'account': 'ADECHIT_FISA_CONT', 'debtor': 'ADECHIT_FISA_DEBITOR',
                'payment-order': 'ADECHIT_DISPOZITIE_PLATA', 'financial': 'ADECHIT_SITUATIE_FINANCIARA'}


def build(repo, unit, kind, args):
    require(kind in BUILDERS and kind not in HIDDEN, 'REPORT', 'Raport necunoscut.', 404)
    built = BUILDERS[kind](repo, unit, args)
    built['report_name'] = REPORT_NAMES[kind]
    return built
