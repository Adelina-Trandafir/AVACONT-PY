"""SLICE-0123-01: the K-BOT application reads the ADECHIT reports with its own bearer session.

The report routes answer a portal session (the site). The application signs in with a bearer token instead
(routes/auth/guard.py). This module lets the SAME routes accept that token, and only for the read calls the
Rapoarte page of K-BOT needs: the subunits, the months, the groups, the child search and the report life cycle.
Everything else (writes, other tables) stays closed to a bearer token. The rights are unchanged: the e-mail of the
bearer session goes through `drepturi.operations_of(..., 'AD')` and through the subunit access list like any user."""
import re
from flask import g, request

# (method, path pattern); the pattern is matched against the whole path.
_ALLOWED = (
    ('GET', re.compile(r'/api/adechit/(context|years|catalog-data|child-search)')),
    ('GET', re.compile(r'/api/adechit/rows/LunaD')),
    ('GET', re.compile(r'/api/adechit/reports/[a-z-]+/generate')),
    ('GET', re.compile(r'/api/adechit/reports/export/[A-Za-z0-9_-]+/(pdf|xlsx)')),
    ('DELETE', re.compile(r'/api/adechit/reports/export/[A-Za-z0-9_-]+')),
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
