"""Design-time validation of editable report definitions; no SQL execution."""
import math
import re
from .nomenclatoare import NomError

FIELD = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')


def fail(message):
    raise NomError('REPORT_CONFIG', message, 422)


def number(value, lower, upper, label):
    try:
        result = float(value)
    except (ValueError, TypeError):
        fail(label + ': valoare numerică invalidă.')
    if isinstance(value, bool) or not math.isfinite(result) or not lower <= result <= upper:
        fail(label + ': valoare în afara limitelor.')
    return result


def string(value, label, maximum=5000):
    if not isinstance(value, str) or len(value) > maximum:
        fail(label + ': text invalid sau prea lung.')


def table(value):
    columns = value.get('columns')
    if not isinstance(columns, list) or len(columns) > 60:
        fail('Coloanele trebuie să fie o listă de maximum 60 de câmpuri.')
    for col in columns:
        if not isinstance(col, dict) or not FIELD.fullmatch(str(col.get('key', ''))):
            fail('Câmp de coloană invalid.')
        string(col.get('label'), 'Etichetă coloană', 500)
        number(col.get('width', 1), .1, 20, 'Lățime coloană')
        number(col.get('size', 8), 5, 36, 'Font coloană')
        if col.get('type') not in ('text', 'decimal', 'int', 'date'):
            fail('Tip de coloană necunoscut.')
        if col.get('align', 'l') not in ('l', 'c', 'r') or not isinstance(col.get('bold', False), bool):
            fail('Formatare coloană invalidă.')
    number(value.get('table_width', 1), .25, 1, 'Lățime tabel')
    for key in ('groups', 'totals'):
        fields = value.get(key, [])
        if not isinstance(fields, list) or any(not isinstance(f, str) or not FIELD.fullmatch(f) for f in fields):
            fail('Câmpuri de grupare/totalizare invalide.')
    headers = value.get('headers', [])
    if not isinstance(headers, list) or len(headers) > 4:
        fail('Antete de tabel invalide.')
    for groups in headers:
        occupied = set()
        if not isinstance(groups, list):
            fail('Rând de antet invalid.')
        for group in groups:
            if not isinstance(group, dict):
                fail('Grup de antet invalid.')
            start, span = group.get('start'), group.get('span')
            if type(start) is not int or type(span) is not int or start < 0 or span < 1 or start + span > len(columns):
                fail('Grupul de antet depășește coloanele.')
            cells = set(range(start, start + span))
            if cells & occupied:
                fail('Grupurile de antet se suprapun.')
            occupied |= cells
            string(group.get('label'), 'Text antet tabel', 500)


def definition(part, value):
    if part == 'unit':
        for key in ('name', 'fiscal_code', 'address', 'manager', 'header', 'footer'):
            string(value.get(key), key)
    elif part == 'layout':
        if value.get('schema') != 1 or not isinstance(value.get('landscape'), bool):
            fail('Versiune sau orientare invalidă.')
        string(value.get('title'), 'Titlu', 1000)
        number(value.get('font_size', 8), 5, 24, 'Font raport')
        number(value.get('title_size', 15), 8, 40, 'Font titlu')
        if value.get('form', 'generic') not in ('generic', 'nir-classic'):
            fail('Formular de raport necunoscut.')
        table(value)
        sections = value.get('sections', {})
        if not isinstance(sections, dict) or len(sections) > 20:
            fail('Secțiuni invalide.')
        for section in sections.values():
            if not isinstance(section, dict) or not section.get('columns'):
                fail('Secțiune fără coloane.')
            table(section)
        signs = value.get('signatures', [])
        if not isinstance(signs, list) or len(signs) > 10:
            fail('Semnături invalide.')
        for sign in signs:
            string(sign, 'Semnătură', 500)
    elements = value.get('elements', [])
    if not isinstance(elements, list) or len(elements) > 100:
        fail('Elemente de raport invalide.')
    for element in elements:
        if not isinstance(element, dict) or element.get('band') not in (('header', 'footer') if part == 'unit' else ('header', 'footer', 'before', 'after')):
            fail('Bandă de raport necunoscută.')
        string(element.get('text'), 'Text element')
        x = number(element.get('x', 0), 0, .9999, 'Poziție orizontală')
        width = number(element.get('w', 1), .01, 1, 'Lățime element')
        if x + width > 1.00001:
            fail('Elementul depășește lățimea paginii.')
        number(element.get('y', 0), 0, 100, 'Poziție verticală')
        number(element.get('h', 12), 5, 100, 'Înălțime element')
        number(element.get('size', 8), 5, 36, 'Font element')
        if not isinstance(element.get('bold', False), bool):
            fail('Formatare element invalidă.')
