import logging
import mysql.connector
from flask import Blueprint, request, jsonify
from mysql.connector import Error
from utils.security import require_api_key
from config import DB_CONFIG  # Importam configurarea

parteneri_bp = Blueprint('parteneri', __name__)
logger = logging.getLogger(__name__)

# --- ACEASTA FUNCTIE LIPSEA ---
def get_db_connection(target_db_name):
    """Conectare dinamica la baza specificata, folosind user/pass din config."""
    return mysql.connector.connect(
        **DB_CONFIG,            # Despachetam host, user, password din config
        database=target_db_name # Suprascriem baza de date
    )
# ------------------------------

@parteneri_bp.route('/api/parteneri/get_partner_ids_by_codes', methods=['POST'])
@require_api_key
def get_partner_ids_by_codes():
    try:
        data = request.json
        
        # Extragem parametrii
        db_name = data.get('db_name')
        unit_id = data.get('unit_id')
        partner_codes = data.get('partner_codes')

        # 1. Validări
        if not db_name or not unit_id:
            return jsonify({"error": "Lipsesc parametrii 'db_name' sau 'unit_id'."}), 400

        if not partner_codes or not isinstance(partner_codes, list):
            return jsonify({"data": []}), 200

        # 2. Conectare (Acum functia exista!)
        conn = get_db_connection(db_name)
        try:
            cursor = conn.cursor(dictionary=True)

            # 3. Query
            placeholders = ', '.join(['%s'] * len(partner_codes))
            
            sql = f"""
                SELECT IdPartener, CodPartener 
                FROM Parteneri 
                WHERE IdUnitate = %s 
                AND CodPartener IN ({placeholders})
            """

            params = [unit_id] + partner_codes
            cursor.execute(sql, tuple(params))
            
            rows = cursor.fetchall()
            
            return jsonify({"data": rows}), 200

        finally:
            if conn.is_connected():
                conn.close()

    except Exception as e:
        logger.error(f"Eroare parteneri: {e}")
        return jsonify({"error": str(e)}), 500

# NEFOLOSITA SI NETESTATA, IGNORATI!
@parteneri_bp.route('/api/parteneri/upsert_coduri', methods=['POST'])
@require_api_key
def upsert_parteneri_coduri():
    try:
        data = request.json
        db_name = data.get('db_name')
        items = data.get('items') 

        if not db_name or not items:
            return jsonify({"error": "Parametri insuficienti."}), 400

        conn = get_db_connection(db_name)
        try:
            cursor = conn.cursor()
            
            # Logic: Daca (IdPartener, IdClsf) exista, updateaza CodAng, CodInd si restul.
            # Daca valorile sunt identice cu cele din DB, MariaDB nu va face nicio schimbare (rowcount 0).
            sql = """
                INSERT INTO Parteneri_Coduri (
                    IdPartener, CodPartener, IdClsf, IdClsfAcc, CodAng, CodInd, ContBancar
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    CodAng = VALUES(CodAng),
                    CodInd = VALUES(CodInd),
                    IdClsfAcc = VALUES(IdClsfAcc),
                    ContBancar = VALUES(ContBancar),
                    CodPartener = VALUES(CodPartener)
            """

            params_list = []
            for item in items:
                params_list.append((
                    item.get('IdPartener'),
                    item.get('CodPartener'),
                    item.get('IdClsf'),
                    item.get('IdClsfAcc'),
                    item.get('CodAng'),
                    item.get('CodInd'),
                    item.get('ContBancar')
                ))

            cursor.executemany(sql, params_list)
            conn.commit()

            return jsonify({"success": True, "affected_rows": cursor.rowcount}), 200

        finally:
            if conn.is_connected():
                conn.close()

    except Exception as e:
        logger.error(f"Eroare Upsert: {e}")
        return jsonify({"error": str(e)}), 500

# ==============================================================================
# 7. INSERT PARTENERI
# ==============================================================================
@parteneri_bp.route('/api/parteneri/syncdb', methods=['POST'])
@require_api_key
def save_parteneri():
    try:
        req_data = request.json
        db_name = req_data.get('db_name')
        data_list = req_data.get('data')

        logger.info(f"Insert PARTENERI in {db_name}. Count: {len(data_list) if data_list else 0}")

        if not db_name or not data_list:
            return jsonify({"error": "Date invalide"}), 400

        conn = None
        try:
            conn = get_db_connection(db_name)
            cursor = conn.cursor()
            conn.start_transaction()
            
            sql = """INSERT INTO Parteneri 
                     (IdUnitate, CodPartener, DenumirePartener, CodFiscal, ContIBAN, Banca, Adresa, Tip) 
                     VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"""
            
            values = [(x['IdUnitate'], x['CodPartener'], x['DenumirePartener'], x['CodFiscal'], 
                       x['ContIBAN'], x['Banca'], x['Adresa'], x['Tip']) 
                      for x in data_list]
            
            # salvez primul IdUnitate din data_list intr-o variabila locala.
            idunitate = data_list[0]['IdUnitate'] if data_list else None
            
            cursor.executemany(sql, values)
            conn.commit()
            
            logger.info(f"Insert PARTENERI Succes. Rows: {cursor.rowcount}")

            # luam codurile trimise
            coduri = [x['CodPartener'] for x in data_list]

            format_strings = ','.join(['%s'] * len(coduri))

            cursor.execute(f"""
                SELECT CodPartener, IdPartener
                FROM Parteneri
                WHERE CodPartener IN ({format_strings}) AND IdUnitate = %s
            """, tuple(coduri) + (idunitate,))

            rows = cursor.fetchall()

            mapping = {cod: idp for cod, idp in rows}

            return jsonify({
                "status": "success",
                "count": cursor.rowcount,
                "mapping": mapping
            }), 200

        except Exception as e:
            if conn: conn.rollback()
            logger.error(f"Eroare Insert Parteneri: {str(e)}", exc_info=True)
            return jsonify({"error": str(e)}), 500
        finally:
            if conn: conn.close()
    except Exception as e:
        logger.error(f"Eroare Generala Parteneri: {str(e)}")
        return jsonify({"error": str(e)}), 500