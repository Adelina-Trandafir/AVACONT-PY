import logging
import mysql.connector
import re
from datetime import date, datetime
from flask import Blueprint, request, jsonify
from mysql.connector import Error
from utils.security import require_api_key
from routes.clasificatii_ss import ss_values   # slice 0075-00: Sector/Sursa/SS are written now
from config import DB_CONFIG  # Importam configurarea
from utils.database import get_kbot_connection
from typing import Any, Dict, cast

DB_NAME_REGEX = re.compile(r'^[A-Za-z0-9_]+$')

clasificatii_bp = Blueprint('clasificatii', __name__)
logger = logging.getLogger(__name__)

def get_db_connection(target_db_name):
    """Conectare dinamica la baza specificata, folosind user/pass din config."""
    if not DB_NAME_REGEX.match(target_db_name):
        raise ValueError("Nume baza de date invalid.")
    return mysql.connector.connect(
        **DB_CONFIG,            # Despachetam host, user, password din config
        database=target_db_name # Suprascriem baza de date
    )

# ============================================================
# HELPERS - VALIDARE / NORMALIZARE INPUT
# ============================================================

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


def _to_int(value, field_name, required=False):
    """
    Normalizeaza valori integer:
    - None / "" -> None
    - string numeric -> int
    - invalid -> ValueError clar
    """
    if value is None or value == "":
        if required:
            raise ValueError(f"Camp obligatoriu lipsa: {field_name}")
        return None

    try:
        return int(value)
    except Exception:
        raise ValueError(f"Camp invalid: {field_name}")


def _to_float(value, field_name, required=False):
    """
    Normalizeaza valori numerice:
    - None / "" -> None
    - string numeric -> float
    - invalid -> ValueError clar
    """
    if value is None or value == "":
        if required:
            raise ValueError(f"Camp obligatoriu lipsa: {field_name}")
        return None

    try:
        return float(value)
    except Exception:
        raise ValueError(f"Camp invalid: {field_name}")


def _to_str(value, field_name, required=False, strip_value=True):
    """
    Normalizeaza valori text:
    - None -> None
    - optional strip()
    - daca required=True, nu permite gol
    """
    if value is None:
        if required:
            raise ValueError(f"Camp obligatoriu lipsa: {field_name}")
        return None

    value = str(value)

    if strip_value:
        value = value.strip()

    if required and value == "":
        raise ValueError(f"Camp obligatoriu lipsa: {field_name}")

    return value

@clasificatii_bp.route('/api/clasificatii/get_clasificatii_ids', methods=['POST'])
@require_api_key
def get_clasificatii_ids():
    try:
        data = request.json
        
        # Extragem parametrii
        db_name = _validate_db_name(data.get('db_name'))
        unit_id = _to_int(data.get('unit_id'), 'unit_id', required=True)
        clasificatii_codes = data.get('clasificatii_codes')

        # 1. Validări
        if not db_name or not unit_id:
            return jsonify({"error": "Lipsesc parametrii 'db_name' sau 'unit_id'."}), 400

        if not clasificatii_codes or not isinstance(clasificatii_codes, list):
            return jsonify({"data": []}), 200

        # 2. Conectare (Acum functia exista!)
        conn = get_db_connection(db_name)
        try:
            cursor = conn.cursor(dictionary=True)

            # 3. Query
            placeholders = ', '.join(['%s'] * len(clasificatii_codes))
            
            sql = f"""
                SELECT IdClsf as IdClsfPY, IdClsfAcc as IdClsf
                FROM Clasificatii 
                WHERE IdUnitate = %s 
                AND IdClsfAcc IN ({placeholders})
            """

            params = [unit_id] + clasificatii_codes
            cursor.execute(sql, tuple(params))
            
            rows = cursor.fetchall()
            
            return jsonify({"data": rows}), 200

        finally:
            if conn.is_connected():
                conn.close()

    except Exception as e:
        logger.error(f"Eroare clasificatii: {e}")
        return jsonify({"error": str(e)}), 500

# ==============================================================================
# 4. INSERT CLASIFICATII
# ==============================================================================
@clasificatii_bp.route('/api/clasificatii/insert', methods=['POST'])
@require_api_key
def insert():
    try:
        req_data = request.json
        db_name = _validate_db_name(req_data.get('db_name'))
        unit_id = _to_int(req_data.get('unit_id'), 'unit_id', required=True)
        data_list = req_data.get('data')

        logger.info(f"Insert CLASIFICATII in {db_name}. Numar pachete: {len(data_list) if data_list else 0}")

        if not db_name or not data_list:
            return jsonify({"error": "Date invalide"}), 400

        conn = None
        try:
            conn = get_db_connection(db_name)
            cursor = conn.cursor()
            conn.start_transaction()
            
            # Sector/Sursa/SS became WRITTEN columns in slice 0075-00 (MariaDB refuses a
            # generated SS built from another column -- error 1901). derive_ss reproduces
            # the old generated expression exactly, so this route behaves as it always did.
            sql_structura = """INSERT INTO Clasificatii (IdClsfAcc, IdUnitate, Capitol, Subcapitol, Articol, Alineat, Denumire, Sector, Sursa, SS)
                               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"""
            
            # Slice 0102: every budget row has a DataInceput (the day it starts to apply). A row
            # brought from Access is the budget of the whole year, so it starts on 01.01.
            sql_buget = """INSERT INTO Clasificatii_Buget (IdClsf, IdUnitate, An, DataInceput, Trim1, Trim2, Trim3, Trim4) 
                           VALUES (%s, %s, %s, MAKEDATE(%s, 1), %s, %s, %s, %s)"""

            inserted_count = 0
            mapping = {}
            
            for item in data_list:
                s = item['structura']
                b = item['buget']
                
                val_s = (s['IdClsfAcc'], s['IdUnitate'], s['Capitol'], s['Subcapitol'], s['Articol'], s['Alineat'], s['Denumire']) + ss_values(s['Capitol'])
                cursor.execute(sql_structura, val_s)
                
                new_id = cursor.lastrowid
                
                val_b = (new_id, b['IdUnitate'], b['An'], b['An'], b['Trim1'], b['Trim2'], b['Trim3'], b['Trim4'])
                cursor.execute(sql_buget, val_b)

                mapping[s['IdClsfAcc']] = new_id
                
                inserted_count += 1

            conn.commit()
            logger.info(f"Insert CLASIFICATII Succes. Pachet: {inserted_count}")
            return jsonify({
                "status": "success",
                "count": inserted_count,
                "mapping": mapping
            }), 200

        except Exception as e:
            if conn: conn.rollback()
            logger.error(f"Eroare Insert Clasificatii: {str(e)}", exc_info=True)
            return jsonify({"error": str(e)}), 500
        finally:
            if conn: conn.close()
    except Exception as e:
        logger.error(f"Eroare Generala Clasificatii: {str(e)}")
        return jsonify({"error": str(e)}), 500

# ==============================================================================
# 5. MAPPING
# ==============================================================================
@clasificatii_bp.route('/api/clasificatii/mapping_clasificatii', methods=['POST'])
@require_api_key
def get_mapping():
    try:
        req_data = request.json
        db_name = _validate_db_name(req_data.get('db_name'))
        logger.info(f"Cerere Mapping pentru {db_name}")

        if not db_name:
            return jsonify({"error": "Missing db_name"}), 400

        conn = None
        try:
            conn = get_db_connection(db_name)
            cursor = conn.cursor(dictionary=True)
            
            cursor.execute("SELECT IdClsfAcc, IdClsf FROM Clasificatii")
            rows = cursor.fetchall()
            rows = cast(list[Dict[str, Any]], rows)
            
            mapping = {row['IdClsfAcc']: row['IdClsf'] for row in rows}
            logger.info(f"Mapping returnat: {len(mapping)} inregistrari")
            
            return jsonify(mapping), 200
        except Exception as e:
            logger.error(f"Eroare Mapping: {str(e)}", exc_info=True)
            return jsonify({"error": str(e)}), 500
        finally:
            if conn: conn.close()
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ==============================================================================
# 6. INSERT RECTIFICARI
# ==============================================================================
@clasificatii_bp.route('/api/clasificatii/rectificari', methods=['POST'])
@require_api_key
def save_rectificari():
    try:
        req_data = request.json
        db_name = _validate_db_name(req_data.get('db_name'))
        data_list = req_data.get('data')

        logger.info(f"Insert RECTIFICARI in {db_name}. Count: {len(data_list) if data_list else 0}")

        if not db_name or not data_list:
            return jsonify({"error": "Date invalide"}), 400

        conn = None
        try:
            conn = get_db_connection(db_name)
            cursor = conn.cursor()
            conn.start_transaction()
            
            sql = """INSERT INTO Clasificatii_Rectificari 
                     (IdClsf, Capitol, Subcapitol, Articol, Alineat, Data, Document, Trim1, Trim2, Trim3, Trim4) 
                     VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"""
            
            values = [(x['IdClsf'], x['Capitol'], x['Subcapitol'], x['Articol'], x['Alineat'], 
                       x['Data'], x['Document'], x['Trim1'], x['Trim2'], x['Trim3'], x['Trim4']) 
                      for x in data_list]
            
            cursor.executemany(sql, values)
            conn.commit()
            
            logger.info(f"Insert RECTIFICARI Succes. Rows: {cursor.rowcount}")
            return jsonify({"status": "success", "count": cursor.rowcount}), 200

        except Exception as e:
            if conn: conn.rollback()
            logger.error(f"Eroare Insert Rectificari: {str(e)}", exc_info=True)
            return jsonify({"error": str(e)}), 500
        finally:
            if conn: conn.close()
    except Exception as e:
        logger.error(f"Eroare Generala Rectificari: {str(e)}")
        return jsonify({"error": str(e)}), 500

# ==============================================================================
# 6.1. UPSERT RECTIFICARI
# ==============================================================================
@clasificatii_bp.route('/api/clasificatii/rectificari_upsert', methods=['POST'])
@require_api_key
def upsert_rectificari():

    try:
        req_data = request.json
        db_name = _validate_db_name(req_data.get('db_name'))
        data_list = req_data.get('data')

        logger.info(
            f"UPSERT RECTIFICARI in {db_name}. "
            f"Count: {len(data_list) if data_list else 0}"
        )

        if not db_name or not data_list:
            return jsonify({"error": "Date invalide"}), 400

        conn = None

        try:
            conn = get_db_connection(db_name)
            cursor = conn.cursor()

            conn.start_transaction()

            sql = """
                INSERT INTO Clasificatii_Rectificari
                (
                    IdClsf,
                    Capitol,
                    Subcapitol,
                    Articol,
                    Alineat,
                    Data,
                    Document,
                    Trim1,
                    Trim2,
                    Trim3,
                    Trim4
                )
                VALUES
                (
                    %s, %s, %s, %s, %s,
                    %s, %s,
                    %s, %s, %s, %s
                )
                ON DUPLICATE KEY UPDATE
                    Capitol    = VALUES(Capitol),
                    Subcapitol = VALUES(Subcapitol),
                    Articol    = VALUES(Articol),
                    Alineat    = VALUES(Alineat),
                    Trim1      = VALUES(Trim1),
                    Trim2      = VALUES(Trim2),
                    Trim3      = VALUES(Trim3),
                    Trim4      = VALUES(Trim4)
            """

            values = [
                (
                    x['IdClsf'],
                    x['Capitol'],
                    x['Subcapitol'],
                    x['Articol'],
                    x['Alineat'],
                    x['Data'],
                    x['Document'],
                    x['Trim1'],
                    x['Trim2'],
                    x['Trim3'],
                    x['Trim4']
                )
                for x in data_list
            ]

            cursor.executemany(sql, values)

            conn.commit()

            logger.info(
                f"UPSERT RECTIFICARI Succes. Rows: {cursor.rowcount}"
            )

            return jsonify({
                "status": "success",
                "count": len(data_list),
                "affected_rows": cursor.rowcount
            }), 200

        except Exception as e:
            if conn:
                conn.rollback()

            logger.error(
                f"Eroare UPSERT Rectificari: {str(e)}",
                exc_info=True
            )

            return jsonify({"error": str(e)}), 500

        finally:
            if conn:
                conn.close()

    except Exception as e:
        logger.error(
            f"Eroare Generala UPSERT Rectificari: {str(e)}"
        )

        return jsonify({"error": str(e)}), 500

# ============================================================
# ENDPOINT - UPSERT CLASIFICATII COMPLETE
# ============================================================
@clasificatii_bp.route('/api/clasificatii/clasificatii_complete_upsert', methods=['POST'])
@require_api_key
def save_clasificatii_complete_upsert():
    conn = None
    cursor = None

    try:
        # ------------------------------------------------------------
        # 1. Citire si validare request
        # ------------------------------------------------------------
        req_data = request.get_json(silent=True) or {}
        db_name = _validate_db_name(req_data.get("db_name"))
        data_list = req_data.get("data")

        if not isinstance(data_list, list) or len(data_list) == 0:
            logger.warning(
                "UPSERT CLASIFICATII respins - payload invalid. db_name=%s, data_type=%s",
                db_name,
                type(data_list).__name__
            )
            return jsonify({"error": "Date invalide"}), 400

        logger.info(
            "UPSERT CLASIFICATII start. db=%s, pachete=%s",
            db_name,
            len(data_list)
        )

        # ------------------------------------------------------------
        # 2. Conectare DB + start tranzactie
        # ------------------------------------------------------------
        try:
            conn = get_db_connection(db_name)
            cursor = conn.cursor(dictionary=True)
            conn.start_transaction()

            logger.info("UPSERT CLASIFICATII tranzactie pornita. db=%s", db_name)

        except Exception as e:
            logger.error(
                "UPSERT CLASIFICATII - eroare conectare/start_transaction. db=%s, err=%s",
                db_name,
                str(e),
                exc_info=True
            )
            return jsonify({"error": "Eroare conectare baza de date"}), 500

        # ------------------------------------------------------------
        # 3. SQL-uri
        # ------------------------------------------------------------
        sql_exists_clsf = """
            SELECT IdClsf
            FROM Clasificatii
            WHERE IdClsf = %s
            LIMIT 1
        """

        # Sector/Sursa/SS are WRITTEN since slice 0075-00 (see routes/clasificatii_ss.py).
        # They MUST appear in the ON DUPLICATE KEY UPDATE list as well: this statement can
        # change `Capitol`, and while the three were generated they followed it by
        # themselves. Leaving them out would keep the old sector on a re-classified row --
        # a row that still passes every foreign key and is wrong.
        sql_upsert_clsf_with_id = """
            INSERT INTO Clasificatii
            (
                IdClsf,
                IdClsfAcc,
                IdUnitate,
                Capitol,
                Subcapitol,
                Articol,
                Alineat,
                Denumire,
                Sector,
                Sursa,
                SS
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                IdClsfAcc = VALUES(IdClsfAcc),
                IdUnitate = VALUES(IdUnitate),
                Capitol = VALUES(Capitol),
                Subcapitol = VALUES(Subcapitol),
                Articol = VALUES(Articol),
                Alineat = VALUES(Alineat),
                Denumire = VALUES(Denumire),
                Sector = VALUES(Sector),
                Sursa = VALUES(Sursa),
                SS = VALUES(SS)
        """

        sql_insert_clsf_no_id = """
            INSERT INTO Clasificatii
            (
                IdClsfAcc,
                IdUnitate,
                Capitol,
                Subcapitol,
                Articol,
                Alineat,
                Denumire,
                Sector,
                Sursa,
                SS
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """

        # Slice 0102: a budget row is a VERSION that starts on DataInceput, and K-BOT's
        # «Clasificatii bugetare» window now owns the versions. This route only brings in the
        # budget of a classification that has NONE for the year yet (a version starting on 01.01);
        # a classification that already has one is left alone -- overwriting a version with
        # Access's current figures would erase the budget the DDF needs as it was.
        sql_exists_buget = """
            SELECT IdBuget
            FROM Clasificatii_Buget
            WHERE IdClsf = %s
              AND An = %s
            LIMIT 1
        """

        sql_insert_buget = """
            INSERT INTO Clasificatii_Buget
            (
                IdClsf,
                IdUnitate,
                An,
                DataInceput,
                Trim1,
                Trim2,
                Trim3,
                Trim4
            )
            VALUES (%s, %s, %s, MAKEDATE(%s, 1), %s, %s, %s, %s)
        """

        # ------------------------------------------------------------
        # 4. Contoare / mapping / protectie duplicate payload
        # ------------------------------------------------------------
        inserted_clsf = 0
        updated_clsf = 0
        inserted_buget = 0
        skipped_buget = 0
        mapping = {}

        seen_payload_keys = set()

        # ------------------------------------------------------------
        # 5. Procesare item cu item
        # ------------------------------------------------------------
        for idx, item in enumerate(data_list, start=1):
            try:
                if not isinstance(item, dict):
                    raise ValueError(f"Item invalid la pozitia {idx}: nu este obiect")

                s = item.get("structura")
                b = item.get("buget")

                if not isinstance(s, dict):
                    raise ValueError(f"Lipseste 'structura' la item-ul {idx}")

                if not isinstance(b, dict):
                    raise ValueError(f"Lipseste 'buget' la item-ul {idx}")

                # ----------------------------------------------------
                # 5.1 Normalizare / validare structura
                # ----------------------------------------------------
                s_clean = {
                    "IdClsf": _to_int(s.get("IdClsf"), f"data[{idx}].structura.IdClsf", required=False),
                    "IdClsfAcc": _to_int(s.get("IdClsfAcc"), f"data[{idx}].structura.IdClsfAcc", required=False),
                    "IdUnitate": _to_int(s.get("IdUnitate"), f"data[{idx}].structura.IdUnitate", required=True),
                    "Capitol": _to_str(s.get("Capitol"), f"data[{idx}].structura.Capitol", required=False),
                    "Subcapitol": _to_str(s.get("Subcapitol"), f"data[{idx}].structura.Subcapitol", required=False),
                    "Articol": _to_str(s.get("Articol"), f"data[{idx}].structura.Articol", required=False),
                    "Alineat": _to_str(s.get("Alineat"), f"data[{idx}].structura.Alineat", required=False),
                    "Denumire": _to_str(s.get("Denumire"), f"data[{idx}].structura.Denumire", required=False, strip_value=False),
                }

                # ----------------------------------------------------
                # 5.2 Normalizare / validare buget
                # ----------------------------------------------------
                b_clean = {
                    "IdUnitate": _to_int(b.get("IdUnitate"), f"data[{idx}].buget.IdUnitate", required=True),
                    "An": _to_int(b.get("An"), f"data[{idx}].buget.An", required=True),
                    "Trim1": _to_float(b.get("Trim1"), f"data[{idx}].buget.Trim1", required=False),
                    "Trim2": _to_float(b.get("Trim2"), f"data[{idx}].buget.Trim2", required=False),
                    "Trim3": _to_float(b.get("Trim3"), f"data[{idx}].buget.Trim3", required=False),
                    "Trim4": _to_float(b.get("Trim4"), f"data[{idx}].buget.Trim4", required=False),
                }

                # ----------------------------------------------------
                # 5.3 Protectie duplicate in acelasi payload
                # ----------------------------------------------------
                # Pentru randurile noi fara IdClsf, cheia interna ramane unica
                # per item ca sa nu blocam inserturi legitime.
                payload_key = (
                    s_clean["IdClsf"] if s_clean["IdClsf"] is not None else f"NEW_{idx}",
                    b_clean["An"]
                )

                if payload_key in seen_payload_keys:
                    raise ValueError(
                        f"Duplicat in payload la item-ul {idx}: "
                        f"IdClsf={s_clean['IdClsf']}, An={b_clean['An']}"
                    )

                seen_payload_keys.add(payload_key)

                # ----------------------------------------------------
                # 5.4 UPSERT Clasificatii
                # ----------------------------------------------------
                if s_clean["IdClsf"] is not None:
                    cursor.execute(sql_exists_clsf, (s_clean["IdClsf"],))
                    existed_before = cursor.fetchone() is not None

                    cursor.execute(
                        sql_upsert_clsf_with_id,
                        (
                            s_clean["IdClsf"],
                            s_clean["IdClsfAcc"],
                            s_clean["IdUnitate"],
                            s_clean["Capitol"],
                            s_clean["Subcapitol"],
                            s_clean["Articol"],
                            s_clean["Alineat"],
                            s_clean["Denumire"]
                        ) + ss_values(s_clean["Capitol"])
                    )

                    current_id_clsf = s_clean["IdClsf"]

                    if existed_before:
                        updated_clsf += 1
                        logger.debug(
                            "UPSERT CLASIFICATII item=%s -> UPDATE Clasificatii IdClsf=%s",
                            idx,
                            current_id_clsf
                        )
                    else:
                        inserted_clsf += 1
                        logger.debug(
                            "UPSERT CLASIFICATII item=%s -> INSERT Clasificatii IdClsf=%s",
                            idx,
                            current_id_clsf
                        )
                else:
                    cursor.execute(
                        sql_insert_clsf_no_id,
                        (
                            s_clean["IdClsfAcc"],
                            s_clean["IdUnitate"],
                            s_clean["Capitol"],
                            s_clean["Subcapitol"],
                            s_clean["Articol"],
                            s_clean["Alineat"],
                            s_clean["Denumire"]
                        ) + ss_values(s_clean["Capitol"])
                    )

                    current_id_clsf = cursor.lastrowid
                    inserted_clsf += 1

                    logger.debug(
                        "UPSERT CLASIFICATII item=%s -> INSERT Clasificatii fara IdClsf, nou IdClsf=%s",
                        idx,
                        current_id_clsf
                    )

                # ----------------------------------------------------
                # 5.5 Verificare existenta buget (slice 0102: versiuni cu DataInceput)
                # ----------------------------------------------------
                cursor.execute(sql_exists_buget, (current_id_clsf, b_clean["An"]))
                existed_buget_before = cursor.fetchone() is not None

                # ----------------------------------------------------
                # 5.6 INSERT Clasificatii_Buget doar daca anul nu are nicio versiune
                # ----------------------------------------------------
                if existed_buget_before:
                    skipped_buget += 1
                    logger.debug(
                        "UPSERT CLASIFICATII item=%s -> Clasificatii_Buget IdClsf=%s An=%s are deja "
                        "o versiune de buget; lasat neatins",
                        idx,
                        current_id_clsf,
                        b_clean["An"]
                    )
                else:
                    cursor.execute(
                        sql_insert_buget,
                        (
                            current_id_clsf,
                            b_clean["IdUnitate"],
                            b_clean["An"],
                            b_clean["An"],
                            b_clean["Trim1"],
                            b_clean["Trim2"],
                            b_clean["Trim3"],
                            b_clean["Trim4"]
                        )
                    )
                    inserted_buget += 1
                    logger.debug(
                        "UPSERT CLASIFICATII item=%s -> INSERT Clasificatii_Buget IdClsf=%s An=%s",
                        idx,
                        current_id_clsf,
                        b_clean["An"]
                    )

                # ----------------------------------------------------
                # 5.7 Mapping raspuns
                # ----------------------------------------------------
                mapping_key = str(s_clean["IdClsfAcc"]) if s_clean["IdClsfAcc"] is not None else f"row_{idx}"
                mapping[mapping_key] = current_id_clsf

            except ValueError as e:
                logger.warning(
                    "UPSERT CLASIFICATII invalid payload. db=%s, item=%s, err=%s",
                    db_name,
                    idx,
                    str(e)
                )
                raise

            except Exception as e:
                logger.error(
                    "UPSERT CLASIFICATII eroare procesare item. db=%s, item=%s, err=%s, item_data=%s",
                    db_name,
                    idx,
                    str(e),
                    item,
                    exc_info=True
                )
                raise

        # ------------------------------------------------------------
        # 6. Commit final
        # ------------------------------------------------------------
        try:
            conn.commit()
            logger.info(
                "UPSERT CLASIFICATII commit succes. db=%s, total=%s, "
                "clasificatii_inserted=%s, clasificatii_updated=%s, "
                "buget_inserted=%s, buget_skipped=%s",
                db_name,
                len(data_list),
                inserted_clsf,
                updated_clsf,
                inserted_buget,
                skipped_buget
            )
        except Exception as e:
            logger.error(
                "UPSERT CLASIFICATII eroare la commit. db=%s, err=%s",
                db_name,
                str(e),
                exc_info=True
            )
            raise

        return jsonify({
            "status": "success",
            "count": len(data_list),
            "clasificatii": {
                "inserted": inserted_clsf,
                "updated": updated_clsf
            },
            "buget": {
                "inserted": inserted_buget,
                "skipped": skipped_buget
            },
            "mapping": mapping
        }), 200

    except ValueError as e:
        if conn:
            try:
                conn.rollback()
                logger.info("UPSERT CLASIFICATII rollback executat dupa ValueError.")
            except Exception as rb_err:
                logger.error(
                    "UPSERT CLASIFICATII rollback esuat dupa ValueError. err=%s",
                    str(rb_err),
                    exc_info=True
                )

        return jsonify({"error": str(e)}), 400

    except Exception as e:
        if conn:
            try:
                conn.rollback()
                logger.info("UPSERT CLASIFICATII rollback executat dupa Exception.")
            except Exception as rb_err:
                logger.error(
                    "UPSERT CLASIFICATII rollback esuat dupa Exception. err=%s",
                    str(rb_err),
                    exc_info=True
                )

        logger.error(
            "UPSERT CLASIFICATII eroare generala. err=%s",
            str(e),
            exc_info=True
        )
        return jsonify({"error": str(e)}), 500

    finally:
        if cursor:
            try:
                cursor.close()
            except Exception as e:
                logger.warning("UPSERT CLASIFICATII cursor.close() a esuat: %s", str(e))

        if conn:
            try:
                conn.close()
            except Exception as e:
                logger.warning("UPSERT CLASIFICATII conn.close() a esuat: %s", str(e))


# ============================================================
# ENDPOINT - ACCESS -> K-BOT (slice 0103-05)
# ============================================================
# The VBA of the Access estate sends the budget and the rectifications of the classifications it
# changed to the NEW MariaDB server (DB_CONFIG_NEW, what K-BOT reads) -- NOT to DB_CONFIG, where the
# routes above write. Correlation: Access IdUnitate + Access IDClsf  ==  Clasificatii.IdUnitate +
# Clasificatii.IdClsfAcc. A classification that does not resolve to exactly one row is reported
# back and skipped, never guessed.
#
# Body:
#   { "db_name": "000_DEMO", "id_unitate": 76, "an": 2026,
#     "data_inceput": "2026-04-01",            (optional: the date the budget below starts to apply;
#                                               missing = the latest version of the year, else 01.01)
#     "clasificatii": [ { "IdClsfAcc": 123, "Trim1": 0, "Trim2": 0, "Trim3": 0, "Trim4": 0,
#                         "rectificari": [ { "Data": "2026-05-10", "Document": "HCL 12",
#                                            "Trim1": 0, "Trim2": 0, "Trim3": 0, "Trim4": 0 } ] } ] }
# Answer: { "status": "success", "budgets": n, "rectificari": n, "unmatched": [ids], "ambiguous": [ids] }
@clasificatii_bp.route('/api/clasificatii/sync_acc_kbot', methods=['POST'])
@require_api_key
def sync_acc_kbot():
    conn = None
    try:
        req_data = request.json or {}
        db_name = _validate_db_name(req_data.get('db_name'))
        id_unitate = _to_int(req_data.get('id_unitate'), 'id_unitate', required=True)
        an = _to_int(req_data.get('an'), 'an', required=True)
        items = req_data.get('clasificatii')
        if not isinstance(items, list) or not items:
            return jsonify({"error": "Lista clasificatii lipseste sau e goala."}), 400

        start_text = _to_str(req_data.get('data_inceput'), 'data_inceput')
        start = None
        if start_text:
            try:
                start = datetime.strptime(start_text[:10], '%Y-%m-%d').date()
            except ValueError:
                return jsonify({"error": "data_inceput nu este o data (aaaa-ll-zz)."}), 400
            if start.year != an:
                return jsonify({"error": "data_inceput nu este in anul " + str(an) + "."}), 400

        conn = get_kbot_connection(db_name)
        cursor = conn.cursor(dictionary=True, buffered=True)

        budgets = 0
        rectificari = 0
        unmatched = []
        ambiguous = []

        for item in items:
            acc_id = _to_int(item.get('IdClsfAcc'), 'IdClsfAcc', required=True)
            cursor.execute(
                "SELECT IDClsf, Capitol, Subcapitol, Articol, Alineat FROM Clasificatii "
                " WHERE IdUnitate = %s AND IdClsfAcc = %s", (id_unitate, acc_id))
            found = cursor.fetchall()
            if not found:
                unmatched.append(acc_id)
                continue
            if len(found) > 1:
                ambiguous.append(acc_id)
                continue
            clsf = found[0]
            id_clsf = int(clsf['IDClsf'])
            trims = [_to_float(item.get('Trim%d' % k), 'Trim%d' % k) for k in (1, 2, 3, 4)]

            # The budget: one version. Without a given start date the version in force is edited in
            # place (the latest of the year); a year with no version gets one on 01.01.
            day = start
            if day is None:
                cursor.execute(
                    "SELECT MAX(DataInceput) AS D FROM Clasificatii_Buget WHERE IdClsf = %s AND An = %s",
                    (id_clsf, an))
                last = cursor.fetchone()
                day = last['D'] if last and last['D'] is not None else date(an, 1, 1)
            cursor.execute(
                "INSERT INTO Clasificatii_Buget (IdClsf, IdUnitate, An, DataInceput, Trim1, Trim2, Trim3, Trim4) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                "ON DUPLICATE KEY UPDATE Trim1 = VALUES(Trim1), Trim2 = VALUES(Trim2), "
                "Trim3 = VALUES(Trim3), Trim4 = VALUES(Trim4)",
                (id_clsf, id_unitate, an, day, *trims))
            budgets += 1

            for r in (item.get('rectificari') or []):
                cursor.execute(
                    "INSERT INTO Clasificatii_Rectificari "
                    "(IdClsf, Capitol, Subcapitol, Articol, Alineat, Data, Document, Trim1, Trim2, Trim3, Trim4) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                    "ON DUPLICATE KEY UPDATE Capitol = VALUES(Capitol), Subcapitol = VALUES(Subcapitol), "
                    "Articol = VALUES(Articol), Alineat = VALUES(Alineat), Trim1 = VALUES(Trim1), "
                    "Trim2 = VALUES(Trim2), Trim3 = VALUES(Trim3), Trim4 = VALUES(Trim4)",
                    (id_clsf, clsf['Capitol'], clsf['Subcapitol'], clsf['Articol'], clsf['Alineat'],
                     _to_str(r.get('Data'), 'Data', required=True),
                     _to_str(r.get('Document'), 'Document', required=True),
                     *[_to_float(r.get('Trim%d' % k), 'Trim%d' % k) for k in (1, 2, 3, 4)]))
                rectificari += 1

        conn.commit()
        logger.info("SYNC ACC->KBOT %s unit=%s: budgets=%s rectificari=%s unmatched=%s ambiguous=%s",
                    db_name, id_unitate, budgets, rectificari, len(unmatched), len(ambiguous))
        return jsonify({"status": "success", "budgets": budgets, "rectificari": rectificari,
                        "unmatched": unmatched, "ambiguous": ambiguous}), 200

    except ValueError as e:
        if conn:
            conn.rollback()
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error("SYNC ACC->KBOT eroare: %s", str(e), exc_info=True)
        return jsonify({"error": str(e)}), 500
    finally:
        if conn:
            try:
                conn.close()
            except Exception as e:
                logger.warning("SYNC ACC->KBOT conn.close() a esuat: %s", str(e))
