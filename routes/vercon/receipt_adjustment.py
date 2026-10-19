"""Quantity that can change price without removing stock already issued."""
from datetime import date
from decimal import Decimal

from . import protocol


def annotate(cursor, ctx, lines, on):
    on = on or date.min
    variants = {line['variant'] for line in lines if line.get('variant') is not None}
    per = {variant: [] for variant in variants}
    if variants:
        for variant, day, quantity in protocol.movements(cursor, ctx, variants):
            per[variant].append((day or date.min, quantity))
    margins = {}
    for variant, movements in per.items():
        balance, minimum = Decimal(0), None
        # Aggregate a day before measuring: its receipts cannot hide its exits.
        days = {}
        for day, quantity in movements:
            days[day] = days.get(day, Decimal(0)) + quantity
        for day in sorted(days):
            if day >= on and minimum is None:
                minimum = balance + days[day]
            balance += days[day]
            if day >= on:
                minimum = min(minimum, balance)
        margins[variant] = max(Decimal(0), balance if minimum is None else minimum)
    for line in lines:
        available = min(Decimal(line.get('Cant') or 0), margins.get(line.get('variant'), Decimal(0)))
        if line.get('has_warehouse_exit'):
            available = Decimal(0)
        line['adjustable_quantity'] = format(available, 'f')


def prepare(parsed, stored):
    """Use only server-derived availability; a client cannot unlock a saved row."""
    for line in parsed['lines']:
        old = stored.get(line.get('id'))
        line['adjustable_quantity'] = (Decimal(old.get('adjustable_quantity') or 0)
                                       if old else line['v']['Cant'])


def eligible(line):
    return (not line.get('has_warehouse_exit') and not line.get('calculation')
            and line['v']['Cant'] >= 1 and line.get('rate') is not None
            and line.get('adjustable_quantity', line['v']['Cant']) >= 1)


def unavailable_message(lines):
    details = ['Niciun produs nu are cel puțin 1 unitate disponibilă pentru ajustare, fără consum sau predare.']
    for index, line in enumerate(lines, 1):
        quantity = line['v']['Cant'] or Decimal(0)
        available = max(Decimal(0), min(quantity, line.get('adjustable_quantity', quantity)))
        if line.get('has_warehouse_exit'):
            available = Decimal(0)
            reason = 'produsul are ieșire/predare și este protejat'
        elif line.get('calculation'):
            reason = 'rândul are deja o corecție automată'
        elif quantity < 1:
            reason = 'cantitatea rândului este mai mică de 1'
        elif available < 1:
            reason = 'stocul rămas după ieșiri/consumuri/predări este sub 1 pe parcursul mișcărilor'
        else:
            reason = 'cota TVA lipsește; completați datele produsului'
        details.append('• %s: cantitate %s, disponibilă pentru ajustare %s; %s.' % (
            line.get('name') or 'Rând %s' % index, format(quantity, 'f'), format(available, 'f'), reason))
    return '\n'.join(details)
