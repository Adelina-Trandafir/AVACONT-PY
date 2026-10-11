import logging
import mysql.connector
from flask import Blueprint, request, jsonify
from mysql.connector import Error
from utils.security import require_access_sync
from utils.access_limits import access_sync_limits
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

# ==============================================================================
# 7. INSERT PARTENERI
# ==============================================================================
@parteneri_bp.route('/api/parteneri/syncdb', methods=['POST'])
@require_access_sync
@access_sync_limits('parteneri_insert')
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


@parteneri_bp.route('/api/parteneri/upsert', methods=['POST'])
@require_access_sync
@access_sync_limits('parteneri_upsert')
def upsert_parteneri():
    try:
        req_data = request.json

        db_name = req_data.get('db_name')
        data_list = req_data.get('data')

        if not db_name or not data_list:
            return jsonify({"error": "Date invalide"}), 400

        conn = None

        try:
            conn = get_db_connection(db_name)
            cursor = conn.cursor()

            conn.start_transaction()

            sql = """
                INSERT INTO Parteneri
                (
                    IdUnitate,
                    CodPartener,
                    DenumirePartener,
                    CodFiscal,
                    ContIBAN,
                    Banca,
                    Adresa,
                    Tip
                )
                VALUES
                (
                    %s, %s, %s, %s,
                    %s, %s, %s, %s
                )
                ON DUPLICATE KEY UPDATE
                    DenumirePartener = VALUES(DenumirePartener),
                    CodFiscal        = VALUES(CodFiscal),
                    ContIBAN         = VALUES(ContIBAN),
                    Banca            = VALUES(Banca),
                    Adresa           = VALUES(Adresa),
                    Tip              = VALUES(Tip)
            """

            values = [
                (
                    x['IdUnitate'],
                    x['CodPartener'],
                    x.get('DenumirePartener'),
                    x.get('CodFiscal'),
                    x.get('ContIBAN'),
                    x.get('Banca'),
                    x.get('Adresa'),
                    x.get('Tip')
                )
                for x in data_list
            ]

            cursor.executemany(sql, values)

            conn.commit()

            # refacem mapping-ul CodPartener -> IdPartener
            idunitate = data_list[0]['IdUnitate']

            coduri = [x['CodPartener'] for x in data_list]

            format_strings = ','.join(['%s'] * len(coduri))

            cursor.execute(f"""
                SELECT
                    CodPartener,
                    IdPartener
                FROM Parteneri
                WHERE IdUnitate = %s
                  AND CodPartener IN ({format_strings})
            """, (idunitate, *coduri))

            rows = cursor.fetchall()

            mapping = {
                cod: idp
                for cod, idp in rows
            }

            return jsonify({
                "status": "success",
                "count": len(data_list),
                "affected_rows": cursor.rowcount,
                "mapping": mapping
            }), 200

        except Exception:
            if conn:
                conn.rollback()
            raise

        finally:
            if conn:
                conn.close()

    except Exception as e:
        logger.error(
            f"Eroare UPSERT Parteneri: {str(e)}",
            exc_info=True
        )

        return jsonify({"error": str(e)}), 500