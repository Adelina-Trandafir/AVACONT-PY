# ================= Standard Library =================
import hashlib
import json
import logging
import os
import shutil
import tempfile
import uuid
import zipfile
from io import BytesIO

# ================= Third Party =================
from flask import Blueprint, request, jsonify, send_file
from ftplib import FTP_TLS
from werkzeug.utils import secure_filename

# ================= Project Imports =================
import config
from utils.security import require_api_key

# --- CONFIGURARE ---
ftp_bp = Blueprint('ftp', __name__)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-memory session store  {upload_id: {filename, ftp_path, total_size}}
# ---------------------------------------------------------------------------
_upload_sessions: dict = {}

# ---------------------------------------------------------------------------
# CHUNK HELPERS
# ---------------------------------------------------------------------------
def _get_ftps() -> FTP_TLS:
    """Return an authenticated, protected FTPS connection."""
    ftps = FTP_TLS()
    ftps.connect(config.FTPS_HOST, config.FTPS_PORT, timeout=30)
    ftps.login(config.FTPS_USER, config.FTPS_PASS)
    ftps.prot_p()
    ftps.set_pasv(True)
    return ftps

def _navigate_ftp_path(ftps: FTP_TLS, ftp_path: str) -> None:
    """CWD to ftp_path from FTP_BASE_DIR, creating missing folders."""
    ftps.cwd(config.FTP_BASE_DIR)
    if ftp_path:
        for folder in ftp_path.split("/"):
            if not folder:
                continue
            try:
                ftps.cwd(folder)
            except Exception:
                ftps.mkd(folder)
                ftps.cwd(folder)
                logger.info("Folder creat pe FTP: %s", folder)

def _cleanup_temp_dir(temp_dir: str) -> None:
    """Remove temp directory silently."""
    try:
        shutil.rmtree(temp_dir, ignore_errors=True)
    except Exception:
        pass

@ftp_bp.route('/api/ftp/get_avacont_version', methods=['GET'])
@require_api_key
def get_avacont_version():
    """
    Returneaza versiunea asociata unui rol din fisierul VER.txt
    aflat in FTP_BASE_DIR.

    Parametri:
    - role (ex: Rol_1)

    Return:
    - 200 succes
    - 404 rol inexistent
    - 400 param invalid
    - 500 eroare FTP
    """
    logger.info("Request versiune AVACONT")

    role = request.args.get("role", "").strip()

    if not role:
        logger.warning("Get version respins: lipseste 'role'")
        return jsonify(success=False, message="Lipseste parametrul 'role'"), 400

    logger.info("Request versiune rol: %s", role)

    ftps = None

    try:
        # ================= CONECTARE FTPS =================

        ftps = FTP_TLS()
        ftps.connect(config.FTPS_HOST, config.FTPS_PORT, timeout=30)
        ftps.login(config.FTPS_USER, config.FTPS_PASS)
        ftps.prot_p()
        ftps.set_pasv(True)

        logger.info("FTPS connected pentru citire versiune")

        # ================= NAVIGARE IN BASE DIR =================

        ftps.cwd(config.FTP_BASE_DIR)

        # ================= CITIRE versiune_avacont.txt =================

        buffer = BytesIO()
        ftps.retrbinary("RETR versiune_avacont.txt", buffer.write)

        buffer.seek(0)
        content = buffer.read().decode("utf-8")
        logger.info("Continut versiune_avacont.txt: %s", content)

        data = json.loads(content)
        logger.info("Chei disponibile în versiune_avacont.txt: %s", list(data.keys()))
        logger.info("Cheia căutată: %s", role)

        if role not in data:
            logger.warning("Rol inexistent in versiune_avacont.txt: %s", role)
            return jsonify(success=False, message=f"Rolul '{role}' nu exista"), 404

        version = data[role]

        logger.info("Versiune gasita | role=%s | version=%s", role, version)

        return jsonify(
            success=True,
            role=role,
            version=version
        ), 200

    except Exception as e:
        logger.exception("Eroare citire versiune_avacont.txt")
        return jsonify(success=False, message=f"Eroare FTP: {str(e)}"), 500

    finally:
        if ftps:
            try:
                ftps.quit()
            except Exception:
                pass

@ftp_bp.route('/api/ftp/get_update_version', methods=['GET'])
@require_api_key
def get_update_version():
    """
    Returneaza versiunea asociata unui rol din fisierul VER.txt
    aflat in FTP_BASE_DIR.

    Parametri:
    - niciun parametru, doar autentificare API key

    Return:
    - 200 succes
    - 404 rol inexistent
    - 400 param invalid
    - 500 eroare FTP
    """
    logger.info("Request versiune AVACONT")

    ftps = None

    try:
        # ================= CONECTARE FTPS =================

        ftps = FTP_TLS()
        ftps.connect(config.FTPS_HOST, config.FTPS_PORT, timeout=30)
        ftps.login(config.FTPS_USER, config.FTPS_PASS)
        ftps.prot_p()
        ftps.set_pasv(True)

        logger.info("FTPS connected pentru citire versiune")

        # ================= NAVIGARE IN BASE DIR =================
        ftps.cwd(config.FTP_BASE_DIR)

        # ================= CITIRE versiune_update.txt =================

        buffer = BytesIO()
        ftps.retrbinary("RETR versiune_update.txt", buffer.write)

        buffer.seek(0)
        content = buffer.read().decode("utf-8")

        data = json.loads(content)
        logger.info("Chei disponibile în versiune_update.txt: %s", list(data.keys()))
        role = "UPDATE"

        if role not in data:
            logger.warning("Rol inexistent in versiune_update.txt: %s", role)
            return jsonify(success=False, message=f"Rolul '{role}' nu exista"), 404

        version = data[role]

        logger.info("Versiune gasita | role=%s | version=%s", role, version)

        return jsonify(
            success=True,
            role=role,
            version=version
        ), 200

    except Exception as e:
        logger.exception("Eroare citire versiune_update.txt")
        return jsonify(success=False, message=f"Eroare FTP: {str(e)}"), 500

    finally:
        if ftps:
            try:
                ftps.quit()
            except Exception:
                pass

@ftp_bp.route('/api/ftp/download_avacont', methods=['GET'])
@require_api_key
def download_avacont():
    """
    Descarca fisierul avacont_<version>.zip din FTP_BASE_DIR.

    Parametru:
    - version: doar cifre, ex: 1123

    Return:
    - 200 fisier pentru download
    - 400 param invalid
    - 404 fisier inexistent
    - 500 eroare FTP
    """

    version = request.args.get("version", "").strip()

    if not version.isdigit():
        logger.warning("Download respins: versiune invalida: %s", version)
        return jsonify(success=False, message="Parametru 'version' invalid. Trebuie doar cifre."), 400

    filename = f"avacont_{version}.zip"
    logger.info("Cerere download baza: %s", filename)

    ftps = None
    buffer = BytesIO()

    try:
        # ================= CONEXIUNE FTPS =================
        ftps = FTP_TLS()
        ftps.connect(config.FTPS_HOST, config.FTPS_PORT, timeout=30)
        ftps.login(config.FTPS_USER, config.FTPS_PASS)
        ftps.prot_p()
        ftps.set_pasv(True)

        logger.info("FTPS connected pentru download baza")

        # ================= NAVIGARE IN BASE DIR =================
        ftps.cwd(config.FTP_BASE_DIR)

        # ================= VERIFICARE FISIER =================
        files = ftps.nlst()
        if filename not in files:
            logger.warning("Fisier inexistent pe FTP: %s", filename)
            return jsonify(success=False, message=f"Fisier '{filename}' nu exista."), 404

        # ================= DESCARCARE IN MEMORIE =================
        ftps.retrbinary(f"RETR {filename}", buffer.write)
        buffer.seek(0)

        logger.info("Fisier incarcat in memorie: %s", filename)

        # ================= RETURNARE FISIER =================
        return send_file(
            buffer,
            as_attachment=True,
            download_name=filename,
            mimetype="application/zip"
        )

    except Exception as e:
        logger.exception("Eroare la download baza")
        return jsonify(success=False, message=f"Eroare FTP: {str(e)}"), 500

    finally:
        if ftps:
            try:
                ftps.quit()
            except Exception:
                pass

@ftp_bp.route('/api/ftp/init_upload', methods=['POST'])
@require_api_key
def init_upload():
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
    temp_dir  = os.path.join(config.TEMP_UPLOAD_DIR, upload_id)
    os.makedirs(temp_dir, exist_ok=True)

    # Save metadata server-side for later validation
    _upload_sessions[upload_id] = {
        "filename":   filename,
        "ftp_path":   ftp_path,
        "total_size": total_size,
    }

    return jsonify(success=True, upload_id=upload_id), 200


@ftp_bp.route('/api/ftp/upload_chunk', methods=['POST'])
@require_api_key
def upload_chunk():
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

    temp_dir = os.path.join(config.TEMP_UPLOAD_DIR, upload_id)
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


@ftp_bp.route('/api/ftp/finalize_upload', methods=['POST'])
@require_api_key
def finalize_upload():
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

    temp_dir   = os.path.join(config.TEMP_UPLOAD_DIR, upload_id)
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
        logger.info("FTPS connected | host=%s | user=%s", config.FTPS_HOST, config.FTPS_USER)

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


@ftp_bp.route('/api/ftp/download_archive', methods=['POST'])
@require_api_key
def download_archive():
    data     = request.json or {}
    ftp_path = data.get("ftp_path", "").strip("/")
    files    = data.get("files", [])

    if not files:
        return jsonify(success=False, message="Lista de fișiere este goală"), 400

    # Create temp zip in a dedicated temp dir so we can clean up safely
    temp_dir = tempfile.mkdtemp()
    zip_path = os.path.join(temp_dir, "download_bundle.zip")

    try:
        ftps = _get_ftps()
        try:
            _navigate_ftp_path(ftps, ftp_path)

            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as archive:
                for filename in files:
                    local_tmp = os.path.join(temp_dir, secure_filename(filename))
                    with open(local_tmp, "wb") as f:
                        ftps.retrbinary(f"RETR {filename}", f.write)
                    archive.write(local_tmp, arcname=filename)
                    os.remove(local_tmp)
        finally:
            try:
                ftps.quit()
            except Exception:
                pass

        # send_file streams the file; cleanup happens after response via callback
        response = send_file(
            zip_path,
            as_attachment=True,
            download_name="download_bundle.zip",
            mimetype="application/zip",
        )

        # Register cleanup after response is sent
        @response.call_on_close
        def _cleanup():
            shutil.rmtree(temp_dir, ignore_errors=True)

        return response

    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return jsonify(success=False, message=str(e)), 500


@ftp_bp.route('/api/ftp/download_file', methods=['POST'])
@require_api_key
def download_file():
    """
    Descarca un singur fisier de pe FTPS si il returneaza ca stream.

    Parametri (JSON):
    - filename : numele fisierului
    - ftp_path : cale relativa fata de FTP_BASE_DIR

    Return:
    - 200 + fisier stream
    - 400 parametri invalizi
    - 404 fisier negasit
    - 500 eroare FTP
    """
    data     = request.json or {}
    filename = secure_filename(data.get("filename", ""))
    ftp_path = data.get("ftp_path", "").strip("/")

    if not filename:
        logger.warning("download_file respins: filename lipsa")
        return jsonify(success=False, message="Parametru 'filename' lipsa"), 400

    logger.info("download_file request | file=%s | path=%s", filename, ftp_path)

    temp_dir   = tempfile.mkdtemp()
    local_file = os.path.join(temp_dir, filename)

    try:
        ftps = _get_ftps()
        logger.info("FTPS connected | host=%s | user=%s", config.FTPS_HOST, config.FTPS_USER)

        try:
            _navigate_ftp_path(ftps, ftp_path)

            # Verifica existenta fisier
            try:
                file_size = ftps.size(filename)
            except Exception:
                file_size = None

            # Incearca nlst daca size() nu e suportat
            if file_size is None:
                try:
                    existing = ftps.nlst()
                    if filename not in existing:
                        return jsonify(success=False, message=f"Fisierul '{filename}' nu exista."), 404
                except Exception:
                    pass

            with open(local_file, "wb") as f:
                ftps.retrbinary(f"RETR {filename}", f.write)

        finally:
            try:
                ftps.quit()
            except Exception:
                pass

        logger.info("download_file gata | file=%s | path=%s", filename, ftp_path)

        response = send_file(
            local_file,
            as_attachment=True,
            download_name=filename,
        )

        @response.call_on_close
        def _cleanup():
            shutil.rmtree(temp_dir, ignore_errors=True)

        return response

    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        logger.exception("Eroare download_file | file=%s", filename)
        return jsonify(success=False, message=f"Eroare FTP: {str(e)}"), 500