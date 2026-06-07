import logging
import mysql.connector
from flask import Blueprint, request, jsonify
from mysql.connector import Error
from utils.security import require_api_key
from config import DB_CONFIG  # Importam configurarea ta din config.py

salarii_bp = Blueprint('salarii', __name__)
logger = logging.getLogger(__name__)

# WHITELIST - DEFINIREA COLOANELOR ACCEPTATE IN TABELE
# Nota: db_name NU este aici, deci va fi ignorat la insert (ceea ce vrem)
ALLOWED_COLUMNS = {
    "SalariiH": [
        "IdUnitate", "ID", "IdSalarii", "AA", "LunaAn", "Forma", 
        "Explicatie", "Cap", "Zec2", "CodAngajament"
    ],
    "Salarii": [
        "IdUnitate", "IdSal", "IdAngajament", "IdSalarii", "IDS", "Clsf", 
        "LunaAn", "Forma", "Explicatie", "ContD", "PartD", "ContC", "PartC", 
        "Valoare", "AreDDF", "CodIndicator"
    ],
    "SalariiPC": [
        "IdSal", "IdPlataC", "IdUnitate", "IdSalarii", "IDS", "IDD", "IDPC", 
        "IDVAA", "LunaAn", "Clsf", "Explicatie", "ContD", "PartD", "ContC", 
        "PartC", "Valoare"
    ],
    "SalariiPN": [
        "IdSal", "IdUnitate", "IdPlataN", "IdSalarii", "IDS", "IDD", "IDPN", 
        "Clsf", "LunaAn", "Explicatie", "ContD", "PartD", "ContC", "PartC", 
        "Valoare", "GRUPA"
    ],
    "SalariiVA": [
        "IdSal", "IdUnitate", "IdRetinere", "IdSalarii", "IDD", "IDVA", 
        "LunaAn", "Explicatie", "ContD", "PartD", "ContC", "PartC", "Valoare"
    ]
}

def get_db_connection(target_db_name):
    """
    Creaza o conexiune la baza de date specifica (target_db_name)
    folosind credentialele (user, pass, host, port) din config.py
    """
    return mysql.connector.connect(
        host=DB_CONFIG['host'],
        port=DB_CONFIG.get('port', 3306), # Default 3306 daca lipseste in config
        user=DB_CONFIG['user'],
        password=DB_CONFIG['password'],
        database=target_db_name
    )

@salarii_bp.route('/api/salarii/save_document', methods=['POST'])
@require_api_key
def save_document_complex():
    """
    Salveaza un document complet (Header + Detalii) intr-o singura tranzactie.
    Identifica baza de date din JSON['header']['db_name'].
    """
    conn = None
    cursor = None
    
    try:
        data = request.json
        
        # Validare structura de baza
        header_data = data.get("header")
        detalii_dict = data.get("detalii")

        if not header_data:
            return jsonify({"status": "error", "message": "Lipsesc datele de Header."}), 400

        # 1. IDENTIFICARE BAZA DE DATE TINTA
        # Extragem db_name din header (ex: "046_GR21")
        target_db = header_data.get("db_name")
        
        if not target_db:
            return jsonify({"status": "error", "message": "Lipseste 'db_name' din Header. Nu stiu unde sa ma conectez."}), 400

        # 2. CONECTARE LA BAZA SPECIFICA
        try:
            conn = get_db_connection(target_db)
        except Error as e:
            logger.error(f"Eroare conectare MySQL la baza '{target_db}': {e}")
            return jsonify({"status": "error", "message": f"Nu s-a putut conecta la baza de date: {target_db}"}), 500

        cursor = conn.cursor()
        conn.start_transaction()

        # 3. INSERARE HEADER (SalariiH)
        try:
            cols_whitelist = ALLOWED_COLUMNS["SalariiH"]
            
            # Filtram dictionarul: pastram doar cheile care sunt in Whitelist.
            # Aici 'db_name' va fi ELIMINAT automat pentru ca nu e in ALLOWED_COLUMNS["SalariiH"]
            clean_header = {k: v for k, v in header_data.items() if k in cols_whitelist}
            
            if not clean_header:
                 raise Exception("Nu exista coloane valide pentru Header SalariiH dupa filtrare.")

            col_names = ', '.join(clean_header.keys())
            placeholders = ', '.join(['%s'] * len(clean_header))
            vals_header = list(clean_header.values())
            
            sql_header = f"INSERT INTO SalariiH ({col_names}) VALUES ({placeholders})"
            cursor.execute(sql_header, vals_header)
            
            # Obtinem ID-ul generat automat (AUTO_INCREMENT) pentru a-l pune in detalii
            new_id_sal = cursor.lastrowid
            
            if not new_id_sal:
                raise Exception("Nu s-a putut genera ID-ul pentru SalariiH.")
                
            logger.info(f"Header inserat in {target_db}. New IdSal={new_id_sal}")

        except Exception as e:
            conn.rollback()
            return jsonify({"status": "error", "message": f"Eroare la inserare Header: {str(e)}"}), 400

        # 4. INSERARE DETALII
        inserted_count = 0
        if detalii_dict and isinstance(detalii_dict, dict):
            
            for table_name, rows in detalii_dict.items():
                
                # Verificam daca tabelul e in whitelist si nu e header-ul
                if table_name not in ALLOWED_COLUMNS or table_name == "SalariiH":
                    continue
                
                permitted_cols = ALLOWED_COLUMNS[table_name]
                
                for idx, row in enumerate(rows):
                    try:
                        # Injectam ID-ul parintelui
                        row['IdSal'] = new_id_sal 
                        
                        # Curatam randul de coloane extra (Pastram IdUnitate, stergem altele daca apar)
                        clean_row = {k: v for k, v in row.items() if k in permitted_cols}
                        
                        if not clean_row: continue

                        col_names_det = ', '.join(clean_row.keys())
                        placeholders_det = ', '.join(['%s'] * len(clean_row))
                        vals_det = list(clean_row.values())
                        
                        sql_det = f"INSERT INTO {table_name} ({col_names_det}) VALUES ({placeholders_det})"
                        cursor.execute(sql_det, vals_det)
                        inserted_count += 1
                        
                    except Exception as e:
                        conn.rollback()
                        logger.error(f"Eroare {table_name} rand {idx}: {e}")
                        return jsonify({"status": "error", "message": f"Eroare la {table_name}, rand {idx}: {str(e)}"}), 400

        # 5. COMMIT TRANZACTIE
        conn.commit()
        
        return jsonify({
            "status": "success", 
            "message": f"Salvat cu succes in {target_db}. {inserted_count} linii detaliu.",
            "IdSal_Generat": new_id_sal
        }), 200

    except Exception as e:
        logger.error(f"Eroare Critica: {e}", exc_info=True)
        if conn and conn.is_connected(): 
            conn.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500
    
    finally:
        if cursor: cursor.close()
        if conn and conn.is_connected(): conn.close()