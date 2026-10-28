"""An OI/MF lot keeps the use account of its original BP through every BT/BS.

Legacy TC accounts are ignored when resolving provenance. Distinct BP accounts
are never merged; missing, cyclic or ambiguous provenance is an explicit error.
No stored rows are changed by this reader.
"""
from collections import defaultdict
from decimal import Decimal

from .nomenclatoare import NomError


class Accounts:
    def __init__(self, rows, parse):
        self.parse, self.cache, self.active = parse, {}, set()
        self.receipts = defaultdict(list)
        for row in rows:
            if Decimal(row['Cant'] or 0) > 0 and (row['IDBP'] or row['IDBT']):
                document = row['IDBP'] or row['IDBT']
                self.receipts[(document, row['IdProdus'], row['PUTVA'])].append(row)

    def account(self, raw):
        position = raw['IDOI']
        if position in self.cache:
            return self.cache[position]
        if position in self.active:
            self.fail(raw, 'bonurile de origine se învârt în cerc')
        self.active.add(position)
        try:
            row = self.parse(raw)
            if row['family'] == 'bp' or not row['quantity'] and not row['IDBO']:
                account = row['IDM']
            else:
                sources = self.receipts.get((row['IDBO'], row['IdProdus'], row['PUTVA']), [])
                if not sources:
                    self.fail(raw, 'bonul de origine nu are în registru o linie de primire cu același produs și același preț '
                                   '(dacă la liniile de primire lipsește produsul, completați-l din «Reglare registru OI», '
                                   'butonul din pagina bonurilor de predare)')
                identities = {(self.parse(source)['family'], self.parse(source)['document']) for source in sources}
                if len(identities) != 1:
                    self.fail(raw, 'numărul bonului de origine există și ca bon de predare, și ca bon de transfer')
                accounts = {self.account(source) for source in sources}
                if len(accounts) != 1:
                    self.fail(raw, 'bonul de origine a primit același produs în conturi diferite, deci nu se poate alege contul fără să ghicim')
                account = accounts.pop()
            self.cache[position] = account
            return account
        finally:
            self.active.remove(position)

    @staticmethod
    def fail(row, reason):
        raise NomError('OI_ACCOUNT_ORIGIN', 'Nu se poate stabili contul obiectului din poziția %s (produsul %s, plecat din bonul %s): %s.' %
                       (row['IDOI'], row['IdProdus'] or 'necompletat', row['IDBO'] or 'necompletat', reason), 409)

    def normalized(self, row):
        return {**self.parse(row), 'stored_IDM': row['IDM'], 'IDM': self.account(row)}


def _num(value):
    """5.0000 -> 5, 184.9900 -> 184,99 (the way the operator reads it)."""
    text = format(Decimal(value or 0).normalize(), 'f')
    return text.replace('.', ',')


def _unresolved(cursor, ctx, family, row, lots, accounts):
    """The refusal sentence, in words the operator can check against the bon: the number printed on it, its date,
    the product and the holder by NAME, quantity and price, and what the register holds for the same bon and product."""
    name = holder = None
    if row['IdProdus']:
        cursor.execute('SELECT pr.DenumireP, pr.UM, pp.COD FROM VC_ProduseP pp LEFT JOIN VC_Produse pr ON pr.SubunitId=pp.SubunitId '
                       'AND pr.GestiuneId=pp.GestiuneId AND pr.ID=pp.ID WHERE pp.SubunitId=%s AND pp.GestiuneId=%s AND pp.IdProdus=%s',
                       (ctx['subunit'], ctx['gestiune'], row['IdProdus']))
        name = cursor.fetchone()
    if row['holder']:
        cursor.execute('SELECT Denumire FROM VC_Clienti WHERE SubunitId=%s AND GestiuneId=%s AND IDC=%s',
                       (ctx['subunit'], ctx['gestiune'], row['holder']))
        holder = cursor.fetchone()
    product = 'produs necompletat'
    if name:
        product = '«%s»%s' % (name['DenumireP'] or 'fără denumire',
                              ' (%s)' % ', '.join(x for x in (name['UM'], name['COD']) if x) if name['UM'] or name['COD'] else '')
    quantity = abs(Decimal(row['Cant'] or 0))
    in_register = lots.get((family, row['document'], row['holder'], row['IdProdus']), [])
    if not in_register:
        found = 'Registrul OI nu are nicio linie pentru acest bon, acest produs și acest gestionar.'
    else:
        found = 'În registrul OI, pentru același bon, produs și gestionar, sunt: %s.' % '; '.join(
            '%s x %s lei (contul %s)' % (_num(q), _num(price), account) for price, q, account in in_register)
    why = ('Nici o linie din registru, nici suma liniilor lui nu dă cantitatea de pe bon la același preț.' if not accounts
           else 'Registrul are mai multe linii cu aceeași cantitate și preț, în conturi diferite (%s).' % ', '.join(sorted(str(a) for a in accounts)))
    return NomError('OI_ACCOUNT_ORIGIN',
                    'Linia %s din bonul %s %s din %s: %s, %s x %s lei, gestionar %s. %s %s' % (
                        row['ID'], family.upper(), row['number'] or '(fără număr)',
                        row['day'].strftime('%d.%m.%Y') if row['day'] else '(fără dată)',
                        product, _num(quantity), _num(row['PUTVA']),
                        (holder or {}).get('Denumire') or row['holder'] or 'necompletat', why, found), 409)


def line_accounts(cursor, ctx):
    """Associate saved document lines with their inherited account, without a multiplying join."""
    from .oi_custody import register_rows
    received = defaultdict(set)
    # One bon line can be split in the register into several rows, one per source lot it took from (a BS line of 5
    # becomes 4 from lot A + 1 from lot B). `batch` keeps the accounts and the total quantity of those rows together.
    batch_accounts = defaultdict(set)
    batch_quantity = defaultdict(Decimal)
    lots = defaultdict(list)   # (family, document, holder, product) -> (price, quantity, account), only for the refusal text
    for row in register_rows(cursor, ctx):
        if row['family'] == 'bs' and row['quantity'] > 0 or row['family'] != 'bs' and row['quantity'] < 0:
            continue
        key = (row['family'], row['document'], row['IDC_PTC'], row['IdProdus'], row['PUTVA'], abs(row['quantity']))
        received[key].add(row['IDM'])
        batch = key[:5]
        batch_accounts[batch].add(row['IDM'])
        batch_quantity[batch] += abs(row['quantity'])
        lots[(row['family'], row['document'], row['IDC_PTC'], row['IdProdus'])].append((row['PUTVA'], abs(row['quantity']), row['IDM']))
    result = {}
    for family, table, document, holder, number, date in (
        ('bp', 'VC_OI_Predare', 'IDBP', 'IDC_BP', 'NRBP', 'DATABP'),
        ('bt', 'VC_OI_Transfer', 'IDBT', 'IDC_BT', 'NRBT', 'DATABT'),
        ('bs', 'VC_OI_Casare', 'IDBC', 'IDC_BC', 'NRBC', 'DATABC'),
    ):
        cursor.execute('SELECT ID,{document} AS document,{holder} AS holder,IdProdus,PUTVA,Cant,{number} AS number,{date} AS day '
                       'FROM {table} WHERE SubunitId=%s AND GestiuneId=%s AND ExercitiuId=%s AND Anulat=0'.format(
                           document=document, holder=holder, table=table, number=number, date=date),
                       (ctx['subunit'], ctx['gestiune'], ctx['exercitiu']))
        rows = cursor.fetchall()
        line_quantity = defaultdict(Decimal)
        for row in rows:
            line_quantity[(family, row['document'], row['holder'], row['IdProdus'], row['PUTVA'])] += abs(Decimal(row['Cant'] or 0))
        for row in rows:
            batch = (family, row['document'], row['holder'], row['IdProdus'], row['PUTVA'])
            accounts = received.get(batch + (abs(Decimal(row['Cant'] or 0)),), set())
            if not accounts and batch_quantity.get(batch) == line_quantity[batch]:
                accounts = batch_accounts[batch]   # the register split the line(s) into lots; the totals agree
            if len(accounts) != 1:
                raise _unresolved(cursor, ctx, family, row, lots, accounts)
            result[(family, row['ID'])] = next(iter(accounts))
    return result


def apply_lines(rows, accounts, document_rows=False):
    """Keep the configured report quantities/dates; only the use account is inherited."""
    result = []
    for raw in rows:
        if raw.get('annulled'):
            result.append(raw)
            continue
        family = raw.get('family') if document_rows else raw['kind'].split('_')[0]
        key = (family, raw['line_id'])
        if key not in accounts:
            raise NomError('OI_ACCOUNT_ORIGIN', 'Linia %s %s nu are un cont de proveniență rezolvat.' % key, 409)
        result.append({**raw, 'category': accounts[key]})
    return result
