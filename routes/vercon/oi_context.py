"""Read-only PTC history for the transfer and write-off editors."""
from utils.clock import bucharest_today
from decimal import Decimal

from flask import request

from . import bon_number, oi, protocol
from .documents import _all, _int, _iso, _money, _one, _scope
from .nomenclatoare import NomError


def statement(cursor, ctx, on, holder=None, variant=None):
    if holder is not None:
        identity = _one(cursor, 'SELECT Denumire AS name FROM VC_Clienti '
                        'WHERE SubunitId=%s AND GestiuneId=%s AND IDC=%s',
                        (ctx['subunit'], ctx['gestiune'], holder))
    else:
        identity = _one(cursor, 'SELECT p.DenumireP AS name,p.UM AS um FROM VC_ProduseP v '
                        'JOIN VC_Produse p ON p.SubunitId=v.SubunitId AND p.GestiuneId=v.GestiuneId AND p.ID=v.ID '
                        'WHERE v.SubunitId=%s AND v.GestiuneId=%s AND v.IdProdus=%s',
                        (ctx['subunit'], ctx['gestiune'], variant))
    if not identity:
        raise NomError('NOT_FOUND', 'Deținătorul sau produsul nu mai există în gestiunea curentă.', 404)
    entries = []
    for family, spec in oi.SPEC.items():
        receiver = 't.' + spec['holder'] if family != 'bs' else 'NULL'
        giver = 't.IDC_OLD' if family == 'bt' else 't.' + spec['holder'] if family == 'bs' else 'NULL'
        parties = 't.' + spec['holder'] + (',t.IDC_OLD' if family == 'bt' else '')
        params = [*_scope(ctx), on]
        if holder is not None:
            where = ' AND (t.{h}=%s'.format(h=spec['holder'])
            params.append(holder)
            if family == 'bt':
                where += ' OR t.IDC_OLD=%s'
                params.append(holder)
            where += ')'
        else:
            where = ' AND t.IdProdus=%s'
            params.append(variant)
        rows = _all(cursor, (
            'SELECT t.{key} AS id,t.{number} AS number,DATE(t.{date}) AS day,'
            '{giver} AS giver_id,{receiver} AS receiver_id,MIN(cg.Denumire) AS giver,'
            'MIN(cr.Denumire) AS receiver,SUM(t.Cant) AS quantity,SUM(t.ValoareCUTVA) AS value '
            'FROM {table} t LEFT JOIN VC_Clienti cg ON cg.SubunitId=t.SubunitId '
            'AND cg.GestiuneId=t.GestiuneId AND cg.IDC={giver} '
            'LEFT JOIN VC_Clienti cr ON cr.SubunitId=t.SubunitId '
            'AND cr.GestiuneId=t.GestiuneId AND cr.IDC={receiver} '
            'WHERE t.SubunitId=%s AND t.GestiuneId=%s AND t.ExercitiuId=%s '
            'AND t.Anulat=0 AND t.{key} IS NOT NULL AND t.{key}<>0 AND DATE(t.{date})<=%s{where} '
            'GROUP BY t.{key},t.{number},DATE(t.{date}),{parties}'
        ).format(key=spec['key'], number=spec['num'], date=spec['date'], table=spec['table'],
                 giver=giver, receiver=receiver, parties=parties, where=where), params)
        for row in rows:
            sign = -1 if holder is not None and row['giver_id'] == holder else 1
            entries.append({'id': row['id'], 'kind': family.upper(), 'number': bon_number.display(family, row['number']),
                            'date': _iso(row['day']), 'giver': row['giver'] or ('Magazie' if family == 'bp' else ''),
                            'receiver': row['receiver'] or ('Casare' if family == 'bs' else ''),
                            'quantity': _money(sign * Decimal(row['quantity'] or 0), ctx['scale']),
                            'value': _money(sign * Decimal(row['value'] or 0), ctx['scale'])})
    entries.sort(key=lambda row: (row['date'] or '', row['id'], row['kind']), reverse=True)
    return {'name': identity['name'] or '', 'um': identity.get('um') or '', 'rows': entries}


def register(bp, guard, scoped, session, build, need):
    @bp.get('/api/vercon/doc/<family>/custody-statement')
    @guard('read')
    def custody_statement(family):
        if family not in ('bt', 'bs'):
            raise NomError('FAMILY_UNKNOWN', 'Fișa nu este disponibilă pentru acest bon.', 404)
        need(family)
        vctx = scoped('read')
        holder, variant = _int(request.args.get('holder')), _int(request.args.get('variant'))
        if (holder is None) == (variant is None) or (holder or variant) <= 0:
            raise NomError('INVALID', 'Alegeți un deținător sau un produs valid.', 422)
        on = protocol.to_date(request.args.get('date') or bucharest_today().isoformat(), 'Data bonului')
        with session(False) as cursor:
            ctx, _ = build(cursor, vctx, family, False)
            return statement(cursor, ctx, on, holder, variant)
