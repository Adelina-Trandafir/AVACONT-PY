import logging
import base64
import io
import re
import os
import json
import pandas as pd
import numpy as np
import threading
from flask import Blueprint, request, jsonify, send_file
from utils.security import require_api_key
from utils.helpers import sanitize_header

tools_bp = Blueprint('tools', __name__)
logger = logging.getLogger(__name__)

_manifest_lock = threading.Lock()

MANIFEST_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '../cache/vbnet/manifest.json')
)
CACHE_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '../cache/vbnet')
)

# ==============================================================================
# PROTECȚIE REDOS
# ==============================================================================
REGEX_TIMEOUT_MS = 100  # millisecunde
DANGEROUS_PATTERNS = [
    r'\([^\)]*[+*][^\)]*\)[+*]'
]
# DANGEROUS_PATTERNS = [
#     r'(\w+\*)+',      # (a+)+ 
#     r'(\w+)+\w+',     # (.*)*x
#     r'\(.*\)\*',      # (.*)* 
# ]

def _load_manifest():
    """Încarcă manifestul {nume_fisier: versiune} din cache/vbnet/manifest.json."""
    with open(MANIFEST_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)

def _write_manifest(manifest):
    """Scriere atomică a manifestului (temp + rename)."""
    tmp = MANIFEST_PATH + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, MANIFEST_PATH)

def _safe_cache_path(filename):
    """Cale absolută în CACHE_DIR dacă filename e basename curat; altfel None (anti path-traversal)."""
    if not filename or filename != os.path.basename(filename):
        return None
    path = os.path.abspath(os.path.join(CACHE_DIR, filename))
    if os.path.commonpath([path, CACHE_DIR]) != CACHE_DIR:
        return None
    return path

def is_safe_regex(pattern):
    """Verifică dacă un pattern regex e sigur împotriva ReDoS."""
    for danger in DANGEROUS_PATTERNS:
        if re.search(danger, pattern):
            return False
    # Test pe un string lung
    try:
        test_str = 'a' * 10000
        re.search(pattern, test_str, timeout=0.1)  # type: ignore # Python 3.11+
    except TimeoutError:
        return False
    except:
        pass  # Python < 3.11 nu are timeout
    return True

# ==============================================================================
# PARSING ȘI CONVERSII
# ==============================================================================
def try_parse_value(val):
    """
    Convertește string în tipul potrivit (int/float/string).
    Elimină ghilimele și spații.
    """
    if pd.isna(val):
        return None
        
    val = str(val).strip()
    
    # Elimină ghilimele externe
    if len(val) >= 2:
        if (val[0] == '"' and val[-1] == '"') or (val[0] == "'" and val[-1] == "'"):
            val = val[1:-1]
    
    # String gol -> None pentru compatibilitate NULL
    if val == '':
        return None
    
    # Încearcă numeric
    try:
        if '.' in val or ',' in val:
            return float(val.replace(',', '.'))
        return int(val)
    except ValueError:
        return val

def split_conditions_smart(filter_string):
    """
    Split după ' AND ', dar DOAR dacă nu suntem în interiorul ghilimelelor.
    Ex: "Col = 'Tom and Jerry' AND Price > 100" 
        -> ["Col = 'Tom and Jerry'", "Price > 100"]
    """
    conditions = []
    current = ''
    in_quotes = False
    quote_char = None
    i = 0
    
    while i < len(filter_string):
        char = filter_string[i]
        
        # Detectare intrare/ieșire din ghilimele
        if char in ('"', "'"):
            if not in_quotes:
                in_quotes = True
                quote_char = char
            elif char == quote_char:
                in_quotes = False
                quote_char = None
            current += char
            i += 1
            continue
        
        # Dacă suntem în ghilimele, adaugă orice caracter
        if in_quotes:
            current += char
            i += 1
            continue
        
        # Verifică dacă următorii caractere formează ' AND '
        if i + 5 <= len(filter_string):
            snippet = filter_string[i:i+5].upper()
            # Verifică ' AND ' cu spații înainte și după
            if snippet == ' AND ' or (i == 0 and snippet.startswith('AND ')):
                # Am găsit un AND valid (în afara ghilimelelor)
                if current.strip():
                    conditions.append(current.strip())
                current = ''
                i += 5  # Sari peste ' AND '
                continue
        
        current += char
        i += 1
    
    # Adaugă ultima condiție
    if current.strip():
        conditions.append(current.strip())
    
    return conditions

def smart_compare(series, operator, target_value):
    """
    Compară o serie pandas cu o valoare, gestionând conversii de tip automate.
    Case insensitive pentru stringuri.
    """
    # NULL handling pentru stringuri goale
    if target_value is None or target_value == '':
        if operator == '=':
            return series.isna() | (series.astype(str).str.strip() == '')
        elif operator == '!=':
            return series.notna() & (series.astype(str).str.strip() != '')
        else:
            # Operatori numerici exclud NULL
            return pd.Series(False, index=series.index)
    
    # Încearcă comparație directă (funcționează pentru numeric, date)
    try:
        if operator == '=':
            # Case insensitive pentru stringuri
            if isinstance(target_value, str) and series.dtype == 'object':
                return series.astype(str).str.lower() == target_value.lower()
            return series == target_value
        elif operator == '!=':
            if isinstance(target_value, str) and series.dtype == 'object':
                return series.astype(str).str.lower() != target_value.lower()
            return series != target_value
        elif operator == '>':
            return series > target_value
        elif operator == '<':
            return series < target_value
        elif operator == '>=':
            return series >= target_value
        elif operator == '<=':
            return series <= target_value
    except:
        pass
    
    # Fallback: convertește ambele la string și compară case insensitive
    series_str = series.astype(str).str.lower()
    target_str = str(target_value).lower()
    
    if operator == '=':
        return series_str == target_str
    elif operator == '!=':
        return series_str != target_str
    else:
        # Operatori numerici - încearcă conversie forțată
        try:
            series_num = pd.to_numeric(series, errors='coerce')
            target_num = float(target_value)
            
            if operator == '>':
                return series_num > target_num
            elif operator == '<':
                return series_num < target_num
            elif operator == '>=':
                return series_num >= target_num
            elif operator == '<=':
                return series_num <= target_num
        except:
            # Nu se poate converti - returnează False
            return pd.Series(False, index=series.index)

def sql_like_to_regex(pattern):
    """
    Convertește un pattern SQL LIKE (%, _) în regex.
    Ex: "John%" -> "^John.*$"
    """
    # Escape caracterele speciale regex (dar nu % și _)
    escaped = re.escape(pattern)
    # Înlocuiește % și _ escapate cu regex echivalent
    escaped = escaped.replace(r'\%', '.*')
    escaped = escaped.replace(r'\_', '.')
    return f'^{escaped}$'

def parse_in_values(values_str):
    """
    Parsează valorile dintr-un IN(...), gestionând corect ghilimelele.
    Ex: '1, "text, cu virgula", 3' -> [1, "text, cu virgula", 3]
    """
    values = []
    current = ''
    in_quotes = False
    quote_char = None
    
    for char in values_str:
        if char in ('"', "'") and not in_quotes:
            in_quotes = True
            quote_char = char
        elif char == quote_char and in_quotes:
            in_quotes = False
            quote_char = None
        elif char == ',' and not in_quotes:
            if current.strip():
                values.append(try_parse_value(current))
            current = ''
        else:
            current += char
    
    # Ultima valoare
    if current.strip():
        values.append(try_parse_value(current))
    
    return values

# ==============================================================================
# APLICARE FILTRE
# ==============================================================================
def apply_complex_filter(df, filter_string):
    """
    Parsează și aplică filtre complexe pe DataFrame.
    Sintaxă: Col1 LIKE "pattern" AND Col2 IN (1,2,3) AND Col3 >= 100
    """
    # Split după AND (protejat de stringuri)
    conditions = split_conditions_smart(filter_string)
    
    # Mapare coloane pentru case insensitive
    columns_ci = {c.lower(): c for c in df.columns}

    mask = pd.Series(True, index=df.index)

    for cond in conditions:
        cond = cond.strip()
        if not cond:
            continue
        
        # -----------------------------------------------------------------------
        # 1. LIKE (cu suport SQL % și regex pur)
        # -----------------------------------------------------------------------
        match_like = re.search(
            r'^(.+?)\s+LIKE\s+[\'"](.+?)[\'"]$',
            cond,
            re.IGNORECASE
        )
        if match_like:
            col, pattern = match_like.groups()
            col_key = col.strip().lower()

            if col_key not in columns_ci:
                logger.warning(f"Coloana '{col.strip()}' nu există. Condiție ignorată.")
                continue

            real_col = columns_ci[col_key]

            # Detectează dacă e SQL LIKE (%, _) sau regex pur
            if '%' in pattern or '_' in pattern:
                regex_pattern = sql_like_to_regex(pattern)
            else:
                regex_pattern = pattern

            # Verificare ReDoS
            if not is_safe_regex(regex_pattern):
                raise ValueError(f"Pattern regex periculos (ReDoS): {pattern}")

            try:
                mask &= df[real_col].astype(str).str.contains(
                    regex_pattern,
                    case=False,
                    regex=True,
                    na=False
                )
            except Exception as e:
                raise ValueError(f"Regex invalid în LIKE: {pattern}. Eroare: {e}")

            continue
        
        # -----------------------------------------------------------------------
        # 2. IN
        # -----------------------------------------------------------------------
        match_in = re.search(
            r'^(.+?)\s+IN\s+\((.+)\)$',
            cond,
            re.IGNORECASE
        )
        if match_in:
            col, values_str = match_in.groups()
            col_key = col.strip().lower()

            if col_key not in columns_ci:
                logger.warning(f"Coloana '{col.strip()}' nu există. Condiție ignorată.")
                continue

            real_col = columns_ci[col_key]
            parsed_values = parse_in_values(values_str)

            # Case insensitive pentru stringuri
            if df[real_col].dtype == 'object':
                col_lower = df[real_col].astype(str).str.lower()
                values_lower = [
                    str(v).lower() if v is not None else None
                    for v in parsed_values
                ]
                mask &= col_lower.isin(values_lower)
            else:
                mask &= df[real_col].isin(parsed_values)

            continue
        
        # -----------------------------------------------------------------------
        # 3. OPERATORI (>=, <=, !=, =, >, <)
        # -----------------------------------------------------------------------
        match_op = re.search(
            r'^(.+?)\s*(>=|<=|!=|=|>|<)\s*(.+)$',
            cond
        )
        if match_op:
            col, op, val_str = match_op.groups()
            col_key = col.strip().lower()
            val = try_parse_value(val_str)

            if col_key not in columns_ci:
                logger.warning(f"Coloana '{col.strip()}' nu există. Condiție ignorată.")
                continue

            real_col = columns_ci[col_key]

            try:
                mask &= smart_compare(df[real_col], op, val)
            except Exception as e:
                logger.error(f"Eroare comparație '{cond}': {e}")
                raise ValueError(f"Eroare la aplicarea condiției: {cond}")

            continue
        
        # Sintaxă necunoscută
        logger.warning(f"Condiție necunoscută: '{cond}'")

    return df[mask]

# ==============================================================================
# ENDPOINT
# ==============================================================================
@tools_bp.route('/api/tools/process_excel', methods=['POST'])
@require_api_key
def process_excel_base64():
    try:
        data = request.json
        file_content_b64 = data.get('file_base64')
        
        # Parametri
        header_rows = int(data.get('header_rows', 1)) 
        skip_rows = int(data.get('skipFirstNRows', 0))
        skip_footer = int(data.get('skipLastNRows', 0))
        skip_first_cols = int(data.get('skipFirstNColumns', 0))
        skip_last_cols = int(data.get('skipLastNColumns', 0))
        
        # Filtre
        col_to_filter = data.get('col_to_filter')
        filter_pattern = data.get('filter')   
        complex_filter = data.get('complex_filter')

        col_to_filter = (
            col_to_filter.strip()
            if isinstance(col_to_filter, str) and col_to_filter.strip()
            else None
        )

        filter_pattern = (
            filter_pattern.strip()
            if isinstance(filter_pattern, str) and filter_pattern.strip()
            else None
        )

        if complex_filter:
            logger.info(f"Processing Excel. ComplexFilter: {complex_filter}")

        elif col_to_filter and filter_pattern:
            logger.info(
                f"Processing Excel. SimpleFilter: Col={col_to_filter}, Pattern={filter_pattern}"
            )

        else:
            logger.info("Processing Excel. No filters applied")

        if not file_content_b64:
            return jsonify({"error": "Lipseste continutul fisierului (Base64)"}), 400

        # 1. Decodare Base64
        try:
            file_bytes = base64.b64decode(file_content_b64)
        except Exception as e:
            return jsonify({"error": f"Base64 invalid: {str(e)}"}), 400

        # 2. Citire cu Pandas
        try:
            if header_rows >= 2:
                header_indices = list(range(header_rows))
                header_arg = header_indices
            else:
                header_arg = 0

            df = pd.read_excel(
                io.BytesIO(file_bytes), 
                header=header_arg, 
                skiprows=skip_rows,
                skipfooter=skip_footer,
                dtype=str 
            )
        except Exception as e:
            logger.error(f"Eroare fatala la citire Excel: {e}")
            return jsonify({"error": f"Nu s-a putut citi fisierul Excel. {str(e)}"}), 500

        # 2.5 Slicing Coloane
        if skip_first_cols > 0 or skip_last_cols > 0:
            total_cols = df.shape[1]
            start_col = skip_first_cols
            if skip_last_cols > 0:
                end_col = total_cols - skip_last_cols
            else:
                end_col = total_cols
            
            if start_col >= end_col:
                return jsonify({"error": "Skip columns a eliminat toate datele."}), 400
                
            df = df.iloc[:, start_col:end_col]

        # 3. Sanitizare Coloane (logica ta existentă)
        new_columns = []
        if header_rows >= 2:
            for col_tuple in df.columns:
                parts = []
                for part in col_tuple:
                    part_str = str(part).strip()
                    if "Unnamed" not in part_str and part_str.lower() != "nan" and part_str != "":
                        parts.append(part_str)
                
                if not parts: 
                    clean_name = f"Col_{len(new_columns) + 1}"
                else:
                    full_name = "__".join(parts)
                    clean_name = sanitize_header(full_name)
                    clean_name = re.sub(r'__+', '!', clean_name)
                
                if not clean_name: 
                    clean_name = f"Col_{len(new_columns) + 1}"
                new_columns.append(clean_name)
        else:
            for col in df.columns:
                part_str = str(col).strip()
                if "Unnamed" in part_str: 
                    clean_name = f"Col_{len(new_columns) + 1}"
                else:
                    clean_name = sanitize_header(part_str)
                    if not clean_name: 
                        clean_name = f"Col_{len(new_columns) + 1}"
                new_columns.append(clean_name)

        df.columns = new_columns

        # =========================================================================
        # 4. APLICARE FILTRE
        # =========================================================================
        if complex_filter:
            try:
                df = apply_complex_filter(df, complex_filter)
                logger.info(f"Complex filter applied. Rows remaining: {len(df)}")
            except ValueError as e:
                return jsonify({"error": str(e)}), 400
            except Exception as e:
                logger.error(f"Eroare filtrare complexa: {e}", exc_info=True)
                return jsonify({"error": f"Eroare in aplicarea filtrului: {str(e)}"}), 500

        elif col_to_filter and filter_pattern:
            # se intră DOAR dacă ambele sunt valide
            if col_to_filter in df.columns:
                if not is_safe_regex(filter_pattern):
                    return jsonify({"error": "Pattern regex periculos (ReDoS)"}), 400

                try:
                    df = df[df[col_to_filter].astype(str).str.contains(
                        filter_pattern,
                        case=False,
                        regex=True,
                        na=False
                    )]
                except Exception as e:
                    return jsonify({"error": f"Regex invalid: {str(e)}"}), 400
            else:
                logger.warning(f"Filter column '{col_to_filter}' not found. Ignoring filter.")

        else:
            logger.info("No valid filters provided. Returning full dataset.")

        # 5. Export
        df = df.fillna('')
        result_list = df.to_dict(orient='records')

        return jsonify({
            "status": "success",
            "count": len(result_list),
            "data": result_list
        }), 200

    except Exception as e:
        logger.error(f"Eroare Procesare Excel API: {str(e)}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@tools_bp.route('/api/tools/say_hello', methods=['POST'])
@require_api_key
def say_hello():
    try:
        data = request.json
        name = data.get('name', 'World')
        logger.info(f"Received say_hello request with name: {name}")
        return jsonify({"message": f"Hello, {name}!"}), 200
    except Exception as e:
        logger.error(f"Eroare in say_hello: {str(e)}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@tools_bp.route('/api/tools/pdfium_dll', methods=['GET'])
@require_api_key
def download_pdfium_dll():
    dll_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '../cache/vbnet/pdfium.dll'))
    
    if not os.path.exists(dll_path):
        logger.error(f"pdfium.dll nu există la calea: {dll_path}")
        return jsonify({"error": "Fișierul nu a fost găsit pe server."}), 404

    try:
        return send_file(
            dll_path,
            mimetype='application/octet-stream',
            as_attachment=True,
            download_name='pdfium.dll'
        )
    except Exception as e:
        logger.error(f"Eroare la trimiterea pdfium.dll: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@tools_bp.route('/api/tools/check_update', methods=['GET'])
@require_api_key
def check_update():
    filename = request.args.get('file', type=str)
    client_version = request.args.get('version', type=int)

    if not filename or client_version is None:
        return jsonify({"error": "Parametrii 'file' și 'version' sunt obligatorii."}), 400

    try:
        manifest = _load_manifest()
    except FileNotFoundError:
        logger.error(f"Manifestul nu există la calea: {MANIFEST_PATH}")
        return jsonify({"error": "Manifest indisponibil pe server."}), 500
    except (json.JSONDecodeError, OSError) as e:
        logger.error(f"Eroare la citirea manifestului: {e}", exc_info=True)
        return jsonify({"error": "Manifest invalid pe server."}), 500

    if filename not in manifest:
        return jsonify({"error": "Fișier necunoscut."}), 404

    server_version = int(manifest[filename])

    if server_version <= client_version:
        return ('', 204)  # client la zi, nimic de trimis

    dll_path = _safe_cache_path(filename)
    if not dll_path or not os.path.exists(dll_path):
        logger.error(f"Fișier listat în manifest dar absent pe disc: {filename}")
        return jsonify({"error": "Fișierul nu a fost găsit pe server."}), 404

    try:
        response = send_file(
            dll_path,
            mimetype='application/octet-stream',
            as_attachment=True,
            download_name=filename
        )
        response.headers['X-File-Version'] = str(server_version)
        return response
    except Exception as e:
        logger.error(f"Eroare la trimiterea fișierului {filename}: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@tools_bp.route('/api/tools/check_updates', methods=['POST'])
@require_api_key
def check_updates():
    payload = request.get_json(silent=True)
    if not isinstance(payload, list):
        return jsonify({"error": "Corpul trebuie să fie o listă JSON de {name, version}."}), 400

    try:
        manifest = _load_manifest()
    except FileNotFoundError:
        logger.error(f"Manifestul nu există la calea: {MANIFEST_PATH}")
        return jsonify({"error": "Manifest indisponibil pe server."}), 500
    except (json.JSONDecodeError, OSError) as e:
        logger.error(f"Eroare la citirea manifestului: {e}", exc_info=True)
        return jsonify({"error": "Manifest invalid pe server."}), 500

    updates = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        name = item.get('name')
        version = item.get('version')
        if not name or name not in manifest:
            continue
        try:
            client_version = int(version)
        except (TypeError, ValueError):
            continue
        server_version = int(manifest[name])
        if server_version > client_version:
            path = _safe_cache_path(name)
            if path and os.path.exists(path):
                updates.append({"name": name, "version": server_version})

    return jsonify({"updates": updates})

@tools_bp.route('/api/tools/upload', methods=['POST'])
@require_api_key
def upload_file():
    uploaded = request.files.get('file')
    if uploaded is None or not uploaded.filename:
        return jsonify({"error": "Lipsește fișierul ('file')."}), 400

    raw_version = request.form.get('version')
    try:
        version = int(raw_version)
    except (TypeError, ValueError):
        return jsonify({"error": "Câmpul 'version' (MAJOR, întreg) e obligatoriu."}), 400
    if version < 0:
        return jsonify({"error": "Versiunea trebuie să fie >= 0."}), 400

    dest_path = _safe_cache_path(uploaded.filename)
    if dest_path is None:
        return jsonify({"error": "Nume de fișier invalid."}), 400

    name = os.path.basename(uploaded.filename)

    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with _manifest_lock:
            try:
                manifest = _load_manifest()
            except FileNotFoundError:
                manifest = {}
            # manifest corupt (JSONDecodeError) -> se propagă la 500, nu-l stricăm
            old_version = manifest.get(name)

            tmp_path = dest_path + '.upload.tmp'
            uploaded.save(tmp_path)
            os.replace(tmp_path, dest_path)

            manifest[name] = version
            _write_manifest(manifest)
    except Exception as e:
        logger.error(f"Eroare la upload {uploaded.filename}: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

    logger.info(f"Upload reușit: {name} v{version} (anterior: {old_version})")
    return jsonify({
        "name": name,
        "version": version,
        "previous_version": old_version,
        "status": "ok"
    })