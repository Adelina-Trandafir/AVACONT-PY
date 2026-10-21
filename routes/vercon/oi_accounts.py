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


def line_accounts(cursor, ctx):
    """Associate saved document lines with their inherited account, without a multiplying join."""
    from .oi_custody import register_rows
    received = defaultdict(set)
    for row in register_rows(cursor, ctx):
        if row['family'] == 'bs' and row['quantity'] > 0 or row['family'] != 'bs' and row['quantity'] < 0:
            continue
        key = (row['family'], row['document'], row['IDC_PTC'], row['IdProdus'], row['PUTVA'], abs(row['quantity']))
        received[key].add(row['IDM'])
    result = {}
    for family, table, document, holder in (
        ('bp', 'VC_OI_Predare', 'IDBP', 'IDC_BP'),
        ('bt', 'VC_OI_Transfer', 'IDBT', 'IDC_BT'),
        ('bs', 'VC_OI_Casare', 'IDBC', 'IDC_BC'),
    ):
        cursor.execute('SELECT ID,{document} AS document,{holder} AS holder,IdProdus,PUTVA,Cant '
                       'FROM {table} WHERE SubunitId=%s AND GestiuneId=%s AND ExercitiuId=%s AND Anulat=0'.format(
                           document=document, holder=holder, table=table),
                       (ctx['subunit'], ctx['gestiune'], ctx['exercitiu']))
        for row in cursor.fetchall():
            key = (family, row['document'], row['holder'], row['IdProdus'], row['PUTVA'], abs(Decimal(row['Cant'] or 0)))
            accounts = received.get(key, set())
            if len(accounts) != 1:
                raise NomError('OI_ACCOUNT_ORIGIN', 'Contul liniei %s din bonul %s %s nu are o proveniență univocă în registru.' %
                               (row['ID'], family.upper(), row['document']), 409)
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
