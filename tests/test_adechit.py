import json
import sqlite3
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from ADECHIT.tools.compare_phases import compare
from ADECHIT.tools.preview import create_app


def client(tmp_path):
    app = create_app(tmp_path)
    app.testing = True
    return app.test_client(), tmp_path / 'preview.sqlite'


def headers(key=False):
    result = {'X-Ade-Unit': 'preview'}
    if key:
        result['Idempotency-Key'] = str(uuid.uuid4())
    return result


def post(http, path, body, key=None):
    h = headers()
    h['Idempotency-Key'] = key or str(uuid.uuid4())
    return http.post(path, json=body, headers=h)


def test_hidden_groups_filter_confirmation_conflict_and_unhide(tmp_path):
    http, database = client(tmp_path)
    group = http.get('/api/adechit/catalog-data', headers=headers()).json['groups'][0]
    body = {'id': group['IDG'], 'version': group['Version'], 'hidden': True}
    refused = post(http, '/api/adechit/group-hidden', body)
    assert refused.status_code == 409
    assert refused.json['reason'] == 'GROUP_ACTIVE_CHILDREN'
    assert len(http.get('/api/adechit/catalog-data', headers=headers()).json['groups']) == 1
    key = str(uuid.uuid4())
    saved = post(http, '/api/adechit/group-hidden', {**body, 'confirm_active': True}, key)
    assert saved.status_code == 200 and saved.json['Ascunsa'] == 1
    assert post(http, '/api/adechit/group-hidden', {**body, 'confirm_active': True}, key).json == saved.json
    assert http.get('/api/adechit/catalog-data', headers=headers()).json['groups'] == []
    all_groups = http.get('/api/adechit/catalog-data?include_hidden=1', headers=headers()).json['groups']
    assert len(all_groups) == 1 and all_groups[0]['Ascunsa'] == 1
    stale = post(http, '/api/adechit/group-hidden', {**body, 'hidden': False})
    assert stale.status_code == 409 and stale.json['reason'] == 'CONFLICT'
    unhidden = post(http, '/api/adechit/group-hidden', {**body, 'version': saved.json['Version'], 'hidden': False})
    assert unhidden.status_code == 200 and unhidden.json['Ascunsa'] == 0
    invalid = post(http, '/api/adechit/group-hidden', {**body, 'hidden': 'true'})
    assert invalid.status_code == 400 and invalid.json['reason'] == 'BOOLEAN'
    connection = sqlite3.connect(database)
    connection.execute('UPDATE AD_Platitori SET Plecat=1 WHERE IDG=?', (group['IDG'],))
    connection.commit()
    connection.close()
    no_active = post(http, '/api/adechit/group-hidden', {**body, 'version': unhidden.json['Version']})
    assert no_active.status_code == 200


def test_hide_group_respects_catalog_rights_and_subunit_scope(tmp_path):
    app = create_app(tmp_path, second_subunit=True)
    app.testing = True
    http = app.test_client()
    h = {**headers(True), 'X-Ade-Subunit': '1'}
    foreign = http.post('/api/adechit/group-hidden', headers=h,
                        json={'id': 101, 'version': 1, 'hidden': True, 'confirm_active': True})
    assert foreign.status_code == 404
    app.config['ADE_TEST_RIGHTS'].discard('catalog')
    denied = http.post('/api/adechit/group-hidden', headers=h,
                       json={'id': 1, 'version': 1, 'hidden': True, 'confirm_active': True})
    assert denied.status_code == 403


def test_main_groups_are_scoped_to_month_attendance_and_hidden_filter(tmp_path):
    http, database = client(tmp_path)
    connection = sqlite3.connect(database)
    connection.executescript('''
        INSERT INTO AD_Grupe (IDG,Grupa,Ascunsa) VALUES (2,'Hidden group',1),(3,'Empty group',0);
        INSERT INTO AD_LunaD (IDL,Anul,Luna) VALUES (2,2026,11),(3,2026,12);
        UPDATE AD_Platitori SET IDG=2 WHERE IDP=1;
        INSERT INTO AD_Prezenta (IDZ,IDP,IDL,IDG,ZilePrezenta) VALUES (2,1,2,2,0);
    ''')
    connection.commit(); connection.close()
    def groups(query):
        response = http.get('/api/adechit/catalog-data' + query, headers=headers())
        assert response.status_code == 200
        return [group['IDG'] for group in response.json['groups']]
    # The old month keeps the child's historical group, despite their current transfer.
    assert groups('?IDL=1') == [1]
    assert groups('?IDL=2') == []
    assert groups('?IDL=2&include_hidden=1') == [2]
    assert groups('?IDL=3&include_hidden=1') == []
    # The Plătitori catalog continues to include groups with no monthly attendance.
    assert groups('?include_hidden=1') == [1, 2, 3]
    invalid = http.get('/api/adechit/catalog-data?IDL=bad', headers=headers())
    assert invalid.status_code == 400
    missing = http.get('/api/adechit/catalog-data?IDL=999', headers=headers())
    assert missing.status_code == 404


def test_legacy_payment_documents_use_payment_month_and_include_amounts(tmp_path):
    http, database = client(tmp_path)
    connection = sqlite3.connect(database)
    connection.executescript('''
        INSERT INTO AD_Plati (IDPL,IDP,IDZ,IDS,IDL,Plata,TIP,Anulata)
            VALUES (1,1,1,1,1,80,2,0),(2,1,1,1,1,120,1,0),(3,1,1,1,1,20,2,1);
        INSERT INTO AD_Chitante (IDC,IDPL,IDL,Numar,Anulata)
            VALUES (1,1,NULL,10,0),(2,3,NULL,11,0);
        INSERT INTO AD_AlteDoc (IDA,IDPL,IDL,NrDoc,Explicatie,Anulata)
            VALUES (1,2,NULL,'OP-1','Virament',0);
    ''')
    connection.commit(); connection.close()
    situation = http.get('/api/adechit/situation/1?IDG=1', headers=headers()).json['rows'][0]
    assert situation['Plata'] == 80 and situation['Plati'] == 120
    receipts = http.get('/api/adechit/ledger/1?IDL=1&kind=receipt', headers=headers())
    assert receipts.status_code == 200
    assert [row['Valoare'] for row in receipts.json['rows']] == [80, 20]
    assert [row['Cancelled'] for row in receipts.json['rows']] == [False, True]
    others = http.get('/api/adechit/ledger/1?IDL=1&kind=other', headers=headers())
    assert others.status_code == 200
    assert others.json['rows'][0]['Valoare'] == 120
    assert others.json['rows'][0]['NrDoc'] == 'OP-1'
    assert http.get('/api/adechit/ledger/1?IDL=1&kind=refund', headers=headers()).json['rows'] == []
    assert http.get('/api/adechit/ledger/1?IDL=2&kind=receipt', headers=headers()).status_code == 409
    assert http.get('/api/adechit/ledger/1?IDL=bad&kind=receipt', headers=headers()).status_code == 400
    assert http.get('/api/adechit/ledger/1?IDL=1&kind=bad', headers=headers()).status_code == 400


def test_context_edit_and_read_only_refusal(tmp_path):
    http, _ = client(tmp_path)
    context = http.get('/api/adechit/context', headers=headers())
    assert context.status_code == 200
    assert context.json['preview'] is True

    row = http.get('/api/adechit/rows/Prezenta', headers=headers()).json['rows'][0]
    saved = post(http, '/api/adechit/attendance', {
        'id': row['IDZ'], 'version': row['Version'], 'values': {'ZilePrezenta': 11},
    })
    assert saved.status_code == 200
    assert saved.json['ZilePrezenta'] == 11
    assert saved.json['ValoareContract'] == 275

    refused = post(http, '/api/adechit/catalog/Chitante', {
        'id': None, 'values': {'Numar': 7},
    })
    assert refused.status_code == 400
    assert refused.json['reason'] == 'TABLE'


def test_workspace_situation_and_prepare_new_children(tmp_path):
    http, database = client(tmp_path)
    situation = http.get('/api/adechit/situation/1', headers=headers())
    assert situation.status_code == 200
    assert situation.json['rows'][0]['Version'] == 1
    assert situation.json['rows'][0]['ZileLuna'] == 22

    connection = sqlite3.connect(database)
    connection.execute("INSERT INTO AD_Platitori (IDP,IDG,Nume,Plecat) VALUES (2,1,'Copil nou',0)")
    connection.commit(); connection.close()
    prepared = post(http, '/api/adechit/attendance/prepare', {'month_id': 1, 'group_id': 1})
    assert prepared.status_code == 200
    assert prepared.json['count'] == 1
    assert prepared.json['created'][0]['IDP'] == 2
    repeated = post(http, '/api/adechit/attendance/prepare', {'month_id': 1, 'group_id': 1})
    assert repeated.status_code == 409 and repeated.json['reason'] == 'NO_NEW_CHILDREN'


def test_lazy_catalog_and_server_filters(tmp_path, monkeypatch):
    from routes.adechit.repository import Repository
    http, database = client(tmp_path)
    connection = sqlite3.connect(database)
    connection.executescript("""
        INSERT INTO AD_Grupe (IDG,Grupa) VALUES (2,'Other group');
        INSERT INTO AD_Platitori (IDP,IDG,Nume,Plecat) VALUES (2,2,'Other child',0);
        INSERT INTO AD_Platitori_sub (IDS,IDP,Nume,Activ) VALUES (2,2,'Other payer',1);
        INSERT INTO AD_LunaD (IDL,Anul,Luna) VALUES (9,2025,12);
    """)
    connection.commit(); connection.close()
    queries = []
    query = Repository.query
    def tracked(self, sql, params=()):
        queries.append(sql)
        return query(self, sql, params)
    monkeypatch.setattr(Repository, 'query', tracked)
    context = http.get('/api/adechit/context', headers=headers()).json
    assert 'months' not in context and 'groups' not in context
    assert queries == []
    assert http.get('/api/adechit/years', headers=headers()).json == {'years': [2026, 2025]}
    groups = http.get('/api/adechit/catalog-data', headers=headers()).json
    assert set(groups) == {'groups'} and len(groups['groups']) == 2
    assert not any('AD_Platitori' in sql for sql in queries)
    months = http.get('/api/adechit/rows/LunaD?Anul=2026', headers=headers()).json['rows']
    assert [row['IDL'] for row in months] == [1]
    children = http.get('/api/adechit/rows/Platitori?IDG=1', headers=headers()).json['rows']
    payers = http.get('/api/adechit/rows/Platitori_sub?IDP=1', headers=headers()).json['rows']
    assert [row['IDP'] for row in children] == [1]
    assert [row['IDS'] for row in payers] == [1]
    assert all('WHERE' in sql for sql in queries if 'AD_Platitori' in sql)
    assert http.get('/api/adechit/rows/Platitori?IDG=bad', headers=headers()).status_code == 400


def test_group_situation_preserves_history_and_closed_snapshot(tmp_path, monkeypatch):
    from routes.adechit.repository import Repository
    http, database = client(tmp_path)
    connection = sqlite3.connect(database)
    connection.executescript("""
        INSERT INTO AD_Grupe (IDG,Grupa) VALUES (2,'Previous group');
        INSERT INTO AD_Platitori (IDP,IDG,Nume,SI,Plecat) VALUES (2,2,'Other child',0,0);
        INSERT INTO AD_LunaD (IDL,IDV,Anul,Luna,Inchisa,ZileLuna) VALUES (2,1,2026,11,0,21);
        INSERT INTO AD_Prezenta (IDZ,IDP,IDG,IDV,IDL,ZilePrezenta,ValoareContract,ValoareTotala)
          VALUES (2,1,2,1,2,5,125,125),(3,2,1,1,2,2,50,50);
        INSERT INTO AD_Plati (IDPL,IDZ,IDP,IDL,Plata,TIP,Anulata) VALUES (1,1,1,1,20,2,0);
    """)
    connection.commit(); connection.close()
    all_rows = http.get('/api/adechit/situation/2', headers=headers()).json['rows']
    def no_full_data(self):
        raise AssertionError('Group reads must not load the entire database')
    monkeypatch.setattr(Repository, 'data', no_full_data)
    scoped = http.get('/api/adechit/situation/2?IDG=2', headers=headers())
    assert scoped.status_code == 200
    assert scoped.json['rows'] == [row for row in all_rows if row['IDG'] == 2]
    assert scoped.json['rows'][0]['SID'] == 230
    empty = http.get('/api/adechit/situation/2?IDG=1', headers=headers()).json['rows']
    assert [row['IDP'] for row in empty] == [2]
    connection = sqlite3.connect(database)
    connection.executescript("""
        UPDATE AD_LunaD SET Inchisa=1 WHERE IDL=2;
        INSERT INTO AD_SS_Buget (IDL,IDG,IDP,IDZ) VALUES (2,2,1,2),(2,1,2,3);
    """)
    connection.commit(); connection.close()
    closed = http.get('/api/adechit/situation/2?IDG=2', headers=headers())
    assert closed.status_code == 200 and closed.json['saved']
    assert [row['IDP'] for row in closed.json['rows']] == [1]
    assert http.get('/api/adechit/situation/2?IDG=bad', headers=headers()).status_code == 400


def test_m04_and_idempotency(tmp_path):
    http, _ = client(tmp_path)
    body = {'kind': 'other', 'attendance_id': 1, 'payer_id': 1, 'date': '2026-11-03',
            'amount': 100, 'number': 'OP-1'}
    key = 'same-operation-0001'
    first = post(http, '/api/adechit/documents', body, key)
    repeated = post(http, '/api/adechit/documents', body, key)
    assert first.status_code == repeated.status_code == 200
    assert first.json == repeated.json
    assert len(http.get('/api/adechit/rows/Plati', headers=headers()).json['rows']) == 1

    changed = post(http, '/api/adechit/documents', {**body, 'amount': 101}, key)
    assert changed.status_code == 409 and changed.json['reason'] == 'KEY_REUSED'
    refused = post(http, '/api/adechit/documents', {**body, 'date': '2025-11-03', 'number': 'OP-2'})
    assert refused.status_code == 400 and refused.json['reason'] == 'DATE_MONTH'


def test_receipt_refund_and_open_decision_refusals(tmp_path):
    http, _ = client(tmp_path)
    receipt = post(http, '/api/adechit/documents', {
        'kind': 'receipt', 'attendance_id': 1, 'payer_id': 1, 'date': '2026-10-08', 'amount': 125,
    })
    assert receipt.status_code == 200
    assert (receipt.json['Serie'], receipt.json['Numar'], receipt.json['Valoare']) == ('DEMO', 1, 125)
    refund = post(http, '/api/adechit/documents', {
        'kind': 'refund', 'attendance_id': 1, 'payer_id': 1, 'date': '2026-10-08',
        'amount': 20.5, 'number': 'R-1', 'explanation': 'Restituire test',
    })
    assert refund.status_code == 200
    refused = post(http, '/api/adechit/cancel', {
        'kind': 'refund', 'id': refund.json['IDR'], 'version': refund.json['Version'], 'reason': 'test',
    })
    assert refused.status_code == 409 and refused.json['reason'] == 'M03'
    transfer = post(http, '/api/adechit/transfer', {'id': 1, 'group_id': 2})
    assert transfer.status_code == 409 and transfer.json['reason'] == 'M06'


def test_m02_closed_snapshot_is_rewritten_on_cancel(tmp_path):
    http, _ = client(tmp_path)
    closed = post(http, '/api/adechit/close', {'id': 1})
    assert closed.status_code == 200
    document = post(http, '/api/adechit/documents', {
        'kind': 'other', 'attendance_id': 1, 'payer_id': 1, 'date': '2026-10-20',
        'amount': 75, 'number': 'OP-75',
    })
    assert document.status_code == 200
    snapshot = http.get('/api/adechit/rows/SS_Buget?IDL=1', headers=headers()).json['rows'][0]
    assert snapshot['Plati'] == 75
    canceled = post(http, '/api/adechit/cancel', {
        'kind': 'other', 'id': document.json['IDA'], 'version': document.json['Version'], 'reason': 'corecție test',
    })
    assert canceled.status_code == 200
    snapshot = http.get('/api/adechit/rows/SS_Buget?IDL=1', headers=headers()).json['rows'][0]
    assert snapshot['Plati'] == 0
    attendance = http.get('/api/adechit/rows/Prezenta?IDL=1', headers=headers()).json['rows'][0]
    locked = post(http, '/api/adechit/attendance', {
        'id': attendance['IDZ'], 'version': attendance['Version'], 'values': {'ZilePrezenta': 1},
    })
    assert locked.status_code == 409 and locked.json['reason'] == 'CLOSED'


def test_m05_blocks_both_months_then_orphans_and_reattaches(tmp_path):
    http, database = client(tmp_path)
    assert post(http, '/api/adechit/close', {'id': 1}).status_code == 200
    next_attendance = http.get('/api/adechit/rows/Prezenta?IDL=2', headers=headers()).json['rows'][0]
    assert post(http, '/api/adechit/documents', {
        'kind': 'other', 'attendance_id': next_attendance['IDZ'], 'payer_id': 1,
        'date': '2026-11-10', 'amount': 30, 'number': 'OP-NOV',
    }).status_code == 200
    blocked = post(http, '/api/adechit/reopen', {'id': 1})
    assert blocked.status_code == 409 and blocked.json['reason'] == 'REOPEN_MOVEMENTS'

    connection = sqlite3.connect(database)
    connection.execute("UPDATE AD_Settings SET SettingValue='0' WHERE SettingKey='BlockReopenWithMovements'")
    connection.commit(); connection.close()
    reopened = post(http, '/api/adechit/reopen', {'id': 1})
    assert reopened.status_code == 200 and reopened.json['orphaned']['Plati'] == 1
    orphan = http.get('/api/adechit/rows/Plati', headers=headers()).json['rows'][0]
    assert orphan['IDL'] is None and orphan['IDZ'] is None
    assert (orphan['OriginMonth'], orphan['OriginYear']) == (11, 2026)

    closed_again = post(http, '/api/adechit/close', {'id': 1})
    assert closed_again.status_code == 200 and closed_again.json['reattached']['Plati'] == 1
    attached = http.get('/api/adechit/rows/Plati', headers=headers()).json['rows'][0]
    assert attached['IDL'] == closed_again.json['opened']['IDL'] and attached['IDZ'] is not None


def test_m05_block_also_checks_reopened_month(tmp_path):
    http, _ = client(tmp_path)
    movement = post(http, '/api/adechit/documents', {
        'kind': 'other', 'attendance_id': 1, 'payer_id': 1, 'date': '2026-10-10',
        'amount': 15, 'number': 'OP-OCT',
    })
    assert movement.status_code == 200
    assert post(http, '/api/adechit/close', {'id': 1}).status_code == 200
    blocked = post(http, '/api/adechit/reopen', {'id': 1})
    assert blocked.status_code == 409 and blocked.json['reason'] == 'REOPEN_MOVEMENTS'


def test_import_repeat_and_reconcile(tmp_path):
    http, database = client(tmp_path)
    connection = sqlite3.connect(database)
    table_names = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE name LIKE 'AD_%'")]
    for name in table_names:
        if name not in ('AD_Lock', 'AD_Operations', 'AD_Settings', 'AD_Imports', 'AD_Subunits', 'AD_ReceiptConfig'):
            connection.execute(f'DELETE FROM `{name}`')
    connection.execute('DELETE FROM AD_Imports')
    connection.commit(); connection.close()

    tables = {name: [] for name in ('Grupe', 'ValoriTaxe', 'Platitori', 'Platitori_sub',
        'LunaD', 'Prezenta', 'Plati', 'Chitante', 'AlteDoc', 'Retur', 'SS_Buget')}
    tables['Grupe'] = [{'IDG': 41, 'Grupa': 'Import test', 'Educator': 'E'}]
    dataset = {'format': 'adechit-access-v1', 'source': 'fixture', 'tables': tables}
    body = {'dataset': dataset, 'source_file': 'fixture.json'}
    first = http.post('/api/adechit/import', json=body, headers=headers())
    repeated = http.post('/api/adechit/import', json=body, headers=headers())
    assert first.status_code == repeated.status_code == 200
    assert first.json['repeated'] is False and repeated.json['repeated'] is True
    reconciliation = http.post('/api/adechit/reconcile', json=body, headers=headers())
    assert reconciliation.status_code == 200 and reconciliation.json['ok'] is True


def test_phase_comparator_reports_first_difference():
    access = {'qPrezenta': [{'IDZ': 1, 'Plata': 10}], 'Update_Solduri': [{'IDZ': 1, 'SID': 2}]}
    web = {'qPrezenta': [{'IDZ': 1, 'Plata': 10}], 'Update_Solduri': [{'IDZ': 1, 'SID': 3}]}
    result = compare(access, web)
    assert result['ok'] is False
    assert result['first_divergent_phase'] == 'Update_Solduri'
    assert result['differences'][0]['field'] == 'SID'


def test_catalog_batch_rollback_and_idempotency(tmp_path):
    http, _ = client(tmp_path)
    body = {'items': [
        {'table': 'Platitori', 'id': 1, 'version': 1, 'values': {'Nume': 'Updated child'}},
        {'table': 'Platitori_sub', 'id': 1, 'version': 99, 'values': {'Nume': 'Updated payer'}},
    ]}
    refused = post(http, '/api/adechit/catalog-save', body)
    assert refused.status_code == 409 and refused.json['reason'] == 'CONFLICT'
    child = http.get('/api/adechit/rows/Platitori', headers=headers()).json['rows'][0]
    assert child['Version'] == 1 and child['Nume'] != 'Updated child'
    body['items'][1]['version'] = 1
    first = post(http, '/api/adechit/catalog-save', body, 'catalog-batch-retry-1')
    repeated = post(http, '/api/adechit/catalog-save', body, 'catalog-batch-retry-1')
    assert first.status_code == repeated.status_code == 200
    assert first.json == repeated.json
    assert first.json['rows'][0]['Version'] == 2


def test_catalog_batch_tax_validation_activation_and_rights(tmp_path):
    http, database = client(tmp_path)
    invalid = post(http, '/api/adechit/catalog-save', {'items': [
        {'table': 'ValoriTaxe', 'id': 1, 'version': 1, 'values': {'TaxaZilnica': 30}},
        {'table': 'ValoriTaxe', 'id': None, 'values': {'TaxaZilnica': -1}},
    ]})
    assert invalid.status_code == 400
    assert http.get('/api/adechit/rows/ValoriTaxe', headers=headers()).json['rows'][0]['TaxaZilnica'] == 25
    saved = post(http, '/api/adechit/catalog-save', {'items': [
        {'table': 'ValoriTaxe', 'id': None, 'values': {'TaxaZilnica': 30, 'Activ': True, 'Expl': 'New tax', 'DeLa': '2026-11'}},
        {'table': 'ValoriTaxe', 'id': 1, 'version': 1, 'values': {'Expl': 'Old tax'}},
    ]})
    assert saved.status_code == 200
    taxes = http.get('/api/adechit/rows/ValoriTaxe', headers=headers()).json['rows']
    assert [row['TaxaZilnica'] for row in taxes if row['Activ']] == [30]
    assert http.get('/api/adechit/rows/Prezenta', headers=headers()).json['rows'][0]['IDV'] == 1
    http.application.config['ADE_TEST_RIGHTS'].discard('catalog')
    refused = post(http, '/api/adechit/catalog-save', {'items': [
        {'table': 'ValoriTaxe', 'id': None, 'values': {'TaxaZilnica': 40}},
    ]})
    assert refused.status_code == 403


def test_cnp_vba_codes_and_api_validation(tmp_path):
    from PYTHON.routes.adechit.domain import cnp_code
    def valid(prefix):
        digit = sum(int(a) * int(b) for a, b in zip(prefix, '279146358279')) % 11
        return prefix + str(1 if digit == 10 else digit)
    sample = valid('520101012345')
    assert cnp_code(sample) == -1
    assert cnp_code(sample[:-1]) == 0
    assert cnp_code('a' + sample[1:]) == 0
    assert cnp_code(valid('520130112345')) == 2
    assert cnp_code(valid('520103212345')) == 3
    assert cnp_code(valid('520101053345')) == 4
    assert cnp_code(sample[:-1] + str((int(sample[-1]) + 1) % 10)) == 5
    # VBA checks only the supplied upper bounds, not a full calendar date.
    assert cnp_code(valid('520000000345')) == -1
    prefixes = ['52010101234' + str(index) for index in range(10)]
    control_ten = next(prefix for prefix in prefixes
                       if sum(int(a) * int(b) for a, b in zip(prefix, '279146358279')) % 11 == 10)
    assert cnp_code(control_ten + '1') == -1
    http, _ = client(tmp_path)
    refused = post(http, '/api/adechit/catalog/Platitori_sub', {
        'id': 1, 'version': 1, 'values': {'CNP_Platitor': 'invalid'},
    })
    assert refused.status_code == 400 and refused.json['reason'] == 'CNP'
    saved = post(http, '/api/adechit/catalog/Platitori_sub', {
        'id': 1, 'version': 1, 'values': {'CNP_Platitor': sample, 'Telefon': '0700000000'},
    })
    assert saved.status_code == 200 and saved.json['Telefon'] == '0700000000'
    invalid_child = post(http, '/api/adechit/child-save', {'id': 1, 'version': 1, 'values': {'CNP': '123'}})
    assert invalid_child.status_code == 400 and invalid_child.json['reason'] == 'CNP'


def test_group_form_atomic_periods_and_conflicts(tmp_path):
    http, _ = client(tmp_path)
    body = {'id': None, 'values': {'Grupa': 'New group', 'InchisaDinAn': None}, 'educators': [
        {'id': None, 'values': {'Educator': 'Teacher', 'DeLa': '2026-09-01', 'PanaLa': '2026-08-01'}},
    ]}
    assert post(http, '/api/adechit/group-save', body).status_code == 400
    assert len(http.get('/api/adechit/rows/Grupe', headers=headers()).json['rows']) == 1
    body['educators'][0]['values']['PanaLa'] = None
    saved = post(http, '/api/adechit/group-save', body)
    assert saved.status_code == 200
    group = saved.json
    periods = http.get('/api/adechit/rows/Grupe_Educator', headers=headers()).json['rows']
    period = next(row for row in periods if row['IDG'] == group['IDG'])
    edited = {'id': group['IDG'], 'version': group['Version'], 'values': {'Grupa': 'Changed'}, 'educators': [
        {'id': period['IDGE'], 'version': 999, 'values': {'Educator': 'Teacher', 'DeLa': '2026-09-01', 'PanaLa': None}},
    ]}
    assert post(http, '/api/adechit/group-save', edited).status_code == 409
    assert http.get('/api/adechit/rows/Grupe', headers=headers()).json['rows'][-1]['Grupa'] == 'New group'
    edited['educators'] = [{'id': None, 'values': {'Educator': 'Teacher', 'DeLa': '2026-10-01', 'PanaLa': None}}]
    refused = post(http, '/api/adechit/group-save', edited)
    assert refused.status_code == 400 and refused.json['reason'] == 'PERIOD'


def test_child_form_transfer_preserves_closed_month_and_logs_once(tmp_path):
    http, _ = client(tmp_path)
    group = post(http, '/api/adechit/group-save', {'id': None, 'values': {'Grupa': 'Target'}, 'educators': []}).json
    assert post(http, '/api/adechit/close', {'id': 1}).status_code == 200
    moved = {'id': 1, 'version': 1, 'values': {'Nume': 'Changed child', 'IDG': group['IDG'], 'Plecat': False}}
    first = post(http, '/api/adechit/child-save', moved, 'child-form-transfer-01')
    repeated = post(http, '/api/adechit/child-save', moved, 'child-form-transfer-01')
    assert first.status_code == repeated.status_code == 200 and first.json == repeated.json
    rows = http.get('/api/adechit/rows/Prezenta', headers=headers()).json['rows']
    assert next(row for row in rows if row['IDL'] == 1)['IDG'] == 1
    assert next(row for row in rows if row['IDL'] == 2)['IDG'] == group['IDG']
    logs = http.get('/api/adechit/rows/Platitori_Istoric', headers=headers()).json['rows']
    assert len(logs) == 1 and logs[0]['Tip'] == 'MUTARE'
    invalid = post(http, '/api/adechit/child-save', {'id': 1, 'version': 2, 'values': {'Plecat': True}})
    assert invalid.status_code == 400


def test_departed_child_with_balance_stays_in_next_month(tmp_path):
    http, _ = client(tmp_path)
    departed = post(http, '/api/adechit/child-save', {'id': 1, 'version': 1,
        'values': {'Plecat': True, 'DataIesire': '2026-10-09'}})
    assert departed.status_code == 200
    assert post(http, '/api/adechit/close', {'id': 1}).status_code == 200
    data = http.get('/api/adechit/catalog-data?include_hidden=1', headers=headers()).json
    special = next(row for row in data['groups'] if row.get('Tip') == 'PLECATI')
    assert special['Ascunsa'] == 1
    attendance = http.get('/api/adechit/rows/Prezenta?IDL=2', headers=headers()).json['rows']
    assert len(attendance) == 1 and attendance[0]['IDG'] == special['IDG']


def test_educator_history_is_authoritative_for_each_period():
    from PYTHON.routes.adechit.domain import group_educators
    group = {'IDG': 1, 'Educator': 'Legacy value'}
    history = [{'IDG': 1, 'Educator': 'A', 'DeLa': '2026-01-01', 'PanaLa': '2026-09-15'},
               {'IDG': 1, 'Educator': 'B', 'DeLa': '2026-09-16', 'PanaLa': '2026-10-31'}]
    assert group_educators(group, history, '2026-09-10') == 'A'
    assert group_educators(group, history, '2026-09-30') == 'B'
    assert group_educators(group, history, '2026-11-01') == ''
