"""SLICE-ADE9-01: renders a report model as a print page (HTML) or as a PDF.

A model is plain data built by reports.py:
  {'title', 'filename', 'landscape': bool, 'font': pt, 'footer': 'text' | None, 'blocks': [...]}
Blocks: letterhead, title, text, table, box, sign, spacer, break. A table cell is a string or {'t': text, 'span': n}.
Column widths are Access twips (1/20 pt); they only give the proportions between the columns."""
from html import escape
from flask import url_for
from .domain import DomainError
from reporting.output import FONT, FONT_LOCK as _font_lock

PAGE_TWIPS = {True: 15930, False: 10770}   # usable width of an A4 sheet with 8 mm margins


def _cell(cell):
    return (cell['t'], int(cell.get('span', 1))) if isinstance(cell, dict) else (cell, 1)


def _total_width(columns):
    return sum(column['w'] for column in columns) or 1


# ---- HTML ----------------------------------------------------------------------------------------------------------

def _html_table(block, landscape):
    columns = block['cols']
    total = _total_width(columns)
    extra = ' rpt-payment-table' if block.get('payment_notice') else ''
    out = [f'<table class="rpt-table{extra}" width="100%"><colgroup>']
    out += [f'<col width="{column["w"] * 100 / total:.2f}%">' for column in columns]
    out.append('</colgroup>')
    if block.get('head', True):
        headers = []
        for index, column in enumerate(columns):
            rowspan = ' rowspan="2"' if block.get('formula') and index < 2 else ''
            label = escape(column['label']).replace('\n', '<br>')
            if block.get('payment_notice') and index in (2, 3):
                label = label.replace('<br>SOLD ', '<br><small>SOLD ') + '</small>'
            headers.append(f'<th class="{_tint(column)}"{rowspan}>{label}</th>')
        formula = ''
        if block.get('formula'):
            formula = '<tr class="rpt-formula">' + ''.join(f'<th>{escape(value)}</th>' for value in block['formula'][2:]) + '</tr>'
        out.append('<thead><tr>' + ''.join(headers) + '</tr>' + formula + '</thead>')
    out.append('<tbody>')
    for row_index, row in enumerate(block['rows']):
        if row.get('cls') == 'grand' and row_index:
            out.append(f'<tr class="grand-gap" aria-hidden="true"><td colspan="{len(columns)}"></td></tr>')
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
    if kind == 'richtext':
        from .report_richtext import paragraphs_html
        return '<section class="rpt-richtext">' + paragraphs_html(block['paragraphs']) + '</section>'
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
    from .report_excel import dialog_html
    landscape = bool(model['landscape'])
    css = [url_for('static', filename='css/adechit-report.css', _external=True),
           url_for('static', filename='css/adechit-export.css', _external=True)]
    if landscape:
        css.append(url_for('static', filename='css/adechit-report-landscape.css', _external=True))
    if model.get('payment_notice'):
        css += [url_for('static', filename='css/adechit-payment-notice.css', _external=True),
                url_for('static', filename='css/adechit-richtext.css', _external=True)]
    script = url_for('static', filename='js/adechit/report-print.js', _external=True)
    excel_script = url_for('static', filename='js/adechit/report-excel.js', _external=True)
    body = ''.join(_html_block(block, landscape) for block in model['blocks'])
    footer = f'<footer class="rpt-footer">{escape(model["footer"])}</footer>' if model.get('footer') else ''
    links = ''.join(f'<link rel="stylesheet" href="{escape(href)}">' for href in css)
    return (f'<!doctype html><html lang="ro"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{escape(model["title"])}</title>{links}<script src="{escape(excel_script)}" defer></script><script src="{escape(script)}" defer></script></head>'
            f'<body class="rpt{" rpt-wide" if landscape else ""}{" rpt-payment-notice" if model.get("payment_notice") else ""}"><nav class="rpt-bar">'
            f'<button id="report-print" type="button" title="Listare la imprimanta implicită">🖨️ Listare</button>'
            f'<button id="report-printer" type="button" title="Alegeți imprimanta">🖨️ Selecție imprimantă</button>'
            f'<button id="report-pdf" type="button" title="Salvează raportul ca PDF">📄 Export PDF</button>'
            f'<button id="report-excel" type="button" title="Alege coloanele și exportă raportul în Excel">📊 Export Excel</button>'
            f'<span class="rpt-bar-gap"></span>'
            f'<button id="report-zoom-out" type="button" title="Micșorează (doar pe ecran)">➖</button>'
            f'<span id="report-zoom-level" class="rpt-zoom">100%</span>'
            f'<button id="report-zoom-in" type="button" title="Mărește (doar pe ecran)">➕</button>'
            f'<span class="rpt-bar-gap"></span>'
            f'<button id="report-back" type="button" title="Închide raportul">⬅ Înapoi</button></nav>'
            f'<main>{body}</main>{footer}{dialog_html(model)}</body></html>')


# ---- PDF -----------------------------------------------------------------------------------------------------------

def pdf_bytes(model):
    from reporting import output
    from reporting.errors import ReportError
    from .report_model import shared_model
    try:
        return output.pdf(shared_model(model))
    except ReportError as error:
        raise DomainError(error.reason, str(error), error.status) from error
    except ImportError as error:
        raise DomainError('PDF_DEPENDENCY', 'Generarea PDF necesită dependențele din requirements-reporting.txt pe server.', 503) from error
