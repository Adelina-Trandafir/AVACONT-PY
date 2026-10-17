"""SLICE-VC06-01: the nomenclatures of a gestiune (categories/accounts, products and their price variants,
holders/clients, VAT rates, the two committees) -- one declarative table, one set of rules.

Rules, the same for every nomenclature:
  * a row belongs to ONE gestiune (the ScopedRepository adds the context to every statement); the same name in
    another gestiune is another row, never merged;
  * nothing is deleted: a row is switched inactive, so a document that points at it keeps its history; changing a
    nomenclature never rewrites a line of a saved document (lines keep their own quantity / price / values);
  * every change carries the revision (Version) the client saw; an old revision is a conflict, never an overwrite;
  * a create carries a RequestKey (VC_Operations): a lost answer is replayed, never applied twice;
  * every change writes a VC_Audit row in the same transaction (the CNP of a client is masked there);
  * values are validated here, whatever the page did: length, required, VAT 0-100%, price >= 0 on the DECIMAL(20,4) of
    the gestiune (refused, never rounded, when it has more decimals than the scale).
Pure rules + statements over a ScopedRepository; no connection is opened here.
"""
import hashlib
import json
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from . import numeric
from .capabilities import MF

_CONTROL = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f]')
_MASKED_IN_AUDIT = frozenset({'CNP'})


class NomError(Exception):
    def __init__(self, reason, message, status=400, fields=None):
        super().__init__(message)
        self.reason, self.status, self.fields = reason, status, fields or {}


def _text(key, label, max_len, required=False, types=None):
    return {'key': key, 'label': label, 'type': 'text', 'max': max_len, 'required': required, 'types': types}


_COMMITTEE = [_text('Receptie1', 'Membru 1', 50, True), _text('Receptie2', 'Membru 2', 50),
              _text('Receptie3', 'Membru 3', 50), _text('Receptie4', 'Membru 4', 50)]

# kind -> spec. `title` is the field shown as the name of a row (lists, selectors, the audit trail).
SPECS = {
    'categorii': {
        'label': 'Categorii / Conturi', 'table': 'VC_Masini', 'pk': 'IdMasina', 'title': 'Masina', 'stamp': False,
        'fields': [_text('Masina', 'Categorie / cont', 50, True), _text('TIP', 'Tip', 255)],
    },
    'produse': {
        'label': 'Produse', 'table': 'VC_Produse', 'pk': 'ID', 'title': 'DenumireP', 'stamp': True,
        'fields': [
            _text('DenumireP', 'Denumire produs', 255, True), _text('UM', 'UM', 50, True), _text('CPV', 'CPV', 255),
            {'key': 'IdMasina', 'label': 'Categorie / cont', 'type': 'ref', 'ref': 'categorii', 'required': True},
            _text('CC', 'Clasificare M.F.', 255, False, frozenset({MF})),
            {'key': 'DNF', 'label': 'Durată normală de funcționare (luni)', 'type': 'int', 'min': 0, 'required': False, 'types': frozenset({MF})},
        ],
    },
    'variante': {
        'label': 'Variante de preț', 'table': 'VC_ProduseP', 'pk': 'IdProdus', 'title': 'COD', 'stamp': True,
        'parent': ('ID', 'produse'),
        'fields': [{'key': 'PUTVA', 'label': 'Preț cu TVA', 'type': 'money', 'required': True}, _text('COD', 'Cod', 255)],
    },
    'clienti': {
        'label': 'Clienți / Gestionari', 'table': 'VC_Clienti', 'pk': 'IDC', 'title': 'Denumire', 'stamp': False,
        'fields': [_text('Denumire', 'Denumire', 50, True), _text('Adresa', 'Adresă', 50), _text('CF', 'CF', 10),
                   _text('J', 'Nr. reg. com.', 20), _text('CNP', 'CNP', 13), _text('Cont', 'Cont', 50)],
    },
    'tva': {
        'label': 'Cote TVA', 'table': 'VC_TVA', 'pk': 'IdTVA', 'title': 'Explicatie', 'stamp': False,
        'fields': [{'key': 'TVA', 'label': 'Cota (%)', 'type': 'percent', 'required': True}, _text('Explicatie', 'Explicație', 50)],
    },
    'comisie_nir': {
        'label': 'Comisie de recepție', 'table': 'VC_ComisieNIR', 'pk': 'IdComisie', 'title': 'Receptie1', 'stamp': False,
        'fields': _COMMITTEE,
    },
    'feluri': {
        'label': 'Feluri de mâncare', 'table': 'VC_FeluriMancare', 'pk': 'IDFel', 'title': 'DenFel', 'stamp': False,
        'fields': [_text('DenFel', 'Denumire fel', 50, True), _text('Explicatie', 'Explicație', 255)],
    },
    'comisie_inventar': {
        'label': 'Comisie de inventariere', 'table': 'VC_ComisieInventar', 'pk': 'IdComisieInv', 'title': 'Receptie1', 'stamp': False,
        'fields': _COMMITTEE,
    },
}

# form key (capabilities.FORMS) -> the nomenclatures that form edits (the first one is the main list)
FORM_KINDS = {
    'baza_date': ('categorii', 'tva', 'feluri'),
    'comisii': ('comisie_nir', 'comisie_inventar'),
    'produse': ('produse', 'variante'),
    'gestionari': ('clienti',),
}


def spec_of(kind):
    spec = SPECS.get(kind)
    if spec is None:
        raise NomError('KIND_UNKNOWN', 'Nomenclatorul nu există.', 404)
    return spec


def fields_for(spec, gestiune_type):
    return [f for f in spec['fields'] if not f.get('types') or gestiune_type in f['types']]


def columns_for(kind, gestiune_type):
    """What the page needs to build the list and the form: key, label, the parse type, limits, the referenced kind."""
    out = []
    for f in fields_for(spec_of(kind), gestiune_type):
        col = {'key': f['key'], 'label': f['label'], 'required': bool(f.get('required')),
               'type': {'percent': 'decimal', 'money': 'decimal'}.get(f['type'], f['type'])}
        if f.get('max'):
            col['max'] = f['max']
        if f['type'] in ('percent', 'money') or f.get('min') is not None:
            col['min'] = f.get('min', 0)
        if f['type'] == 'ref':
            col['ref'] = f['ref']
        out.append(col)
    return out


# ---- values in and out -------------------------------------------------------------------------------------------
def _decimal(raw):
    try:
        value = Decimal(str(raw).strip().replace(',', '.'))
    except (InvalidOperation, ValueError):
        return None
    return value if value.is_finite() else None


def _clean_one(field, raw, scale, repo):
    """(value for the database, None) or (None, message). An empty value is NULL; 0 is a value."""
    kind = field['type']
    empty = raw is None or (isinstance(raw, str) and raw.strip() == '')
    if empty:
        return None, ('Câmpul este obligatoriu.' if field.get('required') else None)
    if kind == 'text':
        if not isinstance(raw, str):
            return None, 'Text invalid.'
        text = raw.strip()
        if _CONTROL.search(text):
            return None, 'Textul conține caractere nepermise.'
        if len(text) > field['max']:
            return None, 'Cel mult %d caractere.' % field['max']
        return text, None
    if kind == 'int':
        number = _decimal(raw)
        if number is None or number != number.to_integral_value():
            return None, 'Introduceți un număr întreg.'
        if field.get('min') is not None and number < field['min']:
            return None, 'Valoarea trebuie să fie cel puțin %s.' % field['min']
        return int(number), None
    if kind in ('percent', 'money'):
        number = _decimal(raw)
        if number is None:
            return None, 'Introduceți un număr.'
        if number < 0:
            return None, 'Valoarea nu poate fi negativă.'
        try:
            if kind == 'percent':
                if number > 100:
                    return None, 'Cota nu poate depăși 100%.'
                return numeric.vat_to_stored(number / 100), None
            return numeric.to_stored(number, scale), None
        except numeric.NumericError as error:
            return None, str(error)
    if kind == 'ref':
        number = _decimal(raw)
        if number is None or number != number.to_integral_value():
            return None, 'Valoare invalidă.'
        target = spec_of(field['ref'])
        if not repo.select(target['table'], {target['pk']: int(number)}, columns=(target['pk'],)):
            return None, 'Valoarea aleasă nu există în această gestiune.'
        return int(number), None
    raise NomError('SPEC', 'Tip de câmp necunoscut: %s' % kind, 500)


def clean(spec, gestiune_type, values, scale, repo):
    """The columns to write, validated. Raises NomError(INVALID) with a message per field."""
    if not isinstance(values, dict):
        raise NomError('INVALID', 'Cerere invalidă.', 400)
    out, errors = {}, {}
    for field in fields_for(spec, gestiune_type):
        value, message = _clean_one(field, values.get(field['key']), scale, repo)
        if message:
            errors[field['key']] = message
        else:
            out[field['key']] = value
    if errors:
        raise NomError('INVALID', 'Corectați câmpurile marcate.', 422, errors)
    return out


def _plain(number):
    text = format(number, 'f')
    return text.rstrip('0').rstrip('.') if '.' in text else text


def present(spec, gestiune_type, row, scale):
    """One row as the page receives it: decimals as strings with a dot (no float noise)."""
    out = {'id': row[spec['pk']], 'version': row['Version'], 'active': bool(row['Activ'])}
    if spec.get('parent'):
        out[spec['parent'][0]] = row[spec['parent'][0]]
    for field in fields_for(spec, gestiune_type):
        value = row.get(field['key'])
        if value is None:
            out[field['key']] = None
        elif field['type'] == 'percent':
            out[field['key']] = _plain(numeric.from_stored(value, numeric.VAT_SCALE) * 100)
        elif field['type'] == 'money':
            out[field['key']] = format(numeric.from_stored(value, scale), 'f')
        else:
            out[field['key']] = value
    return out


# ---- statements --------------------------------------------------------------------------------------------------
def _sort_key(spec):
    return lambda row: (str(row.get(spec['title']) or '').lower(), row[spec['pk']])


def list_rows(repo, kind, gestiune_type, scale, parent=None):
    spec = spec_of(kind)
    where = {}
    if spec.get('parent'):
        parent_id = _decimal(parent)
        if parent_id is None or parent_id != parent_id.to_integral_value():
            raise NomError('PARENT_REQUIRED', 'Alegeți mai întâi produsul.', 400)
        where[spec['parent'][0]] = int(parent_id)
    rows = sorted(repo.select(spec['table'], where), key=_sort_key(spec))
    return [present(spec, gestiune_type, row, scale) for row in rows]


def lookups(repo, kind, gestiune_type):
    """Selector options of the `ref` fields of this kind: {field: [{id, label}]} -- active rows only."""
    out = {}
    for field in fields_for(spec_of(kind), gestiune_type):
        if field['type'] == 'ref':
            target = spec_of(field['ref'])
            rows = sorted(repo.select(target['table'], {'Activ': 1}), key=_sort_key(target))
            out[field['key']] = [{'id': r[target['pk']], 'label': r[target['title']] or ''} for r in rows]
    return out


def _audit_value(values):
    return json.dumps({k: ('***' if k in _MASKED_IN_AUDIT and v is not None else v) for k, v in values.items()},
                      ensure_ascii=False, default=str, sort_keys=True)


def audit(cursor, ctx, operation, table, row_key, before, after, request_key, request_id):
    cursor.execute(
        'INSERT INTO VC_Audit (SubunitId, GestiuneId, UserEmail, Operation, TableName, RowKey, BeforeValue, AfterValue, RequestKey, RequestId) '
        'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
        (ctx['subunit'], ctx['gestiune'], ctx['email'], operation, table, str(row_key),
         None if before is None else _audit_value(before), None if after is None else _audit_value(after), request_key, request_id))


def _freeze_scale(cursor, ctx):
    """The first stored price fixes the scale (and with it the type) of the gestiune: values are direct DECIMAL(20,4)."""
    cursor.execute('UPDATE VC_Gestiuni SET Populated=1 WHERE SubunitId=%s AND GestiuneId=%s AND Populated=0',
                   (ctx['subunit'], ctx['gestiune']))


def _fetch(repo, spec, row_id):
    rows = repo.select(spec['table'], {spec['pk']: row_id})
    if not rows:
        raise NomError('NOT_FOUND', 'Rândul nu mai există.', 404)
    return rows[0]


def _stamped(spec, values):
    return {**values, 'DataSalvare': datetime.now()} if spec['stamp'] else values


def _has_money(spec):
    return any(f['type'] == 'money' for f in spec['fields'])


def _fingerprint(kind, parent, values):
    return hashlib.sha256(json.dumps([kind, parent, values], sort_keys=True, ensure_ascii=False, default=str).encode('utf-8')).hexdigest()


def _allow_nom_write(kind):
    if kind == 'variante':
        raise NomError('PRICES_FROM_NIR_ONLY', 'Prețurile se adaugă și se modifică numai din NIR.', 403)


def create(repo, cursor, ctx, kind, gestiune_type, scale, values, parent=None, request_key=None, request_id=None):
    """Insert one row. ctx = {subunit, gestiune, email}. Returns (row as presented, replayed)."""
    _allow_nom_write(kind)
    spec = spec_of(kind)
    fingerprint = _fingerprint(kind, parent, values)
    if request_key:
        cursor.execute('SELECT Fingerprint, Result FROM VC_Operations WHERE SubunitId=%s AND GestiuneId=%s AND RequestKey=%s FOR UPDATE',
                       (ctx['subunit'], ctx['gestiune'], request_key))
        seen = cursor.fetchone()
        if seen:
            if seen['Fingerprint'] != fingerprint:
                raise NomError('KEY_REUSED', 'Cheia cererii a fost folosită pentru altă cerere.', 409)
            return json.loads(seen['Result']), True
    cleaned = clean(spec, gestiune_type, values, scale, repo)
    if kind == 'produse' and cleaned.get('CPV'):
        from .cpv_catalog import validate
        validate(cursor, cleaned['CPV'])
    if spec.get('parent'):
        column, parent_kind = spec['parent']
        parent_spec = spec_of(parent_kind)
        parent_id = _decimal(parent)
        if parent_id is None or not repo.select(parent_spec['table'], {parent_spec['pk']: int(parent_id)}, columns=(parent_spec['pk'],)):
            raise NomError('PARENT_NOT_FOUND', 'Produsul nu există în această gestiune.', 404)
        cleaned[column] = int(parent_id)
    new_id = repo.insert(spec['table'], _stamped(spec, cleaned))
    if _has_money(spec):
        _freeze_scale(cursor, ctx)
    result = present(spec, gestiune_type, _fetch(repo, spec, new_id), scale)
    audit(cursor, ctx, 'nom.create', spec['table'], new_id, None, cleaned, request_key, request_id)
    if request_key:
        cursor.execute('INSERT INTO VC_Operations (SubunitId, GestiuneId, RequestKey, Fingerprint, Result) VALUES (%s,%s,%s,%s,%s)',
                       (ctx['subunit'], ctx['gestiune'], request_key, fingerprint, json.dumps(result, ensure_ascii=False, default=str)))
    return result, False


def _conflict(repo, spec, row_id):
    current = _fetch(repo, spec, row_id)   # a vanished row answers NOT_FOUND instead
    raise NomError('CONFLICT', 'Rândul a fost modificat de altcineva. Reîncărcați lista.', 409, {'version': current['Version']})


def _version(raw):
    number = _decimal(raw)
    if number is None or number != number.to_integral_value() or number < 1:
        raise NomError('INVALID', 'Lipsește revizia rândului. Reîncărcați lista.', 400)
    return int(number)


def update(repo, cursor, ctx, kind, gestiune_type, scale, row_id, values, base_version, request_id=None):
    _allow_nom_write(kind)
    if kind == 'produse':
        # Serialize account changes with document writes in this gestiune, including the first movement.
        cursor.execute('SELECT GestiuneId FROM VC_Gestiuni WHERE SubunitId=%s AND GestiuneId=%s FOR UPDATE',
                       (ctx['subunit'], ctx['gestiune']))
        cursor.fetchone()
    spec = spec_of(kind)
    version = _version(base_version)
    before = _fetch(repo, spec, row_id)
    if before['Version'] != version:
        _conflict(repo, spec, row_id)
    cleaned = clean(spec, gestiune_type, values, scale, repo)
    if kind == 'produse' and cleaned.get('IdMasina') != before.get('IdMasina'):
        from .nomenclator_usage import blocked_ids
        if row_id in blocked_ids(cursor, ctx['subunit'], ctx['gestiune'], 'produse'):
            raise NomError('PRODUCT_ACCOUNT_LOCKED', 'Contul unui produs cu mișcări nu poate fi modificat.', 409,
                           {'IdMasina': 'Produsul are mișcări; contul rămâne cel original.'})
    if kind == 'produse' and cleaned.get('CPV') and cleaned['CPV'] != before.get('CPV'):
        from .cpv_catalog import validate
        validate(cursor, cleaned['CPV'])
    if repo.update(spec['table'], _stamped(spec, cleaned), {spec['pk']: row_id, 'Version': version}, bump_version=True) != 1:
        _conflict(repo, spec, row_id)
    if _has_money(spec):
        _freeze_scale(cursor, ctx)
    after = _fetch(repo, spec, row_id)
    audit(cursor, ctx, 'nom.update', spec['table'], row_id,
           {k: before.get(k) for k in cleaned}, cleaned, None, request_id)
    return present(spec, gestiune_type, after, scale)


def set_active(repo, cursor, ctx, kind, gestiune_type, scale, row_id, active, base_version, request_id=None):
    """Switch a row on/off. Nothing is deleted: documents that point at it keep their history."""
    _allow_nom_write(kind)
    spec = spec_of(kind)
    version = _version(base_version)
    before = _fetch(repo, spec, row_id)
    if before['Version'] != version:
        _conflict(repo, spec, row_id)
    flag = 1 if active else 0
    if not flag:
        from .nomenclator_usage import require_deactivation
        require_deactivation(cursor, ctx, kind, row_id)
    if repo.update(spec['table'], {'Activ': flag}, {spec['pk']: row_id, 'Version': version}, bump_version=True) != 1:
        _conflict(repo, spec, row_id)
    audit(cursor, ctx, 'nom.activate' if flag else 'nom.deactivate', spec['table'], row_id,
           {'Activ': int(before['Activ'])}, {'Activ': flag}, None, request_id)
    return present(spec, gestiune_type, _fetch(repo, spec, row_id), scale)
