
from __future__ import annotations

import os
import logging
import re

from flask import Blueprint, request, jsonify, send_file
from mysql.connector import errorcode
from utils.security import require_api_key
from utils.database import get_db_connection
from config import FILE_BAZA_PATH, FILE_EF_PATH 
from decimal import Decimal
from typing import Any

admin_bp = Blueprint('admin', __name__)
logger = logging.getLogger(__name__)

# Baza de date ETALON de unde copiem structura
SOURCE_DB = 'AVACONT_SURSA'
COMMON_DB = "AVACONT_COMUN"  # Baza comuna (care nu trebuie modificata in referinte)
DB_NAME_REGEX = re.compile(r'^[A-Za-z0-9_]+$')

def _validate_db_name(db_name):
    """
    Valideaza strict numele bazei de date.
    IMPORTANT:
    - db_name nu poate fi parametrizat prin %s in connect()
    - de aceea il validam whitelist-only
    """
    if not isinstance(db_name, str):
        raise ValueError("db_name invalid")

    db_name = db_name.strip()

    if not db_name:
        raise ValueError("db_name invalid")

    if not DB_NAME_REGEX.fullmatch(db_name):
        raise ValueError("db_name invalid")

    return db_name

# ==============================================================================
# 8. DOWNLOAD: BAZA.ACCDB
# ==============================================================================
@admin_bp.route('/api/admin/download_baza', methods=['GET'])
@require_api_key
def download_baza():
    filename = "defa_bz.accdb"
    base_dir = os.path.dirname(os.path.abspath(__file__))
    parent_dir = os.path.dirname(base_dir)
    file_path = os.path.join(parent_dir, filename)

    logger.info(f"Cerere download pentru: {filename}")

    if os.path.exists(file_path):
        try:
            # Trimitem fisierul ca atasament
            return send_file(file_path, as_attachment=True, download_name=filename)
        except Exception as e:
            logger.error(f"Eroare la trimitere fisier: {str(e)}")
            return jsonify({"error": str(e)}), 500
    else:
        logger.error(f"Fisierul {filename} NU a fost gasit la calea: {file_path}")
        return jsonify({"error": "File not found on server"}), 404	

# ==============================================================================
# 9. DOWNLOAD: EF.ACCDB
# ==============================================================================
@admin_bp.route('/api/admin/download_ef', methods=['GET'])
@require_api_key
def download_ef():
    filename = "defa_ef.accdb"
    base_dir = os.path.dirname(os.path.abspath(__file__))
    parent_dir = os.path.dirname(base_dir)
    file_path = os.path.join(parent_dir, filename)

    logger.info(f"Cerere download pentru: {filename}")

    if os.path.exists(file_path):
        try:
            # Trimitem fisierul ca atasament
            return send_file(file_path, as_attachment=True, download_name=filename)
        except Exception as e:
            logger.error(f"Eroare la trimitere fisier: {str(e)}")
            return jsonify({"error": str(e)}), 500
    else:
        logger.error(f"Fisierul {filename} NU a fost gasit la calea: {file_path}")
        return jsonify({"error": "File not found on server"}), 404	
    
# ==============================================================================
# 9.1. DOWNLOAD: Forexe.ACCDB
# ==============================================================================
@admin_bp.route('/api/admin/download_forexe', methods=['GET'])
@require_api_key
def download_forexe():
    filename = "FX_AN.accdb"
    base_dir = os.path.dirname(os.path.abspath(__file__))
    parent_dir = os.path.dirname(base_dir)
    file_path = os.path.join(parent_dir, filename)

    logger.info(f"Cerere download pentru: {filename}")

    if os.path.exists(file_path):
        try:
            # Trimitem fisierul ca atasament
            return send_file(file_path, as_attachment=True, download_name=filename)
        except Exception as e:
            logger.error(f"Eroare la trimitere fisier: {str(e)}")
            return jsonify({"error": str(e)}), 500
    else:
        logger.error(f"Fisierul {filename} NU a fost gasit la calea: {file_path}")
        return jsonify({"error": "File not found on server"}), 404	

# ==============================================================================
# 10. SETUP DATABASE PENTRU UN NOU CLIENT
# ==============================================================================
@admin_bp.route('/api/admin/check_db', methods=['POST'])
@require_api_key
def check_db():
    conn = None
    try:
        data = request.json
        if not data:
            return jsonify({"error": "No JSON data provided"}), 400

        dc_curent = data.get("dc_curent")
        if not dc_curent:
            return jsonify({"error": "Lipseste parametrul dc_curent"}), 400

        if not isinstance(dc_curent, str) or len(dc_curent) < 8:
            return jsonify({"error": "dc_curent invalid (min 8 caractere)"}), 400

        target_db = dc_curent[:8]

        user_conta = f"{target_db}_Contabil"
        user_admin = f"{target_db}_Administrator"

        def to_int_db(value: Any, default: int = 0) -> int:
            if value is None:
                return default
            if isinstance(value, int):
                return value
            if isinstance(value, bool):
                return int(value)
            if isinstance(value, Decimal):
                return int(value)
            if isinstance(value, float):
                return int(value)
            if isinstance(value, (bytes, bytearray, memoryview)):
                try:
                    s = bytes(value).decode("utf-8", "ignore").strip()
                    return int(s) if s else default
                except Exception:
                    return default
            if isinstance(value, str):
                try:
                    return int(value.strip())
                except Exception:
                    return default
            return default

        conn = get_db_connection()
        cursor = conn.cursor()

        # --- DB exists ---
        cursor.execute("SHOW DATABASES LIKE %s", (target_db,))
        db_exists = cursor.fetchone() is not None

        # --- Users exist (EXISTS) ---
        cursor.execute(
            "SELECT EXISTS(SELECT 1 FROM mysql.user WHERE user = %s)",
            (user_conta,)
        )
        row = cursor.fetchone()
        user_conta_exists = bool(to_int_db(tuple(row)[0], 0)) if row else False

        cursor.execute(
            "SELECT EXISTS(SELECT 1 FROM mysql.user WHERE user = %s)",
            (user_admin,)
        )
        row = cursor.fetchone()
        user_admin_exists = bool(to_int_db(tuple(row)[0], 0)) if row else False

        return jsonify({
            "status": "ok",
            "target_db": target_db,
            "db_exists": db_exists,
            "user_conta": user_conta,
            "user_conta_exists": user_conta_exists,
            "user_admin": user_admin,
            "user_admin_exists": user_admin_exists
        }), 200

    except Exception as e:
        logger.error(f"Eroare CRITICA in check_db: {str(e)}", exc_info=True)
        return jsonify({"error": str(e)}), 500
    finally:
        try:
            if conn and conn.is_connected():
                conn.close()
        except Exception:
            pass


@admin_bp.route('/api/admin/setup_db', methods=['POST'])
@require_api_key
def setup_database():
    try:
        data = request.json
        if not data:
            return jsonify({"error": "No JSON data provided"}), 400

        dc_curent = data.get('dc_curent')

        logger.info(f"Start SETUP DB pentru: {dc_curent}")

        if not dc_curent:
            return jsonify({"error": "Lipseste parametrul dc_curent"}), 400

        target_db = dc_curent[:8]

        user_conta = f"{target_db}_Contabil"
        user_admin = f"{target_db}_Administrator"
        pass_conta = f"{target_db}_Co"
        pass_admin = f"{target_db}_Ad"

        copied_tables = 0
        copied_views = 0

        # ===============================
        # Helpers
        # ===============================
        _re_ident = re.compile(r"^[A-Za-z0-9_]+$")

        def q_ident(name: str) -> str:
            # identifiers cannot be parameterized => validate + backtick-quote
            if not isinstance(name, str) or not _re_ident.match(name):
                raise ValueError(f"Invalid identifier: {name!r}")
            return f"`{name}`"

        def to_int_db(value: Any, default: int = 0) -> int:
            """
            Converteste un scalar venit din DB la int, fara int(x) pe tipuri necunoscute (Pylance friendly).
            """
            if value is None:
                return default

            if isinstance(value, bool):
                return 1 if value else 0

            if isinstance(value, int):
                return value

            if isinstance(value, Decimal):
                return int(value)

            if isinstance(value, float):
                return int(value)

            if isinstance(value, (bytes, bytearray, memoryview)):
                try:
                    s = bytes(value).decode("utf-8", "ignore").strip()
                    if not s:
                        return default
                    # here s is str, safe for int(s)
                    return int(s)
                except Exception:
                    return default

            if isinstance(value, str):
                s = value.strip()
                if not s:
                    return default
                try:
                    return int(s)
                except Exception:
                    return default

            # date/datetime/set/... -> default
            return default

        conn = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor()

            # --- 1. VERIFICARI INITIALE ---
            cursor.execute("SHOW DATABASES LIKE %s", (target_db,))
            row_db = cursor.fetchone()
            db_exists = (row_db is not None)

            # Verificam USER CONTA (EXISTS)  <<< FIXED
            cursor.execute(
                "SELECT EXISTS(SELECT 1 FROM mysql.user WHERE user = %s)",
                (user_conta,)
            )
            row_conta = cursor.fetchone()
            user_conta_exists = False
            if row_conta:
                user_conta_exists = bool(to_int_db(tuple(row_conta)[0], 0))

            # Verificam USER ADMIN (EXISTS)  <<< FIXED
            cursor.execute(
                "SELECT EXISTS(SELECT 1 FROM mysql.user WHERE user = %s)",
                (user_admin,)
            )
            row_admin = cursor.fetchone()
            user_admin_exists = False
            if row_admin:
                user_admin_exists = bool(to_int_db(tuple(row_admin)[0], 0))

            if db_exists and user_conta_exists and user_admin_exists:
                return jsonify({"status": "skipped", "message": "Baza si userii exista deja."}), 200

            # --- 2. CREARE SI CLONARE BAZA DE DATE ---
            if not db_exists:
                logger.info(f"Se creeaza baza de date {target_db}...")

                cursor.execute(
                    f"CREATE DATABASE IF NOT EXISTS {q_ident(target_db)} "
                    "CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci"
                )
                cursor.execute("SET FOREIGN_KEY_CHECKS=0")

                # B. Clonare TABELE
                cursor.execute(
                    f"SHOW FULL TABLES IN {q_ident(SOURCE_DB)} WHERE Table_type = 'BASE TABLE'"
                )
                tables = [str(tuple(row)[0]) for row in cursor.fetchall()]

                cursor.execute(f"USE {q_ident(target_db)}")

                for table in tables:
                    cursor.execute(f"SHOW CREATE TABLE {q_ident(SOURCE_DB)}.{q_ident(table)}")
                    ddl_row = cursor.fetchone()
                    if not ddl_row:
                        continue

                    raw_sql_val = tuple(ddl_row)[1]
                    if isinstance(raw_sql_val, bytes):
                        create_sql = raw_sql_val.decode('utf-8')
                    else:
                        create_sql = str(raw_sql_val)

                    new_sql = create_sql.replace(f"`{SOURCE_DB}`", f"`{target_db}`")
                    new_sql = new_sql.replace("CREATE TABLE", "CREATE TABLE IF NOT EXISTS", 1)

                    try:
                        cursor.execute(new_sql)
                        copied_tables += 1
                    except Exception as e_table:
                        logger.error(f"Eroare clonare tabela {table}: {e_table}")
                        raise e_table

                logger.info(f"Tabele clonate (cu FK): {copied_tables}")

                # C. Clonare VIEW-uri
                cursor.execute(
                    f"SHOW FULL TABLES IN {q_ident(SOURCE_DB)} WHERE Table_type = 'VIEW'"
                )
                views = [str(tuple(row)[0]) for row in cursor.fetchall()]

                for view in views:
                    cursor.execute(f"SHOW CREATE VIEW {q_ident(SOURCE_DB)}.{q_ident(view)}")
                    view_row = cursor.fetchone()

                    if view_row:
                        raw_view_val = tuple(view_row)[1]
                        if isinstance(raw_view_val, bytes):
                            create_view_sql = raw_view_val.decode('utf-8')
                        else:
                            create_view_sql = str(raw_view_val)

                        new_sql = create_view_sql.replace(f"`{SOURCE_DB}`", f"`{target_db}`")

                        try:
                            cursor.execute(f"USE {q_ident(target_db)}")
                            cursor.execute(new_sql)
                            copied_views += 1
                        except Exception as e_view:
                            logger.error(f"Eroare clonare View {view}: {e_view}")

                logger.info(f"View-uri clonate: {copied_views}")

                cursor.execute("SET FOREIGN_KEY_CHECKS=1")

            # --- 3. CONFIGURARE USERI ---
            try:
                cursor.execute("SET GLOBAL strict_password_validation = OFF")
            except:
                pass

            # keep same logic: create users only if missing
            if not user_conta_exists:
                cursor.execute(
                    f"CREATE USER IF NOT EXISTS '{user_conta}'@'%' IDENTIFIED BY %s",
                    (pass_conta,)
                )
                cursor.execute(
                    f"GRANT SELECT, INSERT, UPDATE, DELETE ON {q_ident(target_db)}.* TO '{user_conta}'@'%'"
                )

            if not user_admin_exists:
                cursor.execute(
                    f"CREATE USER IF NOT EXISTS '{user_admin}'@'%' IDENTIFIED BY %s",
                    (pass_admin,)
                )
                cursor.execute(
                    f"GRANT SELECT, INSERT, UPDATE, DELETE ON {q_ident(target_db)}.* TO '{user_admin}'@'%'"
                )

            cursor.execute("FLUSH PRIVILEGES")

            try:
                cursor.execute("SET GLOBAL strict_password_validation = ON")
            except:
                pass

            conn.commit()

            return jsonify({
                "status": "success",
                "message": "Baza de date clonata si configurata.",
                "details": f"Tables: {copied_tables}, Views: {copied_views}, Target: {target_db}"
            }), 200

        except Exception as e:
            logger.error(f"Eroare CRITICA in setup_database: {str(e)}", exc_info=True)
            return jsonify({"error": str(e)}), 500
        finally:
            if conn and conn.is_connected():
                conn.close()

    except Exception as outer_e:
        logger.error(f"Eroare CRITICA in setup_database: {str(outer_e)}", exc_info=True)
        return jsonify({"error": str(outer_e)}), 500


@admin_bp.route('/api/admin/ping_db', methods=['POST'])
@require_api_key
def ping_db():
    try:
        req_data = request.json
        db_name = _validate_db_name(req_data.get('db_name'))

        if not db_name:
            return jsonify({"error": "db_name invalid"}), 400

        conn = None
        try:
            conn = get_db_connection(db_name)
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            return jsonify({"status": "ok"}), 200
        except Exception as e:
            logger.error(f"Eroare ping DB {db_name}: {str(e)}")
            return jsonify({"error": str(e)}), 500
        finally:
            if conn: conn.close()

    except Exception as e:
        logger.error(f"Eroare generala ping_db: {str(e)}")
        return jsonify({"error": str(e)}), 500