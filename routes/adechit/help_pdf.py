"""SLICE-AD12: the ADECHIT help as a PDF, built from the same JSON file the page shows (static/help/adechit.json)."""
import json
import re
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape
from .domain import DomainError
from .report_render import FONT, _font_lock

CONTENT = Path(__file__).resolve().parents[2] / 'static' / 'help' / 'adechit.json'
_TOKEN = re.compile(r'\*\*([^*]+)\*\*|\[([^\]]+)\]\(#([a-z0-9-]+)\)')
INK, BLUE, EMPHASIS = '#1b1b1b', '#0b4f9c', '#0b2a55'


def load_content():
    try:
        return json.loads(CONTENT.read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        raise DomainError('HELP_CONTENT', 'Conținutul ajutorului nu a putut fi citit.', 500) from error


# The PDF font has no emoji: each one the help text uses gets a plain symbol the font does have.
SYMBOLS = {'️': '', '📥': '↓', '❌': '✕', '➕': '+', '➡': '→', '↩': '↩', '🖨': ''}


def plain(text):
    text = re.sub(r' ((?:🔓|🔒))', '', text)
    for symbol, replacement in SYMBOLS.items():
        text = text.replace(symbol, replacement)
    return text


def markup(text):
    """**bold** and [text](#id) into ReportLab paragraph markup. The font has one weight only, so bold is shown by colour."""
    text = plain(text)
    out, position = [], 0
    for match in _TOKEN.finditer(text):
        out.append(escape(text[position:match.start()]))
        if match.group(1) is not None:
            out.append(f'<font color="{EMPHASIS}"><b>{escape(match.group(1))}</b></font>')
        else:
            out.append(f'<a href="#{match.group(3)}" color="{BLUE}">{escape(match.group(2))}</a>')
        position = match.end()
    out.append(escape(text[position:]))
    return ''.join(out)


def pdf_bytes():
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    except ImportError as error:
        raise DomainError('PDF_DEPENDENCY', 'Generarea PDF necesită instalarea dependențelor din requirements-adechit.txt pe server.', 503) from error
    with _font_lock:
        if 'AdeReport' not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont('AdeReport', str(FONT)))
            pdfmetrics.registerFontFamily('AdeReport', normal='AdeReport', bold='AdeReport', italic='AdeReport', boldItalic='AdeReport')
    content = load_content()
    base = ParagraphStyle('help', fontName='AdeReport', fontSize=10, leading=14.5, textColor=colors.HexColor(INK), spaceAfter=5)
    title = ParagraphStyle('help-title', parent=base, fontSize=22, leading=27, spaceAfter=2, textColor=colors.HexColor(EMPHASIS))
    subtitle = ParagraphStyle('help-subtitle', parent=base, fontSize=11, textColor=colors.HexColor('#555555'), spaceAfter=14)
    h2 = ParagraphStyle('help-h2', parent=base, fontSize=15, leading=19, spaceBefore=14, spaceAfter=7, textColor=colors.HexColor(EMPHASIS))
    h3 = ParagraphStyle('help-h3', parent=base, fontSize=11.5, leading=15, spaceBefore=9, spaceAfter=4, textColor=colors.HexColor(EMPHASIS))
    item = ParagraphStyle('help-item', parent=base, leftIndent=16, bulletIndent=3, spaceAfter=3)
    note = ParagraphStyle('help-note', parent=base, backColor=colors.HexColor('#eef5ff'), borderColor=colors.HexColor(BLUE),
                          borderWidth=0, borderPadding=(5, 6, 5, 6), leftIndent=8, rightIndent=4, spaceBefore=4, spaceAfter=9)
    cell = ParagraphStyle('help-cell', parent=base, fontSize=9, leading=12, spaceAfter=0)
    toc_style = ParagraphStyle('help-toc', parent=base, fontSize=11, leading=17, leftIndent=8, spaceAfter=0)

    def anchored(style, text, anchor=None):
        return Paragraph((f'<a name="{anchor}"/>' if anchor else '') + escape(plain(text)), style)

    story = [Paragraph(escape(content['title']), title), Paragraph(escape(content['subtitle']), subtitle),
             Paragraph('Cuprins', h2)]
    for section in content['sections']:
        story.append(Paragraph(f'<a href="#{section["id"]}" color="{BLUE}">{escape(section["title"])}</a>', toc_style))
    story.append(PageBreak())
    usable = A4[0] - 36 * mm
    for section in content['sections']:
        story.append(anchored(h2, section['title'], section['id']))
        for block in section['blocks']:
            if 'p' in block:
                story.append(Paragraph(markup(block['p']), base))
            elif 'h' in block:
                story.append(anchored(h3, block['h'], block.get('id')))
            elif 'ul' in block or 'ol' in block:
                bullets = 'ul' in block
                for number, text in enumerate(block['ul'] if bullets else block['ol'], 1):
                    story.append(Paragraph(markup(text), item, bulletText='•' if bullets else f'{number}.'))
                story.append(Spacer(1, 4))
            elif 'note' in block:
                story.append(Paragraph(markup(block['note']), note))
            elif 'table' in block:
                spec = block['table']
                rows = [[Paragraph(f'<b>{escape(head)}</b>', cell) for head in spec['head']]]
                rows += [[Paragraph(markup(text), cell) for text in row] for row in spec['rows']]
                widths = [usable / len(spec['head'])] * len(spec['head'])
                table = Table(rows, colWidths=widths, repeatRows=1)
                table.setStyle(TableStyle([('GRID', (0, 0), (-1, -1), .4, colors.HexColor('#999999')),
                                           ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e8eef7')),
                                           ('VALIGN', (0, 0), (-1, -1), 'TOP'), ('TOPPADDING', (0, 0), (-1, -1), 3),
                                           ('BOTTOMPADDING', (0, 0), (-1, -1), 3)]))
                story.extend([table, Spacer(1, 8)])
            else:
                raise DomainError('HELP_BLOCK', 'Bloc de ajutor necunoscut.', 500)

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont('AdeReport', 8)
        canvas.setFillColor(colors.HexColor('#666666'))
        canvas.drawString(18 * mm, 10 * mm, content['title'])
        canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f'Pagina {doc.page}')
        canvas.restoreState()

    stream = BytesIO()
    SimpleDocTemplate(stream, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=18 * mm,
                      title=content['title'], author='ADECHIT').build(story, onFirstPage=footer, onLaterPages=footer)
    return stream.getvalue()
