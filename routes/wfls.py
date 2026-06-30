import logging
import os
import re
import json
import base64
from flask import Blueprint, request, jsonify, send_file
from utils.security import require_api_key

wfl_bp = Blueprint('wfls', __name__)
logger = logging.getLogger(__name__)

# Calea catre folderul parinte unde se afla folderul WFL si fisierul de versiuni
def get_wfl_dir():
    base_dir = os.path.dirname(os.path.abspath(__file__))   # .../routes
    app_root = os.path.dirname(base_dir)                    # radacina aplicatiei
    return os.path.join(app_root, "cache", "wfl_templates")

# ==============================================================================
# HELPER: Extragere versiune din header-ul fisierului .wfl
# ==============================================================================
# Cauta primul comentariu de forma: <!-- V.4 - 26/03/2026
_WFL_VERSION_RE = re.compile(r"<!--\s*[Vv]\.?\s*(\d+)")

def parse_wfl_version(file_path):
    """
    Returneaza (version:int|None, reason:str, preview:str).
    reason: 'ok' | 'no_match' | 'error: ...'
    preview: primele caractere citite (pentru diagnostic).
    """
    try:
        with open(file_path, 'rb') as f:
            raw = f.read(2048)
        # Detectam encoding grosier: UTF-16 are multi byti nuli
        if raw[:2] in (b'\xff\xfe', b'\xfe\xff'):
            head = raw.decode('utf-16', errors='replace')
        else:
            head = raw.decode('utf-8-sig', errors='replace')  # -sig scoate BOM-ul UTF-8
        match = _WFL_VERSION_RE.search(head)
        preview = head[:120].replace('\n', ' ').replace('\r', '')
        if match:
            return int(match.group(1)), 'ok', preview
        return None, 'no_match', preview
    except Exception as e:
        return None, f'error: {e}', ''

# ==============================================================================
# HELPER: Parsare fisier versiuni custom
# ==============================================================================
def load_server_versions(file_path):
    """
    Citeste un fisier JSON valid care contine o lista de obiecte.
    Returneaza un dictionar: {'nume_fisier': versiune}
    """
    versions = {}
    if not os.path.exists(file_path):
        logger.error(f"Fisierul de versiuni nu exista: {file_path}")
        return versions

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            # Acum putem folosi json.load direct pentru ca formatul e valid
            data_list = json.load(f)
            
            # Convertim lista in dictionar pentru cautare rapida
            for item in data_list:
                if "FileName" in item and "Version" in item:
                    versions[item["FileName"]] = item["Version"]
                    
    except json.JSONDecodeError as e:
        logger.error(f"Eroare de sintaxa JSON in fisier: {e}")
    except Exception as e:
        logger.error(f"Eroare la citirea versiunilor server: {str(e)}")
    
    return versions

# ==============================================================================
# ENDPOINT: DOWNLOAD VERSIUNI (existent, doar am ajustat path-ul sa fie dinamic)
# ==============================================================================
@wfl_bp.route('/api/wfls/versiuni', methods=['GET'])
@require_api_key
def versiuni():
    wfl_dir = get_wfl_dir()
    filename = "versiuni_wfl.txt"
    file_path = os.path.join(wfl_dir, filename)

    logger.info(f"Cerere download pentru: {filename}")

    if os.path.exists(file_path):
        try:
            return send_file(file_path, as_attachment=True, download_name=filename)
        except Exception as e:
            logger.error(f"Eroare la trimitere fisier: {str(e)}")
            return jsonify({"error": str(e)}), 500
    else:
        logger.error(f"Fisierul {filename} NU a fost gasit la calea: {file_path}")
        return jsonify({"error": "File not found on server"}), 404  

# ==============================================================================
# ENDPOINT NOU: CHECK & DOWNLOAD UPDATES
# ==============================================================================
@wfl_bp.route('/api/wfls/check_updates', methods=['POST'])
@require_api_key
def check_updates():
    """
    Primeste un JSON: [{"FileName": "...", "Version": 1}, ...]
    Returneaza un JSON cu fisierele care au versiune mai mare pe server.
    Format raspuns:
    {
        "status": "success",
        "updates": [
            {
                "FileName": "nume.wfl",
                "Version": 2,
                "Content": "base64_string..."
            }
        ]
    }
    """
    try:
        client_data = request.json
        if not isinstance(client_data, list):
             return jsonify({"error": "Payload-ul trebuie sa fie o lista de obiecte JSON"}), 400

        wfl_dir = get_wfl_dir()
        versions_file_path = os.path.join(wfl_dir, "versiuni_wfl.txt")

        # 1. Incarcam versiunile de pe server
        server_versions = load_server_versions(versions_file_path) # Dict {'nume': int}
        
        # 2. Transformam datele clientului intr-un dict pentru cautare usoara
        client_versions_map = {item.get('FileName'): item.get('Version') for item in client_data}

        files_to_send = []

        # 3. Comparam versiunile
        # Iteram prin ce avem noi pe server (sursa adevarului)
        for fname, server_ver in server_versions.items():
            client_ver = client_versions_map.get(fname)

            # Conditia de update: 
            # Clientul nu are fisierul deloc (None) SAU Clientul are versiune mai mica
            if client_ver is None or server_ver > client_ver:
                
                full_path = os.path.join(wfl_dir, fname)
                
                if os.path.exists(full_path):
                    try:
                        # Citim fisierul binar
                        with open(full_path, "rb") as f:
                            file_content = f.read()
                        
                        # Il codam Base64 ca sa poata fi trimis in JSON
                        encoded_content = base64.b64encode(file_content).decode('utf-8')

                        files_to_send.append({
                            "FileName": fname,
                            "Version": server_ver,
                            "Content": encoded_content
                        })
                        logger.info(f"Adaugat la update: {fname} (Server: {server_ver} > Client: {client_ver})")
                    
                    except Exception as e:
                        logger.error(f"Eroare citire fisier pentru update {fname}: {e}")
                else:
                    logger.warning(f"Fisierul {fname} apare in versiuni_wfl.txt dar nu exista fizic pe disk!")

        return jsonify({
            "status": "success",
            "count": len(files_to_send),
            "updates": files_to_send
        }), 200

    except Exception as e:
        logger.error(f"Eroare la check_updates: {str(e)}")
        return jsonify({"error": str(e)}), 500


# ==============================================================================
# ENDPOINT NOU: REBUILD versiuni_wfl.txt din header-ele fisierelor .wfl
# ==============================================================================
@wfl_bp.route('/api/wfls/rebuild_versions', methods=['GET'])
@require_api_key
def rebuild_versions():
    """
    Scaneaza folderul WFL, citeste versiunea din header-ul fiecarui .wfl
    si (re)scrie versiuni_wfl.txt — regenerare completa (lista reflecta
    exact ce exista pe disk acum).
    Fisierele fara header de versiune sunt sarite.
    """
    try:
        wfl_dir = get_wfl_dir()
        logger.info(f"[REBUILD] Scanez folderul: {wfl_dir}")   # vezi exact ce cale rezolva
        versions_file_path = os.path.join(wfl_dir, "versiuni_wfl.txt")

        if not os.path.isdir(wfl_dir):
            logger.error(f"Folderul WFL nu exista: {wfl_dir}")
            return jsonify({"error": "Folderul WFL nu exista pe server"}), 404

        detected = []
        skipped = []

        for fname in sorted(os.listdir(wfl_dir)):
            if not fname.lower().endswith(".wfl"):
                continue
            full_path = os.path.join(wfl_dir, fname)
            if not os.path.isfile(full_path):
                continue

            version, reason, preview = parse_wfl_version(full_path)
            if version is None:
                logger.warning(f"Sarit ({reason}): {fname} | preview='{preview}'")
                skipped.append({"FileName": fname, "reason": reason, "preview": preview})
                continue

            detected.append({"FileName": fname, "Version": version})
            logger.info(f"Detectat: {fname} -> V.{version}")

        # Scriere atomica: temp-file -> rename (acelasi pattern ca in rest)
        tmp_path = versions_file_path + ".tmp"
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(detected, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, versions_file_path)

        logger.info(f"versiuni_wfl.txt regenerat: {len(detected)} fisiere, {len(skipped)} sarite")

        return jsonify({
            "status": "success",
            "count": len(detected),
            "versions": detected,
            "skipped": skipped
        }), 200

    except Exception as e:
        logger.error(f"Eroare la rebuild_versions: {str(e)}", exc_info=True)
        return jsonify({"error": str(e)}), 500