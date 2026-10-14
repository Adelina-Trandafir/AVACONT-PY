"""SLICE-VC04-02: the repository contract. A ScopedRepository is built ONLY from a context the server verified
(subunit + gestiune [+ exercise] belong to the DC and to each other, the user holds the lock). Every statement it
builds carries the context predicates, so no helper can read or write a row of another gestiune, and an id sent by the
client does not authorise anything by itself: the id is always combined with the context.

Table names come from tables.py (generated); column names are checked against a plain identifier pattern; values are
always parameters. Writes are NOT retried here (a lost answer is handled by the operation key, VC_Operations).
"""
import re

from .tables import EXERCITIU_TABLES, GESTIUNE_TABLES

_IDENT = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')


class ScopeError(Exception):
    pass


class ScopedRepository:
    def __init__(self, cursor, subunit, gestiune, exercitiu=None):
        if not (isinstance(subunit, int) and isinstance(gestiune, int)):
            raise ScopeError('Contextul nu este verificat.')
        self.cursor, self.subunit, self.gestiune, self.exercitiu = cursor, subunit, gestiune, exercitiu

    def _scope(self, table):
        if table in EXERCITIU_TABLES:
            if not isinstance(self.exercitiu, int):
                raise ScopeError('Tabelul %s cere un exercitiu verificat.' % table)
            return 'SubunitId=%s AND GestiuneId=%s AND ExercitiuId=%s', [self.subunit, self.gestiune, self.exercitiu]
        if table in GESTIUNE_TABLES:
            return 'SubunitId=%s AND GestiuneId=%s', [self.subunit, self.gestiune]
        raise ScopeError('Tabel necunoscut: %s' % table)

    @staticmethod
    def _cols(columns):
        for c in columns:
            if not _IDENT.match(c):
                raise ScopeError('Nume de coloana invalid.')
        return list(columns)

    def _where(self, table, where):
        sql, params = self._scope(table)
        for col in self._cols(where):
            sql += ' AND `%s`=%%s' % col
            params.append(where[col])
        return sql, params

    def select(self, table, where=None, columns=('*',), for_update=False):
        cols = '*' if tuple(columns) == ('*',) else ', '.join('`%s`' % c for c in self._cols(columns))
        sql, params = self._where(table, where or {})
        self.cursor.execute('SELECT %s FROM `%s` WHERE %s%s' % (cols, table, sql, ' FOR UPDATE' if for_update else ''), params)
        return self.cursor.fetchall()

    def insert(self, table, values):
        sql, params = self._scope(table)
        ctx_cols = ['SubunitId', 'GestiuneId'] + (['ExercitiuId'] if table in EXERCITIU_TABLES else [])
        cols = self._cols(values)
        if any(c in ctx_cols for c in cols):
            raise ScopeError('Contextul nu se trimite in valori: il pune serverul.')
        names = ctx_cols + cols
        self.cursor.execute('INSERT INTO `%s` (%s) VALUES (%s)' % (table, ', '.join('`%s`' % c for c in names), ', '.join(['%s'] * len(names))),
                            params + [values[c] for c in cols])
        return self.cursor.lastrowid

    def update(self, table, values, where, bump_version=False):
        """`bump_version` adds Version=Version+1 (the revision of the row); put the Version the client saw in `where`
        and a rowcount of 0 means the row changed (or vanished) since."""
        if not where:
            raise ScopeError('UPDATE fara conditie este interzis.')
        sets = self._cols(values)
        if any(c in ('SubunitId', 'GestiuneId', 'ExercitiuId') for c in sets):
            raise ScopeError('Un rand nu isi schimba contextul.')
        wsql, wparams = self._where(table, where)
        assignments = ['`%s`=%%s' % c for c in sets] + (['`Version`=`Version`+1'] if bump_version else [])
        self.cursor.execute('UPDATE `%s` SET %s WHERE %s' % (table, ', '.join(assignments), wsql),
                            [values[c] for c in sets] + wparams)
        return self.cursor.rowcount

    def delete(self, table, where):
        if not where:
            raise ScopeError('DELETE fara conditie este interzis.')
        wsql, wparams = self._where(table, where)
        self.cursor.execute('DELETE FROM `%s` WHERE %s' % (table, wsql), wparams)
        return self.cursor.rowcount
