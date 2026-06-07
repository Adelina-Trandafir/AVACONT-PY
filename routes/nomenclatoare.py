import logging
from flask import Blueprint, request, jsonify
from mysql.connector import errorcode
from utils.security import require_api_key
from utils.database import get_db_connection

nom_bp = Blueprint('nomenclatoare', __name__)
logger = logging.getLogger(__name__)

@nom_bp.route('/api/check_unitate', methods=['POST'])
@require_api_key
def check_unitate():
    try:
        data = request.json
        db_name = data.get('db_name')
        id_unitate = data.get('id_unitate')

        if not db_name or not id_unitate:
            return jsonify({"error": "Parametri lipsa"}), 400

        conn = None
        try:
            conn = get_db_connection(db_name)
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM Unitati WHERE IdUnitate = %s", (id_unitate,))
            result = cursor.fetchone()
            exists = result is not None and result[0] > 0
            return jsonify({"exists": exists}), 200
        except Exception as e:
             # Handle bad db error separately if needed
            return jsonify({"error": str(e)}), 500
        finally:
            if conn: conn.close()
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@nom_bp.route('/api/unitati', methods=['POST'])
@require_api_key
def save_unitati():
    try:
        req_data = request.json
        db_name = req_data.get('db_name')
        data_list = req_data.get('data')

        if not db_name or not data_list:
            return jsonify({"error": "Date invalide"}), 400

        conn = get_db_connection(db_name)
        cursor = conn.cursor()
        try:
            conn.start_transaction()
            sql = "INSERT INTO Unitati (IdUnitate, Detalii, SursaSector, An, CodProgram) VALUES (%s, %s, %s, %s, %s)"
            values = [(x['IdUnitate'], x['Detalii'], x['SursaSector'], x['An'], x['CodProgram']) for x in data_list]
            cursor.executemany(sql, values)
            conn.commit()
            return jsonify({"status": "success", "count": cursor.rowcount}), 200
        except Exception as e:
            conn.rollback()
            return jsonify({"error": str(e)}), 500
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@nom_bp.route('/api/clasificatii_complete', methods=['POST'])
@require_api_key
def save_clasificatii_complete():
    try:
        req_data = request.json
        db_name = req_data.get('db_name')
        data_list = req_data.get('data')

        if not db_name or not data_list:
            return jsonify({"error": "Date invalide"}), 400

        conn = get_db_connection(db_name)
        cursor = conn.cursor()
        try:
            conn.start_transaction()
            sql_structura = "INSERT INTO Clasificatii (IdClsfAcc, IdUnitate, Capitol, Subcapitol, Articol, Alineat, Denumire) VALUES (%s, %s, %s, %s, %s, %s, %s)"
            sql_buget = "INSERT INTO Clasificatii_Buget (IdClsf, IdUnitate, An, Trim1, Trim2, Trim3, Trim4) VALUES (%s, %s, %s, %s, %s, %s, %s)"

            inserted_count = 0
            for item in data_list:
                s = item['structura']
                b = item['buget']
                cursor.execute(sql_structura, (s['IdClsfAcc'], s['IdUnitate'], s['Capitol'], s['Subcapitol'], s['Articol'], s['Alineat'], s['Denumire']))
                new_id = cursor.lastrowid
                cursor.execute(sql_buget, (new_id, b['IdUnitate'], b['An'], b['Trim1'], b['Trim2'], b['Trim3'], b['Trim4']))
                inserted_count += 1
            
            conn.commit()
            return jsonify({"status": "success", "count": inserted_count}), 200
        except Exception as e:
            conn.rollback()
            return jsonify({"error": str(e)}), 500
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@nom_bp.route('/api/mapping_clasificatii', methods=['POST'])
@require_api_key
def get_mapping():
    try:
        req_data = request.json
        db_name = req_data.get('db_name')
        if not db_name: return jsonify({"error": "Missing db_name"}), 400
        
        conn = get_db_connection(db_name)
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT IdClsfAcc, IdClsf FROM Clasificatii")
            rows = cursor.fetchall()
            mapping = {row['IdClsfAcc']: row['IdClsf'] for row in rows}
            return jsonify(mapping), 200
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@nom_bp.route('/api/rectificari', methods=['POST'])
@require_api_key
def save_rectificari():
    try:
        req_data = request.json
        db_name = req_data.get('db_name')
        data_list = req_data.get('data')

        if not db_name or not data_list: return jsonify({"error": "Date invalide"}), 400

        conn = get_db_connection(db_name)
        cursor = conn.cursor()
        try:
            conn.start_transaction()
            sql = "INSERT INTO Clasificatii_Rectificari (IdClsf, Capitol, Subcapitol, Articol, Alineat, Data, Document, Trim1, Trim2, Trim3, Trim4) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
            values = [(x['IdClsf'], x['Capitol'], x['Subcapitol'], x['Articol'], x['Alineat'], x['Data'], x['Document'], x['Trim1'], x['Trim2'], x['Trim3'], x['Trim4']) for x in data_list]
            cursor.executemany(sql, values)
            conn.commit()
            return jsonify({"status": "success", "count": cursor.rowcount}), 200
        except Exception as e:
            conn.rollback()
            return jsonify({"error": str(e)}), 500
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@nom_bp.route('/api/parteneri', methods=['POST'])
@require_api_key
def save_parteneri():
    try:
        req_data = request.json
        db_name = req_data.get('db_name')
        data_list = req_data.get('data')
        
        if not db_name or not data_list: return jsonify({"error": "Date invalide"}), 400

        conn = get_db_connection(db_name)
        cursor = conn.cursor()
        try:
            conn.start_transaction()
            sql = "INSERT INTO Parteneri (IdUnitate, CodPartener, DenumirePartener, CodFiscal, ContIBAN, Banca, Adresa, Tip) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
            values = [(x['IdUnitate'], x['CodPartener'], x['DenumirePartener'], x['CodFiscal'], x['ContIBAN'], x['Banca'], x['Adresa'], x['Tip']) for x in data_list]
            cursor.executemany(sql, values)
            conn.commit()
            return jsonify({"status": "success", "count": cursor.rowcount}), 200
        except Exception as e:
            conn.rollback()
            return jsonify({"error": str(e)}), 500
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"error": str(e)}), 500