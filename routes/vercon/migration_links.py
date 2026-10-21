"""Explicit recovery of migrated returns and storno lines with missing sources.

The route owns the transaction and the gestiune lock. Linking a BR changes only
its source; linking a storno line introduces a stock movement and checks history.
"""
from collections import defaultdict
from utils.clock import utc_now
from decimal import Decimal

from . import documents as docs, protocol
from .nomenclatoare import NomError


def reject(message, reason='SOURCE_INVALID', status=422, field='source'):
    raise NomError(reason, message, status, {field: message})


def _version(row, raw, field='source'):
    version = docs._int(raw)
    if version is None or version < 1:
        reject('Lipsește revizia. Reîncărcați datele.', 'INVALID', 400, field)
    if version != row['Version']:
        reject('Datele au fost modificate de altcineva. Reîncărcați datele.', 'CONFLICT', 409, field)


def link_return(cursor, repo, ctx, doc_id, body, request_id):
    rows = repo.select('VC_Iesiri_H', {'IdBonConsum': doc_id}, for_update=True)
    if not rows or rows[0].get('Retur') != 1:
        reject('Returul nu există în gestiunea și exercițiul curent.', 'NOT_FOUND', 404)
    before = rows[0]
    if before['Anulat']:
        reject('Returul este anulat și nu poate fi asociat.', 'CANCELLED', 409)
    if before['IdRetur'] is not None:
        reject('Returul are deja un bon sursă. Reîncărcați documentul.', 'CONFLICT', 409)
    _version(before, body.get('version'))
    source_id = docs._int(body.get('source'))
    sources = repo.select('VC_Iesiri_H', {'IdBonConsum': source_id}, for_update=True) if source_id else []
    if not sources or sources[0]['Anulat'] or bool(sources[0].get('Retur') or 0):
        reject('Alegeți un bon de consum neanulat din aceeași gestiune și același exercițiu.')
    eligible = {row['variant']: row for row in docs.return_eligible(cursor, ctx, source_id, exclude=doc_id)}
    quantities = defaultdict(Decimal)
    for row in repo.select('VC_IesiriRetur', {'IdBonConsum': doc_id}, for_update=True):
        quantities[row['IdProdus']] += Decimal(row['Cant'] or 0)
    problems = []
    for variant, quantity in quantities.items():
        entry = eligible.get(variant)
        if entry is None:
            problems.append('%s: lipsește din bonul de consum' % _product_name(repo, variant))
        elif quantity <= 0 or quantity > Decimal(entry['left']):
            problems.append('%s: cantitate %s; rămas de returnat %s' % (entry['name'] or str(variant), quantity, entry['left']))
    if problems:
        reject('Asocierea este respinsă. ' + '; '.join(problems) + '.')
    if repo.update('VC_Iesiri_H', {'IdRetur': source_id}, {'IdBonConsum': doc_id, 'Version': before['Version']}, bump_version=True) != 1:
        reject('Returul a fost modificat de altcineva. Reîncărcați documentul.', 'CONFLICT', 409)
    protocol.audit(cursor, ctx, 'br.link', 'VC_Iesiri_H', doc_id,
                   {'IdRetur': None, 'Version': before['Version']},
                   {'IdRetur': source_id, 'Version': before['Version'] + 1}, None, request_id)
    return {'id': doc_id, 'version': before['Version'] + 1}


def _product_name(repo, variant):
    variants = repo.select('VC_ProduseP', {'IdProdus': variant}) if variant is not None else []
    products = repo.select('VC_Produse', {'ID': variants[0]['ID']}) if variants else []
    return '%s (varianta %s)' % (products[0]['DenumireP'], variant) if products else 'Varianta %s' % variant


def orphan_storno(cursor, ctx):
    rows = docs._all(cursor,
        'SELECT x.ID, x.IdProdus, pr.DenumireP, pr.UM, x.Cant, x.PUTVA, x.DataSalvare, '
        'x.Anulat, x.MotivAnulare, x.Version FROM VC_IntrariStorno x ' + docs._name_join('x') +
        ' WHERE x.SubunitId=%s AND x.GestiuneId=%s AND x.ExercitiuId=%s AND x.IdIntrare IS NULL '
        'ORDER BY x.Anulat, x.DataSalvare, x.ID', docs._scope(ctx))
    return [{'id': row['ID'], 'variant': row['IdProdus'], 'name': row['DenumireP'] or 'Varianta %s' % row['IdProdus'],
             'um': row['UM'], 'Cant': docs._money(row['Cant'], ctx['scale']), 'PUTVA': docs._money(row['PUTVA'], ctx['scale']),
             'saved_at': row['DataSalvare'].isoformat(sep=' ') if row['DataSalvare'] else None,
             'annulled': bool(row['Anulat']), 'reason': row['MotivAnulare'], 'version': row['Version']} for row in rows]


def _orphan(repo, line_id, body):
    rows = repo.select('VC_IntrariStorno', {'ID': line_id}, for_update=True)
    if not rows:
        reject('Linia de storno nu există în contextul curent.', 'NOT_FOUND', 404)
    row = rows[0]
    if row['IdIntrare'] is not None:
        reject('Linia este deja legată de o recepție. Reîncărcați lista.', 'CONFLICT', 409)
    if row['Anulat']:
        reject('Linia de storno este deja anulată.', 'CANCELLED', 409)
    _version(row, body.get('version'))
    return row


def link_storno(cursor, repo, ctx, line_id, body, request_id):
    before = _orphan(repo, line_id, body)
    target_id = docs._int(body.get('source'))
    targets = repo.select('VC_NIR', {'IdIntrare': target_id}, for_update=True) if target_id else []
    if not targets or targets[0]['Anulat'] or targets[0].get('Storno') != 1:
        reject('Alegeți o factură storno neanulată din aceeași gestiune și același exercițiu.')
    target = targets[0]
    _version(target, body.get('target_version'))
    sources = repo.select('VC_NIR', {'IdIntrare': target['IdStorno']}, for_update=True) if target['IdStorno'] else []
    if not sources or sources[0]['Anulat'] or bool(sources[0].get('Storno') or 0):
        reject('Factura storno aleasă nu are o recepție sursă validă.')
    if target['Data'] is None or target['DataFc'] is None:
        reject('Factura storno aleasă nu are datele necesare. Corectați mai întâi documentul.')
    if sources[0]['DataFc'] and target['DataFc'] < sources[0]['DataFc']:
        reject('Data facturii storno nu poate fi înaintea facturii stornate. Corectați mai întâi documentul.')
    on = target['Data'].date() if hasattr(target['Data'], 'date') else target['Data']
    # Include the target's existing lines: they already reserve receipt quantity
    # and consume stock. Excluding that header would allow an excessive addition.
    eligible = {row['variant']: row for row in docs.storno_eligible(cursor, ctx, target['IdStorno'], on=on)}
    entry = eligible.get(before['IdProdus'])
    quantity = Decimal(before['Cant'] or 0)
    if entry is None or quantity <= 0 or quantity > Decimal(entry['left']):
        reject('%s: cantitatea %s nu se încadrează în cantitatea recepției și stocul disponibil pentru stornare (%s).' %
               (_product_name(repo, before['IdProdus']), quantity, entry['left'] if entry else '0'))
    if repo.update('VC_IntrariStorno', {'IdIntrare': target_id}, {'ID': line_id, 'Version': before['Version']}, bump_version=True) != 1:
        reject('Linia a fost modificată de altcineva. Reîncărcați lista.', 'CONFLICT', 409)
    # A document editor holding the previous header revision must reload the
    # additional line before replacing its lines with an edited draft.
    if repo.update('VC_NIR', {}, {'IdIntrare': target_id, 'Version': target['Version']}, bump_version=True) != 1:
        reject('Factura storno a fost modificată de altcineva. Reîncărcați lista.', 'CONFLICT', 409)
    protocol.check_timeline(cursor, ctx, {before['IdProdus']})
    after = {'IdIntrare': target_id, 'line': line_id, 'Version': before['Version'] + 1, 'target_version': target['Version'] + 1}
    protocol.audit(cursor, ctx, 'storno.link', 'VC_IntrariStorno', line_id, {'IdIntrare': None, 'Version': before['Version']}, after, None, request_id)
    protocol.audit(cursor, ctx, 'storno.link', 'VC_NIR', target_id, {'Version': target['Version']}, after, None, request_id)
    return {'id': line_id, 'version': before['Version'] + 1, 'document_id': target_id, 'target_version': target['Version'] + 1}


def cancel_storno(cursor, repo, ctx, line_id, body, request_id):
    before = _orphan(repo, line_id, body)
    reason = docs._check_cancel_input(body)
    values = {'Anulat': 1, 'MotivAnulare': reason, 'AnulatDe': ctx['email'], 'AnulatLa': utc_now()}
    if repo.update('VC_IntrariStorno', values, {'ID': line_id, 'Version': before['Version']}, bump_version=True) != 1:
        reject('Linia a fost modificată de altcineva. Reîncărcați lista.', 'CONFLICT', 409)
    protocol.audit(cursor, ctx, 'storno.orphan.cancel', 'VC_IntrariStorno', line_id,
                   {'Anulat': 0, 'Version': before['Version']}, {**values, 'Version': before['Version'] + 1}, None, request_id)
    return {'id': line_id, 'version': before['Version'] + 1}
