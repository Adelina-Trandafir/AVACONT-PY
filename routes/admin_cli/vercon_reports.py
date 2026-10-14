"""KbotAdmin report definitions over the existing SSH CLI. No schema writes."""
import base64
import json
from routes.vercon import report_catalog, report_store


def unit(args):
    # Resolve with the same authoritative list as every other admin operation.
    from .admin_cli import AdminError, _resolve_db, connect
    conn = connect()
    try:
        names = _resolve_db(conn, args.db)
        if len(names) != 1:
            raise AdminError('Alegeți o singură unitate pentru configurarea rapoartelor.')
        return names[0]
    finally:
        conn.close()


def list_definitions(args):
    name = unit(args)
    reports = report_catalog.catalog('ALIMENTE')
    return {'unit': name, 'reports': reports, 'queries': list(report_store.QUERY_KEYS)}


def get_definition(args):
    return report_store.read(unit(args), args.part, args.key)


def save_definition(args):
    from .admin_cli import _actor, AdminError
    try:
        if len(args.payload) > 400000:
            raise ValueError()
        data = json.loads(base64.b64decode(args.payload, validate=True).decode('utf-8'))
    except (ValueError, UnicodeError):
        raise AdminError('Configurarea trimisă nu este validă.') from None
    return report_store.save(unit(args), args.part, args.key, data, args.version, _actor())


def register(sub):
    p = sub.add_parser('vercon-reports-list')
    p.add_argument('--db', required=True)
    p.set_defaults(fn=list_definitions)
    for verb, fn in (('vercon-reports-get', get_definition), ('vercon-reports-save', save_definition)):
        p = sub.add_parser(verb)
        p.add_argument('--db', required=True)
        p.add_argument('--part', choices=('unit', 'query', 'layout'), required=True)
        p.add_argument('--key', default='')
        if verb.endswith('save'):
            p.add_argument('--version', required=True)
            p.add_argument('--payload', required=True)
        p.set_defaults(fn=fn)
