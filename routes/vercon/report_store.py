"""Versioned report configuration on disk; no schema changes or SQL installation.

Defaults ship with the application. Owner changes live in instance/ (excluded
from push), are atomically replaced and are never overwritten by deployment.
"""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from . import report_catalog, report_layouts, report_query, report_validation
from .nomenclatoare import NomError

BASE = Path(__file__).resolve().parents[2]
QUERIES = BASE / 'reports' / 'vercon' / 'queries'
ROOT = Path(os.environ.get('VERCON_REPORT_CONFIG_DIR', str(BASE / 'instance' / 'vercon-reports')))
QUERY_KEYS = ('metadata', 'ledger', 'receipts', 'consumption', 'committee', 'food_header',
              'food_items', 'food_bons', 'food_consumption', 'food_history', 'settings',
              'oi_documents', 'custody_ledger', 'oi_accounts', 'oi_holders')


def _path(unit, part, key):
    if not re.fullmatch(r'[A-Za-z0-9_]{1,64}', str(unit)):
        raise NomError('REPORT_CONFIG', 'Unitate invalidă.', 422)
    if part == 'query' and key not in QUERY_KEYS:
        raise NomError('REPORT_CONFIG', 'Sursă SQL necunoscută.', 422)
    if part == 'layout' and str(key).casefold() not in report_catalog.CATALOG:
        raise NomError('REPORT_CONFIG', 'Machetă necunoscută.', 422)
    if part not in ('query', 'layout', 'unit'):
        raise NomError('REPORT_CONFIG', 'Tip de configurare invalid.', 422)
    # One editable layout for NIR and FCS. Legacy layout-nir_storno.json is left untouched.
    canonical = 'nir' if part == 'layout' and str(key).casefold() == 'nir_storno' else str(key).casefold()
    slug = re.sub(r'[^a-z0-9_-]', '_', canonical) if part != 'unit' else 'identity'
    return ROOT / unit / f'{part}-{slug}.json'


def _default(unit, part, key):
    if part == 'query':
        path = QUERIES / (key + '.sql')
        try:
            return path.read_text(encoding='utf-8')
        except FileNotFoundError:
            raise NomError('REPORT_RESOURCE_MISSING',
                           'Resursele raportului nu sunt instalate complet. '
                           f'Lipsește reports/vercon/queries/{path.name}. '
                           'Contactați administratorul pentru republicarea resurselor.', 503) from None
    if part == 'unit':
        return report_layouts.unit_default(unit)
    return report_layouts.default(report_catalog.CATALOG[str(key).casefold()])


def read(unit, part, key=''):
    path = _path(unit, part, key)
    raw = path.read_bytes() if path.exists() else b''
    data = json.loads(raw)['value'] if raw else _default(unit, part, key)
    version_source = raw or json.dumps(data, ensure_ascii=False, sort_keys=True).encode('utf-8')
    return {'value': data, 'version': hashlib.sha256(version_source).hexdigest(), 'custom': bool(raw)}


def validate(part, value):
    if part == 'query':
        report_query.validate(value)
        return
    if not isinstance(value, dict):
        raise NomError('REPORT_CONFIG', 'Configurarea trebuie să fie un obiect.', 422)
    if len(json.dumps(value)) > 200000:
        raise NomError('REPORT_CONFIG', 'Configurarea este prea mare.', 422)
    report_validation.definition(part, value)


@contextmanager
def _lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_suffix('.lock')
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise NomError('REPORT_BUSY', 'Configurarea se salvează deja. Încercați din nou.', 409) from None
    try:
        os.close(fd)
        yield
    finally:
        lock.unlink(missing_ok=True)


def save(unit, part, key, value, version, actor):
    validate(part, value)
    path = _path(unit, part, key)
    with _lock(path):
        current = read(unit, part, key)
        if current['version'] != version:
            raise NomError('REPORT_CONFLICT', 'Configurarea s-a schimbat. Recitiți înainte de salvare.', 409)
        payload = {'value': deepcopy(value), 'actor': actor, 'previous': current['version']}
        # Every prior version stays recoverable, including the shipped default.
        history = path.parent / 'history'
        history.mkdir(exist_ok=True)
        old = history / (path.stem + '-' + current['version'] + '.json')
        if not old.exists():
            old.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding='utf-8')
        fd, name = tempfile.mkstemp(dir=path.parent, suffix='.tmp')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, path)
        finally:
            if os.path.exists(name):
                os.unlink(name)
    return read(unit, part, key)
