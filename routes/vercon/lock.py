"""SLICE-VC04-04: exclusive access per DC + subunit + gestiune (no year).

One row per (SubunitId, GestiuneId) in VC_ContextLock; its primary key is the rule. The holder is identified by
e-mail AND a random session token (one token per browser tab). Every write and every report/job in a gestiune
presents that token; an old or taken-over token is refused, so a slow request of a former holder cannot write.

  acquire   free -> taken;  held by an expired holder -> taken over;  held by the SAME user in another tab -> only with
            takeover=True (the old tab's token dies); held by somebody else and alive -> Busy.
  heartbeat renews while the token is still the holder's; an expired or replaced token is LockLost.
  release   deletes the row only if the token matches.

The store does the atomic part (conditional INSERT / UPDATE); the service holds the policy. MemoryLockStore serves the
local preview and the contract tests; SqlLockStore runs on the DC database (VC_ContextLock from VC_AVACONT_SURSA.sql).
"""
import secrets
import time

DEFAULT_TTL = 120   # seconds without a heartbeat before the gestiune can be taken over; the page renews every ~40 s


class Busy(Exception):
    def __init__(self, holder_masked):
        super().__init__('Gestiunea este ocupata de alt utilizator.')
        self.holder = holder_masked


class LockLost(Exception):
    pass


class MemoryLockStore:
    def __init__(self, clock=time.time):
        self._rows = {}
        self._clock = clock

    def holder(self, sub, gest):
        row = self._rows.get((sub, gest))
        return None if row is None else {'email': row['email'], 'token': row['token'], 'expired': row['expires'] < self._clock()}

    def insert(self, sub, gest, email, token, ttl):
        if (sub, gest) in self._rows:
            return False
        self._rows[(sub, gest)] = {'email': email, 'token': token, 'expires': self._clock() + ttl}
        return True

    def replace(self, sub, gest, old_token, email, token, ttl, only_if_expired):
        row = self._rows.get((sub, gest))
        if row is None or row['token'] != old_token or (only_if_expired and row['expires'] >= self._clock()):
            return False
        self._rows[(sub, gest)] = {'email': email, 'token': token, 'expires': self._clock() + ttl}
        return True

    def renew(self, sub, gest, token, ttl):
        row = self._rows.get((sub, gest))
        if row is None or row['token'] != token or row['expires'] < self._clock():
            return False
        row['expires'] = self._clock() + ttl
        return True

    def delete(self, sub, gest, token):
        row = self._rows.get((sub, gest))
        if row is None or row['token'] != token:
            return False
        del self._rows[(sub, gest)]
        return True


class SqlLockStore:
    """VC_ContextLock on one DC database. `connect` returns a DB-API connection (autocommit off); every method is one
    atomic statement, committed here. The time is the database's (NOW()), so the app servers' clocks do not matter."""

    def __init__(self, connect):
        self._connect = connect

    def _run(self, sql, params, fetch=False):
        conn = self._connect()
        try:
            cur = conn.cursor(dictionary=True) if fetch else conn.cursor()
            cur.execute(sql, params)
            result = cur.fetchone() if fetch else cur.rowcount
            conn.commit()
            return result
        finally:
            conn.close()

    def holder(self, sub, gest):
        row = self._run('SELECT HolderEmail AS email, SessionToken AS token, ExpiresAt < NOW() AS expired '
                        'FROM VC_ContextLock WHERE SubunitId=%s AND GestiuneId=%s', (sub, gest), fetch=True)
        return None if row is None else {'email': row['email'], 'token': row['token'], 'expired': bool(row['expired'])}

    def insert(self, sub, gest, email, token, ttl):
        import mysql.connector
        try:
            return self._run('INSERT INTO VC_ContextLock (SubunitId, GestiuneId, HolderEmail, SessionToken, ExpiresAt) '
                             'VALUES (%s,%s,%s,%s, NOW() + INTERVAL %s SECOND)', (sub, gest, email, token, ttl)) == 1
        except mysql.connector.IntegrityError:   # 1062: somebody holds it (the primary key is the rule)
            return False

    def replace(self, sub, gest, old_token, email, token, ttl, only_if_expired):
        sql = ('UPDATE VC_ContextLock SET HolderEmail=%s, SessionToken=%s, AcquiredAt=NOW(), RenewedAt=NOW(), '
               'ExpiresAt=NOW() + INTERVAL %s SECOND WHERE SubunitId=%s AND GestiuneId=%s AND SessionToken=%s')
        if only_if_expired:
            sql += ' AND ExpiresAt < NOW()'
        return self._run(sql, (email, token, ttl, sub, gest, old_token)) == 1

    def renew(self, sub, gest, token, ttl):
        return self._run('UPDATE VC_ContextLock SET RenewedAt=NOW(), ExpiresAt=NOW() + INTERVAL %s SECOND '
                         'WHERE SubunitId=%s AND GestiuneId=%s AND SessionToken=%s AND ExpiresAt >= NOW()',
                         (ttl, sub, gest, token)) == 1

    def delete(self, sub, gest, token):
        return self._run('DELETE FROM VC_ContextLock WHERE SubunitId=%s AND GestiuneId=%s AND SessionToken=%s',
                         (sub, gest, token)) == 1


def _mask(email):
    from .diagnostics import mask_email
    return mask_email(email)


class LockService:
    def __init__(self, store, ttl=DEFAULT_TTL):
        self.store, self.ttl = store, ttl

    def acquire(self, sub, gest, email, takeover_own=False):
        token = secrets.token_hex(32)
        if self.store.insert(sub, gest, email, token, self.ttl):
            return token
        held = self.store.holder(sub, gest)
        if held is None:                                   # released between the two statements: try once more
            if self.store.insert(sub, gest, email, token, self.ttl):
                return token
            held = self.store.holder(sub, gest)
        if held is not None and held['expired'] and self.store.replace(sub, gest, held['token'], email, token, self.ttl, True):
            return token
        if held is not None and held['email'] == email and takeover_own and self.store.replace(sub, gest, held['token'], email, token, self.ttl, False):
            return token
        raise Busy(_mask(held['email']) if held else '***')

    def heartbeat(self, sub, gest, token, email):
        # the token alone is not enough: it must be the token of THIS user's holding (a leaked token of another account is useless)
        held = self.store.holder(sub, gest)
        if held is None or held['email'] != email or not self.store.renew(sub, gest, token, self.ttl):
            raise LockLost('Ocuparea gestiunii a expirat sau a fost preluata.')

    def release(self, sub, gest, token, email):
        held = self.store.holder(sub, gest)
        if held is None or held['email'] != email:
            return False
        return self.store.delete(sub, gest, token)

    def validate(self, sub, gest, token, email):
        """A write / report / job needs the CURRENT holder's token. Missing, old, expired or another user's token is refused."""
        held = self.store.holder(sub, gest)
        if not token or held is None or held['expired'] or held['token'] != token or held['email'] != email:
            raise LockLost('Nu detineti ocuparea acestei gestiuni.')
