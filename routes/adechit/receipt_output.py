"""Saved receipt output. Reading/printing never issues another receipt number."""
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from html import escape
from io import BytesIO
from pathlib import Path
from threading import Lock
from .domain import DomainError, require

FONT = Path(__file__).resolve().parents[2] / 'static' / 'fonts' / 'DejaVuSans.ttf'
_font_lock = Lock()


def amount_words(amount):
    units = ['zero', 'unu', 'doi', 'trei', 'patru', 'cinci', 'șase', 'șapte', 'opt', 'nouă']
    teens = ['zece', 'unsprezece', 'doisprezece', 'treisprezece', 'paisprezece',
             'cincisprezece', 'șaisprezece', 'șaptesprezece', 'optsprezece', 'nouăsprezece']
    tens = ['', '', 'douăzeci', 'treizeci', 'patruzeci', 'cincizeci', 'șaizeci', 'șaptezeci', 'optzeci', 'nouăzeci']
    def small(value, feminine=False):
        if value < 10:
            return {1: 'una', 2: 'două'}.get(value, units[value]) if feminine else units[value]
        if value < 20:
            return 'douăsprezece' if feminine and value == 12 else teens[value - 10]
        if value < 100:
            return tens[value // 10] + (' și ' + small(value % 10, feminine) if value % 10 else '')
        hundreds = 'o sută' if value // 100 == 1 else small(value // 100, True) + ' sute'
        return hundreds + (' ' + small(value % 100, feminine) if value % 100 else '')
    number = Decimal(str(amount))
    require(number.is_finite() and abs(number) < Decimal('1000000000000'), 'AMOUNT', 'Suma chitanței nu poate fi reprezentată.', 400)
    number = number.quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
    value = int(abs(number))
    parts = []
    for scale, singular, plural in [(10**9, 'un miliard', 'miliarde'), (10**6, 'un milion', 'milioane'), (1000, 'o mie', 'mii')]:
        count, value = divmod(value, scale)
        if count:
            parts.append(singular if count == 1 else small(count, True) + (' de ' if count % 100 >= 20 or count % 100 == 0 else ' ') + plural)
    if value or not parts:
        parts.append(small(value))
    whole = int(abs(number))
    text = ('minus ' if number < 0 else '') + ' '.join(parts) + (' leu' if whole == 1 else (' de lei' if whole % 100 >= 20 or whole and whole % 100 == 0 else ' lei'))
    cents = int((abs(number) - whole) * 100)
    if cents:
        text += ' și ' + small(cents) + (' ban' if cents == 1 else ' bani')
    return text


def issuer_data(repo, unit):
    """The issuing unit as printed on receipts and reports (name, tax code, address, bank accounts)."""
    if repo.sqlite:
        return {'name': 'Unitate de probă', 'tax_code': '', 'address': '', 'accounts': []}
    rows = repo.query('SELECT u.NumeUnitate, u.CF, d.Adresa, d.Orasul, d.Judetul '
                      'FROM AVACONT_COMUN.Unitati u LEFT JOIN AVACONT_COMUN.Unitati_Date d ON d.DC=u.DC WHERE u.DC=%s', (unit,))
    require(len(rows) == 1, 'UNIT', 'Datele unității pentru chitanță nu sunt disponibile.', 409)
    row = rows[0]
    accounts = repo.query('SELECT Cont,Banca FROM AVACONT_COMUN.Unitati_Conturi WHERE DC=%s ORDER BY IdCont', (unit,))
    return {'name': str(row['NumeUnitate'] or ''), 'tax_code': str(row['CF'] or ''),
            'address': ', '.join(str(row[key]) for key in ('Adresa', 'Orasul', 'Judetul') if row.get(key)), 'accounts': accounts}


def receipt_data(repo, receipt_id, unit):
    receipt = repo.get('Chitante', receipt_id)
    payment = repo.get('Plati', receipt['IDPL'])
    require(payment.get('IDS') is not None, 'PAYER', 'Chitanța nu are un plătitor asociat.', 409)
    payer = repo.get('Platitori_sub', payment['IDS'])
    require(payer['IDP'] == payment['IDP'], 'PAYER', 'Legăturile plătitorului chitanței sunt discordante.', 409)
    issuer = issuer_data(repo, unit)
    amount = Decimal(str(payment.get('Plata') or 0)).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
    day = date.fromisoformat(str(receipt['Data'])[:10]).strftime('%d.%m.%Y')
    # The series and number belong to the subunit of the document; the printout names that subunit (SLICE-ADE10).
    return {'id': receipt_id, 'subunit': repo.subunit_name, 'series': receipt.get('Serie') or '', 'number': receipt['Numar'], 'date': day,
            'issuer': issuer, 'payer': payer.get('Nume') or '', 'address': payer.get('Adresa') or '',
            'amount': f'{amount:,.2f}'.replace(',', ' ').replace('.', ','), 'words': amount_words(amount),
            'explanation': receipt.get('Explicatie') or '', 'cancelled': bool(receipt.get('Anulata') or payment.get('Anulata'))}


def pdf_bytes(data):
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle, KeepTogether
    except ImportError as error:
        raise DomainError('PDF_DEPENDENCY', 'Generarea PDF necesită instalarea dependențelor din requirements-adechit.txt pe server.', 503) from error
    with _font_lock:
        if 'AdeReceipt' not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont('AdeReceipt', str(FONT)))
    stream = BytesIO()
    style = ParagraphStyle('receipt', fontName='AdeReceipt', fontSize=10, leading=15, spaceAfter=5)
    title = ParagraphStyle('receipt-title', parent=style, fontSize=16, leading=21, alignment=1, spaceAfter=12)
    label = ParagraphStyle('receipt-label', parent=style, fontSize=8, leading=11)
    def p(text, kind=style):
        return Paragraph(escape(str(text)), kind)
    copies = []
    for copy in ('Exemplarul plătitorului', 'Exemplarul unității'):
        block = [p(data['issuer']['name']), p('Cod fiscal: ' + data['issuer']['tax_code']), p(data['issuer']['address'])]
        for account in data['issuer']['accounts']:
            block.append(p('Cont: ' + str(account.get('Cont') or '') + (' - ' + str(account['Banca']) if account.get('Banca') else '')))
        if data['cancelled']:
            block.append(p('ANULATĂ', title))
        block += [Spacer(1, 3 * mm), p(f"CHITANȚĂ {data['series']} / {data['number']}", title), p('Data: ' + data['date']),
                  *([p('Subunitate: ' + data['subunit'])] if data.get('subunit') else []),
                  p('Am primit de la: ' + data['payer']), p('Adresa: ' + data['address']),
                  p('Suma de: ' + data['amount'] + ' lei'), p('Adică: ' + data['words']),
                  p('Reprezentând: ' + data['explanation']), Spacer(1, 7 * mm), p('Casier: ____________________'),
                  Spacer(1, 2 * mm), p(copy + ' · Cod 14-4-1', label)]
        box = Table([[block]], colWidths=[180 * mm])
        box.setStyle(TableStyle([('BOX', (0, 0), (-1, -1), .6, 'black'), ('LEFTPADDING', (0, 0), (-1, -1), 12),
                                ('RIGHTPADDING', (0, 0), (-1, -1), 12), ('TOPPADDING', (0, 0), (-1, -1), 12), ('BOTTOMPADDING', (0, 0), (-1, -1), 12)]))
        copies += [KeepTogether([box]), Spacer(1, 8 * mm)]
    SimpleDocTemplate(stream, pagesize=A4, rightMargin=15 * mm, leftMargin=15 * mm, topMargin=12 * mm,
                      bottomMargin=12 * mm, title=f"Chitanță {data['series']}/{data['number']}", author=data['issuer']['name']).build(copies)
    return stream.getvalue()
