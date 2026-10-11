"""SLICE-AD11: isolated read-only parent sessions; operator credentials are never accepted."""
import hashlib
import hmac
import logging
import re
import secrets
import time
from functools import wraps
from io import BytesIO
from flask import Blueprint, Response, current_app, jsonify, request, send_from_directory, render_template, send_file
from routes.auth import mailer
from routes.auth.ratelimit import RateLimiter
from routes.auth.session_store import STORE, SessionStore, RedisSessionStore
from .domain import DomainError, require, cnp_code
from .repository import transaction
from .parent_statement import child_data, monthly_rows, make_statement, statement_pdf, issuer_data

logger = logging.getLogger(__name__)
COOKIE = 'ade_parent_session'
GENERIC = 'Dacă datele sunt corecte și accesul este disponibil, veți primi un cod pe email.'


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def send_portal_mail(address, subject, body):
    conf = mailer._smtp_config()
    from email.message import EmailMessage
    message = EmailMessage()
    message['From'] = conf['sender']; message['To'] = address; message['Subject'] = subject
    message['Message-ID'] = mailer._message_id(conf)
    message.set_content(body)
    mailer._deliver(message, conf)


def portal_url(unit):
    # Deployment can configure the canonical public origin; localhost uses its own origin.
    import config
    base = str(getattr(config, 'ADE_PARENT_PUBLIC_URL', '') or request.url_root).rstrip('/')
    from urllib.parse import quote
    return base + '/adechit/parinti/' + quote(unit, safe='')


def send_access(repo, parent_id, unit, deliver=None):
    parent = repo.get('Platitori_sub', parent_id)
    from .parent_identity import identity_conflicted
    require(not identity_conflicted(repo, str(parent.get('CNP_Platitor') or '').strip()),
            'PORTAL_CONFLICT', 'Accesul în portal este blocat. Corectați conflictele CNP/email din fereastra Plătitori.', 409)
    child = repo.get('Platitori', parent['IDP'])
    require(not child.get('Plecat'), 'DEPARTED', 'Copilul este plecat; accesul în portal nu este disponibil.', 409)
    require(cnp_code(parent.get('CNP_Platitor')) == -1 and parent.get('EMail') and parent.get('CodAccesPortal'),
            'PORTAL_DATA', 'Completați CNP-ul și emailul părintelui înainte de trimiterea codului.', 409)
    (deliver or send_portal_mail)(parent['EMail'], 'ADECHIT - Codul de acces în portal',
        f"Bună ziua,\n\nCodul dumneavoastră de acces: {parent['CodAccesPortal']}\n"
        f"Portal: {portal_url(unit)}\n\nFolosiți CNP-ul și acest cod. La conectare veți primi un cod temporar pe email.")
    return {'message': 'Codul de acces a fost trimis pe email.'}


def create_parent_blueprint(repository_factory=None, deliver=None, preview_units=None):
    bp = Blueprint('ade_parents', __name__, template_folder='../../templates')
    limiter = RateLimiter()
    # Same session infrastructure, distinct namespace: parent tokens cannot reach operator APIs.
    sessions = RedisSessionStore(STORE._r, STORE._prefix + 'ade-parents:') if isinstance(STORE, RedisSessionStore) else SessionStore()
    preview = repository_factory is not None

    def resolve_unit(unit):
        require(isinstance(unit, str) and re.fullmatch(r'[A-Za-z0-9_]{1,64}', unit), 'UNIT', 'Unitate invalidă.', 404)
        if preview:
            require(unit in preview_units, 'UNIT', 'Unitate necunoscută.', 404)
        else:
            from utils.database import get_kbot_comun_connection
            connection = get_kbot_comun_connection()
            try:
                cursor = connection.cursor()
                try:
                    cursor.execute('SELECT DC FROM Unitati WHERE DC=%s', (unit,))
                    require(cursor.fetchone() is not None, 'UNIT', 'Unitate necunoscută.', 404)
                finally:
                    cursor.close()
            finally:
                connection.close()
        return unit

    def boundary(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            try:
                if preview:
                    require(request.remote_addr in ('127.0.0.1','::1'), 'LOCAL', 'Disponibil numai local.', 403)
                if request.method == 'POST':
                    require(request.headers.get('X-Requested-With') == 'ADEParentPortal', 'ORIGIN', 'Cerere invalidă.', 403)
                    origin = request.headers.get('Origin')
                    require(not origin or origin == request.host_url.rstrip('/'), 'ORIGIN', 'Origine invalidă.', 403)
                result = function(*args, **kwargs)
                response = result if isinstance(result, Response) else jsonify(result)
            except DomainError as error:
                response = jsonify(error=str(error), reason=error.reason); response.status_code = error.status
            except mailer.MailNotConfigured:
                logger.exception('Parent mail is not configured')
                response = jsonify(error='Trimiterea emailurilor nu este configurată.', reason='SMTP'); response.status_code=503
            except Exception:
                logger.exception('Parent portal request failed')
                response = jsonify(error='Operația nu a fost finalizată. Reîncercați.', reason='SERVER'); response.status_code=500
            response.headers.update({'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer',
                'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"})
            return response
        return wrapped

    def eligible(repo, cnp, email, access_hash):
        from .parent_identity import identity_conflicted
        if identity_conflicted(repo, cnp):
            return []
        rows = repo.query('SELECT p.*,c.Nume AS ChildName,c.IDG,g.Grupa,c.DataIntrare FROM AD_Platitori_sub p '
            'JOIN AD_Platitori c ON c.IDP=p.IDP AND c.SubunitId=p.SubunitId '
            'JOIN AD_Subunits s ON s.SubunitId=p.SubunitId AND s.Active=1 '
            'LEFT JOIN AD_Grupe g ON g.IDG=c.IDG AND g.SubunitId=c.SubunitId '
            'JOIN AD_PortalParents i ON i.CNP=TRIM(p.CNP_Platitor) '
            'WHERE i.CNP=%s AND LOWER(TRIM(p.EMail))=%s AND i.Email=%s AND p.CodAccesPortal=i.CodAccesPortal '
            'AND COALESCE(c.Plecat,0)=0', (cnp,email,email))
        return [r for r in rows if r.get('CodAccesPortal') and hmac.compare_digest(digest(r['CodAccesPortal']), access_hash)]

    def authenticated(function):
        @wraps(function)
        def wrapped(unit, *args, **kwargs):
            session = sessions.validate_and_touch(request.cookies.get(COOKIE))
            require(session is not None and session.db_name == unit, 'SESSION', 'Sesiunea a expirat. Conectați-vă din nou.', 401)
            with transaction(unit, repository_factory, optional_subunit=True) as repo:
                parents = eligible(repo, session.ctx['cnp'], session.ctx['email'], session.ctx['access_hash'])
                require(parents, 'SESSION', 'Accesul nu mai este disponibil. Conectați-vă din nou.', 401)
                return function(repo, parents, unit, *args, **kwargs)
        return wrapped

    @bp.get('/adechit/parinti/<unit>')
    @boundary
    def page(unit):
        resolve_unit(unit)
        return send_from_directory(current_app.static_folder,'adechit-parents.html')

    @bp.post('/api/adechit/parinti/<unit>/code')
    @boundary
    def code(unit):
        resolve_unit(unit)
        body = request.get_json(silent=True) or {}
        cnp = str(body.get('cnp') or '').strip(); access = str(body.get('access_code') or '').strip()
        ip = request.remote_addr; identity_key = digest(unit + ':' + cnp)
        require(not limiter.is_blocked(ip,identity_key), 'RATE', 'Prea multe solicitări. Reîncercați peste 15 minute.',429)
        # Count successes as well: repeatedly sending valid codes must not flood an inbox.
        limiter.record_failure(ip,identity_key)
        challenge = secrets.token_urlsafe(32); now = int(time.time()); address = None
        if cnp_code(cnp) == -1 and 16 <= len(access) <= 64:
            with transaction(unit,repository_factory,optional_subunit=True) as repo:
                identities = repo.query('SELECT * FROM AD_PortalParents WHERE CNP=%s',(cnp,))
                parent = identities[0] if identities else None
                if parent and parent['Email'] and parent['CodAccesPortal'] and hmac.compare_digest(parent['CodAccesPortal'],access):
                    parents = eligible(repo,cnp,parent['Email'],digest(access))
                    recent = repo.query('SELECT ChallengeId FROM AD_PortalChallenges WHERE CNP=%s AND CreatedAt>%s ORDER BY CreatedAt DESC',(cnp,now-60))
                    if parents and recent:
                        challenge = recent[0]['ChallengeId']
                    if parents and not recent:
                        otp = f'{secrets.randbelow(1000000):06d}'; address=parent['Email']
                        repo.execute('DELETE FROM AD_PortalChallenges WHERE ExpiresAt<%s OR CNP=%s',(now,cnp))
                        repo.execute('INSERT INTO AD_PortalChallenges (ChallengeId,CNP,Email,AccessHash,CodeHash,ExpiresAt,Attempts,CreatedAt) '
                            'VALUES (%s,%s,%s,%s,%s,%s,0,%s)',(challenge,cnp,address,digest(access),digest(challenge+otp),now+300,now))
        if address:
            try:
                (deliver or send_portal_mail)(address,'ADECHIT - Cod de conectare',f'Codul de conectare: {otp}\nValabil 5 minute, pentru o singură utilizare.')
            except Exception:
                with transaction(unit,repository_factory,optional_subunit=True) as repo:
                    repo.execute('DELETE FROM AD_PortalChallenges WHERE ChallengeId=%s',(challenge,))
                raise
        return {'challenge':challenge,'message':GENERIC}

    @bp.post('/api/adechit/parinti/<unit>/verify')
    @boundary
    def verify(unit):
        resolve_unit(unit)
        body=request.get_json(silent=True) or {}; challenge=str(body.get('challenge') or ''); otp=str(body.get('code') or '')
        require(20 <= len(challenge) <= 64 and re.fullmatch(r'[0-9]{6}',otp), 'CODE', 'Cod invalid sau expirat.',401)
        ip=request.remote_addr; key=digest(unit+':challenge:'+challenge)
        require(not limiter.is_blocked(ip,key), 'RATE','Prea multe încercări. Reîncercați peste 15 minute.',429)
        identity=None
        with transaction(unit,repository_factory,optional_subunit=True) as repo:
            rows=repo.query('SELECT * FROM AD_PortalChallenges WHERE ChallengeId=%s',(challenge,))
            pending=rows[0] if rows else None
            if pending and pending['ExpiresAt']>time.time() and pending['Attempts']<5:
                repo.execute('UPDATE AD_PortalChallenges SET Attempts=Attempts+1 WHERE ChallengeId=%s',(challenge,))
                if hmac.compare_digest(pending['CodeHash'],digest(challenge+otp)) and eligible(repo,pending['CNP'],pending['Email'],pending['AccessHash']):
                    identity={'cnp':pending['CNP'],'email':pending['Email'],'access_hash':pending['AccessHash']}
                    repo.execute('DELETE FROM AD_PortalChallenges WHERE ChallengeId=%s',(challenge,))
        if identity is None:
            limiter.record_failure(ip,key)
            raise DomainError('CODE','Cod invalid sau expirat.',401)
        token,_=sessions.create(identity['email'],'',0,unit,identity,'')
        response=jsonify(message='Conectare reușită.')
        response.set_cookie(COOKIE,token,httponly=True,secure=not preview,samesite='Strict',path='/')
        logger.info('ADE parent login success unit=%s',unit)
        return response

    @bp.post('/api/adechit/parinti/<unit>/logout')
    @boundary
    def logout(unit):
        sessions.revoke(request.cookies.get(COOKIE))
        response=jsonify(message='Deconectat.'); response.delete_cookie(COOKIE,path='/'); return response

    @bp.get('/api/adechit/parinti/<unit>/children')
    @boundary
    @authenticated
    def children(repo,parents,unit):
        unique={p['IDP']:{'id':p['IDP'],'name':p['ChildName'],'group':p['Grupa'] or ''} for p in parents}
        return {'children':sorted(unique.values(),key=lambda p:p['name'].casefold()),'preview':preview}

    def select_child(repo,parents,child_id):
        parent=next((p for p in parents if p['IDP']==child_id),None)
        require(parent is not None,'CHILD','Copilul nu este disponibil.',404)
        repo.subunit_id=parent['SubunitId']
        return parent

    @bp.get('/api/adechit/parinti/<unit>/child/<int:child_id>')
    @boundary
    @authenticated
    def dashboard(repo,parents,unit,child_id):
        parent=select_child(repo,parents,child_id)
        data=child_data(repo,child_id); rows=monthly_rows(data)
        return {'name':parent['ChildName'],'group':parent['Grupa'] or '', 'months':rows,
            'balance':rows[-1]['closing'] if rows else data['Platitori'][0].get('SI') or 0,
            'days':sum(r['days'] for r in rows),'payments':sum(r['payments'] for r in rows),'refunds':sum(r['refunds'] for r in rows)}

    @bp.get('/api/adechit/parinti/<unit>/child/<int:child_id>/statement/<output>')
    @boundary
    @authenticated
    def statement(repo,parents,unit,child_id,output):
        parent=select_child(repo,parents,child_id)
        require(output in ('print','pdf'),'OUTPUT','Format necunoscut.',404)
        start=end=None
        require(request.args.get('interval','0') in ('0','1'),'DATE','Interval invalid.',400)
        if request.args.get('interval')=='1':
            from datetime import date
            try:
                start=date.fromisoformat(request.args.get('start','')).isoformat()
                end=date.fromisoformat(request.args.get('end','')).isoformat()
            except ValueError as error:
                raise DomainError('DATE','Completați ambele date ale intervalului.',400) from error
            require(start<=end,'DATE','Data început nu poate depăși data sfârșit.',400)
        data=child_data(repo,child_id)
        monthly_rows(data) # refuse incomplete closed histories in both outputs
        report=make_statement(data,parent,issuer_data(repo,unit),start,end)
        if output=='print':
            return Response(render_template('adechit/statement.html',statement=report),mimetype='text/html')
        return send_file(BytesIO(statement_pdf(report)),mimetype='application/pdf',as_attachment=True,download_name='Fisa_cont.pdf',max_age=0)

    return bp


parent_portal_bp=create_parent_blueprint()
