"""Build once; screen and exports share a short-lived, user-scoped snapshot."""
from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal
import hashlib
import json
import secrets
import os
from pathlib import Path
import re
import tempfile
import time
from . import report_body, report_catalog, report_data, report_food, report_store, report_output, report_reception
from .nomenclatoare import NomError


def primitive(value):
    if isinstance(value, Decimal):
        return format(value, 'f')
    if isinstance(value, (date, datetime)):
        return value.isoformat()[:10]
    if isinstance(value, dict):
        return {key: primitive(item) for key, item in value.items()}
    if isinstance(value, list):
        return [primitive(item) for item in value]
    return value


def build(cursor, unit, ctx, name, params, gestiune_name, *, source=None):
    spec = report_catalog.spec_of(name, ctx['type'])
    unsupported = [key for key in ('document', 'variant', 'category', 'supplier', 'receiver')
                   if (params.get(key) is not None or params.get(key + '_ids') is not None) and key not in spec['filters']]
    if unsupported:
        raise NomError('REPORT_FILTER', 'Raportul nu folosește filtrele: ' + ', '.join(unsupported) + '.', 422)
    for field in ('document', 'variant', 'supplier'):
        if spec.get(field + '_required') and params.get(field) is None:
            raise NomError('REPORT_SELECTION', 'Raportul necesită o singură selecție: ' + field + '.', 422)
    if params['start'] > params['end'] or ctx['type'] not in ('OI', 'MF') and (params['start'].year != ctx['an'] or params['end'].year != ctx['an']):
        raise NomError('REPORT_PERIOD', 'Alegeți un interval în exercițiul curent.', 422)
    layout = report_store.read(unit, 'layout', spec['id'])
    identity = report_store.read(unit, 'unit')
    report_store.validate('layout', layout['value'])
    report_store.validate('unit', identity['value'])
    source = source if source is not None else report_data.Source(cursor, unit, ctx, params)
    notes = []
    receptions = []
    if spec['kind'] == 'food':
        blocks = report_food.build(source, spec, layout['value'])
    else:
        rows = report_data.dataset(source, spec)
        if (params.get('document') is not None or params.get('document_ids') is not None) and not rows:
            raise NomError('NOT_FOUND', 'Documentul nu este disponibil în selecția raportului.', 404)
        committees = {row['IdComisie']: row for row in source.rows('committee')} if spec.get('family') in ('nir', 'storno') else {}
        if layout['value'].get('form') == 'nir-classic' and spec.get('family') in ('nir', 'storno'):
            receptions = report_reception.documents(rows, committees)
        blocks = report_body.build(spec, layout['value'], rows, ctx, identity['value'], committees)
        if spec.get('ledger') == 'custody' or spec['kind'] in ('balance', 'product', 'accounts', 'final', 'inventory', 'statement'):
            notes.append('Stoc evaluat la prețul curent al variantei; documentele păstrează valorile proprii.')
        if (params.get('supplier') is not None or params.get('supplier_ids') is not None) and spec['kind'] == 'statement':
            notes.append('Furnizorul selectează variantele recepționate de la acesta. Fișa păstrează toate mișcările variantelor; consumurile nu au o alocare istorică pe furnizori.')
    title = layout['value']['title']
    period = report_body.date(params['start']) + ' — ' + report_body.date(params['end'])
    values = {'UNITATE': identity['value']['name'], 'CUI': identity['value']['fiscal_code'],
              'ADRESA': identity['value']['address'], 'GESTIUNE': gestiune_name,
              'ANTET': identity['value']['header'], 'SUBSOL': identity['value']['footer'],
              'PERIOADA': period, 'RAPORT': title}
    def band(name):
        elements = [{**e, 'text': report_output.tokens(report_output.tokens(e['text'], values), values)}
                    for e in layout['value'].get('elements', []) if e['band'] == name]
        return [{'t': 'positioned', 'elements': elements}] if elements else []
    heading = [{'t': 'title', 'text': title}]
    if spec['id'] != 'ListaAlimente2015':
        heading.append({'t': 'text', 'text': period})
    model = {'title': title, 'report_name': spec['id'], 'layout': layout['value'], 'identity': identity['value'],
             'gestiune_name': gestiune_name, 'period_text': period,
             'filename': spec['id'].replace(' ', '_') + '_' + params['end'].isoformat(),
             'blocks': heading + band('before') + blocks + band('after'),
             'notes': notes, 'context': {key: ctx[key] for key in ('subunit', 'gestiune', 'exercitiu', 'type', 'scale', 'zec')},
             'versions': {'layout': layout['version'], 'unit': identity['version'], 'queries': source.versions},
             'generated_at': datetime.now().isoformat(timespec='seconds')}
    if receptions:
        model['receptions'] = receptions
    model = primitive(model)
    model['data_hash'] = hashlib.sha256(json.dumps(model, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()
    return model


class Snapshots:
    """Shared by server workers; exports accept only an owner-bound saved model."""

    def folder(self, owner):
        digest = hashlib.sha256(json.dumps(owner).encode()).hexdigest()
        return report_store.ROOT / 'snapshots' / digest

    def put(self, owner, model):
        token, now = secrets.token_urlsafe(24), time.time()
        folder = self.folder(owner)
        folder.mkdir(parents=True, exist_ok=True)
        for path in folder.glob('*.json'):
            try:
                if path.stat().st_mtime < now - 600:
                    path.unlink(missing_ok=True)
            except FileNotFoundError:
                pass
        files = sorted(folder.glob('*.json'), key=lambda path: path.name)
        if len(files) >= 40:
            files[0].unlink(missing_ok=True)
        fd, temporary = tempfile.mkstemp(dir=folder, suffix='.tmp')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump({'expires': now + 600, 'owner': owner, 'model': model}, stream, ensure_ascii=False)
            os.replace(temporary, folder / (token + '.json'))
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return token

    def get(self, owner, token):
        if not re.fullmatch(r'[A-Za-z0-9_-]{32}', token):
            raise NomError('REPORT_EXPIRED', 'Previzualizarea a expirat. Generați din nou raportul.', 409)
        path = self.folder(owner) / (token + '.json')
        try:
            row = json.loads(path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            row = None
        if not row or row['expires'] <= time.time() or tuple(row['owner']) != owner:
            raise NomError('REPORT_EXPIRED', 'Previzualizarea a expirat. Generați din nou raportul.', 409)
        return row['model']
