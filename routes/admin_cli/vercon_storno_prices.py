"""SLICE-VC09-07/09: inspect/repair rounded storno and return prices in ONE unit database.

Repairs VC_IntrariStorno/VC_IesiriRetur.PUTVA, with line/document revisions and audit. No VAT
multiplication, header amount recalculation, schema changes or variant writes.
"""
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_EVEN, localcontext

from routes.admin_cli.admin_common import COMMON_DB, AdminError, _actor, _exec, _journal, _rows
from utils.vercon_suppliers import name_key
from routes.vercon.bon_number import parse as bon_number

SUMMARY_TITLE = 'Preturi storno si retur VERCON - sumar'
REPAIR_COLUMN = 'reparabile'
FAMILIES = {
    'storno': {'label': 'Storno', 'source': 'IntrariStorno', 'table': 'VC_IntrariStorno',
               'header': 'VC_NIR', 'key': 'IdIntrare', 'number': 'Numar', 'day': 'Data',
               'flag': 'Storno', 'cancelled': 's.Anulat'},
    'retur': {'label': 'Retur', 'source': 'IesiriRetur', 'table': 'VC_IesiriRetur',
              'header': 'VC_Iesiri_H', 'key': 'IdBonConsum', 'number': 'NumarBC', 'day': 'DataBC',
              'flag': 'Retur', 'cancelled': '0'},
}
REQUIRED = {
    'VC_IntrariStorno': 'ID SubunitId GestiuneId ExercitiuId IdIntrare IdProdus Cant PUTVA Anulat Version',
    'VC_IesiriRetur': 'ID SubunitId GestiuneId ExercitiuId IdBonConsum IdProdus Cant PUTVA Version',
    'VC_ProduseP': 'SubunitId GestiuneId IdProdus ID PUTVA',
    'VC_Produse': 'SubunitId GestiuneId ID DenumireP',
    'VC_NIR': 'SubunitId GestiuneId ExercitiuId IdIntrare Numar Data Storno Anulat Version',
    'VC_Iesiri_H': 'SubunitId GestiuneId ExercitiuId IdBonConsum NumarBC DataBC Retur Anulat Version',
    'VC_Subunitati': 'SubunitId Name',
    'VC_Gestiuni': 'SubunitId GestiuneId Name',
}
PROVENANCE = {
    'VC_IdMap': 'SourceId TableName Kind TargetId',
    'VC_ImportSources': 'SourceId SubunitId GestiuneId',
}
AUDIT = {'VC_Audit': 'SubunitId GestiuneId ExercitiuId UserEmail Operation TableName RowKey '
                     'BeforeValue AfterValue RequestKey RequestId'}


def _schema(conn, db):
    columns = defaultdict(set)
    for row in _rows(conn, 'SELECT TABLE_NAME AS t,COLUMN_NAME AS c FROM information_schema.COLUMNS '
                          'WHERE TABLE_SCHEMA=%s', (db,)):
        columns[row['t']].add(row['c'])
    return columns


def _missing(columns, required):
    return [table + '.' + col for table, fields in required.items()
            for col in fields.split() if col not in columns.get(table, set())]


def _load(conn, columns):
    rows = []
    # All SQL identifiers below come exclusively from the two fixed family specs.
    for family, spec in FAMILIES.items():
        # EXISTS cannot multiply a line even if several source maps mention it.
        imported = ("EXISTS(SELECT 1 FROM VC_IdMap m JOIN VC_ImportSources src ON src.SourceId=m.SourceId "
                    "WHERE m.TableName='{source}' AND m.Kind='ROW' AND m.TargetId=s.ID "
                    "AND src.SubunitId=s.SubunitId AND src.GestiuneId=s.GestiuneId)".format(**spec)
                    if not _missing(columns, PROVENANCE) else '0')
        rows.extend(_rows(conn, (
            "SELECT '{family}' AS family,s.ID AS line,s.SubunitId AS sub,s.GestiuneId AS gest,s.ExercitiuId AS exercise, "
            's.{key} AS document,s.IdProdus AS variant,s.Cant AS quantity,s.PUTVA AS old_price, '
            's.Version AS version,{cancelled} AS cancelled,pp.PUTVA AS new_price,pp.IdProdus AS found_variant, '
            'pr.DenumireP AS product,u.Name AS sub_name,g.Name AS gest_name,n.{key} AS found_document, '
            'n.{number} AS number,n.{day} AS day,n.{flag} AS correction_flag,n.Anulat AS doc_cancelled,n.Version AS doc_version, '
            'g.GestiuneId AS found_gest,{imported} AS imported FROM {table} s '
            'LEFT JOIN VC_ProduseP pp ON pp.SubunitId=s.SubunitId AND pp.GestiuneId=s.GestiuneId AND pp.IdProdus=s.IdProdus '
            'LEFT JOIN VC_Produse pr ON pr.SubunitId=pp.SubunitId AND pr.GestiuneId=pp.GestiuneId AND pr.ID=pp.ID '
            'LEFT JOIN {header} n ON n.SubunitId=s.SubunitId AND n.GestiuneId=s.GestiuneId '
            'AND n.ExercitiuId=s.ExercitiuId AND n.{key}=s.{key} '
            'LEFT JOIN VC_Subunitati u ON u.SubunitId=s.SubunitId '
            'LEFT JOIN VC_Gestiuni g ON g.SubunitId=s.SubunitId AND g.GestiuneId=s.GestiuneId '
            'WHERE s.PUTVA IS NULL OR pp.PUTVA IS NULL OR s.PUTVA<>pp.PUTVA'
        ).format(family=family, imported=imported, **spec)))
    missing_audit = _missing(columns, AUDIT)
    for row in rows:
        row['repair_problem'] = 'Lipsesc coloanele de audit: ' + ', '.join(missing_audit) + '.' if missing_audit else ''
    return rows


def _reason(row):
    if row['found_variant'] is None or row['new_price'] is None:
        return 'Varianta sau pretul ei lipseste.'
    if row['old_price'] is None:
        return 'Pretul documentului lipseste; nu este cazul cunoscut de rotunjire.'
    old, new = Decimal(row['old_price']), Decimal(row['new_price'])
    if old != old.to_integral_value() or old != new.to_integral_value(rounding=ROUND_HALF_EVEN):
        return 'Diferenta nu corespunde rotunjirii Access la intreg.'
    if row['found_document'] is None or row['correction_flag'] != 1:
        return 'Lipseste documentul parinte bifat ' + FAMILIES[row['family']]['label'] + '.'
    if row['found_gest'] is None or row['doc_version'] is None:
        return 'Gestiunea sau versiunea documentului lipseste.'
    if row['cancelled'] != 0 or row['doc_cancelled'] != 0:
        return 'Linia sau documentul este anulat ori nu are starea stabilita.'
    if row['quantity'] is None:
        return 'Cantitatea lipseste.'
    if not row['imported']:
        return 'Linia nu are provenienta confirmata din migrator.'
    if row['version'] != 1:
        return 'Linia a fost modificata dupa import; cere analiza.'
    return row['repair_problem']


def _order(row):
    # Return numbers are composite labels (BX 2, BX 10), with an explicit numeric key.
    number = bon_number('br', row['number']) if row['family'] == 'retur' else row['number']
    return (name_key(row['sub_name']), name_key(row['gest_name']), name_key(row['product']),
            row['new_price'] is None, row['new_price'] or Decimal(0), row['day'] is None, row['day'],
            name_key(FAMILIES[row['family']]['label']), number is None, Decimal(str(number or 0)),
            name_key(row['number']) if number is None else ())


def _text(value):
    return '' if value is None else format(Decimal(value), 'f')


def _blocks(db, rows, block):
    groups = {}
    for row in rows:
        key = row['sub'], row['gest'], row['family']
        summary = groups.setdefault(key, {'sub': row['sub_name'], 'gest': row['gest_name'],
                                         'family': FAMILIES[row['family']]['label'],
                                         'count': 0, 'repairable': 0, 'effect': Decimal(0)})
        summary['count'] += 1
        if not _reason(row):
            summary['repairable'] += 1
            with localcontext() as ctx:
                ctx.prec = 60
                summary['effect'] += Decimal(row['quantity']) * (Decimal(row['new_price']) - Decimal(row['old_price']))
    summary_rows = sorted(groups.values(), key=lambda row: (name_key(row['sub']), name_key(row['gest']), name_key(row['family'])))
    blocks = [block(db, SUMMARY_TITLE,
        ['subunitate', 'gestiune', 'tip', 'diferente', REPAIR_COLUMN, 'de analizat', 'diferenta valoare reparabile'],
        [[r['sub'], r['gest'], r['family'], r['count'], r['repairable'], r['count'] - r['repairable'], _text(r['effect'])]
         for r in summary_rows],
        'Verificare pe intreaga baza, toate gestiunile si exercitiile. '
        'Lista goala = niciun pret diferit de varianta sau lipsa. '
        'Diferenta valorica este Cant x (pret propus - pret actual), inainte de rotunjirea la afisare. '
        'Repararea schimba pretul liniilor eligibile si reviziile liniilor/documentelor; '
        'nu schimba cantitati sau valori din antete.')]
    if rows:
        headers = ['subunitate', 'gestiune', 'tip', 'produs', 'exercitiu', 'numar document', 'data', 'IdProdus', 'ID linie',
                   'cantitate', 'pret actual', 'pret propus', 'versiune', 'rezultat']
        blocks.append(block(db, 'Preturi storno si retur VERCON - detalii',
            headers, [headers] + [[r['sub_name'], r['gest_name'], FAMILIES[r['family']]['label'], r['product'], r['exercise'], r['number'], r['day'], r['variant'], r['line'],
              _text(r['quantity']), _text(r['old_price']), _text(r['new_price']), r['version'],
              _reason(r) or 'Reparabil: pret rotunjit la intreg in Access.'] for r in sorted(rows, key=_order)],
            'Toate diferentele sunt listate. IdProdus este identificat impreuna cu subunitatea si gestiunea. '
            'Cazurile de analizat raman neschimbate.', raw=True))
    return blocks


def check(conn, db, block):
    columns = _schema(conn, db)
    missing = _missing(columns, REQUIRED)
    if missing:
        return [block(db, SUMMARY_TITLE, ['rezultat'],
                      [['Verificarea nu poate rula: lipsesc ' + ', '.join(missing) + '.']],
                      'Nu se creeaza si nu se modifica structura bazei.')]
    return _blocks(db, _load(conn, columns), block)


def repair(conn, db, block):
    """Fresh scan under the same gestiune locks used by document writes; all or nothing."""
    from routes.vercon.protocol import audit

    columns = _schema(conn, db)
    missing = _missing(columns, {**REQUIRED, **PROVENANCE, **AUDIT})
    if missing:
        raise AdminError('Repararea nu poate rula: lipsesc ' + ', '.join(missing) + '.')
    target_tables = ('VC_IntrariStorno', 'VC_IesiriRetur', 'VC_NIR', 'VC_Iesiri_H', 'VC_Audit')
    engines = {(r['db'], r['table']): str(r['engine'] or '').upper() for r in _rows(conn,
        "SELECT TABLE_SCHEMA AS db,TABLE_NAME AS `table`,ENGINE AS engine FROM information_schema.TABLES "
        "WHERE (TABLE_SCHEMA=%s AND TABLE_NAME IN ('VC_IntrariStorno','VC_IesiriRetur','VC_NIR','VC_Iesiri_H','VC_Audit')) "
        "OR (TABLE_SCHEMA=%s AND TABLE_NAME='Jurnal')", (db, COMMON_DB))}
    targets = [(db, table) for table in target_tables] + [(COMMON_DB, 'Jurnal')]
    if any(engines.get(target) != 'INNODB' for target in targets):
        raise AdminError('Repararea cere tabele InnoDB pentru linii, documente, audit si Jurnal, '
                         'pentru aplicarea tuturor corectiilor in aceeasi tranzactie. Structura nu se modifica automat.')
    changed = []
    documents = {}
    conn.start_transaction(isolation_level='READ COMMITTED')
    try:
        # Internal key order is for lock acquisition only, never presentation.
        _rows(conn, 'SELECT SubunitId,GestiuneId FROM VC_Gestiuni ORDER BY SubunitId,GestiuneId FOR UPDATE')
        for row in sorted(_load(conn, columns), key=_order):
            if _reason(row):
                continue
            family = row['family']
            spec = FAMILIES[family]
            count = _exec(conn, (
                'UPDATE {table} s JOIN VC_ProduseP pp '
                'ON pp.SubunitId=s.SubunitId AND pp.GestiuneId=s.GestiuneId AND pp.IdProdus=s.IdProdus '
                'JOIN {header} n ON n.SubunitId=s.SubunitId AND n.GestiuneId=s.GestiuneId '
                'AND n.ExercitiuId=s.ExercitiuId AND n.{key}=s.{key} '
                'SET s.PUTVA=%s,s.Version=s.Version+1 '
                'WHERE s.SubunitId=%s AND s.GestiuneId=%s AND s.ExercitiuId=%s AND s.ID=%s '
                'AND s.{key}=%s AND s.IdProdus=%s AND s.Version=1 AND s.PUTVA=%s AND s.Cant=%s '
                'AND pp.PUTVA=%s AND {cancelled}=0 AND n.Anulat=0 AND n.{flag}=1 AND n.Version=%s'
                ).format(**spec),
                (row['new_price'], row['sub'], row['gest'], row['exercise'], row['line'],
                 row['document'], row['variant'], row['old_price'], row['quantity'], row['new_price'], row['doc_version']))
            if count != 1:
                raise AdminError('Datele s-au schimbat in timpul repararii. Nu s-a aplicat nicio corectie; reluati verificarea.')
            ctx = {'subunit': row['sub'], 'gestiune': row['gest'], 'exercitiu': row['exercise'], 'email': _actor()}
            audit_cursor = conn.cursor()
            try:
                audit(audit_cursor, ctx, 'admin.' + family + '.price.repair', spec['table'], row['line'],
                      {'PUTVA': row['old_price'], 'Version': row['version']},
                      {'PUTVA': row['new_price'], 'Version': row['version'] + 1})
            finally:
                audit_cursor.close()
            changed.append(row)
            documents[(family, row['sub'], row['gest'], row['exercise'], row['document'])] = row['doc_version']
        # Include the family: a NIR and a return bon can share the same internal document number.
        for (family, sub, gest, exercise, document), version in sorted(documents.items()):
            spec = FAMILIES[family]
            count = _exec(conn, ('UPDATE {header} SET Version=Version+1 WHERE SubunitId=%s AND GestiuneId=%s '
                                'AND ExercitiuId=%s AND {key}=%s AND Version=%s AND Anulat=0 AND {flag}=1').format(**spec),
                          (sub, gest, exercise, document, version))
            if count != 1:
                raise AdminError('Documentul s-a schimbat. Nu s-a aplicat nicio corectie; reluati verificarea.')
            audit_cursor = conn.cursor()
            try:
                audit(audit_cursor, {'subunit': sub, 'gestiune': gest, 'exercitiu': exercise, 'email': _actor()},
                      'admin.' + family + '.price.repair.document', spec['header'], document,
                      {'Version': version}, {'Version': version + 1})
            finally:
                audit_cursor.close()
        if changed:
            _journal(conn, 'storno_preturi_repara', db, 'VC_IntrariStorno / VC_IesiriRetur',
                     '%d linii storno/retur: zecimale pret cu TVA recuperate din varianta aceleiasi gestiuni; '
                     'cantitati si valori antete neschimbate; revizii documente actualizate; '
                     'detaliile vechi/noi sunt in VC_Audit.' % len(changed))
        # Build the remaining result while the gestiuni are still locked.
        remaining = _blocks(db, _load(conn, columns), block)
        done = block(db, 'Reparare preturi storno si retur VERCON', ['rezultat'],
                     [['%d linii corectate in baza %s.' % (len(changed), db)]],
                     'Corectiile sunt in VC_Audit si Jurnal. Mai jos sunt diferentele ramase.', raw=True)
        result = [done]
        if changed:
            headers = ['subunitate', 'gestiune', 'tip', 'produs', 'exercitiu', 'numar document', 'data', 'ID linie', 'pret vechi', 'pret nou']
            result.append(block(db, 'Preturi storno si retur VERCON - corectate',
                headers, [headers] + [[r['sub_name'], r['gest_name'], FAMILIES[r['family']]['label'], r['product'], r['exercise'], r['number'], r['day'], r['line'],
                  _text(r['old_price']), _text(r['new_price'])] for r in changed], raw=True))
        result.extend(remaining)
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
