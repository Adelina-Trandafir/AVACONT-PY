"""SLICE-VC07-01: the food list (ListaAlimente) of an Alimente gestiune.

A list = a header (LABC: number, date, value of the bons, value of the list, the difference) + one row of meal summaries
(VC_ListaAlimente: for each of the three meals the portions, the value of a portion, the value of the meal, the portions
distributed) + the food items of each meal (VC_MicDejun / VC_Dejun / VC_Cina) + the SET of consumption bons it covers
(VC_LegBCLA, one list -> many bons, a bon in only one list).

Rules: the bons are created separately and only CHOSEN here (a bon of this gestiune and exercise, not cancelled, not in
another list); saving writes the header, the meals and the whole set of links in ONE transaction and never creates or
re-saves a bon and never repeats a consumption; the value of a meal is portions x value of a portion (rounded once at the
gestiune's ZEC, stored directly as DECIMAL(20,4)); the value of the list is the sum of the meals; the difference is the value of the
list minus the sum of the bons; a recipe changed later does not rewrite a saved list (the list keeps its own text and
values). Cancelling a list frees its bons.
"""
from utils.clock import bucharest_today, utc_now
from decimal import Decimal

from . import bon_number, document_order, food_list_portions, lista_bc, numeric, protocol
from .documents import _all, _dec, _exists, _fail, _int, _money, _one, _scope, _text, _iso
from .nomenclatoare import NomError

MEALS = {   # key -> (table, portions, value of a portion, value of the meal, distributed)
    'md': ('VC_MicDejun', 'MicDejun_P', 'VMD', 'MicDejun_V', 'MicDejun_D'),
    'd': ('VC_Dejun', 'Dejun_P', 'VD', 'Dejun_V', 'Dejun_D'),
    'c': ('VC_Cina', 'Cina_P', 'VC', 'Cina_V', 'Cina_D'),
}
_MAX_ITEMS = 60


# ---- reading ---------------------------------------------------------------------------------------------------------
def list_documents(cursor, ctx, family):
    s, g, e = _scope(ctx)
    scale = ctx['scale']
    rows = _all(cursor, 'SELECT l.IdLista, l.NumarLA, l.DATALA, l.ValoareBon, l.ValoareLista, l.DEP, l.Anulat, l.Version, '
                        '(SELECT COUNT(*) FROM VC_LegBCLA x WHERE x.SubunitId=l.SubunitId AND x.GestiuneId=l.GestiuneId AND x.ExercitiuId=l.ExercitiuId AND x.IdLista=l.IdLista) AS Bonuri '
                        'FROM VC_LABC l WHERE l.SubunitId=%s AND l.GestiuneId=%s AND l.ExercitiuId=%s ORDER BY l.DATALA DESC, l.IdLista DESC', (s, g, e))
    return food_list_portions.annotate(cursor, ctx, [
        {'id': r['IdLista'], 'number': r['NumarLA'], 'date': _iso(r['DATALA']), 'total': _money(r['ValoareLista'], scale), 'bons_total': _money(r['ValoareBon'], scale),
         'diff': _money(r['DEP'], scale), 'bons': r['Bonuri'], 'annulled': bool(r['Anulat']), 'version': r['Version']} for r in rows])


def get_document(cursor, repo, ctx, family, doc_id):
    s, g, e = _scope(ctx)
    scale = ctx['scale']
    rows = repo.select('VC_LABC', {'IdLista': doc_id})
    if not rows:
        raise NomError('NOT_FOUND', 'Lista nu mai există.', 404)
    head = rows[0]
    summary = (repo.select('VC_ListaAlimente', {'IDLISTA': doc_id}) or [{}])[0]
    meals = {}
    for key, (table, p, v, total, d) in MEALS.items():
        items = _all(cursor, 'SELECT x.Id, x.IdFel, x.Tip, x.Explicatie, f.DenFel FROM {t} x LEFT JOIN VC_FeluriMancare f ON f.SubunitId=x.SubunitId AND f.GestiuneId=x.GestiuneId AND f.IDFel=x.IdFel '
                              'WHERE x.SubunitId=%s AND x.GestiuneId=%s AND x.ExercitiuId=%s AND x.IdLista=%s ORDER BY x.Id'.format(t=table), (s, g, e, doc_id))
        meals[key] = {'P': summary.get(p), 'VMD': _money(summary.get(v), scale), 'V': _money(summary.get(total), scale), 'D': summary.get(d),
                      'items': [{'id': i['Id'], 'IdFel': i['IdFel'], 'fel': i['DenFel'], 'Tip': i['Tip'], 'Explicatie': i['Explicatie']} for i in items]}
    bons = _all(cursor, 'SELECT h.IdBonConsum, h.NumarBC, h.DataBC, h.ValoareBC, h.Anulat, c.Denumire FROM VC_LegBCLA l JOIN VC_Iesiri_H h ON h.SubunitId=l.SubunitId AND h.GestiuneId=l.GestiuneId '
                        'AND h.ExercitiuId=l.ExercitiuId AND h.IdBonConsum=l.IdBonConsum LEFT JOIN VC_Clienti c ON c.SubunitId=h.SubunitId AND c.GestiuneId=h.GestiuneId AND c.IDC=h.IDC '
                        'WHERE l.SubunitId=%s AND l.GestiuneId=%s AND l.ExercitiuId=%s AND l.IdLista=%s ORDER BY h.DataBC, h.IdBonConsum', (s, g, e, doc_id))
    doc = {'family': 'lista', 'id': doc_id, 'version': head['Version'], 'annulled': bool(head['Anulat']), 'reason': head['MotivAnulare'], 'annulled_by': head['AnulatDe'],
            'number': head['NumarLA'], 'header': {'Data': _iso(head['DATALA']), 'Comentarii': summary.get('Comentarii'), 'Valoare': _money(head['ValoareLista'], scale),
                                                    'bons_total': _money(head['ValoareBon'], scale), 'diff': _money(head['DEP'], scale)},
            'meals': meals, 'bons': [{'id': b['IdBonConsum'], 'number': bon_number.display('bc', b['NumarBC']), 'date': _iso(b['DataBC']), 'receiver': b['Denumire'] or '', 'total': _money(b['ValoareBC'], scale),
                                      'annulled': bool(b['Anulat'])} for b in bons]}
    return food_list_portions.annotate(cursor, ctx, [doc])[0]


def lookups(cursor, ctx, family):
    s, g = ctx['subunit'], ctx['gestiune']
    # Suggestions are saved list texts, scoped to the current context. New text is
    # stored with the list through the existing write path, without a new table.
    explanation_queries = [
        'SELECT DISTINCT x.IdFel, x.Tip, x.Explicatie FROM {table} x '
        'JOIN VC_LABC h ON h.SubunitId=x.SubunitId AND h.GestiuneId=x.GestiuneId '
        'AND h.ExercitiuId=x.ExercitiuId AND h.IdLista=x.IdLista '
        'WHERE x.SubunitId=%s AND x.GestiuneId=%s AND x.ExercitiuId=%s '
        "AND h.Anulat=0 AND x.Explicatie IS NOT NULL AND TRIM(x.Explicatie)<>''".format(table=meal[0])
        for meal in MEALS.values()
    ]
    explanations = _all(cursor, ' UNION '.join(explanation_queries) + ' ORDER BY Tip, Explicatie', _scope(ctx) * len(MEALS))
    return {'dishes': [{'id': r['IDFel'], 'label': r['DenFel'] or ''} for r in _all(
        cursor, 'SELECT IDFel, DenFel FROM VC_FeluriMancare WHERE SubunitId=%s AND GestiuneId=%s AND Activ=1 ORDER BY DenFel', (s, g))],
        'explanations': explanations,
        'today': bucharest_today().isoformat()}


def eligible_bons(cursor, ctx, exclude_list=None):
    """The bons a list can still take: this gestiune and exercise, not cancelled, not a return, not in ANOTHER list."""
    s, g, e = _scope(ctx)
    scale = ctx['scale']
    rows = _all(cursor, 'SELECT h.IdBonConsum, h.NumarBC, h.DataBC, h.ValoareBC, c.Denumire FROM VC_Iesiri_H h LEFT JOIN VC_Clienti c ON c.SubunitId=h.SubunitId AND c.GestiuneId=h.GestiuneId AND c.IDC=h.IDC '
                        'WHERE h.SubunitId=%s AND h.GestiuneId=%s AND h.ExercitiuId=%s AND COALESCE(h.Retur,0)=0 AND h.Anulat=0 AND NOT EXISTS (SELECT 1 FROM VC_LegBCLA l WHERE l.SubunitId=h.SubunitId '
                        'AND l.GestiuneId=h.GestiuneId AND l.ExercitiuId=h.ExercitiuId AND l.IdBonConsum=h.IdBonConsum AND l.IdLista<>%s) ORDER BY h.DataBC DESC, h.IdBonConsum DESC', (s, g, e, exclude_list or 0))
    return [{'id': r['IdBonConsum'], 'number': bon_number.display('bc', r['NumarBC']), 'date': _iso(r['DataBC']), 'receiver': r['Denumire'] or '', 'total': _money(r['ValoareBC'], scale)} for r in rows]


# ---- writing ---------------------------------------------------------------------------------------------------------
def _clean(cursor, repo, ctx, exercise, body):
    errors = {}
    header = body.get('header') if isinstance(body.get('header'), dict) else {}
    raw_meals = body.get('meals') if isinstance(body.get('meals'), dict) else {}
    out = {'date': protocol.to_date(header.get('Data'), 'Data listei', exercise, errors, 'h.Data'), 'comments': _text(header.get('Comentarii'), 'h.Comentarii', errors, False, 255), 'meals': {}}
    for key, (table, p, v, total, d) in MEALS.items():
        raw = raw_meals.get(key) if isinstance(raw_meals.get(key), dict) else {}
        portions, distributed = _int(raw.get('P') if raw.get('P') not in (None, '') else 0), _int(raw.get('D') if raw.get('D') not in (None, '') else 0)
        if portions is None or portions < 0:
            errors['m.%s.P' % key] = 'Numărul de porții trebuie să fie un întreg pozitiv sau zero.'
        if distributed is None or distributed < 0:
            errors['m.%s.D' % key] = 'Numărul distribuit trebuie să fie un întreg pozitiv sau zero.'
        price = _dec(raw.get('VMD') if raw.get('VMD') not in (None, '') else 0)
        value = None
        if price is None or price < 0:
            errors['m.%s.VMD' % key] = 'Valoarea unei porții nu poate fi negativă.'
        elif portions is not None and portions >= 0:
            try:
                numeric.to_stored(price, ctx['scale'])
                value = numeric.calc_round(Decimal(portions) * price, ctx['zec'])
                numeric.to_stored(value, ctx['scale'])
            except numeric.NumericError as error:
                errors['m.%s.VMD' % key] = str(error)
        items = []
        raw_items = raw.get('items') if isinstance(raw.get('items'), list) else []
        if len(raw_items) > _MAX_ITEMS:
            errors['m.%s.items' % key] = 'Cel mult %d feluri pe masă.' % _MAX_ITEMS
        for index, item in enumerate(raw_items[:_MAX_ITEMS]):
            item = item if isinstance(item, dict) else {}
            text = _text(item.get('Explicatie'), 'm.%s.items.%d' % (key, index), errors, False, 255)
            if not text:
                continue                                  # an empty row is not a dish (as in Access)
            dish = _int(item.get('IdFel')) if item.get('IdFel') not in (None, '') else None
            tip = _text(item.get('Tip'), 'm.%s.items.%d' % (key, index), errors, False, 50)
            if dish is not None:
                dishes = repo.select('VC_FeluriMancare', {'IDFel': dish}, columns=('IDFel', 'DenFel'))
                if not dishes:
                    errors['m.%s.items.%d' % (key, index)] = 'Felul ales nu există în această gestiune.'
                elif not tip:
                    tip = dishes[0]['DenFel']
                    if tip and len(tip) > 50:
                        errors['m.%s.items.%d' % (key, index)] = 'Denumirea felului poate avea cel mult 50 de caractere.'
            items.append({'IdFel': dish, 'Tip': tip, 'Explicatie': text})
        out['meals'][key] = {'P': portions, 'D': distributed, 'price': price, 'value': value, 'items': items}
    bons = body.get('bons')
    if bons is None:
        bons = []
    if not isinstance(bons, list) or any(isinstance(b, bool) or not isinstance(b, int) for b in bons):
        errors['bons'] = 'Lista de bonuri este invalidă.'
        bons = []
    out['bons'] = bons
    _fail(errors)
    return out


def _write_meals(repo, ctx, list_id, parsed, now):
    scale = ctx['scale']
    summary = {'IDLISTA': list_id, 'Comentarii': parsed['comments']}
    total = 0
    for key, (table, p, v, value_col, d) in MEALS.items():
        meal = parsed['meals'][key]
        stored = numeric.to_stored(meal['value'], scale)
        total += stored
        summary[p], summary[v], summary[value_col], summary[d] = meal['P'], numeric.to_stored(meal['price'], scale), stored, meal['D']
        for item in meal['items']:
            repo.insert(table, {'IdLista': list_id, 'IdFel': item['IdFel'], 'Tip': item['Tip'], 'Explicatie': item['Explicatie']})
    repo.insert('VC_ListaAlimente', summary)
    return total


def _link_bons(repo, list_id, bons):
    try:
        return lista_bc.set_bonuri(repo, list_id, bons)
    except lista_bc.ListaBcError as error:
        raise NomError('BON_INVALID', str(error), 409) from error


def _bons_total(cursor, ctx, list_id):
    row = _one(cursor, 'SELECT COALESCE(SUM(h.ValoareBC),0) AS t FROM VC_LegBCLA l JOIN VC_Iesiri_H h ON h.SubunitId=l.SubunitId AND h.GestiuneId=l.GestiuneId AND h.ExercitiuId=l.ExercitiuId '
                       'AND h.IdBonConsum=l.IdBonConsum WHERE l.SubunitId=%s AND l.GestiuneId=%s AND l.ExercitiuId=%s AND l.IdLista=%s AND h.Anulat=0', (*_scope(ctx), list_id))
    return Decimal(row['t'])


def _number(cursor, ctx, manual, errors, exclude=None):
    def taken(n):
        sql = 'SELECT 1 FROM VC_LABC WHERE SubunitId=%s AND GestiuneId=%s AND ExercitiuId=%s AND NumarLA=%s AND Anulat=0'
        params = list(_scope(ctx)) + [n]
        if exclude:
            sql += ' AND IdLista<>%s'
            params.append(exclude)
        return bool(_one(cursor, sql + ' LIMIT 1', params))
    if manual in (None, ''):
        return protocol.next_number(cursor, ctx, 'LISTA', taken)
    number = _int(manual)
    if number is None or number < 1:
        errors['h.Numar'] = 'Numărul trebuie să fie un întreg pozitiv.'
        return None
    if taken(number):
        errors['h.Numar'] = 'Numărul %d este deja folosit.' % number
        return None
    protocol.raise_counter(cursor, ctx, 'LISTA', number)
    return number


def create(cursor, repo, ctx, exercise, body, request_key, request_id):
    parsed = _clean(cursor, repo, ctx, exercise, body)
    errors = {}
    number = _number(cursor, ctx, (body.get('header') or {}).get('Numar'), errors)
    _fail(errors)
    list_id = repo.insert('VC_LABC', {'NumarLA': number, 'DATALA': parsed['date'], 'ValoareBon': 0, 'DEP': 0, 'ValoareLista': 0})
    total = _write_meals(repo, ctx, list_id, parsed, utc_now())
    _link_bons(repo, list_id, parsed['bons'])
    bons_total = _bons_total(cursor, ctx, list_id)
    repo.update('VC_LABC', {'ValoareBon': bons_total, 'ValoareLista': total, 'DEP': total - bons_total}, {'IdLista': list_id})
    protocol.freeze(cursor, ctx)
    protocol.audit(cursor, ctx, 'lista.create', 'VC_LABC', list_id, None, {'number': number, 'bons': parsed['bons'], 'total': total}, request_key, request_id)
    return {'id': list_id, 'number': number}


def edit(cursor, repo, ctx, exercise, doc_id, body, request_id):
    from . import recalculation_audit

    rows = repo.select('VC_LABC', {'IdLista': doc_id}, for_update=True)
    if not rows:
        raise NomError('NOT_FOUND', 'Lista nu mai există.', 404)
    before = rows[0]
    if before['Anulat']:
        raise NomError('CANCELLED', 'Lista este anulată și nu mai poate fi modificată.', 409)
    version = _int(body.get('version'))
    if version is None or before['Version'] != version:
        raise NomError('CONFLICT', 'Lista a fost modificată de altcineva. Reîncărcați lista.', 409, {'version': before['Version']})
    document_order.require_latest(cursor, ctx, 'lista', doc_id)
    parsed = _clean(cursor, repo, ctx, exercise, body)
    summary = (repo.select('VC_ListaAlimente', {'IDLISTA': doc_id}) or [{}])[0]
    if any(parsed['meals'][key]['P'] != (summary.get(meal[1]) or 0) for key, meal in MEALS.items()):
        food_list_portions.require_month(cursor, ctx, before['DATALA'], parsed['date'])
    document_order.require_position(cursor, ctx, 'lista', doc_id, parsed['date'])
    errors = {}
    number = before['NumarLA']
    manual = (body.get('header') or {}).get('Numar')
    if manual not in (None, '') and _int(manual) != number:
        number = _number(cursor, ctx, manual, errors, exclude=doc_id)
    _fail(errors)
    prior_balances = recalculation_audit.food_snapshot(cursor, ctx)
    for table in [m[0] for m in MEALS.values()] + ['VC_ListaAlimente']:
        repo.delete(table, {'IDLISTA' if table == 'VC_ListaAlimente' else 'IdLista': doc_id})
    total = _write_meals(repo, ctx, doc_id, parsed, utc_now())
    result = _link_bons(repo, doc_id, parsed['bons'])
    bons_total = _bons_total(cursor, ctx, doc_id)
    if repo.update('VC_LABC', {'NumarLA': number, 'DATALA': parsed['date'], 'ValoareBon': bons_total, 'ValoareLista': total, 'DEP': total - bons_total},
                   {'IdLista': doc_id, 'Version': before['Version']}, bump_version=True) != 1:
        raise NomError('CONFLICT', 'Lista a fost modificată de altcineva. Reîncărcați lista.', 409)
    protocol.audit(cursor, ctx, 'lista.update', 'VC_LABC', doc_id, {'number': before['NumarLA'], 'total': before['ValoareLista']},
                   {'number': number, 'total': total, 'bons_added': result['added'], 'bons_removed': result['removed']}, None, request_id)
    origin = recalculation_audit.cause('lista', doc_id, number, parsed['date'], before['Version'] + 1)
    recalculation_audit.record(cursor, ctx, prior_balances, recalculation_audit.food_snapshot(cursor, ctx),
                               origin, 'lista.balance_recalculated', 'VC_LABC', request_id)
    return {'id': doc_id, 'number': number}


def cancel(cursor, repo, ctx, family, doc_id, body, request_id):
    from .documents import _check_cancel_input
    reason = _check_cancel_input(body)
    rows = repo.select('VC_LABC', {'IdLista': doc_id}, for_update=True)
    if not rows:
        raise NomError('NOT_FOUND', 'Lista nu mai există.', 404)
    before = rows[0]
    if before['Anulat']:
        raise NomError('CANCELLED', 'Lista este deja anulată.', 409)
    version = _int(body.get('version'))
    if version is None or before['Version'] != version:
        raise NomError('CONFLICT', 'Lista a fost modificată de altcineva. Reîncărcați lista.', 409)
    document_order.require_latest(cursor, ctx, 'lista', doc_id)
    freed = lista_bc.current(repo, doc_id)
    repo.delete('VC_LegBCLA', {'IdLista': doc_id})   # the bons are free again; they are not touched
    cursor.execute('UPDATE VC_LABC SET Anulat=1, MotivAnulare=%s, AnulatDe=%s, AnulatLa=NOW(), Version=Version+1 WHERE SubunitId=%s AND GestiuneId=%s AND ExercitiuId=%s AND IdLista=%s',
                   (reason, ctx['email'], *_scope(ctx), doc_id))
    protocol.audit(cursor, ctx, 'lista.cancel', 'VC_LABC', doc_id, {'Anulat': 0, 'bons': freed}, {'Anulat': 1, 'Motiv': reason}, None, request_id)
    return {'id': doc_id}


CREATE = {'lista': create}
EDIT = {'lista': edit}
AUDIT_TABLE = {'lista': 'VC_LABC'}
