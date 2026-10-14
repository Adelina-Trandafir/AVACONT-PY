"""Resolve a bank payment's child/month without requiring entered attendance."""
from .domain import SCHEMA, require
from . import service


def reviewed_target(repo, body):
    # Keep old in-flight requests compatible; new pages identify the child and month directly.
    legacy = None
    if body.get('child_id') is None or body.get('month_id') is None:
        reference = body.get('attendance_id')
        require(type(reference) is int and reference > 0, 'REQUEST', 'Alegeți copilul și luna.', 400)
        legacy = repo.get('Prezenta', reference)
    child_id = body.get('child_id', legacy['IDP'] if legacy else None)
    month_id = body.get('month_id', legacy['IDL'] if legacy else None)
    require(type(child_id) is int and child_id > 0 and type(month_id) is int and month_id > 0,
            'REQUEST', 'Alegeți copilul și luna.', 400)
    child = repo.get('Platitori', child_id)
    month = repo.get('LunaD', month_id)
    parent = repo.get('Platitori_sub', body.get('payer_id'))
    require(parent['IDP'] == child_id and bool(parent.get('Activ')), 'PAYER',
            'Alegeți plătitorul activ al copilului din subunitatea curentă.', 400)
    require(parent['Version'] == body.get('payer_version') and child['Version'] == body.get('child_version'),
            'CONFLICT', 'Copilul sau plătitorul s-a modificat. Reîncărcați selecția.', 409)
    if body.get('month_version') is not None:
        require(month['Version'] == body['month_version'], 'CONFLICT', 'Luna s-a modificat. Reîncărcați selecția.', 409)
    rows = repo.rows('Prezenta', IDP=child_id, IDL=month_id)
    require(len(rows) <= 1, 'ATTENDANCE_AMBIGUOUS', 'Copilul are mai multe înregistrări lunare. Corectați evidența.', 409)
    attendance = rows[0] if rows else None
    if body.get('attendance_id') is not None:
        require(attendance and attendance['IDZ'] == body['attendance_id'], 'CONFLICT', 'Înregistrarea lunară s-a schimbat.', 409)
        if body.get('attendance_version') is not None:
            require(attendance['Version'] == body['attendance_version'], 'CONFLICT', 'Datele lunare s-au modificat.', 409)
    return child, month, parent, attendance


def ensure_payment_row(repo, child, month, attendance):
    """A financial link with zero days; existing attendance is never overwritten."""
    if attendance is not None:
        return attendance
    repo.get('Grupe', child['IDG'])
    if month['Inchisa']:
        require(not repo.rows('SS_Buget', IDP=child['IDP'], IDL=month['IDL']), 'SNAPSHOT',
                'Există o situație salvată fără înregistrarea lunară corespunzătoare.', 409)
    attendance = repo.insert('Prezenta', {'IDP': child['IDP'], 'IDL': month['IDL'], 'IDG': child['IDG'],
                                        'IDV': month.get('IDV'), 'ZilePrezenta': 0,
                                        'ValoareContract': 0, 'ValoareTotala': 0})
    if month['Inchisa']:
        # The existing payment command rewrites this saved situation after inserting the receipt.
        rows, _ = service.calculate(repo.data(), month['IDL'])
        snapshot = next(row for row in rows if row['IDZ'] == attendance['IDZ'])
        repo.insert('SS_Buget', {key: value for key, value in snapshot.items() if key in SCHEMA['SS_Buget']['fields']})
    return attendance
