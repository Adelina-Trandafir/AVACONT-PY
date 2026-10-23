"""Recalculate a linked food list after editing its BC, in the BC transaction."""
from decimal import Decimal

from . import food_list_portions, protocol
from .nomenclatoare import NomError


def refresh_for_bon(cursor, repo, ctx, bon_id, request_id):
    from .lista import _bons_total
    from . import recalculation_audit

    bon = repo.select('VC_Iesiri_H', {'IdBonConsum': bon_id})[0]
    origin = recalculation_audit.cause('bc', bon_id, bon['NumarBC'], bon['DataBC'], bon['Version'])
    changed = []
    for link in repo.select('VC_LegBCLA', {'IdBonConsum': bon_id}, ('IdLista',)):
        list_id = link['IdLista']
        heads = repo.select('VC_LABC', {'IdLista': list_id}, for_update=True)
        if not heads or heads[0]['Anulat']:
            raise NomError('LINKED_LIST', 'Lista asociată nu mai este disponibilă.', 409)
        before = heads[0]
        food_list_portions.require_month(cursor, ctx, before['DATALA'])
        if before['ValoareLista'] is None:
            raise NomError('LIST_VALUE_MISSING', 'Lista asociată nu are valoarea păstrată. Corectați mai întâi lista.', 409)
        total = _bons_total(cursor, ctx, list_id)
        values = {'ValoareBon': total, 'DEP': Decimal(before['ValoareLista']) - total}
        if repo.update('VC_LABC', values, {'IdLista': list_id, 'Version': before['Version']}, bump_version=True) != 1:
            raise NomError('CONFLICT', 'Lista asociată a fost modificată. Reîncărcați documentele.', 409)
        protocol.audit(cursor, ctx, 'lista.bc_update', 'VC_LABC', list_id,
                       {key: before[key] for key in values},
                       {**values, 'bon_id': bon_id, 'cause': origin, 'reason': origin['reason']}, None, request_id)
        changed.append(list_id)
    return changed
