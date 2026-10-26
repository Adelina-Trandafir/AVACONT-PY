"""ADE contracts. No connections or mutable global business state at import."""
import json
import math
from pathlib import Path

SCHEMA = json.loads(Path(__file__).with_name('schema.json').read_text(encoding='utf-8'))
EDITABLE = {
    'Grupe': 'Grupa',
    'Platitori': 'Nume CNP DataIntrare',
    'Platitori_sub': 'IDP Nume Adresa CNP_Platitor CUI Cont Banca Telefon EMail TrimiteMail Activ',
    'ValoriTaxe': 'TaxaZilnica Expl Activ DeLa',
    'Prezenta': 'ZilePrezenta',
}
EDITABLE = {table: fields.split() for table, fields in EDITABLE.items()}


class DomainError(ValueError):
    def __init__(self, reason, message, status=409, details=None):
        super().__init__(message)
        self.reason = reason
        self.status = status
        self.details = details   # optional structured data the page turns into links


def require(condition, reason, message, status=409, details=None):
    if not condition:
        raise DomainError(reason, message, status, details)


def cnp_code(value):
    """Port vercnp/UltimaCifra: preserve VBA bounds, without calendar reinterpretation."""
    if not isinstance(value, str) or len(value) != 13 or not value.isascii() or not value.isdigit():
        return 0
    for start, limit, code in ((3, 12, 2), (5, 31, 3), (7, 52, 4)):
        if int(value[start:start + 2]) > limit:
            return code
    check = sum(int(digit) * int(weight) for digit, weight in zip(value[:12], '279146358279')) % 11
    if check == 10:
        check = 1
    return -1 if int(value[-1]) == check else 5


def validate_cnp(value):
    # Empty CNP is allowed for existing records and corporate payers.
    if value in (None, ''):
        return
    code = cnp_code(value)
    messages = {0: 'CNP-ul trebuie să conțină 13 cifre.', 2: 'Luna din CNP este invalidă.',
                3: 'Ziua din CNP este invalidă.', 4: 'Codul județului din CNP este invalid.',
                5: 'Cifra de control a CNP-ului este invalidă.'}
    require(code == -1, 'CNP', messages.get(code, 'CNP invalid.'), 400)


def educators_at(rows, group_id, day):
    return ', '.join(dict.fromkeys(row['Educator'] for row in rows
        if row['IDG'] == group_id and row.get('Educator')
        and (not row.get('DeLa') or str(row['DeLa'])[:10] <= day)
        and (not row.get('PanaLa') or str(row['PanaLa'])[:10] >= day)))


def group_educators(group, rows, day):
    return educators_at(rows, group['IDG'], day) if any(row['IDG'] == group['IDG'] for row in rows) else group.get('Educator')


def receipt_number_text(number, sub=0):
    """A receipt number as printed: 751 or 751,1. The sub-number is kept in hundredths like VC_NIR.NumarSub (751,1 = 751 + sub 10)."""
    if number is None:
        return ''
    fraction = int(sub or 0)
    return f'{number},{fraction:02d}'.rstrip('0') if fraction else str(number)


def access_long(value):
    if value is None:
        return None
    require(isinstance(value, (int, float)) and math.isfinite(value), 'NUMBER', 'Valoare numerică invalidă.', 400)
    result = round(value)
    require(-2147483648 <= result <= 2147483647, 'OVERFLOW', 'Valoare în afara limitelor Access Long.', 400)
    return result


def nz(value):
    return 0 if value is None else value


def add(*values):
    return None if None in values else sum(values)


def sub(a, b):
    return None if a is None or b is None else a - b


def positive(value):
    return value is not None and value > 0


def compensation(row):
    value, sid, sic = row['ValoareContract'], row['SID'], row['SIC']
    paid = add(row['Plata'], row['Plati'])
    if positive(sid):
        return 0
    if positive(paid):
        difference = sub(value, paid)
        return 0 if difference is not None and difference < 0 else difference
    return value if sic is not None and value is not None and sic > value else sic


def update_situation(row, snapshot=False, finalize=True):
    """Preserve each Access assignment and conversion; reject ambiguous phase results."""
    result = dict(row)
    base = sub(row['SID'], row['SIC']) if snapshot else nz(row.get('SoldInitial'))
    if snapshot:
        balance = sub(add(base, row['ValoareContract']), sub(add(row['Plata'], row['Plati']), nz(row['Retur'])))
    else:
        balance = base + nz(row['ValoareContract']) - (nz(row['Plata']) + nz(row['Plati']) - nz(row['Retur']))
    debit = access_long(balance if positive(balance) else 0)
    credit = access_long(abs(balance) if balance is not None and balance < 0 else 0)
    initial = compensation(row)
    comp = initial
    if not snapshot and positive(initial):
        before = row['SIC'] if positive(row['SIC']) and positive(row.get('SFD')) else initial
        after = row['SIC'] if positive(row['SIC']) and positive(debit) else initial
        # The final Update_Compensare can remove the evaluation-order ambiguity.
        finals = {row['SIC'] if positive(debit) and positive(value) else value for value in (before, after)}
        require(len(finals) == 1, 'D04', 'Ordinea evaluării Access necesită o probă pentru acest rând.')
        comp = before
    anticipated = sub(sub(add(row['Plata'], row['Plati']), row['ValoareContract']), row['SID'])
    result.update(Compensare=comp, Anticipat=anticipated if positive(anticipated) else 0, SFD=debit, SFC=credit)
    if finalize and positive(debit) and positive(comp) and (not snapshot or positive(row['SIC'])):
        result['Compensare'] = row['SIC']
    return result


def finalize_compensation(row):
    """The separate Access Update_Compensare phase, after SFD has been stored."""
    result = dict(row)
    if positive(row.get('SFD')) and positive(row.get('Compensare')):
        result['Compensare'] = row.get('SIC')
    return result
