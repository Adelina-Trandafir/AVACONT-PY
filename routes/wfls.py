import logging
import os
import json
import base64
from flask import Blueprint, request, jsonify, send_file
from utils.security import require_api_key

wfl_bp = Blueprint('wfls', __name__)
logger = logging.getLogger(__name__)

# Calea catre folderul parinte unde se afla folderul WFL si fisierul de versiuni
def get_wfl_base_path():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(base_dir) # Folderul radacina al aplicatiei

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
    base_path = get_wfl_base_path()
    filename = "versiuni_wfl.txt"
    file_path = os.path.join(base_path, "WFL", filename)

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

        base_path = get_wfl_base_path()
        wfl_dir = os.path.join(base_path, "WFL")
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