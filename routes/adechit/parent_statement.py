"""SLICE-AD11: child-only monthly projection and chronological account statement."""
from calendar import monthrange
from datetime import date
from decimal import Decimal
from html import escape
from io import BytesIO
from .calculations import calculate, active
from .domain import require, SCHEMA, DomainError
from .receipt_output import FONT, _font_lock
from .service import MONTHS


def issuer_data(repo, unit):
    if repo.sqlite:
        return {'name': 'Grădinița de probă', 'tax_code': 'DEMO', 'address': 'Adresă fictivă', 'accounts': []}
    rows = repo.query('SELECT u.NumeUnitate,u.CF,d.Adresa,d.Orasul,d.Judetul FROM AVACONT_COMUN.Unitati u '
        'LEFT JOIN AVACONT_COMUN.Unitati_Date d ON d.DC=u.DC WHERE u.DC=%s', (unit,))
    require(len(rows) == 1, 'UNIT', 'Datele unității nu sunt disponibile.', 409)
    row = rows[0]
    return {'name': row['NumeUnitate'] or '', 'tax_code': str(row['CF'] or ''),
        'address': ', '.join(str(row[k]) for k in ('Adresa', 'Orasul', 'Judetul') if row.get(k)),
        'accounts': repo.query('SELECT Cont,Banca FROM AVACONT_COMUN.Unitati_Conturi WHERE DC=%s ORDER BY IdCont', (unit,))}


def child_data(repo, child_id):
    data = {table: [] for table in SCHEMA}
    data['Platitori'] = [repo.get('Platitori', child_id)]
    for table in ('Prezenta', 'Plati', 'Retur', 'SS_Buget'):
        data[table] = repo.rows(table, IDP=child_id)
    data['LunaD'] = repo.rows('LunaD')
    data['Grupe'] = repo.rows('Grupe')
    data['Grupe_Educator'] = repo.rows('Grupe_Educator')
    for table in ('Chitante', 'AlteDoc'):
        data[table] = repo.query(f'SELECT d.* FROM AD_{table} d JOIN AD_Plati p ON p.IDPL=d.IDPL '
                                'AND p.SubunitId=d.SubunitId WHERE p.SubunitId=%s AND p.IDP=%s', (repo.scope(), child_id))
    return data


def monthly_rows(data):
    rows = []
    for month in sorted(data['LunaD'], key=lambda r: (r['Anul'], r['Luna'])):
        saved = [r for r in data['SS_Buget'] if r['IDL'] == month['IDL']]
        if month['Inchisa']:
            require(saved or not any(r['IDL'] == month['IDL'] for r in data['Prezenta']), 'SNAPSHOT',
                    'O lună închisă nu are situația salvată. Solicitați verificarea evidenței.', 409)
            calculated = saved
        else:
            calculated, _ = calculate(data, month['IDL'])
        for row in calculated:
            rows.append({'month': f"{month['Anul']:04d}-{month['Luna']:02d}",
                'label': f"{MONTHS[month['Luna']-1].capitalize()} {month['Anul']}", 'closed': bool(month['Inchisa']),
                'days': row.get('ZilePrezenta') or 0, 'opening': (row.get('SID') or 0) - (row.get('SIC') or 0),
                'due': row.get('ValoareContract') or 0, 'payments': (row.get('Plata') or 0) + (row.get('Plati') or 0),
                'refunds': row.get('Retur') or 0, 'closing': (row.get('SFD') or 0) - (row.get('SFC') or 0)})
    return rows


def money(value):
    return Decimal(str(value or 0))


def format_money(value):
    return f'{money(value):,.2f}'.replace(',', ' ').replace('.', ',')


def make_statement(data, parent, issuer, start=None, end=None):
    child = data['Platitori'][0]
    months = {r['IDL']: r for r in data['LunaD']}
    snapshots = {r['IDL']: r for r in data['SS_Buget']}
    entries = []
    for row in data['Prezenta']:
        month = months[row['IDL']]
        source = snapshots.get(row['IDL'], row) if month['Inchisa'] else row
        day = date(month['Anul'], month['Luna'], monthrange(month['Anul'], month['Luna'])[1]).isoformat()
        entries.append({'date': day, 'explanation': f"Prezență {MONTHS[month['Luna']-1].capitalize()}/{month['Anul']} ({source.get('ZilePrezenta') or 0} zile)",
                        'debit': money(source.get('ValoareContract')), 'credit': money(0), 'order': (1, row['IDZ'])})
    receipts = {r['IDPL']: r for r in data['Chitante'] if active(r, 'Anulata')}
    others = {r['IDPL']: r for r in data['AlteDoc'] if active(r, 'Anulata')}
    for table, amount, flag, key in (('Plati', 'Plata', 'Anulata', 'IDPL'), ('Retur', 'Suma', 'Anulat', 'IDR')):
        for row in data[table]:
            if not active(row, flag):
                continue
            doc = receipts.get(row.get('IDPL')) or others.get(row.get('IDPL')) or {}
            raw_day = row.get('Data') or doc.get('Data') or doc.get('DataDoc')
            require(raw_day, 'MOVEMENT_DATE', 'O mișcare nu are dată. Solicitați verificarea evidenței.', 409)
            day = date.fromisoformat(str(raw_day)[:10]).isoformat()
            if table == 'Retur':
                explanation = 'Restituire ' + str(row.get('NrDoc') or '') + (' · ' + row['Explicatie'] if row.get('Explicatie') else '')
            elif doc.get('IDC') is not None:
                explanation = f"Chitanță {doc.get('Serie') or ''}/{doc['Numar']}"
            else:
                explanation = ' '.join(str(v) for v in (doc.get('Explicatie') or 'Plată', doc.get('NrDoc') or '') if v)
            entries.append({'date': day, 'explanation': explanation, 'debit': money(row.get(amount)) if table == 'Retur' else money(0),
                            'credit': money(row.get(amount)) if table == 'Plati' else money(0), 'order': (0, row[key])})
    entries.sort(key=lambda r: (r['date'], r['order']))
    opening = money(child.get('SI'))
    if start:
        opening += sum((r['debit'] - r['credit'] for r in entries if r['date'] < start), money(0))
    selected = [r for r in entries if (not start or r['date'] >= start) and (not end or r['date'] <= end)]
    def display(row, balance):
        return {**row, 'debit': format_money(row['debit']), 'credit': format_money(row['credit']),
                'balance': format_money(abs(balance)), 'balance_type': 'Debit' if balance > 0 else 'Credit' if balance < 0 else ''}
    output = [display({'date': '', 'explanation': 'Sold inițial', 'debit': money(0), 'credit': money(0), 'kind': 'opening'}, opening)]
    balance = opening
    total_debit = total_credit = money(0)
    month_debit = month_credit = money(0)
    previous = None
    def subtotal(period):
        year, month = map(int, period.split('-'))
        return display({'date': '', 'explanation': f'Sold final {MONTHS[month-1].capitalize()}/{year}',
                        'debit': month_debit, 'credit': month_credit, 'kind': 'subtotal'}, balance)
    for row in selected:
        period = row['date'][:7]
        if previous and period != previous:
            output.append(subtotal(previous))
            month_debit = month_credit = money(0)
        previous = period
        balance += row['debit'] - row['credit']
        total_debit += row['debit']; total_credit += row['credit']
        month_debit += row['debit']; month_credit += row['credit']
        output.append(display({**row, 'date': date.fromisoformat(row['date']).strftime('%d.%m.%Y'), 'kind': 'movement'}, balance))
    if previous:
        output.append(subtotal(previous))
    output.append(display({'date': '', 'explanation': 'Total perioadă', 'debit': total_debit, 'credit': total_credit, 'kind': 'total'}, balance))
    return {'issuer': issuer, 'child': child['Nume'], 'address': parent.get('Adresa') or '',
            'cnp': '•••••••••' + str(child.get('CNP') or '')[-4:] if child.get('CNP') else '',
            'email': parent.get('EMail') or '', 'rows': output,
            'period': f'{date.fromisoformat(start):%d.%m.%Y} - {date.fromisoformat(end):%d.%m.%Y}' if start else 'Toată perioada',
            'generated': date.today().strftime('%d.%m.%Y')}


def statement_pdf(data):
    try:
        import reportlab
    except ImportError as error:
        raise DomainError('PDF_DEPENDENCY', 'Generarea PDF necesită dependențele din requirements-adechit.txt.', 503) from error
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    with _font_lock:
        if 'AdeStatement' not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont('AdeStatement', str(FONT)))
    style = ParagraphStyle('statement', fontName='AdeStatement', fontSize=8, leading=11, wordWrap='CJK')
    title = ParagraphStyle('title', parent=style, fontSize=15, leading=20, alignment=1)
    numeric = ParagraphStyle('numeric', parent=style, alignment=2)
    def p(value, kind=style):
        return Paragraph(escape(str(value)), kind)
    issuer = data['issuer']
    account = ', '.join(str(r.get('Cont') or '') for r in issuer['accounts'])
    blocks = [Table([[p(issuer['name']), p('Cod fiscal: '+issuer['tax_code'])],
                     [p(issuer['address']), p('Cont: '+account)]], colWidths=[110*mm,70*mm]), Spacer(1,4*mm), p('Fișă cont', title),
              Spacer(1,3*mm)]
    identity = Table([[p(label),p(value)] for label,value in [('Nume',data['child']),('Adresa',data['address']),
                     ('CNP',data['cnp']),('Email',data['email']),('Perioada',data['period'])]], colWidths=[30*mm,150*mm])
    identity.setStyle(TableStyle([('GRID',(0,0),(-1,-1),.3,colors.lightgrey)]))
    blocks += [identity,Spacer(1,3*mm)]
    headers = ['Data','Explicație','DEBIT','CREDIT','Sold','Tip sold']
    rows = [[p(v) for v in headers]] + [[p(r[k],numeric if k in ('debit','credit','balance') else style)
            for k in ('date','explanation','debit','credit','balance','balance_type')] for r in data['rows']]
    table = Table(rows,colWidths=[23*mm,73*mm,22*mm,22*mm,22*mm,18*mm],repeatRows=1,hAlign='LEFT')
    rules = [('GRID',(0,0),(-1,-1),.4,colors.black),('VALIGN',(0,0),(-1,-1),'TOP'),
             ('BACKGROUND',(0,0),(-1,0),colors.whitesmoke),('LEFTPADDING',(0,0),(-1,-1),4),('RIGHTPADDING',(0,0),(-1,-1),4)]
    for i,row in enumerate(data['rows'],1):
        if row['kind'] == 'subtotal':
            rules.append(('BACKGROUND',(0,i),(-1,i),colors.HexColor('#eeeeee')))
        elif row['kind'] == 'total':
            rules.append(('BACKGROUND',(0,i),(-1,i),colors.HexColor('#d6d6d6')))
        elif row['kind'] != 'movement':
            rules.append(('BACKGROUND',(0,i),(-1,i),colors.whitesmoke))
    table.setStyle(TableStyle(rules)); blocks.append(table)
    stream = BytesIO()
    def footer(canvas, document):
        canvas.setFont('AdeStatement',8)
        canvas.drawString(15*mm,8*mm,'Generată la '+data['generated'])
        canvas.drawRightString(195*mm,8*mm,f'Pagina {document.page}')
    SimpleDocTemplate(stream,pagesize=A4,leftMargin=15*mm,rightMargin=15*mm,topMargin=12*mm,bottomMargin=15*mm,
                      title='Fișă cont',author=issuer['name']).build(blocks,onFirstPage=footer,onLaterPages=footer)
    return stream.getvalue()
