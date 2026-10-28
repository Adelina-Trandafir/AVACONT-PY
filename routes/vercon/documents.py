"""SLICE-VC06-02..05: the documents of the stock gestiuni -- receipt (NIR, with the fixed-asset extension for MF), storno
of a receipt (FCS), consumption bon (BC) and return of a consumption (BR).

One write = one transaction (the route owns it): header, lines, MF rows, numbering, RequestKey, audit and the stock check
are committed together or not at all. Rules shared by the four families:
  * the values of a NEW document are computed here, from the quantity, the price and the VAT rate, once per line at the
    gestiune's ZEC (CONTRACT_CALCULE section 2.2) and stored directly as DECIMAL(20,4); nothing the page computed is trusted;
  * a NIR's declared total must equal the lines exactly (receipt_rules); other document families retain their
    existing header-total rules. Historical unchanged values are preserved;
  * a price is never negative and a quantity is always positive; stock never goes negative (protocol.check_timeline);
  * a saved document is edited only while its period is open, with the revision the page saw, and is never deleted: it is
    cancelled with a reason and stays visible as cancelled;
  * a document another one depends on (a receipt with a storno, a bon with a return, an asset already in
    the OI/MF register) is not edited or cancelled until that dependency is cancelled first. Editing a BC in the active
    month validates the full stock timeline and recalculates its food list atomically; cancellation still requires unlinking it.
Numbers: automatic, unique per gestiune + kind + exercise; a manual number is accepted only when free. A MATERIALE gestiune
can number its BC separately for every account (consumption_numbering).
"""
from utils.clock import utc_now
from utils import vercon_suppliers as suppliers
from decimal import Decimal, InvalidOperation

from . import bon_number, consumption_numbering, consumption_period, document_order, numeric, protocol, receipt_choices
from .nomenclatoare import NomError

FAMILIES = ('nir', 'storno', 'bc', 'br')
_MAX_LINES = 500
_TEXT_MAX = {'NrFact': 255, 'Expl': 255, 'Motiv': 255}


# ---- small helpers ---------------------------------------------------------------------------------------------------
def _dec(raw):
    if raw is None or (isinstance(raw, str) and raw.strip() == ''):
        return None
    try:
        value = Decimal(str(raw).strip().replace(',', '.'))
    except (InvalidOperation, ValueError):
        return None
    return value if value.is_finite() else None


def _int(raw):
    value = _dec(raw)
    return int(value) if value is not None and value == value.to_integral_value() else None


def _text(raw, key, errors, required=False, max_len=255):
    value = None if raw is None else str(raw).strip()
    if not value:
        if required:
            errors[key] = 'Câmpul este obligatoriu.'
        return None
    if len(value) > max_len:
        errors[key] = 'Cel mult %d caractere.' % max_len
        return None
    return value


def _money(stored, scale):
    return None if stored is None else format(numeric.from_stored(stored, scale), 'f')


def _iso(value):
    return value.strftime('%Y-%m-%d') if value is not None else None


def _one(cursor, sql, params=()):
    cursor.execute(sql, params)
    return cursor.fetchone()


def _all(cursor, sql, params=()):
    cursor.execute(sql, params)
    return cursor.fetchall()


def _fail(errors, message='Corectați câmpurile marcate.'):
    if errors:
        raise NomError('INVALID', message, 422, errors)


def _exists(repo, table, pk, value, active=False):
    where = {pk: value}
    if active:
        where['Activ'] = 1
    return bool(repo.select(table, where, columns=(pk,)))


_NOT_IN_ACCOUNT = 'Produsul nu face parte din contul ales. Contul unui produs se atribuie în nomenclatorul Produse.'


def _account_of_product(repo, product):
    """The account (category) a product belongs to; None when it has none."""
    rows = repo.select('VC_Produse', {'ID': product}, columns=('IdMasina',)) if product is not None else []
    return rows[0]['IdMasina'] if rows else None


def _account_of_variant(repo, variant):
    rows = repo.select('VC_ProduseP', {'IdProdus': variant}, columns=('ID',)) if variant is not None else []
    return _account_of_product(repo, rows[0]['ID']) if rows else None


def _line_key(raw, index):
    return str(raw.get('key') if isinstance(raw, dict) and raw.get('key') not in (None, '') else index)


def _scope(ctx):
    return (ctx['subunit'], ctx['gestiune'], ctx['exercitiu'])


# ---- the arithmetic of a line (CONTRACT_CALCULE 2.2) ------------------------------------------------------------------
def receipt_line(cant, pret, rate, zec, scale):
    """Direct decimal quantities, prices and fractional VAT rate; calculation rounds at ZEC."""
    cota = Decimal(rate)
    putva = numeric.calc_round(pret * (1 + cota), zec)
    valoare = numeric.calc_round(cant * pret, zec)
    cutva = numeric.calc_round(cant * putva, zec)
    return {'Cant': numeric.to_stored(cant, scale), 'Pret': numeric.to_stored(pret, scale), 'PUTVA': numeric.to_stored(putva, scale),
            'Valoare': numeric.to_stored(valoare, scale), 'ValoareCUTVA': numeric.to_stored(cutva, scale),
            'TVA': numeric.to_stored(cutva - valoare, scale)}


def priced_line(cant, putva_int, zec, scale):
    """A line priced from a stored price with VAT (a variant's PUTVA, or the price of the document it refers to)."""
    putva = numeric.from_stored(putva_int, scale)
    return {'Cant': numeric.to_stored(cant, scale), 'PUTVA': putva_int,
            'ValoareCUTVA': numeric.to_stored(numeric.calc_round(cant * putva, zec), scale)}


def total_of(lines, scale):
    return sum((numeric.from_stored(line['ValoareCUTVA'], scale) for line in lines), Decimal(0))


# ---- reading ---------------------------------------------------------------------------------------------------------
def _name_join(alias):
    return ('LEFT JOIN VC_ProduseP pp ON pp.SubunitId={a}.SubunitId AND pp.GestiuneId={a}.GestiuneId AND pp.IdProdus={a}.IdProdus '
            'LEFT JOIN VC_Produse pr ON pr.SubunitId=pp.SubunitId AND pr.GestiuneId=pp.GestiuneId AND pr.ID=pp.ID').format(a=alias)


def list_documents(cursor, ctx, family):
    """The summaries the tree is built from, newest first. The page groups them (supplier -> date, receiver -> date)."""
    s, g, e = _scope(ctx)
    scale = ctx['scale']
    if family in ('nir', 'storno'):
        rows = _all(cursor, 'SELECT n.IdIntrare, n.Numar, COALESCE(n.NumarSub,0) AS sub_number, n.Data, n.DataFc, n.NrFact, n.Valoare, n.CodPartener, n.IdStorno, n.TipDoc, n.Anulat, n.Version, '
                            'COALESCE(p.DenumirePartener, n.FurnizorV) AS Partener FROM VC_NIR n LEFT JOIN ' + suppliers.relation() + ' p ON p.CodFiscal=n.CodPartener '
                            'WHERE n.SubunitId=%s AND n.GestiuneId=%s AND n.ExercitiuId=%s AND COALESCE(n.Storno,0)=%s ORDER BY n.Data DESC, n.Numar DESC',
                    (s, g, e, 1 if family == 'storno' else 0))
        return [{'id': r['IdIntrare'], 'number': abs(r['Numar'] or 0), 'sub_number': r['sub_number'], 'date': _iso(r['Data']), 'invoice_date': _iso(r['DataFc']), 'invoice': r['NrFact'],
                 'total': _money(r['Valoare'], scale), 'party_cf': r['CodPartener'], 'party': r['Partener'] or '', 'doc_type': r['TipDoc'],
                 'source': r['IdStorno'], 'annulled': bool(r['Anulat']), 'version': r['Version']} for r in rows]
    rows = _all(cursor, 'SELECT h.IdBonConsum, h.NumarBC, h.DataBC, h.ValoareBC, h.IDC, h.IdMasina, h.IdRetur, h.Anulat, h.Version, c.Denumire AS Primitor, m.Masina AS Cont, '
                        '(SELECT COUNT(*) FROM VC_LegBCLA l WHERE l.SubunitId=h.SubunitId AND l.GestiuneId=h.GestiuneId AND l.ExercitiuId=h.ExercitiuId AND l.IdBonConsum=h.IdBonConsum) AS Legaturi '
                        'FROM VC_Iesiri_H h LEFT JOIN VC_Clienti c ON c.SubunitId=h.SubunitId AND c.GestiuneId=h.GestiuneId AND c.IDC=h.IDC '
                        'LEFT JOIN VC_Masini m ON m.SubunitId=h.SubunitId AND m.GestiuneId=h.GestiuneId AND m.IdMasina=h.IdMasina '
                        'WHERE h.SubunitId=%s AND h.GestiuneId=%s AND h.ExercitiuId=%s AND COALESCE(h.Retur,0)=%s ORDER BY h.DataBC DESC, h.IdBonConsum DESC',
                (s, g, e, 1 if family == 'br' else 0))
    return [{'id': r['IdBonConsum'], 'number': bon_number.display(family, r['NumarBC']), 'date': _iso(r['DataBC']), 'total': _money(r['ValoareBC'], scale),
             'receiver_id': r['IDC'], 'receiver': r['Primitor'] or '', 'category_id': r['IdMasina'], 'category': r['Cont'] or '', 'source': r['IdRetur'],
             'source_missing': family == 'br' and r['IdRetur'] is None,
             'source_label': 'Fără bon sursă' if family == 'br' and r['IdRetur'] is None else None,
             'linked': bool(r['Legaturi']), 'annulled': bool(r['Anulat']), 'version': r['Version']} for r in rows]


def get_document(cursor, repo, ctx, family, doc_id):
    s, g, e = _scope(ctx)
    scale = ctx['scale']
    if family in ('nir', 'storno'):
        rows = repo.select('VC_NIR', {'IdIntrare': doc_id})
        if not rows or bool(rows[0].get('Storno') or 0) != (family == 'storno'):
            raise NomError('NOT_FOUND', 'Documentul nu mai există.', 404)
        h = rows[0]
        partner = suppliers.find(cursor, h['CodPartener'])
        if family == 'nir':
            lines = _all(cursor, 'SELECT i.ID, i.IdProdus, pp.ID AS Produs, pp.COD, pr.DenumireP, pr.UM, pr.IdMasina AS Category, i.Cant, i.Pret, i.IDTVA, i.PUTVA, i.Valoare, i.TVA, i.ValoareCUTVA, '
                                 'm.DataIntrare, m.DNF, m.DF, m.ValoareLuna FROM VC_Intrari i ' + _name_join('i') +
                                 ' LEFT JOIN VC_ProduseM m ON m.SubunitId=i.SubunitId AND m.GestiuneId=i.GestiuneId AND m.IdProdus=i.IdProdus '
                                 'WHERE i.SubunitId=%s AND i.GestiuneId=%s AND i.ExercitiuId=%s AND i.IdIntrare=%s ORDER BY i.ID', (s, g, e, doc_id))
            out_lines = [{'id': r['ID'], 'variant': r['IdProdus'], 'product': r['Produs'], 'name': r['DenumireP'], 'um': r['UM'], 'code': r['COD'], 'category': r['Category'],
                          'Cant': _money(r['Cant'], scale), 'Pret': _money(r['Pret'], scale), 'IDTVA': r['IDTVA'], 'PUTVA': _money(r['PUTVA'], scale),
                          'Valoare': _money(r['Valoare'], scale), 'TVA': _money(r['TVA'], scale), 'ValoareCUTVA': _money(r['ValoareCUTVA'], scale),
                          'data_intrarii': _iso(r['DataIntrare']), 'DNF': r['DNF'], 'DF': r['DF'], 'ValoareLuna': _money(r['ValoareLuna'], scale)} for r in lines]
            from .receipt_mf_edit import annotate
            annotate(cursor, ctx, out_lines)
            from .receipt_adjustment import annotate as annotate_adjustment
            stock_day = h['DataFc'] or h['Data']
            annotate_adjustment(cursor, ctx, out_lines, stock_day.date() if hasattr(stock_day, 'date') else stock_day)
        else:
            lines = _all(cursor, 'SELECT s.ID, s.IdProdus, pr.DenumireP, pr.UM, s.Cant, s.PUTVA FROM VC_IntrariStorno s ' + _name_join('s') +
                                 ' WHERE s.SubunitId=%s AND s.GestiuneId=%s AND s.ExercitiuId=%s AND s.IdIntrare=%s ORDER BY s.ID', (s, g, e, doc_id))
            out_lines = [{'id': r['ID'], 'variant': r['IdProdus'], 'name': r['DenumireP'], 'um': r['UM'], 'Cant': _money(r['Cant'], scale),
                          'PUTVA': _money(r['PUTVA'], scale), 'ValoareCUTVA': _money(
                              numeric.to_stored(numeric.calc_round(numeric.from_stored(r['Cant'], scale) * numeric.from_stored(r['PUTVA'] or 0, scale), ctx['zec']), scale), scale)}
                         for r in lines]
        source_label = None
        if family == 'storno':
            from .storno_stock import enrich_lines
            enrich_lines(cursor, ctx, h['IdStorno'], out_lines)
            source_rows = repo.select('VC_NIR', {'IdIntrare': h['IdStorno']})
            if source_rows:
                source = source_rows[0]
                source_label = 'NIR %s%s • %s • %s' % (
                    abs(source['Numar'] or 0), _sub_text(source.get('NumarSub') or 0), source['NrFact'] or '',
                    source['Data'].strftime('%d.%m.%Y') if source['Data'] else '')
        return {'family': family, 'id': doc_id, 'version': h['Version'], 'annulled': bool(h['Anulat']), 'reason': h['MotivAnulare'],
                'annulled_by': h['AnulatDe'], 'number': abs(h['Numar'] or 0), 'sub_number': h.get('NumarSub', 0), 'doc_type': h['TipDoc'],
                'header': {'CodFiscal': suppliers.code(h['CodPartener']), 'party': partner['DenumirePartener'] if partner else h['FurnizorV'], 'NrFact': h['NrFact'],
                           'DataFc': _iso(h['DataFc']), 'Data': _iso(h['Data']), 'Valoare': _money(h['Valoare'], scale), 'IdMasina': h['IdMasina'],
                           'IdComisie': h['IdComisie'], 'source': h['IdStorno'], 'source_label': source_label},
                'lines': out_lines}
    rows = repo.select('VC_Iesiri_H', {'IdBonConsum': doc_id})
    if not rows or bool(rows[0].get('Retur') or 0) != (family == 'br'):
        raise NomError('NOT_FOUND', 'Documentul nu mai există.', 404)
    h = rows[0]
    table = 'VC_IesiriRetur' if family == 'br' else 'VC_Iesiri'
    extra = '' if family == 'br' else ', x.ValoareCUTVA'
    lines = _all(cursor, 'SELECT x.ID, x.IdProdus, pr.DenumireP, pr.UM, x.Cant, x.PUTVA%s FROM %s x ' % (extra, table) + _name_join('x') +
                 ' WHERE x.SubunitId=%s AND x.GestiuneId=%s AND x.ExercitiuId=%s AND x.IdBonConsum=%s ORDER BY x.ID', (s, g, e, doc_id))
    out_lines = []
    for r in lines:
        if family == 'br':
            value = numeric.to_stored(numeric.calc_round(numeric.from_stored(r['Cant'], scale) * numeric.from_stored(r['PUTVA'] or 0, scale), ctx['zec']), scale)
        else:
            value = r['ValoareCUTVA']
        out_lines.append({'id': r['ID'], 'variant': r['IdProdus'], 'name': r['DenumireP'], 'um': r['UM'], 'Cant': _money(r['Cant'], scale),
                          'PUTVA': _money(r['PUTVA'], scale), 'ValoareCUTVA': _money(value, scale)})
    client = _one(cursor, 'SELECT Denumire FROM VC_Clienti WHERE SubunitId=%s AND GestiuneId=%s AND IDC=%s', (s, g, h['IDC'])) if h['IDC'] else None
    linked = _one(cursor, 'SELECT COUNT(*) AS n FROM VC_LegBCLA WHERE SubunitId=%s AND GestiuneId=%s AND ExercitiuId=%s AND IdBonConsum=%s', (s, g, e, doc_id))
    source = _one(cursor, 'SELECT NumarBC FROM VC_Iesiri_H WHERE SubunitId=%s AND GestiuneId=%s AND ExercitiuId=%s AND IdBonConsum=%s',
                  (s, g, e, h['IdRetur'])) if family == 'br' and h['IdRetur'] is not None else None
    source_missing = family == 'br' and h['IdRetur'] is None
    return {'family': family, 'id': doc_id, 'version': h['Version'], 'annulled': bool(h['Anulat']), 'reason': h['MotivAnulare'], 'annulled_by': h['AnulatDe'],
            'number': bon_number.display(family, h['NumarBC']), 'linked': bool(linked['n']), 'source_missing': source_missing,
            'header': {'IDC': h['IDC'], 'receiver': client['Denumire'] if client else None, 'IdMasina': h['IdMasina'], 'Data': _iso(h['DataBC']),
                       'Expl': h['Expl'], 'Valoare': _money(h['ValoareBC'], scale), 'source': h['IdRetur'],
                       'source_label': 'Fără bon sursă' if source_missing else bon_number.display('bc', source['NumarBC']) if source else None},
            'lines': out_lines}


def lookups(cursor, ctx, family):
    """What the editor needs to offer choices (ids + labels; nothing the server does not check again)."""
    s, g = ctx['subunit'], ctx['gestiune']
    scale = ctx['scale']
    out = {'categories': [{'id': r['IdMasina'], 'label': r['Masina'] or ''} for r in _all(
        cursor, 'SELECT IdMasina, Masina FROM VC_Masini WHERE SubunitId=%s AND GestiuneId=%s AND Activ=1 ORDER BY Masina', (s, g))]}
    if family == 'nir':
        out['products'] = receipt_choices.products(cursor, s, g)
    out['variants'] = [] if family == 'nir' else [{'id': r['IdProdus'], 'product': r['ID'], 'name': r['DenumireP'] or '', 'um': r['UM'] or '', 'code': r['COD'] or '', 'category': r['IdMasina'],
                        'putva': _money(r['PUTVA'], scale)} for r in _all(
        cursor, 'SELECT pp.IdProdus, pp.ID, pp.COD, pp.PUTVA, pr.DenumireP, pr.UM, pr.IdMasina FROM VC_ProduseP pp JOIN VC_Produse pr ON pr.SubunitId=pp.SubunitId '
                'AND pr.GestiuneId=pp.GestiuneId AND pr.ID=pp.ID WHERE pp.SubunitId=%s AND pp.GestiuneId=%s AND pp.Activ=1 AND pr.Activ=1 ORDER BY pr.DenumireP, pp.IdProdus', (s, g))]
    if family in ('nir', 'storno'):
        out['partners'] = receipt_choices.partners(cursor)
        out['commissions'] = [{'id': r['IdComisie'], 'label': ', '.join(x for x in (r['Receptie1'], r['Receptie2'], r['Receptie3'], r['Receptie4']) if x)} for r in _all(
            cursor, 'SELECT IdComisie, Receptie1, Receptie2, Receptie3, Receptie4 FROM VC_ComisieNIR WHERE SubunitId=%s AND GestiuneId=%s AND Activ=1', (s, g))]
        out['vat'] = [{'id': r['IdTVA'], 'label': r['Explicatie'] or '', 'percent': format(numeric.from_stored(r['TVA'] or 0, numeric.VAT_SCALE) * 100, 'f').rstrip('0').rstrip('.') or '0'} for r in _all(
            cursor, 'SELECT IdTVA, TVA, Explicatie FROM VC_TVA WHERE SubunitId=%s AND GestiuneId=%s AND Activ=1 ORDER BY TVA', (s, g))]
    else:
        out['receivers'] = [{'id': r['IDC'], 'label': r['Denumire'] or ''} for r in _all(
            cursor, 'SELECT IDC, Denumire FROM VC_Clienti WHERE SubunitId=%s AND GestiuneId=%s AND Activ=1 ORDER BY Denumire', (s, g))]
    out['mf'] = ctx['type'] == 'MF' and family == 'nir'
    return out


# ---- numbering -------------------------------------------------------------------------------------------------------
def _numarsub_column(cursor, ctx):
    """True when VC_NIR has the NumarSub column. Looked up once per request (kept in ctx), not once per number tried."""
    if '_numarsub' not in ctx:
        ctx['_numarsub'] = _has_numarsub(cursor)
    return ctx['_numarsub']


def _sub_number(raw, errors):
    """The sub-number of a NIR number, in hundredths (707,1 = 10; 707,01 = 1). 0 = none; at most 2 digits."""
    if raw in (None, '', 0, '0'):
        return 0
    value = _int(raw)
    if value is None or not 0 <= value <= 99:
        errors['h.Numar'] = 'Sub-numărul poate avea cel mult 2 cifre (1-99 sutimi).'
        return 0
    return value


def _sub_text(sub):
    return (',%d' % (sub // 10) if sub % 10 == 0 else ',%02d' % sub) if sub else ''


def _nir_number_taken(cursor, ctx, number, storno, exclude=None, sub=0):
    sql = 'SELECT 1 FROM VC_NIR WHERE SubunitId=%s AND GestiuneId=%s AND ExercitiuId=%s AND Numar=%s AND COALESCE(Storno,0)=%s AND Anulat=0'
    params = list(_scope(ctx)) + [-number if storno else number, 1 if storno else 0]
    if _numarsub_column(cursor, ctx):
        sql += ' AND COALESCE(NumarSub,0)=%s'   # 707 and 707,1 are two different numbers
        params.append(sub)
    if exclude:
        sql += ' AND IdIntrare<>%s'
        params.append(exclude)
    return bool(_one(cursor, sql + ' LIMIT 1', params))


def _bon_number_taken(cursor, ctx, family, number, exclude=None, account=None):
    """Is this number of a BC / BR in use? Every spelling of it counts (BC 13, BC13, 13). `account` narrows the search to one account
    (the gestiune numbers its BC per account)."""
    if number is None:
        return False
    spellings = bon_number.variants(family, number)
    sql = ('SELECT 1 FROM VC_Iesiri_H WHERE SubunitId=%s AND GestiuneId=%s AND ExercitiuId=%s AND NumarBC IN (' + ','.join(['%s'] * len(spellings))
           + ') AND COALESCE(Retur,0)=%s AND Anulat=0')
    params = list(_scope(ctx)) + spellings + [1 if family == 'br' else 0]
    if account is not None:
        sql += ' AND IdMasina=%s'
        params.append(account)
    if exclude:
        sql += ' AND IdBonConsum<>%s'
        params.append(exclude)
    return bool(_one(cursor, sql + ' LIMIT 1', params))


def _kind(family):
    return {'nir': 'NIR', 'storno': 'STORNO', 'bc': 'BC', 'br': 'BR'}[family]


def _assign_number(cursor, ctx, family, manual, errors, exclude=None, sub=0, account=None):
    """(number, text). Automatic when `manual` is empty; a manual number only when free. For NIR/storno the number is the
    integer stored in NIR.Numar (a storno stores it negative); for the bons it is the text stored in NumarBC (bon_number: BC 13, BX 13).
    `account` = the account of a BC: with per-account numbering (consumption_numbering) it picks the counter and the scope."""
    bon = family in ('bc', 'br')
    scoped = account if family == 'bc' and account is not None and consumption_numbering.per_account(cursor, ctx) else None
    kind = consumption_numbering.kind(scoped) if family == 'bc' else _kind(family)
    if manual in (None, ''):
        if sub:
            errors['h.Numar'] = 'Sub-numărul se poate folosi numai împreună cu un număr.'
            return None, None
        if family in ('nir', 'storno'):
            return protocol.next_number(cursor, ctx, _kind(family), lambda n: _nir_number_taken(cursor, ctx, n, family == 'storno', exclude)), None
        number = protocol.next_number(cursor, ctx, kind, lambda n: _bon_number_taken(cursor, ctx, family, n, exclude, scoped))
        return number, bon_number.text(family, number)
    number = _int(manual)
    if number is None or number < 1:
        errors['h.Numar'] = 'Numărul trebuie să fie un întreg pozitiv.'
        return None, None
    taken = _nir_number_taken(cursor, ctx, number, family == 'storno', exclude, sub) if family in ('nir', 'storno') \
        else _bon_number_taken(cursor, ctx, family, number, exclude, scoped)
    if taken:
        errors['h.Numar'] = 'Numărul %s este deja folosit.' % ((bon_number.text(family, number) if bon else str(number)) + _sub_text(sub))
        return None, None
    if not sub:
        protocol.raise_counter(cursor, ctx, kind, number)   # a sub-number sits between two numbers: it moves nothing
    return number, (bon_number.text(family, number) if bon else None)


# ---- guards shared by edit and cancel --------------------------------------------------------------------------------
def _load_for_write(repo, ctx, family, doc_id, base_version):
    table, pk = ('VC_NIR', 'IdIntrare') if family in ('nir', 'storno') else ('VC_Iesiri_H', 'IdBonConsum')
    rows = repo.select(table, {pk: doc_id}, for_update=True)
    if not rows:
        raise NomError('NOT_FOUND', 'Documentul nu mai există.', 404)
    row = rows[0]
    is_variant = bool(row.get('Storno') or 0) if family in ('nir', 'storno') else bool(row.get('Retur') or 0)
    if is_variant != (family in ('storno', 'br')):
        raise NomError('NOT_FOUND', 'Documentul nu mai există.', 404)
    if row['Anulat']:
        raise NomError('CANCELLED', 'Documentul este anulat și nu mai poate fi modificat.', 409)
    version = _int(base_version)
    if version is None or version < 1:
        raise NomError('INVALID', 'Lipsește revizia documentului. Reîncărcați documentul.', 400)
    if row['Version'] != version:
        raise NomError('CONFLICT', 'Documentul a fost modificat de altcineva. Reîncărcați documentul.', 409, {'version': row['Version']})
    return table, pk, row


def _guard_dependents(cursor, ctx, family, doc_id, action):
    if family == 'nir' and action == 'edit':
        from .efactura_receipts import require_editable
        require_editable(cursor, ctx, doc_id)
    s, g, e = _scope(ctx)
    what = 'modificat' if action == 'edit' else 'anulat'
    if family == 'nir':
        if _one(cursor, 'SELECT 1 FROM VC_NIR WHERE SubunitId=%s AND GestiuneId=%s AND ExercitiuId=%s AND IdStorno=%s AND Anulat=0 LIMIT 1', (s, g, e, doc_id)):
            raise NomError('HAS_STORNO', 'Recepția are storno: anulați mai întâi storno-ul, apoi puteți modifica sau anula recepția.', 409)
        # Edits protect issued MF rows in _clean_nir and retain stock for all exits
        # through simulate/check_timeline. Registry use alone cannot block a free unit.
        if action != 'edit' and _one(cursor, 'SELECT 1 FROM VC_Intrari i JOIN VC_ProduseP p ON p.SubunitId=i.SubunitId AND p.GestiuneId=i.GestiuneId AND p.IdProdus=i.IdProdus '
                        'WHERE i.SubunitId=%s AND i.GestiuneId=%s AND i.ExercitiuId=%s AND i.IdIntrare=%s AND (EXISTS (SELECT 1 FROM VC_OI_Predare x WHERE x.SubunitId=p.SubunitId AND x.GestiuneId=p.GestiuneId AND x.IdProdus=p.IdProdus AND x.Anulat=0) '
                        'OR EXISTS (SELECT 1 FROM VC_OI_Transfer x WHERE x.SubunitId=p.SubunitId AND x.GestiuneId=p.GestiuneId AND x.IdProdus=p.IdProdus AND x.Anulat=0) '
                        'OR EXISTS (SELECT 1 FROM VC_OI_Casare x WHERE x.SubunitId=p.SubunitId AND x.GestiuneId=p.GestiuneId AND x.IdProdus=p.IdProdus AND x.Anulat=0)) LIMIT 1', (s, g, e, doc_id)):
            raise NomError('DOC_LINKED', 'Un produs al recepției apare deja în registrul OI/MF: documentul nu mai poate fi %s.' % what, 409)
    if family == 'bc':
        if action == 'edit':
            consumption_period.require_edit(cursor, ctx, doc_id)
        else:
            document_order.require_latest(cursor, ctx, 'bc', doc_id)
        if _one(cursor, 'SELECT 1 FROM VC_Iesiri_H WHERE SubunitId=%s AND GestiuneId=%s AND ExercitiuId=%s AND IdRetur=%s AND COALESCE(Retur,0)=1 AND Anulat=0 LIMIT 1', (s, g, e, doc_id)):
            raise NomError('HAS_RETUR', 'Bonul are retur: anulați mai întâi returul.', 409)
        if action != 'edit' and _one(cursor, 'SELECT 1 FROM VC_LegBCLA WHERE SubunitId=%s AND GestiuneId=%s AND ExercitiuId=%s AND IdBonConsum=%s LIMIT 1', (s, g, e, doc_id)):
            raise NomError('LINKED_LIST', 'Bonul este asociat unei liste de alimente: scoateți-l din listă înainte să fie %s.' % what, 409)


def _check_cancel_input(raw):
    errors = {}
    reason = _text((raw or {}).get('reason') if isinstance(raw, dict) else None, 'reason', errors, True, 255)
    _fail(errors, 'Motivul anulării este obligatoriu.')
    return reason


def _mark_cancelled(cursor, ctx, table, pk, doc_id, reason, base_version):
    cursor.execute('UPDATE `%s` SET Anulat=1, MotivAnulare=%%s, AnulatDe=%%s, AnulatLa=NOW(), Version=Version+1 '
                   'WHERE SubunitId=%%s AND GestiuneId=%%s AND ExercitiuId=%%s AND `%s`=%%s AND Version=%%s' % (table, pk),
                   (reason, ctx['email'], ctx['subunit'], ctx['gestiune'], ctx['exercitiu'], doc_id, base_version))
    if cursor.rowcount != 1:
        raise NomError('CONFLICT', 'Documentul a fost modificat de altcineva. Reîncărcați documentul.', 409)


# ---- NIR (receipt) ---------------------------------------------------------------------------------------------------
def cancel_document(cursor, repo, ctx, family, doc_id, body, request_id):
    from . import invoices
    reason = _check_cancel_input(body)
    link = invoices.link_of(cursor, ctx, doc_id) if family == 'nir' and invoices.ready(cursor) else None
    if link:
        invoices.load(cursor, link['FACTURA_ID'], lock=True)
    table, pk, before = _load_for_write(repo, ctx, family, doc_id, body.get('version'))
    _guard_dependents(cursor, ctx, family, doc_id, 'cancel')
    _mark_cancelled(cursor, ctx, table, pk, doc_id, reason, before['Version'])
    if family == 'storno':
        cursor.execute('UPDATE VC_IntrariStorno SET Anulat=1, MotivAnulare=%s, AnulatDe=%s, AnulatLa=NOW(), Version=Version+1 '
                       'WHERE SubunitId=%s AND GestiuneId=%s AND ExercitiuId=%s AND IdIntrare=%s', (reason, ctx['email'], *_scope(ctx), doc_id))
    products = _document_variants(cursor, ctx, family, doc_id)
    protocol.check_timeline(cursor, ctx, products)
    if link:
        invoices.refresh_state(cursor, link['FACTURA_ID'])
    protocol.audit(cursor, ctx, family + '.cancel', table, doc_id, {'Anulat': 0}, {'Anulat': 1, 'Motiv': reason}, None, request_id)
    return {'id': doc_id}


def _document_variants(cursor, ctx, family, doc_id):
    table, key = {'nir': ('VC_Intrari', 'IdIntrare'), 'storno': ('VC_IntrariStorno', 'IdIntrare'), 'bc': ('VC_Iesiri', 'IdBonConsum'), 'br': ('VC_IesiriRetur', 'IdBonConsum')}[family]
    return {r['IdProdus'] for r in _all(cursor, 'SELECT IdProdus FROM %s WHERE SubunitId=%%s AND GestiuneId=%%s AND ExercitiuId=%%s AND %s=%%s' % (table, key), (*_scope(ctx), doc_id))}


# ---- FCS (storno of a receipt) -----------------------------------------------------------------------------------------
def storno_eligible(cursor, ctx, nir_id, exclude=None, on=None):
    from .storno_stock import eligible
    return eligible(cursor, ctx, nir_id, exclude, on)


def _clean_stornolines(cursor, ctx, source_id, raw_lines, exclude=None, on=None):
    errors = {}
    eligible = {l['variant']: l for l in storno_eligible(cursor, ctx, source_id, exclude, on)}
    lines, used = [], set()
    raw_lines = raw_lines if isinstance(raw_lines, list) else []
    if len(raw_lines) > _MAX_LINES:
        errors['lines'] = 'Cel mult %d linii pe document.' % _MAX_LINES
    if not raw_lines:
        errors['lines'] = 'Alegeți cel puțin un produs de stornat.'
    for index, raw in enumerate(raw_lines[:_MAX_LINES]):
        raw = raw if isinstance(raw, dict) else {}
        key = _line_key(raw, index)
        variant, cant = _int(raw.get('variant')), _dec(raw.get('Cant'))
        entry = eligible.get(variant)
        identity = None
        if entry:
            product = entry['product'] if entry['product'] is not None else ('variant', variant)
            identity = (product, Decimal(entry['PUTVA'] or '0'))
        if entry is None or identity in used:
            errors['l|%s|variant' % key] = 'Produsul nu este disponibil din recepție sau combinația produs + preț a fost aleasă de două ori.'
            continue
        used.add(identity)
        if cant is None or cant <= 0:
            errors['l|%s|Cant' % key] = 'Cantitatea trebuie să fie pozitivă.'
            continue
        try:
            quantity = numeric.to_stored(cant, ctx['scale'])
        except numeric.NumericError as error:
            errors['l|%s|Cant' % key] = str(error)
            continue
        left = numeric.to_stored(Decimal(entry['left']), ctx['scale'])
        if quantity > left:
            errors['l|%s|Cant' % key] = 'Cantitate peste cea rămasă de stornat (%s).' % entry['left']
            continue
        price = Decimal(entry['PUTVA'] or '0')
        lines.append({'variant': variant, 'Cant': quantity, 'PUTVA': numeric.to_stored(price, ctx['scale']), 'ValoareCUTVA': numeric.to_stored(numeric.calc_round(cant * price, ctx['zec']), ctx['scale'])})
    _fail(errors)
    return lines


def _source_nir(repo, source_id):
    rows = repo.select('VC_NIR', {'IdIntrare': source_id})
    if not rows or rows[0]['Anulat'] or bool(rows[0].get('Storno') or 0):
        raise NomError('SOURCE_INVALID', 'Recepția aleasă nu există sau este anulată.', 409)
    return rows[0]


def create_storno(cursor, repo, ctx, exercise, body, request_key, request_id):
    source = _source_nir(repo, _int(body.get('source')))
    header = body.get('header') if isinstance(body.get('header'), dict) else {}
    errors = {}
    cols = {'TipDoc': 'NIR', 'Storno': 1, 'CodPartener': suppliers.code(source['CodPartener']) or None, 'FurnizorV': source['FurnizorV'], 'IdStorno': source['IdIntrare'],
            'IdMasina': source['IdMasina'], 'IdComisie': _int(header.get('IdComisie')) if header.get('IdComisie') not in (None, '') else source['IdComisie']}
    cols['NrFact'] = _text(header.get('NrFact'), 'h.NrFact', errors, True)
    cols['DataFc'] = protocol.to_date(header.get('DataFc'), 'Data facturii storno', None, errors, 'h.DataFc')
    cols['Data'] = protocol.to_date(header.get('Data'), 'Data NIR', exercise if ctx['type'] not in ('OI', 'MF') else None, errors, 'h.Data')
    if cols['DataFc'] and source['DataFc'] and cols['DataFc'] < source['DataFc'].date():
        errors['h.DataFc'] = 'Data storno nu poate fi înaintea facturii storno-ate.'
    if cols['IdComisie'] is not None and not _exists(repo, 'VC_ComisieNIR', 'IdComisie', cols['IdComisie']):
        errors['h.IdComisie'] = 'Valoarea aleasă nu există în această gestiune.'
    _fail(errors)
    lines = _clean_stornolines(cursor, ctx, source['IdIntrare'], body.get('lines'), on=cols['Data'])
    from . import receipt_number
    sub = _sub_number(header.get('NumarSub'), errors)
    _fail(errors)
    number, sub = receipt_number.assign_pair(cursor, ctx, header.get('Numar'), sub, cols['Data'])
    _fail(errors)
    total = numeric.to_stored(total_of(lines, ctx['scale']), ctx['scale'])
    values = {**cols, 'Valoare': total, 'Numar': -number}
    if _has_numarsub(cursor):
        values['NumarSub'] = sub
    doc_id = repo.insert('VC_NIR', values)
    now = utc_now()
    for line in lines:
        repo.insert('VC_IntrariStorno', {'IdIntrare': doc_id, 'IdProdus': line['variant'], 'Cant': line['Cant'], 'PUTVA': line['PUTVA'], 'DataSalvare': now})
    protocol.check_timeline(cursor, ctx, {l['variant'] for l in lines})
    protocol.freeze(cursor, ctx)
    protocol.audit(cursor, ctx, 'storno.create', 'VC_NIR', doc_id, None, {'header': cols, 'total': total, 'lines': len(lines)}, request_key, request_id)
    return {'id': doc_id, 'number': number}


def edit_storno(cursor, repo, ctx, exercise, doc_id, body, request_id):
    table, pk, before = _load_for_write(repo, ctx, 'storno', doc_id, body.get('version'))
    source = _source_nir(repo, _int(body.get('source')) if 'source' in body else before['IdStorno'])
    header = body.get('header') if isinstance(body.get('header'), dict) else {}
    errors = {}
    cols = {'IdStorno': source['IdIntrare'], 'CodPartener': suppliers.code(source['CodPartener']) or None, 'FurnizorV': source['FurnizorV'], 'IdMasina': source['IdMasina'],
            'IdComisie': _int(header.get('IdComisie')) if header.get('IdComisie') not in (None, '') else None,
            'NrFact': _text(header.get('NrFact'), 'h.NrFact', errors, True),
            'DataFc': protocol.to_date(header.get('DataFc'), 'Data facturii storno', None, errors, 'h.DataFc'),
            'Data': protocol.to_date(header.get('Data'), 'Data NIR', exercise if ctx['type'] not in ('OI', 'MF') else None, errors, 'h.Data')}
    if cols['DataFc'] and source['DataFc'] and cols['DataFc'] < source['DataFc'].date():
        errors['h.DataFc'] = 'Data storno nu poate fi înaintea facturii storno-ate.'
    if header.get('IdComisie') not in (None, ''):
        cols['IdComisie'] = _int(header.get('IdComisie'))
        if cols['IdComisie'] is None or not _exists(repo, 'VC_ComisieNIR', 'IdComisie', cols['IdComisie']):
            errors['h.IdComisie'] = 'Valoarea aleasă nu există în această gestiune.'
    _fail(errors)
    lines = _clean_stornolines(cursor, ctx, source['IdIntrare'], body.get('lines'), exclude=doc_id, on=cols['Data'])
    old_day = before['Data'].date() if hasattr(before['Data'], 'date') else before['Data']
    if old_day and cols['Data'].year != old_day.year:
        errors['h.Data'] = 'Data FCS nu poate muta documentul în altă serie anuală.'
    from . import receipt_number
    sub = _sub_number(header.get('NumarSub', before.get('NumarSub', 0)), errors)
    _fail(errors)
    number, sub = receipt_number.assign_pair(cursor, ctx, header.get('Numar') or abs(before['Numar'] or 0), sub, cols['Data'], doc_id)
    _fail(errors)
    cols['Numar'] = -number
    if _has_numarsub(cursor):
        cols['NumarSub'] = sub
    cols['Valoare'] = numeric.to_stored(total_of(lines, ctx['scale']), ctx['scale'])
    repo.delete('VC_IntrariStorno', {'IdIntrare': doc_id})
    now = utc_now()
    for line in lines:
        repo.insert('VC_IntrariStorno', {'IdIntrare': doc_id, 'IdProdus': line['variant'], 'Cant': line['Cant'], 'PUTVA': line['PUTVA'], 'DataSalvare': now})
    if repo.update('VC_NIR', cols, {'IdIntrare': doc_id, 'Version': before['Version']}, bump_version=True) != 1:
        raise NomError('CONFLICT', 'Documentul a fost modificat de altcineva. Reîncărcați documentul.', 409)
    protocol.check_timeline(cursor, ctx, {l['variant'] for l in lines})
    protocol.audit(cursor, ctx, 'storno.update', 'VC_NIR', doc_id, {'Numar': before['Numar'], 'Valoare': before['Valoare']}, {'header': cols, 'lines': len(lines)}, None, request_id)
    return {'id': doc_id, 'number': number}


# ---- BC (consumption) and BR (return) ----------------------------------------------------------------------------------
def _variant_prices(repo, variants):
    out = {}
    for variant in set(variants):
        rows = repo.select('VC_ProduseP', {'IdProdus': variant, 'Activ': 1})
        if rows:
            out[variant] = Decimal(rows[0]['PUTVA'] or 0)
    return out


def _bon_header(cursor, repo, ctx, exercise, header, errors, source=None, existing=None):
    cols = {}
    receiver = _int(header.get('IDC')) if source is None else source['IDC']
    if source is None and (receiver is None or not _exists(repo, 'VC_Clienti', 'IDC', receiver, True)):
        errors['h.IDC'] = 'Alegeți primitorul.'
    cols['IDC'] = receiver
    if source is None:
        category = _int(header.get('IdMasina'))
        if category is None or not _exists(repo, 'VC_Masini', 'IdMasina', category, True):
            errors['h.IdMasina'] = 'Alegeți categoria / contul.'
        cols['IdMasina'] = category
    else:
        cols['IdMasina'] = source['IdMasina']
    cols['DataBC'] = protocol.to_date(header.get('Data'), 'Data bonului', exercise if ctx['type'] not in ('OI', 'MF') else None, errors, 'h.Data')
    cols['Expl'] = _text(header.get('Expl'), 'h.Expl', errors, False, 255)
    if source is not None and cols['DataBC'] and source['DataBC'] and cols['DataBC'] < source['DataBC'].date():
        errors['h.Data'] = 'Returul nu poate fi înaintea bonului de consum.'
    return cols


def create_bc(cursor, repo, ctx, exercise, body, request_key, request_id):
    header = body.get('header') if isinstance(body.get('header'), dict) else {}
    errors = {}
    cols = _bon_header(cursor, repo, ctx, exercise, header, errors)
    lines = _clean_bon_lines(repo, ctx, body.get('lines'), errors, cols.get('IdMasina'))
    _fail(errors)
    number, text = _assign_number(cursor, ctx, 'bc', header.get('Numar'), errors, account=cols.get('IdMasina'))
    _fail(errors)
    total = numeric.to_stored(total_of(lines, ctx['scale']), ctx['scale'])
    doc_id = repo.insert('VC_Iesiri_H', {**cols, 'NumarBC': text, 'ValoareBC': total, 'Retur': 0, 'DataSalvare': utc_now()})
    now = utc_now()
    for line in lines:
        repo.insert('VC_Iesiri', {'IdBonConsum': doc_id, 'IdProdus': line['variant'], 'Cant': line['Cant'], 'PUTVA': line['PUTVA'], 'ValoareCUTVA': line['ValoareCUTVA'], 'DataSalvare': now})
    protocol.check_timeline(cursor, ctx, {l['variant'] for l in lines})
    protocol.freeze(cursor, ctx)
    protocol.audit(cursor, ctx, 'bc.create', 'VC_Iesiri_H', doc_id, None, {'header': cols, 'number': text, 'total': total, 'lines': len(lines)}, request_key, request_id)
    return {'id': doc_id, 'number': text}


def _clean_bon_lines(repo, ctx, raw_lines, errors, account=None):
    """Lines of a consumption bon: a variant and a quantity; the price is the variant's price with VAT AT SAVE TIME (the
    line keeps it afterwards, whatever the nomenclature does)."""
    raw_lines = raw_lines if isinstance(raw_lines, list) else []
    if not raw_lines:
        errors['lines'] = 'Bonul nu are nicio linie.'
    if len(raw_lines) > _MAX_LINES:
        errors['lines'] = 'Cel mult %d linii pe document.' % _MAX_LINES
    prices = _variant_prices(repo, [_int(r.get('variant')) for r in raw_lines if isinstance(r, dict) and _int(r.get('variant')) is not None])
    out = []
    for index, raw in enumerate(raw_lines[:_MAX_LINES]):
        raw = raw if isinstance(raw, dict) else {}
        key = _line_key(raw, index)
        variant, cant = _int(raw.get('variant')), _dec(raw.get('Cant'))
        if variant is None or variant not in prices:
            errors['l|%s|variant' % key] = 'Alegeți produsul.'
            continue
        if account is not None and _account_of_variant(repo, variant) != account:
            errors['l|%s|variant' % key] = _NOT_IN_ACCOUNT
            continue
        if cant is None or cant <= 0:
            errors['l|%s|Cant' % key] = 'Cantitatea trebuie să fie pozitivă.'
            continue
        try:
            out.append({'variant': variant, **priced_line(cant, prices[variant], ctx['zec'], ctx['scale'])})
        except numeric.NumericError as error:
            errors['l|%s|Cant' % key] = str(error)
    return out


def edit_bc(cursor, repo, ctx, exercise, doc_id, body, request_id):
    from . import recalculation_audit

    table, pk, before = _load_for_write(repo, ctx, 'bc', doc_id, body.get('version'))
    _guard_dependents(cursor, ctx, 'bc', doc_id, 'edit')
    header = body.get('header') if isinstance(body.get('header'), dict) else {}
    errors = {}
    cols = _bon_header(cursor, repo, ctx, exercise, header, errors)
    old_lines = _all(cursor, 'SELECT ID, IdProdus, Cant, PUTVA FROM VC_Iesiri WHERE SubunitId=%s AND GestiuneId=%s AND ExercitiuId=%s AND IdBonConsum=%s', (*_scope(ctx), doc_id))
    lines = _clean_bon_lines(repo, ctx, body.get('lines'), errors, cols.get('IdMasina'))
    _fail(errors)
    consumption_period.require_date(cursor, ctx, cols['DataBC'])
    text = before['NumarBC']
    manual = header.get('Numar')
    account = cols.get('IdMasina')
    # With per-account numbering a bon moved to another account is checked (and its number reserved) in that account.
    moved = account != before['IdMasina'] and consumption_numbering.per_account(cursor, ctx)
    if manual not in (None, '') and (_int(manual) != bon_number.parse('bc', text) or moved):
        _, text = _assign_number(cursor, ctx, 'bc', manual, errors, exclude=doc_id, account=account)
    elif moved and _bon_number_taken(cursor, ctx, 'bc', bon_number.parse('bc', text), doc_id, account):
        _, text = _assign_number(cursor, ctx, 'bc', None, errors, exclude=doc_id, account=account)
    _fail(errors)
    # a saved line keeps its price: a line of the same variant is re-priced only when the page changed its quantity
    variants = {line['variant'] for line in lines} | {row['IdProdus'] for row in old_lines}
    prior_stock = recalculation_audit.stock_snapshot(cursor, ctx, variants)
    has_food_list = bool(repo.select('VC_LegBCLA', {'IdBonConsum': doc_id}, ('IdLista',)))
    prior_food = recalculation_audit.food_snapshot(cursor, ctx) if has_food_list else {}
    old_price = {r['IdProdus']: r['PUTVA'] for r in old_lines}
    now = utc_now()
    repo.delete('VC_Iesiri', {'IdBonConsum': doc_id})
    total_lines = []
    for line in lines:
        if line['variant'] in old_price and old_price[line['variant']] is not None:
            line = {'variant': line['variant'], **priced_line(numeric.from_stored(line['Cant'], ctx['scale']), old_price[line['variant']], ctx['zec'], ctx['scale'])}
        total_lines.append(line)
        repo.insert('VC_Iesiri', {'IdBonConsum': doc_id, 'IdProdus': line['variant'], 'Cant': line['Cant'], 'PUTVA': line['PUTVA'], 'ValoareCUTVA': line['ValoareCUTVA'], 'DataSalvare': now})
    total = numeric.to_stored(total_of(total_lines, ctx['scale']), ctx['scale'])
    if repo.update('VC_Iesiri_H', {**cols, 'NumarBC': text, 'ValoareBC': total, 'DataSalvare': now}, {'IdBonConsum': doc_id, 'Version': before['Version']}, bump_version=True) != 1:
        raise NomError('CONFLICT', 'Documentul a fost modificat de altcineva. Reîncărcați documentul.', 409)
    protocol.check_timeline(cursor, ctx, variants)
    from .food_list_recalculation import refresh_for_bon
    recalculated = refresh_for_bon(cursor, repo, ctx, doc_id, request_id)
    origin = recalculation_audit.cause('bc', doc_id, text, cols['DataBC'], before['Version'] + 1)
    origin['previous_number'], origin['previous_date'] = before['NumarBC'], str(before['DataBC'])[:10]
    recalculation_audit.record(cursor, ctx, prior_stock, recalculation_audit.stock_snapshot(cursor, ctx, variants),
                               origin, 'bc.stock_recalculated', 'VC_Iesiri_H', request_id)
    if has_food_list:
        recalculation_audit.record(cursor, ctx, prior_food, recalculation_audit.food_snapshot(cursor, ctx),
                                   origin, 'lista.balance_recalculated', 'VC_LABC', request_id)
    protocol.audit(cursor, ctx, 'bc.update', 'VC_Iesiri_H', doc_id,
                   {'NumarBC': before['NumarBC'], 'ValoareBC': before['ValoareBC'], 'line_details': old_lines},
                   {'header': cols, 'total': total, 'lines': len(total_lines), 'line_details': total_lines}, None, request_id)
    return {'id': doc_id, 'number': text, 'recalculated_lists': recalculated}


def return_eligible(cursor, ctx, bc_id, exclude=None):
    """The lines of a consumption bon with what is still left to return, at the price of the bon."""
    s, g, e = _scope(ctx)
    scale = ctx['scale']
    rows = _all(cursor, 'SELECT o.IdProdus, pr.DenumireP, pr.UM, SUM(o.Cant) AS q, MIN(o.PUTVA) AS putva FROM VC_Iesiri o ' + _name_join('o') +
                ' WHERE o.SubunitId=%s AND o.GestiuneId=%s AND o.ExercitiuId=%s AND o.IdBonConsum=%s GROUP BY o.IdProdus, pr.DenumireP, pr.UM ORDER BY pr.DenumireP', (s, g, e, bc_id))
    done = {r['IdProdus']: Decimal(r['q']) for r in _all(
        cursor, 'SELECT x.IdProdus, SUM(x.Cant) AS q FROM VC_IesiriRetur x JOIN VC_Iesiri_H h ON h.SubunitId=x.SubunitId AND h.GestiuneId=x.GestiuneId AND h.ExercitiuId=x.ExercitiuId AND h.IdBonConsum=x.IdBonConsum '
                'WHERE x.SubunitId=%s AND x.GestiuneId=%s AND x.ExercitiuId=%s AND h.IdRetur=%s AND COALESCE(h.Retur,0)=1 AND h.Anulat=0 AND h.IdBonConsum<>%s GROUP BY x.IdProdus',
        (s, g, e, bc_id, exclude or 0))}
    return [{'variant': r['IdProdus'], 'name': r['DenumireP'], 'um': r['UM'], 'qty': _money(Decimal(r['q']), scale), 'done': _money(done.get(r['IdProdus'], 0), scale),
             'left': _money(Decimal(r['q']) - done.get(r['IdProdus'], 0), scale), 'PUTVA': _money(r['putva'], scale)} for r in rows]


def _source_bc(repo, source_id):
    rows = repo.select('VC_Iesiri_H', {'IdBonConsum': source_id}) if source_id is not None else []
    if not rows or rows[0]['Anulat'] or bool(rows[0].get('Retur') or 0):
        raise NomError('SOURCE_INVALID', 'Bonul de consum ales nu există sau este anulat.', 409)
    return rows[0]


def _clean_return_lines(cursor, ctx, source_id, raw_lines, exclude=None):
    errors = {}
    eligible = {l['variant']: l for l in return_eligible(cursor, ctx, source_id, exclude)}
    lines, used = [], set()
    if not isinstance(raw_lines, list) or not raw_lines:
        errors['lines'] = 'Alegeți cel puțin un produs de returnat.'
        raw_lines = []
    for index, raw in enumerate(raw_lines[:_MAX_LINES]):
        raw = raw if isinstance(raw, dict) else {}
        key = _line_key(raw, index)
        variant, cant = _int(raw.get('variant')), _dec(raw.get('Cant'))
        entry = eligible.get(variant)
        if entry is None or variant in used:
            errors['l|%s|variant' % key] = 'Produsul nu face parte din bon sau a fost ales de două ori.'
            continue
        used.add(variant)
        if cant is None or cant <= 0:
            errors['l|%s|Cant' % key] = 'Cantitatea trebuie să fie pozitivă.'
            continue
        try:
            quantity = numeric.to_stored(cant, ctx['scale'])
        except numeric.NumericError as error:
            errors['l|%s|Cant' % key] = str(error)
            continue
        if quantity > numeric.to_stored(Decimal(entry['left']), ctx['scale']):
            errors['l|%s|Cant' % key] = 'Cantitate peste cea rămasă de returnat (%s).' % entry['left']
            continue
        lines.append({'variant': variant, **priced_line(cant, numeric.to_stored(Decimal(entry['PUTVA'] or '0'), ctx['scale']), ctx['zec'], ctx['scale'])})
    _fail(errors)
    return lines


def create_br(cursor, repo, ctx, exercise, body, request_key, request_id):
    source = _source_bc(repo, _int(body.get('source')))
    header = body.get('header') if isinstance(body.get('header'), dict) else {}
    errors = {}
    cols = _bon_header(cursor, repo, ctx, exercise, header, errors, source=source)
    _fail(errors)
    lines = _clean_return_lines(cursor, ctx, source['IdBonConsum'], body.get('lines'))
    number, text = _assign_number(cursor, ctx, 'br', header.get('Numar'), errors)
    _fail(errors)
    total = numeric.to_stored(total_of(lines, ctx['scale']), ctx['scale'])
    doc_id = repo.insert('VC_Iesiri_H', {**cols, 'IdRetur': source['IdBonConsum'], 'NumarBC': text, 'ValoareBC': total, 'Retur': 1, 'DataSalvare': utc_now()})
    now = utc_now()
    for line in lines:
        repo.insert('VC_IesiriRetur', {'IdBonConsum': doc_id, 'IdProdus': line['variant'], 'Cant': line['Cant'], 'PUTVA': line['PUTVA'], 'DataSalvare': now})
    protocol.freeze(cursor, ctx)
    protocol.audit(cursor, ctx, 'br.create', 'VC_Iesiri_H', doc_id, None, {'header': cols, 'number': text, 'total': total, 'lines': len(lines)}, request_key, request_id)
    return {'id': doc_id, 'number': text}


def edit_br(cursor, repo, ctx, exercise, doc_id, body, request_id):
    table, pk, before = _load_for_write(repo, ctx, 'br', doc_id, body.get('version'))
    if before['IdRetur'] is None:
        message = 'Returul nu are bon sursă. Asociați-l mai întâi cu un bon de consum, apoi îl puteți modifica.'
        raise NomError('SOURCE_REQUIRED', message, 422, {'source': message})
    source = _source_bc(repo, before['IdRetur'])
    header = body.get('header') if isinstance(body.get('header'), dict) else {}
    errors = {}
    cols = _bon_header(cursor, repo, ctx, exercise, header, errors, source=source)
    _fail(errors)
    lines = _clean_return_lines(cursor, ctx, source['IdBonConsum'], body.get('lines'), exclude=doc_id)
    text = before['NumarBC']
    manual = header.get('Numar')
    if manual not in (None, '') and _int(manual) != bon_number.parse('br', text):
        _, text = _assign_number(cursor, ctx, 'br', manual, errors, exclude=doc_id)
    _fail(errors)
    repo.delete('VC_IesiriRetur', {'IdBonConsum': doc_id})
    now = utc_now()
    for line in lines:
        repo.insert('VC_IesiriRetur', {'IdBonConsum': doc_id, 'IdProdus': line['variant'], 'Cant': line['Cant'], 'PUTVA': line['PUTVA'], 'DataSalvare': now})
    total = numeric.to_stored(total_of(lines, ctx['scale']), ctx['scale'])
    if repo.update('VC_Iesiri_H', {**cols, 'NumarBC': text, 'ValoareBC': total, 'DataSalvare': now}, {'IdBonConsum': doc_id, 'Version': before['Version']}, bump_version=True) != 1:
        raise NomError('CONFLICT', 'Documentul a fost modificat de altcineva. Reîncărcați documentul.', 409)
    protocol.check_timeline(cursor, ctx, {l['variant'] for l in lines})
    protocol.audit(cursor, ctx, 'br.update', 'VC_Iesiri_H', doc_id, {'NumarBC': before['NumarBC'], 'ValoareBC': before['ValoareBC']}, {'header': cols, 'total': total, 'lines': len(lines)}, None, request_id)
    return {'id': doc_id, 'number': text}


from .receipts import create_nir, edit_nir, _has_numarsub

CREATE = {'nir': create_nir, 'storno': create_storno, 'bc': create_bc, 'br': create_br}
EDIT = {'nir': edit_nir, 'storno': edit_storno, 'bc': edit_bc, 'br': edit_br}


# ---- the trail of a document (VC06-05) --------------------------------------------------------------------------------
_TABLE = {'nir': 'VC_NIR', 'storno': 'VC_NIR', 'bc': 'VC_Iesiri_H', 'br': 'VC_Iesiri_H'}


def history(cursor, ctx, family, doc_id):
    """Who did what to the document and when, with the values before and after (the e-mail is masked: enough to recognise the
    account). Read from VC_Audit, which every write fills in the same transaction as the document."""
    from .diagnostics import mask_email
    rows = _all(cursor, 'SELECT At, UserEmail, Operation, BeforeValue, AfterValue FROM VC_Audit WHERE SubunitId=%s AND GestiuneId=%s AND TableName=%s AND RowKey=%s ORDER BY AuditId',
                (ctx['subunit'], ctx['gestiune'], _TABLE[family], str(doc_id)))
    return [{'at': r['At'].strftime('%Y-%m-%d %H:%M:%S') if r['At'] else None, 'user': mask_email(r['UserEmail']), 'operation': r['Operation'],
             'before': r['BeforeValue'], 'after': r['AfterValue']} for r in rows]
