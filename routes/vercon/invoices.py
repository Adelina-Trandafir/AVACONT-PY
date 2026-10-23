"""DC-wide supplier invoices. Local receipts retain their stock context and identities.

All mutations run in the caller's transaction. No DDL, implicit merging or historical recalculation.
"""
from decimal import Decimal
from utils import vercon_suppliers as suppliers
import json

from . import numeric, protocol
from .documents import _all, _one, _int, _dec, _text, _fail, _scope, _iso
from .nomenclatoare import NomError

TABLES = ('VC_FACTURI', 'VC_FACTURI_LINII', 'VC_NIR_FACTURI', 'VC_FACTURI_REPARTIZARI', 'VC_NIR_CALCULE')


def ready(cursor):
    rows = _all(cursor, 'SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() '
                'AND TABLE_NAME IN (%s,%s,%s,%s,%s)', TABLES)
    return {r['TABLE_NAME'].upper() for r in rows} == set(TABLES)


def require(cursor):
    if not ready(cursor):
        raise NomError('INVOICE_SCHEMA_REQUIRED', 'Aplicați manual sql/VC_FACTURI_NIR_20261016.sql și sincronizați schema în DC.', 503)
    from . import supplier_identity
    supplier_identity.require(cursor)


def load(cursor, invoice_id, version=None, lock=False):
    row = _one(cursor, 'SELECT * FROM VC_FACTURI WHERE ID=%s' + (' FOR UPDATE' if lock else ''), (invoice_id,))
    if not row or row['REUNITA_IN']:
        raise NomError('NOT_FOUND', 'Factura nu există sau a fost reunită cu altă factură.', 404)
    if version is not None and _int(version) != row['VERSION']:
        raise NomError('CONFLICT', 'Factura a fost modificată. Reîncărcați factura.', 409)
    return row


def link_of(cursor, ctx, nir_id):
    return _one(cursor, 'SELECT * FROM VC_NIR_FACTURI WHERE SUBUNIT_ID=%s AND GESTIUNE_ID=%s '
                'AND EXERCITIU_ID=%s AND NIR_ID=%s', (*_scope(ctx), nir_id))


def amounts(cursor, invoice_id):
    return _all(cursor, 'SELECT r.FACTURA_LINIE_ID AS line, SUM(r.CANT) AS quantity, SUM(r.VALOARE) AS amount '
                'FROM VC_FACTURI_REPARTIZARI r JOIN VC_NIR_FACTURI f ON f.ID=r.NIR_FACTURA_ID '
                'JOIN VC_NIR n ON n.SubunitId=f.SUBUNIT_ID AND n.GestiuneId=f.GESTIUNE_ID '
                'AND n.ExercitiuId=f.EXERCITIU_ID AND n.IdIntrare=f.NIR_ID '
                'WHERE f.FACTURA_ID=%s AND n.Anulat=0 GROUP BY r.FACTURA_LINIE_ID', (invoice_id,))


def refresh_state(cursor, invoice_id, strict=False):
    row = load(cursor, invoice_id, lock=True)
    lines = _all(cursor, 'SELECT * FROM VC_FACTURI_LINII WHERE FACTURA_ID=%s ORDER BY ID', (invoice_id,))
    received = {r['line']: r for r in amounts(cursor, invoice_id)}
    complete = bool(lines)
    for line in lines:
        value = received.get(line['ID'], {})
        quantity = value.get('quantity') or Decimal(0)
        amount = value.get('amount') or Decimal(0)
        if not line['RECONSTRUITA'] and line['CANT'] is not None and quantity > line['CANT']:
            raise NomError('INVOICE_OVERALLOCATED', 'Cantitatea recepționată depășește linia de factură „%s”.' % line['DENUMIRE'], 409)
        if not line['RECONSTRUITA'] and line['VALOARE'] is not None and amount > line['VALOARE']:
            raise NomError('INVOICE_OVERALLOCATED', 'Valoarea recepționată depășește ținta liniei „%s”. Corectați repartizarea înainte de salvare.' % line['DENUMIRE'], 409)
        if line['CANT'] is None or line['VALOARE'] is None or quantity != line['CANT'] or amount != line['VALOARE']:
            complete = False
    total = sum((r['amount'] or Decimal(0) for r in received.values()), Decimal(0))
    complete = complete and bool(row['TOTAL_CONFIRMAT']) and row['TOTAL'] is not None and total == row['TOTAL']
    if strict and not complete:
        raise NomError('INVOICE_INCOMPLETE', 'Factura nu este complet recepționată sau totalurile/cantitățile nu sunt egale cu cele confirmate.', 422)
    state = 'RECONCILIATA' if complete else 'MIGRATA' if row['MIGRATA'] and not row['TOTAL_CONFIRMAT'] else 'PARTIAL' if received else 'IN_LUCRU'
    cursor.execute('UPDATE VC_FACTURI SET STARE=%s, VERSION=VERSION+1 WHERE ID=%s', (state, invoice_id))
    return state


def read(cursor, invoice_id):
    row = load(cursor, invoice_id)
    lines = _all(cursor, 'SELECT * FROM VC_FACTURI_LINII WHERE FACTURA_ID=%s ORDER BY ID', (invoice_id,))
    got = {r['line']: r for r in amounts(cursor, invoice_id)}
    receipts = _all(cursor, 'SELECT f.ID AS link, f.SUBUNIT_ID AS subunit, f.GESTIUNE_ID AS gestiune, '
                   'f.EXERCITIU_ID AS exercise, f.NIR_ID AS id, f.TOTAL_SURSA AS source_total, '
                   'n.Numar AS number, n.NumarSub AS sub_number, n.Data AS day, n.Valoare AS total, n.Anulat AS cancelled, '
                   's.Name AS subunit_name, g.Name AS gestiune_name, '
                   '(SELECT GROUP_CONCAT(DISTINCT m.Masina ORDER BY m.Masina SEPARATOR \', \') FROM VC_Intrari i '
                   'JOIN VC_ProduseP pp ON pp.SubunitId=i.SubunitId AND pp.GestiuneId=i.GestiuneId AND pp.IdProdus=i.IdProdus '
                   'JOIN VC_Produse p ON p.SubunitId=pp.SubunitId AND p.GestiuneId=pp.GestiuneId AND p.ID=pp.ID '
                   'JOIN VC_Masini m ON m.SubunitId=p.SubunitId AND m.GestiuneId=p.GestiuneId AND m.IdMasina=p.IdMasina '
                   'WHERE i.SubunitId=n.SubunitId AND i.GestiuneId=n.GestiuneId AND i.ExercitiuId=n.ExercitiuId AND i.IdIntrare=n.IdIntrare) AS account '
                   'FROM VC_NIR_FACTURI f JOIN VC_NIR n ON n.SubunitId=f.SUBUNIT_ID AND n.GestiuneId=f.GESTIUNE_ID '
                   'AND n.ExercitiuId=f.EXERCITIU_ID AND n.IdIntrare=f.NIR_ID '
                   'JOIN VC_Subunitati s ON s.SubunitId=f.SUBUNIT_ID JOIN VC_Gestiuni g ON g.GestiuneId=f.GESTIUNE_ID '
                   'WHERE f.FACTURA_ID=%s ORDER BY f.SUBUNIT_ID,f.GESTIUNE_ID,n.Data,n.Numar', (invoice_id,))
    def plain(value):
        return None if value is None else format(Decimal(value), 'f')
    return {'id': row['ID'], 'version': row['VERSION'], 'fiscal_code': suppliers.code(row['CodPartener']), 'number': row['NUMAR'],
            'date': _iso(row['DATA']), 'currency': row['MONEDA'], 'total': plain(row['TOTAL']),
            'confirmed': bool(row['TOTAL_CONFIRMAT']), 'state': row['STARE'], 'migrated': bool(row['MIGRATA']), 'local': bool(row['LOCALA']),
            'received': plain(sum((r['amount'] or Decimal(0) for r in got.values()), Decimal(0))),
            'lines': [{'id': r['ID'], 'name': r['DENUMIRE'], 'um': r['UM'], 'quantity': plain(r['CANT']),
                       'rate': plain(r['COTA']), 'total': plain(r['VALOARE']), 'reconstructed': bool(r['RECONSTRUITA']),
                       'received_quantity': plain(got.get(r['ID'], {}).get('quantity') or 0),
                       'received_total': plain(got.get(r['ID'], {}).get('amount') or 0)} for r in lines],
            'receipts': [{**r, 'day': _iso(r['day']), 'total': plain(r['total']), 'source_total': plain(r['source_total'])} for r in receipts]}


def candidates(cursor, fiscal_code=None, number=None, day=None):
    clauses, params = ['f.REUNITA_IN IS NULL'], []
    for value, clause in ((suppliers.code(fiscal_code), 'f.CodPartener=%s'), (number, 'f.NUMAR=%s'), (day, 'f.DATA=%s')):
        if value not in (None, ''):
            clauses.append(clause)
            params.append(value)
    return _all(cursor, 'SELECT f.ID AS id, f.NUMAR AS number, f.DATA AS date, f.TOTAL AS total, '
                'f.TOTAL_CONFIRMAT AS confirmed, f.STARE AS state, f.VERSION AS version, '
                'p.DenumirePartener AS partner FROM VC_FACTURI f LEFT JOIN ' + suppliers.relation() + ' p ON p.CodFiscal=f.CodPartener '
                'WHERE ' + ' AND '.join(clauses) + ' ORDER BY f.DATA DESC,f.ID DESC LIMIT 200', params)


def clean_header(cursor, raw):
    errors = {}
    partner = suppliers.code(raw.get('fiscal_code'))
    if not suppliers.find(cursor, partner, active_only=True):
        errors['fiscal_code'] = 'Alegeți furnizorul după codul fiscal.'
    number = _text(raw.get('number'), 'number', errors, True)
    day = protocol.to_date(raw.get('date'), 'Data facturii', errors=errors, key='date')
    currency = str(raw.get('currency') or 'RON').strip().upper()
    if len(currency) != 3 or not currency.isalpha():
        errors['currency'] = 'Introduceți codul monedei, de exemplu RON.'
    elif currency != 'RON':
        errors['currency'] = 'Recepțiile sunt exprimate în RON. Conversia valutară nu este definită pentru acest flux.'
    total = _dec(raw.get('total'))
    if total is None or total < 0:
        errors['total'] = 'Introduceți valoarea totală declarată.'
    _fail(errors)
    try:
        total = numeric.to_stored(total)
    except numeric.NumericError as error:
        raise NomError('INVALID', str(error), 422) from None
    return partner, number, day, currency, total


def save(cursor, ctx, raw, request_id=None):
    invoice_id = _int(raw.get('id'))
    before = load(cursor, invoice_id, raw.get('version'), True) if invoice_id else None
    partner, number, day, currency, total = clean_header(cursor, raw)
    if before:
        linked = _one(cursor, 'SELECT 1 FROM VC_NIR_FACTURI WHERE FACTURA_ID=%s LIMIT 1', (invoice_id,))
        if linked and (partner != before['CodPartener'] or number != before['NUMAR'] or day != before['DATA'] or currency != before['MONEDA']):
            raise NomError('INVOICE_LINKED', 'Factura are recepții. Furnizorul, numărul, data și moneda nu se modifică printr-o singură recepție.', 409)
        cursor.execute('UPDATE VC_FACTURI SET TOTAL=%s,TOTAL_CONFIRMAT=1,LOCALA=0,VERSION=VERSION+1 WHERE ID=%s', (total, invoice_id))
    else:
        cursor.execute('INSERT INTO VC_FACTURI (CodPartener,NUMAR,DATA,MONEDA,TOTAL,TOTAL_CONFIRMAT,CREAT_DE) '
                       'VALUES (%s,%s,%s,%s,%s,1,%s)', (partner, number, day, currency, total, ctx['email']))
        invoice_id = cursor.lastrowid
    raw_lines = raw.get('lines')
    if not isinstance(raw_lines, list) or not raw_lines or len(raw_lines) > 500:
        raise NomError('INVALID', 'Factura trebuie să aibă între 1 și 500 de linii.', 422)
    existing = {r['ID']: r for r in _all(cursor, 'SELECT * FROM VC_FACTURI_LINII WHERE FACTURA_ID=%s', (invoice_id,))}
    kept = set()
    for item in raw_lines:
        if not isinstance(item, dict):
            raise NomError('INVALID', 'Linie de factură invalidă.', 422)
        line_id = _int(item.get('id'))
        errors = {}
        name = _text(item.get('name'), 'name', errors, True)
        quantity, amount, rate = _dec(item.get('quantity')), _dec(item.get('total')), _dec(item.get('rate'))
        if 'vat_percent' in item:
            percent = _dec(item['vat_percent'])
            rate = percent / 100 if percent is not None else None
        historical = existing.get(line_id)
        if historical and historical['RECONSTRUITA'] and (quantity, amount, rate, name, str(item.get('um') or '')) == (
                historical['CANT'], historical['VALOARE'], historical['COTA'], historical['DENUMIRE'], historical['UM'] or ''):
            if line_id in kept:
                raise NomError('INVALID', 'Linie istorică repetată.', 422)
            kept.add(line_id)
            continue
        if quantity is None or quantity <= 0 or amount is None or amount < 0 or rate is None or not 0 <= rate <= 1:
            errors['lines'] = 'Completați cantitatea pozitivă, cota și valoarea fiecărei linii.'
        _fail(errors)
        try:
            values = (name, str(item.get('um') or '')[:40], numeric.to_stored(quantity), numeric.to_stored(rate), numeric.to_stored(amount))
        except numeric.NumericError as error:
            raise NomError('INVALID', str(error), 422, {'lines': str(error)}) from None
        if line_id:
            if line_id not in existing or line_id in kept:
                raise NomError('CONFLICT', 'Identitate de linie necunoscută sau repetată.', 409)
            if rate != existing[line_id]['COTA'] and _one(cursor,
                    'SELECT 1 FROM VC_FACTURI_REPARTIZARI r JOIN VC_NIR_FACTURI f ON f.ID=r.NIR_FACTURA_ID '
                    'JOIN VC_NIR n ON n.SubunitId=f.SUBUNIT_ID AND n.GestiuneId=f.GESTIUNE_ID '
                    'AND n.ExercitiuId=f.EXERCITIU_ID AND n.IdIntrare=f.NIR_ID '
                    'WHERE r.FACTURA_LINIE_ID=%s AND n.Anulat=0 LIMIT 1', (line_id,)):
                raise NomError('INVOICE_VAT', 'Cota unei linii recepționate nu se schimbă prin antetul facturii comune.', 409)
            cursor.execute('UPDATE VC_FACTURI_LINII SET DENUMIRE=%s,UM=%s,CANT=%s,COTA=%s,VALOARE=%s,RECONSTRUITA=0 '
                           'WHERE FACTURA_ID=%s AND ID=%s', (*values, invoice_id, line_id))
        else:
            cursor.execute('INSERT INTO VC_FACTURI_LINII (FACTURA_ID,DENUMIRE,UM,CANT,COTA,VALOARE) VALUES (%s,%s,%s,%s,%s,%s)', (invoice_id, *values))
            line_id = cursor.lastrowid
        kept.add(line_id)
    for line_id in set(existing) - kept:
        if _one(cursor, 'SELECT 1 FROM VC_FACTURI_REPARTIZARI WHERE FACTURA_LINIE_ID=%s LIMIT 1', (line_id,)):
            raise NomError('INVOICE_LINKED', 'O linie deja recepționată nu poate fi eliminată.', 409)
        cursor.execute('DELETE FROM VC_FACTURI_LINII WHERE FACTURA_ID=%s AND ID=%s', (invoice_id, line_id))
    total_lines = _one(cursor, 'SELECT SUM(VALOARE) AS total FROM VC_FACTURI_LINII WHERE FACTURA_ID=%s', (invoice_id,))['total']
    if total_lines != total:
        raise NomError('INVOICE_TOTAL', 'Suma valorilor repartizabile ale liniilor trebuie să fie exact ValoareFC.', 422)
    refresh_state(cursor, invoice_id)
    protocol.audit(cursor, ctx, 'invoice.save', 'VC_FACTURI', invoice_id, None, {'total': total, 'lines': len(kept)}, None, request_id)
    return read(cursor, invoice_id)


def prepare_receipt(cursor, ctx, nir_id, header):
    """Serialize concurrent local allocations using the common invoice row."""
    current = link_of(cursor, ctx, nir_id) if nir_id else None
    if nir_id and not current:
        backfill(cursor, ctx, nir_id=nir_id)
        current = link_of(cursor, ctx, nir_id)
    wanted = _int(header.get('FacturaId')) or (current['FACTURA_ID'] if current else None)
    ids = sorted({i for i in (wanted, current['FACTURA_ID'] if current else None) if i})
    for identity in ids:
        load(cursor, identity, lock=True)
    if current:
        current['_allocations'] = {r['INTRARE_ID']: r['FACTURA_LINIE_ID'] for r in _all(cursor,
                                  'SELECT INTRARE_ID,FACTURA_LINIE_ID FROM VC_FACTURI_REPARTIZARI WHERE NIR_FACTURA_ID=%s', (current['ID'],))}
    if wanted:
        invoice = load(cursor, wanted)
        if invoice['LOCALA'] and (not current or current['FACTURA_ID'] != wanted):
            cursor.execute('UPDATE VC_FACTURI SET LOCALA=0,VERSION=VERSION+1 WHERE ID=%s', (wanted,))
        if (suppliers.code(header.get('CodFiscal')), str(header.get('NrFact') or '').strip(), str(header.get('DataFc') or '')[:10]) != (
                invoice['CodPartener'], invoice['NUMAR'], _iso(invoice['DATA'])):
            if invoice['LOCALA'] and current and current['FACTURA_ID'] == wanted:
                cursor.execute('UPDATE VC_FACTURI SET CodPartener=%s,NUMAR=%s,DATA=%s,VERSION=VERSION+1 WHERE ID=%s',
                               (suppliers.code(header.get('CodFiscal')), str(header.get('NrFact') or '').strip(), header.get('DataFc'), wanted))
            else:
                raise NomError('INVOICE_HEADER', 'Furnizorul, numărul și data trebuie să corespundă facturii comune.', 422)
    return wanted, current


def clear_allocations(cursor, ctx, nir_id):
    link = link_of(cursor, ctx, nir_id)
    if link:
        cursor.execute('DELETE FROM VC_FACTURI_REPARTIZARI WHERE NIR_FACTURA_ID=%s', (link['ID'],))


def write_calculation(cursor, ctx, line_id, calculation):
    # A row in this table is the marker for inverse calculation from the gross total.
    cursor.execute('DELETE FROM VC_NIR_CALCULE WHERE SUBUNIT_ID=%s AND GESTIUNE_ID=%s AND EXERCITIU_ID=%s AND INTRARE_ID=%s', (*_scope(ctx), line_id))
    if calculation:
        cursor.execute('INSERT INTO VC_NIR_CALCULE (SUBUNIT_ID,GESTIUNE_ID,EXERCITIU_ID,INTRARE_ID,TOTAL_BRUT,PRET_INITIAL,ORIGINE_JSON) '
                       'VALUES (%s,%s,%s,%s,%s,%s,%s)', (*_scope(ctx), line_id, calculation['gross'], calculation['input_price'], json.dumps(calculation.get('origin'), ensure_ascii=False)))


def receipt_saved(cursor, ctx, nir_id, parsed, invoice_id, current, request_id=None):
    # Resolve origin keys after all lines have persistent IDs, also for opening-stock receipts.
    saved = {line['key']: line['saved_id'] for line in parsed['lines']}
    for line in parsed['lines']:
        calculation = line.get('calculation')
        if calculation and not line.get('preserve'):
            origin = calculation.get('origin') or {}
            if origin.get('split'):
                origin['parent_id'] = saved.get(origin.get('parent_key'), origin.get('parent_id'))
            write_calculation(cursor, ctx, line['saved_id'], calculation)
    if parsed['cols']['TipDoc'] == 'STOC_INITIAL':
        if current or invoice_id:
            raise NomError('INVALID', 'Stocul inițial nu se asociază unei facturi furnizor.', 422)
        return
    new_invoice = not invoice_id
    auto = new_invoice or bool(load(cursor, invoice_id)['LOCALA'])
    # The unified editor created its invoice lines from receipts, rather than
    # from separately entered supplier targets. Only those lines can follow a
    # local edit/undo automatically; imported or explicitly shared targets keep
    # their existing allocation rules. The creation audit identifies this flow.
    owned = set(current.get('_allocations', {}).values()) if current and current['FACTURA_ID'] == invoice_id else set()
    managed = bool(not auto and current and current['FACTURA_ID'] == invoice_id and _one(cursor,
                   "SELECT 1 FROM VC_Audit WHERE Operation='invoice.distribution' AND TableName='VC_FACTURI' AND RowKey=%s LIMIT 1",
                   (str(invoice_id),)))
    if managed and owned:
        shared = _one(cursor, 'SELECT 1 FROM VC_FACTURI_REPARTIZARI WHERE FACTURA_LINIE_ID IN (' +
                      ','.join(['%s'] * len(owned)) + ') AND NIR_FACTURA_ID<>%s LIMIT 1', (*sorted(owned), current['ID']))
        if shared:
            raise NomError('INVOICE_LINE_SHARED', 'O linie a facturii este împărțită cu altă recepție. Repartizarea trebuie corectată împreună.', 409)
    if new_invoice:
        cols = parsed['cols']
        cursor.execute('INSERT INTO VC_FACTURI (CodPartener,NUMAR,DATA,TOTAL,TOTAL_CONFIRMAT,LOCALA,CREAT_DE) '
                       'VALUES (%s,%s,%s,%s,%s,1,%s)', (cols['CodPartener'], cols['NrFact'], cols['DataFc'],
                                                     parsed['declared'], int(parsed['declared'] is not None), ctx['email']))
        invoice_id = cursor.lastrowid
    if current:
        cursor.execute('UPDATE VC_NIR_FACTURI SET FACTURA_ID=%s WHERE ID=%s', (invoice_id, current['ID']))
        link_id = current['ID']
    else:
        cursor.execute('INSERT INTO VC_NIR_FACTURI (FACTURA_ID,SUBUNIT_ID,GESTIUNE_ID,EXERCITIU_ID,NIR_ID) VALUES (%s,%s,%s,%s,%s)',
                       (invoice_id, *_scope(ctx), nir_id))
        link_id = cursor.lastrowid
    allowed = {r['ID']: r for r in _all(cursor, 'SELECT * FROM VC_FACTURI_LINII WHERE FACTURA_ID=%s', (invoice_id,))}
    if managed and any(allowed.get(identity, {}).get('RECONSTRUITA') for identity in owned):
        managed = False
    used = set()
    for line in parsed['lines']:
        line_id = line.get('invoice_line')
        if not line_id and current and current['FACTURA_ID'] == invoice_id:
            line_id = current.get('_allocations', {}).get(line.get('id'))
        if managed and line_id and line_id not in owned:
            raise NomError('INVOICE_LINE', 'Linia facturii nu aparține acestei recepții.', 422)
        if auto or managed:
            values = (line.get('name') or 'Produs recepționat', line['v']['Cant'], line.get('rate'), line['v']['ValoareCUTVA'])
            if line_id in allowed and line_id not in used:
                cursor.execute('UPDATE VC_FACTURI_LINII SET DENUMIRE=%s,CANT=%s,COTA=%s,VALOARE=%s WHERE ID=%s AND FACTURA_ID=%s', (*values, line_id, invoice_id))
            else:
                cursor.execute('INSERT INTO VC_FACTURI_LINII (FACTURA_ID,DENUMIRE,CANT,COTA,VALOARE) VALUES (%s,%s,%s,%s,%s)', (invoice_id, *values))
                line_id = cursor.lastrowid
        elif line_id not in allowed:
            raise NomError('INVOICE_LINE', 'Asociați fiecare rând NIR unei linii din factura comună.', 422)
        elif allowed[line_id]['COTA'] is not None and line.get('rate') != allowed[line_id]['COTA']:
            raise NomError('INVOICE_VAT', 'Cota TVA a recepției nu corespunde liniei facturii comune.', 422)
        cursor.execute('INSERT INTO VC_FACTURI_REPARTIZARI (NIR_FACTURA_ID,FACTURA_LINIE_ID,SUBUNIT_ID,GESTIUNE_ID,EXERCITIU_ID,INTRARE_ID,CANT,VALOARE) '
                       'VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',
                       (link_id, line_id, *_scope(ctx), line['saved_id'], line['v']['Cant'], line['v']['ValoareCUTVA']))
        used.add(line_id)
    if auto or managed:
        for unused in (set(allowed) if auto else owned) - used:
            cursor.execute('DELETE FROM VC_FACTURI_LINII WHERE ID=%s AND FACTURA_ID=%s', (unused, invoice_id))
    if auto:
        cursor.execute('UPDATE VC_FACTURI SET TOTAL=%s,TOTAL_CONFIRMAT=%s,VERSION=VERSION+1 WHERE ID=%s',
                       (parsed['declared'], int(parsed['declared'] is not None), invoice_id))
    if managed and load(cursor, invoice_id)['TOTAL_CONFIRMAT']:
        target = load(cursor, invoice_id)['TOTAL']
        received = sum((row['amount'] or Decimal(0) for row in amounts(cursor, invoice_id)), Decimal(0))
        if target != received:
            raise NomError('INVOICE_TOTAL', 'Totalul recepțiilor (%s) trebuie să rămână exact ValoareFC (%s).' % (received, target), 422,
                           {'h.Valoare': 'Păstrați valoarea repartizată acestui NIR sau corectați rândurile.'})
    refresh_state(cursor, invoice_id)
    if current and current['FACTURA_ID'] != invoice_id:
        refresh_state(cursor, current['FACTURA_ID'])
    protocol.audit(cursor, ctx, 'invoice.receipt', 'VC_FACTURI', invoice_id, None, {'nir': nir_id}, None, request_id)


def enrich(cursor, ctx, doc):
    from .efactura_receipts import annotate
    annotate(cursor, ctx, [doc])
    if not ready(cursor):
        doc['invoice_schema'] = False
        return doc
    doc['invoice_schema'] = True
    meta = {r['INTRARE_ID']: r for r in _all(cursor, 'SELECT c.INTRARE_ID,c.TOTAL_BRUT,c.PRET_INITIAL,c.ORIGINE_JSON FROM VC_NIR_CALCULE c JOIN VC_Intrari i '
            'ON i.SubunitId=c.SUBUNIT_ID AND i.GestiuneId=c.GESTIUNE_ID AND i.ExercitiuId=c.EXERCITIU_ID AND i.ID=c.INTRARE_ID '
            'WHERE c.SUBUNIT_ID=%s AND c.GESTIUNE_ID=%s AND c.EXERCITIU_ID=%s AND i.IdIntrare=%s', (*_scope(ctx), doc['id']))}
    allocations = {r['INTRARE_ID']: r['FACTURA_LINIE_ID'] for r in _all(cursor, 'SELECT r.INTRARE_ID,r.FACTURA_LINIE_ID FROM VC_FACTURI_REPARTIZARI r '
                  'JOIN VC_NIR_FACTURI f ON f.ID=r.NIR_FACTURA_ID WHERE f.SUBUNIT_ID=%s AND f.GESTIUNE_ID=%s AND f.EXERCITIU_ID=%s AND f.NIR_ID=%s', (*_scope(ctx), doc['id']))}
    for line in doc['lines']:
        line['invoice_line'] = allocations.get(line['id'])
        if line['id'] in meta:
            row = meta[line['id']]
            # Keep the API marker used by AUTO locks and undo, derived solely from row existence.
            line.update({'calc_mode': 'GROSS', 'gross_total': format(row['TOTAL_BRUT'], 'f'), 'input_price': format(row['PRET_INITIAL'], 'f')})
            line['auto_origin'] = json.loads(row['ORIGINE_JSON'])
            from .receipt_auto import sign_row
            line['auto_token'] = sign_row(ctx, doc['id'], line)
    link = link_of(cursor, ctx, doc['id'])
    if link:
        invoice = read(cursor, link['FACTURA_ID'])
        doc['invoice'] = invoice
        doc['header']['FacturaId'] = invoice['id']
        if not invoice['local'] and not doc['annulled']:
            from .receipt_invoice_view import other_lines
            doc['other_lines'] = other_lines(cursor, ctx, doc, invoice['id'])
    return doc


def backfill(cursor, ctx, request_id=None, nir_id=None):
    """Attach unlinked historical receipts. Source amounts remain provisional, never summed as an invoice total."""
    rows = _all(cursor, 'SELECT n.* FROM VC_NIR n WHERE n.SubunitId=%s AND n.GestiuneId=%s AND (%s IS NULL OR n.IdIntrare=%s) '
                "AND COALESCE(n.Storno,0)=0 AND n.TipDoc<>'STOC_INITIAL' AND NOT EXISTS "
                '(SELECT 1 FROM VC_NIR_FACTURI f WHERE f.SUBUNIT_ID=n.SubunitId AND f.GESTIUNE_ID=n.GestiuneId '
                'AND f.EXERCITIU_ID=n.ExercitiuId AND f.NIR_ID=n.IdIntrare) ORDER BY n.IdIntrare FOR UPDATE',
                (ctx['subunit'], ctx['gestiune'], nir_id, nir_id))
    for row in rows:
        cursor.execute("INSERT INTO VC_FACTURI (CodPartener,NUMAR,DATA,TOTAL_CONFIRMAT,MIGRATA,STARE,CREAT_DE) VALUES (%s,%s,%s,0,1,'MIGRATA',%s)",
                       (row['CodPartener'], row['NrFact'] or '', row['DataFc'], ctx['email']))
        invoice_id = cursor.lastrowid
        origin = _one(cursor, "SELECT m.SourceId,m.SourceKey FROM VC_IdMap m JOIN VC_ImportSources s ON s.SourceId=m.SourceId "
                      "WHERE s.SubunitId=%s AND s.GestiuneId=%s AND m.TableName='NIR' AND m.Kind='ROW' AND m.TargetId=%s LIMIT 1",
                      (ctx['subunit'], ctx['gestiune'], row['IdIntrare'])) or {}
        cursor.execute('INSERT INTO VC_NIR_FACTURI (FACTURA_ID,SUBUNIT_ID,GESTIUNE_ID,EXERCITIU_ID,NIR_ID,TOTAL_SURSA,SOURCE_ID,SOURCE_KEY) '
                       'VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',
                       (invoice_id, row['SubunitId'], row['GestiuneId'], row['ExercitiuId'], row['IdIntrare'], row['Valoare'], origin.get('SourceId'), origin.get('SourceKey')))
        link_id = cursor.lastrowid
        lines = _all(cursor, 'SELECT i.*,p.DenumireP,p.UM,t.TVA AS rate FROM VC_Intrari i '
                     'LEFT JOIN VC_ProduseP pp ON pp.SubunitId=i.SubunitId AND pp.GestiuneId=i.GestiuneId AND pp.IdProdus=i.IdProdus '
                     'LEFT JOIN VC_Produse p ON p.SubunitId=pp.SubunitId AND p.GestiuneId=pp.GestiuneId AND p.ID=pp.ID '
                     'LEFT JOIN VC_TVA t ON t.SubunitId=i.SubunitId AND t.GestiuneId=i.GestiuneId AND t.IdTVA=i.IDTVA '
                     'WHERE i.SubunitId=%s AND i.GestiuneId=%s AND i.ExercitiuId=%s AND i.IdIntrare=%s',
                     (row['SubunitId'], row['GestiuneId'], row['ExercitiuId'], row['IdIntrare']))
        for line in lines:
            cursor.execute('INSERT INTO VC_FACTURI_LINII (FACTURA_ID,DENUMIRE,UM,CANT,COTA,VALOARE,RECONSTRUITA) VALUES (%s,%s,%s,%s,%s,%s,1)',
                           (invoice_id, line['DenumireP'] or 'Produs sursă', line['UM'], line['Cant'], line['rate'], line['ValoareCUTVA']))
            invoice_line = cursor.lastrowid
            cursor.execute('INSERT INTO VC_FACTURI_REPARTIZARI (NIR_FACTURA_ID,FACTURA_LINIE_ID,SUBUNIT_ID,GESTIUNE_ID,EXERCITIU_ID,INTRARE_ID,CANT,VALOARE) '
                           'VALUES (%s,%s,%s,%s,%s,%s,%s,%s)', (link_id, invoice_line, row['SubunitId'], row['GestiuneId'], row['ExercitiuId'], line['ID'], line['Cant'], line['ValoareCUTVA']))
    protocol.audit(cursor, ctx, 'invoice.backfill', 'VC_FACTURI', None, None, {'receipts': len(rows)}, None, request_id)
    return {'attached': len(rows)}


def merge(cursor, ctx, raw, request_id=None):
    target = _int(raw.get('target'))
    sources = raw.get('sources')
    if not target or not isinstance(sources, list) or not sources or len(sources) > 100:
        raise NomError('INVALID', 'Selectați factura destinație și fragmentele de reunit.', 422)
    versions = {_int(r.get('id')): r.get('version') for r in sources if isinstance(r, dict)}
    versions[target] = raw.get('version')
    rows = {identity: load(cursor, identity, version, True) for identity, version in sorted(versions.items()) if identity}
    dest = rows[target]
    for identity, row in rows.items():
        if identity == target:
            continue
        if (row['CodPartener'], row['NUMAR'], row['DATA'], row['MONEDA']) != (dest['CodPartener'], dest['NUMAR'], dest['DATA'], dest['MONEDA']):
            raise NomError('INVOICE_CANDIDATE', 'Fragmentele trebuie să aibă același furnizor, număr, dată și monedă.', 422)
        if row['TOTAL_CONFIRMAT'] and not row['LOCALA']:
            raise NomError('INVOICE_CONFIRMED', 'O factură globală confirmată nu se reunește automat ca fragment.', 409)
        cursor.execute('UPDATE VC_FACTURI_LINII SET FACTURA_ID=%s WHERE FACTURA_ID=%s', (target, identity))
        cursor.execute('UPDATE VC_NIR_FACTURI SET FACTURA_ID=%s WHERE FACTURA_ID=%s', (target, identity))
        cursor.execute("UPDATE VC_FACTURI SET REUNITA_IN=%s,STARE='REUNITA',VERSION=VERSION+1 WHERE ID=%s", (target, identity))
    cursor.execute('UPDATE VC_FACTURI SET LOCALA=0,TOTAL_CONFIRMAT=0,TOTAL=NULL,VERSION=VERSION+1 WHERE ID=%s', (target,))
    refresh_state(cursor, target)
    protocol.audit(cursor, ctx, 'invoice.merge', 'VC_FACTURI', target, None, {'sources': sorted(rows)}, None, request_id)
    return read(cursor, target)
