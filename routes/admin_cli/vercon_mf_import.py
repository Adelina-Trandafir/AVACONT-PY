"""KBotAdmin: one-time import of fixed assets (MF) from the old Access register (mf.mdb) into a VERCON MF gestiune.

The Access file is read on the operator's PC; this module only receives the assets as JSON. Nothing is written by the
check; the import writes through the SAME code as the receipt page (`receipts.create_nir`), one STOC_INITIAL document
per asset dated on the asset's entry date, so the assets enter the gestiune (warehouse account) and nothing else:
the holder is given later by a handover voucher. All assets of a call are written in one transaction.

Verbs: `vercon-mf-targets` (subunits, MF gestiuni and warehouse accounts of a unit), `vercon-mf-check` (what the import
would do, nothing written), `vercon-mf-import` (the write).
"""
import base64
import json
import uuid
from decimal import Decimal

from routes.admin_cli.admin_common import AdminError, _actor
from routes.admin_cli.vercon_reports import unit
from routes.vercon.connection import connection

_MAX_ASSETS = 200
_UM = 'buc'
_WAREHOUSE = ('MFM', 'MAGAZIE')


def _payload(raw):
    try:
        if len(raw) > 400000:
            raise ValueError()
        data = json.loads(base64.b64decode(raw, validate=True).decode('utf-8'))
    except (ValueError, UnicodeError):
        raise AdminError('Lista de mijloace fixe trimisă nu este validă.') from None
    if not isinstance(data, list) or not 1 <= len(data) <= _MAX_ASSETS:
        raise AdminError('Trimiteți între 1 și %d mijloace fixe.' % _MAX_ASSETS)
    return data


def _all(cursor, sql, params=()):
    cursor.execute(sql, params)
    return cursor.fetchall()


def targets(args):
    name = unit(args)
    with connection(name) as conn:
        cursor = conn.cursor(dictionary=True)
        return {
            'unit': name,
            'subunits': _all(cursor, 'SELECT SubunitId AS id, Name AS name FROM VC_Subunitati WHERE Active=1 ORDER BY Name'),
            'gestiuni': _all(cursor, "SELECT GestiuneId AS id, SubunitId AS subunit, Name AS name, State AS state FROM VC_Gestiuni "
                                     "WHERE Type='MF' AND Active=1 ORDER BY SubunitId, Name"),
            'accounts': _all(cursor, "SELECT IdMasina AS id, SubunitId AS subunit, GestiuneId AS gestiune, Masina AS name FROM VC_Masini "
                                     "WHERE Activ=1 AND TIP IN ('MFM','MAGAZIE') ORDER BY SubunitId, GestiuneId, Masina"),
        }


def _gestiune(cursor, subunit, gestiune, lock=False):
    row = _all(cursor, 'SELECT GestiuneId, SubunitId, Name, Type, State, Zec, Scara FROM VC_Gestiuni '
                       'WHERE SubunitId=%s AND GestiuneId=%s AND Active=1' + (' FOR UPDATE' if lock else ''), (subunit, gestiune))
    if not row or row[0]['Type'] != 'MF':
        raise AdminError('Alegeți o gestiune de mijloace fixe (tip MF) din subunitatea aleasă.')
    if row[0]['State'] == 'ARHIVATA':
        raise AdminError('Gestiunea este arhivată.')
    return row[0]


def _account(cursor, subunit, gestiune, account):
    row = _all(cursor, 'SELECT IdMasina, TIP FROM VC_Masini WHERE SubunitId=%s AND GestiuneId=%s AND IdMasina=%s AND Activ=1',
               (subunit, gestiune, account))
    if not row or str(row[0]['TIP'] or '').strip().upper() not in _WAREHOUSE:
        raise AdminError('Alegeți un cont de tip MAGAZIE din această gestiune.')


def _zero_vat(cursor, subunit, gestiune):
    row = _all(cursor, 'SELECT IdTVA FROM VC_TVA WHERE SubunitId=%s AND GestiuneId=%s AND Activ=1 AND TVA=0 ORDER BY IdTVA LIMIT 1',
               (subunit, gestiune))
    if not row:
        raise AdminError('Gestiunea nu are o cotă TVA 0 activă. Valoarea de intrare din Access se păstrează ca atare, fără TVA în plus; '
                         'adăugați cota 0 în nomenclatorul «Cote TVA» al gestiunii.')
    return row[0]['IdTVA']


def _assess(cursor, subunit, gestiune, assets):
    """One verdict per asset: the cleaned values and the list of reasons it cannot be imported."""
    from routes.vercon import documents as d, protocol
    from routes.vercon.nomenclatoare import NomError
    from routes.vercon.receipt_products import duration_message
    used = {str(r['COD']).strip().casefold() for r in _all(cursor, 'SELECT COD FROM VC_ProduseP WHERE SubunitId=%s AND GestiuneId=%s AND COD IS NOT NULL',
                                                           (subunit, gestiune))}
    seen, out = set(), []
    for index, raw in enumerate(assets):
        raw = raw if isinstance(raw, dict) else {}
        reasons = []
        code = str(raw.get('code') or '').strip()
        name = str(raw.get('name') or '').strip()
        value = d._dec(raw.get('value'))
        dnf, df = d._int(raw.get('dnf')), d._int(raw.get('df'))
        wanted = str(raw.get('cc') or '').strip()
        try:
            entry = protocol.to_date(raw.get('entry_date'), 'Data intrării')
        except NomError as error:
            entry = None
            reasons.append(str(error))
        if not code:
            reasons.append('Numărul de inventar lipsește.')
        elif code.casefold() in used:
            reasons.append('Numărul de inventar %s există deja în această gestiune.' % code)
        elif code.casefold() in seen:
            reasons.append('Numărul de inventar %s apare de două ori în listă.' % code)
        seen.add(code.casefold())
        if not name or len(name) > 255 or len(code) > 255:
            reasons.append('Denumirea lipsește sau numărul de inventar ori denumirea depășesc 255 de caractere.')
        if value is None or value <= 0:
            reasons.append('Valoarea de intrare trebuie să fie pozitivă.')
        if dnf is None or dnf < 1:
            reasons.append('Durata normală trebuie să fie de cel puțin 1 lună.')
        if df is None or df < 0 or (dnf is not None and df > dnf):
            reasons.append('Durata folosită este invalidă.')
        row = _all(cursor, "SELECT Cod, DNF_MIN, DNF_MAX FROM AVACONT_COMUN.VC_ClasificariMF WHERE TRIM(TRAILING '.' FROM Cod)=%s LIMIT 1",
                   (wanted.rstrip('.'),)) if wanted else []
        classification = row[0] if row else None
        if not classification:
            reasons.append('Clasificarea «%s» nu există în AVACONT_COMUN.VC_ClasificariMF.' % wanted)
        elif classification['DNF_MIN'] is None or classification['DNF_MAX'] is None:
            reasons.append('Clasificarea %s nu are limite de durată.' % classification['Cod'])
        elif dnf is not None and not classification['DNF_MIN'] * 12 <= dnf <= classification['DNF_MAX'] * 12:
            reasons.append(duration_message(classification['Cod'], classification['DNF_MIN'], classification['DNF_MAX']))
        out.append({'index': index, 'code': code, 'name': name, 'entry': entry, 'value': value, 'dnf': dnf, 'df': df,
                    'cc': classification['Cod'] if classification else wanted, 'reasons': reasons})
    return out


def _report(assessed):
    return [{'index': a['index'], 'code': a['code'], 'name': a['name'], 'cc': a['cc'], 'ok': not a['reasons'],
             'reasons': a['reasons']} for a in assessed]


def check(args):
    name = unit(args)
    assets = _payload(args.payload)
    with connection(name) as conn:
        cursor = conn.cursor(dictionary=True)
        gest = _gestiune(cursor, args.subunit, args.gestiune)
        _account(cursor, args.subunit, args.gestiune, args.account)
        vat = None
        try:
            vat = _zero_vat(cursor, args.subunit, args.gestiune)
        except AdminError as error:
            warning = str(error)
        else:
            warning = ''
        return {'unit': name, 'gestiune': gest['Name'], 'vat_ready': vat is not None, 'warning': warning,
                'assets': _report(_assess(cursor, args.subunit, args.gestiune, assets))}


def _locked_by_other(cursor, subunit, gestiune):
    row = _all(cursor, 'SELECT HolderEmail AS email FROM VC_ContextLock WHERE SubunitId=%s AND GestiuneId=%s AND ExpiresAt >= NOW()',
               (subunit, gestiune))
    return row[0]['email'] if row else None


def run(args):
    from routes.vercon import protocol, receipts
    from routes.vercon.nomenclatoare import NomError
    from routes.vercon.repository import ScopedRepository
    name = unit(args)
    assets = _payload(args.payload)
    created = []
    with connection(name) as conn:
        conn.start_transaction(isolation_level='READ COMMITTED')
        cursor = conn.cursor(dictionary=True)
        try:
            gest = _gestiune(cursor, args.subunit, args.gestiune, lock=True)
            _account(cursor, args.subunit, args.gestiune, args.account)
            holder = _locked_by_other(cursor, args.subunit, args.gestiune)
            if holder:
                raise AdminError('Gestiunea este deschisă acum de %s. Reîncercați după ce o închide.' % holder)
            vat = _zero_vat(cursor, args.subunit, args.gestiune)
            assessed = _assess(cursor, args.subunit, args.gestiune, assets)
            bad = [a for a in assessed if a['reasons']]
            if bad:
                raise AdminError('Importul nu a pornit; nimic nu s-a scris. ' + ' '.join(
                    '%s: %s' % (a['code'] or a['index'] + 1, ' '.join(a['reasons'])) for a in bad))
            exercise = protocol.exercise_of(cursor, args.subunit, args.gestiune, 'MF', None)
            ctx = {'unit': name, 'subunit': args.subunit, 'gestiune': args.gestiune, 'exercitiu': exercise['id'], 'email': _actor(),
                   'type': 'MF', 'zec': int(gest['Zec']), 'scale': int(gest['Scara']), 'year': exercise['an'], 'an': exercise['an'],
                   'closed': exercise['closed']}
            repo = ScopedRepository(cursor, args.subunit, args.gestiune, exercise['id'])
            key = 'mfimport-' + uuid.uuid4().hex[:16]
            for position, a in enumerate(assessed, 1):
                day = a['entry'].isoformat()
                body = {'header': {'TipDoc': 'STOC_INITIAL', 'Data': day},
                        'lines': [{'key': 'a%d' % position, 'product': -position, 'code': a['code'], 'data_intrarii': day,
                                   'DNF': a['dnf'], 'DF': a['df'], 'Cant': '1', 'Pret': format(a['value'], 'f'), 'IDTVA': vat,
                                   'new_product': {'DenumireP': a['name'], 'UM': _UM, 'IdMasina': args.account, 'CC': a['cc'], 'DNF': a['dnf']}}]}
                result = receipts.create_nir(cursor, repo, ctx, exercise, body, key, key)
                created.append({'code': a['code'], 'name': a['name'], 'document': result['id'], 'number': result['number']})
            protocol.audit(cursor, ctx, 'mf.import', 'VC_NIR', created[0]['document'], None,
                           {'source': 'mf.mdb', 'assets': [c['code'] for c in created]}, key, key)
            conn.commit()
        except NomError as error:
            conn.rollback()
            detail = ' '.join(str(v) for v in error.fields.values())
            raise AdminError('Importul s-a oprit, nimic nu s-a scris. %s %s' % (error, detail if detail not in str(error) else '')) from None
        except Exception:
            conn.rollback()
            raise
    return {'unit': name, 'created': created}


def register(sub):
    for verb, fn in (('vercon-mf-targets', targets), ('vercon-mf-check', check), ('vercon-mf-import', run)):
        p = sub.add_parser(verb)
        p.add_argument('--db', required=True)
        if verb != 'vercon-mf-targets':
            p.add_argument('--subunit', type=int, required=True)
            p.add_argument('--gestiune', type=int, required=True)
            p.add_argument('--account', type=int, required=True)
            p.add_argument('--payload', required=True)
        p.set_defaults(fn=fn)
