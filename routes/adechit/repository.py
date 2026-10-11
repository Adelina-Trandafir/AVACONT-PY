"""SLICE-ADE3/8/10: shared DB connections; one subunit lock serializes all ADE mutations of that subunit.

The ADE context is DC + subunit (SLICE-ADE10). Every table access below is limited to the repository's
subunit, so no helper can read or write a row of another subunit of the same DC.

Lock order: AD_Lock(subunit) -> permission -> business rows -> receipt configuration of the subunit.
No automatic retry around business writes; operation keys handle lost responses.
"""
from contextlib import contextmanager
from datetime import date, datetime
from .domain import SCHEMA, require


class Repository:
    def __init__(self, connection, sqlite=False):
        self.connection = connection
        self.sqlite = sqlite
        self.cursor = connection.cursor() if sqlite else connection.cursor(dictionary=True)
        self.subunit_id = None
        self.subunit_name = ''
        self.subunits = []

    def execute(self, sql, params=()):
        if self.sqlite:
            sql = sql.replace('%s', '?').replace(' FOR UPDATE', '')
        self.cursor.execute(sql, params)
        return self.cursor

    def query(self, sql, params=()):
        return [{key: value.isoformat() if isinstance(value, (date, datetime)) else value
                 for key, value in dict(row).items()} for row in self.execute(sql, params).fetchall()]

    # ---- subunit context ---------------------------------------------------------------------------------

    def scope(self):
        """The subunit every query is limited to; refuses to run without one."""
        require(self.subunit_id is not None, 'SUBUNIT_REQUIRED', 'Alegeți subunitatea în care lucrați.', 409)
        return int(self.subunit_id)

    def load_subunits(self, email=None):
        # execute(), not query(): this is context resolution, not a business read.
        # Allowed = the subunit has no access list (open to the DC) or the list names this e-mail.
        rows = self.execute('SELECT s.SubunitId, s.Name, s.Active, '
                            '(NOT EXISTS (SELECT 1 FROM AD_SubunitAccess a WHERE a.SubunitId=s.SubunitId) '
                            'OR EXISTS (SELECT 1 FROM AD_SubunitAccess a WHERE a.SubunitId=s.SubunitId AND a.Email=%s)) AS Allowed '
                            'FROM AD_Subunits s ORDER BY s.Name', (email or '',)).fetchall()
        self.subunits = [dict(row) for row in rows]
        return self.subunits

    def select_subunit(self, reference, optional=False):
        """Validate the subunit the client sent against this DC. A missing reference is accepted only when the DC
        has exactly one active subunit (older pages, single-evidence units)."""
        active = [row for row in self.subunits if row['Active'] and row['Allowed']]
        chosen = None
        if reference not in (None, ''):
            require(not any(str(row['SubunitId']) == str(reference) and row['Active'] and not row['Allowed'] for row in self.subunits),
                    'SUBUNIT_FORBIDDEN', 'Nu aveți acces la subunitatea selectată.', 403)
            require(str(reference).isdigit(), 'SUBUNIT_CONTEXT',
                    'Subunitatea selectată nu mai este disponibilă. Reîncărcați pagina.', 409)
            chosen = next((row for row in active if row['SubunitId'] == int(reference)), None)
            require(chosen is not None, 'SUBUNIT_CONTEXT',
                    'Subunitatea selectată nu mai este disponibilă. Reîncărcați pagina.', 409)
        elif len(active) == 1:
            chosen = active[0]
        elif not optional:
            require(bool(active), 'SUBUNIT_NONE', 'Unitatea nu are nicio subunitate. Importați evidența cu ADE.Migrator.', 409)
            require(False, 'SUBUNIT_REQUIRED', 'Alegeți subunitatea în care lucrați.', 409)
        if chosen is not None:
            self.subunit_id = int(chosen['SubunitId'])
            self.subunit_name = chosen['Name']
        return chosen

    # ---- scoped table access -----------------------------------------------------------------------------

    def rows(self, table, **filters):
        require(table in SCHEMA, 'TABLE', 'Tabel necunoscut.', 404)
        require(set(filters) <= set(SCHEMA[table]['fields']), 'FILTER', 'Filtru invalid.', 400)
        where = ' WHERE `SubunitId`=%s' + ''.join(f' AND `{key}`=%s' for key in filters)
        return self.query(f'SELECT * FROM `AD_{table}`{where} ORDER BY `{SCHEMA[table]["key"]}`',
                          (self.scope(), *filters.values()))

    def month_groups(self, month_id, include_hidden=False):
        # Use attendance's historical group, not the child's current group after a transfer.
        hidden = '' if include_hidden else ' AND g.Ascunsa=0'
        return self.query('SELECT g.* FROM AD_Grupe g WHERE g.SubunitId=%s' + hidden
                          + ' AND EXISTS (SELECT 1 FROM AD_Prezenta p WHERE p.SubunitId=g.SubunitId'
                          + ' AND p.IDG=g.IDG AND p.IDL=%s AND p.IDP IS NOT NULL) ORDER BY g.IDG',
                          (self.scope(), month_id))

    def years(self):
        return [row['Anul'] for row in self.query('SELECT DISTINCT Anul FROM AD_LunaD WHERE SubunitId=%s ORDER BY Anul DESC',
                                                  (self.scope(),))]

    def situation_data(self, month_id, group_id):
        # Keep all earlier movements of the selected children for opening balances,
        # including movements in groups they previously belonged to.
        sub = self.scope()  # an int, safe to write into the statement text
        people = f'SELECT IDP FROM AD_Prezenta WHERE SubunitId={sub} AND IDL=%s AND IDG=%s'
        attendance = f'SELECT IDZ FROM AD_Prezenta WHERE SubunitId={sub} AND IDP IN ({people})'
        payments = f'SELECT IDPL FROM AD_Plati WHERE SubunitId={sub} AND IDZ IN ({attendance})'
        data = {table: [] for table in SCHEMA}
        data['LunaD'] = [self.get('LunaD', month_id)]
        data['Grupe'] = [self.get('Grupe', group_id)]
        data['Grupe_Educator'] = self.rows('Grupe_Educator', IDG=group_id)
        for table, clause in [('Platitori', f'IDP IN ({people})'),
                              ('Prezenta', f'IDP IN ({people})'),
                              ('Plati', f'IDZ IN ({attendance})'),
                              ('Retur', f'IDP IN ({people})'),
                              ('Chitante', f'IDPL IN ({payments})'),
                              ('AlteDoc', f'IDPL IN ({payments})')]:
            data[table] = self.query(f'SELECT * FROM `AD_{table}` WHERE SubunitId={sub} AND {clause}', (month_id, group_id))
        return data

    def ledger_rows(self, attendance_id, month_id, kind):
        attendance = self.get('Prezenta', attendance_id)
        require(attendance['IDL'] == month_id, 'CONTEXT_CHANGED', 'Prezența nu aparține lunii selectate.', 409)
        if kind == 'refund':
            # Legacy Access refunds may have no IDL: the attendance row (checked above to belong to this month) fixes the month.
            return [{**row, 'Cancelled': bool(row.get('Anulat'))}
                    for row in self.rows('Retur', IDZ=attendance_id)]
        table = 'Chitante' if kind == 'receipt' else 'AlteDoc'
        # Legacy Access documents have no IDL: their month is the linked payment's.
        rows = self.query(f'SELECT d.*, p.Plata AS Valoare, p.IDS AS PayerId, p.Anulata AS PaymentCancelled '
                          f'FROM AD_{table} d JOIN AD_Plati p ON p.IDPL=d.IDPL AND p.SubunitId=d.SubunitId '
                          'WHERE p.SubunitId=%s AND p.IDZ=%s',
                          (self.scope(), attendance_id))
        for row in rows:
            payment_cancelled = row.pop('PaymentCancelled', False)
            row['Cancelled'] = bool(row.get('Anulata') or payment_cancelled)
        return rows

    def get(self, table, row_id):
        key = SCHEMA[table]['key']
        result = self.query(f'SELECT * FROM `AD_{table}` WHERE `SubunitId`=%s AND `{key}`=%s', (self.scope(), row_id))
        require(bool(result), 'NOT_FOUND', 'Înregistrarea nu există în subunitatea curentă.', 404)
        return result[0]

    def insert(self, table, values):
        require(table in SCHEMA and set(values) <= set(SCHEMA[table]['fields']), 'FIELDS', 'Câmpuri necunoscute.', 400)
        keys = ['SubunitId', *values]
        self.execute(f'INSERT INTO `AD_{table}` (' + ','.join(f'`{k}`' for k in keys) + ') VALUES (' + ','.join(['%s'] * len(keys)) + ')',
                     (self.scope(), *(values[k] for k in values)))
        return self.get(table, values.get(SCHEMA[table]['key'], self.cursor.lastrowid))

    def update(self, table, row, changes):
        require(bool(changes), 'VALUES', 'Nu există modificări.', 400)
        require(set(changes) <= set(SCHEMA[table]['fields']), 'FIELDS', 'Câmpuri necunoscute.', 400)
        key = SCHEMA[table]['key']
        self.execute(f'UPDATE `AD_{table}` SET ' + ','.join(f'`{k}`=%s' for k in changes) + ', Version=Version+1 '
                     f'WHERE `SubunitId`=%s AND `{key}`=%s AND Version=%s', (*changes.values(), self.scope(), row[key], row['Version']))
        require(self.cursor.rowcount == 1, 'CONFLICT', 'Rândul a fost modificat. Reîncărcați înainte de a reîncerca.')
        return self.get(table, row[key])

    def delete(self, table, row):
        require(table in SCHEMA, 'TABLE', 'Tabel necunoscut.', 404)
        key = SCHEMA[table]['key']
        self.execute(f'DELETE FROM `AD_{table}` WHERE `SubunitId`=%s AND `{key}`=%s AND Version=%s',
                     (self.scope(), row[key], row['Version']))
        require(self.cursor.rowcount == 1, 'CONFLICT', 'Rândul a fost modificat. Reîncărcați înainte de a reîncerca.')

    def setting(self, key, default=None):
        rows = self.query('SELECT SettingValue FROM AD_Settings WHERE SubunitId=%s AND SettingKey=%s', (self.scope(), key))
        return rows[0]['SettingValue'] if rows else default

    def receipt_config(self, lock=False):
        """The receipt series of the current subunit; None when the subunit has none yet."""
        rows = self.query('SELECT * FROM AD_ReceiptConfig WHERE SubunitId=%s' + (' FOR UPDATE' if lock else ''), (self.scope(),))
        return rows[0] if rows else None

    def data(self):
        return {table: self.rows(table) for table in SCHEMA}


@contextmanager
def transaction(db_name, factory=None, subunit=None, optional_subunit=False, email=None):
    """One ADE transaction. `subunit` is the id the client sent (X-Ade-Subunit); it is validated against the
    subunits of `db_name` before anything is locked or read."""
    if factory is None:
        from utils.database import get_kbot_connection
        connection = get_kbot_connection(db_name)
        repo = Repository(connection)
    else:
        repo = factory(db_name)
        connection = repo.connection
    try:
        if repo.sqlite:
            connection.execute('BEGIN IMMEDIATE')
            repo.load_subunits(email)
        else:
            # Resolve the subunit in a short read of its own and END it: under REPEATABLE READ the first plain read
            # fixes the transaction snapshot, and a request that then waits for the lock would go on with old data.
            repo.load_subunits(email)
            connection.commit()
            connection.start_transaction()
        chosen = repo.select_subunit(subunit, optional=optional_subunit)
        # Unit-wide parent identity precedes every subunit lock, including migration.
        repo.execute('INSERT IGNORE INTO AD_Lock (ID) VALUES (0)' if not repo.sqlite else 'INSERT OR IGNORE INTO AD_Lock (ID) VALUES (0)')
        repo.execute('SELECT ID FROM AD_Lock WHERE ID=0 FOR UPDATE').fetchall()
        if chosen is not None:
            # The lock row of a subunit has the subunit's id; create it for subunits made after AD_05.
            repo.execute('INSERT IGNORE INTO AD_Lock (ID) VALUES (%s)' if not repo.sqlite else 'INSERT OR IGNORE INTO AD_Lock (ID) VALUES (%s)',
                         (repo.subunit_id,))
            repo.execute('SELECT ID FROM AD_Lock WHERE ID=%s FOR UPDATE', (repo.subunit_id,)).fetchall()
        yield repo
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        repo.cursor.close()
        connection.close()
