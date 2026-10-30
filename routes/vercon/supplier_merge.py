"""Duplicate suppliers: one firm typed under two fiscal codes (28022254 / 280222254), and their merge.

The identity of a supplier in online VERCON is its normalized fiscal code, stored in VC_NIR.CodPartener and
VC_FACTURI.CodPartener. Two partners are «duplicates» when their names are equal once spaces, accents, punctuation,
a leading SC / S.C. and a trailing SRL / S.R.L. / SA are removed (utils.vercon_suppliers.duplicate_key) and their
fiscal codes differ. Only suppliers that appear in VERCON documents (receipts, common invoices) or in received e-invoices
count: a partner nobody ever invoiced is not a candidate. Suppliers without a fiscal code are ignored.

    GET  /api/vercon/suppliers/duplicates   the groups (and whether the user may merge them)
    POST /api/vercon/suppliers/merge        {keep, merge: [fiscal codes], request_key}

A merge moves every NIR and every common invoice of the DC from the merged codes to `keep`, bumps their versions
(an editor that has them open gets a conflict and reloads) and hides the merged partners (Parteneri.Ascuns = 1).
It runs in ONE transaction. The partner rows are hidden, never deleted: FOREXE keeps its references.
"""
from flask import g, request
from utils import vercon_suppliers as suppliers

from . import protocol
from .documents import _all, _one
from .nomenclatoare import NomError

MAX_SOURCES = 50


def _marks(values):
    return ','.join(['%s'] * len(values))


def has_einvoices(cursor):
    return bool(_one(cursor, 'SELECT 1 AS x FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s', ('EF_Primite',)))


def usage(cursor, codes):
    """{fiscal code: [receipts, common invoices, received e-invoices]} of the DC; annulled receipts and absorbed invoices left out."""
    marks = _marks(codes)
    out = {code: [0, 0, 0] for code in codes}
    queries = [(0, 'SELECT CodPartener AS cf, COUNT(*) AS n FROM VC_NIR WHERE CodPartener IN (' + marks + ') AND COALESCE(Anulat,0)=0 GROUP BY CodPartener'),
               (1, 'SELECT CodPartener AS cf, COUNT(*) AS n FROM VC_FACTURI WHERE CodPartener IN (' + marks + ') AND REUNITA_IN IS NULL GROUP BY CodPartener')]
    if has_einvoices(cursor):
        queries.append((2, 'SELECT CuiNormalizat AS cf, COUNT(*) AS n FROM EF_Primite WHERE CuiNormalizat IN (' + marks + ') GROUP BY CuiNormalizat'))
    for slot, sql in queries:
        for row in _all(cursor, sql, codes):
            out.setdefault(suppliers.code(row['cf']), [0, 0, 0])[slot] += row['n']
    return out


def candidates(cursor):
    """Every supplier that appears in a VERCON receipt, a common invoice or a received e-invoice, with the name to compare:
    the registered partner, else the name written on the receipt, else the one on the e-invoice. Partners with no document
    at all are NOT candidates."""
    found = {}

    def add(code, name, slot, count):
        code = suppliers.code(code)
        if code:
            item = found.setdefault(code, {'names': [], 'counts': [0, 0, 0]})
            item['counts'][slot] += count
            if name and name not in item['names']:
                item['names'].append(name)
    for row in _all(cursor, "SELECT CodPartener AS cf, MIN(NULLIF(FurnizorV,'')) AS name, COUNT(*) AS n FROM VC_NIR "
                    "WHERE COALESCE(CodPartener,'')<>'' AND COALESCE(Anulat,0)=0 GROUP BY CodPartener"):
        add(row['cf'], row['name'], 0, row['n'])
    for row in _all(cursor, "SELECT CodPartener AS cf, COUNT(*) AS n FROM VC_FACTURI WHERE COALESCE(CodPartener,'')<>'' "
                    'AND REUNITA_IN IS NULL GROUP BY CodPartener'):
        add(row['cf'], None, 1, row['n'])
    if has_einvoices(cursor):
        for row in _all(cursor, "SELECT CuiNormalizat AS cf, MIN(NULLIF(DenumireP,'')) AS name, COUNT(*) AS n FROM EF_Primite "
                        "WHERE COALESCE(CuiNormalizat,'')<>'' GROUP BY CuiNormalizat"):
            add(row['cf'], row['name'], 2, row['n'])
    partners = {}
    for row in _all(cursor, 'SELECT p.CodFiscal, p.DenumirePartener, p.Ascuns FROM ' + suppliers.relation(False) + ' p'):
        partners[row['CodFiscal']] = row
    out = {}
    for code, item in found.items():
        partner = partners.get(code)
        registered = bool(partner and not partner['Ascuns'])
        name = (partner['DenumirePartener'] if partner and partner['DenumirePartener'] else None) or (item['names'][0] if item['names'] else code)
        out[code] = {'cf': code, 'name': name, 'receipts': item['counts'][0], 'invoices': item['counts'][1],
                     'einvoices': item['counts'][2], 'partner': registered}
    return out


def groups(cursor):
    """Suppliers of the documents that share a name key but not a fiscal code; the busiest first inside a group."""
    by_key = {}
    for item in candidates(cursor).values():
        key = suppliers.duplicate_key(item['name'])
        if key:
            by_key.setdefault(key, []).append(item)
    result = []
    for key, members in by_key.items():
        if len(members) > 1:
            members.sort(key=lambda item: (-item['receipts'], -item['einvoices'], -item['invoices'], item['cf']))
            result.append({'key': key, 'label': members[0]['name'], 'members': members})
    return sorted(result, key=lambda item: (suppliers.name_key(item['label']), item['key']))


def merge(cursor, ctx, raw, request_id=None):
    keep = suppliers.code(raw.get('keep'))
    sources = raw.get('merge')
    if not keep or not isinstance(sources, list) or not 1 <= len(sources) <= MAX_SOURCES:
        raise NomError('INVALID', 'Alegeți partenerul păstrat și cel puțin un partener de combinat.', 422)
    losers = list(dict.fromkeys(suppliers.code(value) for value in sources))
    if '' in losers or keep in losers:
        raise NomError('INVALID', 'Partenerul păstrat nu poate fi și combinat în el însuși.', 422)
    same_group = next((group['members'] for group in groups(cursor) if keep in {m['cf'] for m in group['members']}), [])
    if {keep, *losers} - {member['cf'] for member in same_group}:
        raise NomError('SUPPLIER_CHANGED', 'Lista partenerilor s-a schimbat. Închideți și redeschideți fereastra.', 409)
    survivor = suppliers.find(cursor, keep, active_only=True)
    if not survivor:
        raise NomError('SUPPLIER_NO_PARTNER', 'Furnizorul păstrat nu există în Parteneri. Adăugați-l mai întâi în nomenclatorul Parteneri sau păstrați alt cod fiscal.', 422)
    marks = _marks(losers)
    before = usage(cursor, [keep, *losers])
    cursor.execute('UPDATE VC_NIR SET CodPartener=%s, FurnizorV=%s, Version=Version+1 WHERE CodPartener IN (' + marks + ')',
                   (keep, survivor['DenumirePartener'], *losers))
    receipts = cursor.rowcount
    cursor.execute('UPDATE VC_FACTURI SET CodPartener=%s, VERSION=VERSION+1 WHERE CodPartener IN (' + marks + ')', (keep, *losers))
    invoices = cursor.rowcount
    cursor.execute('UPDATE Parteneri SET Ascuns=1 WHERE ' + suppliers.sql('CodFiscal') + ' IN (' + marks + ')', losers)
    twins = _one(cursor, 'SELECT COUNT(*) AS n FROM (SELECT 1 FROM VC_FACTURI WHERE CodPartener=%s AND REUNITA_IN IS NULL '
                 'GROUP BY NUMAR, DATA HAVING COUNT(*)>1) t', (keep,))['n']
    result = {'keep': keep, 'merged': losers, 'receipts': receipts, 'invoices': invoices, 'invoice_twins': twins}
    protocol.audit(cursor, ctx, 'supplier.merge', 'Parteneri', keep, {'usage': before}, result, None, request_id)
    return result


def register(bp, guard, scoped, session, build):
    @bp.get('/api/vercon/suppliers/duplicates')
    @guard('read')
    def supplier_duplicates():
        scoped('read')
        from .partners import is_administrator
        can_merge = 'receptie' in g.vercon_operations and not is_administrator(g.portal)
        if not can_merge:
            return {'can_merge': False, 'groups': []}
        with session(False) as cursor:
            return {'can_merge': True, 'groups': groups(cursor) if suppliers.schema_ready(cursor) else []}

    @bp.post('/api/vercon/suppliers/merge')
    @guard('receptie')
    def supplier_merge():
        from .partners import is_administrator
        if is_administrator(g.portal):
            raise NomError('FORBIDDEN', 'Administratorul poate actualiza partenerii numai prin ANAF sau cod fiscal negăsit în ANAF.', 403)
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise NomError('INVALID', 'Cerere invalidă.', 400)
        vctx = scoped('receptie', data)
        key = str(data.get('request_key') or '')[:64]
        if not key:
            raise NomError('REQUEST', 'Lipsește cheia cererii.', 400)
        fingerprint = protocol.fingerprint('supplier.merge', {k: v for k, v in data.items() if k != 'request_key'})
        with session(True) as cursor:
            from . import supplier_identity
            supplier_identity.require(cursor)
            ctx, _ = build(cursor, vctx, 'nir', True)
            replay = protocol.replayed(cursor, ctx, key, fingerprint)
            if replay is not None:
                return replay
            result = merge(cursor, ctx, data, request.headers.get('X-Request-Id'))
            protocol.remember(cursor, ctx, key, fingerprint, result)
            return result
