"""Read-only NIR/eFactura metadata, edit guard and scoped access to the original invoice PDF."""
from io import BytesIO

from flask import send_file

from utils import vercon_efactura as shared
from .nomenclatoare import NomError

LOCK_MESSAGE = 'NIR-ul este asociat unei eFacturi și nu poate fi modificat. Eliminați asocierile înainte de editare.'


def linked(cursor, ctx, identities):
    if not identities:
        return {}
    tables = shared.rows(cursor, 'SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() '
                         "AND TABLE_NAME IN ('EF_Primite','EF_PrimiteVercon','EF_PrimiteVerconNir')")
    if len(tables) != 3:
        return {}
    scope = (ctx['subunit'], ctx['gestiune'], ctx['exercitiu'])
    marks = ','.join(['%s'] * len(identities))
    candidates = shared.rows(cursor,
        'SELECT r.IdTrimitere,r.IdIntrare,p.IdPrimita,p.NrFact,p.DataFact,p.DenumireP '
        'FROM EF_PrimiteVerconNir r JOIN EF_PrimiteVercon t ON t.IdTrimitere=r.IdTrimitere '
        'JOIN EF_Primite p ON p.IdPrimita=t.IdPrimita '
        'JOIN VC_NIR n ON n.SubunitId=r.SubunitId AND n.GestiuneId=r.GestiuneId '
        'AND n.ExercitiuId=r.ExercitiuId AND n.IdIntrare=r.IdIntrare '
        "WHERE t.Stare IN ('TRIMISA','PRELUATA') AND COALESCE(n.Anulat,0)=0 AND COALESCE(n.Storno,0)=0 "
        'AND r.SubunitId=%s AND r.GestiuneId=%s AND r.ExercitiuId=%s AND r.IdIntrare IN (' + marks + ') '
        'ORDER BY p.DenumireP,p.DataFact,p.NrFact', (*scope, *identities))
    manual, valid = set(), set()
    if candidates and shared.ready(cursor):
        sending_ids = sorted({row['IdTrimitere'] for row in candidates})
        manual = {row['ID_TRIMITERE'] for row in shared.rows(cursor,
                  'SELECT ID_TRIMITERE FROM EF_VERCON_ASOCIERI WHERE ID_TRIMITERE IN (' + ','.join(['%s'] * len(sending_ids)) + ')', tuple(sending_ids))}
        if manual:
            valid = {(row['ID_TRIMITERE'], row['NIR_ID']) for row in shared.allocations(cursor, receipt_scope=scope) if row['VALID']}
    result = {}
    for row in candidates:
        if row['IdTrimitere'] in manual and (row['IdTrimitere'], row['IdIntrare']) not in valid:
            continue
        result.setdefault(row['IdIntrare'], []).append({
            'id': row['IdPrimita'], 'sending': row['IdTrimitere'], 'number': row['NrFact'] or '',
            'date': row['DataFact'].isoformat() if row['DataFact'] else '', 'supplier': row['DenumireP'] or ''})
    return result


def annotate(cursor, ctx, documents):
    invoices = linked(cursor, ctx, [doc['id'] for doc in documents])
    for doc in documents:
        doc['efactura'] = invoices.get(doc['id'], [])
        doc['has_efactura'] = bool(doc['efactura'])
        if doc['has_efactura']:
            doc['edit_locked'] = True
            doc['edit_message'] = LOCK_MESSAGE
    return documents


def require_editable(cursor, ctx, nir_id):
    if linked(cursor, ctx, [nir_id]).get(nir_id):
        raise NomError('EF_NIR_LOCKED', LOCK_MESSAGE, 409)


def register(bp, guard, scoped, session, build, need):
    @bp.get('/api/vercon/doc/nir/<int:nir_id>/efactura/<int:invoice_id>/pdf')
    @guard('read')
    def invoice_pdf(nir_id, invoice_id):
        need('nir')
        verified = scoped('read')
        with session(False) as cursor:
            ctx, _ = build(cursor, verified, 'nir', False)
            if not any(item['id'] == invoice_id for item in linked(cursor, ctx, [nir_id]).get(nir_id, [])):
                raise NomError('NOT_FOUND', 'eFactura nu este asociată acestui NIR în contextul selectat.', 404)
        from routes.efactura import primite
        from routes.efactura.tokens import EfEroare
        try:
            data, name = primite.pdf_anaf(ctx['unit'], invoice_id)
        except EfEroare as error:
            raise NomError(error.reason, str(error), error.status) from None
        return send_file(BytesIO(data), mimetype='application/pdf', download_name=name, as_attachment=False, max_age=0)
