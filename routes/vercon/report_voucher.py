"""Common P/T/C and consumption header, following the supplied form reference."""

FAMILIES = {'bp': ('BP', 'BON DE PREDARE'), 'bt': ('BT', 'BON DE TRANSFER'),
            'bs': ('BS', 'BON DE CASARE'), 'bc': ('BC', 'BON DE CONSUM'),
            'br': ('BX', 'BON DE RETUR')}
TYPES = {'MF': 'MIJLOACE FIXE ÎN FOLOSINȚĂ', 'OI': 'OBIECTE DE INVENTAR ÎN FOLOSINȚĂ',
         'MATERIALE': 'MATERIALE', 'ALIMENTE': 'ALIMENTE'}


def applies(spec):
    return spec['kind'] == 'documents' and spec.get('family') in FAMILIES


def header(spec, row, ctx, gestiune_name=''):
    family = spec['family']
    prefix, title = FAMILIES[family]
    parts = str(row.get('date') or '')[:10].split('-')
    year, month, day = parts if len(parts) == 3 else ('', '', '')
    if family in ('bt', 'bs'):
        giver = row.get('giver_name') or ''
    else:
        giver = (row.get('party') or '') if family == 'br' else 'Magazie'
    receiver = (row.get('party') or '') if family not in ('bs', 'br') else ('Magazie' if family == 'br' else '')
    cells = []

    def cell(column, line, text, colspan=1, rowspan=1, bold=False, size=8):
        cells.append({'column': column, 'row': line, 'text': text,
                      'colspan': colspan, 'rowspan': rowspan, 'bold': bold, 'size': size})

    cell(0, 0, gestiune_name or ctx['type'], colspan=7, bold=True, size=10)
    cell(7, 0, title, rowspan=4, bold=True, size=12)
    cell(0, 1, 'Număr\ndocument', rowspan=2, bold=True)
    cell(1, 1, 'Data', colspan=3, bold=True)
    cell(4, 1, TYPES.get(ctx['type'], ctx['type']), colspan=3, bold=True, size=9)
    for index, label in enumerate(('Ziua', 'Luna', 'Anul', 'Predător', 'Primitor', 'Nr. comandă'), 1):
        cell(index, 2, label, bold=True)
    for index, value in enumerate((f"{prefix} {row.get('number') or ''}".strip(), day, month, year,
                                   giver, receiver, row.get('order') or '')):
        cell(index, 3, value)
    return {'t': 'form_grid', 'widths': [125, 74, 73, 74, 177, 174, 117, 262],
            'keys': ['NUMAR_DOCUMENT', 'ZIUA', 'LUNA', 'ANUL', 'PREDATOR', 'PRIMITOR', 'COMANDA', 'TIP_BON'],
            'heights': [26, 15, 15, 16], 'cells': cells, 'keep_with_next': True}
