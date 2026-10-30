"""Shared NIR/FCS form on A4 portrait, following the supplied print reference.

Reads the same scoped, exact document rows as the other report outputs.
No database access, edits to saved definitions or invented document values.
"""
from collections import OrderedDict
from html import escape
from io import BytesIO
from pathlib import Path
from threading import Lock
from . import report_model
from .nomenclatoare import NomError

FONT_ROOT = Path(__file__).resolve().parents[2] / 'static' / 'fonts'
FONT_LOCK = Lock()
FONTS = {'NirMono': 'DejaVuSansMono.ttf', 'NirMonoBold': 'DejaVuSansMono-Bold.ttf',
         'NirMonoItalic': 'DejaVuSansMono-Oblique.ttf'}


def documents(rows, committees):
    grouped = OrderedDict()
    for row in rows:
        grouped.setdefault(row['document'], []).append(row)
    result = []
    for rows in grouped.values():
        # Stable order within each already-scoped document; numbering restarts at one.
        rows = sorted(rows, key=lambda row: row.get('line_id') or 0)
        committee = committees.get(rows[0].get('committee'), {})
        result.append({'header': rows[0], 'lines': rows, 'total': report_model.total(rows, 'value'),
                       'members': [committee.get('Receptie' + str(i)) or '' for i in range(1, 5)]})
    return result


def pdf(model):
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen.canvas import Canvas
    from reportlab.platypus import Paragraph, Table, TableStyle
    from .report_output import tokens
    from reporting import pdf_row_links

    row_links = bool(model.get('stock_card'))
    if row_links:
        Table = pdf_row_links.table_type(Table)

    with FONT_LOCK:
        for name, filename in FONTS.items():
            if name in pdfmetrics.getRegisteredFontNames():
                continue
            path = FONT_ROOT / filename
            if not path.is_file():
                raise NomError('REPORT_RESOURCE_MISSING',
                               f'Lipsește static/fonts/{filename}. Republicați fonturile rapoartelor.', 503)
            pdfmetrics.registerFont(TTFont(name, str(path)))

    page_width, page_height = A4
    margin = 18
    width = page_width - 2 * margin
    layout, identity = model['layout'], model['identity']
    size = layout.get('font_size', 8)
    zec = model['context']['zec']
    signature_top = page_height - 156
    footer_top = page_height - 66
    values = {'UNITATE': identity['name'], 'CUI': identity['fiscal_code'],
              'ADRESA': identity['address'], 'GESTIUNE': model['gestiune_name'],
              'PERIOADA': model['period_text'], 'RAPORT': model['title'],
              'ANTET': identity['header'], 'SUBSOL': identity['footer'], 'SUBUNITATE': identity.get('subunit_name', '')}

    def paragraph(value='', align=TA_LEFT, point=None, font='NirMono', href=None):
        point = point or size
        text = escape(str(value if value is not None else '')).replace('\n', '<br/>')
        if href:
            text = ('<font color="#175da8">' + text + '</font>' if row_links else
                    '<link href="' + escape(href, quote=True) + '" color="#175da8">' + text + '</link>')
        result = Paragraph(text, ParagraphStyle('nir', fontName=font, fontSize=point,
                                               leading=point * 1.12, alignment=align, spaceAfter=0))
        return pdf_row_links.mark(result, href) if row_links else result

    def table(data, proportions, commands=(), minima=None, repeat=0, inset=0):
        total = sum(proportions)
        item = Table(data, colWidths=[(width - 2 * inset) * w / total for w in proportions],
                     minRowHeights=minima, repeatRows=repeat, hAlign='LEFT')
        item.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (-1, -1), 2), ('RIGHTPADDING', (0, 0), (-1, -1), 2),
            ('TOPPADDING', (0, 0), (-1, -1), 1.5), ('BOTTOMPADDING', (0, 0), (-1, -1), 1.5),
            *commands]))
        return item

    def draw(item, canvas, top, x=margin):
        _, height = item.wrap(width, page_height)
        item.drawOn(canvas, x, page_height - top - height)
        return top + height

    def day(value):
        value = str(value or '')[:10]
        return '.'.join(reversed(value.split('-'))) if len(value) == 10 else value

    def receipt_number(row):
        number = row.get('number')
        number = abs(number) if isinstance(number, int) else number
        fraction = int(row.get('sub_number') or 0)
        return f'{number},{str(fraction).zfill(2).rstrip("0")}' if fraction else number

    def document_quantity(value):
        formatted = report_model.number(value, zec)
        return formatted.rstrip('0').rstrip(',') if ',' in formatted else formatted

    title = paragraph(model['title'], TA_CENTER, layout.get('title_size', 16), 'NirMonoBold')
    title_height = title.wrap(width, page_height)[1]
    issuer = identity['letterhead']
    letterhead = table([[paragraph('\n'.join(issuer['left']), point=9),
                         paragraph(issuer['right'], TA_RIGHT, 11)]], [.6, .4])
    title_top = 16 + letterhead.wrap(width, page_height)[1] + 8
    header_top = max(78, title_top + title_height + 12)

    def header(document):
        row = document['header']
        heading = [
            [paragraph('Cod\nfurnizor', TA_CENTER), paragraph('Nr. Factură /\nAviz Expediere', TA_CENTER),
             paragraph('Data\nExpedierii', TA_CENTER), paragraph('Nota de recepție', TA_CENTER), '',
             paragraph('Număr comandă', TA_CENTER)],
            ['', '', '', paragraph('Număr', TA_CENTER), paragraph('Data', TA_CENTER), ''],
            [paragraph(row.get('fiscal_code'), TA_CENTER), paragraph(row.get('invoice'), TA_CENTER),
             paragraph(day(row.get('movement_date')), TA_CENTER), paragraph(receipt_number(row), TA_CENTER),
             paragraph(day(row.get('date')), TA_CENTER), paragraph(row.get('order') or row.get('account'), TA_CENTER)],
        ]
        header_table = table(heading, [1.12, 1.40, .92, .84, .98, 2.70], [
            ('GRID', (0, 0), (-1, -1), .5, colors.black), ('BOX', (0, 0), (-1, -1), 1.4, colors.black),
            ('BACKGROUND', (0, 0), (-1, 1), colors.HexColor('#ececec')),
            ('SPAN', (3, 0), (4, 0)),
            *[('SPAN', (c, 0), (c, 1)) for c in (0, 1, 2, 5)],
        ], [19, 19, 20], inset=10)
        supplier = table([[
            paragraph('Subsemnații, membrii ai comisiei de recepție, am procedat '
                      'la recepționarea valorilor materiale furnizate de', point=7.5, font='NirMonoItalic'),
            paragraph(row.get('party'), TA_CENTER), paragraph(row.get('address'), TA_CENTER, 7.5),
            paragraph('cu auto nr.', TA_CENTER, 7.5, 'NirMonoItalic'),
        ]], [3.70, 1.88, 1.28, 1.08], [('GRID', (0, 0), (-1, -1), .5, colors.black)], [44], inset=10)
        accompanying = table([[
            paragraph('Documente însoțitoare:', point=7.5, font='NirMonoItalic'),
            paragraph(row.get('invoice'), TA_CENTER),
            paragraph('constatându-se următoarele: Marfa corespunde d.p.d.v. cantitativ',
                      point=7.5, font='NirMonoItalic'),
        ]], [1.60, 2.10, 4.24], [('GRID', (0, 0), (-1, -1), .5, colors.black)], [20], inset=10)
        return header_table, supplier, accompanying

    def details(document):
        rows = [[paragraph(label, TA_CENTER) for label in
                 ('Nr.\ncrt.', 'COD ARTICOL', 'DENUMIRE ARTICOL', 'UM', 'Cant. cnf.\nDocum.', 'RECEPȚIONAT', '', '')],
                ['', '', '', '', '', *[paragraph(label, TA_CENTER) for label in ('Cantitate', 'Preț unitar', 'Valoare')]]]
        for index, row in enumerate(document['lines'], 1):
            rows.append([paragraph(index, TA_CENTER), paragraph(row.get('code') or row.get('variant'), TA_CENTER),
                         paragraph(row.get('name'), TA_CENTER, href=row.get('stock_card_href')), paragraph(row.get('um'), TA_CENTER),
                         paragraph(document_quantity(row.get('quantity')), TA_RIGHT),
                         paragraph(report_model.number(row.get('quantity'), zec), TA_RIGHT),
                         paragraph(report_model.number(row.get('price'), zec), TA_RIGHT),
                         paragraph(report_model.number(row.get('value'), zec), TA_RIGHT)])
        commands = [('GRID', (0, 0), (-1, -1), .5, colors.black),
                    ('BACKGROUND', (0, 0), (-1, 1), colors.HexColor('#ececec')),
                    ('SPAN', (5, 0), (7, 0)),
                    *[('SPAN', (c, 0), (c, 1)) for c in range(5)]]
        return table(rows, [.36, .90, 2.30, .38, .64, .83, .98, 1.67], commands,
                     [21, 21] + [18] * len(document['lines']), repeat=2)

    def signatures(document):
        members = document['members']
        rows = [
            [paragraph('CERTIFICARE COMISIA DE RECEPȚIE', TA_CENTER), '', '', '', paragraph('Gestionar'), ''],
            [paragraph(label, TA_CENTER) for label in
             ('NUME ȘI PRENUME', 'SEMNĂTURA', 'NUME ȘI PRENUME', 'SEMNĂTURA', 'DATA PRIMIRII', 'SEMNĂTURA')],
            [paragraph(members[0], point=7.5), '', paragraph(members[2], point=7.5), '', '', ''],
            [paragraph(members[1], point=7.5), '', paragraph(members[3], point=7.5), '', '', ''],
        ]
        return table(rows, [1.60, 1.26, 1.61, 1.31, 1.43, 1.16], [
            ('GRID', (0, 0), (-1, -1), .5, colors.black),
            ('SPAN', (0, 0), (3, 0)), ('SPAN', (4, 0), (5, 0)),
            ('SPAN', (4, 2), (4, 3)), ('SPAN', (5, 2), (5, 3)),
            ('BACKGROUND', (0, 0), (-1, 1), colors.HexColor('#ececec')),
        ], [20, 18, 18, 18])

    # Plan pagination from measured rows, including wrapped long product names.
    pages = []
    for document in model.get('receptions', []):
        bands = header(document)
        measured = [item.wrap(width, page_height)[1] for item in bands]
        body_top = max(238, header_top + measured[0] + 8 + measured[1] + 6 + measured[2] + 24)
        signature = signatures(document)
        signature_height = signature.wrap(width, page_height)[1]
        sign_top = min(signature_top, footer_top - signature_height - 12)
        available = sign_top - body_top - 52
        if available < 60:
            raise NomError('REPORT_LAYOUT', 'Antetul sau numele comisiei depășesc spațiul machetei NIR.', 422)
        pending = details(document)
        first = True
        while pending is not None:
            _, height = pending.wrap(width, page_height)
            if height <= available:
                part, pending = pending, None
            else:
                parts = pending.split(width, available)
                if len(parts) < 2:
                    raise NomError('REPORT_LAYOUT', 'Un rând este prea înalt pentru macheta NIR. Reduceți fontul raportului.', 422)
                part, pending = parts
            pages.append({'document': document, 'bands': bands, 'table': part, 'body_top': body_top,
                          'signature': signature, 'sign_top': sign_top, 'last': pending is None,
                          'continued': not first})
            first = False

    stream = BytesIO()
    canvas = Canvas(stream, pagesize=A4)
    canvas.setTitle(model['title'])
    count = len(pages) or 1
    for index, plan in enumerate(pages or [None], 1):
        draw(letterhead, canvas, 16)
        draw(title, canvas, title_top)
        if plan:
            top = header_top
            for item, gap in zip(plan['bands'], (8, 6, 24)):
                top = draw(item, canvas, top, margin + 10) + gap
            bottom = draw(plan['table'], canvas, plan['body_top'])
            if plan['continued']:
                draw(paragraph('Continuare', TA_RIGHT, 7), canvas, plan['body_top'] - 14)
            if plan['last']:
                # Only the two rightmost cells carry the final total, as in the reference.
                total_width = width * (0.98 + 1.67) / 8.06
                total = Table([[paragraph('TOTALURI', TA_CENTER),
                                paragraph(report_model.number(plan['document']['total'], zec), TA_RIGHT, font='NirMonoBold')]],
                              colWidths=[total_width * .98 / 2.65, total_width * 1.67 / 2.65])
                total.setStyle(TableStyle([('GRID', (0, 0), (-1, -1), .5, colors.black),
                                          ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#ececec')),
                                          ('LEFTPADDING', (0, 0), (-1, -1), 2), ('RIGHTPADDING', (0, 0), (-1, -1), 2),
                                          ('TOPPADDING', (0, 0), (-1, -1), 2), ('BOTTOMPADDING', (0, 0), (-1, -1), 2)]))
                bottom = draw(total, canvas, bottom, margin + width - total_width)
            # Rule out the remaining unused writing area without covering any item rows.
            top = bottom + 6
            end = plan['sign_top'] - 34
            if end - top >= 14:
                canvas.setLineWidth(.5)
                canvas.line(margin + 3, page_height - top, page_width - margin - 3, page_height - top)
                canvas.line(margin + 3, page_height - end, page_width - margin - 3, page_height - end)
                canvas.line(margin + 3, page_height - end, page_width - margin - 3, page_height - top)
            draw(plan['signature'], canvas, plan['sign_top'])
        else:
            draw(paragraph('Nu există înregistrări pentru selecția curentă.'), canvas, header_top)
        canvas.setLineWidth(.5)
        canvas.line(margin, page_height - footer_top, page_width - margin, page_height - footer_top)
        current = {**values, 'PAGINA': index, 'PAGINI': count}
        footer = tokens(tokens(identity['footer'], current), current)
        p = paragraph(footer, point=7, font='NirMonoItalic')
        _, footer_height = p.wrap(width * .76, 50)
        p.drawOn(canvas, margin + 3, page_height - footer_top - 3 - footer_height)
        p = paragraph(f'Pagina {index} din {count}', TA_RIGHT, 8, 'NirMonoBold')
        _, height = p.wrap(width * .24, 20)
        p.drawOn(canvas, margin + width * .76, page_height - footer_top - 3 - height)
        canvas.showPage()
    canvas.save()
    return stream.getvalue()
