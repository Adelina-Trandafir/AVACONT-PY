"""SLICE-0123-02: the K-BOT application reads the VERCON reports with its own bearer session.

Same idea as routes/adechit/kbot_bridge.py. The VERCON routes answer a portal session (the site). The application
signs in with a bearer token instead (routes/auth/guard.py). This module lets the SAME routes accept that token,
and only for the calls the Rapoarte page of K-BOT needs: the context (subunit / gestiune / year), the report
catalog, the selection branches, the report life cycle. Everything else (documents, nomenclatures, locks, writes)
stays closed to a bearer token. The rights are unchanged: the e-mail of the bearer session goes through
`drepturi.operations_of(..., 'VR')`, the context is resolved from the user's own subunit tree, and the snapshot
owner is the same tuple as for a site user."""
import re
from flask import g, request

# (method, path pattern); the pattern is matched against the whole path.
_ALLOWED = (
    ('GET', re.compile(r'/api/vercon/context')),
    ('GET', re.compile(r'/api/vercon/reports/catalog')),
    ('GET', re.compile(r'/api/vercon/reports/[A-Za-z0-9_ -]+/(selection|generate)')),
    ('POST', re.compile(r'/api/vercon/reports/[A-Za-z0-9_ -]+/generate')),
    ('GET', re.compile(r'/api/vercon/reports/export/[A-Za-z0-9_-]+/(pdf|xlsx)')),
    ('DELETE', re.compile(r'/api/vercon/reports/export/[A-Za-z0-9_-]+')),
)


def wants(req):
    """True when this request carries a K-BOT bearer token for one of the calls above."""
    if not (req.headers.get('Authorization') or '').startswith('Bearer '):
        return False
    return any(req.method == method and pattern.fullmatch(req.path) for method, pattern in _ALLOWED)


def authenticate(run):
    """Wraps `run` like `require_portal_session` does, with the identity of the bearer session."""
    from routes.auth.guard import require_session

    @require_session
    def bridged():
        session = g.session
        if not session.db_name:
            return {'error': 'Alegeți unitatea.', 'reason': 'UNITATE_NEDESCHISA'}, 409
        g.portal = {'email': session.username, 'db_name': session.db_name, 'role': (session.ctx or {}).get('Role')}
        return run()
    return bridged
