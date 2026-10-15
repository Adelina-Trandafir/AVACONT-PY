"""SLICE-VC07-02/03: the documents of the OI and MF gestiuni -- handover to a holder (BP), transfer between holders (BT) and
write-off (BS, "casare").

Model (the Access one, kept so the migrated history works unchanged):
  * a document is a GROUP of rows that share a document id (IDBP / IDBT / IDBC); there is no header table. The page sees
    one document; the revision of a document is `<largest line id>-<sum of the line revisions>`, which changes whenever its
    lines are rewritten, cancelled or re-priced;
  * the REGISTER (VC_OI_Produse) records who holds what: a positive row is a batch received by a holder (a handover, or the
    receiving side of a transfer), a negative row takes quantity out of a batch (the giving side of a transfer, a
    write-off). A batch is (id of the document it entered the holder with, product). What a holder holds = the sum of the
    rows of that holder per batch. The register is derived from the documents and is rewritten together with them;
  * a handover also creates the consumption bon (BC) that takes the goods out of the magazie, so the stock engine of
    VC06 sees it; the register and the stock are never added together (no double count);
  * transfer and write-off take goods only from a batch the holder really holds, at the batch's price, never more than
    the batch holds and never before the batch entered; a transfer's two sides are written together (net zero);
  * a document other documents drew goods from is not edited or cancelled until those are cancelled first;
  * ids of NEW documents come from one counter per gestiune, above every id already used by any of the four id
    columns, so a batch id never collides with another kind of document.
Everything here runs inside the transaction of the route.
"""
from datetime import datetime
from decimal import Decimal

from . import numeric, protocol
from .documents import (_NOT_IN_ACCOUNT, _account_of_variant, _all, _dec, _exists, _fail, _int, _line_key, _money, _name_join, _one, _scope, _text, _iso,
                        _MAX_LINES, priced_line, total_of)
from .nomenclatoare import NomError

FAMILIES_OI = ('bp', 'bt', 'bs')
SPEC = {
    'bp': {'table': 'VC_OI_Predare', 'key': 'IDBP', 'num': 'NRBP', 'date': 'DATABP', 'holder': 'IDC_BP', 'kind': 'BP'},
    'bt': {'table': 'VC_OI_Transfer', 'key': 'IDBT', 'num': 'NRBT', 'date': 'DATABT', 'holder': 'IDC_BT', 'old': 'IDC_OLD', 'kind': 'BT'},
    'bs': {'table': 'VC_OI_Casare', 'key': 'IDBC', 'num': 'NRBC', 'date': 'DATABC', 'holder': 'IDC_BC', 'kind': 'BS'},
}
AUDIT_TABLE = {family: spec['table'] for family, spec in SPEC.items()}


# ---- reading ---------------------------------------------------------------------------------------------------------
def _rev(rows):
    return '%d-%d' % (max(r['ID'] for r in rows), sum(int(r['Version']) for r in rows))


def list_documents(cursor, ctx, family):
    spec = SPEC[family]
    s, g, e = _scope(ctx)
    old_sql = ', MIN(co.Denumire) AS old_name, MIN(t.IDC_OLD) AS old_id' if 'old' in spec else ', NULL AS old_name, NULL AS old_id'
    old_join = ' LEFT JOIN VC_Clienti co ON co.SubunitId=t.SubunitId AND co.GestiuneId=t.GestiuneId AND co.IDC=t.IDC_OLD' if 'old' in spec else ''
    rows = _all(cursor, ('SELECT t.{k} AS id, MIN(t.{n}) AS number, MIN(t.{d}) AS d, MIN(t.{h}) AS holder_id, MIN(c.Denumire) AS holder, SUM(t.ValoareCUTVA) AS total, '
                         'MAX(t.Anulat) AS annulled, CONCAT(MAX(t.ID), \'-\', SUM(t.Version)) AS version, COUNT(*) AS lines{old} FROM {tb} t '
                         'LEFT JOIN VC_Clienti c ON c.SubunitId=t.SubunitId AND c.GestiuneId=t.GestiuneId AND c.IDC=t.{h}{oj} '
                         'WHERE t.SubunitId=%s AND t.GestiuneId=%s AND t.ExercitiuId=%s AND t.{k} IS NOT NULL AND t.{k}<>0 GROUP BY t.{k} ORDER BY d DESC, t.{k} DESC').format(
        k=spec['key'], n=spec['num'], d=spec['date'], h=spec['holder'], tb=spec['table'], old=old_sql, oj=old_join), (s, g, e))
    return [{'id': r['id'], 'number': r['number'], 'date': _iso(r['d']), 'holder_id': r['holder_id'], 'holder': r['holder'] or '',
             'old_id': r['old_id'], 'old': r['old_name'] or '', 'total': _money(Decimal(r['total'] or 0), ctx['scale']), 'annulled': bool(r['annulled']),
             'version': r['version'], 'lines': r['lines']} for r in rows]


def _rows(cursor, ctx, family, doc_id):
    spec = SPEC[family]
    s, g, e = _scope(ctx)
    return _all(cursor, 'SELECT t.* FROM {tb} t WHERE t.SubunitId=%s AND t.GestiuneId=%s AND t.ExercitiuId=%s AND t.{k}=%s ORDER BY t.ID'.format(tb=spec['table'], k=spec['key']), (s, g, e, doc_id))


def get_document(cursor, repo, ctx, family, doc_id):
    spec = SPEC[family]
    s, g, e = _scope(ctx)
    scale = ctx['scale']
    rows = _rows(cursor, ctx, family, doc_id)
    if not rows:
        raise NomError('NOT_FOUND', 'Documentul nu mai există.', 404)
    first = rows[0]
    batch_of = _batches_of_document(cursor, ctx, family, doc_id)
    used = {}
    lines = []
    for r in rows:
        info = _one(cursor, 'SELECT pr.DenumireP, pr.UM, pp.COD FROM VC_ProduseP pp LEFT JOIN VC_Produse pr ON pr.SubunitId=pp.SubunitId AND pr.GestiuneId=pp.GestiuneId AND pr.ID=pp.ID '
                            'WHERE pp.SubunitId=%s AND pp.GestiuneId=%s AND pp.IdProdus=%s', (s, g, r['IdProdus'])) or {}
        origins = batch_of.get(r['IdProdus'], [])
        position = used.get(r['IdProdus'], 0)
        used[r['IdProdus']] = position + 1
        lines.append({'id': r['ID'], 'variant': r['IdProdus'], 'name': info.get('DenumireP'), 'um': info.get('UM'), 'code': info.get('COD'),
                      'Cant': _money(r['Cant'], scale), 'PUTVA': _money(r['PUTVA'], scale), 'ValoareCUTVA': _money(r['ValoareCUTVA'], scale),
                      'batch': origins[position] if position < len(origins) else None, 'IdMasina': r.get('IdMasina')})
    header = {'Data': _iso(first[spec['date']]), 'Expl': first['Expl'], spec['holder']: first[spec['holder']]}
    if family == 'bp':
        header.update({'IdMasina': first['IdMasina'], 'IdMasinaP': first['IdMasinaP'], 'IdBonConsum': first['IdBonConsum']})
    if 'old' in spec:
        header['IDC_OLD'] = first['IDC_OLD']
    header['Valoare'] = _money(sum(Decimal(r['ValoareCUTVA'] or 0) for r in rows), scale)
    return {'family': family, 'id': doc_id, 'version': _rev(rows), 'annulled': any(r['Anulat'] for r in rows), 'reason': first['MotivAnulare'],
            'annulled_by': first['AnulatDe'], 'number': first[spec['num']], 'header': header, 'lines': lines}


def _batches_of_document(cursor, ctx, family, doc_id):
    """{product: [origin batch ids]} of a saved transfer / write-off, read from the register rows it wrote (best effort: a
    migrated document whose register rows differ simply shows no batch)."""
    if family == 'bp':
        return {}
    column, sign = ('IDBT', '>') if family == 'bt' else ('IDBC', '<')
    rows = _all(cursor, 'SELECT IdProdus, IDBO FROM VC_OI_Produse WHERE SubunitId=%s AND GestiuneId=%s AND {c}=%s AND Cant {s} 0 ORDER BY IDOI'.format(c=column, s=sign),
                (ctx['subunit'], ctx['gestiune'], doc_id))
    out = {}
    for r in rows:
        out.setdefault(r['IdProdus'], []).append(r['IDBO'])
    return out


def lookups(cursor, ctx, family):
    from .documents import lookups as base
    out = base(cursor, ctx, 'bc')
    out['holders'] = out.pop('receivers')
    out['today'] = datetime.now().strftime('%Y-%m-%d')
    return out


# ---- the register ----------------------------------------------------------------------------------------------------
def holder_batches(cursor, ctx, holder, exclude=None):
    """{(origin, product): {origin, product, qty, putva, idm}} of what `holder` holds now. `exclude` = (family, id) leaves a
    document's own rows out (the document being edited)."""
    rows = _all(cursor, 'SELECT IDOI, IDBP, IDBT, IDBC, IDBO, IDM, IdProdus, Cant, PUTVA FROM VC_OI_Produse WHERE SubunitId=%s AND GestiuneId=%s AND IDC_PTC=%s ORDER BY IDOI',
                (ctx['subunit'], ctx['gestiune'], holder))
    column = {'bp': 'IDBP', 'bt': 'IDBT', 'bs': 'IDBC'}.get(exclude[0]) if exclude else None
    batches = {}
    for r in rows:
        if column and (r[column] or 0) == exclude[1]:
            continue
        quantity = Decimal(r['Cant'] or 0)
        origin = (r['IDBP'] or r['IDBT']) if quantity > 0 else r['IDBO']
        if not origin:
            continue
        batch = batches.setdefault((origin, r['IdProdus']), {'origin': origin, 'product': r['IdProdus'], 'qty': 0, 'putva': None, 'idm': None})
        batch['qty'] += quantity
        if quantity > 0 and batch['putva'] is None:
            batch['putva'], batch['idm'] = r['PUTVA'], r['IDM']
    return {k: b for k, b in batches.items() if b['qty'] > 0 and b['putva'] is not None}


def _origin_date(cursor, ctx, origin, product):
    s, g, e = _scope(ctx)
    row = _one(cursor, 'SELECT MIN(d) AS d FROM (SELECT DATABP AS d FROM VC_OI_Predare WHERE SubunitId=%s AND GestiuneId=%s AND ExercitiuId=%s AND IDBP=%s AND IdProdus=%s AND Anulat=0 '
                       'UNION ALL SELECT DATABT FROM VC_OI_Transfer WHERE SubunitId=%s AND GestiuneId=%s AND ExercitiuId=%s AND IDBT=%s AND IdProdus=%s AND Anulat=0) x',
               (s, g, e, origin, product, s, g, e, origin, product))
    return row['d'] if row and row['d'] else None


def eligible(cursor, ctx, holder, exclude=None):
    """The batches a holder can still give away: [{origin, variant, name, um, qty, PUTVA, date}] -- the picking list of a
    transfer and of a write-off."""
    scale = ctx['scale']
    out = []
    for batch in sorted(holder_batches(cursor, ctx, holder, exclude).values(), key=lambda b: (b['origin'], b['product'])):
        info = _one(cursor, 'SELECT pr.DenumireP, pr.UM FROM VC_ProduseP pp LEFT JOIN VC_Produse pr ON pr.SubunitId=pp.SubunitId AND pr.GestiuneId=pp.GestiuneId AND pr.ID=pp.ID '
                            'WHERE pp.SubunitId=%s AND pp.GestiuneId=%s AND pp.IdProdus=%s', (ctx['subunit'], ctx['gestiune'], batch['product'])) or {}
        day = _origin_date(cursor, ctx, batch['origin'], batch['product'])
        out.append({'origin': batch['origin'], 'variant': batch['product'], 'name': info.get('DenumireP'), 'um': info.get('UM'), 'qty': _money(batch['qty'], scale),
                    'left': _money(batch['qty'], scale), 'PUTVA': _money(batch['putva'], scale), 'date': _iso(day)})
    return out


def _next_id(cursor, ctx):
    """The id of a new document: one counter per gestiune, above every id column already in use."""
    s, g = ctx['subunit'], ctx['gestiune']
    top = 0
    for table, columns in (('VC_OI_Predare', ('IDBP',)), ('VC_OI_Transfer', ('IDBT',)), ('VC_OI_Casare', ('IDBC',)), ('VC_OI_Produse', ('IDBP', 'IDBT', 'IDBC', 'IDBO'))):
        for column in columns:
            row = _one(cursor, 'SELECT MAX(%s) AS m FROM %s WHERE SubunitId=%%s AND GestiuneId=%%s' % (column, table), (s, g))
            top = max(top, int(row['m'] or 0))
    cursor.execute('INSERT INTO VC_Numbering (SubunitId, GestiuneId, ExercitiuId, Kind, LastNumber) VALUES (%s,%s,%s,%s,0) ON DUPLICATE KEY UPDATE Kind=Kind',
                   (s, g, ctx['exercitiu'], 'OIID'))
    cursor.execute('SELECT LastNumber FROM VC_Numbering WHERE SubunitId=%s AND GestiuneId=%s AND ExercitiuId=%s AND Kind=%s FOR UPDATE', (s, g, ctx['exercitiu'], 'OIID'))
    new = max(int(cursor.fetchone()['LastNumber']), top) + 1
    cursor.execute('UPDATE VC_Numbering SET LastNumber=%s, Version=Version+1 WHERE SubunitId=%s AND GestiuneId=%s AND ExercitiuId=%s AND Kind=%s', (new, s, g, ctx['exercitiu'], 'OIID'))
    return new


def _number(cursor, ctx, family, manual, errors, exclude=None):
    spec = SPEC[family]

    def taken(text):
        sql = 'SELECT 1 FROM {tb} WHERE SubunitId=%s AND GestiuneId=%s AND ExercitiuId=%s AND {n}=%s AND Anulat=0'.format(tb=spec['table'], n=spec['num'])
        params = list(_scope(ctx)) + [text]
        if exclude:
            sql += ' AND {k}<>%s'.format(k=spec['key'])
            params.append(exclude)
        return bool(_one(cursor, sql + ' LIMIT 1', params))
    if manual in (None, ''):
        return str(protocol.next_number(cursor, ctx, spec['kind'], lambda n: taken(str(n))))
    number = _int(manual)
    if number is None or number < 1:
        errors['h.Numar'] = 'Numărul trebuie să fie un întreg pozitiv.'
        return None
    if taken(str(number)):
        errors['h.Numar'] = 'Numărul %s este deja folosit.' % number
        return None
    protocol.raise_counter(cursor, ctx, spec['kind'], number)
    return str(number)


def _outputs(cursor, ctx, family, doc_id, products):
    """True when other documents drew goods from the batches of this one (a transfer or a write-off references it)."""
    if family == 'bs' or not products:
        return False
    marks = ','.join(str(int(p)) for p in products)   # integers read from our own rows
    column = {'bp': 'IDBP', 'bt': 'IDBT'}[family]
    row = _one(cursor, 'SELECT 1 FROM VC_OI_Produse WHERE SubunitId=%s AND GestiuneId=%s AND Cant<0 AND IDBO=%s AND IdProdus IN (' + marks + ') AND COALESCE(' + column + ',0)<>%s LIMIT 1',
               (ctx['subunit'], ctx['gestiune'], doc_id, doc_id))
    return bool(row)


def _insert_register(repo, family, doc_id, rows):
    for row in rows:
        repo.insert('VC_OI_Produse', {'IDBP': doc_id if family == 'bp' else 0, 'IDBT': doc_id if family == 'bt' else 0, 'IDBC': doc_id if family == 'bs' else 0, **row})


def _forget(repo, family, doc_id):
    column = {'bp': 'IDBP', 'bt': 'IDBT', 'bs': 'IDBC'}[family]
    repo.delete('VC_OI_Produse', {column: doc_id})


def _cleaned_header(cursor, repo, ctx, family, header, errors):
    spec = SPEC[family]
    out = {}
    data = header if isinstance(header, dict) else {}
    holder = _int(data.get(spec['holder']))
    if holder is None or not _exists(repo, 'VC_Clienti', 'IDC', holder, True):
        errors['h.' + spec['holder']] = 'Alegeți deținătorul.'
    out[spec['holder']] = holder
    if 'old' in spec:
        old = _int(data.get('IDC_OLD'))
        if old is None or not _exists(repo, 'VC_Clienti', 'IDC', old, True):
            errors['h.IDC_OLD'] = 'Alegeți predătorul.'
        elif holder is not None and old == holder:
            errors['h.' + spec['holder']] = 'Primitorul trebuie să fie altcineva decât predătorul.'
        out['IDC_OLD'] = old
    if family == 'bp':
        for field in ('IdMasina', 'IdMasinaP'):
            value = _int(data.get(field))
            if value is None or not _exists(repo, 'VC_Masini', 'IdMasina', value, True):
                errors['h.' + field] = 'Alegeți contul.'
            out[field] = value
    out['_date'] = protocol.to_date(data.get('Data'), 'Data bonului', None, errors, 'h.Data')
    out['Expl'] = _text(data.get('Expl'), 'h.Expl', errors, False, 255)
    return out


# ---- BP (handover) ---------------------------------------------------------------------------------------------------
def _bp_lines(repo, ctx, raw_lines, errors, account=None):
    raw_lines = raw_lines if isinstance(raw_lines, list) else []
    if not raw_lines:
        errors['lines'] = 'Bonul nu are nicio linie.'
    out = []
    for index, raw in enumerate(raw_lines[:_MAX_LINES]):
        raw = raw if isinstance(raw, dict) else {}
        key = _line_key(raw, index)
        variant, cant = _int(raw.get('variant')), _dec(raw.get('Cant'))
        rows = repo.select('VC_ProduseP', {'IdProdus': variant, 'Activ': 1}) if variant is not None else []
        if not rows:
            errors['l|%s|variant' % key] = 'Alegeți produsul.'
            continue
        if account is not None and _account_of_variant(repo, variant) != account:
            errors['l|%s|variant' % key] = _NOT_IN_ACCOUNT
            continue
        if cant is None or cant <= 0:
            errors['l|%s|Cant' % key] = 'Cantitatea trebuie să fie pozitivă.'
            continue
        try:
            out.append({'variant': variant, **priced_line(cant, Decimal(rows[0]['PUTVA'] or 0), ctx['zec'], ctx['scale'])})
        except numeric.NumericError as error:
            errors['l|%s|Cant' % key] = str(error)
    return out


def _write_bp(cursor, repo, ctx, doc_id, number, header, lines, bc_id, now):
    """BC (the stock side), OI_Predare lines and register rows, for a handover. Returns the id of the BC."""
    total = numeric.to_stored(total_of(lines, ctx['scale']), ctx['scale'])
    bc_values = {'IDC': header['IDC_BP'], 'IdMasina': header['IdMasinaP'], 'DataBC': header['_date'], 'Expl': header['Expl'], 'NumarBC': number,
                 'ValoareBC': total, 'Retur': 0, 'DataSalvare': now}
    if bc_id:
        repo.delete('VC_Iesiri', {'IdBonConsum': bc_id})
        repo.update('VC_Iesiri_H', bc_values, {'IdBonConsum': bc_id}, bump_version=True)
    else:
        bc_id = repo.insert('VC_Iesiri_H', bc_values)
    for line in lines:
        repo.insert('VC_Iesiri', {'IdBonConsum': bc_id, 'IdProdus': line['variant'], 'Cant': line['Cant'], 'PUTVA': line['PUTVA'], 'ValoareCUTVA': line['ValoareCUTVA'], 'DataSalvare': now})
        repo.insert('VC_OI_Predare', {'IDBP': doc_id, 'IdBonConsum': bc_id, 'IdMasina': header['IdMasina'], 'IdMasinaP': header['IdMasinaP'], 'IDC_BP': header['IDC_BP'],
                                      'IdProdus': line['variant'], 'NRBP': number, 'DATABP': header['_date'], 'Cant': line['Cant'], 'PUTVA': line['PUTVA'],
                                      'ValoareCUTVA': line['ValoareCUTVA'], 'Expl': header['Expl'], 'DataSalvare': now})
    _insert_register(repo, 'bp', doc_id, [{'IDM': header['IdMasina'], 'IDC_PTC': header['IDC_BP'], 'IdProdus': l['variant'], 'PUTVA': l['PUTVA'], 'Cant': l['Cant'],
                                           'ValoareCUTVA': l['ValoareCUTVA'], 'DataSalvare': now} for l in lines])
    return bc_id


def create_bp(cursor, repo, ctx, exercise, body, request_key, request_id):
    errors = {}
    header = _cleaned_header(cursor, repo, ctx, 'bp', body.get('header'), errors)
    lines = _bp_lines(repo, ctx, body.get('lines'), errors, header.get('IdMasinaP'))
    _fail(errors)
    number = _number(cursor, ctx, 'bp', (body.get('header') or {}).get('Numar'), errors)
    _fail(errors)
    doc_id = _next_id(cursor, ctx)
    now = datetime.now()
    _write_bp(cursor, repo, ctx, doc_id, number, header, lines, None, now)
    protocol.check_timeline(cursor, ctx, {l['variant'] for l in lines})
    protocol.freeze(cursor, ctx)
    protocol.audit(cursor, ctx, 'bp.create', 'VC_OI_Predare', doc_id, None, {'number': number, 'holder': header['IDC_BP'], 'lines': len(lines)}, request_key, request_id)
    return {'id': doc_id, 'number': number}


def _guard(cursor, ctx, family, doc_id, rows):
    if _outputs(cursor, ctx, family, doc_id, {r['IdProdus'] for r in rows}):
        raise NomError('HAS_OUTPUTS', 'Din bun s-au făcut transferuri sau casări: anulați-le mai întâi, apoi puteți modifica sau anula documentul.', 409)


def _load(cursor, repo, ctx, family, doc_id, base_version):
    rows = _all(cursor, 'SELECT t.* FROM {tb} t WHERE t.SubunitId=%s AND t.GestiuneId=%s AND t.ExercitiuId=%s AND t.{k}=%s ORDER BY t.ID FOR UPDATE'.format(
        tb=SPEC[family]['table'], k=SPEC[family]['key']), (*_scope(ctx), doc_id))
    if not rows:
        raise NomError('NOT_FOUND', 'Documentul nu mai există.', 404)
    if any(r['Anulat'] for r in rows):
        raise NomError('CANCELLED', 'Documentul este anulat și nu mai poate fi modificat.', 409)
    if str(base_version or '') != _rev(rows):
        raise NomError('CONFLICT', 'Documentul a fost modificat de altcineva. Reîncărcați documentul.', 409)
    return rows


def edit_bp(cursor, repo, ctx, exercise, doc_id, body, request_id):
    rows = _load(cursor, repo, ctx, 'bp', doc_id, body.get('version'))
    _guard(cursor, ctx, 'bp', doc_id, rows)
    errors = {}
    header = _cleaned_header(cursor, repo, ctx, 'bp', body.get('header'), errors)
    lines = _bp_lines(repo, ctx, body.get('lines'), errors, header.get('IdMasinaP'))
    _fail(errors)
    number = rows[0]['NRBP']
    manual = (body.get('header') or {}).get('Numar')
    if manual not in (None, '') and str(_int(manual)) != str(number):
        number = _number(cursor, ctx, 'bp', manual, errors, exclude=doc_id)
    _fail(errors)
    old_products = {r['IdProdus'] for r in rows}
    old_price = {r['IdProdus']: r['PUTVA'] for r in rows if r['PUTVA'] is not None}
    # a product already on the document keeps the price it was handed over at (the nomenclature may have moved on)
    lines = [({'variant': l['variant'], **priced_line(numeric.from_stored(l['Cant'], ctx['scale']), Decimal(old_price[l['variant']]), ctx['zec'], ctx['scale'])} if l['variant'] in old_price else l) for l in lines]
    bc_id = rows[0]['IdBonConsum']
    _forget(repo, 'bp', doc_id)
    repo.delete('VC_OI_Predare', {'IDBP': doc_id})
    _write_bp(cursor, repo, ctx, doc_id, number, header, lines, bc_id, datetime.now())
    protocol.check_timeline(cursor, ctx, {l['variant'] for l in lines} | old_products)
    protocol.audit(cursor, ctx, 'bp.update', 'VC_OI_Predare', doc_id, {'number': rows[0]['NRBP'], 'lines': len(rows)}, {'number': number, 'lines': len(lines)}, None, request_id)
    return {'id': doc_id, 'number': number}


# ---- BT (transfer) and BS (write-off) --------------------------------------------------------------------------------
def _taken_lines(cursor, ctx, family, holder, exclude_id, raw_lines, document_date, errors):
    """Validate the picked batches against what the holder holds (without this document's own rows)."""
    available = holder_batches(cursor, ctx, holder, (family, exclude_id) if exclude_id else None) if holder else {}
    raw_lines = raw_lines if isinstance(raw_lines, list) else []
    if not raw_lines:
        errors['lines'] = 'Alegeți cel puțin un produs.'
    asked, out = {}, []
    for index, raw in enumerate(raw_lines[:_MAX_LINES]):
        raw = raw if isinstance(raw, dict) else {}
        key = _line_key(raw, index)
        origin, variant, cant = _int(raw.get('batch')), _int(raw.get('variant')), _dec(raw.get('Cant'))
        batch = available.get((origin, variant))
        if batch is None:
            errors['l|%s|variant' % key] = 'Produsul nu mai este la acest deținător.'
            continue
        if cant is None or cant <= 0:
            errors['l|%s|Cant' % key] = 'Cantitatea trebuie să fie pozitivă.'
            continue
        try:
            quantity = numeric.to_stored(cant, ctx['scale'])
        except numeric.NumericError as error:
            errors['l|%s|Cant' % key] = str(error)
            continue
        asked[(origin, variant)] = asked.get((origin, variant), 0) + quantity
        if asked[(origin, variant)] > batch['qty']:
            errors['l|%s|Cant' % key] = 'Cantitate peste cea deținută (%s).' % _money(batch['qty'], ctx['scale'])
            continue
        entered = _origin_date(cursor, ctx, origin, variant)
        if document_date and entered and document_date < entered.date():
            errors['l|%s|Cant' % key] = 'Documentul nu poate fi înaintea intrării produsului la deținător (%s).' % entered.strftime('%d.%m.%Y')
            continue
        putva = Decimal(batch['putva'] or 0)
        value = numeric.to_stored(numeric.calc_round(numeric.from_stored(quantity, ctx['scale']) * numeric.from_stored(putva, ctx['scale']), ctx['zec']), ctx['scale'])
        out.append({'origin': origin, 'variant': variant, 'Cant': quantity, 'PUTVA': putva, 'ValoareCUTVA': value, 'idm': batch['idm']})
    return out


def _write_bt(repo, ctx, doc_id, number, header, lines, now):
    new, old = header['IDC_BT'], header['IDC_OLD']
    for l in lines:
        repo.insert('VC_OI_Transfer', {'IDBT': doc_id, 'IdMasina': l['idm'], 'IDC_BT': new, 'IDC_OLD': old, 'IdProdus': l['variant'], 'NRBT': number, 'DATABT': header['_date'],
                                       'Cant': l['Cant'], 'PUTVA': l['PUTVA'], 'ValoareCUTVA': l['ValoareCUTVA'], 'Expl': header['Expl'], 'DataSalvare': now})
    rows = []
    for l in lines:   # the receiving side, then the giving side: net zero
        rows.append({'IDBO': l['origin'], 'IDM': l['idm'], 'IDC_PTC': new, 'IdProdus': l['variant'], 'PUTVA': l['PUTVA'], 'Cant': l['Cant'], 'ValoareCUTVA': l['ValoareCUTVA'], 'DataSalvare': now})
    for l in lines:
        rows.append({'IDBO': l['origin'], 'IDM': l['idm'], 'IDC_PTC': old, 'IdProdus': l['variant'], 'PUTVA': l['PUTVA'], 'Cant': -l['Cant'], 'ValoareCUTVA': -l['ValoareCUTVA'], 'DataSalvare': now})
    _insert_register(repo, 'bt', doc_id, rows)


def _write_bs(repo, ctx, doc_id, number, header, lines, now):
    holder = header['IDC_BC']
    for l in lines:
        repo.insert('VC_OI_Casare', {'IDBC': doc_id, 'IdMasina': l['idm'], 'IDC_BC': holder, 'IdProdus': l['variant'], 'NRBC': number, 'DATABC': header['_date'],
                                     'Cant': l['Cant'], 'PUTVA': l['PUTVA'], 'ValoareCUTVA': l['ValoareCUTVA'], 'Expl': header['Expl'], 'DataSalvare': now})
    _insert_register(repo, 'bs', doc_id, [{'IDBO': l['origin'], 'IDM': l['idm'], 'IDC_PTC': holder, 'IdProdus': l['variant'], 'PUTVA': l['PUTVA'], 'Cant': -l['Cant'],
                                           'ValoareCUTVA': -l['ValoareCUTVA'], 'DataSalvare': now} for l in lines])


def _giver(family, header):
    return header['IDC_OLD'] if family == 'bt' else header['IDC_BC']


def create_out(family, cursor, repo, ctx, exercise, body, request_key, request_id):
    errors = {}
    header = _cleaned_header(cursor, repo, ctx, family, body.get('header'), errors)
    _fail(errors)
    lines = _taken_lines(cursor, ctx, family, _giver(family, header), None, body.get('lines'), header['_date'], errors)
    _fail(errors)
    number = _number(cursor, ctx, family, (body.get('header') or {}).get('Numar'), errors)
    _fail(errors)
    doc_id = _next_id(cursor, ctx)
    (_write_bt if family == 'bt' else _write_bs)(repo, ctx, doc_id, number, header, lines, datetime.now())
    protocol.freeze(cursor, ctx)
    protocol.audit(cursor, ctx, family + '.create', SPEC[family]['table'], doc_id, None, {'number': number, 'lines': len(lines), 'total': sum(l['ValoareCUTVA'] for l in lines)}, request_key, request_id)
    return {'id': doc_id, 'number': number}


def edit_out(family, cursor, repo, ctx, exercise, doc_id, body, request_id):
    rows = _load(cursor, repo, ctx, family, doc_id, body.get('version'))
    _guard(cursor, ctx, family, doc_id, rows)
    errors = {}
    header = _cleaned_header(cursor, repo, ctx, family, body.get('header'), errors)
    _fail(errors)
    spec = SPEC[family]
    number = rows[0][spec['num']]
    manual = (body.get('header') or {}).get('Numar')
    if manual not in (None, '') and str(_int(manual)) != str(number):
        number = _number(cursor, ctx, family, manual, errors, exclude=doc_id)
    _fail(errors)
    lines = _taken_lines(cursor, ctx, family, _giver(family, header), doc_id, body.get('lines'), header['_date'], errors)
    _fail(errors)
    _forget(repo, family, doc_id)
    repo.delete(spec['table'], {spec['key']: doc_id})
    (_write_bt if family == 'bt' else _write_bs)(repo, ctx, doc_id, number, header, lines, datetime.now())
    protocol.audit(cursor, ctx, family + '.update', spec['table'], doc_id, {'number': rows[0][spec['num']], 'lines': len(rows)}, {'number': number, 'lines': len(lines)}, None, request_id)
    return {'id': doc_id, 'number': number}


# ---- cancel ----------------------------------------------------------------------------------------------------------
def cancel(cursor, repo, ctx, family, doc_id, body, request_id):
    from .documents import _check_cancel_input
    reason = _check_cancel_input(body)
    rows = _load(cursor, repo, ctx, family, doc_id, body.get('version'))
    _guard(cursor, ctx, family, doc_id, rows)
    spec = SPEC[family]
    cursor.execute('UPDATE {tb} SET Anulat=1, MotivAnulare=%s, AnulatDe=%s, AnulatLa=NOW(), Version=Version+1 WHERE SubunitId=%s AND GestiuneId=%s AND ExercitiuId=%s AND {k}=%s'.format(
        tb=spec['table'], k=spec['key']), (reason, ctx['email'], *_scope(ctx), doc_id))
    _forget(repo, family, doc_id)   # the register is derived: a cancelled document holds nothing
    if family == 'bp' and rows[0]['IdBonConsum']:
        cursor.execute('UPDATE VC_Iesiri_H SET Anulat=1, MotivAnulare=%s, AnulatDe=%s, AnulatLa=NOW(), Version=Version+1 WHERE SubunitId=%s AND GestiuneId=%s AND ExercitiuId=%s AND IdBonConsum=%s',
                       (reason, ctx['email'], *_scope(ctx), rows[0]['IdBonConsum']))
    protocol.check_timeline(cursor, ctx, {r['IdProdus'] for r in rows})
    protocol.audit(cursor, ctx, family + '.cancel', spec['table'], doc_id, {'Anulat': 0}, {'Anulat': 1, 'Motiv': reason}, None, request_id)
    return {'id': doc_id}


CREATE = {'bp': create_bp, 'bt': lambda *a: create_out('bt', *a), 'bs': lambda *a: create_out('bs', *a)}
EDIT = {'bp': edit_bp, 'bt': lambda *a: edit_out('bt', *a), 'bs': lambda *a: edit_out('bs', *a)}
