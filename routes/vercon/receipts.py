"""Receipt writes. The caller owns the transaction and the verified context."""
from datetime import datetime
from decimal import Decimal

from . import numeric, protocol, receipt_choices, receipt_rules, receipt_number, receipt_auto, receipt_products
from .nomenclatoare import NomError
from .documents import (_int, _dec, _text, _one, _all, _fail, _exists, _scope,
                        _line_key, _account_of_product, _account_of_variant,
                        _NOT_IN_ACCOUNT, _MAX_LINES, receipt_line, total_of,
                        _sub_number, _numarsub_column, _assign_number,
                        _load_for_write, _guard_dependents)

def _clean_nir(cursor, repo, ctx, exercise, body, family, source=None, stored=None, allow_empty=False):
    """Validated header + lines of a receipt (family nir) or of a storno (family storno). Errors are keyed 'h.<field>' /
    'l|<row key>|<field>' so the page can mark them."""
    errors = {}
    header = body.get('header') if isinstance(body.get('header'), dict) else {}
    raw_lines = body.get('lines') if isinstance(body.get('lines'), list) else []
    out = {'cols': {}, 'lines': [], 'declared': None}
    cols = out['cols']
    doc_type = 'NIR' if family == 'storno' else (header.get('TipDoc') or 'NIR')
    if doc_type not in ('NIR', 'STOC_INITIAL'):
        errors['h.TipDoc'] = 'Tip de document necunoscut.'
    cols['TipDoc'] = doc_type
    cols['Storno'] = 1 if family == 'storno' else 0
    # supplier
    partner = None
    if family == 'storno':
        partner = source['IdPartener'] if source else None
    else:
        partner_id = _int(header.get('IdPartener'))
        if doc_type == 'STOC_INITIAL' and partner_id is None:
            partner = None
        elif partner_id is None or not _one(cursor, "SELECT 1 FROM Parteneri WHERE IdPartener=%s AND COALESCE(Ascuns,0)=0 AND NULLIF(TRIM(CodFiscal),'') IS NOT NULL", (partner_id,)):
            errors['h.IdPartener'] = 'Alegeți furnizorul.'
        else:
            partner = partner_id
    cols['IdPartener'] = partner
    if partner:
        row = _one(cursor, 'SELECT DenumirePartener FROM Parteneri WHERE IdPartener=%s', (partner,))
        cols['FurnizorV'] = (row['DenumirePartener'] if row else None)
    else:
        cols['FurnizorV'] = None
    cols['NrFact'] = _text(header.get('NrFact'), 'h.NrFact', errors, required=doc_type == 'NIR' or family == 'storno')
    if doc_type == 'STOC_INITIAL' and not cols['NrFact']:
        cols['NrFact'] = 'STOC INITIAL'
    cols['DataFc'] = protocol.to_date(header.get('DataFc'), 'Data facturii', None, errors, 'h.DataFc')
    cols['Data'] = protocol.to_date(header.get('Data'), 'Data NIR', exercise if ctx['type'] not in ('OI', 'MF') else None, errors, 'h.Data')
    for field, table, pk in (('IdMasina', 'VC_Masini', 'IdMasina'), ('IdComisie', 'VC_ComisieNIR', 'IdComisie')):
        value = _int(header.get(field))
        if header.get(field) not in (None, '') and (value is None or not _exists(repo, table, pk, value)):
            errors['h.' + field] = 'Valoarea aleasă nu există în această gestiune.'
        cols[field] = value
    selected = body.get('accounts')
    if family == 'nir' and selected is not None:
        if not isinstance(selected, list) or not selected or any(_int(value) is None for value in selected):
            errors['h.Accounts'] = 'Bifați cel puțin un cont.'
        selected = {_int(value) for value in selected} if isinstance(selected, list) else set()
        if ctx['type'] in ('MF', 'OI') and any(not receipt_products.account(repo, ctx, identity) for identity in selected):
            errors['h.Accounts'] = 'Alegeți numai conturi MAGAZIE din gestiunea curentă.'
    if family == 'storno':
        cols['IdStorno'] = source['IdIntrare'] if source else None
        if source and cols['DataFc'] and source['DataFc'] and cols['DataFc'] < source['DataFc'].date():
            errors['h.DataFc'] = 'Data storno nu poate fi înaintea facturii storno-ate.'
    declared = _dec(header.get('Valoare'))
    if header.get('Valoare') not in (None, '') and (declared is None or declared < 0):
        errors['h.Valoare'] = 'Introduceți o valoare pozitivă.'
    out['declared'] = declared
    # lines
    if not raw_lines and not allow_empty:
        errors['lines'] = 'Documentul nu are nicio linie.'
    if len(raw_lines) > _MAX_LINES:
        errors['lines'] = 'Cel mult %d linii pe document.' % _MAX_LINES
    mf = ctx['type'] == 'MF' and family == 'nir'
    protected = []
    if mf and stored:
        from .receipt_mf_edit import partition
        raw_lines, protected = partition(repo, raw_lines, stored, errors)
    undo_ids = receipt_auto.permitted_undos(ctx, body.get('id'), _int(body.get('version')), body.get('undo_tokens') or [])
    drafted_products, codes = {}, {str(line.get('COD') or '').strip().casefold() for line in protected if line.get('COD')}
    for index, raw in enumerate(raw_lines[:_MAX_LINES]):
        raw = raw if isinstance(raw, dict) else {}
        key = _line_key(raw, index)
        line = {'key': key, 'id': _int(raw.get('id')), 'invoice_line': _int(raw.get('invoice_line'))}
        old = (stored or {}).get(line['id'])
        drafted = receipt_products.draft(cursor, repo, ctx, raw, errors, key) if family == 'nir' else None
        if raw.get('new_product') is not None:
            identity = _int(raw.get('product'))
            if old or identity is None or identity >= 0 or drafted is None:
                errors['l|%s|product' % key] = 'Produsul nou este incomplet sau invalid.'
            elif identity in drafted_products and drafted_products[identity] != drafted:
                errors['l|%s|product' % key] = 'Același produs nou are definiții diferite în ciornă.'
            else:
                drafted_products[identity] = drafted
                line['new_product'] = drafted
        if family == 'nir' and selected is not None:
            account = drafted['IdMasina'] if drafted else _account_of_product(repo, _int(raw.get('product')))
            if account not in selected and not (account is None and old and unchanged(raw, old, mf)):
                errors['l|%s|product' % key] = 'Produsul nu aparține conturilor bifate.'
        if mf:
            code = str(raw.get('code') or '').strip().casefold()
            if code in codes:
                errors['l|%s|code' % key] = 'Numărul de inventar este repetat în această intrare.'
            if code:
                codes.add(code)
        if old and old.get('calc_mode') == 'GROSS' and (not unchanged(raw, old, mf) or _int(raw.get('invoice_line')) != old.get('invoice_line')):
            if line['id'] not in undo_ids or raw.get('calc_mode') == 'GROSS':
                errors['lines'] = 'Rândurile auto-calculate pot fi numai șterse cu revenire în grupul de origine.'
                continue
        if line['id'] and old is None and stored is not None:
            errors['lines'] = 'O linie nu aparține documentului sau nu mai există.'
            continue
        if old and not mf and not raw.get('recalculate') and unchanged(raw, old, mf):
            line.update({'preserve': True, 'variant': old['variant'], 'product': old['product'], 'IDTVA': old['IDTVA'],
                         'name': old['name'], 'v': {field: Decimal(old[field]) if old[field] is not None else None
                         for field in ('Cant', 'Pret', 'PUTVA', 'Valoare', 'TVA', 'ValoareCUTVA')}})
            vat_rows = repo.select('VC_TVA', {'IdTVA': old['IDTVA']})
            line['rate'] = Decimal(vat_rows[0]['TVA'] or 0) if vat_rows else None
            if old.get('calc_mode') == 'GROSS':
                line['calculation'] = {'mode': 'GROSS', 'gross': Decimal(old['gross_total']), 'input_price': Decimal(old['input_price']), 'origin': old.get('auto_origin')}
            out['lines'].append(line)
            continue
        bad = lambda field, message, key=key: errors.__setitem__('l|%s|%s' % (key, field), message)
        cant, pret = _dec(raw.get('Cant')), _dec(raw.get('Pret'))
        if cant is None or cant <= 0:
            bad('Cant', 'Cantitatea trebuie să fie pozitivă.')
        if pret is None or pret < 0:
            bad('Pret', 'Prețul nu poate fi negativ.')
        rate = None
        if family == 'nir':
            tva_id = _int(raw.get('IDTVA'))
            tva_row = repo.select('VC_TVA', {'IdTVA': tva_id}) if tva_id is not None else []
            if not tva_row:
                bad('IDTVA', 'Alegeți cota TVA.')
            else:
                rate = Decimal(tva_row[0]['TVA'] or 0)
                line['rate'] = rate
                line['IDTVA'] = tva_id
        if mf:
            product = _int(raw.get('product'))
            if product is None or not drafted and not _exists(repo, 'VC_Produse', 'ID', product, True):
                bad('product', 'Alegeți produsul.')
            line['product'] = product
            line['COD'] = _text(raw.get('code'), 'l|%s|code' % key, errors, True, 255)
            line['data_intrarii'] = protocol.to_date(raw.get('data_intrarii'), 'Data intrării', None, errors, 'l|%s|data_intrarii' % key)
            dnf, df = _int(raw.get('DNF')), _int(raw.get('DF'))
            if dnf is None or dnf < 1:
                bad('DNF', 'Durata normală trebuie să fie de cel puțin 1 lună.')
            if df is None or df < 0:
                bad('DF', 'Durata folosită este obligatorie și nu poate fi negativă.')
            if cant is not None and cant != cant.to_integral_value():
                bad('Cant', 'Un mijloc fix se recepționează în bucăți întregi.')
            if cant is not None and cant != 1:
                bad('Cant', 'Fiecare activ MF are propriul rând, cu cantitatea 1.')
            if old and product != old['product']:
                bad('product', 'Identitatea unui activ existent nu se schimbă. Scoateți activul și adăugați unul nou dacă dependențele permit.')
            if line['data_intrarii'] and cols.get('Data') and line['data_intrarii'] > cols['Data']:
                bad('data_intrarii', 'Data intrării MF nu poate depăși data NIR-ului.')
            if line.get('COD'):
                used = repo.select('VC_ProduseP', {'COD': line['COD']}, columns=('IdProdus',))
                if any(r['IdProdus'] != (old or {}).get('variant') for r in used):
                    bad('code', 'Numărul de inventar este deja folosit de alt activ.')
                if any(r.get('COD') == line['COD'] for r in out['lines']):
                    bad('code', 'Numărul de inventar este repetat în ciornă.')
            # The shared classification catalogue stores years; asset DNF is in months.
            classification = receipt_products.classification(cursor, repo, ctx, product, drafted)
            if not classification or classification['DNF_MIN'] is None or classification['DNF_MAX'] is None:
                bad('DNF', 'Alegeți o clasificare MF cu limite de durată înainte de DNF.')
            if classification and classification['DNF_MIN'] is not None and classification['DNF_MAX'] is not None and dnf is not None and not classification['DNF_MIN'] * 12 <= dnf <= classification['DNF_MAX'] * 12:
                bad('DNF', receipt_products.duration_message(classification['code'], classification['DNF_MIN'], classification['DNF_MAX']))
            line['DNF'], line['DF'] = dnf, df or 0
        elif family == 'nir' and 'product' in raw:
            product = _int(raw.get('product'))
            if product is None or not drafted and not _exists(repo, 'VC_Produse', 'ID', product, True):
                bad('product', 'Alegeți produsul.')
            line['product'] = product
        else:
            variant = _int(raw.get('variant'))
            if variant is None or not _exists(repo, 'VC_ProduseP', 'IdProdus', variant, True):
                bad('variant', 'Alegeți produsul.')
            line['variant'] = variant
            if family != 'nir' and variant is not None and cols['IdMasina'] is not None and _account_of_variant(repo, variant) != cols['IdMasina']:
                bad('variant', _NOT_IN_ACCOUNT)
        if mf and old and not raw.get('recalculate') and unchanged(raw, old, mf):
            line.update({'preserve': True, 'variant': old['variant'], 'name': old['name'],
                         'ValoareLuna': _dec(old.get('ValoareLuna')),
                         'v': {field: Decimal(old[field]) if old[field] is not None else None
                               for field in ('Cant', 'Pret', 'PUTVA', 'Valoare', 'TVA', 'ValoareCUTVA')}})
            if old.get('calc_mode') == 'GROSS':
                line['calculation'] = {'mode': 'GROSS', 'gross': Decimal(old['gross_total']), 'input_price': Decimal(old['input_price']), 'origin': old.get('auto_origin')}
            out['lines'].append(line)
            continue
        if cant is not None and pret is not None and (rate is not None or family == 'storno'):
            try:
                if family == 'nir':
                    line['v'] = receipt_line(cant, pret, rate, ctx['zec'], ctx['scale'])
                    if old and not mf and not old.get('calc_mode') and all(
                            _dec(raw.get(field)) == _dec(old.get(field)) for field in ('product', 'IDTVA', 'Pret')):
                        # A quantity split must retain the variant of the units already issued.
                        unit = _dec(old.get('PUTVA'))
                        if unit is not None:
                            line['variant'] = old['variant']
                            line['v']['PUTVA'] = unit
                            line['v']['ValoareCUTVA'] = numeric.to_stored(numeric.calc_round(cant * unit, ctx['zec']))
                            line['v']['TVA'] = line['v']['ValoareCUTVA'] - line['v']['Valoare']
                    if raw.get('calc_mode') == 'GROSS':
                        receipt_auto.verify_row(ctx, body.get('id'), raw)
                        gross = _dec(raw.get('gross_total'))
                        input_price = _dec(raw.get('input_price'))
                        if gross is None or input_price is None or input_price < 0:
                            bad('Pret', 'Corecția nu are o valoare sau un preț inițial valid.')
                        else:
                            initial = receipt_line(cant, input_price, rate, ctx['zec'], ctx['scale'])
                            if abs(gross - initial['ValoareCUTVA']) > 1:
                                bad('Pret', 'Corecția depășește 1 leu.')
                            line['v'] = receipt_rules.gross_values(cant, gross, rate, ctx['zec'])
                            line['calculation'] = {'mode': 'GROSS', 'gross': gross, 'input_price': input_price, 'origin': raw.get('auto_origin')}
                    if mf and line.get('DNF'):
                        line['ValoareLuna'] = receipt_products.monthly(line)
            except numeric.NumericError as error:
                bad('Cant', str(error))
        product_id = line.get('product')
        if product_id is not None:
            products = repo.select('VC_Produse', {'ID': product_id}, columns=('DenumireP',)) if not drafted else []
            line['name'] = drafted['DenumireP'] if drafted else products[0]['DenumireP'] if products else ''
        out['lines'].append(line)
    out['lines'].extend(protected)
    if len(out['lines']) > _MAX_LINES:
        errors['lines'] = 'Cel mult %d linii pe document, inclusiv activele cu ieșire păstrate.' % _MAX_LINES
    ids = [r['id'] for r in out['lines'] if r.get('id')]
    if len(ids) != len(set(ids)):
        errors['lines'] = 'Aceeași linie salvată apare de mai multe ori în cerere.'
    for old_id, old in (stored or {}).items():
        if old.get('calc_mode') == 'GROSS' and old_id not in ids and old_id not in undo_ids:
            errors['lines'] = 'Ștergerea corecției cere revenirea cantității în grupul de origine.'
    _fail(errors)
    if family == 'nir':
        accounts = {row['new_product']['IdMasina'] if row.get('new_product') else _account_of_product(repo, row['product']) if row.get('product') is not None
                    else _account_of_variant(repo, row.get('variant')) for row in out['lines']}
        if ctx['type'] in ('MF', 'OI') and any(not receipt_products.account(repo, ctx, identity) for identity in accounts):
            raise NomError('INVALID', 'Produsele MF/OI trebuie să aparțină unor conturi MAGAZIE din aceeași gestiune.', 422)
        cols['IdMasina'] = next(iter(accounts)) if len(accounts) == 1 else None
    return out


def _nir_header_values(ctx, parsed, total_int, number):
    cols = dict(parsed['cols'])
    cols['Valoare'] = total_int
    cols['Numar'] = -number if cols['Storno'] else number
    return cols


def _nir_total(ctx, parsed, errors_ok=True):
    return receipt_rules.exact_total(parsed)


def create_nir(cursor, repo, ctx, exercise, body, request_key, request_id, parsed=None):
    from . import invoices
    invoices.require(cursor)
    body = {**body, 'id': None}
    parsed = parsed if parsed is not None else _clean_nir(cursor, repo, ctx, exercise, body, 'nir')
    errors = {}
    header = body.get('header') or {}
    sub = _sub_number(header.get('NumarSub'), errors)
    if sub and not _numarsub_column(cursor, ctx):
        errors['h.Numar'] = 'Sub-numerele NIR nu sunt disponibile în această bază de date.'
    number = None
    if not errors:
        number = receipt_number.assign(cursor, ctx, header.get('Numar'), sub, parsed['cols']['Data'])
    _fail(errors)
    total_int = _nir_total(ctx, parsed)
    invoice_id, current = invoices.prepare_receipt(cursor, ctx, None, header)
    receipt_rules.simulate(cursor, repo, ctx, parsed)
    now = datetime.now()
    values = _nir_header_values(ctx, parsed, total_int, number)
    if _numarsub_column(cursor, ctx):
        values['NumarSub'] = sub
    nir_id = repo.insert('VC_NIR', values)
    created_products = {}
    for line in parsed['lines']:
        receipt_products.materialize(cursor, repo, ctx, line, created_products)
        line['saved_id'] = _write_receipt_line(cursor, repo, ctx, nir_id, line, now)
    invoices.receipt_saved(cursor, ctx, nir_id, parsed, invoice_id, current, request_id)
    protocol.freeze(cursor, ctx)
    result = {'id': nir_id, 'number': number}
    protocol.audit(cursor, ctx, 'nir.create', 'VC_NIR', nir_id, None, {'header': parsed['cols'], 'total': total_int, 'lines': len(parsed['lines'])}, request_key, request_id)
    return result


def _has_numarsub(cursor):
    row = _one(cursor, "SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='VC_NIR' AND COLUMN_NAME='NumarSub' LIMIT 1")
    return bool(row)


def _write_receipt_line(cursor, repo, ctx, nir_id, line, now, existing=None):
    """Insert a receipt line (and, on MF, the new asset: variant + register row). `existing` = the stored line when editing."""
    v = line['v']
    if line.get('preserve') and existing:
        return existing['ID']
    variant = line.get('variant')
    if ctx['type'] == 'MF':
        line['ValoareLuna'] = receipt_products.monthly(line)
        if existing is None:
            variant = repo.insert('VC_ProduseP', {'ID': line['product'], 'PUTVA': v['PUTVA'], 'COD': line['COD'], 'DataSalvare': now})
            repo.insert('VC_ProduseM', {'IdProdus': variant, 'DataIntrare': line['data_intrarii'], 'DNF': line['DNF'], 'DF': line['DF'], 'ValoareLuna': line['ValoareLuna']})
        else:
            variant = existing['IdProdus']
            repo.update('VC_ProduseP', {'PUTVA': v['PUTVA'], 'COD': line['COD'], 'DataSalvare': now}, {'IdProdus': variant}, bump_version=True)
            repo.update('VC_ProduseM', {'DataIntrare': line['data_intrarii'], 'DNF': line['DNF'], 'DF': line['DF'], 'ValoareLuna': line['ValoareLuna']}, {'IdProdus': variant}, bump_version=True)
    elif line.get('product') is not None:
        variant = receipt_choices.price_variant(repo, line['product'], v['PUTVA'], now, existing)
    values = {'IdIntrare': nir_id, 'IdProdus': variant, 'IDTVA': line['IDTVA'], 'Cant': v['Cant'], 'Pret': v['Pret'], 'TVA': v['TVA'],
              'Valoare': v['Valoare'], 'PUTVA': v['PUTVA'], 'ValoareCUTVA': v['ValoareCUTVA'], 'DataSalvare': now}
    line_id = repo.insert('VC_Intrari', values) if existing is None else _update_line(repo, 'VC_Intrari', existing['ID'], values)
    from . import invoices
    invoices.write_calculation(cursor, ctx, line_id, line.get('calculation'))
    return line_id


def _update_line(repo, table, line_id, values):
    values = {k: v for k, v in values.items() if k not in ('IdIntrare', 'IdBonConsum')}
    if repo.update(table, values, {'ID': line_id}, bump_version=True) != 1:
        raise NomError('CONFLICT', 'Documentul a fost modificat de altcineva. Reîncărcați documentul.', 409)
    return line_id


def edit_nir(cursor, repo, ctx, exercise, doc_id, body, request_id):
    from . import invoices
    invoices.require(cursor)
    body = {**body, 'id': doc_id}
    table, pk, before = _load_for_write(repo, ctx, 'nir', doc_id, body.get('version'))
    _guard_dependents(cursor, ctx, 'nir', doc_id, 'edit')
    previous = saved_document(cursor, repo, ctx, doc_id)
    old_lines = {r['id']: r for r in previous['lines']}
    parsed = _clean_nir(cursor, repo, ctx, exercise, body, 'nir', stored=old_lines)
    errors = {}
    header = body.get('header') or {}
    manual = header.get('Numar')
    number = abs(before['Numar'] or 0)
    before_sub = int(before.get('NumarSub') or 0)
    sub = _sub_number(header.get('NumarSub'), errors) if 'NumarSub' in header else before_sub   # an old client that sends none keeps it
    if sub and not _numarsub_column(cursor, ctx):
        errors['h.Numar'] = 'Sub-numerele NIR nu sunt disponibile în această bază de date.'
    wanted = manual if manual not in (None, '') else number
    old_day = before['Data'].date() if hasattr(before['Data'], 'date') else before['Data']
    changed_number = _int(str(wanted)) != number or sub != before_sub
    changed_day = parsed['cols']['Data'] != old_day
    if changed_day and old_day and parsed['cols']['Data'].year != old_day.year:
        errors['h.Data'] = 'Data NIR nu poate muta documentul în altă serie anuală.'
    if not errors and (changed_number or changed_day):
        number = receipt_number.assign(cursor, ctx, wanted, sub, parsed['cols']['Data'], doc_id)
    _fail(errors)
    economic = len(parsed['lines']) != len(old_lines) or any(not r.get('preserve') for r in parsed['lines'])
    same_declared = parsed['declared'] == before['Valoare']
    total_int = before['Valoare'] if not economic and same_declared else _nir_total(ctx, parsed)
    invoice_id, current = invoices.prepare_receipt(cursor, ctx, doc_id, header)
    moved_stock_date = str(header.get('DataFc') or '')[:10] != str(previous['header']['DataFc'])[:10]
    if economic or moved_stock_date:
        receipt_rules.simulate(cursor, repo, ctx, parsed, doc_id)
    invoices.clear_allocations(cursor, ctx, doc_id)
    stored = _all(cursor, 'SELECT ID, IdProdus FROM VC_Intrari WHERE SubunitId=%s AND GestiuneId=%s AND ExercitiuId=%s AND IdIntrare=%s', (*_scope(ctx), doc_id))
    by_id = {r['ID']: r for r in stored}
    touched = {r['IdProdus'] for r in stored}
    now = datetime.now()
    kept = set()
    created_products = {}
    for line in parsed['lines']:
        receipt_products.materialize(cursor, repo, ctx, line, created_products)
        existing = by_id.get(line['id']) if line['id'] else None
        if line['id'] and existing is None:
            raise NomError('CONFLICT', 'O linie a documentului nu mai există. Reîncărcați documentul.', 409)
        kept_id = _write_receipt_line(cursor, repo, ctx, doc_id, line, now, existing)
        line['saved_id'] = kept_id
        kept.add(kept_id)
        touched.add(line.get('variant') or (existing or {}).get('IdProdus'))
    for row in stored:
        if row['ID'] not in kept:
            invoices.write_calculation(cursor, ctx, row['ID'], None)
            repo.delete('VC_Intrari', {'ID': row['ID']})
            if ctx['type'] == 'MF':
                _drop_asset(repo, row['IdProdus'])
    touched.update(r['IdProdus'] for r in _all(cursor, 'SELECT IdProdus FROM VC_Intrari WHERE SubunitId=%s AND GestiuneId=%s AND ExercitiuId=%s AND IdIntrare=%s', (*_scope(ctx), doc_id)))
    values = {k: v for k, v in _nir_header_values(ctx, parsed, total_int, number).items() if k not in ('Storno', 'TipDoc')}
    if not changed_number:
        values['Numar'] = before['Numar']
    if _numarsub_column(cursor, ctx):
        values['NumarSub'] = sub
    if repo.update('VC_NIR', values, {'IdIntrare': doc_id, 'Version': before['Version']}, bump_version=True) != 1:
        raise NomError('CONFLICT', 'Documentul a fost modificat de altcineva. Reîncărcați documentul.', 409)
    if economic or moved_stock_date:
        protocol.check_timeline(cursor, ctx, touched)
    invoices.receipt_saved(cursor, ctx, doc_id, parsed, invoice_id, current, request_id)
    protocol.audit(cursor, ctx, 'nir.update', 'VC_NIR', doc_id, {'Numar': before['Numar'], 'Valoare': before['Valoare'], 'NrFact': before['NrFact']},
                   {'header': parsed['cols'], 'total': total_int, 'lines': len(parsed['lines'])}, None, request_id)
    return {'id': doc_id, 'number': number}


def saved_document(cursor, repo, ctx, doc_id):
    from .documents import get_document
    from . import invoices
    return invoices.enrich(cursor, ctx, get_document(cursor, repo, ctx, 'nir', doc_id))


def unchanged(raw, old, mf):
    for field in ('product', 'IDTVA', 'Cant', 'Pret'):
        if _dec(raw.get(field)) != _dec(old.get(field)):
            return False
    for field in ('calc_mode', 'gross_total', 'input_price'):
        if str(raw.get(field) or '') != str(old.get(field) or ''):
            return False
    if mf:
        for field in ('code', 'data_intrarii', 'DNF', 'DF'):
            if str(raw.get(field) or '') != str(old.get(field) or ''):
                return False
    return True


def _drop_asset(repo, variant):
    try:
        repo.delete('VC_ProduseM', {'IdProdus': variant})
        repo.delete('VC_ProduseP', {'IdProdus': variant})
    except Exception as error:   # a foreign key refuses it: the asset is used somewhere else
        if getattr(error, 'errno', None) == 1451:
            raise NomError('DOC_LINKED', 'Un mijloc fix al recepției este folosit în alt document și nu poate fi scos.', 409)
        raise


