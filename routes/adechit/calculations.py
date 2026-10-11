"""SLICE-ADE7: isolated translation of the 2021 query phases, with trace output."""
from copy import deepcopy
from calendar import monthrange
from .domain import access_long, nz, require, update_situation, finalize_compensation, group_educators


def active(row, flag):
    # Access '=False' excludes NULL as well as True.
    return row.get(flag) is not None and row[flag] == 0


def total(rows, field):
    values = [r[field] for r in rows if r.get(field) is not None]
    return sum(values) if values else None


def calculate(data, month_id, group_id=None):
    people = {r['IDP']: r for r in data['Platitori']}
    groups = {r['IDG']: r for r in data['Grupe']}
    months = {r['IDL']: r for r in data['LunaD']}
    payments = [r for r in data['Plati'] if active(r, 'Anulata')]
    # Legacy refunds may carry no IDL: the month of their attendance row is theirs.
    month_of_attendance = {r['IDZ']: r['IDL'] for r in data['Prezenta']}
    refunds = [{**r, 'IDL': month_of_attendance.get(r.get('IDZ'))} if r.get('IDL') is None else r
               for r in data['Retur'] if active(r, 'Anulat')]
    month = months[month_id]
    rows, trace = [], {}
    for attendance in data['Prezenta']:
        if attendance['IDL'] != month_id or (group_id is not None and attendance['IDG'] != group_id):
            continue
        person, group = people.get(attendance['IDP']), groups.get(attendance['IDG'])
        if person is None or group is None:
            continue  # INNER JOIN, also reported by the migration validator.
        current = [r for r in payments if r['IDZ'] == attendance['IDZ']]
        returned = [r for r in refunds if r['IDZ'] == attendance['IDZ']]
        require(all((r['IDP'], r['IDL']) == (attendance['IDP'], month_id) for r in returned),
                'RETUR_JOIN', 'Restituiri cu legături discordante: reconcilierea importului este necesară.')
        # A missing LEFT JOIN row contributes IIf(NULL, amount, 0) = 0.
        paid = total([{'v': r.get('Plata') if r.get('TIP') == 2 else 0} for r in current] or [{'v': 0}], 'v')
        other = total([{'v': r.get('Plata') if r.get('TIP') is not None and r['TIP'] != 2 else 0} for r in current] or [{'v': 0}], 'v')
        rows.append({**{k: attendance.get(k) for k in ('IDG', 'IDP', 'IDL', 'IDZ', 'ZilePrezenta', 'ValoareContract', 'ValoareTotala')},
                     **{k: person.get(k) for k in ('Nume', 'CNP', 'Plecat')},
                     'Grupa': group.get('Grupa'), 'Educator': group_educators(group, data.get('Grupe_Educator', []),
                         f"{month['Anul']:04d}-{month['Luna']:02d}-{monthrange(month['Anul'], month['Luna'])[1]:02d}"),
                     'Luna': month.get('LunaT'), 'Anul': month['Anul'], 'Plata': paid,
                     'Plati': access_long(other), 'Retur': access_long(nz(total(returned, 'Suma'))),
                     'SoldInitial': 0, 'SID': 0, 'SIC': 0, 'Restanta': 0,
                     'Compensare': 0, 'Anticipat': 0, 'SFD': 0, 'SFC': 0, 'Detalii': None})
    trace['qPrezenta'] = deepcopy(rows)
    balances = {}
    for person_id, person in people.items():
        earlier = lambda table: [r for r in table if r.get('IDP') == person_id and r.get('IDL') is not None and r['IDL'] < month_id]
        balances[person_id] = (nz(person.get('SI')) + nz(total(earlier(data['Prezenta']), 'ValoareContract'))
                               - nz(total(earlier(payments), 'Plata')) + nz(total(earlier(refunds), 'Suma')))
    trace['qSolduri'] = balances.copy()
    for row in rows:
        balance = balances[row['IDP']]
        row.update(SoldInitial=access_long(balance), SID=access_long(max(balance, 0)), SIC=access_long(max(-balance, 0)))
    trace['Update_Solduri'] = deepcopy(rows)
    rows = [update_situation(row, finalize=False) for row in rows]
    trace['Update_Situatie'] = deepcopy(rows)
    explanations = []
    for row in rows:
        details = []
        for payment in payments:
            if payment['IDZ'] != row['IDZ'] or payment['IDL'] != month_id:
                continue
            for receipt in data['Chitante']:
                if receipt['IDPL'] == payment['IDPL'] and receipt.get('Numar') is not None:
                    doc = 'Ch:' + str(receipt['Numar'])
                    details.append(doc)
                    explanations.append({'IDZ': row['IDZ'], 'DOC': doc})
            for document in data['AlteDoc']:
                if document['IDPL'] == payment['IDPL'] and document.get('Explicatie') is not None and document.get('NrDoc') is not None:
                    doc = document['Explicatie'][:2] + '.' + document['NrDoc']
                    details.append(doc)
                    explanations.append({'IDZ': row['IDZ'], 'DOC': doc})
        for refund in refunds:
            if refund['IDZ'] == row['IDZ'] and refund['IDL'] == month_id:
                doc = 'Re: ' + (refund['NrDoc'] if refund.get('NrDoc') is not None else 'Fără nr.')
                details.append(doc)
                explanations.append({'IDZ': row['IDZ'], 'DOC': doc})
        if details:
            row['Detalii'] = ';'.join(details)
    trace['qExplicatie'] = explanations
    trace['Update_detalii'] = deepcopy(rows)
    rows = [finalize_compensation(row) for row in rows]
    trace['Update_Compensare'] = deepcopy(rows)
    trace['Salvare_Lunara'] = deepcopy(rows)
    return rows, trace
