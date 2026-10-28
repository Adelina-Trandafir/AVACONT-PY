"""Optional numbering of the consumption bons (BC) separately for every account (SLICE-VC06-19).

Only a MATERIALE gestiune can ask for it, through the VC_Settings row NUM_BC_PE_CONT (IntValue 1). When it is on, every
account has its own counter (VC_Numbering.Kind = 'BC:<IdMasina>') and a number is "taken" only inside its own account;
the same number can then be on one bon of each account. When it is off nothing changes: one counter 'BC'.
"""

SETTING = 'NUM_BC_PE_CONT'


def per_account(cursor, ctx):
    """True when the gestiune numbers its consumption bons separately for every account."""
    if ctx.get('type') != 'MATERIALE':
        return False
    if '_bc_per_account' not in ctx:
        cursor.execute('SELECT IntValue FROM VC_Settings WHERE SubunitId=%s AND GestiuneId=%s AND SettingKey=%s',
                       (ctx['subunit'], ctx['gestiune'], SETTING))
        row = cursor.fetchone()
        ctx['_bc_per_account'] = bool(row and row['IntValue'] and int(row['IntValue']) == 1)
    return ctx['_bc_per_account']


def kind(account):
    """The VC_Numbering kind of the counter of one account."""
    return 'BC:%d' % account if account is not None else 'BC'
