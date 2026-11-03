"""Duplicate Clasificatii rows for the private KBotAdmin CLI: find them, and delete the ones nothing important points to.

A duplicate is a second (third, ...) row with the same unit and the same four parts of the code (Capitol, Subcapitol, Articol,
Alineat) in the `Clasificatii` table of ONE unit database; they appear when the classifications are transferred more than once.

A row is LINKED when its IDClsf is held by any other table of the database (every column called IdClsf, plus every column a
foreign key points from to Clasificatii.IDClsf, in any schema): FX_, EF_ and so on. Linked rows are never touched.
The exceptions are Clasificatii_Rectificari and Clasificatii_Buget (operator's rule, 03.11.2026: a link only from these two does
NOT protect a row; a link from FX_ or EF_ does): the row is deleted together with its rows in those two tables, which are written
to the Jurnal first.
Of the rows of a group that are not linked, all go, except that a group with no linked row at all keeps one (the one with a real
IdClsfAcc, then the oldest).
`check` only reads; `repair` WRITES: one transaction, the candidate rows locked, the links re-read under the lock, every deleted
row written to the Jurnal, and the transaction refused if any other linked table lost a row (a cascade nobody expected).
"""
from collections import defaultdict

from routes.admin_cli.admin_common import COMMON_DB, AdminError, _exec, _journal, _q, _rows

TITLE = 'Clasificatii dublate'
SUMMARY_TITLE = TITLE + ' - sumar'
DETAIL_TITLE = TITLE + ' - detaliu'
REPAIR_COLUMN = 'reparabile'
TABLE = 'Clasificatii'
# A link from these tables alone does not protect a row: the row goes, and so do its rows in these tables.
SOFT_TABLES = {
    'clasificatii_rectificari': {'table': 'Clasificatii_Rectificari', 'pk': 'ID'},
    'clasificatii_buget': {'table': 'Clasificatii_Buget', 'pk': 'IdBuget'},
}
REQUIRED = 'IDClsf IdClsfAcc IdUnitate Capitol Subcapitol Articol Alineat Denumire'.split()
KEY_PARTS = ('IdUnitate', 'Capitol', 'Subcapitol', 'Articol', 'Alineat')
CHUNK = 500
DETAIL_LIMIT = 1000


def _norm(value):
    return '' if value is None else str(value).rstrip().casefold()


def _has_table(conn, db):
    have = {r['c'].casefold() for r in _rows(
        conn, 'SELECT COLUMN_NAME AS c FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s', (db, TABLE))}
    return [c for c in REQUIRED if c.casefold() not in have]


def _link_columns(conn, db):
    """(schema, table, column) of every column that can hold a Clasificatii.IDClsf, except the table itself and the _snap_ copies."""
    found = {}
    for r in _rows(conn, "SELECT c.TABLE_NAME AS t, c.COLUMN_NAME AS col FROM information_schema.COLUMNS c "
                         "JOIN information_schema.TABLES x ON x.TABLE_SCHEMA=c.TABLE_SCHEMA AND x.TABLE_NAME=c.TABLE_NAME "
                         "WHERE c.TABLE_SCHEMA=%s AND x.TABLE_TYPE='BASE TABLE' AND LOWER(c.COLUMN_NAME)='idclsf'", (db,)):
        found[(db.casefold(), r['t'].casefold(), r['col'].casefold())] = (db, r['t'], r['col'])
    for r in _rows(conn, 'SELECT TABLE_SCHEMA AS s, TABLE_NAME AS t, COLUMN_NAME AS col FROM information_schema.KEY_COLUMN_USAGE '
                         'WHERE REFERENCED_TABLE_SCHEMA=%s AND LOWER(REFERENCED_TABLE_NAME)=%s '
                         'AND LOWER(REFERENCED_COLUMN_NAME)=%s', (db, TABLE.casefold(), 'idclsf')):
        found[(r['s'].casefold(), r['t'].casefold(), r['col'].casefold())] = (r['s'], r['t'], r['col'])
    return [v for k, v in sorted(found.items())
            if not (k[0] == db.casefold() and k[1] == TABLE.casefold()) and not k[1].startswith('_snap')]


def _chunks(items):
    for i in range(0, len(items), CHUNK):
        yield items[i:i + CHUNK]


def _links(conn, link_columns, ids):
    """id -> [(table, rows holding it)] for the ids that something else holds."""
    held = defaultdict(list)
    for schema, table, col in link_columns:
        full = _q(schema) + '.' + _q(table)
        for part in _chunks(ids):
            sql = ('SELECT ' + _q(col) + ' AS id, COUNT(*) AS n FROM ' + full + ' WHERE ' + _q(col) + ' IN (' +
                   ','.join(['%s'] * len(part)) + ') GROUP BY ' + _q(col))
            for r in _rows(conn, sql, tuple(part)):
                held[int(r['id'])].append((table, int(r['n'])))
    return held


def _counts(conn, link_columns):
    done, out = set(), {}
    for schema, table, _col in link_columns:
        if (schema, table) in done:
            continue
        done.add((schema, table))
        out[(schema, table)] = int(_rows(conn, 'SELECT COUNT(*) AS n FROM ' + _q(schema) + '.' + _q(table))[0]['n'])
    return out


def _hard(links):
    """The links that protect a row: everything except the soft tables (rectificari, budget)."""
    return [lk for lk in links if lk[0].casefold() not in SOFT_TABLES]


def _soft(links):
    return [lk for lk in links if lk[0].casefold() in SOFT_TABLES]


def _plan(conn, db):
    """Every row of every duplicate group with what would happen to it, plus the link columns used."""
    rows = _rows(conn,
                 'SELECT c.IDClsf, c.IdClsfAcc, c.IdUnitate, c.Capitol, c.Subcapitol, c.Articol, c.Alineat, c.Denumire '
                 'FROM Clasificatii c JOIN (SELECT IdUnitate, Capitol, Subcapitol, Articol, Alineat FROM Clasificatii '
                 'GROUP BY IdUnitate, Capitol, Subcapitol, Articol, Alineat HAVING COUNT(*)>1) d '
                 'ON d.IdUnitate<=>c.IdUnitate AND d.Capitol=c.Capitol AND d.Subcapitol=c.Subcapitol '
                 'AND d.Articol=c.Articol AND d.Alineat=c.Alineat '
                 'ORDER BY c.IdUnitate, c.Capitol, c.Subcapitol, c.Articol, c.Alineat, c.IDClsf')
    link_columns = _link_columns(conn, db)
    held = _links(conn, link_columns, [int(r['IDClsf']) for r in rows]) if rows else {}
    groups = defaultdict(list)
    for r in rows:
        groups[tuple(_norm(r[k]) for k in KEY_PARTS)].append(r)
    plan = []
    for members in groups.values():
        linked = [r for r in members if _hard(held.get(int(r['IDClsf']), []))]
        keeper = None if linked else min(members, key=lambda r: (int(r['IdClsfAcc'] or 0) == 0, int(r['IDClsf'])))
        for r in members:
            links = held.get(int(r['IDClsf']), [])
            if r in linked:
                action = 'RAMANE - legat'
            elif r is keeper:
                action = 'RAMANE - nelegat, pastrat din grup'
            elif _soft(links):
                action = 'SE STERGE - impreuna cu randurile lui din ' + ', '.join(sorted({t for t, _n in _soft(links)}))
            else:
                action = 'SE STERGE'
            plan.append({'row': r, 'action': action, 'links': links, 'group': len(members), 'linked_in_group': len(linked),
                         'delete': action.startswith('SE STERGE')})
    return plan, link_columns


def _counted(by_table):
    return ', '.join('%s %d' % (t, n) for t, n in sorted(by_table.items())) or 'niciunul'


def _clsf(r):
    return '.'.join(str(r[k]) for k in ('Capitol', 'Subcapitol', 'Articol', 'Alineat'))


def _links_text(links):
    return ', '.join('%s (%d)' % (t, n) for t, n in links) or '-'


def check(conn, db, block):
    missing = _has_table(conn, db)
    if missing:
        return block(db, SUMMARY_TITLE, ['rezultat'], [['Baza nu are tabela Clasificatii cu coloanele cerute (' + ', '.join(missing) +
                                                         '): nu este nimic de verificat.']], raw=True)
    plan, link_columns = _plan(conn, db)
    deletable = [p for p in plan if p['delete']]
    kept_linked = [p for p in plan if p['action'] == 'RAMANE - legat']
    groups = {(tuple(_norm(p['row'][k]) for k in KEY_PARTS)): p for p in plan}
    still = {k for k, p in groups.items() if p['linked_in_group'] > 1}
    summary = block(db, SUMMARY_TITLE, ['grupuri cu dubluri', 'randuri in aceste grupuri', REPAIR_COLUMN, 'raman legate',
                                        'grupuri cu mai multe legate'],
                    [[len(groups), len(plan), len(deletable), len(kept_linked), len(still)]],
                    'reparabile = randuri care se sterg; un grup fara niciun rand legat pastreaza unul. '
                    'O legatura doar din Clasificatii_Rectificari sau Clasificatii_Buget nu opreste stergerea (randul se sterge odata cu randurile lui de acolo); '
                    'o legatura din FX_, EF_ sau orice alta tabela o opreste. '
                    'Legaturile se cauta in %d coloane din %d tabele ale bazei (orice coloana IdClsf si orice cheie straina spre Clasificatii). '
                    'Grupurile cu mai multe randuri legate nu se unesc automat: se analizeaza de mana.'
                    % (len(link_columns), len({(s, t) for s, t, _c in link_columns})))
    detail_rows = [[p['row']['IdUnitate'], _clsf(p['row']), p['row']['IDClsf'], p['row']['IdClsfAcc'], p['row']['Denumire'],
                    _links_text(p['links']), p['action']] for p in plan[:DETAIL_LIMIT]]
    detail = block(db, DETAIL_TITLE, ['unitate', 'clsf', 'IDClsf', 'IdClsfAcc', 'denumire', 'legaturi', 'ce se face'], detail_rows,
                   'Doar primele %d randuri.' % DETAIL_LIMIT if len(plan) > DETAIL_LIMIT else '')
    return [summary, detail]


def repair(conn, db, block):
    """WRITES. Deletes the duplicates in ONE database that nothing but the soft tables (rectificari, budget) holds; all or nothing."""
    missing = _has_table(conn, db)
    if missing:
        raise AdminError('Repararea nu poate rula: lipsesc coloanele Clasificatii.' + ', '.join(missing) + '.')
    soft_names = [s['table'] for s in SOFT_TABLES.values()]
    engines = {(r['db'], r['table']): str(r['engine'] or '').upper() for r in _rows(
        conn, 'SELECT TABLE_SCHEMA AS db,TABLE_NAME AS `table`,ENGINE AS engine FROM information_schema.TABLES '
              "WHERE (TABLE_SCHEMA=%s AND TABLE_NAME IN (%s,%s,%s)) OR (TABLE_SCHEMA=%s AND TABLE_NAME='Jurnal')",
        tuple([db, TABLE] + soft_names + [COMMON_DB]))}
    needed = [(db, TABLE), (COMMON_DB, 'Jurnal')] + [(db, t) for t in soft_names if (db, t) in engines]
    if any(engines.get(t) != 'INNODB' for t in needed):
        raise AdminError('Repararea cere tabelele Clasificatii, Clasificatii_Rectificari, Clasificatii_Buget si Jurnal in InnoDB, '
                         'ca totul sa se aplice intr-o singura tranzactie.')
    conn.start_transaction(isolation_level='READ COMMITTED')
    try:
        plan, _unused = _plan(conn, db)
        if not plan:
            conn.rollback()
            return [block(db, TITLE + ' - reparare', ['rezultat'], [['Nu exista clasificatii dublate: nu s-a sters nimic.']], raw=True)]
        # Lock every row of the groups: a new row pointing at one of them waits, and finds it gone after the commit.
        ids = [int(p['row']['IDClsf']) for p in plan]
        for part in _chunks(ids):
            _rows(conn, 'SELECT IDClsf FROM Clasificatii WHERE IDClsf IN (' + ','.join(['%s'] * len(part)) + ') FOR UPDATE', tuple(part))
        plan, link_columns = _plan(conn, db)          # the links are read again under the lock
        doomed = [p for p in plan if p['delete']]
        if not doomed:
            conn.rollback()
            return [block(db, TITLE + ' - reparare', ['rezultat'], [['Toate dublurile sunt legate de ceva: nu s-a sters nimic.']], raw=True)]
        before = _counts(conn, link_columns)
        snapshot, removed = [], {}
        for p in doomed:
            idc = int(p['row']['IDClsf'])
            full = _rows(conn, 'SELECT * FROM Clasificatii WHERE IDClsf=%s', (idc,))
            snapshot.append(str(dict(full[0])) if full else 'IDClsf %d' % idc)
            # its rows in the soft tables go first (rectificari are RESTRICT, the budget would cascade), written to the Jurnal
            for spec in SOFT_TABLES.values():
                table = spec['table']
                if (db, table) not in engines:
                    continue
                for gone in _rows(conn, 'SELECT * FROM ' + table + ' WHERE IdClsf=%s', (idc,)):
                    snapshot.append(table + ' sters: ' + str(dict(gone)))
                n = _exec(conn, 'DELETE FROM ' + table + ' WHERE IdClsf=%s', (idc,))
                if n:
                    removed[table] = removed.get(table, 0) + n
            if _exec(conn, 'DELETE FROM Clasificatii WHERE IDClsf=%s', (idc,)) != 1:
                raise AdminError('Datele s-au schimbat in timpul repararii (IDClsf %d). Nu s-a sters nimic; reluati verificarea.' % idc)
        after = _counts(conn, link_columns)
        # every linking table keeps its row count, except the soft tables, which lose exactly the rows of the deleted classifications
        expected = dict(before)
        for key in expected:
            expected[key] -= sum(n for t, n in removed.items() if t.casefold() == key[1].casefold())
        lost = ['%s.%s: %d -> %d' % (s, t, expected[(s, t)], after[(s, t)]) for (s, t) in before if after[(s, t)] != expected[(s, t)]]
        if lost:
            raise AdminError('Stergerea ar fi schimbat randuri din alte tabele (%s). Nu s-a sters nimic.' % '; '.join(lost))
        _journal(conn, 'clasificatii_dubluri_repara', db, TABLE,
                 ('%d clasificatii dublate sterse (nelegate de FX_, EF_ sau alta tabela decat rectificari si bugete). Randuri sterse odata cu ele: %s. '
                  'Sterse: %s' % (len(doomed), _counted(removed), ' | '.join(snapshot)))[:60000])
        done = block(db, TITLE + ' - reparare', ['rezultat'],
                     [['%d randuri dublate sterse din Clasificatii, baza %s. Sterse odata cu ele: %s.'
                       % (len(doomed), db, _counted(removed))]],
                     'Randurile sterse sunt in Jurnal, cu valorile de dinainte. Mai jos, ce a mai ramas.', raw=True)
        after_blocks = check(conn, db, block)
        conn.commit()
        return [done] + after_blocks
    except Exception:
        conn.rollback()
        raise
