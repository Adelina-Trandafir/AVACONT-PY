"""SLICE-VC06-02..05: what every document write shares -- the exercise of the context, the period rule, the numbering,
the RequestKey, the audit line and the stock timeline that keeps the stock from ever going negative.

Everything here runs INSIDE the transaction the route opened (one cursor, committed or rolled back by the route), with
ids taken from the context the server verified. Nothing here opens a connection or commits.
"""
import hashlib
import json
from datetime import date, datetime

from .context import HISTORY_TYPES
from .nomenclatoare import NomError

_PERIOD = 'Perioada este închisă: documentele nu se mai pot modifica.'


# ---- exercise and period -----------------------------------------------------------------------------------------
def exercise_of(cursor, sub, gest, gest_type, year):
    """The exercise a document of this context belongs to. Materiale/Alimente: the chosen year. OI/MF keep ONE multi-year
    history (the year is only a filter): their single exercise."""
    if gest_type in HISTORY_TYPES:
        cursor.execute('SELECT ExercitiuId, An, Closed FROM VC_Exercitii WHERE SubunitId=%s AND GestiuneId=%s ORDER BY An LIMIT 1', (sub, gest))
    else:
        if not isinstance(year, int):
            raise NomError('NO_EXERCISE', 'Alegeți anul exercițiului.', 409)
        cursor.execute('SELECT ExercitiuId, An, Closed FROM VC_Exercitii WHERE SubunitId=%s AND GestiuneId=%s AND An=%s', (sub, gest, year))
    row = cursor.fetchone()
    if not row:
        raise NomError('NO_EXERCISE', 'Exercițiul ales nu există pentru această gestiune.', 409)
    return {'id': row['ExercitiuId'], 'an': int(row['An']), 'closed': bool(row['Closed'])}


def ensure_open(exercise):
    if exercise['closed']:
        raise NomError('PERIOD_CLOSED', _PERIOD, 409)


def to_date(raw, label, exercise=None, errors=None, key=None):
    """ISO date text -> date. Collects the message under `key` when `errors` is given, otherwise raises."""
    message = None
    value = None
    try:
        value = datetime.strptime(str(raw or '').strip()[:10], '%Y-%m-%d').date()
    except ValueError:
        message = '%s: introduceți o dată validă.' % label
    if value is not None and exercise is not None and value.year != exercise['an']:
        message = '%s trebuie să fie în anul exercițiului (%d).' % (label, exercise['an'])
    if message:
        if errors is None:
            raise NomError('INVALID', message, 422)
        errors[key or label] = message
        return None
    return value


# ---- idempotency ---------------------------------------------------------------------------------------------------
def fingerprint(*parts):
    return hashlib.sha256(json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str).encode('utf-8')).hexdigest()


def replayed(cursor, ctx, request_key, fp):
    """The stored answer of a RequestKey already applied (same body), else None. A key with another body is refused."""
    if not request_key:
        return None
    cursor.execute('SELECT Fingerprint, Result FROM VC_Operations WHERE SubunitId=%s AND GestiuneId=%s AND RequestKey=%s FOR UPDATE',
                   (ctx['subunit'], ctx['gestiune'], request_key))
    seen = cursor.fetchone()
    if not seen:
        return None
    if seen['Fingerprint'] != fp:
        raise NomError('KEY_REUSED', 'Cheia cererii a fost folosită pentru altă cerere.', 409)
    return json.loads(seen['Result'])


def remember(cursor, ctx, request_key, fp, result):
    if request_key:
        cursor.execute('INSERT INTO VC_Operations (SubunitId, GestiuneId, RequestKey, Fingerprint, Result) VALUES (%s,%s,%s,%s,%s)',
                       (ctx['subunit'], ctx['gestiune'], request_key, fp, json.dumps(result, ensure_ascii=False, default=str)))


# ---- audit ---------------------------------------------------------------------------------------------------------
def audit(cursor, ctx, operation, table, row_key, before, after, request_key=None, request_id=None):
    def dump(value):
        return None if value is None else json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)
    cursor.execute(
        'INSERT INTO VC_Audit (SubunitId, GestiuneId, ExercitiuId, UserEmail, Operation, TableName, RowKey, BeforeValue, AfterValue, RequestKey, RequestId) '
        'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
        (ctx['subunit'], ctx['gestiune'], ctx.get('exercitiu'), ctx['email'], operation, table, str(row_key),
         dump(before), dump(after), request_key, request_id))


def freeze(cursor, ctx):
    """The first document fixes the scale and the type of the gestiune (values are direct DECIMAL(20,4))."""
    cursor.execute('UPDATE VC_Gestiuni SET Populated=1 WHERE SubunitId=%s AND GestiuneId=%s AND Populated=0', (ctx['subunit'], ctx['gestiune']))


# ---- numbering -----------------------------------------------------------------------------------------------------
def next_number(cursor, ctx, kind, taken):
    """The next free number of a document kind in this exercise: the counter under a locking read, then the first number
    that is really free (`taken(n)` -> True when a document already uses it). The counter moves in the same transaction."""
    cursor.execute('INSERT INTO VC_Numbering (SubunitId, GestiuneId, ExercitiuId, Kind, LastNumber) VALUES (%s,%s,%s,%s,0) '
                   'ON DUPLICATE KEY UPDATE Kind=Kind', (ctx['subunit'], ctx['gestiune'], ctx['exercitiu'], kind))
    cursor.execute('SELECT LastNumber FROM VC_Numbering WHERE SubunitId=%s AND GestiuneId=%s AND ExercitiuId=%s AND Kind=%s FOR UPDATE',
                   (ctx['subunit'], ctx['gestiune'], ctx['exercitiu'], kind))
    number = int(cursor.fetchone()['LastNumber']) + 1
    while taken(number):
        number += 1
    cursor.execute('UPDATE VC_Numbering SET LastNumber=%s, Version=Version+1 WHERE SubunitId=%s AND GestiuneId=%s AND ExercitiuId=%s AND Kind=%s',
                   (number, ctx['subunit'], ctx['gestiune'], ctx['exercitiu'], kind))
    return number


def raise_counter(cursor, ctx, kind, number):
    """A manual number above the counter moves the counter, so the next automatic number follows it."""
    cursor.execute('INSERT INTO VC_Numbering (SubunitId, GestiuneId, ExercitiuId, Kind, LastNumber) VALUES (%s,%s,%s,%s,%s) '
                   'ON DUPLICATE KEY UPDATE LastNumber=GREATEST(LastNumber, VALUES(LastNumber)), Version=Version+1',
                   (ctx['subunit'], ctx['gestiune'], ctx['exercitiu'], kind, number))


# ---- the stock timeline ----------------------------------------------------------------------------------------------
# Every movement of a variant, signed, with the date it counts from. Cancelled documents do not count. Receipts count
# from the invoice date (DataFc, as the Access query did), returns and consumption from the bon date, a storno from
# the date of the storno document. Stock never goes negative: the check runs inside the transaction of every write.
def movements(cursor, ctx, products=None, exclude=None):
    """[(product, date, signed quantity)]. `exclude` = ('bc'|'br'|'nir'|'storno', id): a document left out of the sum (the one
    being edited: its draft is validated separately and must not count twice)."""
    from .stock_source import warehouse
    # Preserve the existing protocol API (empty products means all); new read services
    # can explicitly select no variants without accidentally reading the entire ledger.
    return [(row['p'], row['d'], row['q']) for row in warehouse(cursor, ctx, products or None, exclude)]


def available(cursor, ctx, on_date, products=None, exclude=None):
    """{variant: stock on that date} -- informative; the binding check is `check_timeline`."""
    out = {}
    for product, day, quantity in movements(cursor, ctx, products, exclude):
        if day is not None and day <= on_date:
            out[product] = out.get(product, 0) + quantity
    return out


def check_timeline(cursor, ctx, products):
    """Raises STOCK_NEGATIVE when, for any of the variants, the running stock drops below zero on any day (additions of a
    day are counted before the removals of the same day). Cheap: only the variants a write touched."""
    products = {int(p) for p in products if p is not None}
    if not products:
        return
    per = {}
    for product, day, quantity in movements(cursor, ctx, products):
        per.setdefault(product, []).append((day or date.min, quantity))
    for product, items in per.items():
        balance = 0
        for day, group in _by_day(items):
            balance += group
            if balance < 0:
                raise NomError('STOCK_NEGATIVE', 'Stoc negativ interzis: produsul %s ar ajunge la %s la data %s.' % (
                    _label(cursor, ctx, product), _plain(balance, ctx['scale']), day.strftime('%d.%m.%Y')), 409,
                    {'product': product, 'date': day.isoformat()})


def _by_day(items):
    """[(day, net)] with the additions of a day before its removals, so a day never fails on its own ordering."""
    days = {}
    for day, quantity in items:
        entry = days.setdefault(day, [0, 0])
        entry[0 if quantity > 0 else 1] += quantity
    out = []
    running_names = sorted(days)
    for day in running_names:
        adds, removes = days[day]
        out.append((day, adds))        # additions first ...
        out.append((day, removes))     # ... then removals
    return out


def _label(cursor, ctx, variant):
    cursor.execute('SELECT pr.DenumireP AS n FROM VC_ProduseP p JOIN VC_Produse pr ON pr.SubunitId=p.SubunitId AND pr.GestiuneId=p.GestiuneId AND pr.ID=p.ID '
                   'WHERE p.SubunitId=%s AND p.GestiuneId=%s AND p.IdProdus=%s', (ctx['subunit'], ctx['gestiune'], variant))
    row = cursor.fetchone()
    return '«%s»' % (row['n'] if row and row['n'] else variant)


def _plain(stored, scale):
    from . import numeric
    return format(numeric.from_stored(stored, scale), 'f')
