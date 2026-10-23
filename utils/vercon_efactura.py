"""Shared read model for VERCON and the accountant. No writes, schema creation or connections."""
import hashlib
import re
import unicodedata
from decimal import Decimal
from utils.vercon_suppliers import code as fiscal_code, sql as fiscal_sql


def rows(cursor, sql, args=()):
    cursor.execute(sql, args)
    return cursor.fetchall()


def ready(cursor):
    found = rows(cursor, "SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() "
                 "AND TABLE_NAME IN ('EF_VERCON_ASOCIERI','EF_VERCON_ALOCARI','EF_VERCON_CORELARI')")
    return len(found) == 3


def name_hash(value):
    name = ' '.join(unicodedata.normalize('NFKC', value or '').casefold().split())
    return hashlib.sha256(name.encode('utf-8')).hexdigest()


def cpv(value):
    # Accept the catalogue's 8 digits and UBL's optional check digit, never a CPV prefix.
    match = re.fullmatch(r'(\d{8})(?:-?\d)?', str(value or '').strip())
    return match[1] if match else None


def receipt_supplier_sql(cursor, nir='n'):
    """CodPartener is the fiscal code, never an internal partner code or ID."""
    return fiscal_sql(nir + '.CodPartener')


def allocations(cursor, sending_id=None, receipt_scope=None):
    filters, args = [], []
    if sending_id is not None:
        filters.append('a.ID_TRIMITERE=%s')
        args.append(sending_id)
    if receipt_scope is not None:
        filters.append('a.SUBUNIT_ID=%s AND a.GESTIUNE_ID=%s AND a.EXERCITIU_ID=%s')
        args.extend(receipt_scope)
    supplier = receipt_supplier_sql(cursor)
    return rows(cursor,
        'SELECT a.*, p.Denumire AS NUME_EFACTURA, '
        '(n.IdIntrare IS NOT NULL AND COALESCE(n.Anulat,0)=0 AND COALESCE(n.Storno,0)=0 '
        "AND n.TipDoc<>'STOC_INITIAL' AND i.ID IS NOT NULL AND i.IdProdus=a.PRODUS_PRET_ID "
        'AND n.Version=a.NIR_VERSION AND i.Cant=a.CANT_SURSA AND i.PUTVA=a.PRET_SURSA '
        'AND DATE(n.DataFc)=DATE(f.DataFact) '
        + "AND NULLIF(" + supplier + ",'')=NULLIF(" + fiscal_sql('f.CuiNormalizat') + ",'') "
        "AND t.Stare IN ('TRIMISA','PRELUATA')) AS VALID "
        'FROM EF_VERCON_ALOCARI a JOIN EF_PrimiteVercon t ON t.IdTrimitere=a.ID_TRIMITERE '
        'JOIN EF_Primite f ON f.IdPrimita=t.IdPrimita JOIN EF_PrimiteLinii p ON p.IdLinie=a.ID_LINIE '
        'LEFT JOIN VC_NIR n ON n.SubunitId=a.SUBUNIT_ID AND n.GestiuneId=a.GESTIUNE_ID '
        'AND n.ExercitiuId=a.EXERCITIU_ID AND n.IdIntrare=a.NIR_ID '
        'LEFT JOIN VC_Intrari i ON i.SubunitId=a.SUBUNIT_ID AND i.GestiuneId=a.GESTIUNE_ID '
        'AND i.ExercitiuId=a.EXERCITIU_ID AND i.IdIntrare=a.NIR_ID AND i.ID=a.INTRARE_ID '
        + ('WHERE ' + ' AND '.join(filters) if filters else ''), tuple(args))


def progress(cursor, sending_id):
    """None = legacy integral association. Otherwise quantity budgets, including cancelled/edited NIRs."""
    if not ready(cursor) or not rows(cursor, 'SELECT 1 FROM EF_VERCON_ASOCIERI WHERE ID_TRIMITERE=%s', (sending_id,)):
        return None
    lines = rows(cursor, 'SELECT l.IdLinie,l.Cant FROM EF_PrimiteLinii l JOIN EF_PrimiteVercon t ON t.IdPrimita=l.IdPrimita '
                 'WHERE t.IdTrimitere=%s', (sending_id,))
    used = {}
    links = allocations(cursor, sending_id)
    for link in links:
        if link['VALID']:
            used[link['ID_LINIE']] = used.get(link['ID_LINIE'], Decimal(0)) + link['CANT_FACTURA']
    complete = bool(lines) and all(l['Cant'] is not None and l['Cant'] > 0 and used.get(l['IdLinie'], 0) == l['Cant'] for l in lines)
    return {'used': used, 'links': links, 'state': 'PRELUATA' if complete else 'PARTIALA' if used else 'TRIMISA'}


def progress_states(cursor):
    """Batch projection for the queue/login notice, without per-invoice schema and budget queries."""
    if not ready(cursor):
        return {}
    source = rows(cursor, 'SELECT a.ID_TRIMITERE,l.IdLinie,l.Cant FROM EF_VERCON_ASOCIERI a '
                  'JOIN EF_PrimiteVercon t ON t.IdTrimitere=a.ID_TRIMITERE '
                  'LEFT JOIN EF_PrimiteLinii l ON l.IdPrimita=t.IdPrimita')
    if not source:
        return {}
    lines, used = {}, {}
    for row in source:
        items = lines.setdefault(row['ID_TRIMITERE'], [])
        if row['IdLinie'] is not None:
            items.append(row)
    for link in allocations(cursor):
        if link['VALID']:
            amounts = used.setdefault(link['ID_TRIMITERE'], {})
            amounts[link['ID_LINIE']] = amounts.get(link['ID_LINIE'], Decimal(0)) + link['CANT_FACTURA']
    states = {}
    for sending_id, items in lines.items():
        amounts = used.get(sending_id, {})
        complete = bool(items) and all(item['Cant'] is not None and item['Cant'] > 0
                                      and amounts.get(item['IdLinie'], 0) == item['Cant'] for item in items)
        states[sending_id] = 'PRELUATA' if complete else 'PARTIALA' if amounts else 'TRIMISA'
    return states
