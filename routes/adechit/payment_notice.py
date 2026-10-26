"""Monthly advance payment notice and its per-subunit HTML configuration."""
from collections import Counter
from decimal import Decimal, ROUND_HALF_UP
from .domain import require
from . import report_richtext

REPORT_KEY = 'ADECHIT_SITUATIE_PLATA'
MAX_HTML = 30000
DEFAULT_HTML = '''<p align="center"><b>BENEFICIAR: [UNITATE]</b></p>
<p align="center">Cont unitate: <font color="red" size="4"><b>[CONT]</b></font></p>
<p align="center">Cod fiscal: <b>[COD_FISCAL]</b></p>
<p align="center"><b>Explicație plată:</b> Nume și prenume copil, denumirea grupei ([GRUPA])</p>
<p align="center"><font color="blue"><b>VĂ RUGĂM SĂ ACORDAȚI MAI MULTĂ ATENȚIE LA RUBRICA DETALII</b></font></p>
<p align="center"><font color="red">NU SCRIEȚI ALTCEVA</font></p>
<p><font color="red"><b>La efectuarea plății:</b></font></p>
<ul><li>Scrieți numele copilului și denumirea grupei.</li>
<li>Faceți transfer individual pentru fiecare copil.</li></ul>'''


def configuration(repo):
    rows = repo.query('SELECT FOOTER_HTML, VERSION FROM AD_REPORT_CONFIG WHERE SUBUNIT_ID=%s AND REPORT_KEY=%s',
                      (repo.scope(), REPORT_KEY))
    return {'html': rows[0]['FOOTER_HTML'] if rows else DEFAULT_HTML, 'version': rows[0]['VERSION'] if rows else 0}


def save_configuration(repo, body):
    source, version = body.get('html'), body.get('version')
    require(isinstance(source, str) and len(source) <= MAX_HTML, 'REPORT_HTML',
            'Textul HTML trebuie să aibă cel mult 30.000 de caractere.', 400)
    require(type(version) is int and version >= 0, 'VERSION', 'Versiunea configurării este invalidă.', 400)
    current = configuration(repo)
    require(current['version'] == version, 'CONFLICT', 'Textul raportului a fost modificat. Redeschideți configurarea.')
    # Store the editable source; every rendering passes through the same allowlist.
    if version:
        repo.execute('UPDATE AD_REPORT_CONFIG SET FOOTER_HTML=%s, VERSION=VERSION+1 '
                     'WHERE SUBUNIT_ID=%s AND REPORT_KEY=%s AND VERSION=%s',
                     (source, repo.scope(), REPORT_KEY, version))
        require(repo.cursor.rowcount == 1, 'CONFLICT', 'Textul raportului a fost modificat. Redeschideți configurarea.')
    else:
        repo.execute('INSERT INTO AD_REPORT_CONFIG (SUBUNIT_ID, REPORT_KEY, FOOTER_HTML, VERSION) VALUES (%s,%s,%s,1)',
                     (repo.scope(), REPORT_KEY, source))
    return configuration(repo)


def month_tax(repo, month):
    """The month's own tax row. When that link is missing or dangling (an import can leave it so), the tax its attendance rows
    carry, then the tax whose period covers the month, then the only tax there is."""
    taxes = repo.rows('ValoriTaxe')
    by_id = {row['IDV']: row for row in taxes}
    if month.get('IDV') in by_id:
        return by_id[month['IDV']]
    used = Counter(row['IDV'] for row in repo.rows('Prezenta', IDL=month['IDL']) if row.get('IDV') in by_id)
    if used:
        return by_id[used.most_common(1)[0][0]]
    period = f'{month["Anul"]:04d}-{month["Luna"]:02d}'
    covering = [row for row in taxes if row.get('DeLa') and row['DeLa'] <= period and (not row.get('PanaLa') or row['PanaLa'] >= period)]
    if covering:
        return max(covering, key=lambda row: row['DeLa'])
    return taxes[0] if len(taxes) == 1 else None


def payment_notice_report(repo, unit, args):
    from .reports import need_int, opt_int, situation_rows, collate, num, col, model
    from .receipt_output import issuer_data
    from .service import month_days, MONTHS
    month_id, group_id = need_int(args, 'month'), opt_int(args, 'group')
    everything = args.get('all') == '1'
    require(everything or group_id is not None, 'FILTER', 'Alegeți grupa sau bifați «Toate grupele».', 400)
    month, rows = situation_rows(repo, month_id, None if everything else group_id)
    require(bool(rows), 'NO_DATA', 'Raportul selectat nu conține date.', 404)
    days = month_days(month)
    tax = month_tax(repo, month)
    require(tax is not None, 'TAX', f'Luna {MONTHS[month["Luna"] - 1]} {month["Anul"]} nu are o taxă zilnică asociată (legătura lunii: {month.get("IDV")}).', 400)
    require(tax.get('TaxaZilnica') is not None, 'TAX', 'Taxa zilnică lipsește pentru luna selectată.')
    daily = Decimal(str(tax['TaxaZilnica']))
    monthly = (days * daily).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
    month_name = MONTHS[month['Luna'] - 1].upper()
    issuer = issuer_data(repo, unit)
    source = configuration(repo)['html']
    columns = [col('Nr.\ncrt.', 600, 'c', key='NR_CRT'), col('Numele și\nprenumele', 3600, key='NUME'),
               col('restanță\nSOLD DEBIT', 1200, 'r', key='SOLD_DEBIT'),
               col('bani în\ncont\nSOLD CREDIT', 1200, 'r', key='SOLD_CREDIT'),
               col(f'NR. MAX. ZILE LUNĂ\n{days} zile ×\n{num(daily, 2)} lei/zi\nTaxă LUNARĂ', 1200, 'r', key='TAXA_LUNARA'),
               col(f'de plată în\n{month_name}\n{month["Anul"]}', 1500, 'r', key='DE_PLATA')]
    groups = {}
    for row in rows:
        groups.setdefault(row['IDG'], []).append(row)
    blocks = []
    for index, members in enumerate(sorted(groups.values(), key=lambda group: collate(group[0].get('Grupa')))):
        if index:
            blocks.append({'t': 'break'})
        group_name = str(members[0].get('Grupa') or '')
        group_name = group_name[6:].strip() if group_name.lower().startswith('grupa ') else group_name
        body = []
        debit_total = credit_total = due_total = Decimal(0)
        for number, row in enumerate(sorted(members, key=lambda member: collate(member.get('Nume'))), 1):
            debit, credit = Decimal(str(row.get('SID') or 0)), Decimal(str(row.get('SIC') or 0))
            due = debit + monthly - credit
            debit_total += debit
            credit_total += credit
            due_total += due
            body.append({'cells': [str(number), str(row.get('Nume') or ''), num(debit, 2), num(credit, 2),
                                   num(monthly, 2), num(due, 2)]})
        body.append({'cells': ['', 'TOTAL', num(debit_total, 2), num(credit_total, 2), '', num(due_total, 2)], 'cls': 'total'})
        values = {'UNITATE': issuer['name'], 'COD_FISCAL': issuer['tax_code'], 'GRUPA': group_name,
                  'CONT': str(issuer['accounts'][0]['Cont'] or '') if issuer['accounts'] else '',
                  'LUNA': month_name, 'AN': str(month['Anul'])}
        # Replace placeholders in parsed text only, never in tag names or attributes.
        paragraphs = report_richtext.parse_html(source)
        for paragraph in paragraphs:
            for run in paragraph['runs']:
                for key, value in values.items():
                    run['text'] = run['text'].replace(f'[{key}]', value)
        blocks.extend([{'t': 'title', 'text': f'GRUPA {group_name.upper()}'},
                       {'t': 'table', 'cols': columns, 'rows': body, 'payment_notice': True,
                        'formula': ['', '', '1', '2', '3', '4 = (1+3)-2']},
                       {'t': 'richtext', 'paragraphs': paragraphs}])
    result = model('Situație de plată', 'Situatie_plata_grupe' if everything else 'Situatie_plata',
                   blocks, font=9, footer=None)
    result['payment_notice'] = True
    return result
