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
    'FX_DDF_REV_SA': 'IDREV ValCur',
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
# What hangs on a single revision besides its A/B/PRT lines (those are copies of the revision and go with it).
BY_REVISION = ('FX_DDF_REV_ATT', 'FX_DDF_REV_ATT_IMG', 'FX_DDF_PDF', 'FX_PDF_SEMNATURI', 'FX_Rezervari')
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


def _money(value):
    return '{:,.2f}'.format(float(value or 0)).replace(',', ' ')


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
    # Suma = the revision total the application itself shows (TotalRevizie = SUM(ValCur) over section A), NLinii = its section A lines.
    for r in _rows(conn, 'SELECT r.IDREV,r.IDDF,r.NumarRev,r.DataRev,r.Desc_Scurta,'
                         'COALESCE((SELECT ROUND(SUM(sa.ValCur),2) FROM FX_DDF_REV_SA sa WHERE sa.IDREV=r.IDREV),0) AS Suma,'
                         '(SELECT COUNT(*) FROM FX_DDF_REV_SA sa WHERE sa.IDREV=r.IDREV) AS NLinii '
                         'FROM FX_DDF_REV r WHERE r.IDDF IN (SELECT IDDF FROM FX_DDF WHERE CodAngajament=%s) '
                         'ORDER BY r.IDDF,r.NumarRev,r.IDREV', (cod,)):
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


def _same_revision(a, b):
    """The operator's key: same date and same total (over section A lines that really exist) = the same revision."""
    return (a['DataRev'] is not None and a['DataRev'] == b['DataRev'] and a['NLinii'] > 0 and b['NLinii'] > 0
            and round(float(a['Suma']), 2) == round(float(b['Suma']), 2))


def _rev_attached(conn, columns, idrev):
    """label -> rows of what hangs on ONE revision and would go with it (`FX_Rezervari` is listed too: its rows are re-pointed, not lost)."""
    found = OrderedDict()
    for table, label in DEPENDENTS:
        if table not in BY_REVISION or table not in columns:
            continue
        if table == 'FX_DDF_REV_ATT_IMG':
            n = _rows(conn, 'SELECT COUNT(*) AS n FROM FX_DDF_REV_ATT_IMG i JOIN FX_DDF_REV_ATT a ON a.IdRevAtt=i.IdRevAtt '
                            'WHERE a.IDREV=%s', (idrev,))[0]['n']
        elif 'IDREV' in columns[table]:
            n = _rows(conn, 'SELECT COUNT(*) AS n FROM ' + table + ' WHERE IDREV=%s', (idrev,))[0]['n']
        else:
            continue
        if n:
            found[table] = (label, n)
    return found


def _eligibility(conn, ctx):
    """(main, extras, reasons, impact, plan): `reasons` empty = the automatic repair applies. Per revision of an extra header:
    the same date AND the same total as ANY revision of the main header = the same revision -> it is dropped (its reservations are
    re-pointed to the revision that stays); otherwise it moves under the main header. Anything unclear is a reason, never a guess.
    plan = {'drop': [(revision, kept revision, reservations)], 'move': [revision]}"""
    heads, revs, pointer, rules, columns = ctx['heads'], ctx['revs'], ctx['pointer'], ctx['rules'], ctx['columns']
    impact = {iddf: _impact(conn, ctx, iddf) for iddf in heads}
    plan = {'drop': [], 'move': []}
    if len(heads) < 2:
        return None, [], ['are un singur antet sau niciunul: nu e nimic de unit aici.'], impact, plan
    if pointer not in heads:
        return None, [], ['angajamentul nu arata spre niciunul dintre antete (FX_Angajamente.IDDF = %s): nu se stie care este cel principal.' % pointer], impact, plan
    reasons = []
    extras = [i for i in heads if i != pointer]
    if len({str(rows[0]['DC']) for rows in heads.values()}) > 1:
        reasons.append('antetele au DC diferit.')
    main_revs = revs.get(pointer, [])
    taken = {x['NumarRev'] for x in main_revs if x['NumarRev'] is not None}
    for iddf in extras:
        for table, (label, n) in impact[iddf].items():
            if n and (table in STAY_BEHIND or _blocks(table, rules)):
                reasons.append('IDDF %s are %d %s, care nu se muta odata cu reviziile.' % (iddf, n, label))
        for x in revs.get(iddf, []):
            kept = next((m for m in main_revs if _same_revision(x, m)), None)
            if kept is not None:
                attached = _rev_attached(conn, columns, x['IDREV'])
                lost = [(label, n) for t, (label, n) in attached.items() if t != 'FX_Rezervari']
                if lost:
                    reasons.append('IDDF %s: revizia IDREV %s seamana cu IDREV %s din antetul principal (aceeasi data si suma), dar are %s, care s-ar pierde.'
                                   % (iddf, x['IDREV'], kept['IDREV'], ', '.join('%d %s' % (n, label) for label, n in lost)))
                plan['drop'].append((x, kept, attached.get('FX_Rezervari', ('', 0))[1]))
                continue
            plan['move'].append(x)
            if x['NumarRev'] is None:
                reasons.append('IDDF %s: revizia IDREV %s nu are NumarRev.' % (iddf, x['IDREV']))
            elif x['NumarRev'] in taken:
                reasons.append('IDDF %s: NumarRev %s (IDREV %s) exista deja in documentul principal sau in alt antet de mutat si revizia nu are aceeasi data si suma ca una din ele.' % (iddf, x['NumarRev'], x['IDREV']))
            else:
                taken.add(x['NumarRev'])
    return pointer, extras, reasons, impact, plan


def explain(conn, db, param, block):
    cod = _valid_code(param)
    columns = _schema(conn, db)
    missing = _missing(columns, REQUIRED)
    if missing:
        raise AdminError('Explicatia nu poate rula: lipsesc ' + ', '.join(missing) + '.')
    ctx = _load_code(conn, db, columns, cod)
    ang, pointer, heads, revs, rules = ctx['ang'], ctx['pointer'], ctx['heads'], ctx['revs'], ctx['rules']
    main, extras, reasons, impact, plan = _eligibility(conn, ctx)

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
            found.append('    IDREV %s - NumarRev %s - %s - suma %s (%d linii) - %s' % (
                x['IDREV'], x['NumarRev'], _day(x['DataRev']), _money(x['Suma']), x['NLinii'], x['Desc_Scurta'] or ''))
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
        dropped = {x['IDREV']: (kept, resv) for x, kept, resv in plan['drop']}
        for iddf in heads:
            if iddf == pointer:
                continue
            counts = impact[iddf]
            rv = revs.get(iddf, [])
            stop = [(label, n) for t, (label, n) in counts.items() if n and _blocks(t, rules)]
            if stop:
                verdict.append('IDDF %s: NU se poate sterge - are %s.' % (iddf, ', '.join('%d %s' % (n, label) for label, n in stop)))
                continue
            if not rv and not any(n for t, (label, n) in counts.items() if t != 'FX_DDF_REV'):
                verdict.append('IDDF %s: antet gol (fara revizii si fara nimic legat de el) - se poate sterge fara pierderi.' % iddf)
                continue
            verdict.append('IDDF %s are %s. Fiecare revizie a lui e comparata cu TOATE reviziile din IDDF %s '
                           '(aceeasi data + aceeasi suma = aceeasi revizie):' % (iddf, _plural(len(rv), 'revizie', 'revizii'), pointer))
            for x in rv:
                if x['IDREV'] in dropped:
                    kept, resv = dropped[x['IDREV']]
                    verdict.append('  - IDREV %s (NumarRev %s, %s, suma %s) EXISTA DEJA in IDDF %s, ca IDREV %s (NumarRev %s): se STERGE, nu se muta nicaieri.'
                                   % (x['IDREV'], x['NumarRev'], _day(x['DataRev']), _money(x['Suma']), pointer, kept['IDREV'], kept['NumarRev']))
                    if resv:
                        verdict.append('    Cele %s legate de ea NU se sterg (sunt unice): raman si se leaga de IDREV %s.' % (_plural(resv, 'rezervare', 'rezervari'), kept['IDREV']))
                else:
                    note = ' (NumarRev %s lipsea din IDDF %s si se potriveste in gol)' % (x['NumarRev'], pointer) if x['NumarRev'] in gaps else ''
                    verdict.append('  - IDREV %s (NumarRev %s, %s, suma %s) nu seamana cu nicio revizie din IDDF %s: se muta sub IDDF %s%s.'
                                   % (x['IDREV'], x['NumarRev'], _day(x['DataRev']), _money(x['Suma']), pointer, pointer, note))
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
            resv_total = sum(r for _x, _k, r in plan['drop'])
            parts = []
            if plan['drop']:
                parts.append('se sterg %s (exista deja in IDDF %s)' % (_plural(len(plan['drop']), 'revizie', 'revizii'), main))
            parts.append('se muta %s sub IDDF %s' % (_plural(len(plan['move']), 'revizie', 'revizii'), main) if plan['move']
                         else 'nu se muta nicio revizie')
            if resv_total:
                parts.append('%s ramane si se leaga de revizia care a ramas' % _plural(resv_total, 'rezervare', 'rezervari'))
            parts.append('antetele ramase goale (IDDF %s) se sterg' % ', '.join(map(str, extras)))
            verdict.append(REPAIRABLE_MARK + ': ' + '; '.join(parts) + '. Butonul «Repara codul» face asta intr-o singura tranzactie.')
    third = block(db, 'Concluzie pentru %s' % cod, ['text'], [[line] for line in verdict],
                  'Aceasta explicatie nu schimba nimic in baza.', raw=True)
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
        main, extras, reasons, _unused, plan = _eligibility(conn, ctx)
        if reasons:
            raise AdminError('Codul %s nu se repara automat: %s Nu s-a schimbat nimic.' % (cod, ' '.join(reasons)))
        moved, removed, dropped, resv_moved = [], [], [], 0
        for iddf in extras:
            # 1: the revisions that are the same as one of the main header (same date and total) are dropped; their reservations stay,
            #    re-pointed to the revision that remains (a reservation is unique, never deleted). What the dropped revision held is written to the Jurnal first.
            for x, kept, _resv in [d for d in plan['drop'] if d[0]['IDDF'] == iddf]:
                snapshot = [str(dict(r)) for r in _rows(conn, 'SELECT * FROM FX_DDF_REV WHERE IDREV=%s', (x['IDREV'],))]
                for table in ('FX_DDF_REV_SA', 'FX_DDF_REV_SB'):
                    if table in columns:
                        snapshot.extend(table + ' ' + str(dict(r)) for r in _rows(conn, 'SELECT * FROM ' + table + ' WHERE IDREV=%s', (x['IDREV'],)))
                if 'FX_Rezervari' in columns and 'IDREV' in columns['FX_Rezervari']:
                    # A reservation exists once (FX_Rezervari.IDH is unique): it is NOT deleted with the duplicate revision (ON DELETE CASCADE)
                    # but re-pointed to the revision that stays. Its row goes to the Jurnal first.
                    rez = _rows(conn, 'SELECT * FROM FX_Rezervari WHERE IDREV=%s', (x['IDREV'],))
                    snapshot.extend('FX_Rezervari ' + str(dict(r)) for r in rez)
                    resv_moved += _exec(conn, 'UPDATE FX_Rezervari SET IDREV=%s WHERE IDREV=%s', (kept['IDREV'], x['IDREV']))
                if _exec(conn, 'DELETE FROM FX_DDF_REV WHERE IDREV=%s AND IDDF=%s', (x['IDREV'], iddf)) != 1:
                    raise AdminError('Datele s-au schimbat in timpul repararii. Nu s-a aplicat nimic; reluati verificarea.')
                dropped.append('IDREV %s (identica cu IDREV %s): %s' % (x['IDREV'], kept['IDREV'], ' / '.join(snapshot)[:8000]))
            # 2: the others move under the main header, together with the lines that carry their IDDF.
            idrevs = [x['IDREV'] for x in plan['move'] if x['IDDF'] == iddf]
            if idrevs and _exec(conn, 'UPDATE FX_DDF_REV SET IDDF=%s WHERE IDDF=%s AND IDREV IN (' +
                                ','.join(['%s'] * len(idrevs)) + ')', tuple([main, iddf] + idrevs)) != len(idrevs):
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
                 ('Revizii mutate sub IDDF %s: IDREV %s. Revizii identice sterse (aceeasi data si suma), cu continutul lor: %s. '
                  'Rezervari mutate pe revizia ramasa: %d. Antete sterse (valorile de dinainte): %s.'
                  % (main, ', '.join(map(str, moved)) or '-', ' | '.join(dropped) or '-', resv_moved, ' | '.join(removed)))[:60000])
        done = block(db, 'Reparare documente de fundamentare', ['rezultat'],
                     [['%s: %s identice sterse, %s mutate sub IDDF %s, %s mutate pe revizia ramasa, %s sters(e).'
                       % (cod, _plural(len(dropped), 'revizie', 'revizii'), _plural(len(moved), 'revizie', 'revizii'), main,
                          _plural(resv_moved, 'rezervare', 'rezervari'), _plural(len(removed), 'antet', 'antete'))]],
                     'Tot ce s-a facut este in Jurnal, cu continutul reviziilor sterse si valorile antetelor sterse. Mai jos, starea de acum.', raw=True)
        after = explain(conn, db, cod, block)
        conn.commit()
        return [done] + after
    except Exception:
        conn.rollback()
        raise
