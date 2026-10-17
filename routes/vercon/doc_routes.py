"""SLICE-VC06-02..05: the HTTP side of the documents (NIR, storno, BC, BR).

    GET  /api/vercon/doc/<family>/list                    the saved documents of the exercise (the page builds the tree)
    GET  /api/vercon/doc/<family>/lookups                 suppliers, committees, VAT, categories, products/variants, receivers
    GET  /api/vercon/doc/<family>/<id>                    one document, header + lines
    GET  /api/vercon/doc/<family>/sources                 storno: the receipts that can be stornoed; br: the bons that can be returned
    GET  /api/vercon/doc/<family>/eligible?source=<id>    the lines of the source with the quantity still left
    GET  /api/vercon/stock/available?date=&products=      stock of the variants on a date (informative; the binding check runs on save)
    POST /api/vercon/doc/<family>                         create   {header, lines, source?, request_key}
    PUT  /api/vercon/doc/<family>/<id>                    edit     {header, lines, version}
    POST /api/vercon/doc/<family>/<id>/cancel             cancel   {reason, version}  (the document stays, marked cancelled)

family = nir | storno | bc | br. A write needs the family's VR right, the CURRENT lock token, an open period and the
revision the page saw; everything it touches is committed in ONE transaction or not at all.
"""
from contextlib import contextmanager
from datetime import datetime

from flask import g, request

from . import depreciation_read, documents, lista, mf, oi, protocol, stock_routes, report_routes, receipt_number, receipt_choices, receipt_tree, receipt_context, receipt_routes, receipt_distribution, invoices
from .capabilities import FormUnavailable
from .nomenclatoare import NomError

RIGHT = {'nir': 'receptie', 'storno': 'storno', 'bc': 'consum', 'br': 'retur', 'bp': 'oi_predare', 'bt': 'oi_transfer', 'bs': 'oi_casare', 'lista': 'alimente'}
FORM_OF = {'lista': 'lista_alimente'}   # the menu key of a family whose name differs
ALL_FAMILIES = documents.FAMILIES + oi.FAMILIES_OI + ('lista',)


def backend(family):
    """The module that knows this family of documents (all expose the same functions)."""
    if family in oi.FAMILIES_OI:
        return oi
    if family == 'lista':
        return lista
    return documents


def create_fn(family):
    return backend(family).CREATE[family]


def edit_fn(family):
    return backend(family).EDIT[family]


def cancel_fn(family):
    module = backend(family)
    return getattr(module, 'cancel_document', None) or module.cancel
_DEFAULT_ZEC = 4
_DEFAULT_SCARA = 4


def register(bp, guard, scoped, open_db):
    documents._TABLE.update(oi.AUDIT_TABLE)
    documents._TABLE.update(lista.AUDIT_TABLE)

    @contextmanager
    def session(write):
        if open_db is None:
            raise FormUnavailable('NO_DATABASE', 'Această secțiune nu este disponibilă în previzualizarea locală.', 503)
        with open_db(g.portal['db_name']) as conn:
            if write:
                # A common invoice can be allocated from different gestiuni. After its
                # row lock is obtained, budget reads must see the preceding commit.
                conn.start_transaction(isolation_level='READ COMMITTED')
            cursor = conn.cursor(dictionary=True)
            try:
                yield cursor
                if write:
                    conn.commit()
            except Exception:
                conn.rollback()
                raise

    def family_of(name):
        if name not in ALL_FAMILIES:
            raise NomError('FAMILY_UNKNOWN', 'Tipul de document nu există.', 404)
        return name

    def build(cursor, vctx, family, write):
        """The verified facts every statement uses: the ids come from the server-resolved context, never from the page."""
        gest = vctx['gestiune']
        if family is not None and gest['type'] not in _types_of(family):
            raise FormUnavailable('FORM_NOT_AVAILABLE', 'Documentul nu este disponibil pentru acest tip de gestiune.', 403)
        sel = vctx['selection']
        exercise = protocol.exercise_of(cursor, sel['subunit'], sel['gestiune'], gest['type'], sel['year'])
        if write:
            protocol.ensure_open(exercise)
            cursor.execute('SELECT GestiuneId FROM VC_Gestiuni WHERE SubunitId=%s AND GestiuneId=%s FOR UPDATE',
                           (sel['subunit'], sel['gestiune']))
            cursor.fetchone()
        return {'subunit': sel['subunit'], 'gestiune': sel['gestiune'], 'exercitiu': exercise['id'], 'email': g.portal['email'], 'type': gest['type'],
                'zec': int(gest.get('zec', _DEFAULT_ZEC)), 'scale': int(gest.get('scara', _DEFAULT_SCARA)), 'year': sel['year'], 'an': exercise['an'],
                'closed': exercise['closed']}, exercise

    def _types_of(family):
        from .capabilities import FORMS
        return FORMS[FORM_OF.get(family, family)][2]

    def repo_of(cursor, ctx):
        from .repository import ScopedRepository
        return ScopedRepository(cursor, ctx['subunit'], ctx['gestiune'], ctx['exercitiu'])

    def body():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise NomError('INVALID', 'Cerere invalidă.', 400)
        return data

    def request_id():
        return (request.headers.get('X-Request-Id') or '')[:64] or None

    def read_document(cursor, ctx, family, doc_id):
        doc = backend(family).get_document(cursor, repo_of(cursor, ctx), ctx, family, doc_id)
        return invoices.enrich(cursor, ctx, doc) if family == 'nir' else doc

    stock_routes.register(bp, guard, scoped, session, build)
    report_routes.register(bp, guard, scoped, session, build)

    # ---- reads -----------------------------------------------------------------------------------------------------
    @bp.get('/api/vercon/doc/<family>/list')
    @guard('read')
    def doc_list(family):
        family_of(family)
        _need(family)
        vctx = scoped('read')
        with session(False) as cursor:
            ctx, _ = build(cursor, vctx, family, False)
            rows = backend(family).list_documents(cursor, ctx, family)
        return {'family': family, 'documents': rows, 'year': ctx['an'], 'closed': ctx['closed'], 'scale': ctx['scale'], 'zec': ctx['zec'],
                'can_write': RIGHT[family] in g.vercon_operations}

    def _need(family):
        if RIGHT[family] not in g.vercon_operations:
            raise FormUnavailable('FORBIDDEN', 'Nu aveți dreptul necesar pentru această operație VERCON.', 403)

    receipt_context.register(bp, guard, scoped, session, build, _need)
    receipt_routes.register(bp, guard, scoped, session, build, repo_of, _need)
    receipt_distribution.register(bp, guard, scoped, session, build, repo_of, _need)

    @bp.get('/api/vercon/doc/<family>/lookups')
    @guard('read')
    def doc_lookups(family):
        family_of(family)
        _need(family)
        vctx = scoped('read')
        with session(False) as cursor:
            ctx, _ = build(cursor, vctx, family, False)
            out = backend(family).lookups(cursor, ctx, family)
            if family == 'nir' and not isinstance(out.get('products'), list):
                out['products'] = receipt_choices.products(cursor, ctx['subunit'], ctx['gestiune'])
        out.update({'scale': ctx['scale'], 'zec': ctx['zec'], 'year': ctx['an'], 'closed': ctx['closed'], 'today': datetime.now().strftime('%Y-%m-%d')})
        return out

    @bp.get('/api/vercon/doc/nir/tree')
    @guard('read')
    def receipt_tree_level():
        """One level of the saved-receipts tree: level=parties | months&party= | documents&party=&month=YYYY-MM."""
        _need('nir')
        vctx = scoped('read')
        level, party = request.args.get('level', 'parties'), request.args.get('party')
        if level not in ('parties', 'months', 'documents') or (level != 'parties' and party is None):
            raise NomError('INVALID', 'Cerere invalidă.', 400)
        with session(False) as cursor:
            ctx, _ = build(cursor, vctx, 'nir', False)
            if level == 'parties':
                items = receipt_tree.parties(cursor, ctx)
            elif level == 'months':
                items = receipt_tree.months(cursor, ctx, party)
            else:
                items = receipt_tree.documents(cursor, ctx, party, request.args.get('month'))
        return {'level': level, 'items': items, 'year': ctx['an'], 'closed': ctx['closed'], 'scale': ctx['scale'], 'zec': ctx['zec'],
                'can_write': RIGHT['nir'] in g.vercon_operations}

    @bp.get('/api/vercon/doc/<family>/number')
    @guard('read')
    def receipt_number_check(family):
        if family not in ('nir', 'storno'):
            raise NomError('FAMILY_UNKNOWN', 'Acest document nu folosește seria NIR/FCS.', 404)
        _need(family)
        vctx = scoped('read')
        exclude = documents._int(request.args.get('exclude'))
        with session(False) as cursor:
            ctx, _ = build(cursor, vctx, family, False)
            return receipt_number.check(cursor, ctx, request.args.get('number'), exclude, documents._int(request.args.get('sub')) or 0, request.args.get('date'), insert=family == 'storno')

    @bp.get('/api/vercon/doc/<family>/sources')
    @guard('read')
    def doc_sources(family):
        family_of(family)
        _need(family)
        if family not in ('storno', 'br'):
            raise NomError('FAMILY_UNKNOWN', 'Acest document nu are sursă.', 404)
        vctx = scoped('read')
        with session(False) as cursor:
            ctx, _ = build(cursor, vctx, family, False)
            if family == 'storno':
                from .storno_stock import sources
                on = protocol.to_date(request.args.get('date') or datetime.now().strftime('%Y-%m-%d'), 'Data FCS')
                rows = sources(cursor, ctx, on, documents._int(request.args.get('exclude')))
            else:
                rows = documents.list_documents(cursor, ctx, 'bc')
        return {'documents': [r for r in rows if not r['annulled']]}

    @bp.get('/api/vercon/doc/<family>/eligible')
    @guard('read')
    def doc_eligible(family):
        family_of(family)
        _need(family)
        if family in ('bt', 'bs'):
            holder = documents._int(request.args.get('holder'))
            exclude = documents._int(request.args.get('exclude'))
            if holder is None:
                raise NomError('INVALID', 'Alegeți deținătorul.', 400)
            vctx = scoped('read')
            with session(False) as cursor:
                ctx, _ = build(cursor, vctx, family, False)
                return {'lines': oi.eligible(cursor, ctx, holder, (family, exclude) if exclude else None)}
        if family not in ('storno', 'br'):
            raise NomError('FAMILY_UNKNOWN', 'Acest document nu are sursă.', 404)
        source = documents._int(request.args.get('source'))
        exclude = documents._int(request.args.get('exclude'))
        if source is None:
            raise NomError('INVALID', 'Alegeți documentul sursă.', 400)
        vctx = scoped('read')
        with session(False) as cursor:
            ctx, _ = build(cursor, vctx, family, False)
            on = protocol.to_date(request.args.get('date') or datetime.now().strftime('%Y-%m-%d'), 'Data FCS') if family == 'storno' else None
            lines = documents.storno_eligible(cursor, ctx, source, exclude, on) if family == 'storno' else documents.return_eligible(cursor, ctx, source, exclude)
        return {'lines': lines}

    @bp.get('/api/vercon/doc/<family>/<int:doc_id>')
    @guard('read')
    def doc_get(family, doc_id):
        family_of(family)
        _need(family)
        vctx = scoped('read')
        with session(False) as cursor:
            ctx, _ = build(cursor, vctx, family, False)
            return read_document(cursor, ctx, family, doc_id)

    @bp.get('/api/vercon/doc/<family>/<int:doc_id>/history')
    @guard('read')
    def doc_history(family, doc_id):
        family_of(family)
        _need(family)
        vctx = scoped('read')
        with session(False) as cursor:
            ctx, _ = build(cursor, vctx, family, False)
            return {'history': documents.history(cursor, ctx, family, doc_id)}

    @bp.get('/api/vercon/doc/lista/bons')
    @guard('read')
    def lista_bons():
        _need('lista')
        vctx = scoped('read')
        exclude = documents._int(request.args.get('exclude'))
        with session(False) as cursor:
            ctx, _ = build(cursor, vctx, 'lista', False)
            return {'bons': lista.eligible_bons(cursor, ctx, exclude)}

    @bp.get('/api/vercon/mf/assets')
    @guard('read')
    def mf_assets():
        if 'mf' not in g.vercon_operations:
            raise FormUnavailable('FORBIDDEN', 'Nu aveți dreptul necesar pentru această operație VERCON.', 403)
        vctx = scoped('read')
        with session(False) as cursor:
            ctx, _ = build(cursor, vctx, None, False)
            if ctx['type'] != 'MF':
                raise FormUnavailable('FORM_NOT_AVAILABLE', 'Evidența MF există doar la gestiunile de tip MF.', 403)
            return {'assets': mf.assets(cursor, ctx), 'scale': ctx['scale'], 'zec': ctx['zec'], 'can_write': True,
                    'can_report': 'raport' in g.vercon_operations}

    @bp.get('/api/vercon/mf/depreciation')
    @guard('raport')
    def mf_depreciation():
        vctx = scoped('raport')
        on_date = protocol.to_date(request.args.get('date'), 'Data calculului')
        raw_variant = request.args.get('variant')
        variant = None
        if raw_variant is not None:
            variant = documents._int(raw_variant)
            if variant is None or variant <= 0:
                raise NomError('INVALID', 'Mijlocul fix selectat este invalid.', 422)
        with session(False) as cursor:
            ctx, _ = build(cursor, vctx, None, False)
            return depreciation_read.read(cursor, ctx, on_date, variant)

    @bp.put('/api/vercon/mf/assets/<int:variant>')
    @guard('read')
    def mf_update(variant):
        data = body()
        if 'mf' not in g.vercon_operations:
            raise FormUnavailable('FORBIDDEN', 'Nu aveți dreptul necesar pentru această operație VERCON.', 403)
        vctx = scoped('mf', data)
        with session(True) as cursor:
            ctx, _ = build(cursor, vctx, None, True)
            result = mf.update(cursor, repo_of(cursor, ctx), ctx, variant, data, request_id())
            assets = [a for a in mf.assets(cursor, ctx) if a['id'] == variant]
        return {'result': result, 'asset': assets[0] if assets else None}

    @bp.get('/api/vercon/stock/available')
    @guard('read')
    def stock_available():
        vctx = scoped('read')
        on = protocol.to_date(request.args.get('date'), 'Data', None)
        products = [documents._int(p) for p in (request.args.get('products') or '').split(',') if p.strip()]
        if any(p is None for p in products):
            raise NomError('INVALID', 'Lista de produse este invalidă.', 400)
        exclude = None
        if request.args.get('exclude_kind') in ('nir', 'storno', 'bc', 'br') and documents._int(request.args.get('exclude_id')) is not None:
            exclude = (request.args['exclude_kind'], documents._int(request.args['exclude_id']))
        with session(False) as cursor:
            ctx, _ = build(cursor, vctx, None, False)
            stock = protocol.available(cursor, ctx, on, products or None, exclude)
        from .numeric import from_stored
        return {'date': on.isoformat(), 'available': {str(k): format(from_stored(v, ctx['scale']), 'f') for k, v in stock.items()}}

    # ---- writes ----------------------------------------------------------------------------------------------------
    @bp.post('/api/vercon/doc/<family>')
    @guard('read')   # the family's own right is checked below, with the context
    def doc_create(family):
        family_of(family)
        data = body()
        _need_write(family)
        vctx = scoped(RIGHT[family], data)
        key = str(data.get('request_key') or '')[:64]
        if not key:
            raise NomError('REQUEST', 'Lipsește cheia cererii.', 400)
        fp = protocol.fingerprint(family, data.get('source'), data.get('header'), data.get('lines'))
        with session(True) as cursor:
            ctx, exercise = build(cursor, vctx, family, True)
            again = protocol.replayed(cursor, ctx, key, fp)
            if again is not None:
                result, was_replay = again, True
            else:
                result = create_fn(family)(cursor, repo_of(cursor, ctx), ctx, exercise, data, key, request_id())
                protocol.remember(cursor, ctx, key, fp, result)
                was_replay = False
            doc = read_document(cursor, ctx, family, result['id'])
        return {'result': result, 'document': doc, 'replayed': was_replay}

    def _need_write(family):
        if RIGHT[family] not in g.vercon_operations:
            raise FormUnavailable('FORBIDDEN', 'Nu aveți dreptul necesar pentru această operație VERCON.', 403)

    @bp.put('/api/vercon/doc/<family>/<int:doc_id>')
    @guard('read')
    def doc_edit(family, doc_id):
        family_of(family)
        data = body()
        _need_write(family)
        vctx = scoped(RIGHT[family], data)
        with session(True) as cursor:
            ctx, exercise = build(cursor, vctx, family, True)
            key = str(data.get('request_key') or '')[:64]
            fp = protocol.fingerprint('edit', family, doc_id, data.get('version'), data.get('source'), data.get('header'), data.get('lines'))
            replay = protocol.replayed(cursor, ctx, key, fp) if key else None
            result = replay if replay is not None else edit_fn(family)(cursor, repo_of(cursor, ctx), ctx, exercise, doc_id, data, request_id())
            if key and replay is None:
                protocol.remember(cursor, ctx, key, fp, result)
            doc = read_document(cursor, ctx, family, doc_id)
        return {'result': result, 'document': doc}

    @bp.post('/api/vercon/doc/<family>/<int:doc_id>/cancel')
    @guard('read')
    def doc_cancel(family, doc_id):
        family_of(family)
        data = body()
        _need_write(family)
        vctx = scoped(RIGHT[family], data)
        with session(True) as cursor:
            ctx, _ = build(cursor, vctx, family, True)
            key = str(data.get('request_key') or '')[:64]
            fp = protocol.fingerprint('cancel', family, doc_id, data.get('version'), data.get('reason'))
            replay = protocol.replayed(cursor, ctx, key, fp) if key else None
            result = replay if replay is not None else cancel_fn(family)(cursor, repo_of(cursor, ctx), ctx, family, doc_id, data, request_id())
            if key and replay is None:
                protocol.remember(cursor, ctx, key, fp, result)
            doc = read_document(cursor, ctx, family, doc_id)
        return {'result': result, 'document': doc}
