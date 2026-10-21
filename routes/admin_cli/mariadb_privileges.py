"""Synchronize real privileges on the K-BOT server from the complete account policy.

Never opens the legacy server or creates a schema/user. GRANT/REVOKE implicitly
commit, so they use a dedicated administrative connection, separate from the
transaction containing application memberships and audit entries.
"""
import logging
from contextlib import contextmanager

import mysql.connector

from routes.admin_cli.admin_common import AdminError, _exec, _journal, _rows
from routes.admin_cli.privilege_policy import build_plan, grant_sql

logger = logging.getLogger(__name__)
LOCK_NAME = 'kbotadmin:mariadb-privileges'


def _config():
    import config
    cfg = getattr(config, 'DB_CONFIG_DREPTURI', None)
    if not cfg:
        raise AdminError('Lipseste DB_CONFIG_DREPTURI. Aplicati manual sql/SEC03_kbotadmin_privilegii.sql '
                         'si configurati contul separat de administrare a drepturilor.')
    cfg = dict(cfg)
    server = config.DB_CONFIG_NEW
    if (cfg.get('host'), int(cfg.get('port', 3306))) != (server.get('host'), int(server.get('port', 3306))):
        raise AdminError('DB_CONFIG_DREPTURI trebuie sa indice acelasi server ca DB_CONFIG_NEW.')
    if not cfg.get('user') or cfg['user'] == server.get('user'):
        raise AdminError('Administrarea drepturilor necesita un cont separat de contul de serviciu.')
    cfg.pop('database', None)
    cfg['autocommit'] = True
    cfg.setdefault('connection_timeout', 10)
    cfg.setdefault('read_timeout', 30)
    cfg.setdefault('write_timeout', 30)
    return cfg


def _protected_names(cfg):
    import config
    names = {'root', 'admin', 'avacont', 'mysql', 'mariadb.sys', 'mysql.sys',
             'mysql.session', 'kbot_provizionare', 'kbot_drepturi', cfg['user'].casefold()}
    for name in dir(config):
        value = getattr(config, name)
        if (name.startswith('DB_CONFIG') or name.endswith('DB_CONFIG')) and isinstance(value, dict):
            names.add(str(value.get('user', '')).casefold())
    admins = getattr(config, 'PORTAL_ADMIN_EMAILS', ()) or ()
    if isinstance(admins, str):
        admins = admins.split(',')
    names.update(str(value).strip().casefold() for value in admins)
    extra = getattr(config, 'DREPTURI_CONTURI_PROTEJATE', ()) or ()
    if isinstance(extra, str):
        extra = extra.split(',')
    names.update(str(value).strip().casefold() for value in extra)
    # The owner's default account must never be normalized as an operator.
    names.add('scavatarsoft@gmail.com')
    return names


def _preflight(conn, username):
    accounts = _rows(conn, "SELECT User, Host FROM mysql.user WHERE BINARY User=BINARY %s AND is_role='N' "
                     "ORDER BY Host", (username,))
    if not accounts:
        raise AdminError('Contul MariaDB %s nu exista. Creati-l intai; KBOTAdmin nu creeaza utilizatori aici.' % username)
    # REVOKE ALL does not remove inherited roles or proxy identities. Refuse BEFORE
    # touching the account rather than claiming it is limited while inheritance survives.
    for account in accounts:
        params = (account['User'], account['Host'])
        roles = _rows(conn, 'SELECT Role FROM mysql.roles_mapping WHERE BINARY User=BINARY %s AND Host=%s', params)
        proxies = _rows(conn, 'SELECT Proxied_user FROM mysql.proxies_priv WHERE BINARY User=BINARY %s AND Host=%s', params)
        if roles or proxies:
            raise AdminError('Contul %s@%s are roluri MariaDB sau PROXY. Retrageti-le manual inainte de '
                             'sincronizare; nu s-a schimbat nimic.' % params)
    public = _rows(conn, "SELECT 1 AS x FROM mysql.user WHERE User='PUBLIC' AND is_role='Y'")
    if public:
        for row in _rows(conn, 'SHOW GRANTS FOR PUBLIC'):
            statement = str(next(iter(row.values())))
            if not statement.upper().startswith('GRANT USAGE ON *.* TO '):
                raise AdminError('Rolul PUBLIC acorda privilegii mostenite. Limitati-l manual inainte de sincronizare.')
    return accounts


def _transactional_metadata(conn):
    required = {'UNITATI_UTILIZATORI', 'UTILIZATORI_ROLURI', 'JURNAL'}
    tables = _rows(conn, 'SELECT TABLE_NAME, ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s',
                   ('AVACONT_COMUN',))
    safe = {r['TABLE_NAME'].upper() for r in tables if (r['ENGINE'] or '').upper() in {'INNODB', 'XTRADB'}}
    if required - safe:
        raise AdminError('Sincronizarea necesita tabele tranzactionale InnoDB in AVACONT_COMUN: %s. '
                         'Nu s-a schimbat nimic.' % ', '.join(sorted(required - safe)))


def _kill_connections(conn, username):
    """Global privileges are cached by old sessions; require a new MariaDB login."""
    rows = _rows(conn, 'SELECT ID FROM information_schema.PROCESSLIST WHERE BINARY USER=BINARY %s', (username,))
    for row in rows:
        try:
            _exec(conn, 'KILL CONNECTION %d' % int(row['ID']))
        except mysql.connector.Error as error:
            if error.errno != 1094:  # the session may have closed since the SELECT
                raise
    return len(rows)


class PrivilegeChange:
    def __init__(self, app_conn, privilege_conn, username, accounts):
        self.app_conn = app_conn
        self.privilege_conn = privilege_conn
        self.username = username
        self.accounts = accounts
        self.started = False
        self.commit_attempted = False
        self.finished = False

    def _revoke_all(self):
        for account in self.accounts:
            _exec(self.privilege_conn, 'REVOKE ALL PRIVILEGES, GRANT OPTION FROM %s@%s',
                  (account['User'], account['Host']))

    def finish(self):
        # Finish all policy reads before withdrawing anything. Membership edits are
        # still uncommitted and are visible only on app_conn.
        grants = build_plan(self.app_conn, self.privilege_conn, self.username)
        self.started = True
        self._revoke_all()
        for account in self.accounts:
            for db, table, rights in grants:
                _exec(self.privilege_conn, grant_sql(db, table, rights), (account['User'], account['Host']))
        disconnected = _kill_connections(self.privilege_conn, self.username)
        summary = {'hosts': [a['Host'] for a in self.accounts], 'tables': len(grants),
                   'databases': sorted({g[0] for g in grants}), 'disconnected': disconnected}
        _journal(self.app_conn, 'ADMIN_SQL_SYNC', None, self.username,
                 'hosturi=%d; tabele=%d; conexiuni inchise=%d' % (len(self.accounts), len(grants), disconnected))
        self.commit_attempted = True
        self.app_conn.commit()
        self.finished = True
        return summary

    def abort(self):
        metadata_uncertain = self.commit_attempted
        try:
            self.app_conn.rollback()
        except Exception:
            metadata_uncertain = True
            logger.exception('Application role rollback could not be confirmed')
        if not self.started:
            return ('Starea rolurilor nu poate fi confirmata. Reincarcati conturile si reaplicati politica.'
                    if metadata_uncertain else None)
        # Never restore the original ALL PRIVILEGES. After a partial failure try
        # every host, even if one fails. The operator can reapply the full policy.
        incomplete = False
        for account in self.accounts:
            try:
                _exec(self.privilege_conn, 'REVOKE ALL PRIVILEGES, GRANT OPTION FROM %s@%s',
                      (account['User'], account['Host']))
            except Exception:
                incomplete = True
                logger.exception('Privilege cleanup failed for %s@%s', account['User'], account['Host'])
        try:
            _kill_connections(self.privilege_conn, self.username)
        except Exception:
            incomplete = True
            logger.exception('Privilege cleanup could not disconnect %s', self.username)
        detail = ('Retragerea privilegiilor nu s-a terminat; interventie DBA necesara.' if incomplete else
                  'Privilegiile SQL au fost retrase temporar. Reaplicati drepturile MariaDB pentru acest cont.')
        detail = (('Starea salvarii rolurilor nu poate fi confirmata; reincarcati conturile. ' if metadata_uncertain else
                   'Modificarile rolurilor au fost anulate. ') + detail)
        try:
            _journal(self.app_conn, 'ADMIN_SQL_SYNC', None, self.username, detail, 'EROARE')
            self.app_conn.commit()
        except Exception:
            logger.exception('Privilege failure audit could not be saved')
        return detail


@contextmanager
def changing(app_conn, username):
    cfg = _config()
    if username.casefold() in _protected_names(cfg):
        raise AdminError('Cont protejat: %s. Drepturile sale se administreaza separat de conturile de lucru.' % username)
    conn = mysql.connector.connect(**{**cfg, "time_zone": "+00:00"})
    locked = False
    change = None
    try:
        # Prevent GRANT from implicitly recreating an account deleted concurrently.
        mode = _rows(conn, 'SELECT @@SESSION.sql_mode AS mode')[0]['mode'] or ''
        modes = set(mode.split(',')) | {'NO_AUTO_CREATE_USER'}
        _exec(conn, 'SET SESSION sql_mode=%s', (','.join(sorted(modes - {''})),))
        locked = _rows(conn, 'SELECT GET_LOCK(%s, 5) AS acquired', (LOCK_NAME,))[0]['acquired'] == 1
        if not locked:
            raise AdminError('O alta schimbare de drepturi este in curs. Reincercati.')
        accounts = _preflight(conn, username)
        _transactional_metadata(app_conn)
        app_conn.autocommit = False
        change = PrivilegeChange(app_conn, conn, username, accounts)
        yield change
        if not change.finished:
            raise AdminError('Sincronizarea MariaDB nu a fost finalizata.')
    except Exception as error:
        if change is not None:
            detail = change.abort()
            if detail:
                raise AdminError('Sincronizarea MariaDB a esuat. ' + detail) from error
        raise
    finally:
        if locked:
            try:
                _rows(conn, 'SELECT RELEASE_LOCK(%s) AS released', (LOCK_NAME,))
            except Exception:
                logger.exception('Could not release the privilege synchronization lock')
        conn.close()
