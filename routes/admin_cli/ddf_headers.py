"""KBotAdmin: checks on the FX_DDF headers of ONE unit database. READ ONLY - nothing here writes.

`check`   - the whole database: lists the codes whose documents look wrong (several headers under one code,
            a pointer that leads nowhere, a revision number used twice, a revision filed under another code).
`explain` - ONE code: what the database holds for it, in plain words, and what deleting each header
            would do (the foreign keys are read from the live server, not assumed).

Born from AAB2SGAKRH4 (02.11.2026): two FX_DDF headers (IDDF 8 and 9) under one code; the editor refuses to
choose. IDDF 9 held revision 7 of the document, with a reservation attached - deleting the header would have
deleted that revision and the reservation with it (FX_Rezervari.IDREV is ON DELETE CASCADE).
"""
import re
from collections import OrderedDict, defaultdict

from routes.admin_cli.admin_common import COMMON_DB, AdminError, _exec, _journal, _rows

SUMMARY_TITLE = 'Documente de fundamentare (FX_DDF) - sumar'
DETAIL_TITLE = 'Documente de fundamentare (FX_DDF) - detalii'
PROBLEMS_COLUMN = 'cazuri'

REQUIRED = {
    'FX_Angajamente': 'CodAngajament IDDF',
    'FX_DDF': 'IDDF CodAngajament CUAL DataCreare DC Program Stare',
    'FX_DDF_REV': 'IDREV IDDF CodAngajament NumarRev DataRev Desc_Scurta',
}

# (table, what the operator calls its rows). Order = order of the lines shown.
DEPENDENTS = [
    ('FX_ORD', 'ordonantari'),
    ('FX_DDF_REV', 'revizii'),
    ('FX_DDF_REV_SA', 'linii din sectiunea A'),
    ('FX_DDF_REV_SB', 'linii din sectiunea B'),
    ('FX_DDF_REV_PRT', 'linii PRT'),
    ('FX_DDF_REV_ATT', 'atasamente'),
    ('FX_DDF_REV_ATT_IMG', 'imagini ale atasamentelor'),
    ('FX_DDF_PDF', 'PDF-uri'),
    ('FX_PDF_SEMNATURI', 'semnaturi pe PDF'),
    ('FX_Rezervari', 'rezervari'),
    ('FX_DDF_Parteneri', 'parteneri ai documentului'),
    ('EF_PrimiteAsocieri', 'asocieri cu facturi primite'),
]


def _blocks(table, rules):
    """The server refuses to delete a header while rows of this table point at it."""
    return bool(rules.get(table, set()) & {'RESTRICT', 'NO ACTION'})

# Tables whose rows hang on the header itself and do NOT travel when a revision is moved to another header.
STAY_BEHIND = ('FX_ORD', 'FX_DDF_Parteneri', 'EF_PrimiteAsocieri')
# Tables that carry both IDDF and IDREV: moving a revision means changing their IDDF too.
MOVE_WITH_REVISION = ('FX_DDF_REV_SA', 'FX_DDF_REV_SB', 'FX_DDF_REV_PRT', 'FX_DDF_REV_ATT')
REPAIRABLE_MARK = 'REPARABIL'

_RX_COD = re.compile(r'^[A-Za-z0-9_./-]{1,60}$')


def _schema(conn, db):
    columns = defaultdict(set)
    for row in _rows(conn, 'SELECT TABLE_NAME AS t,COLUMN_NAME AS c FROM information_schema.COLUMNS '
                          'WHERE TABLE_SCHEMA=%s', (db,)):
        columns[row['t']].add(row['c'])
    return columns


def _missing(columns, required):
    return [table + '.' + col for table, fields in required.items()
            for col in fields.split() if col not in columns.get(table, set())]


def _day(value):
    return value.strftime('%d.%m.%Y') if hasattr(value, 'strftime') else ('' if value is None else str(value))


def _plural(n, one, many):
    return '%d %s' % (n, one if n == 1 else many)


# =========================================================================================
# check - the whole database
# =========================================================================================
def _problems(conn):
    """[(kind, code, text)] - every finding, one line each. Read only."""
    found = []

    revs = defaultdict(int)
    for r in _rows(conn, 'SELECT IDDF AS iddf,COUNT(*) AS n FROM FX_DDF_REV GROUP BY IDDF'):
        revs[r['iddf']] = r['n']
    pointer = {r['cod']: r['iddf'] for r in _rows(conn, 'SELECT CodAngajament AS cod,IDDF AS iddf FROM FX_Angajamente')}

    for r in _rows(conn, 'SELECT CodAngajament AS cod,GROUP_CONCAT(DISTINCT IDDF ORDER BY IDDF) AS ids '
                         'FROM FX_DDF GROUP BY CodAngajament HAVING COUNT(DISTINCT IDDF)>1 ORDER BY CodAngajament'):
        ids = [int(i) for i in r['ids'].split(',')]
        parts = ['IDDF %d (%s)' % (i, _plural(revs[i], 'revizie', 'revizii')) for i in ids]
        found.append(('antete multiple', r['cod'], '%s documente: %s; angajamentul arata spre IDDF %s.'
                      % (len(ids), ', '.join(parts), pointer.get(r['cod']))))

    for r in _rows(conn, 'SELECT a.CodAngajament AS cod,a.IDDF AS iddf FROM FX_Angajamente a '
                         'WHERE a.IDDF IS NOT NULL AND NOT EXISTS (SELECT 1 FROM FX_DDF d '
                         'WHERE d.IDDF=a.IDDF AND d.CodAngajament=a.CodAngajament) ORDER BY a.CodAngajament'):
        found.append(('indicator gresit', r['cod'],
                      'FX_Angajamente.IDDF = %s, dar acest IDDF nu este un antet al codului.' % r['iddf']))

    for r in _rows(conn, 'SELECT MIN(CodAngajament) AS cod,IDDF AS iddf,NumarRev AS nr,COUNT(*) AS n,'
                         'GROUP_CONCAT(IDREV ORDER BY IDREV) AS idrevs FROM FX_DDF_REV WHERE NumarRev IS NOT NULL '
                         'GROUP BY IDDF,NumarRev HAVING COUNT(*)>1 ORDER BY IDDF,NumarRev'):
        found.append(('numar de revizie dublat', r['cod'] or '(fara cod)',
                      'in IDDF %s, NumarRev %s apare de %s ori (IDREV %s).' % (r['iddf'], r['nr'], r['n'], r['idrevs'])))

    for r in _rows(conn, 'SELECT r.IDREV AS idrev,r.IDDF AS iddf,r.CodAngajament AS cod_rev,d.cod AS cod_antet '
                         'FROM FX_DDF_REV r JOIN (SELECT IDDF,MIN(CodAngajament) AS cod FROM FX_DDF GROUP BY IDDF) d '
                         'ON d.IDDF=r.IDDF WHERE r.CodAngajament IS NOT NULL AND r.CodAngajament<>d.cod '
                         'ORDER BY r.IDREV'):
        found.append(('revizie cu alt cod', r['cod_antet'],
                      'revizia IDREV %s sta sub IDDF %s (cod %s) dar poarta codul %s.'
                      % (r['idrev'], r['iddf'], r['cod_antet'], r['cod_rev'])))
    return found


def check(conn, db, block):
    columns = _schema(conn, db)
    missing = _missing(columns, REQUIRED)
    if missing:
        return [block(db, SUMMARY_TITLE, ['rezultat'],
                      [['Verificarea nu poate rula: lipsesc ' + ', '.join(missing) + '.']],
                      'Nu se creeaza si nu se modifica structura bazei.')]
    found = _problems(conn)
    kinds = OrderedDict((k, 0) for k in ('antete multiple', 'indicator gresit', 'numar de revizie dublat', 'revizie cu alt cod'))
    for kind, _cod, _text in found:
        kinds[kind] += 1
    meaning = {
        'antete multiple': 'un cod are mai multe documente; editorul refuza sa aleaga si revizia nu se poate salva',
        'indicator gresit': 'angajamentul arata spre un document care nu e al lui',
        'numar de revizie dublat': 'doua revizii cu acelasi numar in acelasi document',
        'revizie cu alt cod': 'o revizie poarta alt cod decat documentul sub care sta',
    }
    blocks = [block(db, SUMMARY_TITLE, ['problema', PROBLEMS_COLUMN, 'ce inseamna'],
                    [[k, n, meaning[k]] for k, n in kinds.items()],
                    'Doar citire. Lista cu toate zero = nimic de semnalat. Cu cazuri: scrieti codul in caseta '
                    'si apasati «Explica» - arata ce are baza pentru cod si ce ar strica stergerea fiecarui antet.')]
    if found:
        headers = ['problema', 'cod angajament', 'ce s-a gasit']
        blocks.append(block(db, DETAIL_TITLE, headers, [headers] + [[k, c, t] for k, c, t in found],
                            'Toate cazurile sunt listate. Nimic nu a fost schimbat.', raw=True))
    return blocks


# =========================================================================================
# explain - one code
# =========================================================================================
def _delete_rules(conn, db):
    """table -> set of ON DELETE rules of its foreign keys towards FX_DDF / FX_DDF_REV / FX_DDF_REV_ATT, as the server has them."""
    rules = defaultdict(set)
    for r in _rows(conn, 'SELECT TABLE_NAME AS t,DELETE_RULE AS rule FROM information_schema.REFERENTIAL_CONSTRAINTS '
                         "WHERE CONSTRAINT_SCHEMA=%s AND REFERENCED_TABLE_NAME IN ('FX_DDF','FX_DDF_REV','FX_DDF_REV_ATT')",
                   (db,)):
        rules[r['t']].add(r['rule'])
    return rules


def _count(conn, columns, table, iddf, idrevs):
    """Rows of `table` that belong to the header (by IDDF, or by one of its revisions); None when the table does not exist."""
    cols = columns.get(table)
    if not cols:
        return None
    if table == 'FX_DDF_REV_ATT_IMG':
        if not idrevs:
            return 0
        marks = ','.join(['%s'] * len(idrevs))
        return _rows(conn, 'SELECT COUNT(*) AS n FROM FX_DDF_REV_ATT_IMG i JOIN FX_DDF_REV_ATT a ON a.IdRevAtt=i.IdRevAtt '
                           'WHERE a.IDREV IN (' + marks + ')', tuple(idrevs))[0]['n']
    where, params = [], []
    if 'IDDF' in cols:
        where.append('IDDF=%s')
        params.append(iddf)
    if 'IDREV' in cols and idrevs:
        where.append('IDREV IN (' + ','.join(['%s'] * len(idrevs)) + ')')
        params.extend(idrevs)
    if not where:
        return 0 if 'IDREV' in cols else None
    return _rows(conn, 'SELECT COUNT(*) AS n FROM ' + table + ' WHERE ' + ' OR '.join(where), tuple(params))[0]['n']


def _ranges(numbers):
    """[0,1,2,3,5] -> '0-3, 5'"""
    out, start, prev = [], None, None
    for n in sorted(numbers):
        if start is None:
            start = prev = n
        elif n == prev + 1:
            prev = n
        else:
            out.append(str(start) if start == prev else '%d-%d' % (start, prev))
            start = prev = n
    if start is not None:
        out.append(str(start) if start == prev else '%d-%d' % (start, prev))
    return ', '.join(out)


def _valid_code(param):
    cod = (param or '').strip()
    if not _RX_COD.match(cod):
        raise AdminError('Codul angajamentului nu este valid (litere, cifre, _ . / - , pana la 60 de caractere).')
    return cod


def _load_code(conn, db, columns, cod):
    """Everything the checks need about ONE code, read in one go."""
    ang = _rows(conn, 'SELECT CodAngajament,IDDF,Stare FROM FX_Angajamente WHERE CodAngajament=%s', (cod,))
    if not ang:
        raise AdminError('Angajamentul «%s» nu exista in baza %s.' % (cod, db))
    heads = OrderedDict()
    for r in _rows(conn, 'SELECT * FROM FX_DDF WHERE CodAngajament=%s ORDER BY IDDF,CUAL', (cod,)):
        heads.setdefault(r['IDDF'], []).append(r)
    revs = defaultdict(list)
    for r in _rows(conn, 'SELECT IDREV,IDDF,NumarRev,DataRev,Desc_Scurta FROM FX_DDF_REV '
                         'WHERE IDDF IN (SELECT IDDF FROM FX_DDF WHERE CodAngajament=%s) ORDER BY IDDF,NumarRev,IDREV', (cod,)):
        revs[r['IDDF']].append(r)
    return {'cod': cod, 'ang': ang[0], 'pointer': ang[0]['IDDF'], 'heads': heads, 'revs': revs,
            'rules': _delete_rules(conn, db), 'columns': columns}


def _impact(conn, ctx, iddf):
    """table -> (label, rows) of everything that hangs on one header (directly or through its revisions)."""
    idrevs = [x['IDREV'] for x in ctx['revs'].get(iddf, [])]
    counts = OrderedDict()
    for table, label in DEPENDENTS:
        n = _count(conn, ctx['columns'], table, iddf, idrevs)
        if n is not None:
            counts[table] = (label, n)
    return counts


def _eligibility(conn, ctx):
    """(main, extras, reasons, impact): `reasons` empty = the automatic repair applies (move the revisions of the extra headers
    under the one the angajament points to, then delete the headers left empty). Anything unclear is a reason, never a guess."""
    heads, revs, pointer, rules = ctx['heads'], ctx['revs'], ctx['pointer'], ctx['rules']
    impact = {iddf: _impact(conn, ctx, iddf) for iddf in heads}
    if len(heads) < 2:
        return None, [], ['are un singur antet sau niciunul: nu e nimic de unit aici.'], impact
    if pointer not in heads:
        return None, [], ['angajamentul nu arata spre niciunul dintre antete (FX_Angajamente.IDDF = %s): nu se stie care este cel principal.' % pointer], impact
    reasons = []
    extras = [i for i in heads if i != pointer]
    if len({str(rows[0]['DC']) for rows in heads.values()}) > 1:
        reasons.append('antetele au DC diferit.')
    taken = {x['NumarRev'] for x in revs.get(pointer, []) if x['NumarRev'] is not None}
    for iddf in extras:
        for table, (label, n) in impact[iddf].items():
            if n and (table in STAY_BEHIND or _blocks(table, rules)):
                reasons.append('IDDF %s are %d %s, care nu se muta odata cu reviziile.' % (iddf, n, label))
        for x in revs.get(iddf, []):
            if x['NumarRev'] is None:
                reasons.append('IDDF %s: revizia IDREV %s nu are NumarRev.' % (iddf, x['IDREV']))
            elif x['NumarRev'] in taken:
                reasons.append('IDDF %s: NumarRev %s (IDREV %s) exista deja in documentul principal sau in alt antet de mutat.' % (iddf, x['NumarRev'], x['IDREV']))
            else:
                taken.add(x['NumarRev'])
    return pointer, extras, reasons, impact


def explain(conn, db, param, block):
    cod = _valid_code(param)
    columns = _schema(conn, db)
    missing = _missing(columns, REQUIRED)
    if missing:
        raise AdminError('Explicatia nu poate rula: lipsesc ' + ', '.join(missing) + '.')
    ctx = _load_code(conn, db, columns, cod)
    ang, pointer, heads, revs, rules = ctx['ang'], ctx['pointer'], ctx['heads'], ctx['revs'], ctx['rules']
    main, extras, reasons, impact = _eligibility(conn, ctx)

    # ---- 1: what the database holds ------------------------------------------------------
    found = ['Angajament %s%s.' % (cod, (' - ' + str(ang['Stare'])) if ang.get('Stare') else '')]
    if pointer is None:
        found.append('FX_Angajamente.IDDF este gol: angajamentul nu arata spre niciun document.')
    elif pointer in heads:
        found.append('FX_Angajamente.IDDF = %s: acesta este documentul folosit de angajament (documentul principal).' % pointer)
    else:
        found.append('FX_Angajamente.IDDF = %s, dar acest IDDF nu este un antet al acestui cod.' % pointer)
    found.append('Antete FX_DDF sub acest cod: %d.' % len(heads))
    for iddf, rows in heads.items():
        h = rows[0]
        found.append('')
        found.append('Antet IDDF %s (CUAL %s) - creat %s, DC %s, program %s, stare %s%s.' % (
            iddf, ', '.join(str(x['CUAL']) for x in rows), _day(h['DataCreare']), h['DC'], h['Program'], h['Stare'],
            ' [principal]' if iddf == pointer else ''))
        rv = revs.get(iddf, [])
        found.append('  Revizii: %d%s.' % (len(rv), (' (NumarRev ' + _ranges([x['NumarRev'] for x in rv if x['NumarRev'] is not None]) + ')') if rv else ''))
        for x in rv:
            found.append('    IDREV %s - NumarRev %s - %s - %s' % (x['IDREV'], x['NumarRev'], _day(x['DataRev']), x['Desc_Scurta'] or ''))
    first = block(db, 'Ce are baza pentru %s' % cod, ['text'], [[line] for line in found],
                  'Citit din baza %s; nimic nu a fost schimbat.' % db, raw=True)

    # ---- 2: what deleting each header would do --------------------------------------------
    effects, verdict = [], []
    for iddf in heads:
        counts = impact[iddf]
        effects.append('Daca s-ar sterge antetul IDDF %s (DELETE FROM FX_DDF):' % iddf)
        stop = [(label, n) for t, (label, n) in counts.items() if n and _blocks(t, rules)]
        if stop:
            effects.append('  - baza REFUZA stergerea: %s leg(a)te de acest antet (cheie RESTRICT).' %
                           ', '.join('%d %s' % (n, label) for label, n in stop))
        lost = 0
        for table, (label, n) in counts.items():
            if not n or _blocks(table, rules):
                continue
            kinds = rules.get(table, set())
            if 'CASCADE' in kinds:
                effects.append('  - se sterg odata cu el: %d %s.' % (n, label))
                lost += n
            elif kinds & {'SET NULL', 'SET DEFAULT'}:
                effects.append('  - %d %s raman, dar fara legatura cu documentul.' % (n, label))
            else:
                effects.append('  - %d %s raman cu IDDF %s care nu mai exista (nu au cheie straina).' % (n, label, iddf))
        if not lost and not stop:
            effects.append('  - nu ar disparea nimic: antetul nu are nimic legat de el.')
        if iddf == pointer:
            effects.append('  - angajamentul ar ramane fara document (FX_Angajamente.IDDF = %s nu mai exista).' % iddf)
        effects.append('')
    second = block(db, 'Ce ar strica stergerea fiecarui antet', ['text'], [[line] for line in effects],
                   'Regulile (CASCADE / RESTRICT) sunt citite de pe serverul acesta, nu presupuse.', raw=True)

    # ---- 3: the conclusion -----------------------------------------------------------------
    if len(heads) > 1 and pointer in heads:
        main_numbers = {x['NumarRev'] for x in revs.get(pointer, []) if x['NumarRev'] is not None}
        gaps = sorted(set(range(min(main_numbers), max(main_numbers) + 1)) - main_numbers) if main_numbers else []
        for iddf in heads:
            if iddf == pointer:
                continue
            counts = impact[iddf]
            rv = revs.get(iddf, [])
            stop = [(label, n) for t, (label, n) in counts.items() if n and _blocks(t, rules)]
            if stop:
                verdict.append('IDDF %s: NU se poate sterge - are %s.' % (iddf, ', '.join('%d %s' % (n, label) for label, n in stop)))
                continue
            others = [(label, n) for t, (label, n) in counts.items() if n and t != 'FX_DDF_REV' and not _blocks(t, rules)]
            if not rv and not others:
                verdict.append('IDDF %s: antet gol (fara revizii si fara nimic legat de el) - se poate sterge fara pierderi.' % iddf)
                continue
            clash = sorted({x['NumarRev'] for x in rv if x['NumarRev'] in main_numbers})
            fits = sorted({x['NumarRev'] for x in rv if x['NumarRev'] in gaps})
            line = 'IDDF %s: NU se sterge simplu - ar disparea %s' % (iddf, _plural(len(rv), 'revizie', 'revizii'))
            if others:
                line += ' si ' + ', '.join('%d %s' % (n, label) for label, n in others)
            line += '. Reviziile lui trebuie mutate sub IDDF %s inainte.' % pointer
            verdict.append(line)
            if clash:
                verdict.append('  Mutarea s-ar lovi: NumarRev %s exista in ambele antete (de analizat de mana).' % _ranges(clash))
            else:
                verdict.append('  Mutarea nu se loveste de nicio revizie a lui IDDF %s (niciun NumarRev comun).' % pointer)
            if fits:
                verdict.append('  NumarRev %s lipseste din IDDF %s si se afla in IDDF %s - se potriveste exact in gol.' % (_ranges(fits), pointer, iddf))
    elif len(heads) > 1:
        verdict.append('Sunt %d antete, iar angajamentul nu arata spre niciunul: mai intai trebuie hotarat care este documentul principal.' % len(heads))
    elif not heads:
        verdict.append('Codul nu are niciun document de fundamentare.')
    else:
        verdict.append('Un singur antet: nu e problema de antete multiple.')
    for iddf, rv in revs.items():
        seen = defaultdict(list)
        for x in rv:
            if x['NumarRev'] is not None:
                seen[x['NumarRev']].append(x['IDREV'])
        for nr, ids in seen.items():
            if len(ids) > 1:
                verdict.append('IDDF %s: NumarRev %s apare de %d ori (IDREV %s).' % (iddf, nr, len(ids), ', '.join(map(str, ids))))
    if len(heads) > 1:
        verdict.append('')
        if reasons:
            verdict.append('Nu se poate repara automat: ' + ' '.join(reasons))
        else:
            verdict.append(REPAIRABLE_MARK + ': reviziile antetelor in plus (IDDF %s) se pot muta sub IDDF %s, apoi antetele ramase goale se sterg. '
                           'Butonul «Repara» face asta intr-o singura tranzactie.' % (', '.join(map(str, extras)), main))
    third = block(db, 'Concluzie pentru %s' % cod, ['text'], [[line] for line in verdict],
                  'Aceasta comanda nu repara nimic.', raw=True)
    return [first, second, third]


# =========================================================================================
# repair - one code, one transaction
# =========================================================================================
def repair(conn, db, param, block):
    """WRITES. Moves the revisions of the extra headers of ONE code under the header the angajament points to, then deletes
    the headers left empty. All or nothing; whatever `_eligibility` does not clear is refused with its reasons."""
    cod = _valid_code(param)
    columns = _schema(conn, db)
    missing = _missing(columns, REQUIRED)
    if missing:
        raise AdminError('Repararea nu poate rula: lipsesc ' + ', '.join(missing) + '.')
    wanted = [t for t in ('FX_DDF', 'FX_DDF_REV') + MOVE_WITH_REVISION if t in columns]
    engines = {(r['db'], r['table']): str(r['engine'] or '').upper() for r in _rows(conn,
        'SELECT TABLE_SCHEMA AS db,TABLE_NAME AS `table`,ENGINE AS engine FROM information_schema.TABLES '
        'WHERE (TABLE_SCHEMA=%s AND TABLE_NAME IN (' + ','.join(['%s'] * len(wanted)) + ')) '
        "OR (TABLE_SCHEMA=%s AND TABLE_NAME='Jurnal')", tuple([db] + wanted + [COMMON_DB]))}
    if any(engines.get(target) != 'INNODB' for target in [(db, t) for t in wanted] + [(COMMON_DB, 'Jurnal')]):
        raise AdminError('Repararea cere tabele InnoDB (documente, revizii, linii si Jurnal), ca totul sa se aplice intr-o singura '
                         'tranzactie. Structura nu se modifica automat.')
    conn.start_transaction(isolation_level='READ COMMITTED')
    try:
        # The rows of this code stay locked from the re-check until the commit.
        _rows(conn, 'SELECT IDDF FROM FX_DDF WHERE CodAngajament=%s FOR UPDATE', (cod,))
        _rows(conn, 'SELECT CodAngajament FROM FX_Angajamente WHERE CodAngajament=%s FOR UPDATE', (cod,))
        ctx = _load_code(conn, db, columns, cod)
        main, extras, reasons, _unused = _eligibility(conn, ctx)
        if reasons:
            raise AdminError('Codul %s nu se repara automat: %s Nu s-a schimbat nimic.' % (cod, ' '.join(reasons)))
        moved, removed = [], []
        for iddf in extras:
            idrevs = [x['IDREV'] for x in ctx['revs'].get(iddf, [])]
            if _exec(conn, 'UPDATE FX_DDF_REV SET IDDF=%s WHERE IDDF=%s', (main, iddf)) != len(idrevs):
                raise AdminError('Datele s-au schimbat in timpul repararii. Nu s-a aplicat nimic; reluati verificarea.')
            for table in MOVE_WITH_REVISION:
                if table not in columns:
                    continue
                where, params = 'IDDF=%s', [main, iddf]
                if idrevs and 'IDREV' in columns[table]:
                    where += ' OR IDREV IN (' + ','.join(['%s'] * len(idrevs)) + ')'
                    params.extend(idrevs)
                _exec(conn, 'UPDATE ' + table + ' SET IDDF=%s WHERE ' + where, tuple(params))
            left = []
            for table, label in DEPENDENTS:
                n = _count(conn, columns, table, iddf, [])
                if n:
                    left.append('%d %s' % (n, label))
            if left:
                raise AdminError('Dupa mutare au ramas pe IDDF %s: %s. Nu s-a aplicat nimic; se analizeaza de mana.' % (iddf, ', '.join(left)))
            gone = _exec(conn, 'DELETE FROM FX_DDF WHERE IDDF=%s AND NOT EXISTS (SELECT 1 FROM FX_DDF_REV r WHERE r.IDDF=%s)', (iddf, iddf))
            if gone < 1:
                raise AdminError('Antetul IDDF %s nu s-a putut sterge. Nu s-a aplicat nimic.' % iddf)
            moved.extend(idrevs)
            removed.append('IDDF %s: %s' % (iddf, '; '.join(str(dict(r)) for r in ctx['heads'][iddf])))
        _journal(conn, 'ddf_antete_repara', db, cod,
                 ('Revizii mutate sub IDDF %s: IDREV %s. Antete sterse (valorile de dinainte): %s.'
                  % (main, ', '.join(map(str, moved)) or '-', ' | '.join(removed)))[:60000])
        done = block(db, 'Reparare documente de fundamentare', ['rezultat'],
                     [['%s: %s mutate sub IDDF %s, %s sters(e).' % (cod, _plural(len(moved), 'revizie', 'revizii'), main, _plural(len(removed), 'antet', 'antete'))]],
                     'Mutarea si stergerea sunt in Jurnal, cu valorile antetelor sterse. Mai jos, starea de acum.', raw=True)
        after = explain(conn, db, cod, block)
        conn.commit()
        return [done] + after
    except Exception:
        conn.rollback()
        raise
