"""SLICE-VC06-22/26: partner list and restricted ANAF updates for every VR reader.

Only existing Parteneri rows are updated. The client supplies a fiscal code, never
the name/address to save. A manual save repeats the ANAF lookup and changes only
CodFiscal when ANAF explicitly reports the code as missing.
"""
import hashlib
import json
from contextlib import contextmanager

from flask import g, request

from routes.inregistrare import anaf
from utils import vercon_suppliers as suppliers
from utils.database import get_kbot_comun_connection

from .capabilities import FormUnavailable
from .nomenclatoare import NomError, audit

OPERATION = 'parteneri_anaf'
_COLUMNS = ('IdPartener', 'IdUnitate', 'CodPartener', 'DenumirePartener', 'CodFiscal',
            'Adresa', 'ContIBAN', 'Banca', 'Tip', 'Ascuns')
_SELECT = 'SELECT ' + ','.join(_COLUMNS) + ' FROM Parteneri'
_VISIBLE = "COALESCE(Ascuns,0)=0 AND TRIM(COALESCE(Tip,''))='1'"


def is_administrator(context):
    """Read the actual unit role for the Administrator's partner-edit restriction."""
    conn = get_kbot_comun_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute('SELECT Rol FROM Unitati_Utilizatori WHERE UN=%s AND DC=%s',
                       (context['email'], context['db_name']))
        return any(str(row['Rol'] or '').strip().casefold() == 'administrator' for row in cursor.fetchall())
    finally:
        conn.close()


def _version(row):
    return hashlib.sha256(json.dumps(row, sort_keys=True, ensure_ascii=False, default=str).encode('utf-8')).hexdigest()


def _present(row):
    return {'id': row['IdPartener'], 'version': _version(row),
            **{key: row[key] or '' for key in ('CodPartener', 'DenumirePartener', 'CodFiscal', 'Adresa', 'ContIBAN', 'Banca')}}


def _one(cursor, row_id, version, locked=False):
    cursor.execute(_SELECT + ' WHERE IdPartener=%s AND ' + _VISIBLE + (' FOR UPDATE' if locked else ''), (row_id,))
    row = cursor.fetchone()
    if row is None:
        raise NomError('NOT_FOUND', 'Partenerul nu mai este disponibil. Reîmprospătați lista.', 404)
    if not isinstance(version, str) or version != _version(row):
        raise NomError('PARTNER_CHANGED', 'Partenerul a fost modificat între timp. Reîmprospătați lista.', 409)
    return row


def _lookup(cf):
    try:
        found = anaf.lookup(cf, strict_missing=True)
    except anaf.AnafNotFound:
        return None
    except anaf.AnafIncomplete:
        raise NomError('ANAF_INCOMPLETE', 'ANAF a găsit codul, dar nu a întors denumirea. Reîncercați mai târziu.', 502) from None
    except anaf.AnafUnavailable:
        raise NomError('ANAF_UNAVAILABLE', 'ANAF nu a putut fi contactat. Reîncercați mai târziu.', 502) from None
    if suppliers.code(found['cui']) != cf:
        raise NomError('ANAF_INVALID', 'Codul întors de ANAF nu coincide cu cel cerut.', 502)
    if any(len(found[key]) > 255 for key in ('denumire', 'adresa')):
        raise NomError('ANAF_INVALID', 'Datele ANAF depășesc lungimea permisă pentru partener.', 422)
    return found


def _refuse_own_code(cf):
    conn = get_kbot_comun_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute('SELECT CF FROM Unitati WHERE DC=%s', (g.portal['db_name'],))
        if any(suppliers.code(row['CF']) == cf for row in cursor.fetchall()):
            raise NomError('OWN_FISCAL_CODE', 'Unitatea nu poate fi propriul furnizor.', 422)
    finally:
        conn.close()


def register(bp, guard, scoped, open_db):
    @contextmanager
    def session(write=False):
        if open_db is None:
            raise FormUnavailable('NO_DATABASE', 'Partenerii nu sunt disponibili în previzualizarea locală.', 503)
        with open_db(g.portal['db_name']) as conn:
            cursor = conn.cursor(dictionary=True)
            try:
                yield cursor
                if write:
                    conn.commit()
            except Exception:
                conn.rollback()
                raise

    @bp.get('/api/vercon/partners')
    @guard('read')
    def partner_list():
        scoped('read')
        with session() as cursor:
            # Include partners without CF so their fiscal identity can be completed.
            cursor.execute(_SELECT + ' WHERE ' + _VISIBLE)
            rows = cursor.fetchall()
        rows.sort(key=lambda row: (suppliers.name_key(row['DenumirePartener']),
                                   suppliers.name_key(row['CodPartener']), suppliers.name_key(row['CodFiscal'])))
        return {'rows': [_present(row) for row in rows], 'can_update': OPERATION in g.vercon_operations}

    @bp.post('/api/vercon/partners/<int:row_id>/anaf')
    @guard(OPERATION)
    def partner_update(row_id):
        data = request.get_json(silent=True)
        permitted = {'subunit', 'gestiune', 'year', 'version', 'cod_fiscal', 'mode'}
        if not isinstance(data, dict) or set(data) - permitted or data.get('mode') not in ('anaf', 'fiscal'):
            raise NomError('INVALID', 'Sunt permise numai actualizarea ANAF și completarea codului fiscal.', 400)
        ctx = scoped(OPERATION, data)
        cf = suppliers.code(data.get('cod_fiscal'))
        if not cf.isascii() or not cf.isdigit() or not anaf.is_valid_cf(cf):
            raise NomError('INVALID', 'Introduceți un cod fiscal românesc de 2–10 cifre.', 422)
        _refuse_own_code(cf)
        with session() as cursor:
            stored = _one(cursor, row_id, data.get('version'))
        # No database row lock is held while waiting for the external service.
        found = _lookup(cf)
        if found is None and data['mode'] == 'anaf':
            return {'found': False, 'cod_fiscal': cf}
        if found is not None and data['mode'] == 'fiscal':
            raise NomError('ANAF_FOUND', 'Codul există în ANAF. Folosiți «Sincronizează cu ANAF».', 409)
        with session(True) as cursor:
            if suppliers.code(stored['CodFiscal']) != cf:
                # Serialize fiscal changes across gestiuni and reject duplicates, including hidden partners.
                cursor.execute('SELECT IdPartener,CodFiscal FROM Parteneri ORDER BY IdPartener FOR UPDATE')
                if any(row['IdPartener'] != row_id and suppliers.code(row['CodFiscal']) == cf for row in cursor.fetchall()):
                    raise NomError('FISCAL_EXISTS', 'Există deja un partener cu acest cod fiscal.', 409)
            before = _one(cursor, row_id, data.get('version'), locked=True)
            after = {**before, 'CodFiscal': cf}
            if found is None:
                cursor.execute('UPDATE Parteneri SET CodFiscal=%s WHERE IdPartener=%s', (cf, row_id))
            else:
                after.update(DenumirePartener=found['denumire'], Adresa=found['adresa'])
                cursor.execute('UPDATE Parteneri SET CodFiscal=%s,DenumirePartener=%s,Adresa=%s WHERE IdPartener=%s',
                               (cf, found['denumire'], found['adresa'], row_id))
            who = {**ctx['selection'], 'email': g.portal['email']}
            audit(cursor, who, 'partner.anaf' if found else 'partner.fiscal', 'Parteneri', row_id,
                  before, after, None, request.headers.get('X-Request-Id'))
        return {'found': found is not None, 'row': _present(after)}
