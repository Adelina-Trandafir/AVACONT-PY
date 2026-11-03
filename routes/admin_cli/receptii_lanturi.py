"""KBotAdmin: the reception chains (FX_Receptii_R / FX_Receptii_H / FX_Receptii) of ONE unit database.

`check`  - READ ONLY, the whole database: every reception whose chain does not close (the newest snapshot has another
           total, or another line, than the reception itself), sorted into the causes that can be told apart:
             1. a snapshot sits on another reception than its own (its reception NAME is the name of another reception);
             2. a reception was overwritten by its same-day twin (it carries the twin's values, its own chain is right);
             3. any other chain that does not close (listed, never repaired automatically).
`repair` - WRITES, ONE code, one transaction: moves the snapshots of cause 1, puts the reception of cause 2 back to what
           its chain says, recomputes DIF on the chains it touched and moves the stored `TotalReceptii` / `Ramas` of the
           ordonantare lines by the difference, so the ordonantari need printing again, not generating again.

Born from AAB2EBFXAAE (02.11.2026, 038_SCGP): reception 58 ("dobanda ... transa 4", AA2 3553) carried the values of reception
59 (AAB 11543) after a download made before the same-day matching fix (30.09.2026); snapshots 75 and 76 ("... si contrib handica
neincadrati", 19213) sat on reception 60 instead of reception 61. Both showed only as the association guard refusing every
later download. Nothing here guesses: whatever the rules below do not clear is listed as it is and left alone.
"""
import re
from collections import OrderedDict, defaultdict
from datetime import datetime

from routes.admin_cli.admin_common import COMMON_DB, AdminError, _exec, _journal, _rows

SUMMARY_TITLE = 'Lanturi de receptii (instantanee) - sumar'
DETAIL_TITLE = 'Lanturi de receptii (instantanee) - detalii'
PROBLEMS_COLUMN = 'cazuri'
REPAIRABLE_COLUMN = 'reparabile'
REPAIRABLE_MARK = 'REPARABIL'

KIND_MOVED = 'instantaneu pe alta receptie'
KIND_TWIN = 'receptie rescrisa de gemena din aceeasi zi'
KIND_OTHER = 'lant neinchis, alta cauza'
MEANING = {
    KIND_MOVED: 'instantaneul poarta numele altei receptii a angajamentului si sta pe una care nu e a lui',
    KIND_TWIN: 'receptia are valorile unei alte receptii din aceeasi zi; lantul ei, intact, spune altceva',
    KIND_OTHER: 'ultimul instantaneu nu are valoarea receptiei si nu se poate spune care parte e gresita',
}

REQUIRED = {
    'FX_Receptii_R': 'IDRR CodAngajament DataR SumaAntet Descriere Sters',
    'FX_Receptii_RHR': 'IDRHR IDRR CodAngajament CodIndicator Valoare',
    'FX_Receptii_H': 'IDRH IDRR CodAngajament DataH Total Descriere Sters EsteStergere DIFH',
    'FX_Receptii': 'IDR IDRH CodAngajament CodIndicator Data Valoare DIF DIFC CodAI IdClsf',
    'FX_ORD': 'IDORDP NrORD DataORD CodAngajament',
    'FX_ORD_TBL': 'IDORDTBLP IDORDP CodIndicator TotalReceptii Ramas',
}

_RX_COD = re.compile(r'^[A-Za-z0-9_./-]{1,60}$')
_RX_SPACES = re.compile(r'\s+')


# =========================================================================================
# reading
# =========================================================================================
def _schema(conn, db):
    columns = defaultdict(set)
    for row in _rows(conn, 'SELECT TABLE_NAME AS t,COLUMN_NAME AS c FROM information_schema.COLUMNS '
                          'WHERE TABLE_SCHEMA=%s', (db,)):
        columns[row['t']].add(row['c'])
    return columns


def _missing(columns, required):
    return [table + '.' + col for table, fields in required.items()
            for col in fields.split() if col not in columns.get(table, set())]


def _valid_code(param):
    cod = (param or '').strip()
    if not _RX_COD.match(cod):
        raise AdminError('Codul angajamentului nu este valid (litere, cifre, _ . / - , pana la 60 de caractere).')
    return cod


def _norm(text):
    """The name of a reception as compared: without the K-BOT marker, lower case, single spaces; '' when empty."""
    from routes.forexe.prelucrare_helpers import fara_marcaj
    return _RX_SPACES.sub(' ', (fara_marcaj(text or '') or '')).strip().lower()


def _day(value):
    return value.strftime('%d.%m.%Y') if hasattr(value, 'strftime') else ('' if value is None else str(value))


def _moment(value):
    return value.strftime('%d.%m.%Y %H:%M') if hasattr(value, 'strftime') else ('' if value is None else str(value))


def _money(value):
    return '{:,.2f}'.format(float(value or 0)).replace(',', ' ')


def _plural(n, one, many):
    return '%d %s' % (n, one if n == 1 else many)


def _load(conn, cod=None):
    """The reception chains of one angajament, or of the whole database: receptions that are not deleted, their lines, and
    every snapshot that is linked to one, oldest first. Nothing is written."""
    from routes.forexe.prelucrare_helpers import SNAPSHOT_COUNTS_SQL
    where_cod = ' AND {}.CodAngajament=%s' if cod else ''
    params = (cod,) if cod else ()

    recs, rec_lines, rhr_rows = OrderedDict(), defaultdict(dict), defaultdict(list)
    for r in _rows(conn, 'SELECT IDRR,CodAngajament,DataR,SumaAntet,Descriere,TipReceptie FROM FX_Receptii_R '
                         'WHERE COALESCE(Sters,0)=0' + where_cod.format('FX_Receptii_R') + ' ORDER BY CodAngajament,DataR,IDRR', params):
        recs[r['IDRR']] = r
    for r in _rows(conn, 'SELECT IDRHR,IDRR,CodIndicator,Valoare FROM FX_Receptii_RHR WHERE IDRR IS NOT NULL'
                         + where_cod.format('FX_Receptii_RHR') + ' ORDER BY IDRHR', params):
        if r['IDRR'] in recs:
            ind = r['CodIndicator'] or ''
            rec_lines[r['IDRR']][ind] = round(rec_lines[r['IDRR']].get(ind, 0.0) + float(r['Valoare'] or 0), 2)
            rhr_rows[r['IDRR']].append((r['IDRHR'], ind, round(float(r['Valoare'] or 0), 2)))

    chains, snap_lines = defaultdict(list), defaultdict(dict)
    for h in _rows(conn, 'SELECT H.IDRH,H.IDRR,H.CodAngajament,H.DataH,H.Total,H.Descriere FROM FX_Receptii_H H '
                         'WHERE H.IDRR IS NOT NULL AND ' + SNAPSHOT_COUNTS_SQL + where_cod.format('H')
                         + ' ORDER BY H.DataH,H.IDRH', params):
        if h['IDRR'] in recs:
            chains[h['IDRR']].append(h)
    for r in _rows(conn, 'SELECT L.IDRH,L.CodIndicator,SUM(L.Valoare) AS v FROM FX_Receptii L JOIN FX_Receptii_H H ON H.IDRH=L.IDRH '
                         'WHERE H.IDRR IS NOT NULL' + where_cod.format('H') + ' GROUP BY L.IDRH,L.CodIndicator', params):
        snap_lines[r['IDRH']][r['CodIndicator'] or ''] = round(float(r['v'] or 0), 2)
    return {'recs': recs, 'rec_lines': rec_lines, 'rhr_rows': rhr_rows, 'chains': chains, 'snap_lines': snap_lines}


# =========================================================================================
# the rules
# =========================================================================================
def _closes(ctx, idrr, chain):
    """The chain-end rule (F15): the newest snapshot has the total of the reception, and every line the reception really carries
    (a zero line is not a line). A chain with no snapshot has nothing to compare."""
    if not chain:
        return True
    last = chain[-1]
    rec = ctx['recs'][idrr]
    if round(float(rec['SumaAntet'] or 0), 2) != round(float(last['Total'] or 0), 2):
        return False
    snap = ctx['snap_lines'].get(last['IDRH'], {})
    return all(v == 0 or round(snap.get(ind, 0.0), 2) == v for ind, v in ctx['rec_lines'].get(idrr, {}).items())


def _with_snapshots(chain, extra):
    """The chain with `extra` snapshots added, in the order the chain is read (DataH, then IDRH)."""
    return sorted(chain + extra, key=lambda h: (h['DataH'] or datetime.min, h['IDRH']))


def _day_of(value):
    return value.date() if isinstance(value, datetime) else value


def _near(data_r, snapshots):
    """True when every snapshot lies at most PRAG_ZILE_SUGESTIE days from the reception date (the association form's window)."""
    from routes.forexe.prelucrare_asociere import PRAG_ZILE_SUGESTIE
    day_r = _day_of(data_r)
    try:
        return all(abs((day_r - _day_of(h['DataH'])).days) <= PRAG_ZILE_SUGESTIE for h in snapshots)
    except (TypeError, AttributeError):         # a missing or unreadable date: not near
        return False


def _nonzero(lines):
    return {k: v for k, v in lines.items() if round(v, 2) != 0}


def _describe(ctx, idrr):
    rec = ctx['recs'][idrr]
    return 'receptia %s (%s, %s, %s)' % (idrr, _day(rec['DataR']), _money(rec['SumaAntet']), rec['Descriere'] or 'fara nume')


def _findings(ctx):
    """[{kind, cod, text, plan, reasons}] - plan is None when nothing may be done automatically; reasons say why not.
    plan = {'type': 'move', 'idrhs': [...], 'from': IDRR, 'to': IDRR}
         | {'type': 'rewrite', 'idrr': IDRR, 'total': x, 'rhr': [(IDRHR, indicator, old, new)]}"""
    out, handled = [], set()
    by_cod = defaultdict(list)
    for idrr, rec in ctx['recs'].items():
        by_cod[rec['CodAngajament']].append(idrr)
    chains = ctx['chains']
    open_ids = [i for i in ctx['recs'] if chains.get(i) and not _closes(ctx, i, chains[i])]

    # ---- 1: a snapshot on another reception than its own ------------------------------------------------------------------
    for idrr in open_ids:
        rec, chain = ctx['recs'][idrr], chains[idrr]
        mine = _norm(rec['Descriere'])
        if not mine:
            continue
        groups = OrderedDict()
        for h in chain:
            name = _norm(h['Descriere'])
            if name and name != mine:
                groups.setdefault(name, []).append(h)
        for name, hs in groups.items():
            targets = [o for o in by_cod[rec['CodAngajament']] if o != idrr and _norm(ctx['recs'][o]['Descriere']) == name]
            probe = {'recs': ctx['recs'], 'rec_lines': ctx['rec_lines'], 'snap_lines': ctx['snap_lines']}
            # Several receptions carry the name (03.11.2026: AAB2K43X2C4, IDRH 31 «PLATA FACT» sat on 28; 27, 30, 31 and 243 all
            # bear that name). Narrowed in two steps, each applied only to what is left: the chain must CLOSE with the snapshots
            # added, and the snapshots must lie within the days a snapshot is trusted to sit from its reception's date (the
            # window the association form suggests with). Exactly one left = the reception they belong to; none or several:
            # left alone, as before.
            if len(targets) > 1:
                targets = [o for o in targets if _closes(probe, o, _with_snapshots(chains.get(o, []), hs))]
            if len(targets) > 1:
                targets = [o for o in targets if _near(ctx['recs'][o]['DataR'], hs)]
            if len(targets) != 1:
                continue
            target = targets[0]
            moved = {h['IDRH'] for h in hs}
            left = [h for h in chain if h['IDRH'] not in moved]
            joined = _with_snapshots(chains.get(target, []), hs)
            reasons = []
            if not left:
                reasons.append('%s ar ramas fara niciun instantaneu.' % _describe(ctx, idrr))
            elif not _closes(ctx, idrr, left):
                reasons.append('dupa mutare lantul receptiei %s tot nu se inchide.' % idrr)
            if not _closes(probe, target, joined):
                reasons.append('dupa mutare lantul receptiei %s nu se inchide.' % target)
            text = ('instantaneele %s (%s, numele «%s») stau pe %s, dar numele lor este cel al lui %s.'
                    % (', '.join('IDRH %s' % h['IDRH'] for h in hs), ', '.join(_moment(h['DataH']) + ' ' + _money(h['Total']) for h in hs),
                       hs[0]['Descriere'], _describe(ctx, idrr), _describe(ctx, target)))
            plan = None if reasons else {'type': 'move', 'idrhs': sorted(moved), 'from': idrr, 'to': target}
            if plan:
                text += ' %s: se muta pe receptia %s.' % (REPAIRABLE_MARK, target)
            else:
                text += ' Nu se repara automat: ' + ' '.join(reasons)
            out.append({'kind': KIND_MOVED, 'cod': rec['CodAngajament'], 'text': text, 'plan': plan})
            handled.update((idrr, target))

    # ---- 2: a reception overwritten by its same-day twin ------------------------------------------------------------------
    # The twin may carry the SAME name (03.11.2026: AAB3P85NKAX 15/16, AAB2DF4BC3P 65/66); what tells the two apart is the
    # chain: the open one disagrees with its own newest snapshot, the twin's chain closes.
    for idrr in open_ids:
        if idrr in handled:
            continue
        rec, chain = ctx['recs'][idrr], chains[idrr]
        last = chain[-1]
        mine = _norm(rec['Descriere'])
        if not mine or _norm(last['Descriere']) != mine:
            continue
        twins = [o for o in by_cod[rec['CodAngajament']]
                 if o != idrr and ctx['recs'][o]['DataR'] == rec['DataR']
                 and round(float(ctx['recs'][o]['SumaAntet'] or 0), 2) == round(float(rec['SumaAntet'] or 0), 2)
                 and _nonzero(ctx['rec_lines'].get(o, {})) == _nonzero(ctx['rec_lines'].get(idrr, {}))
                 and chains.get(o) and _closes(ctx, o, chains[o])]
        if not twins:
            continue
        snap = ctx['snap_lines'].get(last['IDRH'], {})
        rows, reasons = [], []
        per_ind = defaultdict(list)
        for idrhr, ind, old in ctx['rhr_rows'].get(idrr, []):
            per_ind[ind].append((idrhr, old))
        for ind, items in per_ind.items():
            if len(items) > 1:
                reasons.append('indicatorul %s are %d randuri in receptie.' % (ind, len(items)))
            else:
                rows.append((items[0][0], ind, items[0][1], round(snap.get(ind, 0.0), 2)))
        for ind, v in _nonzero(snap).items():
            if ind not in per_ind:
                reasons.append('instantaneul are indicatorul %s, pe care receptia nu il are.' % ind)
        text = ('%s are valorile lui %s (aceeasi zi, aceeasi suma si aceleasi linii), dar numele si lantul ei spun altceva: '
                'ultimul instantaneu, IDRH %s din %s, are %s.'
                % (_describe(ctx, idrr), ', '.join(_describe(ctx, t) for t in twins), last['IDRH'], _moment(last['DataH']), _money(last['Total'])))
        plan = None if reasons else {'type': 'rewrite', 'idrr': idrr, 'total': round(float(last['Total'] or 0), 2), 'rhr': rows}
        text += (' %s: suma si liniile receptiei se pun cum spune instantaneul.' % REPAIRABLE_MARK) if plan \
            else ' Nu se repara automat: ' + ' '.join(reasons)
        out.append({'kind': KIND_TWIN, 'cod': rec['CodAngajament'], 'text': text, 'plan': plan})
        handled.add(idrr)

    # ---- 3: everything else that does not close --------------------------------------------------------------------------
    for idrr in open_ids:
        if idrr in handled:
            continue
        rec, last = ctx['recs'][idrr], chains[idrr][-1]
        out.append({'kind': KIND_OTHER, 'cod': rec['CodAngajament'], 'plan': None,
                    'text': '%s: ultimul instantaneu, IDRH %s din %s, are %s.'
                            % (_describe(ctx, idrr), last['IDRH'], _moment(last['DataH']), _money(last['Total']))})
    return out


# =========================================================================================
# check
# =========================================================================================
def check(conn, db, block):
    columns = _schema(conn, db)
    missing = _missing(columns, REQUIRED)
    if missing:
        return [block(db, SUMMARY_TITLE, ['rezultat'], [['Verificarea nu poate rula: lipsesc ' + ', '.join(missing) + '.']],
                      'Nu se creeaza si nu se modifica structura bazei.')]
    found = _findings(_load(conn))
    rows = []
    for kind in (KIND_MOVED, KIND_TWIN, KIND_OTHER):
        mine = [f for f in found if f['kind'] == kind]
        rows.append([kind, len(mine), sum(1 for f in mine if f['plan']), MEANING[kind]])
    blocks = [block(db, SUMMARY_TITLE, ['problema', PROBLEMS_COLUMN, REPAIRABLE_COLUMN, 'ce inseamna'], rows,
                    'Doar citire. Zero peste tot = toate lanturile se inchid. Daca apar cazuri reparabile apar caseta pentru cod si butonul '
                    '«Repara receptiile»: scrieti codul angajamentului din lista si apasati-l (o singura baza, un singur cod, o tranzactie).')]
    if found:
        headers = ['problema', 'cod angajament', 'ce s-a gasit']
        blocks.append(block(db, DETAIL_TITLE, headers, [headers] + [[f['kind'], f['cod'], f['text']] for f in found],
                            'Toate cazurile sunt listate. Nimic nu a fost schimbat.', raw=True))
    return blocks


# =========================================================================================
# repair
# =========================================================================================
_ORD_READ_SQL = (
    'SELECT T.IDORDTBLP AS id,O.NrORD AS nr,O.DataORD AS dt,T.CodIndicator AS ind,T.TotalReceptii AS stored,T.Ramas AS ramas,'
    'ROUND(COALESCE((SELECT SUM(R.DIF) FROM FX_Receptii R JOIN FX_Receptii_H H ON R.IDRH=H.IDRH '
    'WHERE H.CodAngajament=O.CodAngajament AND COALESCE(H.Sters,0)=0 AND R.CodIndicator=T.CodIndicator '
    'AND DATE(R.Data)<=DATE(O.DataORD)),0),2) AS read_now '
    'FROM FX_ORD_TBL T JOIN FX_ORD O ON O.IDORDP=T.IDORDP WHERE O.CodAngajament=%s ORDER BY O.DataORD,O.NrORD,T.IDORDTBLP')


def _ord_reads(conn, cod):
    """IDORDTBLP -> the line and the reception figure the ordonantare reads for it NOW (the sum of qFX_ORD_REC_ANT, ord_edit.py)."""
    return {r['id']: r for r in _rows(conn, _ORD_READ_SQL, (cod,))}


def repair(conn, db, param, block):
    """WRITES. All or nothing; whatever `_findings` does not clear stays as it is and is listed at the end."""
    from routes.forexe.prelucrare_asociere import recalculeaza_final
    from routes.forexe.prelucrare_pasi import step4d_calculeaza_dif
    cod = _valid_code(param)
    columns = _schema(conn, db)
    missing = _missing(columns, REQUIRED)
    if missing:
        raise AdminError('Repararea nu poate rula: lipsesc ' + ', '.join(missing) + '.')
    wanted = list(REQUIRED)
    engines = {(r['db'], r['table']): str(r['engine'] or '').upper() for r in _rows(conn,
        'SELECT TABLE_SCHEMA AS db,TABLE_NAME AS `table`,ENGINE AS engine FROM information_schema.TABLES '
        'WHERE (TABLE_SCHEMA=%s AND TABLE_NAME IN (' + ','.join(['%s'] * len(wanted)) + ')) '
        "OR (TABLE_SCHEMA=%s AND TABLE_NAME='Jurnal')", tuple([db] + wanted + [COMMON_DB]))}
    if any(engines.get(target) != 'INNODB' for target in [(db, t) for t in wanted] + [(COMMON_DB, 'Jurnal')]):
        raise AdminError('Repararea cere tabele InnoDB (receptii, instantanee, ordonantari si Jurnal), ca totul sa se aplice intr-o singura '
                         'tranzactie. Structura nu se modifica automat.')
    conn.start_transaction(isolation_level='READ COMMITTED')
    try:
        # The rows of this code stay locked from the re-check until the commit.
        _rows(conn, 'SELECT IDRR FROM FX_Receptii_R WHERE CodAngajament=%s FOR UPDATE', (cod,))
        _rows(conn, 'SELECT IDRH FROM FX_Receptii_H WHERE CodAngajament=%s FOR UPDATE', (cod,))
        _rows(conn, 'SELECT T.IDORDTBLP FROM FX_ORD_TBL T JOIN FX_ORD O ON O.IDORDP=T.IDORDP WHERE O.CodAngajament=%s FOR UPDATE', (cod,))
        ctx = _load(conn, cod)
        if not ctx['recs']:
            raise AdminError('Angajamentul «%s» nu are receptii in baza %s.' % (cod, db))
        todo = [f for f in _findings(ctx) if f['plan']]
        if not todo:
            raise AdminError('Codul %s nu are nimic de reparat automat. Nu s-a schimbat nimic.' % cod)
        moves = [f['plan'] for f in todo if f['plan']['type'] == 'move']
        rewrites = [f['plan'] for f in todo if f['plan']['type'] == 'rewrite']
        before = _ord_reads(conn, cod) if moves else {}
        done, saved = [], []

        for plan in rewrites:
            rec = ctx['recs'][plan['idrr']]
            saved.append('receptia %s inainte: SumaAntet %s, linii %s' % (plan['idrr'], rec['SumaAntet'], ctx['rhr_rows'].get(plan['idrr'])))
            if _exec(conn, 'UPDATE FX_Receptii_R SET SumaAntet=%s WHERE IDRR=%s AND CodAngajament=%s AND ROUND(SumaAntet,2)=%s',
                     (plan['total'], plan['idrr'], cod, round(float(rec['SumaAntet'] or 0), 2))) != 1:
                raise AdminError('Datele s-au schimbat in timpul repararii. Nu s-a aplicat nimic; reluati verificarea.')
            for idrhr, ind, old, new in plan['rhr']:
                if old != new and _exec(conn, 'UPDATE FX_Receptii_RHR SET Valoare=%s WHERE IDRHR=%s AND IDRR=%s AND ROUND(COALESCE(Valoare,0),2)=%s',
                                        (new, idrhr, plan['idrr'], old)) != 1:
                    raise AdminError('Datele s-au schimbat in timpul repararii. Nu s-a aplicat nimic; reluati verificarea.')
            done.append('receptia %s pusa la %s (linii: %s)' % (plan['idrr'], _money(plan['total']),
                        ', '.join('%s %s' % (ind, _money(new)) for _i, ind, _o, new in plan['rhr'])))

        touched = []
        for plan in moves:
            saved.append('instantanee %s erau pe receptia %s' % (plan['idrhs'], plan['from']))
            if _exec(conn, 'UPDATE FX_Receptii_H SET IDRR=%s WHERE CodAngajament=%s AND IDRR=%s AND IDRH IN (' + ','.join(['%s'] * len(plan['idrhs'])) + ')',
                     tuple([plan['to'], cod, plan['from']] + plan['idrhs'])) != len(plan['idrhs']):
                raise AdminError('Datele s-au schimbat in timpul repararii. Nu s-a aplicat nimic; reluati verificarea.')
            done.append('instantaneele %s mutate de pe receptia %s pe receptia %s' % (', '.join(map(str, plan['idrhs'])), plan['from'], plan['to']))
            touched.extend((plan['from'], plan['to']))
        if touched:
            cur = conn.cursor(dictionary=True)
            try:
                for idrr in sorted(set(touched)):
                    recalculeaza_final(cur, idrr)       # Final / Partial, as the association window does
                    step4d_calculeaza_dif(cur, cod, idrr)   # DIFH / DIF, as step 4d does
            finally:
                cur.close()

        ord_done, ord_left = [], []
        if moves:
            after = _ord_reads(conn, cod)
            for key, was in before.items():
                delta = round(float(after[key]['read_now']) - float(was['read_now']), 2)
                if delta == 0:
                    continue
                label = 'ordonantarea %s din %s, linia %s: ' % (was['nr'], _day(was['dt']), was['ind'])
                if round(float(was['stored'] or 0), 2) != round(float(was['read_now']), 2):
                    ord_left.append(label + 'cifra salvata %s nu era cea citita (%s); NU s-a atins, se verifica de mana.'
                                    % (_money(was['stored']), _money(was['read_now'])))
                    continue
                _exec(conn, 'UPDATE FX_ORD_TBL SET TotalReceptii=ROUND(TotalReceptii+%s,2),Ramas=ROUND(Ramas+%s,2) WHERE IDORDTBLP=%s',
                      (delta, delta, key))
                saved.append('%stotal %s, ramas %s' % (label, was['stored'], was['ramas']))
                ord_done.append(label + 'total receptii %s -> %s, ramas %s -> %s.'
                                % (_money(was['stored']), _money(float(was['stored'] or 0) + delta),
                                   _money(was['ramas']), _money(float(was['ramas'] or 0) + delta)))

        _journal(conn, 'receptii_lanturi_repara', db, cod,
                 ('Facut: %s. Ordonantari: %s. Valorile de dinainte: %s. Ordonantari lasate: %s.'
                  % (' | '.join(done), ' | '.join(ord_done) or '-', ' | '.join(saved), ' | '.join(ord_left) or '-'))[:60000])
        lines = list(done)
        lines.append('')
        lines.append('Ordonantari: %s.' % (_plural(len(ord_done), 'linie corectata', 'linii corectate') if ord_done else 'nicio linie nu s-a schimbat'))
        lines.extend('  - ' + t for t in ord_done)
        if ord_left:
            lines.append('NU s-au atins:')
            lines.extend('  - ' + t for t in ord_left)
        if ord_done:
            lines.append('Ordonantarile cu linii corectate trebuie listate (tiparite) din nou; nu trebuie generate din nou.')
        result = block(db, 'Reparare lanturi de receptii pentru %s' % cod, ['text'], [[t] for t in lines],
                       'Tot ce s-a facut este in Jurnal, cu valorile de dinainte. Mai jos, starea de acum.', raw=True)
        rest = [f for f in _findings(_load(conn, cod))]
        state = [['%s: %s' % (f['kind'], f['text'])] for f in rest] or [['Toate lanturile receptiilor codului se inchid.']]
        after_block = block(db, 'Lanturi ramase pentru %s' % cod, ['text'], state, 'Citit dupa reparare, in aceeasi tranzactie.', raw=True)
        conn.commit()
        return [result, after_block]
    except Exception:
        conn.rollback()
        raise
