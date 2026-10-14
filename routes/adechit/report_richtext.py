"""Small, shared HTML vocabulary for configurable report notes, print and PDF.

Input formatting is translated to CSS classes. No supplied attributes, URLs,
scripts or CSS reach the output. The saved document also works without inline CSS.
"""
import re
from html import escape
from html.parser import HTMLParser

COLORS = {'black': '#000000', 'red': '#ff0000', 'blue': '#4169e1', 'green': '#008000',
          'orange': '#ff8c00', 'purple': '#800080', 'grey': '#808080'}
FONTS = {'sans': 'Arial', 'serif': 'Times New Roman', 'mono': 'Courier New'}
SIZES = (8, 10, 12, 14, 16, 18, 24)
BLOCKS = {'p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li'}
SKIP = {'script', 'style', 'iframe', 'object', 'svg', 'math', 'template'}
INLINE = {'b', 'strong', 'i', 'em', 'u', 'span', 'font'}


def formatting(tag, attrs, parent):
    result = dict(parent)
    if tag in ('b', 'strong'):
        result['bold'] = True
    if tag in ('i', 'em'):
        result['italic'] = True
    if tag == 'u':
        result['underline'] = True
    if tag.startswith('h') and tag in BLOCKS:
        result.update(bold=True, size={ 'h1': 24, 'h2': 18, 'h3': 16 }.get(tag, 14))
    css = dict(part.split(':', 1) for part in attrs.get('style', '').lower().split(';') if ':' in part)
    css = {key.strip(): value.strip() for key, value in css.items()}
    color = attrs.get('color', css.get('color', '')).lower().strip()
    for key, value in COLORS.items():
        rgb = 'rgb(' + ','.join(str(int(value[i:i + 2], 16)) for i in (1, 3, 5)) + ')'
        if color in (key, value, rgb, rgb.replace(',', ', ')):
            result['color'] = key
    if color == '#f00':
        result['color'] = 'red'
    face = attrs.get('face', css.get('font-family', '')).lower()
    for key, value in FONTS.items():
        if value.lower() in face:
            result['font'] = key
    size = attrs.get('size')
    if size and size.isdigit() and 1 <= int(size) <= 7:
        result['size'] = SIZES[int(size) - 1]
    match = re.fullmatch(r'(\d+(?:\.\d+)?)(pt|px)', css.get('font-size', ''))
    if match:
        points = float(match[1]) * (.75 if match[2] == 'px' else 1)
        result['size'] = min(SIZES, key=lambda value: abs(value - points))
    if css.get('font-weight') in ('bold', '700', '800', '900'):
        result['bold'] = True
    elif css.get('font-weight') in ('normal', '400'):
        result.pop('bold', None)
    if css.get('font-style') == 'italic':
        result['italic'] = True
    if 'underline' in css.get('text-decoration', ''):
        result['underline'] = True
    align = css.get('text-align', attrs.get('align', ''))
    if align in ('left', 'center', 'right'):
        result['align'] = align
    for name in attrs.get('class', '').split():
        if name in ('rt-bold', 'rt-italic', 'rt-underline'):
            result[name[3:]] = True
        for prefix, choices, field in [('rt-color-', COLORS, 'color'), ('rt-font-', FONTS, 'font')]:
            if name.startswith(prefix) and name[len(prefix):] in choices:
                result[field] = name[len(prefix):]
        if name.startswith('rt-size-') and name[8:].isdigit() and int(name[8:]) in SIZES:
            result['size'] = int(name[8:])
        if name in ('rt-left', 'rt-center', 'rt-right'):
            result['align'] = name[3:]
    return result


class NoteParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.paragraphs = []
        self.current = None
        self.stack = []
        self.lists = []
        self.skipped = 0

    def current_style(self):
        return self.stack[-1][1] if self.stack else {}

    def paragraph(self, style=None):
        if self.current is None:
            self.current = {'runs': [], 'align': (style or self.current_style()).get('align', 'left')}
            self.paragraphs.append(self.current)
        return self.current

    def handle_starttag(self, tag, attrs):
        if tag in SKIP or self.skipped:
            self.skipped += 1
            return
        values = dict(attrs)
        if tag in ('ul', 'ol'):
            self.current = None
            start = values.get('start') or '1'
            self.lists.append({'tag': tag, 'number': int(start) - 1 if start.isdigit() and len(start) < 6 else 0})
        if tag in BLOCKS | INLINE:
            style = formatting(tag, {k: v or '' for k, v in values.items()}, self.current_style())
            self.stack.append((tag, style))
            if tag in BLOCKS:
                self.current = None
                paragraph = self.paragraph(style)
                for name in values.get('class', '').split():
                    if re.fullmatch(r'rt-indent-[1-5]', name):
                        paragraph['indent'] = int(name[-1])
                if tag == 'li' and self.lists:
                    self.lists[-1]['number'] += 1
                    paragraph.update(indent=min(len(self.lists), 5),
                                     bullet=f"{self.lists[-1]['number']}." if self.lists[-1]['tag'] == 'ol' else '•')
        if tag == 'br':
            self.handle_data('\n')

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag != 'br':
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if self.skipped:
            self.skipped = max(0, self.skipped - 1)
            return
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break
        if tag in BLOCKS or tag in ('ul', 'ol'):
            self.current = None
        if tag in ('ul', 'ol') and self.lists:
            self.lists.pop()

    def handle_data(self, data):
        if self.skipped or not data:
            return
        if self.current is None and not data.strip():
            return
        self.paragraph()['runs'].append({'text': data, **self.current_style()})


def parse_html(source):
    parser = NoteParser()
    parser.feed(source or '')
    parser.close()
    return parser.paragraphs


def classes(run):
    names = ['rt-' + key for key in ('bold', 'italic', 'underline') if run.get(key)]
    names += [f'rt-{key}-{run[key]}' for key in ('color', 'font', 'size') if key in run]
    return ' '.join(names)


def paragraphs_html(paragraphs):
    output = []
    for paragraph in paragraphs:
        text = ''.join(f'<span class="{classes(run)}">{escape(run["text"]).replace(chr(10), "<br>")}</span>'
                       for run in paragraph['runs'])
        indent = f' rt-indent-{paragraph["indent"]}' if paragraph.get('indent') else ''
        bullet = escape(paragraph.get('bullet', ''))
        output.append(f'<p class="rt-{paragraph["align"]}{indent}">{bullet + " " if bullet else ""}{text or "<br>"}</p>')
    return ''.join(output)


def sanitized_html(source):
    return paragraphs_html(parse_html(source))


def editable_html(source):
    """Safe semantic markup that native editing commands can toggle and extend."""
    output, opened = [], None
    for paragraph in parse_html(source):
        list_tag = ('ul' if paragraph.get('bullet') == '•' else 'ol') if paragraph.get('bullet') else None
        if list_tag != opened:
            if opened:
                output.append(f'</{opened}>')
            if list_tag:
                start = paragraph['bullet'].rstrip('.')
                output.append('<ul>' if list_tag == 'ul' else f'<ol start="{start}">')
            opened = list_tag
        pieces = []
        for run in paragraph['runs']:
            text = escape(run['text']).replace('\n', '<br>')
            for field, tag in [('bold', 'b'), ('italic', 'i'), ('underline', 'u')]:
                if run.get(field):
                    text = f'<{tag}>{text}</{tag}>'
            attrs = []
            if run.get('font'):
                attrs.append(f'face="{FONTS[run["font"]]}"')
            if run.get('size'):
                attrs.append(f'size="{SIZES.index(run["size"]) + 1}"')
            if run.get('color'):
                attrs.append(f'color="{COLORS[run["color"]]}"')
            pieces.append(f'<font {" ".join(attrs)}>{text}</font>' if attrs else text)
        tag = 'li' if list_tag else 'p'
        output.append(f'<{tag} align="{paragraph["align"]}">{"".join(pieces) or "<br>"}</{tag}>')
    if opened:
        output.append(f'</{opened}>')
    return ''.join(output)


def pdf_flowables(paragraphs, base_size=10):
    from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph
    output = []
    for paragraph in paragraphs:
        pieces = []
        for run in paragraph['runs']:
            text = escape(run['text']).replace('\n', '<br/>')
            face = {'sans': 'Helvetica', 'serif': 'Times-Roman', 'mono': 'Courier'}.get(run.get('font'), 'Helvetica')
            # Standard PDF fonts need DejaVu for Romanian and other Unicode glyphs.
            text = re.sub(r'[^\x00-\xff]', lambda match: f'<font name="AdeReport">{match[0]}</font>', text)
            for key, tag in [('bold', 'b'), ('italic', 'i'), ('underline', 'u')]:
                if run.get(key):
                    text = f'<{tag}>{text}</{tag}>'
            text = f'<font name="{face}" color="{COLORS.get(run.get("color"), "#000000")}" size="{run.get("size", base_size)}">{text}</font>'
            pieces.append(text)
        largest = max((run.get('size', base_size) for run in paragraph['runs']), default=base_size)
        style = ParagraphStyle('note', fontName='AdeReport', fontSize=base_size, leading=largest * 1.25,
                               alignment={'left': TA_LEFT, 'center': TA_CENTER, 'right': TA_RIGHT}[paragraph['align']],
                               leftIndent=paragraph.get('indent', 0) * 12, spaceAfter=4)
        output.append(Paragraph(''.join(pieces) or '<br/>', style, bulletText=paragraph.get('bullet')))
    return output


def plain_text(paragraphs):
    return '\n'.join((p.get('bullet', '') + ' ' if p.get('bullet') else '')
                     + ''.join(run['text'] for run in p['runs']) for p in paragraphs)
