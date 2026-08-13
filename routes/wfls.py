import logging
import os
import re
import json
import base64
from flask import Blueprint, request, jsonify, send_file
from utils.security import require_api_key
import threading
 
_versions_lock = threading.Lock()
 
VERSIONS_FILENAME = "versiuni_wfl.txt"

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
# HELPER: cale sigura in folderul WFL (anti path-traversal + doar .wfl)
# ==============================================================================
def _safe_wfl_path(filename):
    """
    Returneaza calea absoluta in cache/wfl_templates daca numele e un basename
    curat cu extensia .wfl; altfel None.
    """
    if not filename or filename != os.path.basename(filename):
        return None
    if not filename.lower().endswith(".wfl"):
        return None
 
    wfl_dir = os.path.abspath(get_wfl_dir())
    path = os.path.abspath(os.path.join(wfl_dir, filename))
    if os.path.commonpath([path, wfl_dir]) != wfl_dir:
        return None
    return path
    
# ==============================================================================
# HELPER: citire / scriere lista de versiuni (pastreaza formatul JSON existent)
# ==============================================================================
def _versions_file_path():
    return os.path.join(get_wfl_dir(), VERSIONS_FILENAME)
 
 
def _read_versions_list(path):
    """Returneaza lista de {FileName, Version}. Lista goala daca fisierul lipseste."""
    if not os.path.exists(path):
        logger.warning(f"{VERSIONS_FILENAME} nu exista la {path} — se porneste de la lista goala")
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"{VERSIONS_FILENAME} nu contine o lista JSON")
    return data
 
 
def _write_versions_list(path, data):
    """Scriere atomica: .tmp -> os.replace (acelasi pattern ca la rebuild_versions)."""
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)
 
 
def _upsert_version(fname, version):
    """
    Actualizeaza (sau insereaza) intrarea pentru fname in versiuni_wfl.txt.
    Se apeleaza DOAR sub _versions_lock. Sorteaza dupa FileName ca sa ramana
    consistent cu ce produce rebuild_versions.
    """
    path = _versions_file_path()
    data = _read_versions_list(path)
 
    for item in data:
        if item.get("FileName") == fname:
            item["Version"] = version
            break
    else:
        data.append({"FileName": fname, "Version": version})
 
    data.sort(key=lambda x: x.get("FileName", ""))
    _write_versions_list(path, data)
 
 
# ==============================================================================
# HELPER: verdictul comparatiei de versiuni
# ==============================================================================
def _upload_verdict(server_ver, client_ver):
    """
    'new'     -> fisierul nu e in versiuni_wfl.txt         => upload direct
    'block'   -> server > client                           => refuz
    'confirm' -> server == client                          => cere confirmare
    'upgrade' -> server < client                           => upload direct
    """
    if server_ver is None:
        return "new"
    if server_ver > client_ver:
        return "block"
    if server_ver == client_ver:
        return "confirm"
    return "upgrade"
 
 
_VERDICT_MESSAGES = {
    "new":     "Fisier nou — se incarca direct.",
    "upgrade": "Versiune mai noua decat cea de pe server — se incarca direct.",
    "confirm": "Versiune identica cu cea de pe server — necesita confirmare pentru suprascriere.",
    "block":   "Serverul are o versiune MAI NOUA — incarcare blocata.",
}
 
 
def _validate_client_version(raw):
    """Returneaza (version:int|None, error:str|None). Refuza -1 / 0 / non-numeric."""
    try:
        version = int(raw)
    except (TypeError, ValueError):
        return None, "Campul 'version' (intreg) e obligatoriu."
    if version <= 0:
        return None, "Versiune invalida (fisierul nu are header V.x valid)."
    return version, None

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

# ==============================================================================
# ENDPOINT: CHECK UPLOAD — verdict inainte de a trimite continutul
# ==============================================================================
@wfl_bp.route('/api/wfls/check_upload', methods=['POST'])
@require_api_key
def check_upload():
    """
    Primeste: [{"FileName": "x.wfl", "Version": 3}, ...]  (sau un singur obiect)
    Returneaza verdictul per fisier, fara sa scrie nimic pe disk.
 
    {
      "status": "success",
      "results": [
        {"FileName": "x.wfl", "ClientVersion": 3, "ServerVersion": 2,
         "verdict": "upgrade", "needs_confirm": false, "allowed": true,
         "message": "..."}
      ]
    }
    """
    try:
        payload = request.get_json(silent=True)
        if isinstance(payload, dict):
            payload = [payload]
        if not isinstance(payload, list):
            return jsonify({"error": "Payload-ul trebuie sa fie o lista de obiecte JSON"}), 400
 
        server_versions = load_server_versions(_versions_file_path())
        results = []
 
        for item in payload:
            if not isinstance(item, dict):
                continue
 
            fname = item.get("FileName")
            client_ver, err = _validate_client_version(item.get("Version"))
 
            if not fname or _safe_wfl_path(fname) is None:
                results.append({
                    "FileName": fname, "verdict": "invalid", "allowed": False,
                    "needs_confirm": False, "message": "Nume de fisier invalid (se accepta doar .wfl)."
                })
                continue
 
            if err:
                results.append({
                    "FileName": fname, "verdict": "invalid", "allowed": False,
                    "needs_confirm": False, "message": err
                })
                continue
 
            server_ver = server_versions.get(fname)
            verdict = _upload_verdict(server_ver, client_ver)
 
            results.append({
                "FileName": fname,
                "ClientVersion": client_ver,
                "ServerVersion": server_ver,
                "verdict": verdict,
                "allowed": verdict != "block",
                "needs_confirm": verdict == "confirm",
                "message": _VERDICT_MESSAGES[verdict],
            })
 
            logger.info(
                f"[CHECK_UPLOAD] {fname} | client={client_ver} server={server_ver} -> {verdict}"
            )
 
        return jsonify({"status": "success", "count": len(results), "results": results}), 200
 
    except Exception as e:
        logger.error(f"Eroare la check_upload: {str(e)}", exc_info=True)
        return jsonify({"error": str(e)}), 500
 
 
# ==============================================================================
# ENDPOINT: UPLOAD — scrie fisierul in cache/wfl_templates + actualizeaza versiunea
# ==============================================================================
@wfl_bp.route('/api/wfls/upload', methods=['POST'])
@require_api_key
def upload_wfl():
    """
    Multipart form-data:
      file    : fisierul .wfl
      version : intreg (MAJOR, citit din header-ul V.x de catre client)
      confirm : 'true' / '1' — necesar DOAR cand versiunea e egala cu cea de pe server
 
    Coduri:
      200 -> incarcat, versiuni_wfl.txt actualizat
      400 -> parametri invalizi
      403 -> blocat (serverul are versiune mai noua)
      409 -> necesita confirmare (versiune egala, confirm lipsa)
      500 -> eroare interna
    """
    uploaded = request.files.get('file')
    if uploaded is None or not uploaded.filename:
        return jsonify({"error": "Lipseste fisierul ('file')."}), 400
 
    fname = os.path.basename(uploaded.filename)
    dest_path = _safe_wfl_path(fname)
    if dest_path is None:
        return jsonify({"error": "Nume de fisier invalid (se accepta doar .wfl)."}), 400
 
    client_ver, err = _validate_client_version(request.form.get('version'))
    if err:
        return jsonify({"error": err}), 400
 
    confirm = str(request.form.get('confirm', '')).strip().lower() in ('true', '1', 'yes', 'da')
 
    try:
        wfl_dir = get_wfl_dir()
        os.makedirs(wfl_dir, exist_ok=True)
 
        with _versions_lock:
            server_versions = load_server_versions(_versions_file_path())
            server_ver = server_versions.get(fname)
            verdict = _upload_verdict(server_ver, client_ver)
 
            if verdict == "block":
                logger.warning(
                    f"[UPLOAD] BLOCAT {fname} | client={client_ver} < server={server_ver}"
                )
                return jsonify({
                    "status": "blocked",
                    "FileName": fname,
                    "ClientVersion": client_ver,
                    "ServerVersion": server_ver,
                    "verdict": verdict,
                    "message": _VERDICT_MESSAGES[verdict],
                }), 403
 
            if verdict == "confirm" and not confirm:
                logger.info(
                    f"[UPLOAD] CONFIRMARE NECESARA {fname} | versiune identica ({client_ver})"
                )
                return jsonify({
                    "status": "confirm_required",
                    "FileName": fname,
                    "ClientVersion": client_ver,
                    "ServerVersion": server_ver,
                    "verdict": verdict,
                    "message": _VERDICT_MESSAGES[verdict],
                }), 409
 
            if verdict == "new" and os.path.exists(dest_path):
                logger.warning(
                    f"[UPLOAD] {fname} exista pe disk dar lipseste din {VERSIONS_FILENAME} "
                    f"— se suprascrie si se adauga intrarea"
                )
 
            # --- scriere atomica a fisierului ---
            tmp_path = dest_path + '.upload.tmp'
            uploaded.save(tmp_path)
            os.replace(tmp_path, dest_path)
 
            # --- actualizare incrementala versiuni_wfl.txt ---
            _upsert_version(fname, client_ver)
 
        logger.info(
            f"[UPLOAD] OK {fname} v{client_ver} (anterior: {server_ver}) | verdict={verdict} "
            f"| confirm={confirm}"
        )
        return jsonify({
            "status": "ok",
            "FileName": fname,
            "Version": client_ver,
            "PreviousVersion": server_ver,
            "verdict": verdict,
            "message": "Fisier incarcat si versiune actualizata.",
        }), 200
 
    except Exception as e:
        logger.error(f"Eroare la upload_wfl {fname}: {str(e)}", exc_info=True)
        try:
            tmp_path = dest_path + '.upload.tmp'
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass
        return jsonify({"error": str(e)}), 500