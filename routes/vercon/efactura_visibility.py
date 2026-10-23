"""Unit-wide invoice visibility, shared by all warehouses and users. Allocations stay intact."""
from flask import request

from . import protocol
from .documents import _all, _one
from .nomenclatoare import NomError


def ready(cursor):
    return bool(_one(cursor, "SELECT 1 FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() "
                     "AND TABLE_NAME='EF_VERCON_VIZIBILITATE'"))


def inactive_ids(cursor):
    return {r['ID_TRIMITERE'] for r in _all(cursor,
            'SELECT ID_TRIMITERE FROM EF_VERCON_VIZIBILITATE WHERE INACTIV=1')}


def set_inactive(cursor, ctx, sending_id, inactive):
    if not ready(cursor):
        raise NomError('EF_VISIBILITY_SCHEMA_REQUIRED',
                       'Marcajul Inactiv necesită aplicarea manuală a sql/EV_03_vercon_facturi_inactive.sql și sincronizarea structurii în DC.', 503)
    sending = _one(cursor, 'SELECT IdTrimitere,Stare FROM EF_PrimiteVercon WHERE IdTrimitere=%s FOR UPDATE', (sending_id,))
    if not sending or sending['Stare'] not in ('TRIMISA', 'PRELUATA'):
        raise NomError('NOT_FOUND', 'Factura a fost retrasă sau returnată contabilului. Reîncărcați lista.', 409)
    before = _one(cursor, 'SELECT INACTIV FROM EF_VERCON_VIZIBILITATE WHERE ID_TRIMITERE=%s', (sending_id,))
    old = bool(before and before['INACTIV'])
    if old != inactive:
        cursor.execute('INSERT INTO EF_VERCON_VIZIBILITATE (ID_TRIMITERE,INACTIV,MODIFICAT_DE) '
                       'VALUES (%s,%s,%s) ON DUPLICATE KEY UPDATE INACTIV=VALUES(INACTIV),'
                       'MODIFICAT_DE=VALUES(MODIFICAT_DE),MODIFICAT_LA=NOW()',
                       (sending_id, int(inactive), ctx['email']))
        protocol.audit(cursor, ctx, 'efactura.visibility', 'EF_PrimiteVercon', sending_id,
                       {'inactive': old}, {'inactive': inactive}, None, request.headers.get('X-Request-Id'))
    return {'id': sending_id, 'inactive': inactive}


def register(bp, guard, scoped, session, build, need):
    @bp.post('/api/vercon/doc/nir/efactura/<int:sending_id>/visibility')
    @guard('receptie')
    def update_visibility(sending_id):
        need('nir')
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or type(data.get('inactive')) is not bool:
            raise NomError('INVALID', 'Marcajul Inactiv trebuie să fie bifat sau debifat.', 422)
        verified = scoped('receptie', data)
        with session(True) as cursor:
            # Metadata only: still requires the current warehouse lock, including in a closed year.
            ctx, _ = build(cursor, verified, 'nir', False)
            return set_inactive(cursor, ctx, sending_id, data['inactive'])
