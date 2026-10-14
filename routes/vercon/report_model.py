"""Exact report values; one model is used for screen, PDF and XLSX."""
from decimal import Decimal, ROUND_HALF_UP, localcontext
import re


def number(value, zec):
    if value == '':
        return ''
    if value is None:
        return '—'
    with localcontext() as context:
        context.prec = 60
        rounded = Decimal(str(value)).quantize(Decimal(1).scaleb(-zec), rounding=ROUND_HALF_UP)
        return format(rounded, f',.{zec}f').replace(',', '#').replace('.', ',').replace('#', '.')


def total(rows, key):
    # NULL does not silently become a valid zero total.
    values = [row.get(key) for row in rows]
    if any(value is None for value in values):
        return None
    with localcontext() as context:
        context.prec = 60
        return format(sum((Decimal(str(value)) for value in values if value != ''), Decimal(0)), 'f')


def table(columns, rows, zec, totals=()):
    """columns: (key,label,type[,width]); raw rows remain attached for comparison."""
    cols = [{'key': c[0], 'label': c[1], 'type': c[2], 'w': c[3] if len(c) > 3 else 1350,
             'align': 'r' if c[2] in ('decimal', 'int') else 'l'} for c in columns]
    def cells(row):
        return [number(row.get(c['key']), zec) if c['type'] == 'decimal'
                else '—' if row.get(c['key']) is None else str(row[c['key']]) for c in cols]
    printed = [{'cells': cells(row)} for row in rows]
    if totals:
        sums = {key: total(rows, key) for key in totals}
        printed.append({'cells': [number(sums[c['key']], zec) if c['key'] in sums
                                 else 'TOTAL' if index == 0 else '' for index, c in enumerate(cols)], 'cls': 'total'})
    return {'t': 'table', 'cols': cols, 'rows': printed, 'raw': rows}


def model(spec, ctx, start, end, blocks, names, notes=()):
    title = spec['label']
    heading = [{'t': 'letterhead', 'left': names, 'right': f"{ctx['type']} · {ctx['an']}"},
               {'t': 'title', 'text': title, 'sub': [f'{start.isoformat()} — {end.isoformat()}']}]
    return {'title': title, 'report_name': spec['id'],
            'filename': re.sub(r'[^\w-]', '_', spec['id']) + '_' + end.isoformat(),
            'landscape': any(len(b.get('cols', [])) > 8 for b in blocks), 'font': 8,
            'footer': 'VERCON • valori afișate la precizia gestiunii',
            'blocks': heading + [{'t': 'text', 'text': note} for note in notes] + blocks,
            'context': {key: ctx[key] for key in ('subunit', 'gestiune', 'exercitiu', 'type', 'scale', 'zec')},
            'period': {'start': start.isoformat(), 'end': end.isoformat()}}
