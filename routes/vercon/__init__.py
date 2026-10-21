"""SLICE-VC01/VC02/VC04: VERCON blueprint. Section VR of the portal (roles live in AVACONT_COMUN, read by
routes/portal/drepturi.py). Nothing here touches a database at import time.

Every API call needs the portal session, the unit the page was opened for (X-Vercon-Unit) and the VR right
of the operation. A business call goes through `scoped(...)`: the context is verified again on the server, the TYPE
must allow the operation, the context revision must match and a write needs the current lock token.
Business endpoints: nomenclatures and settings (VC06-01, nom_routes.py); documents arrive with VC06-02+."""
import logging
from functools import wraps
from io import BytesIO

from flask import Blueprint, Response, current_app, g, jsonify, request, send_file, send_from_directory

from . import capabilities, diagnostics, doc_routes, nom_routes, operations
from .capabilities import FormUnavailable
from .connection import connection  # noqa: F401  re-exported for the repositories of VC03+
from .context import SelectionInvalid, resolve
from .lock import Busy, LockLost, LockService, SqlLockStore
from .nomenclatoare import form_kinds, NomError
from .provider import SqlProvider

SECTION = "VR"

logger = logging.getLogger(__name__)

_PAGE_HEADERS = {
    'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
    'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                               "img-src 'self' data: blob:; worker-src 'self' blob:; frame-src 'self' blob:; "
                               "frame-ancestors 'none'; base-uri 'none'",
}


def _refuse(status, reason, message):
    return jsonify(error=message, reason=reason), status


def create_blueprint(authenticate=None, rights=None, preview=False, provider=None, lock_factory=None, open_db=None):
    bp = Blueprint('vercon', __name__)
    provider = provider or SqlProvider()   # the VC_* tables of the unit database (the preview passes a fixture one)
    if open_db is None and not preview:
        open_db = connection   # the preview has no database: its nomenclature calls answer 503
    # lock_factory(db_name) -> LockService of that DC database (VC_ContextLock); the preview passes an in-memory one.
    if lock_factory is None:
        def lock_factory(db_name):
            from utils.database import get_kbot_connection
            return LockService(SqlLockStore(lambda: get_kbot_connection(db_name)))
    # rights(context) -> set of VR operations the signed-in user may do in this unit. A failure of the check is an
    # error (500), never an open door.
    if rights is None:
        def rights(context):
            from routes.portal import drepturi
            return drepturi.operations_of(context["email"], context["db_name"], SECTION)

    def guard(operation):
        def decorate(function):
            @wraps(function)
            def guarded(*args, **kwargs):
                def run():
                    rid = diagnostics.request_id(request.headers.get('X-Request-Id'))
                    context = getattr(g, 'portal', None) or {}
                    try:
                        if request.headers.get('X-Vercon-Unit') != context.get('db_name'):
                            diagnostics.refusal(operation, context, rid, 'CONTEXT_CHANGED')
                            response = _refuse(409, 'CONTEXT_CHANGED', 'Unitatea s-a schimbat. Reîncărcați pagina.')
                        else:
                            g.vercon_operations = rights(context)
                            if operation not in g.vercon_operations:
                                diagnostics.refusal(operation, context, rid, 'FORBIDDEN')
                                response = _refuse(403, 'FORBIDDEN', 'Nu aveți dreptul necesar pentru această operație VERCON.')
                            else:
                                result = function(*args, **kwargs)
                                response = result if isinstance(result, (Response, tuple)) else jsonify(result)
                                diagnostics.info(operation, 'success', context, rid)
                    except SelectionInvalid as error:
                        diagnostics.refusal(operation, context, rid, error.reason)
                        response = _refuse(409, error.reason, str(error))
                    except Busy as error:
                        diagnostics.busy(operation, context, rid)
                        response = _refuse(409, 'GESTIUNE_OCCUPIED', 'Gestiunea este ocupată de alt utilizator (%s).' % error.holder)
                    except LockLost as error:
                        diagnostics.refusal(operation, context, rid, 'LOCK_LOST')
                        response = _refuse(409, 'LOCK_LOST', str(error))
                    except NomError as error:
                        diagnostics.refusal(operation, context, rid, error.reason)
                        response = (jsonify(error=str(error), reason=error.reason, fields=error.fields), error.status)
                    except FormUnavailable as error:
                        diagnostics.refusal(operation, context, rid, error.reason)
                        response = _refuse(error.status, error.reason, str(error))
                    except Exception:
                        logger.exception(diagnostics.line(operation, 'failed', context, rid))
                        response = _refuse(500, 'SERVER', 'Operația nu a fost finalizată.')
                    if isinstance(response, tuple):
                        response = current_app.make_response(response)
                    response.headers.update({'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
                                             'X-Request-Id': rid})
                    return response
                if authenticate:
                    return authenticate(run)()
                from routes.portal.portal import require_portal_session
                from utils.timing import timed
                return require_portal_session(timed('vercon.' + operation)(run))()
            return guarded
        return decorate

    @bp.get('/vercon')
    def page():
        response = send_from_directory(current_app.static_folder, 'vercon.html')
        if preview:   # only the local launcher marks the page; production never does
            response.direct_passthrough = False
            response.set_data(response.get_data(as_text=True).replace('<html lang="ro">', '<html lang="ro" data-vc-preview="true">', 1))
        response.headers.update(_PAGE_HEADERS)
        return response

    def _resolved(source=None, *, auto_subunit=True):
        args = source if source is not None else request.args
        return resolve(provider.tree(g.portal['db_name'], g.portal['email']),
                       args.get('subunit'), args.get('gestiune'), args.get('year'), auto_subunit=auto_subunit)

    def _locks():
        return lock_factory(g.portal['db_name'])

    def _other_holds(locks, gest, sub):
        held = locks.store.holder(sub, gest['id'])
        return bool(held and not held['expired'] and held['email'] != g.portal['email'])

    def scoped(operation, source=None, lock_token=None, version=None):
        """The one door of every business call: the context is re-resolved on the server (the ids the client sent
        authorise nothing alone), the TYPE must allow the operation, the context revision must be the client's, and a
        write needs the CURRENT lock token. Returns the verified context."""
        ctx = _resolved(source)
        gest = ctx['gestiune']
        if gest is None:
            raise SelectionInvalid('Alegeți subunitatea și gestiunea.')
        if not operations.type_allows(operation, gest['type']):
            raise FormUnavailable('OPERATION_NOT_FOR_TYPE', 'Operația nu este disponibilă pentru acest tip de gestiune.', 403)
        version = version if version is not None else request.headers.get('X-Vercon-Version')
        if version and version != ctx['version']:
            raise FormUnavailable('CONTEXT_CHANGED', 'Contextul s-a schimbat. Reîncărcați pagina.', 409)
        if operations.is_write(operation):
            _locks().validate(ctx['selection']['subunit'], gest['id'], lock_token if lock_token is not None else request.headers.get('X-Vercon-Lock'), g.portal['email'])
        return ctx

    @bp.get('/api/vercon/context')
    @guard('read')
    def context():
        ctx = _resolved(auto_subunit=False)
        gest = ctx.pop('gestiune')
        menu = []
        ops = set(g.vercon_operations)
        mine = False
        if gest:
            sub = ctx['selection']['subunit']
            locks = _locks()
            ops = operations.effective(ops, gest['type'])           # right AND type
            occupied = bool(gest.get('occupied_by')) or _other_holds(locks, gest, sub)
            held = locks.store.holder(sub, gest['id'])
            mine = bool(held and not held['expired'] and held['email'] == g.portal['email'])
            menu = capabilities.menu_for(gest['type'], ops, occupied)
        return {'section': SECTION, 'unit': g.portal['db_name'], 'operations': sorted(ops), 'lock_mine': mine,
                'ready': bool(ctx['options']['subunits']), 'menu': menu,
                'type_locked': bool(gest and gest.get('populated')), **ctx}

    @bp.get('/api/vercon/forms/<form>/open')
    @guard('read')
    def open_form(form):
        # The same gate as the menu, applied to a direct call: a form the type may not open is refused here.
        ctx = _resolved()
        gest = ctx['gestiune']
        if gest is None:
            raise SelectionInvalid('Alegeți subunitatea și gestiunea.')
        occupied = bool(gest.get('occupied_by')) or _other_holds(_locks(), gest, ctx['selection']['subunit'])
        capabilities.check_open(form, gest['type'], operations.effective(g.vercon_operations, gest['type']), occupied)
        return {'form': form, 'type': gest['type'], 'selection': ctx['selection'], 'version': ctx['version'], 'write': capabilities.form_writes(form),
                'nom': form_kinds(form, gest['type']), 'columns': capabilities.columns_for(form, gest['type'])}

    @bp.post('/api/vercon/gestiune/type')
    @guard('configure')
    def set_type():
        # The type is editable only for a gestiune that holds no data yet; after that it is immutable.
        body = request.get_json(silent=True) or {}
        new_type = body.get('type')
        if new_type not in capabilities.TYPES:
            raise FormUnavailable('TYPE_UNKNOWN', 'Tip de gestiune necunoscut.', 400)
        ctx = scoped('configure', body)
        gest = ctx['gestiune']
        if gest.get('populated'):
            return _refuse(409, 'TYPE_LOCKED', 'Tipul nu mai poate fi schimbat: gestiunea conține date.')
        provider.set_type(g.portal['db_name'], ctx['selection']['subunit'], gest['id'], new_type)
        return {'type': new_type}

    # ---- exclusive access (VC04-04) --------------------------------------------------------------------------------
    @bp.post('/api/vercon/lock/acquire')
    @guard('read')
    def lock_acquire():
        body = request.get_json(silent=True) or {}
        ctx = _resolved(body)
        gest = ctx['gestiune']
        if gest is None:
            raise SelectionInvalid('Alegeți subunitatea și gestiunea.')
        if not any(operations.is_write(o) for o in operations.effective(g.vercon_operations, gest['type'])):
            raise FormUnavailable('FORBIDDEN', 'Nu aveți nicio operație de scriere în această gestiune.', 403)
        locks = _locks()
        token = locks.acquire(ctx['selection']['subunit'], gest['id'], g.portal['email'], bool(body.get('takeover')))
        return {'token': token, 'ttl': locks.ttl}

    @bp.post('/api/vercon/lock/heartbeat')
    @guard('read')
    def lock_heartbeat():
        body = request.get_json(silent=True) or {}
        ctx = _resolved(body)
        _locks().heartbeat(ctx['selection']['subunit'], ctx['selection']['gestiune'], request.headers.get('X-Vercon-Lock'), g.portal['email'])
        return {'ok': True}

    @bp.post('/api/vercon/lock/release')
    @guard('read')
    def lock_release():
        body = request.get_json(silent=True) or {}
        ctx = _resolved(body)
        return {'released': _locks().release(ctx['selection']['subunit'], ctx['selection']['gestiune'], request.headers.get('X-Vercon-Lock'), g.portal['email'])}

    @bp.get('/api/vercon/help/pdf')
    @guard('read')
    def help_pdf():
        from .help_pdf import pdf_bytes
        response = send_file(BytesIO(pdf_bytes()), mimetype='application/pdf', as_attachment=True,
                             download_name='Ajutor_VERCON.pdf', max_age=0)
        response.headers.update({'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})
        return response

    nom_routes.register(bp, guard, scoped, open_db)
    doc_routes.register(bp, guard, scoped, open_db)
    bp.scoped = scoped   # the door business routes (VC06+) use
    return bp


vercon_bp = create_blueprint()
