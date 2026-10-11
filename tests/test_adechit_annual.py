import sqlite3
from copy import deepcopy

import pytest
from test_adechit import client, headers, post


def setup(tmp_path):
    http, database = client(tmp_path)
    with sqlite3.connect(database) as connection:
        connection.executescript("""
            UPDATE AD_LunaD SET Luna=8,LunaT='august',LA='082026' WHERE IDL=1;
            INSERT INTO AD_Grupe (IDG,Grupa,Tip) VALUES (2,'Destination','NORMALA');
            INSERT INTO AD_Platitori (IDP,IDG,Nume,SI,Plecat) VALUES (2,1,'Other child',0,0);
            INSERT INTO AD_Prezenta (IDZ,IDP,IDG,IDV,IDL,ZilePrezenta,ValoareContract,ValoareTotala)
                VALUES (2,2,1,1,1,0,0,0);
        """)
    data = http.get('/api/adechit/annual/1', headers=headers()).json
    draft = {'version': data['month']['Version'], 'new_groups': [],
             'groups': [{'id': row['IDG'], 'version': row['Version'], 'name': row['Grupa'], 'educators': row['educators']} for row in data['groups']],
             'children': [{'id': row['IDP'], 'version': row['Version'], 'group_id': row['IDG'], 'departed': False} for row in data['children']],
             'history': [{'id': row['IDGE'], 'version': row['Version']} for row in data['history']]}
    return http, database, draft


def test_august_requires_annual_plan(tmp_path):
    http, database, _ = setup(tmp_path)
    result = post(http, '/api/adechit/close', {'id': 1})
    assert result.status_code == 400 and result.json['reason'] == 'ANNUAL_REQUIRED'
    with sqlite3.connect(database) as connection:
        assert connection.execute('SELECT Inchisa FROM AD_LunaD WHERE IDL=1').fetchone()[0] == 0
        assert connection.execute('SELECT COUNT(*) FROM AD_SS_Buget').fetchone()[0] == 0


def test_individual_moves_new_group_departures_and_history(tmp_path):
    http, database, draft = setup(tmp_path)
    draft['new_groups'] = [{'id': -1, 'name': 'New group', 'educators': ['New educator']}]
    draft['groups'][0].update(name='Renamed group', educators=['Next educator'])
    draft['children'][0]['departed'] = True
    draft['children'][1]['group_id'] = -1
    result = post(http, '/api/adechit/close', {'id': 1, 'annual': draft}, key='annual-test-close')
    assert result.status_code == 200, result.json
    assert result.json['opened']['Luna'] == 9
    assert post(http, '/api/adechit/close', {'id': 1, 'annual': draft}, key='annual-test-close').json == result.json
    with sqlite3.connect(database) as connection:
        assert connection.execute('SELECT Plecat,DataIesire FROM AD_Platitori WHERE IDP=1').fetchone() == (1, '2026-08-31')
        new_id = connection.execute("SELECT IDG FROM AD_Grupe WHERE Grupa='New group'").fetchone()[0]
        assert connection.execute('SELECT IDG FROM AD_Platitori WHERE IDP=2').fetchone()[0] == new_id
        assert connection.execute('SELECT IDG FROM AD_Prezenta WHERE IDP=2 AND IDL=2').fetchone()[0] == new_id
        assert connection.execute('SELECT Grupa FROM AD_SS_Buget WHERE IDP=1').fetchone()[0] == 'Grupa de probă'
        assert connection.execute('SELECT Tip FROM AD_Grupe WHERE IDG=(SELECT IDG FROM AD_Prezenta WHERE IDP=1 AND IDL=2)').fetchone()[0] == 'PLECATI'
        assert connection.execute("SELECT PanaLa FROM AD_Grupe_Educator WHERE Educator='Educator de probă'").fetchone()[0] == '2026-08-31'
        assert connection.execute("SELECT DeLa FROM AD_Grupe_Educator WHERE Educator='Next educator'").fetchone()[0] == '2026-09-01'
        assert connection.execute('SELECT COUNT(*) FROM AD_Platitori_Istoric').fetchone()[0] == 2


@pytest.mark.parametrize('change,reason', [('name', 'NAME_UNIQUE'), ('educators', 'EDUCATOR'), ('children', 'CHILD_REQUIRED')])
def test_new_group_requirements(tmp_path, change, reason):
    http, database, draft = setup(tmp_path)
    draft['new_groups'] = [{'id': -1, 'name': 'New group', 'educators': ['Teacher']}]
    draft['children'][0]['group_id'] = -1
    if change == 'name':
        draft['new_groups'][0]['name'] = '  destination  '
    elif change == 'educators':
        draft['new_groups'][0]['educators'] = []
    else:
        draft['children'][0]['departed'] = True
    result = post(http, '/api/adechit/close', {'id': 1, 'annual': draft})
    assert result.status_code == 400 and result.json['reason'] == reason
    with sqlite3.connect(database) as connection:
        assert connection.execute('SELECT COUNT(*) FROM AD_Grupe').fetchone()[0] == 2
        assert connection.execute('SELECT COUNT(*) FROM AD_LunaD').fetchone()[0] == 1


def test_conflict_and_late_failure_roll_back_entire_close(tmp_path):
    http, database, draft = setup(tmp_path)
    stale = deepcopy(draft)
    stale['children'][0]['version'] = 99
    assert post(http, '/api/adechit/close', {'id': 1, 'annual': stale}).json['reason'] == 'CONFLICT'
    draft['groups'][0]['name'] = 'Changed'
    draft['children'][1]['group_id'] = 2
    with sqlite3.connect(database) as connection:
        connection.execute('UPDATE AD_ValoriTaxe SET Activ=0')
    result = post(http, '/api/adechit/close', {'id': 1, 'annual': draft})
    assert result.json['reason'] == 'ACTIVE_TAX'
    with sqlite3.connect(database) as connection:
        assert connection.execute('SELECT Grupa FROM AD_Grupe WHERE IDG=1').fetchone()[0] == 'Grupa de probă'
        assert connection.execute('SELECT IDG FROM AD_Platitori WHERE IDP=2').fetchone()[0] == 1
        assert connection.execute('SELECT COUNT(*) FROM AD_Platitori_Istoric').fetchone()[0] == 0
        assert connection.execute('SELECT COUNT(*) FROM AD_SS_Buget').fetchone()[0] == 0


def test_all_children_can_move_or_stay(tmp_path):
    http, database, draft = setup(tmp_path)
    for child in draft['children']:
        child['group_id'] = 2
    result = post(http, '/api/adechit/close', {'id': 1, 'annual': draft})
    assert result.status_code == 200
    with sqlite3.connect(database) as connection:
        assert connection.execute('SELECT DISTINCT IDG FROM AD_Prezenta WHERE IDL=2').fetchall() == [(2,)]
