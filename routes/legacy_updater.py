# routes/legacy_updater.py
"""
TEMPORARY bridge for clients built before the security plan S2 retired the shared X-Api-Key API.

Those clients (the old updater and the file uploader) can only talk to:

    GET  /api/tools/check_update    ?file=<name>&version=<major>   -> the file, 204 or 404
    POST /api/tools/check_updates   [{name, version}, ...]         -> {"updates": [...]}
    POST /api/tools/upload          multipart file + version       -> {"status": "ok", ...}

until they have updated themselves. Once every client runs the new updater (it asks
/api/update/* first and falls back here only when that fails), set LEGACY_UPDATER_ENABLED = False in the server's config.py -- every route below then answers 404 again, as if it was never registered.

POST /api/tools/upload needs its own key (X-Upload-Key, LEGACY_UPLOAD_KEY in config.py; see _guard_upload), not the shared API_KEY.

Only these routes come back (the three above, GET /api/mfp/get_ef_token for the old EF.EXE, GET /api/ftp/get_avacont_version and /api/ftp/download_avacont for the old updater, and POST /api/ftp/init_upload, upload_chunk and finalize_upload for the old backup uploader).

The key is read from the server's config.py (`API_KEY`) at request time and NEVER imported at module
load, so a server whose config.py no longer defines it simply refuses (503) instead of failing to start.
config.py is host-only and is not touched.
"""
import hashlib
import hmac
import json
import logging
import os
import shutil
import threading
import uuid

from flask import Blueprint, jsonify, request, send_file
from werkzeug.utils import secure_filename

logger = logging.getLogger(__name__)

legacy_updater_bp = Blueprint("legacy_updater", __name__)

def _enabled():
    """config.LEGACY_UPDATER_ENABLED, checked on every request (config.py itself is loaded at service start); missing = True. False retires the bridge."""
    try:
        import config
        return bool(getattr(config, "LEGACY_UPDATER_ENABLED", True))
    except Exception:
        return True

_manifest_lock = threading.Lock()

CACHE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../cache/vbnet"))
MANIFEST_PATH = os.path.join(CACHE_DIR, "manifest.json")


def _legacy_key():
    try:
        import config
        return str(getattr(config, "API_KEY", "") or "")
    except Exception:
        return ""


def _guard():
    """None when the call may proceed, otherwise the (response, status) to return."""
    if not _enabled():
        return jsonify({"error": "Not found"}), 404
    expected = _legacy_key()
    if not expected:
        logger.error("API_KEY is not set in config.py: the legacy updater routes refuse everything")
        return jsonify({"error": "Unavailable"}), 503
    given = request.headers.get("X-Api-Key", "")
    if given and hmac.compare_digest(given.encode("utf-8"), expected.encode("utf-8")):
        return None
    logger.warning(f"LEGACY UPDATER: access refused, IP {request.remote_addr}, {request.path}")
    return jsonify({"error": "Unauthorized"}), 401


def _guard_upload():
    """Guard of POST /api/tools/upload, the only route that PUBLISHES update files (every old client installs them).
    It needs its own key, LEGACY_UPLOAD_KEY in the server's config.py, sent as X-Upload-Key. There is NO fallback to
    API_KEY: that key is compiled into every client, so accepting it here would change nothing. A missing key = 503."""
    if not _enabled():
        return jsonify({"error": "Not found"}), 404
    try:
        import config
        expected = str(getattr(config, "LEGACY_UPLOAD_KEY", "") or "")
    except Exception:
        expected = ""
    if not expected:
        logger.error("LEGACY_UPLOAD_KEY is not set in config.py: POST /api/tools/upload refuses everything")
        return jsonify({"error": "Unavailable"}), 503
    given = request.headers.get("X-Upload-Key", "")
    if given and hmac.compare_digest(given.encode("utf-8"), expected.encode("utf-8")):
        return None
    logger.warning(f"LEGACY UPDATER: upload refused, IP {request.remote_addr}")
    return jsonify({"error": "Unauthorized"}), 401


def _load_manifest():
    with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_manifest(manifest):
    tmp = MANIFEST_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, MANIFEST_PATH)


def _safe_cache_path(filename):
    """Absolute path inside CACHE_DIR when filename is a clean basename, else None (path traversal)."""
    if not filename or filename != os.path.basename(filename):
        return None
    path = os.path.abspath(os.path.join(CACHE_DIR, filename))
    if os.path.commonpath([path, CACHE_DIR]) != CACHE_DIR:
        return None
    return path


@legacy_updater_bp.route("/api/tools/check_update", methods=["GET"])
def check_update():
    denied = _guard()
    if denied:
        return denied

    filename = request.args.get("file", type=str)
    client_version = request.args.get("version", type=int)
    if not filename or client_version is None:
        return jsonify({"error": "Parametrii 'file' si 'version' sunt obligatorii."}), 400

    try:
        manifest = _load_manifest()
    except FileNotFoundError:
        logger.error(f"Manifest missing at {MANIFEST_PATH}")
        return jsonify({"error": "Manifest indisponibil pe server."}), 500
    except (json.JSONDecodeError, OSError) as e:
        logger.error(f"Manifest unreadable: {e}", exc_info=True)
        return jsonify({"error": "Manifest invalid pe server."}), 500

    if filename not in manifest:
        return jsonify({"error": "Fisier necunoscut."}), 404

    server_version = int(manifest[filename])
    if server_version <= client_version:
        return ("", 204)

    path = _safe_cache_path(filename)
    if not path or not os.path.exists(path):
        logger.error(f"File in manifest but not on disk: {filename}")
        return jsonify({"error": "Fisierul nu a fost gasit pe server."}), 404

    try:
        response = send_file(path, mimetype="application/octet-stream", as_attachment=True, download_name=filename)
        response.headers["X-File-Version"] = str(server_version)
        logger.info(f"LEGACY UPDATER: sent {filename} v{server_version} (client v{client_version})")
        return response
    except Exception as e:
        logger.error(f"Error sending {filename}: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@legacy_updater_bp.route("/api/tools/check_updates", methods=["POST"])
def check_updates():
    denied = _guard()
    if denied:
        return denied

    payload = request.get_json(silent=True)
    if not isinstance(payload, list):
        return jsonify({"error": "Corpul trebuie sa fie o lista JSON de {name, version}."}), 400

    try:
        manifest = _load_manifest()
    except FileNotFoundError:
        logger.error(f"Manifest missing at {MANIFEST_PATH}")
        return jsonify({"error": "Manifest indisponibil pe server."}), 500
    except (json.JSONDecodeError, OSError) as e:
        logger.error(f"Manifest unreadable: {e}", exc_info=True)
        return jsonify({"error": "Manifest invalid pe server."}), 500

    updates = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not name or name not in manifest:
            continue
        try:
            client_version = int(item.get("version"))
        except (TypeError, ValueError):
            continue
        server_version = int(manifest[name])
        if server_version > client_version:
            path = _safe_cache_path(name)
            if path and os.path.exists(path):
                updates.append({"name": name, "version": server_version})

    return jsonify({"updates": updates})


@legacy_updater_bp.route("/api/tools/upload", methods=["POST"])
def upload_file():
    denied = _guard_upload()
    if denied:
        return denied

    uploaded = request.files.get("file")
    if uploaded is None or not uploaded.filename:
        return jsonify({"error": "Lipseste fisierul ('file')."}), 400

    try:
        version = int(request.form.get("version"))
    except (TypeError, ValueError):
        return jsonify({"error": "Campul 'version' (MAJOR, intreg) e obligatoriu."}), 400
    if version == 0:
        return jsonify({"error": "Versiunea trebuie sa fie <> 0."}), 400

    dest_path = _safe_cache_path(uploaded.filename)
    if dest_path is None:
        return jsonify({"error": "Nume de fisier invalid."}), 400

    name = os.path.basename(uploaded.filename)

    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with _manifest_lock:
            try:
                manifest = _load_manifest()
            except FileNotFoundError:
                manifest = {}
            old_version = manifest.get(name)

            tmp_path = dest_path + ".upload.tmp"
            uploaded.save(tmp_path)
            os.replace(tmp_path, dest_path)

            manifest[name] = version
            _write_manifest(manifest)
    except Exception as e:
        logger.error(f"Upload error {uploaded.filename}: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

    logger.info(f"LEGACY UPDATER: upload {name} v{version} (previous: {old_version})")
    return jsonify({"name": name, "version": version, "previous_version": old_version, "status": "ok"})


# ---------------------------------------------------------------------------
# The AVACONT (Access) package is read from the FTPS store, as before. FTPS_* and FTP_BASE_DIR come
# from the server's config.py; if they are gone the call answers 500 with the reason.
# ---------------------------------------------------------------------------
def _ftps():
    import config
    from ftplib import FTP_TLS
    ftps = FTP_TLS()
    ftps.connect(config.FTPS_HOST, config.FTPS_PORT, timeout=30)
    ftps.login(config.FTPS_USER, config.FTPS_PASS)
    ftps.prot_p()
    ftps.set_pasv(True)
    ftps.cwd(config.FTP_BASE_DIR)
    return ftps


@legacy_updater_bp.route("/api/ftp/get_avacont_version", methods=["GET"])
def get_avacont_version():
    denied = _guard()
    if denied:
        return denied

    role = request.args.get("role", "").strip()
    if not role:
        return jsonify(success=False, message="Lipseste parametrul 'role'"), 400

    ftps = None
    try:
        from io import BytesIO
        ftps = _ftps()
        buffer = BytesIO()
        ftps.retrbinary("RETR versiune_avacont.txt", buffer.write)
        data = json.loads(buffer.getvalue().decode("utf-8"))
        if role not in data:
            return jsonify(success=False, message=f"Rolul '{role}' nu exista"), 404
        return jsonify(success=True, role=role, version=data[role]), 200
    except Exception as e:
        logger.exception("Error reading versiune_avacont.txt")
        return jsonify(success=False, message=f"Eroare FTP: {str(e)}"), 500
    finally:
        if ftps:
            try:
                ftps.quit()
            except Exception:
                pass


@legacy_updater_bp.route("/api/ftp/download_avacont", methods=["GET"])
def download_avacont():
    denied = _guard()
    if denied:
        return denied

    version = request.args.get("version", "").strip()
    if not version.isdigit():
        return jsonify(success=False, message="Parametru 'version' invalid. Trebuie doar cifre."), 400

    filename = f"avacont_{version}.zip"
    ftps = None
    try:
        from io import BytesIO
        ftps = _ftps()
        if filename not in ftps.nlst():
            return jsonify(success=False, message=f"Fisier '{filename}' nu exista."), 404
        buffer = BytesIO()
        ftps.retrbinary(f"RETR {filename}", buffer.write)
        buffer.seek(0)
        return send_file(buffer, as_attachment=True, download_name=filename, mimetype="application/zip")
    except Exception as e:
        logger.exception("Error downloading the AVACONT package")
        return jsonify(success=False, message=f"Eroare FTP: {str(e)}"), 500
    finally:
        if ftps:
            try:
                ftps.quit()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Chunked upload of a backup to the FTPS store (the old Avacont_Uploader):
# init_upload -> upload_chunk (x N) -> finalize_upload. Sessions live in this process's memory,
# exactly as before, so one upload must stay on one server process.
# ---------------------------------------------------------------------------
_upload_sessions = {}


def _cfg():
    import config
    return config


def _get_ftps():
    from ftplib import FTP_TLS
    cfg = _cfg()
    ftps = FTP_TLS()
    ftps.connect(cfg.FTPS_HOST, cfg.FTPS_PORT, timeout=30)
    ftps.login(cfg.FTPS_USER, cfg.FTPS_PASS)
    ftps.prot_p()
    ftps.set_pasv(True)
    return ftps


def _navigate_ftp_path(ftps, ftp_path):
    """CWD to ftp_path from FTP_BASE_DIR, creating missing folders."""
    ftps.cwd(_cfg().FTP_BASE_DIR)
    if ftp_path:
        for folder in ftp_path.split("/"):
            if not folder:
                continue
            try:
                ftps.cwd(folder)
            except Exception:
                ftps.mkd(folder)
                ftps.cwd(folder)
                logger.info("FTP folder created: %s", folder)


def _cleanup_temp_dir(temp_dir):
    try:
        shutil.rmtree(temp_dir, ignore_errors=True)
    except Exception:
        pass


@legacy_updater_bp.route('/api/ftp/init_upload', methods=['POST'])
def init_upload():
    denied = _guard()
    if denied:
        return denied

    data = request.json or {}
    filename  = secure_filename(data.get("filename", ""))
    ftp_path  = data.get("ftp_path", "").strip("/")
    total_size = data.get("total_size")

    if not filename or not total_size:
        return jsonify(success=False, message="Parametri invalizi: filename și total_size sunt obligatorii"), 400

    try:
        total_size = int(total_size)
        if total_size <= 0:
            raise ValueError
    except (ValueError, TypeError):
        return jsonify(success=False, message="total_size invalid"), 400

    upload_id = str(uuid.uuid4())
    temp_dir  = os.path.join(_cfg().TEMP_UPLOAD_DIR, upload_id)
    os.makedirs(temp_dir, exist_ok=True)

    # Save metadata server-side for later validation
    _upload_sessions[upload_id] = {
        "filename":   filename,
        "ftp_path":   ftp_path,
        "total_size": total_size,
    }

    return jsonify(success=True, upload_id=upload_id), 200


@legacy_updater_bp.route('/api/ftp/upload_chunk', methods=['POST'])
def upload_chunk():
    denied = _guard()
    if denied:
        return denied

    upload_id    = request.form.get("upload_id", "").strip()
    chunk_index  = request.form.get("chunk_index")
    total_chunks = request.form.get("total_chunks")
    chunk_sha256 = request.form.get("chunk_sha256", "").strip().lower()

    if not upload_id or chunk_index is None or not chunk_sha256:
        return jsonify(success=False, message="Parametri lipsă"), 400

    if 'file' not in request.files:
        return jsonify(success=False, message="Fișier lipsă"), 400

    # Validate session
    if upload_id not in _upload_sessions:
        return jsonify(success=False, message="Upload session necunoscută"), 400

    temp_dir = os.path.join(_cfg().TEMP_UPLOAD_DIR, upload_id)
    if not os.path.isdir(temp_dir):
        return jsonify(success=False, message="Upload session expirată"), 400

    file_bytes = request.files['file'].read()

    # Checksum verification
    actual_sha = hashlib.sha256(file_bytes).hexdigest()
    if actual_sha != chunk_sha256:
        return jsonify(success=False, message=f"Checksum invalid pe chunk {chunk_index}"), 400

    chunk_path = os.path.join(temp_dir, f"{int(chunk_index):05d}.part")
    with open(chunk_path, "wb") as f:
        f.write(file_bytes)

    return jsonify(success=True), 200


@legacy_updater_bp.route('/api/ftp/finalize_upload', methods=['POST'])
def finalize_upload():
    denied = _guard()
    if denied:
        return denied

    req_data     = request.json or {}
    upload_id    = req_data.get("upload_id", "").strip()
    total_chunks = req_data.get("total_chunks")
    final_sha256 = req_data.get("final_sha256", "").strip().lower()
    override     = req_data.get("override", False) is True or req_data.get("override") == 1

    # ── Validate session ────────────────────────────────────────────────────
    session = _upload_sessions.get(upload_id)
    if not session:
        logger.warning("finalize_upload respins: session necunoscuta | upload_id=%s", upload_id)
        return jsonify(success=False, message="Upload session necunoscută"), 400

    filename = session["filename"]
    ftp_path = session["ftp_path"]

    if not total_chunks or not final_sha256:
        logger.warning("finalize_upload respins: parametri lipsa | upload_id=%s", upload_id)
        return jsonify(success=False, message="Parametri lipsă: total_chunks, final_sha256"), 400

    try:
        total_chunks = int(total_chunks)
    except (ValueError, TypeError):
        return jsonify(success=False, message="total_chunks invalid"), 400

    logger.info(
        "finalize_upload request | file=%s | path=%s | chunks=%d | override=%s",
        filename, ftp_path, total_chunks, override
    )

    temp_dir   = os.path.join(_cfg().TEMP_UPLOAD_DIR, upload_id)
    final_file = os.path.join(temp_dir, filename)

    try:
        # ── 1. Assemble chunks ──────────────────────────────────────────────
        sha = hashlib.sha256()
        with open(final_file, "wb") as outfile:
            for i in range(total_chunks):
                chunk_file = os.path.join(temp_dir, f"{i:05d}.part")
                if not os.path.exists(chunk_file):
                    logger.warning("finalize_upload: chunk lipsa | index=%d | upload_id=%s", i, upload_id)
                    return jsonify(success=False, message=f"Chunk {i} lipsă"), 400
                with open(chunk_file, "rb") as cf:
                    chunk_data = cf.read()
                    outfile.write(chunk_data)
                    sha.update(chunk_data)

        # ── 2. Final checksum ───────────────────────────────────────────────
        if sha.hexdigest() != final_sha256:
            logger.warning(
                "finalize_upload: checksum invalid | file=%s | upload_id=%s",
                filename, upload_id
            )
            return jsonify(success=False, message="Checksum final invalid — fișier corupt"), 400

        # ── 3. Connect FTPS ─────────────────────────────────────────────────
        ftps = _get_ftps()
        logger.info("FTPS connected | host=%s | user=%s", _cfg().FTPS_HOST, _cfg().FTPS_USER)

        try:
            _navigate_ftp_path(ftps, ftp_path)

            # ── 4. Verificare existenta fisier ──────────────────────────────
            file_exists = False
            try:
                existing_files = ftps.nlst()
                file_exists = filename in existing_files
            except Exception:
                try:
                    ftps.size(filename)
                    file_exists = True
                except Exception:
                    file_exists = False

            if file_exists and not override:
                logger.warning(
                    "finalize_upload blocat: fisier existent | file=%s | path=%s",
                    filename, ftp_path
                )
                return jsonify(
                    success=False,
                    message=f"Fisierul '{filename}' exista deja. Folositi override=true."
                ), 409

            if file_exists and override:
                logger.info("finalize_upload: suprascriere fisier | file=%s", filename)

            # ── 5. Upload ───────────────────────────────────────────────────
            with open(final_file, "rb") as f:
                ftps.storbinary(f"STOR {filename}", f)

        finally:
            try:
                ftps.quit()
            except Exception:
                pass

        logger.info(
            "finalize_upload complet | file=%s | path=%s | overwritten=%s",
            filename, ftp_path, file_exists and override
        )

        return jsonify(
            success=True,
            message="Upload realizat cu succes",
            details={
                "filename":    filename,
                "ftp_path":    ftp_path,
                "overwritten": file_exists and override,
            }
        ), 200

    except Exception as e:
        logger.exception("Eroare FTPS finalize_upload | file=%s | upload_id=%s", filename, upload_id)
        return jsonify(success=False, message=f"Eroare FTP: {str(e)}"), 500

    finally:
        # ── Cleanup temp dir întotdeauna ────────────────────────────────────
        _cleanup_temp_dir(temp_dir)
        _upload_sessions.pop(upload_id, None)


# ---------------------------------------------------------------------------
# EF.EXE (the old e-invoice token tool of the Access side, source in the EF2 project) still downloads its
# client data as a password-protected zip. The file lives in the server's project root and is only SENT,
# never read or opened here. Same guard and same LEGACY_UPDATER_ENABLED switch as the rest of the bridge.
# ---------------------------------------------------------------------------
@legacy_updater_bp.route("/api/mfp/get_ef_token", methods=["GET"])
def get_ef_token():
    denied = _guard()
    if denied:
        return denied

    token_path = os.path.abspath("token.zip")
    if not os.path.isfile(token_path):
        logger.error("LEGACY EF: token.zip is missing from the server project root")
        return jsonify({"error": "Fisierul token.zip nu exista pe server"}), 404

    try:
        logger.info(f"LEGACY EF: token.zip sent to {request.remote_addr}")
        return send_file(token_path, mimetype="application/zip", as_attachment=True, download_name="token.zip")
    except Exception as e:
        logger.error(f"Error sending token.zip: {e}", exc_info=True)
        return jsonify({"error": "Eroare la trimiterea fisierului"}), 500
