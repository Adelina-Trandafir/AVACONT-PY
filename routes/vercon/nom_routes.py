"""SLICE-VC06-01: the HTTP side of the nomenclatures and of the gestiune settings.

Every call goes through the blueprint's `guard` (session, unit, VR right) and `scoped` (context re-verified on the
server, type, revision, and for a write the CURRENT lock token). A write opens one transaction: the row, its audit
line and (for a create) the RequestKey are committed together or not at all.

    GET  /api/vercon/nom/<kind>[?parent=<id>]          read           list + columns + selector options
    POST /api/vercon/nom/<kind>                        nomenclatoare  create  {values, request_key, parent?}
    PUT  /api/vercon/nom/<kind>/<id>                   nomenclatoare  edit    {values, version}
    POST /api/vercon/nom/<kind>/<id>/active            nomenclatoare  on/off  {active, version}
    GET  /api/vercon/settings                          read           zec / scara / type / counters
    POST /api/vercon/settings                          configure      {zec, scara}  only while the gestiune holds no data
"""
from contextlib import contextmanager

from flask import g, request

from . import nomenclatoare as nom
from .capabilities import FormUnavailable
from .nomenclatoare import NomError
from .nomenclator_usage import annotate, category_statement
from .nomenclator_stock import product_statement
from .nomenclator_products import used_products
from . import cpv_catalog, mf_classifications

_DEFAULT_ZEC = 4
_DEFAULT_SCARA = 4


def register(bp, guard, scoped, open_db):
    """open_db(db_name) -> context manager yielding a DB-API connection; None where there is no database (preview)."""

    @contextmanager
    def session(write):
        if open_db is None:
            raise FormUnavailable('NO_DATABASE', 'Această secțiune nu este disponibilă în previzualizarea locală.', 503)
        with open_db(g.portal['db_name']) as conn:
            cursor = conn.cursor(dictionary=True)
            try:
                yield cursor
                if write:
                    conn.commit()
            except Exception:
                conn.rollback()
                raise

    def repo_of(cursor, ctx):
        from .repository import ScopedRepository
        return ScopedRepository(cursor, ctx['selection']['subunit'], ctx['selection']['gestiune'])

    def facts(ctx):
        gest = ctx['gestiune']
        return {'subunit': ctx['selection']['subunit'], 'gestiune': ctx['selection']['gestiune'], 'email': g.portal['email']}, \
            gest['type'], int(gest.get('scara', _DEFAULT_SCARA))

    def body():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise NomError('INVALID', 'Cerere invalidă.', 400)
        return data

    def request_id():
        return (request.headers.get('X-Request-Id') or '')[:64] or None

    @bp.get('/api/vercon/cpv')
    @guard('read')
    def cpv_read():
        scoped('read')
        with session(False) as cursor:
            return cpv_catalog.read(cursor, request.args.get('parent'), request.args.get('name', ''), request.args.get('code', ''))

    @bp.get('/api/vercon/cpv/<code>')
    @guard('read')
    def cpv_detail(code):
        scoped('read')
        with session(False) as cursor:
            return cpv_catalog.detail(cursor, code)

    def mf_context():
        ctx = scoped('read')
        if ctx['gestiune']['type'] != 'MF':
            raise FormUnavailable('FORM_NOT_AVAILABLE', 'Clasificările MF există doar la gestiunile MF.', 403)

    @bp.get('/api/vercon/mf/classifications')
    @guard('read')
    def mf_classifications_read():
        mf_context()
        with session(False) as cursor:
            return mf_classifications.read(cursor, request.args.get('parent'), request.args.get('name', ''), request.args.get('code', ''))

    @bp.get('/api/vercon/mf/classifications/<code>')
    @guard('read')
    def mf_classifications_detail(code):
        mf_context()
        with session(False) as cursor:
            return mf_classifications.detail(cursor, code)

    @bp.get('/api/vercon/nom/<kind>')
    @guard('read')
    def nom_list(kind):
        ctx = scoped('read')
        who, gest_type, scale = facts(ctx)
        nom.spec_of(kind, gest_type)
        with session(False) as cursor:
            repo = repo_of(cursor, ctx)
            rows = nom.list_rows(repo, kind, gest_type, scale, request.args.get('parent'))
            rows = annotate(cursor, who['subunit'], who['gestiune'], kind, rows)
            options = nom.lookups(repo, kind, gest_type)
            labels = options
            if kind == 'produse':
                used = used_products(cursor, ctx)
                rows = [{**row, 'used_in_year': row['id'] in used} for row in rows]
                labels = {**options, 'IdMasina': [{'id': row['IdMasina'], 'label': row['Masina'] or ''}
                          for row in repo.select('VC_Masini', columns=('IdMasina', 'Masina'))]}
        return {'kind': kind, 'label': nom.SPECS[kind]['label'], 'columns': nom.columns_for(kind, gest_type), 'rows': rows,
                'lookups': options, 'labels': labels, 'scale': scale,
                'can_write': kind != 'variante' and 'nomenclatoare' in g.vercon_operations}

    @bp.get('/api/vercon/nom/produse/<int:row_id>/statement')
    @guard('read')
    def nom_product_statement(row_id):
        ctx = scoped('read')
        with session(False) as cursor:
            return product_statement(cursor, ctx, row_id)

    @bp.get('/api/vercon/nom/categorii/<int:row_id>/statement')
    @guard('read')
    def nom_category_statement(row_id):
        ctx = scoped('read')
        with session(False) as cursor:
            return category_statement(cursor, ctx, row_id)

    @bp.post('/api/vercon/nom/<kind>')
    @guard('nomenclatoare')
    def nom_create(kind):
        data = body()
        ctx = scoped('nomenclatoare', data)
        who, gest_type, scale = facts(ctx)
        nom.spec_of(kind, gest_type)
        key = str(data.get('request_key') or '')[:64]
        if not key:
            raise NomError('REQUEST', 'Lipsește cheia cererii.', 400)
        with session(True) as cursor:
            row, replayed = nom.create(repo_of(cursor, ctx), cursor, who, kind, gest_type, scale, data.get('values'),
                                       data.get('parent'), key, request_id())
        return {'row': row, 'replayed': replayed}

    @bp.put('/api/vercon/nom/<kind>/<int:row_id>')
    @guard('nomenclatoare')
    def nom_update(kind, row_id):
        data = body()
        ctx = scoped('nomenclatoare', data)
        who, gest_type, scale = facts(ctx)
        nom.spec_of(kind, gest_type)
        with session(True) as cursor:
            row = nom.update(repo_of(cursor, ctx), cursor, who, kind, gest_type, scale, row_id, data.get('values'), data.get('version'), request_id())
        return {'row': row}

    @bp.post('/api/vercon/nom/<kind>/<int:row_id>/active')
    @guard('nomenclatoare')
    def nom_active(kind, row_id):
        data = body()
        if not isinstance(data.get('active'), bool):
            raise NomError('INVALID', 'Cerere invalidă.', 400)
        ctx = scoped('nomenclatoare', data)
        who, gest_type, scale = facts(ctx)
        nom.spec_of(kind, gest_type)
        with session(True) as cursor:
            row = nom.set_active(repo_of(cursor, ctx), cursor, who, kind, gest_type, scale, row_id, data['active'], data.get('version'), request_id())
        return {'row': row}

    # ---- gestiune settings ---------------------------------------------------------------------------------------
    @bp.get('/api/vercon/settings')
    @guard('read')
    def settings_read():
        ctx = scoped('read')
        who, gest_type, scale = facts(ctx)
        with session(False) as cursor:
            cursor.execute('SELECT SettingKey, IntValue FROM VC_Settings WHERE SubunitId=%s AND GestiuneId=%s ORDER BY SettingKey',
                           (who['subunit'], who['gestiune']))
            stored = [{'key': r['SettingKey'], 'value': format(r['IntValue'], 'f')} for r in cursor.fetchall()]
        gest = ctx['gestiune']
        return {'name': gest['name'], 'type': gest_type, 'zec': int(gest.get('zec', _DEFAULT_ZEC)), 'scara': scale,
                'populated': bool(gest.get('populated')), 'settings': stored,
                'can_write': 'configure' in g.vercon_operations}

    @bp.post('/api/vercon/settings')
    @guard('configure')
    def settings_write():
        data = body()
        ctx = scoped('configure', data)
        who, gest_type, scale = facts(ctx)
        zec, scara = data.get('zec'), data.get('scara')
        if not all(isinstance(v, int) and not isinstance(v, bool) for v in (zec, scara)):
            raise NomError('INVALID', 'Zecimalele și scara sunt numere întregi.', 422, {'zec': 'Număr întreg 0-4.', 'scara': 'Număr întreg 0-4.'})
        if not 0 <= zec <= 4:
            raise NomError('INVALID', 'Zecimalele afișate trebuie să fie între 0 și 4.', 422, {'zec': 'Între 0 și 4.'})
        if scara != 4:
            raise NomError('INVALID', 'Precizia de stocare DECIMAL(20,4) este fixă la 4.', 422, {'scara': 'Precizie fixă: 4.'})
        with session(True) as cursor:
            cursor.execute('UPDATE VC_Gestiuni SET Zec=%s, Scara=%s, Version=Version+1 '
                           'WHERE SubunitId=%s AND GestiuneId=%s', (zec, scara, who['subunit'], who['gestiune']))
            if cursor.rowcount != 1:
                raise NomError('NOT_FOUND', 'Gestiunea nu mai există sau setările sunt deja aplicate.', 409)
            nom.audit(cursor, who, 'settings.precision', 'VC_Gestiuni', who['gestiune'], {'Zec': ctx['gestiune'].get('zec'), 'Scara': scale},
                       {'Zec': zec, 'Scara': scara}, None, request_id())
        return {'zec': zec, 'scara': scara}
