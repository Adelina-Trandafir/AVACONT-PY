"""Shared receipt arithmetic, chronology and read-only draft simulation."""
from copy import deepcopy
from datetime import date
from decimal import Decimal

from . import numeric, protocol
from .nomenclatoare import NomError

ACCEPTED_DIFFERENCE = Decimal(1)


def chronology(cursor, ctx, number, sub, day, exclude=None):
    from .documents import _all, _sub_text
    rows = _all(cursor, 'SELECT IdIntrare, ABS(Numar) AS Numar, COALESCE(NumarSub,0) AS sub, DATE(Data) AS day, COALESCE(Storno,0) AS storno '
                'FROM VC_NIR WHERE SubunitId=%s AND GestiuneId=%s AND YEAR(Data)=%s '
                "AND Numar<>0 AND TipDoc<>'STOC_INITIAL' AND IdIntrare<>%s",
                (ctx['subunit'], ctx['gestiune'], day.year, exclude or 0))
    pair = (number, sub)
    earlier = [r for r in rows if (r['Numar'], r['sub']) < pair and r['day']]
    later = [r for r in rows if (r['Numar'], r['sub']) > pair and r['day']]
    low = max(earlier, key=lambda r: r['day']) if earlier else None
    high = min(later, key=lambda r: r['day']) if later else None
    if (low and day < low['day']) or (high and day > high['day']):
        def describe(row):
            return '%s (%s %s%s)' % (row['day'].strftime('%d.%m.%Y'), 'FCS' if row['storno'] else 'NIR', row['Numar'], _sub_text(row['sub']))
        message = 'Data NIR/FCS trebuie să fie între %s și %s.' % (
            describe(low) if low else 'începutul anului', describe(high) if high else 'sfârșitul anului')
        raise NomError('NIR_CHRONOLOGY', message, 422, {'h.Data': message})
    return {'minimum': low['day'].isoformat() if low else None,
            'maximum': high['day'].isoformat() if high else None}


def gross_values(quantity, gross, rate, zec):
    """Gross is authoritative. The inverse VAT calculation survives future saves."""
    quantity, gross, rate = Decimal(quantity), Decimal(gross), Decimal(rate)
    if quantity <= 0 or gross < 0 or rate < 0:
        raise NomError('INVALID', 'Cantitatea, valoarea corectată sau cota TVA este invalidă.', 422)
    gross = numeric.to_stored(gross)
    unit = numeric.calc_round(gross / quantity, 4)
    if numeric.calc_round(unit * quantity, zec) != gross:
        raise NomError('ADJUST_PRECISION', 'Corecția nu poate fi reprezentată la precizia acestei cantități.', 422)
    net = numeric.calc_round(gross / (1 + rate), zec)
    return {key: numeric.to_stored(value) for key, value in {
        'Cant': quantity, 'Pret': numeric.calc_round(net / quantity, 4),
        'PUTVA': unit, 'Valoare': net, 'TVA': gross - net, 'ValoareCUTVA': gross}.items()}


def adjust(parsed, ctx, key=None):
    """Split at most one unit. Retain quantity, product and the real VAT rate."""
    from .documents import receipt_line
    from .receipt_adjustment import eligible, unavailable_message
    target = parsed['declared']
    if target is None:
        raise NomError('INVALID', 'Introduceți valoarea aferentă acestui NIR înainte de corecție.', 422,
                       {'h.Valoare': 'Valoarea țintă este obligatorie.'})
    original = sum((r['v']['ValoareCUTVA'] for r in parsed['lines']), Decimal(0))
    delta = target - original
    if not delta:
        return parsed
    if any(line.get('calculation') for line in parsed['lines']):
        raise NomError('AUTO_ROW_LOCKED', 'Ștergeți corecția existentă cu revenire în grup înainte de o nouă ajustare.', 422)
    if abs(delta) > Decimal(1):
        raise NomError('ADJUST_LIMIT', 'Diferența de %s lei depășește limita de 1 leu (valoare NIR %s lei, total produse %s lei). Corectați produsele sau repartizarea facturii.' % (
            format(delta, 'f'), format(target, 'f'), format(original, 'f')), 422)
    result = deepcopy(parsed)
    candidates = [r for r in result['lines'] if eligible(r)]
    candidates.sort(key=lambda r: r['key'] != str(key))
    if not candidates:
        raise NomError('ADJUST_LINE', unavailable_message(result['lines']), 422)
    for chosen in candidates:
        quantity = chosen['v']['Cant']
        fragment = Decimal(1)
        rest = quantity - fragment
        remaining = receipt_line(rest, chosen['v']['Pret'], chosen['rate'], ctx['zec'], ctx['scale']) if rest else None
        if remaining and chosen.get('preserve'):
            # The consumed part keeps the original price variant, including historical prices.
            remaining['PUTVA'] = chosen['v']['PUTVA']
            remaining['ValoareCUTVA'] = numeric.to_stored(numeric.calc_round(rest * remaining['PUTVA'], ctx['zec']))
            remaining['TVA'] = remaining['ValoareCUTVA'] - remaining['Valoare']
        others = sum((r['v']['ValoareCUTVA'] for r in result['lines'] if r is not chosen), Decimal(0))
        gross = target - others - (remaining['ValoareCUTVA'] if remaining else 0)
        if gross < 0:
            continue
        corrected = deepcopy(chosen)
        corrected.pop('preserve', None)
        corrected['v'] = gross_values(fragment, gross, chosen['rate'], ctx['zec'])
        corrected['calculation'] = {'mode': 'GROSS', 'gross': gross, 'input_price': chosen['v']['Pret'], 'origin': {
            'parent_key': chosen['key'], 'parent_id': chosen.get('id'), 'split': bool(rest),
            'product': chosen.get('product'), 'variant': chosen.get('variant'), 'IDTVA': chosen['IDTVA'],
            'Pret': format(chosen['v']['Pret'], 'f'), 'rate': format(chosen['rate'], 'f')}}
        if rest:
            if ctx['type'] == 'MF':
                raise NomError('MF_QUANTITY', 'Un activ MF trebuie să aibă cantitatea 1.', 422)
            chosen['v'] = remaining
            chosen.pop('preserve', None)
            corrected.update({'id': None, 'key': chosen['key'] + '-adjust', 'variant': None})
            result['lines'].insert(result['lines'].index(chosen) + 1, corrected)
        else:
            chosen.pop('preserve', None)
            chosen.update(corrected)
        result['adjustment'] = {'difference': format(delta, 'f'), 'line': corrected['key'], 'vat': format(chosen['rate'], 'f')}
        return result
    raise NomError('ADJUST_LINE', 'Niciun rând eligibil nu poate prelua această corecție fără valoare negativă.', 422)


def exact_total(parsed):
    if any(line['v']['ValoareCUTVA'] is None for line in parsed['lines']):
        raise NomError('HISTORICAL_VALUES', 'Documentul are valori istorice lipsă. Corectați explicit liniile înainte de o modificare economică.', 422)
    total = sum((line['v']['ValoareCUTVA'] for line in parsed['lines']), Decimal(0))
    target = parsed['declared']
    # The operator may knowingly save a difference of at most 1 leu; the invoice keeps the declared value.
    if target is not None and target != total and not (parsed.get('accept_difference') and abs(target - total) <= ACCEPTED_DIFFERENCE):
        message = 'Totalul liniilor (%s) diferă de valoarea NIR (%s) cu %s lei. Aplicați corecția sau corectați repartizarea.' % (
            format(total, 'f'), format(target, 'f'), format(target - total, 'f'))
        raise NomError('NIR_TOTAL_DIFFERENT', message, 422, {'h.Valoare': message})
    try:
        return numeric.to_stored(total)
    except numeric.NumericError as error:
        raise NomError('INVALID', str(error), 422, {'h.Valoare': str(error)}) from None


def simulate(cursor, repo, ctx, parsed, exclude=None):
    """Check stock-reducing variants only; return their identities for the save check.

    A positive addition cannot cause a shortage, including in an imperfect imported
    history. No writes, including price variants: new ones get synthetic identities.
    """
    from .documents import _all, _scope
    old = _all(cursor, 'SELECT i.IdProdus, i.Cant, DATE(COALESCE(n.DataFc,n.Data)) AS day '
               'FROM VC_Intrari i JOIN VC_NIR n ON n.SubunitId=i.SubunitId AND n.GestiuneId=i.GestiuneId '
               'AND n.ExercitiuId=i.ExercitiuId AND n.IdIntrare=i.IdIntrare '
               'WHERE i.SubunitId=%s AND i.GestiuneId=%s AND i.ExercitiuId=%s AND i.IdIntrare=%s',
               (*_scope(ctx), exclude)) if exclude else []
    drafts, synthetic = [], {}
    on = parsed['cols']['DataFc'] or parsed['cols']['Data']
    for line in parsed['lines']:
        variant = line.get('variant')
        if ctx['type'] != 'MF' and not line.get('preserve'):
            matches = repo.select('VC_ProduseP', {'ID': line.get('product'), 'PUTVA': line['v']['PUTVA'], 'Activ': 1}, columns=('IdProdus',))
            ids = {r['IdProdus'] for r in matches}
            variant = variant if variant in ids else min(ids) if ids else None
        if variant is None:
            identity = (line.get('product'), line['v']['PUTVA'], line['key'] if ctx['type'] == 'MF' else None)
            variant = synthetic.setdefault(identity, -len(synthetic) - 1)
        drafts.append((variant, on, line['v']['Cant'] or Decimal(0)))
    changes = {}
    for row in old:
        changes.setdefault(row['IdProdus'], []).append((row['day'] or date.min, -Decimal(row['Cant'])))
    for variant, day, quantity in drafts:
        changes.setdefault(variant, []).append((day or date.min, quantity))
    products = set()
    for variant, items in changes.items():
        difference = Decimal(0)
        for day, amount in protocol._by_day(items):
            difference += amount
            if difference < 0:
                products.add(variant)
                break
    if not products:
        return products
    existing = protocol.movements(cursor, ctx, {p for p in products if p > 0}, ('nir', exclude) if exclude else None) if any(p > 0 for p in products) else []
    per = {}
    for variant, day, quantity in existing + drafts:
        if variant in products:
            per.setdefault(variant, []).append((day or date.min, quantity))
    for variant, items in per.items():
        balance = Decimal(0)
        for day, amount in protocol._by_day(items):
            balance += amount
            if balance < 0:
                label = protocol._label(cursor, ctx, variant) if variant > 0 else 'produsul din ciornă'
                raise NomError('STOCK_NEGATIVE', '%s: la %s ar lipsi %s. Modificarea nu poate fi aplicată.' % (
                    label, day.strftime('%d.%m.%Y'), format(-balance, 'f')), 409,
                    {'product': variant, 'date': day.isoformat(), 'missing': format(-balance, 'f')})
    return products


def serialize(parsed, ctx=None, doc_id=None):
    rows = []
    for line in parsed['lines']:
        row = {'key': line['key'], 'id': line.get('id'), 'product': line.get('product'), 'variant': line.get('variant'),
               'IDTVA': line['IDTVA'], 'invoice_line': line.get('invoice_line')}
        if line.get('has_warehouse_exit'):
            row['has_warehouse_exit'] = True
        if 'adjustable_quantity' in line:
            row['adjustable_quantity'] = format(line['adjustable_quantity'], 'f')
        row.update({k: None if v is None else format(v, 'f') for k, v in line['v'].items()})
        if line.get('calculation'):
            row.update({'calc_mode': 'GROSS', 'gross_total': format(line['v']['ValoareCUTVA'], 'f'),
                        'input_price': format(line['calculation']['input_price'], 'f')})
            row['auto_origin'] = line['calculation'].get('origin')
        for dest, src in (('code', 'COD'), ('data_intrarii', 'data_intrarii'), ('DNF', 'DNF'), ('DF', 'DF')):
            if src in line:
                value = line[src]
                row[dest] = value.isoformat() if hasattr(value, 'isoformat') else value
        if line.get('new_product'):
            row['new_product'] = line['new_product']
        if line.get('DNF'):
            from .receipt_products import monthly
            value = line.get('ValoareLuna') if line.get('preserve') else monthly(line)
            row['ValoareLuna'] = None if value is None else format(value, '.4f')
        if row.get('calc_mode') == 'GROSS' and ctx:
            from .receipt_auto import sign_row
            row['auto_token'] = sign_row(ctx, doc_id, row)
        rows.append(row)
    return {'lines': rows, 'total': format(sum((r['v']['ValoareCUTVA'] or Decimal(0) for r in parsed['lines']), Decimal(0)), 'f'),
            'declared': None if parsed['declared'] is None else format(parsed['declared'], 'f'),
            'adjustment': parsed.get('adjustment'), 'undo_token': parsed.get('undo_token')}
