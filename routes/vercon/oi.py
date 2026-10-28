"""SLICE-VC07-02/03: the documents of the OI and MF gestiuni -- handover to a holder (BP), transfer between holders (BT) and
write-off (BS, "casare").

Model (the Access one, kept so the migrated history works unchanged):
  * a document is a GROUP of rows that share a document id (IDBP / IDBT / IDBC); there is no header table. The page sees
    one document; the revision of a document is `<largest line id>-<sum of the line revisions>`, which changes whenever its
    lines are rewritten, cancelled or re-priced;
  * the REGISTER (VC_OI_Produse) records who holds what: a positive row is a batch received by a holder (a handover, or the
    receiving side of a transfer), a negative row takes quantity out of a batch (the giving side of a transfer, a
    write-off). A batch is (entry document, product, use account, document price). What a holder holds = the sum of the
    rows of that holder per batch. The register is derived from the documents and is rewritten together with them;
    IDBC identifies the current document for all three families, with IDBP / IDBT as handover / transfer markers.
    IDBO identifies the source BP / BT for transfers and write-offs, including both sides of a transfer;
  * a handover also creates the consumption bon (BC) that takes the goods out of the magazie, so the stock engine of
    VC06 sees it; the register and the stock are never added together (no double count);
  * transfer and write-off take goods only from a batch the holder really holds, at the batch's price, never more than
    the batch holds and never before the batch entered; a transfer's two sides are written together (net zero);
  * a document other documents drew goods from is not edited or cancelled until those are cancelled first;
  * ids of NEW documents come from one counter per gestiune, above every id already used by any of the four id
    columns, so a batch id never collides with another kind of document.
Everything here runs inside the transaction of the route.
"""
from utils.clock import bucharest_today, utc_now
from decimal import Decimal

from . import bon_number, numeric, protocol, oi_custody
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
                         'MAX(t.Anulat) AS annulled, MAX(t.ID) AS version_id, SUM(t.Version) AS version_sum, COUNT(*) AS line_count{old} FROM {tb} t '
                         'LEFT JOIN VC_Clienti c ON c.SubunitId=t.SubunitId AND c.GestiuneId=t.GestiuneId AND c.IDC=t.{h}{oj} '
                         'WHERE t.SubunitId=%s AND t.GestiuneId=%s AND t.ExercitiuId=%s AND t.{k} IS NOT NULL AND t.{k}<>0 GROUP BY t.{k} ORDER BY d DESC, t.{k} DESC').format(
        k=spec['key'], n=spec['num'], d=spec['date'], h=spec['holder'], tb=spec['table'], old=old_sql, oj=old_join), (s, g, e))
    return [{'id': r['id'], 'number': bon_number.display(family, r['number']), 'date': _iso(r['d']), 'holder_id': r['holder_id'], 'holder': r['holder'] or '',
             'old_id': r['old_id'], 'old': r['old_name'] or '', 'total': _money(Decimal(r['total'] or 0), ctx['scale']), 'annulled': bool(r['annulled']),
             'version': '%d-%d' % (r['version_id'], r['version_sum']), 'lines': r['line_count']} for r in rows]


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
    accounts = {r['IdMasina']: r['Masina'] or '' for r in _all(cursor,
                'SELECT IdMasina,Masina FROM VC_Masini WHERE SubunitId=%s AND GestiuneId=%s', (s, g))}
    holders = {r['IDC']: r['Denumire'] or '' for r in _all(cursor,
               'SELECT IDC,Denumire FROM VC_Clienti WHERE SubunitId=%s AND GestiuneId=%s', (s, g))}
    batch_of = _batches_of_document(cursor, ctx, family, doc_id)
    used = {}
    lines = []
    for r in rows:
        info = _one(cursor, 'SELECT pr.DenumireP, pr.UM, pp.COD FROM VC_ProduseP pp LEFT JOIN VC_Produse pr ON pr.SubunitId=pp.SubunitId AND pr.GestiuneId=pp.GestiuneId AND pr.ID=pp.ID '
                            'WHERE pp.SubunitId=%s AND pp.GestiuneId=%s AND pp.IdProdus=%s', (s, g, r['IdProdus'])) or {}
        batch_key = (r['IdProdus'], r['PUTVA'], abs(Decimal(r['Cant'] or 0)))
        origins = batch_of.get(batch_key, [])
        position = used.get(batch_key, 0)
        used[batch_key] = position + 1
        lot = origins[position] if position < len(origins) else None
        if family != 'bp' and lot is None and not first['Anulat']:
            raise NomError('OI_ACCOUNT_ORIGIN', 'Linia %s nu are lotul de proveniență în registru.' % r['ID'], 409)
        account = lot['account'] if lot else r.get('IdMasina')
        lines.append({'id': r['ID'], 'variant': r['IdProdus'], 'name': info.get('DenumireP'), 'um': info.get('UM'), 'code': info.get('COD'),
                      'Cant': _money(r['Cant'], scale), 'PUTVA': _money(r['PUTVA'], scale), 'ValoareCUTVA': _money(r['ValoareCUTVA'], scale),
                      'batch': lot['origin'] if lot else None, 'IdMasina': account,
                      'account': accounts.get(account, '')})
    header = {'Data': _iso(first[spec['date']]), 'Expl': first['Expl'], spec['holder']: first[spec['holder']]}
    if family == 'bp':
        header.update({'IdMasina': first['IdMasina'], 'IdMasinaP': first['IdMasinaP'], 'IdBonConsum': first['IdBonConsum']})
    if 'old' in spec:
        header['IDC_OLD'] = first['IDC_OLD']
    header['labels'] = {spec['holder']: holders.get(first[spec['holder']], ''),
                        'IdMasina': accounts.get(first.get('IdMasina'), ''),
                        'IdMasinaP': accounts.get(first.get('IdMasinaP'), ''),
                        'IDC_OLD': holders.get(first.get('IDC_OLD'), '')}
    header['Valoare'] = _money(sum(Decimal(r['ValoareCUTVA'] or 0) for r in rows), scale)
    return {'family': family, 'id': doc_id, 'version': _rev(rows), 'annulled': any(r['Anulat'] for r in rows), 'reason': first['MotivAnulare'],
            'annulled_by': first['AnulatDe'], 'number': bon_number.display(family, first[spec['num']]), 'header': header, 'lines': lines}


def _batches_of_document(cursor, ctx, family, doc_id):
    """Source IDBOs of a saved transfer/write-off, isolated by its current document family."""
    if family == 'bp':
        return {}
    out = {}
    for r in oi_custody.register_rows(cursor, ctx, document=(family, doc_id)):
        if (family == 'bt' and r['quantity'] <= 0) or (family == 'bs' and r['quantity'] >= 0):
            continue
        key = (r['IdProdus'], r['PUTVA'], abs(r['quantity']))
        out.setdefault(key, []).append({'origin': r['IDBO'], 'account': r['IDM']})
    if any(len({lot['account'] for lot in lots}) > 1 for lots in out.values()):
        raise NomError('OI_ACCOUNT_ORIGIN', 'Liniile bonului au conturi de proveniență diferite și nu pot fi asociate univoc.', 409)
    return out


def lookups(cursor, ctx, family):
    from .documents import lookups as base
    out = base(cursor, ctx, 'bc')
    out['holders'] = out.pop('receivers')
    # Never assume one warehouse account or one account for goods in use.
    # Source eligibility follows the product's account, destination is explicit.
    source_ids = {r['IdMasina'] for r in _all(cursor,
        'SELECT DISTINCT pr.IdMasina FROM VC_Intrari i JOIN VC_NIR n ON n.SubunitId=i.SubunitId AND n.GestiuneId=i.GestiuneId '
        'AND n.ExercitiuId=i.ExercitiuId AND n.IdIntrare=i.IdIntrare JOIN VC_ProduseP pp ON pp.SubunitId=i.SubunitId '
        'AND pp.GestiuneId=i.GestiuneId AND pp.IdProdus=i.IdProdus JOIN VC_Produse pr ON pr.SubunitId=pp.SubunitId '
        'AND pr.GestiuneId=pp.GestiuneId AND pr.ID=pp.ID WHERE i.SubunitId=%s AND i.GestiuneId=%s AND i.ExercitiuId=%s AND n.Anulat=0', _scope(ctx))}
    types = {row['IdMasina']: row['TIP'] for row in _all(cursor,
        'SELECT IdMasina,TIP FROM VC_Masini WHERE SubunitId=%s AND GestiuneId=%s',
        (ctx['subunit'], ctx['gestiune']))}
    warehouse_type, use_type = ('MFM', 'MFF') if ctx['type'] == 'MF' else ('OIM', 'OIF')
    # NULL remains a valid unclassified legacy account. Explicit roles must match.
    out['warehouse_accounts'] = [item for item in out['categories'] if item['id'] in source_ids
                                 and types.get(item['id']) in (None, '', warehouse_type)]
    out['use_accounts'] = [item for item in out['categories'] if types.get(item['id']) in (None, '', use_type)]
    out['today'] = bucharest_today().isoformat()
    return out


# ---- the register ----------------------------------------------------------------------------------------------------
def holder_batches(cursor, ctx, holder, exclude=None, on_date=None):
    return oi_custody.batches(cursor, ctx, holder, exclude, on_date)


def _origin_date(cursor, ctx, origin, product):
    s, g, e = _scope(ctx)
    row = _one(cursor, 'SELECT MIN(d) AS d FROM (SELECT DATABP AS d FROM VC_OI_Predare WHERE SubunitId=%s AND GestiuneId=%s AND ExercitiuId=%s AND IDBP=%s AND IdProdus=%s AND Anulat=0 '
                       'UNION ALL SELECT DATABT FROM VC_OI_Transfer WHERE SubunitId=%s AND GestiuneId=%s AND ExercitiuId=%s AND IDBT=%s AND IdProdus=%s AND Anulat=0) x',
               (s, g, e, origin, product, s, g, e, origin, product))
    return row['d'] if row and row['d'] else None


def eligible(cursor, ctx, holder, exclude=None, on_date=None, account=None):
    """The batches a holder can still give away: [{origin, variant, name, um, qty, PUTVA, date}] -- the picking list of a
    transfer and of a write-off."""
    scale = ctx['scale']
    out = []
    accounts = {row['IdMasina']: row['Masina'] for row in _all(cursor,
                'SELECT IdMasina,Masina FROM VC_Masini WHERE SubunitId=%s AND GestiuneId=%s', (ctx['subunit'], ctx['gestiune']))}
    for batch in sorted(holder_batches(cursor, ctx, holder, exclude, on_date).values(), key=lambda b: (b['origin'], b['product'], b['idm'] or 0)):
        if batch['capacity'] <= 0:
            continue
        if account is not None and batch['idm'] != account:
            continue
        info = _one(cursor, 'SELECT pr.DenumireP, pr.UM FROM VC_ProduseP pp LEFT JOIN VC_Produse pr ON pr.SubunitId=pp.SubunitId AND pr.GestiuneId=pp.GestiuneId AND pr.ID=pp.ID '
                            'WHERE pp.SubunitId=%s AND pp.GestiuneId=%s AND pp.IdProdus=%s', (ctx['subunit'], ctx['gestiune'], batch['product'])) or {}
        day = batch['date']
        out.append({'origin': batch['origin'], 'variant': batch['product'], 'name': info.get('DenumireP'), 'um': info.get('UM'), 'qty': _money(batch['qty'], scale),
                    'left': _money(batch['capacity'], scale), 'PUTVA': _money(batch['putva'], scale), 'date': _iso(day),
                    'IdMasina': batch['idm'], 'account': accounts.get(batch['idm']) or 'Fără cont', 'batch': batch['origin']})
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
    from .oi_number import check
    spec = SPEC[family]
    verdict = check(cursor, ctx, family, manual, exclude)
    number = verdict['suggested'] if manual in (None, '') else _int(manual)
    if not verdict['valid']:
        errors['h.Numar'] = 'Numărul trebuie să fie un întreg pozitiv.'
        return None
    if verdict['duplicate']:
        errors['h.Numar'] = 'Numărul %s este deja folosit.' % number
        return None
    protocol.raise_counter(cursor, ctx, spec['kind'], number)
    return bon_number.text(family, number)   # BP 13, BT 13, BS 13 (casare)


def _outputs(cursor, ctx, family, doc_id, products):
    """True when other documents drew goods from the batches of this one (a transfer or a write-off references it)."""
    if family == 'bs' or not products:
        return False
    return any(row['family'] in ('bt', 'bs') and row['quantity'] < 0
               and row['IDBO'] == doc_id and row['IdProdus'] in products
               for row in oi_custody.events(cursor, ctx, exclude=(family, doc_id)))


def _insert_register(repo, family, doc_id, rows):
    for row in rows:
        repo.insert('VC_OI_Produse', oi_custody.register_values(family, doc_id, row))


def _forget(repo, family, doc_id):
    repo.cursor.execute('DELETE FROM VC_OI_Produse WHERE SubunitId=%s AND GestiuneId=%s AND ' +
                        oi_custody.document_where(family), (repo.subunit, repo.gestiune, doc_id))


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
    elif data.get('IdMasina') not in (None, ''):
        value = _int(data.get('IdMasina'))
        if value is None or not _exists(repo, 'VC_Masini', 'IdMasina', value):
            errors['h.IdMasina'] = 'Alegeți un cont de folosință valid.'
        out['IdMasina'] = value
    out['_date'] = protocol.to_date(data.get('Data'), 'Data bonului', None, errors, 'h.Data')
    out['Expl'] = _text(data.get('Expl'), 'h.Expl', errors, False, 255)
    return out


# ---- BP (handover) ---------------------------------------------------------------------------------------------------
def _bp_lines(repo, ctx, raw_lines, errors, account=None, old_rows=()):
    raw_lines = raw_lines if isinstance(raw_lines, list) else []
    if len(raw_lines) > _MAX_LINES:
        errors['lines'] = 'Bonul conține prea multe linii.'
    if not raw_lines:
        errors['lines'] = 'Bonul nu are nicio linie.'
    old_by_id = {row['ID']: row for row in old_rows}
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
        old = old_by_id.get(_int(raw.get('id')))
        price = old['PUTVA'] if old and old['IdProdus'] == variant else rows[0]['PUTVA']
        if price is None or Decimal(price) < 0:
            errors['l|%s|variant' % key] = 'Produsul nu are un preț valid pentru predare.'
            continue
        try:
            out.append({'variant': variant, **priced_line(cant, Decimal(price), ctx['zec'], ctx['scale'])})
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
    now = utc_now()
    _write_bp(cursor, repo, ctx, doc_id, number, header, lines, None, now)
    protocol.check_timeline(cursor, ctx, {l['variant'] for l in lines})
    oi_custody.check_timeline(cursor, ctx, {l['variant'] for l in lines})
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
    lines = _bp_lines(repo, ctx, body.get('lines'), errors, header.get('IdMasinaP'), rows)
    _fail(errors)
    number = rows[0]['NRBP']
    manual = (body.get('header') or {}).get('Numar')
    if manual not in (None, '') and _int(manual) != bon_number.parse('bp', number):
        number = _number(cursor, ctx, 'bp', manual, errors, exclude=doc_id)
    _fail(errors)
    old_products = {r['IdProdus'] for r in rows}
    # Existing lines keep their own historical price, including repeated variants.
    bc_id = rows[0]['IdBonConsum']
    _forget(repo, 'bp', doc_id)
    repo.delete('VC_OI_Predare', {'IDBP': doc_id})
    _write_bp(cursor, repo, ctx, doc_id, number, header, lines, bc_id, utc_now())
    protocol.check_timeline(cursor, ctx, {l['variant'] for l in lines} | old_products)
    oi_custody.check_timeline(cursor, ctx, {l['variant'] for l in lines} | old_products)
    protocol.audit(cursor, ctx, 'bp.update', 'VC_OI_Predare', doc_id, {'number': rows[0]['NRBP'], 'lines': len(rows)}, {'number': number, 'lines': len(lines)}, None, request_id)
    return {'id': doc_id, 'number': number}


# ---- BT (transfer) and BS (write-off) --------------------------------------------------------------------------------
def _taken_lines(cursor, ctx, family, holder, exclude_id, raw_lines, document_date, errors):
    """Validate the picked batches against what the holder holds (without this document's own rows)."""
    available = holder_batches(cursor, ctx, holder, (family, exclude_id) if exclude_id else None, document_date) if holder else {}
    raw_lines = raw_lines if isinstance(raw_lines, list) else []
    if not raw_lines:
        errors['lines'] = 'Alegeți cel puțin un produs.'
    if len(raw_lines) > _MAX_LINES:
        errors['lines'] = 'Bonul conține prea multe linii.'
    asked, out = {}, []
    for index, raw in enumerate(raw_lines[:_MAX_LINES]):
        raw = raw if isinstance(raw, dict) else {}
        key = _line_key(raw, index)
        origin, variant, cant = _int(raw.get('batch')), _int(raw.get('variant')), _dec(raw.get('Cant'))
        batch_price = _dec(raw.get('PUTVA'))
        # TC account fields are historical hints only. The selected origin determines the account.
        matches = [b for (o, p, a, price), b in available.items() if o == origin and p == variant
                   and (batch_price is None or price == batch_price)]
        batch = matches[0] if len(matches) == 1 else None
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
        batch_key = (origin, variant, batch['idm'], batch['putva'])
        asked[batch_key] = asked.get(batch_key, 0) + quantity
        if asked[batch_key] > batch['capacity']:
            errors['l|%s|Cant' % key] = 'Cantitate peste disponibilul de la data bonului, ținând cont și de documentele ulterioare (%s).' % _money(batch['capacity'], ctx['scale'])
            continue
        entered = batch['date']
        if document_date and entered and document_date < entered:
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
    (_write_bt if family == 'bt' else _write_bs)(repo, ctx, doc_id, number, header, lines, utc_now())
    oi_custody.check_timeline(cursor, ctx)
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
    if manual not in (None, '') and _int(manual) != bon_number.parse(family, number):
        number = _number(cursor, ctx, family, manual, errors, exclude=doc_id)
    _fail(errors)
    lines = _taken_lines(cursor, ctx, family, _giver(family, header), doc_id, body.get('lines'), header['_date'], errors)
    _fail(errors)
    _forget(repo, family, doc_id)
    repo.delete(spec['table'], {spec['key']: doc_id})
    (_write_bt if family == 'bt' else _write_bs)(repo, ctx, doc_id, number, header, lines, utc_now())
    oi_custody.check_timeline(cursor, ctx)
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
    oi_custody.check_timeline(cursor, ctx, {r['IdProdus'] for r in rows})
    protocol.audit(cursor, ctx, family + '.cancel', spec['table'], doc_id, {'Anulat': 0}, {'Anulat': 1, 'Motiv': reason}, None, request_id)
    return {'id': doc_id}


CREATE = {'bp': create_bp, 'bt': lambda *a: create_out('bt', *a), 'bs': lambda *a: create_out('bs', *a)}
EDIT = {'bp': edit_bp, 'bt': lambda *a: edit_out('bt', *a), 'bs': lambda *a: edit_out('bs', *a)}
