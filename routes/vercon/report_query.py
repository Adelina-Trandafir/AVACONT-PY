"""Editable SELECT sources. Every table marker becomes a server-scoped relation.

An administrator changes joins/projections/filtering, but cannot remove the
DC/subunit/gestiune/exercise boundary or submit a mutation through reports.
No SQL is executed when an administrator saves a definition.
"""
import re
from utils import vercon_suppliers as suppliers
from .tables import GESTIUNE_TABLES, EXERCITIU_TABLES
from .nomenclatoare import NomError

TABLES = GESTIUNE_TABLES | EXERCITIU_TABLES | {'Parteneri', 'VC_Settings'}
PARAMETERS = {'start', 'end', 'document', 'variant', 'category', 'supplier', 'receiver'}
MARKER = re.compile(r'\{\{TABLE:([A-Za-z_][A-Za-z0-9_]*)\}\}')
PARAM = re.compile(r'%\(([a-z_]+)\)s')
STRINGS = re.compile(r"'(?:''|\\.|[^'\\])*'")
FUNCTIONS = {'SUM', 'MIN', 'MAX', 'COUNT', 'COALESCE', 'IFNULL', 'NULLIF', 'DATE',
             'YEAR', 'MONTH', 'DAY', 'CONCAT', 'CONCAT_WS', 'ROUND', 'ABS', 'UPPER',
             'LOWER', 'LEFT', 'RIGHT', 'SUBSTRING', 'CAST', 'IF', 'IN', 'EXISTS'}


def validate(sql):
    if not isinstance(sql, str) or not 1 <= len(sql) <= 100000:
        raise NomError('REPORT_QUERY', 'Textul interogării este gol sau prea mare.', 422)
    markers = MARKER.findall(sql)
    if not markers or any(name not in TABLES for name in markers):
        raise NomError('REPORT_QUERY', 'Folosiți numai tabelele permise, prin {{TABLE:Nume}}.', 422)
    if set(PARAM.findall(sql)) - PARAMETERS:
        raise NomError('REPORT_QUERY', 'Interogarea folosește un parametru necunoscut.', 422)
    plain = STRINGS.sub("'literal'", sql)
    if re.search(r'\b(IdPartener|PARTENER_ID)\b', plain, re.I):
        raise NomError('REPORT_QUERY', 'Sursa raportului trebuie să lege furnizorii exclusiv prin cod fiscal.', 422)
    plain = MARKER.sub('REPORT_TABLE', PARAM.sub('0', plain))
    if re.search(r';|--|/\*|#|`|"|\b(INTO|OUTFILE|DUMPFILE|UPDATE|DELETE|INSERT|REPLACE|CALL|SET|WITH|FOR|LOCK|PROCEDURE|USE)\b', plain, re.I):
        raise NomError('REPORT_QUERY', 'Sunt permise numai SELECT-uri fără comentarii sau efecte secundare.', 422)
    tokens = re.findall(r"'literal'|[A-Za-z_][A-Za-z0-9_]*|[(),.]|\S", plain)
    if not tokens or tokens[0].upper() != 'SELECT':
        raise NomError('REPORT_QUERY', 'Interogarea trebuie să înceapă cu SELECT.', 422)
    depth, from_depths = 0, set()
    for index, token in enumerate(tokens):
        upper = token.upper()
        if upper in ('FROM', 'JOIN'):
            following = tokens[index + 1] if index + 1 < len(tokens) else ''
            if following != 'REPORT_TABLE' and not (following == '(' and index + 2 < len(tokens) and tokens[index + 2].upper() == 'SELECT'):
                raise NomError('REPORT_QUERY', 'Fiecare FROM/JOIN trebuie să folosească un marker de tabel sau un SELECT delimitat.', 422)
            from_depths.add(depth)
        elif upper in ('WHERE', 'GROUP', 'ORDER', 'HAVING', 'UNION', 'LIMIT'):
            from_depths.discard(depth)
        elif token == ',' and depth in from_depths:
            raise NomError('REPORT_QUERY', 'Folosiți JOIN explicit, nu tabele separate prin virgulă.', 422)
        elif token == '(':
            previous = tokens[index - 1].upper() if index else ''
            if re.fullmatch(r'[A-Z_][A-Z0-9_]*', previous) and previous not in FUNCTIONS and previous not in ('FROM', 'JOIN', 'AND', 'OR', 'NOT', 'AS', 'SELECT', 'WHERE', 'HAVING', 'WHEN', 'THEN', 'ELSE', 'ON'):
                raise NomError('REPORT_QUERY', 'Funcție SQL nepermisă: ' + previous, 422)
            depth += 1
        elif token == ')':
            from_depths.discard(depth)
            depth -= 1
            if depth < 0:
                raise NomError('REPORT_QUERY', 'Paranteze SQL invalide.', 422)
    if depth:
        raise NomError('REPORT_QUERY', 'Paranteze SQL invalide.', 422)
    return sql


def fiscal_sources(sql):
    """Adapt saved, explicitly aliased NIR/partner sources to the fiscal contract, in memory only."""
    aliases = {}
    plain = STRINGS.sub("''", sql)
    for match in re.finditer(r'\{\{TABLE:(VC_NIR|Parteneri)\}\}\s+(?:AS\s+)?([A-Za-z_][A-Za-z0-9_]*)', plain, re.I):
        aliases[match[2]] = 'CodPartener' if match[1].upper() == 'VC_NIR' else 'CodFiscal'
    # A literal internal ID cannot be reinterpreted as a tax code. Keep such
    # definitions editable, but let validation refuse execution until corrected.
    for alias in aliases:
        field = r'\b' + re.escape(alias) + r'\.IdPartener\b'
        if (re.search(field + r"\s*(?:=|<>|!=|<=|>=|<|>|IN\s*\()\s*(?:\d|')", plain, re.I)
                or re.search(r"(?:\b\d+|'literal')\s*(?:=|<>|!=|<=|>=|<|>)\s*" + field, plain, re.I)):
            return sql
    chunks = re.split('(' + STRINGS.pattern + ')', sql)
    for index in range(0, len(chunks), 2):
        for alias, column in aliases.items():
            chunks[index] = re.sub(r'\b' + re.escape(alias) + r'\.IdPartener\b', alias + '.' + column, chunks[index], flags=re.I)
    return ''.join(chunks)


def execute(cursor, sql, ctx, params, *, result_filters=None):
    sql = fiscal_sources(sql)
    validate(sql)
    if 'VC_NIR' in MARKER.findall(sql):
        from . import supplier_identity
        supplier_identity.require(cursor)
    def scoped(match):
        table = match.group(1)
        if table == 'Parteneri':
            return ('(SELECT p.* FROM ' + suppliers.relation() + ' p WHERE p.CodFiscal IN '
                    '(SELECT n.CodPartener FROM VC_NIR n WHERE n.SubunitId=%(scope_s)s '
                    'AND n.GestiuneId=%(scope_g)s AND n.ExercitiuId=%(scope_e)s))')
        where = 'SubunitId=%(scope_s)s AND GestiuneId=%(scope_g)s'
        if table in EXERCITIU_TABLES:
            where += ' AND ExercitiuId=%(scope_e)s'
        return '(SELECT * FROM ' + table + ' WHERE ' + where + ')'
    values = {**{key: params.get(key) for key in PARAMETERS}, 'scope_s': ctx['subunit'],
              'scope_g': ctx['gestiune'], 'scope_e': ctx['exercitiu']}
    statement = MARKER.sub(scoped, sql)
    if result_filters:
        predicates = []
        for key, value in result_filters.items():
            if key not in ('category', 'product'):
                raise ValueError('Unsupported report selection column')
            if value is None:
                predicates.append(key + ' IS NULL')
            else:
                predicates.append(key + '=%(selection_' + key + ')s')
                values['selection_' + key] = value
        statement = 'SELECT * FROM (' + statement + ') report_selection WHERE ' + ' AND '.join(predicates)
    cursor.execute(statement, values)
    return cursor.fetchall()
