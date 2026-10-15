"""VC08-02: calendar depreciation of ONE MF variant. Exact decimal arithmetic.

The Access basis is ProduseP.PUTVA; ValoareLuna is the raw monthly amount and
ValoareLunara is its historical companion, not a second amount to accumulate.
DF is the duration already used at the first handover. A transfer never restarts it.
No database, clock, float, writes or automatic historical corrections.
"""
from datetime import date, datetime
from decimal import Decimal
from . import numeric


def calendar_months(start, end):
    """Access DateDiff('m', start, end): boundaries, not elapsed whole days."""
    return (end.year - start.year) * 12 + end.month - start.month


def as_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


def rounded_ratio(numerator, denominator):
    """Nonnegative rational -> integer, HALF_EVEN; no Decimal context limit."""
    quotient, remainder = divmod(numerator, denominator)
    if 2 * remainder > denominator or (2 * remainder == denominator and quotient % 2):
        quotient += 1
    return quotient


def monthly_amount(value, normal, raw, legacy, scale, zec):
    """Round once at calculation ZEC; amounts are unscaled decimals."""
    if raw is not None:
        return numeric.calc_round(raw, zec), 'ValoareLuna'
    if legacy is not None:
        return numeric.calc_round(legacy, zec), 'ValoareLunara'
    return numeric.calc_round(Decimal(value) / normal, zec), 'value_divided_by_DNF'


def accumulated(value, monthly, used, normal):
    # The final month absorbs the exact rest, including source decimals beyond ZEC.
    return value if used >= normal else min(value, used * monthly)


def calculate(asset, events, on_date, scale, zec):
    """Result with decimal amounts or explicit uncalculable state, never fabricated 0.

    events: {kind: receipt|storno|handover|transfer|scrapping, date, quantity}.
    A unique inventory variant must represent one unit. Multiple document lines
    are counted independently; ambiguous quantities/partial scrapping are diagnosed.
    """
    result = {'status': 'invalid', 'issues': [], 'valuation_date': on_date.isoformat(),
              'start_date': None, 'stop_date': None, 'acquisition_date': None, 'calendar_months': None,
              'used_months': None, 'remaining_months': None, 'value': asset.get('price'),
              'monthly': None, 'monthly_source': None, 'accumulated': None,
              'remaining': None, 'month_depreciation': None, 'next_month_depreciation': None,
              'written_off_remaining': None, 'receipt_lines': 0, 'handover_lines': 0,
              'transfer_lines': 0}

    def issue(code, message):
        result['issues'].append({'code': code, 'message': message})

    if not isinstance(scale, int) or not isinstance(zec, int) or not 0 <= zec <= scale <= 4:
        issue('PRECISION_INVALID', 'Scara și zecimalele gestiunii nu sunt valide.')
        return result
    if asset.get('duplicate_rows'):
        issue('MF_DUPLICATE_REGISTER', 'Varianta are mai multe rânduri în registrul MF; calculul nu le adună.')
        return result
    value, normal, used_before = asset.get('price'), asset.get('DNF'), asset.get('DF')
    if not isinstance(value, (int, Decimal)) or isinstance(value, bool) or value < 0:
        issue('MF_VALUE_INVALID', 'Valoarea variantei lipsește sau este negativă.')
    if not isinstance(normal, int) or normal <= 0:
        issue('MF_DURATION_INVALID', 'Durata normală lipsește sau este zero/negativă.')
    # A NULL DF is not silently interpreted as 0 in historical imported data.
    if not isinstance(used_before, int) or used_before < 0 or isinstance(normal, int) and used_before > normal:
        issue('MF_DF_INVALID', 'DF lipsește, este negativ sau depășește durata normală.')
    for field in ('ValoareLuna', 'ValoareLunara'):
        raw = asset.get(field)
        if raw is not None and (not isinstance(raw, (int, Decimal)) or isinstance(raw, bool) or raw < 0):
            issue('MF_MONTHLY_INVALID', '%s este negativă sau invalidă.' % field)
    entry = as_date(asset.get('DataIntrare'))
    if entry is None:
        issue('MF_ENTRY_MISSING', 'Data intrării lipsește.')
    if result['issues']:
        return result
    if on_date < entry:
        result['status'] = 'not_entered'
        return result

    dated = []
    for event in events:
        day = as_date(event.get('date'))
        if day is None:
            issue('MF_DOCUMENT_DATE_MISSING', 'Un document al activului nu are dată validă.')
            continue
        quantity = event.get('quantity')
        if not isinstance(quantity, (int, Decimal)) or isinstance(quantity, bool) or quantity < 0:
            issue('MF_DOCUMENT_QUANTITY_INVALID', 'Un document al activului are cantitate invalidă.')
            continue
        dated.append({**event, 'date': day})
    if result['issues']:
        return result
    dated.sort(key=lambda event: (event['date'], event['kind']))
    receipts = [event for event in dated if event['kind'] == 'receipt' and event['date'] <= on_date]
    handovers = [event for event in dated if event['kind'] == 'handover' and event['quantity'] > 0]
    result['receipt_lines'] = len(receipts)
    result['acquisition_date'] = receipts[0]['date'].isoformat() if receipts else None
    result['handover_lines'] = sum(event['date'] <= on_date for event in handovers)
    result['transfer_lines'] = sum(event['kind'] == 'transfer' and event['date'] <= on_date for event in dated)
    quantity = sum(event['quantity'] for event in receipts) - sum(
        event['quantity'] for event in dated if event['kind'] == 'storno' and event['date'] <= on_date)
    if not receipts:
        issue('MF_RECEIPT_MISSING', 'Activul nu are recepție neanulată la data calculului.')
        return result
    if quantity != 1:
        issue('MF_QUANTITY_AMBIGUOUS', 'Varianta nu reprezintă o singură bucată la data calculului. Nu se repartizează arbitrar valoarea amortizării.')
        return result
    if not handovers or handovers[0]['date'] > on_date:
        result['status'] = 'not_in_use'
        return result
    start = handovers[0]['date']
    if start < entry:
        issue('MF_HANDOVER_BEFORE_ENTRY', 'Prima predare este înaintea datei intrării. Corectați datele activului.')
        return result
    result['start_date'] = start.isoformat()
    stop, disposed = None, 0
    for event in dated:
        if event['kind'] != 'scrapping' or event['date'] > on_date or event['quantity'] == 0:
            continue
        if event['date'] < start:
            issue('MF_SCRAPPING_BEFORE_HANDOVER', 'Casarea este înaintea primei predări.')
            return result
        disposed += event['quantity']
        if disposed > quantity:
            issue('MF_SCRAPPING_EXCESS', 'Cantitatea casată depășește cantitatea activului.')
            return result
        if disposed == quantity and stop is None:
            stop = event['date']
    if disposed and disposed < quantity:
        issue('MF_PARTIAL_SCRAPPING', 'Casarea parțială a unui număr de inventar necesită repartizarea explicită a valorii.')
        return result
    monthly, monthly_source = monthly_amount(value, normal, asset.get('ValoareLuna'),
                                              asset.get('ValoareLunara'), scale, zec)
    if abs(monthly * normal - value) * 2 > normal * Decimal(1).scaleb(-zec):
        issue('MF_MONTHLY_BASIS_DIFFERENCE', 'Valoarea lunară stocată nu corespunde valorii curente împărțite la DNF. Se păstrează valoarea lunară sursă, cu restul în ultima lună.')
    if asset.get('ValoareLuna') is not None and asset.get('ValoareLunara') is not None:
        if asset['ValoareLuna'] != asset['ValoareLunara']:
            issue('MF_MONTHLY_SOURCE_DIFFERENCE', 'ValoareLuna și ValoareLunara diferă; calculul folosește ValoareLuna, fără rescrierea sursei.')
    effective = min(on_date, stop) if stop else on_date
    elapsed = calendar_months(start, effective)
    used = min(normal, used_before + elapsed)
    total = accumulated(value, monthly, used, normal)
    prior_used = min(normal, used_before + max(0, elapsed - 1))
    this_month = total - accumulated(value, monthly, prior_used, normal) if elapsed > 0 else 0
    next_month = accumulated(value, monthly, min(normal, used + 1), normal) - total
    result.update(status='written_off' if stop else 'fully_depreciated' if used == normal else 'active',
                  stop_date=stop.isoformat() if stop else None, calendar_months=elapsed,
                  used_months=used, remaining_months=normal - used, monthly=monthly, monthly_source=monthly_source,
                  accumulated=total, remaining=value - total,
                  month_depreciation=this_month if not stop or calendar_months(stop, on_date) == 0 else 0,
                  next_month_depreciation=0 if stop else next_month,
                  written_off_remaining=value - total if stop else None)
    return result
