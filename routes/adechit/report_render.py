"""SLICE-ADE9-01: renders a report model as a print page (HTML) or as a PDF.

A model is plain data built by reports.py:
  {'title', 'filename', 'landscape': bool, 'font': pt, 'footer': 'text' | None, 'blocks': [...]}
Blocks: letterhead, title, text, table, box, sign, spacer, break. A table cell is a string or {'t': text, 'span': n}.
Column widths are Access twips (1/20 pt); they only give the proportions between the columns."""
from html import escape
from io import BytesIO
from pathlib import Path
from threading import Lock
from flask import url_for
from .domain import DomainError

FONT = Path(__file__).resolve().parents[2] / 'static' / 'fonts' / 'DejaVuSans.ttf'
_font_lock = Lock()
PAGE_TWIPS = {True: 15930, False: 10770}   # usable width of an A4 sheet with 8 mm margins


def _cell(cell):
    return (cell['t'], int(cell.get('span', 1))) if isinstance(cell, dict) else (cell, 1)


def _total_width(columns):
    return sum(column['w'] for column in columns) or 1


# ---- HTML ----------------------------------------------------------------------------------------------------------

def _html_table(block, landscape):
    columns = block['cols']
    total = _total_width(columns)
    out = ['<table class="rpt-table" width="100%"><colgroup>']
    out += [f'<col width="{column["w"] * 100 / total:.2f}%">' for column in columns]
    out.append('</colgroup>')
    if block.get('head', True):
        out.append('<thead><tr>' + ''.join(
            f'<th class="{_tint(column)}">{escape(column["label"]).replace(chr(10), "<br>")}</th>' for column in columns) + '</tr></thead>')
    out.append('<tbody>')
    for row in block['rows']:
        cells, index, parts = row['cells'], 0, []
        for cell in cells:
            text, span = _cell(cell)
            column = columns[index]
            classes = ' '.join(filter(None, [{'r': 'r', 'c': 'c'}.get(column.get('align'), ''), _tint(column)]))
            parts.append(f'<td class="{classes}"{f" colspan={span}" if span > 1 else ""}>{escape(str(text))}</td>')
            index += span
        out.append(f'<tr class="{row.get("cls", "")}">' + ''.join(parts) + '</tr>')
    out.append('</tbody></table>')
    return ''.join(out)


def _tint(column):
    return f'tint-{column["tint"]}' if column.get('tint') else ''


def _html_block(block, landscape):
    kind = block['t']
    if kind == 'letterhead':
        left = ''.join(f'<div>{escape(line)}</div>' for line in block['left'])
        return f'<div class="rpt-letterhead"><div class="rpt-left">{left}</div><div class="rpt-right">{escape(block.get("right", ""))}</div></div>'
    if kind == 'title':
        return f'<h1 class="rpt-title">{escape(block["text"])}</h1>' + ''.join(f'<p class="rpt-sub">{escape(line)}</p>' for line in block.get('sub', []))
    if kind == 'text':
        return f'<p class="rpt-text {block.get("align", "")} {"bold" if block.get("bold") else ""}">{escape(block["text"])}</p>'
    if kind == 'table':
        return _html_table(block, landscape)
    if kind == 'box':
        rows = ''.join(f'<tr><th>{escape(label)}</th><td>{escape(str(value))}</td></tr>' for label, value in block['rows'])
        title = f'<div class="rpt-box-title">{escape(block["title"])}</div>' if block.get('title') else ''
        return f'<section class="rpt-box">{title}<table class="rpt-kv">{rows}</table></section>'
    if kind == 'sign':
        heads = ''.join(f'<th>{escape(head).replace(chr(10), "<br>")}</th>' for head in block['heads'])
        blanks = ''.join('<td></td>' for _ in block['heads'])
        return f'<table class="rpt-sign"><tr>{heads}</tr><tr class="rpt-sign-space">{blanks}</tr></table>'
    if kind == 'spacer':
        return '<div class="rpt-spacer"></div>'
    if kind == 'break':
        return '<div class="rpt-break"></div>'
    raise DomainError('REPORT_BLOCK', 'Bloc de raport necunoscut.', 500)


def html_page(model):
    landscape = bool(model['landscape'])
    css = [url_for('static', filename='css/adechit-report.css', _external=True)]
    if landscape:
        css.append(url_for('static', filename='css/adechit-report-landscape.css', _external=True))
    script = url_for('static', filename='js/adechit/report-print.js', _external=True)
    body = ''.join(_html_block(block, landscape) for block in model['blocks'])
    footer = f'<footer class="rpt-footer">{escape(model["footer"])}</footer>' if model.get('footer') else ''
    links = ''.join(f'<link rel="stylesheet" href="{escape(href)}">' for href in css)
    return (f'<!doctype html><html lang="ro"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{escape(model["title"])}</title>{links}<script src="{escape(script)}" defer></script></head>'
            f'<body class="rpt{" rpt-wide" if landscape else ""}"><nav class="rpt-bar">'
            f'<button id="report-print" type="button" title="Listare la imprimanta implicită">🖨️ Listare</button>'
            f'<button id="report-printer" type="button" title="Alegeți imprimanta">🖨️ Selecție imprimantă</button>'
            f'<button id="report-pdf" type="button" title="Salvează raportul ca PDF">📄 Export PDF</button>'
            f'<span class="rpt-bar-gap"></span>'
            f'<button id="report-zoom-out" type="button" title="Micșorează (doar pe ecran)">➖</button>'
            f'<span id="report-zoom-level" class="rpt-zoom">100%</span>'
            f'<button id="report-zoom-in" type="button" title="Mărește (doar pe ecran)">➕</button>'
            f'<span class="rpt-bar-gap"></span>'
            f'<button id="report-back" type="button" title="Închide raportul">⬅ Înapoi</button></nav>'
            f'<main>{body}</main>{footer}</body></html>')


# ---- PDF -----------------------------------------------------------------------------------------------------------

def pdf_bytes(model):
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
        from reportlab.lib.pagesizes import A4, landscape as to_landscape
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    except ImportError as error:
        raise DomainError('PDF_DEPENDENCY', 'Generarea PDF necesită instalarea dependențelor din requirements-adechit.txt pe server.', 503) from error
    with _font_lock:
        if 'AdeReport' not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont('AdeReport', str(FONT)))
            pdfmetrics.registerFontFamily('AdeReport', normal='AdeReport', bold='AdeReport', italic='AdeReport', boldItalic='AdeReport')
    is_landscape = bool(model['landscape'])
    page = to_landscape(A4) if is_landscape else A4
    side = 8 * mm
    available = page[0] - 2 * side
    size = float(model.get('font', 8))
    grey, border = colors.HexColor('#ececec'), colors.HexColor('#a6a6a6')
    tints = {'d': colors.HexColor('#f9eded'), 'c': colors.HexColor('#e6edd7'), 'z': colors.HexColor('#dfe5ed')}
    aligns = {'l': TA_LEFT, 'c': TA_CENTER, 'r': TA_RIGHT}

    def style(name, **kw):
        return ParagraphStyle(name, fontName='AdeReport', fontSize=kw.pop('size', size), leading=kw.pop('leading', size * 1.25), **kw)

    def para(text, st):
        return Paragraph(escape(str(text)).replace('\n', '<br/>'), st)

    cell_styles = {key: style('cell-' + key, alignment=value) for key, value in aligns.items()}
    story = []

    def scaled(columns):
        total = _total_width(columns)
        factor = available / (total / 20)
        return [column['w'] / 20 * factor for column in columns]

    for block in model['blocks']:
        kind = block['t']
        if kind == 'letterhead':
            left = [para(line, style('lh', size=size + 1)) for line in block['left']]
            data = [[left, para(block.get('right', ''), style('lhr', size=size + 3, alignment=TA_RIGHT))]]
            table = Table(data, colWidths=[available * 0.6, available * 0.4])
            table.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0)]))
            story += [table, Spacer(1, 3 * mm)]
        elif kind == 'title':
            story.append(para(block['text'], style('title', size=14, leading=18, alignment=TA_CENTER, spaceAfter=2)))
            story += [para(line, style('sub', size=size + 1, alignment=TA_CENTER)) for line in block.get('sub', [])]
            story.append(Spacer(1, 2 * mm))
        elif kind == 'text':
            story.append(para(block['text'], style('text', size=size + (1 if block.get('bold') else 0), alignment=aligns.get({'center': 'c', 'right': 'r'}.get(block.get('align'), 'l')), spaceAfter=3)))
        elif kind == 'table':
            columns, data, commands = block['cols'], [], []
            if block.get('head', True):
                data.append([para(column['label'], style('head', alignment=TA_CENTER)) for column in columns])
                commands += [('BACKGROUND', (0, 0), (-1, 0), grey)]
                for index, column in enumerate(columns):
                    if column.get('tint'):
                        commands.append(('BACKGROUND', (index, 0), (index, 0), tints[column['tint']]))
            offset = len(data)
            for row_index, row in enumerate(block['rows']):
                line, index = [], 0
                for cell in row['cells']:
                    text, span = _cell(cell)
                    line.append(para(text, cell_styles[columns[index].get('align', 'l')]))
                    if span > 1:
                        commands.append(('SPAN', (index, offset + row_index), (index + span - 1, offset + row_index)))
                        line += [''] * (span - 1)
                    index += span
                line += [''] * (len(columns) - len(line))
                data.append(line)
                if row.get('cls') in ('total', 'grand', 'sub'):
                    commands.append(('BACKGROUND', (0, offset + row_index), (-1, offset + row_index), grey))
                if row.get('cls') == 'cancelled':
                    commands.append(('TEXTCOLOR', (0, offset + row_index), (-1, offset + row_index), colors.HexColor('#7f7f7f')))
            table = Table(data, colWidths=scaled(columns), repeatRows=1 if block.get('head', True) else 0, hAlign='LEFT')
            table.setStyle(TableStyle([('GRID', (0, 0), (-1, -1), .25, border), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                                       ('LEFTPADDING', (0, 0), (-1, -1), 2), ('RIGHTPADDING', (0, 0), (-1, -1), 2),
                                       ('TOPPADDING', (0, 0), (-1, -1), 1.5), ('BOTTOMPADDING', (0, 0), (-1, -1), 1.5), *commands]))
            story.append(table)
        elif kind == 'box':
            inner = [[para(label, style('kvl')), para(value, style('kvv'))] for label, value in block['rows']]
            rows = ([[para(block['title'], style('boxt', size=size + 3, alignment=TA_CENTER)), '']] if block.get('title') else []) + inner
            table = Table(rows, colWidths=[available * 0.3, available * 0.7], hAlign='LEFT')
            commands = [('BOX', (0, 0), (-1, -1), .8, colors.black), ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                        ('TOPPADDING', (0, 0), (-1, -1), 3), ('BOTTOMPADDING', (0, 0), (-1, -1), 3)]
            if block.get('title'):
                commands.append(('SPAN', (0, 0), (1, 0)))
            table.setStyle(TableStyle(commands))
            story += [table, Spacer(1, 2 * mm)]
        elif kind == 'sign':
            count = len(block['heads'])
            table = Table([[para(head, style('sgh', alignment=TA_CENTER)) for head in block['heads']], [''] * count],
                          colWidths=[available / count] * count, rowHeights=[None, 18 * mm], hAlign='LEFT')
            table.setStyle(TableStyle([('GRID', (0, 0), (-1, -1), .8, colors.black)]))
            story += [table, Spacer(1, 2 * mm)]
        elif kind == 'spacer':
            story.append(Spacer(1, 4 * mm))
        elif kind == 'break':
            story.append(PageBreak())
        else:
            raise DomainError('REPORT_BLOCK', 'Bloc de raport necunoscut.', 500)

    footer_text = model.get('footer')

    def draw_footer(canvas, doc):
        if not footer_text:
            return
        canvas.saveState()
        canvas.setFont('AdeReport', 7)
        canvas.drawString(side, 6 * mm, footer_text)
        canvas.drawRightString(page[0] - side, 6 * mm, f'Pagina: {doc.page}')
        canvas.restoreState()

    stream = BytesIO()
    SimpleDocTemplate(stream, pagesize=page, leftMargin=side, rightMargin=side, topMargin=8 * mm, bottomMargin=14 * mm,
                      title=model['title']).build(story, onFirstPage=draw_footer, onLaterPages=draw_footer)
    return stream.getvalue()
