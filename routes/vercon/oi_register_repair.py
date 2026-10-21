"""Manual repair of the OI/MF register: receipt positions that arrived without their product.

The old program kept the product on the bon's own lines and often left IdProdus empty on the register's receipt rows;
the migration copies the register as it is. The custody readers need the product on every position, so the operator
settles those positions here: each one is listed with the product the bon's own lines suggest, and only the product the
operator confirms is written. Nothing else in the register changes, and every change is audited.
"""
from collections import Counter, defaultdict
from decimal import Decimal

from flask import request

from . import oi_custody, protocol
from .documents import _all, _int, _money, _name_join
from .nomenclatoare import NomError

LINES = {'bp': ('VC_OI_Predare', 'IDBP', 'NRBP'), 'bt': ('VC_OI_Transfer', 'IDBT', 'NRBT')}
SURE, ORDER, CHOOSE = 'sure', 'order', 'choose'
OPERATION = 'oi.register.product'


def reject(message, reason='REGISTER_INVALID', status=422):
    raise NomError(reason, message, status)


def _family(row):
    return ('bp', row['IDBP']) if row['IDBP'] else ('bt', row['IDBT'])


def _amount(value):
    return Decimal(value or 0)


def _unresolved(cursor, ctx):
    return _all(cursor, 'SELECT IDOI, IDBP, IDBT, IDC_PTC, Cant, PUTVA, Version FROM VC_OI_Produse '
                'WHERE SubunitId=%s AND GestiuneId=%s AND COALESCE(Cant,0)>0 AND COALESCE(IdProdus,0)=0 '
                'AND (COALESCE(IDBP,0)<>0 OR COALESCE(IDBT,0)<>0) ORDER BY IDOI', (ctx['subunit'], ctx['gestiune']))


def _lines(cursor, ctx, family, documents):
    """The active lines of the bons (all exercises of the gestiune), tagged with their family."""
    table, key, number = LINES[family]
    marks = ','.join(['%s'] * len(documents))
    sql = ('SELECT l.ID, l.{key} AS document, l.{number} AS number, l.IdProdus, l.Cant, l.PUTVA, pr.DenumireP, pr.UM FROM {table} l '
           .format(key=key, number=number, table=table) + _name_join('l') +
           ' WHERE l.SubunitId=%s AND l.GestiuneId=%s AND l.Anulat=0 AND l.{key} IN ({marks}) ORDER BY l.ID'.format(key=key, marks=marks))
    return [{**line, 'family': family} for line in _all(cursor, sql, (ctx['subunit'], ctx['gestiune'], *documents))]


def _taken(cursor, ctx):
    """How many register receipt positions already carry each (bon, product, quantity, price)."""
    taken = Counter()
    for row in _all(cursor, 'SELECT IDBP, IDBT, IdProdus, Cant, PUTVA FROM VC_OI_Produse WHERE SubunitId=%s AND GestiuneId=%s '
                    'AND COALESCE(Cant,0)>0 AND COALESCE(IdProdus,0)<>0 AND (COALESCE(IDBP,0)<>0 OR COALESCE(IDBT,0)<>0)',
                    (ctx['subunit'], ctx['gestiune'])):
        family, document = _family(row)
        taken[(family, document, row['IdProdus'], _amount(row['Cant']), _amount(row['PUTVA']))] += 1
    return taken


def _free_lines(lines, taken):
    """Per (family, bon, quantity, price): the bon's lines that no register position accounts for yet, in line order."""
    left, free = taken.copy(), defaultdict(list)
    for line in lines:
        key = (line['family'], line['document'], line['IdProdus'], _amount(line['Cant']), _amount(line['PUTVA']))
        if left[key] > 0:
            left[key] -= 1
            continue
        free[(line['family'], line['document'], key[3], key[4])].append(line)
    return free


def pending(cursor, ctx):
    rows = _unresolved(cursor, ctx)
    documents = defaultdict(set)
    for row in rows:
        family, document = _family(row)
        documents[family].add(document)
    lines = [line for family, found in documents.items() for line in _lines(cursor, ctx, family, sorted(found))]
    free = _free_lines(lines, _taken(cursor, ctx))
    numbers = {(line['family'], line['document']): line['number'] for line in lines if line['number']}
    groups = defaultdict(list)
    for row in rows:
        family, document = _family(row)
        groups[(family, document, _amount(row['Cant']), _amount(row['PUTVA']))].append(row)
    products, result = {}, []
    for key, members in groups.items():
        candidates = [line for line in free.get(key, []) if line['IdProdus']]
        variants = sorted({line['IdProdus'] for line in candidates})
        for line in candidates:
            products[line['IdProdus']] = {'name': line['DenumireP'] or 'Varianta %s' % line['IdProdus'], 'um': line['UM'] or ''}
        same_count = len(candidates) == len(members)
        for index, row in enumerate(members):
            if same_count and len(variants) == 1:
                status, proposal = SURE, variants[0]
            elif same_count:
                status, proposal = ORDER, candidates[index]['IdProdus']
            else:
                status, proposal = CHOOSE, None
            result.append({'id': row['IDOI'], 'family': key[0], 'document': key[1], 'number': numbers.get((key[0], key[1])) or str(key[1]),
                           'Cant': _money(row['Cant'], ctx['scale']), 'PUTVA': _money(row['PUTVA'], ctx['scale']),
                           'status': status, 'proposal': proposal, 'choices': variants, 'version': row['Version']})
    result.sort(key=lambda item: item['id'])
    return result, products


def problem(cursor, ctx):
    """Why the custody readers still refuse the register (None when they accept it)."""
    try:
        oi_custody.events(cursor, ctx)
    except NomError as error:
        return str(error)
    return None


def listing(cursor, ctx):
    rows, products = pending(cursor, ctx)
    return {'rows': rows, 'products': products, 'problem': problem(cursor, ctx), 'can_write': not ctx['closed']}


def apply(cursor, repo, ctx, data, request_id):
    """Write the products the operator confirmed. All of them are written, or none."""
    raw = data.get('assignments')
    if not isinstance(raw, list) or not raw or len(raw) > 2000:
        reject('Alegeți cel puțin o poziție de reglat.', 'INVALID', 400)
    wanted = {}
    for item in raw:
        position, variant, version = (_int(item.get(k)) if isinstance(item, dict) else None for k in ('id', 'variant', 'version'))
        if position is None or variant is None or version is None or position in wanted:
            reject('Cererea de reglare este invalidă. Reîncărcați lista.', 'INVALID', 400)
        wanted[position] = (variant, version)
    rows = {row['IDOI']: row for row in _unresolved(cursor, ctx)}
    locked = {}
    for position in sorted(wanted):
        found = repo.select('VC_OI_Produse', {'IDOI': position}, for_update=True)
        if not found or position not in rows:
            reject('O poziție a fost reglată sau modificată între timp. Reîncărcați lista.', 'CONFLICT', 409)
        locked[position] = found[0]
    documents = defaultdict(set)
    for position in sorted(wanted):
        family, document = _family(locked[position])
        documents[family].add(document)
    lines = [line for family, found in documents.items() for line in _lines(cursor, ctx, family, sorted(found))]
    capacity, taken = Counter(), _taken(cursor, ctx)
    for line in lines:
        capacity[(line['family'], line['document'], line['IdProdus'], _amount(line['Cant']), _amount(line['PUTVA']))] += 1
    for position in sorted(wanted):
        variant, version = wanted[position]
        row = locked[position]
        if row['Version'] != version:
            reject('Poziția %s a fost modificată de altcineva. Reîncărcați lista.' % position, 'CONFLICT', 409)
        family, document = _family(row)
        key = (family, document, variant, _amount(row['Cant']), _amount(row['PUTVA']))
        if taken[key] >= capacity[key]:
            reject('Poziția %s: bonul %s nu are o linie cu acest produs, cu aceeași cantitate și același preț, care să nu fie deja folosită.'
                   % (position, document), 'PRODUCT_INVALID')
        taken[key] += 1
        if repo.update('VC_OI_Produse', {'IdProdus': variant}, {'IDOI': position, 'Version': version}, bump_version=True) != 1:
            reject('Poziția %s a fost modificată de altcineva. Reîncărcați lista.' % position, 'CONFLICT', 409)
        protocol.audit(cursor, ctx, OPERATION, 'VC_OI_Produse', position, {'IdProdus': None, 'Version': version},
                       {'IdProdus': variant, 'Version': version + 1}, None, request_id)
    return {'applied': len(wanted), 'problem': problem(cursor, ctx)}


def register(bp, guard, scoped, session, build, repo_of, need, right):
    def family_of(family):
        if family not in ('bp', 'bt', 'bs'):
            raise NomError('FAMILY_UNKNOWN', 'Reglarea registrului nu există pentru acest tip de bon.', 404)
        return family

    @bp.get('/api/vercon/doc/<family>/register')
    @guard('read')
    def register_list(family):
        family_of(family)
        need(family)
        vctx = scoped('read')
        with session(False) as cursor:
            ctx, _ = build(cursor, vctx, family, False)
            return listing(cursor, ctx)

    @bp.post('/api/vercon/doc/<family>/register/product')
    @guard('read')
    def register_write(family):
        family_of(family)
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise NomError('INVALID', 'Cerere invalidă.', 400)
        need(family)
        vctx = scoped(right[family], data)
        key = str(data.get('request_key') or '')[:64]
        fp = protocol.fingerprint(OPERATION, data.get('assignments'))
        with session(True) as cursor:
            ctx, _ = build(cursor, vctx, family, True)
            replay = protocol.replayed(cursor, ctx, key, fp) if key else None
            result = replay if replay is not None else apply(cursor, repo_of(cursor, ctx), ctx, data,
                                                             (request.headers.get('X-Request-Id') or '')[:64] or None)
            if key and replay is None:
                protocol.remember(cursor, ctx, key, fp, result)
        return {'result': result}
