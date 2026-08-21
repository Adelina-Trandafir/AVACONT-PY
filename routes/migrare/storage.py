# routes/migrare/storage.py
# -----------------------------------------------------------------------------
# Where the pushed Access files live, and the chunked upload that puts them there.
#
# Why chunked: main.py caps every request at 17 MB (MAX_CONTENT_LENGTH) and
# FX_2026.accdb is ~29 MB. The shape is the same three-step dance routes/ftp.py
# already uses (init -> chunk -> finalize), but the destination is a local folder,
# not FTPS, so none of that code is reused.
#
# The upload session registry is an in-memory dict. That is safe here and only
# here because the deployment is locked to a single Gunicorn worker (see the
# on_starting guard in gunicorn.conf.py).
# -----------------------------------------------------------------------------

import hashlib
import logging
import os
import re
import shutil
import time
import uuid

# config.py sta pe server, nu in depozit. Importul lene ii lasa pe modulele
# pure (validare, rutare, tabele) sa fie importabile si pe o statie fara config,
# ca suita de teste offline sa poata rula. Rutele, care chiar au nevoie de el,
# esueaza oricum la primul apel, cu numele cheii lipsa.
try:
    import config
except ImportError:                                  # pragma: no cover - off-host
    config = None

logger = logging.getLogger(__name__)

# Numele bazei de unitate: 000_DEMO, 005_CEVM, ...
DC_RE = re.compile(r"^[0-9]{3}_[A-Za-z0-9]+$")

# Anul de exercitiu, ca sa nu ajunga nimic altceva in numele fisierului.
AN_RE = re.compile(r"^[0-9]{4}$")

# Chunk-ul pe care il acceptam. Sub plafonul global de 17 MB cu o marja lata,
# fiindca multipart adauga si el cativa octeti.
MAX_CHUNK_BYTES = 4 * 1024 * 1024

# O sesiune de incarcare abandonata se sterge dupa atat.
SESSION_TTL_SECONDS = 6 * 3600

# {upload_id: {"kind", "an", "dc", "total_size", "sha256", "started"}}
_sessions = {}


class StorageError(Exception):
    """Eroare de stocare cu mesaj gata de aratat operatorului (romana)."""


def pushed_dir():
    """
    Folderul in care stau fisierele Access impinse de migrator.

    Vine din config.PUSHED_ACCDB_DIR. Nu are valoare implicita ascunsa: daca
    lipseste din config, oprim cu numele cheii, nu scriem in cine stie ce cale.
    """
    path = getattr(config, "PUSHED_ACCDB_DIR", None)
    if not path:
        raise StorageError(
            "PUSHED_ACCDB_DIR lipsește din config.py — serverul nu știe unde să "
            "păstreze fișierele Access împinse de migrator.")
    os.makedirs(path, exist_ok=True)
    return path


def _temp_root():
    path = getattr(config, "TEMP_UPLOAD_DIR", None)
    if not path:
        raise StorageError(
            "TEMP_UPLOAD_DIR lipsește din config.py — nu există unde aduna "
            "bucățile fișierului în timpul încărcării.")
    os.makedirs(path, exist_ok=True)
    return path


def validate_dc(dc):
    if not dc or not DC_RE.match(dc):
        raise StorageError("Numele bazei de unitate (DC) este invalid: «%s»." % (dc or ""))
    return dc


def validate_an(an):
    an = str(an or "").strip()
    if not AN_RE.match(an):
        raise StorageError("Anul este invalid: «%s». Se așteaptă patru cifre." % an)
    return an


def fx_file_name(an, dc):
    """fx_2026_005_CEVM.accdb — numele cerut de operator, cu litere mici."""
    return "fx_%s_%s.accdb" % (validate_an(an), validate_dc(dc).lower())


def pushed_path(name):
    """Calea absoluta a unui fisier impins. Numele e deja construit de noi."""
    return os.path.join(pushed_dir(), name)


def list_pushed():
    """Ce se afla pe server acum: nume, dimensiune, data ultimei scrieri."""
    out = []
    for name in sorted(os.listdir(pushed_dir())):
        if not name.lower().endswith(".accdb"):
            continue
        full = os.path.join(pushed_dir(), name)
        try:
            st = os.stat(full)
        except OSError as exc:
            raise StorageError("Fișierul «%s» nu poate fi citit: %s" % (name, exc))
        out.append({
            "nume": name,
            "octeti": st.st_size,
            "modificat": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime)),
        })
    return out


# -----------------------------------------------------------------------------
# Incarcarea in bucati
# -----------------------------------------------------------------------------

def begin_upload(kind, an, dc, total_size, sha256):
    """
    kind: "fx" -- the year's FOREXE file. It is the only kind of file the
    migration takes; a row's unit is found in the file itself (FX_Angajamente
    carries both IdUnitate and DC), so there is no routing file any more.

    Returns upload_id. The name is validated NOW, not at finalisation, so the
    operator learns immediately if he got the DC wrong.
    """
    if kind != "fx":
        raise StorageError(
            "Tipul de fișier «%s» nu este cunoscut. Migrarea ia un singur fel de "
            "fișier: «fx»." % (kind or ""))

    try:
        total_size = int(total_size)
    except (TypeError, ValueError):
        raise StorageError("Dimensiunea totală trimisă nu este un număr.")
    if total_size <= 0:
        raise StorageError("Dimensiunea totală trebuie să fie mai mare decât zero.")

    sha256 = (sha256 or "").strip().lower()
    if not re.match(r"^[0-9a-f]{64}$", sha256):
        raise StorageError("Amprenta SHA-256 a fișierului lipsește sau este invalidă.")

    name = fx_file_name(an, dc)

    _prune_sessions()

    upload_id = str(uuid.uuid4())
    os.makedirs(os.path.join(_temp_root(), upload_id), exist_ok=True)
    _sessions[upload_id] = {
        "nume": name,
        "total_size": total_size,
        "sha256": sha256,
        "started": time.time(),
    }
    logger.info("migrare: sesiune de încărcare %s pentru «%s» (%d octeți)",
                upload_id, name, total_size)
    return upload_id, name


def store_chunk(upload_id, index, data, chunk_sha256):
    session = _sessions.get(upload_id)
    if session is None:
        raise StorageError("Sesiunea de încărcare nu mai există. Reia încărcarea.")

    if len(data) > MAX_CHUNK_BYTES:
        raise StorageError(
            "Bucata trimisă are %d octeți, peste plafonul de %d." % (len(data), MAX_CHUNK_BYTES))

    actual = hashlib.sha256(data).hexdigest()
    if actual != (chunk_sha256 or "").strip().lower():
        raise StorageError("Amprenta bucății %s nu corespunde — transmisie coruptă." % index)

    try:
        index = int(index)
    except (TypeError, ValueError):
        raise StorageError("Indexul bucății nu este un număr.")
    if index < 0:
        raise StorageError("Indexul bucății nu poate fi negativ.")

    path = os.path.join(_temp_root(), upload_id, "%06d.part" % index)
    with open(path, "wb") as fh:
        fh.write(data)


def finish_upload(upload_id, total_chunks):
    """
    Lipeste bucatile, verifica amprenta intregului fisier si abia apoi muta peste
    destinatie. Un fisier deja existent este inlocuit -- migrarea se reia oricand.
    """
    session = _sessions.get(upload_id)
    if session is None:
        raise StorageError("Sesiunea de încărcare nu mai există. Reia încărcarea.")

    try:
        total_chunks = int(total_chunks)
    except (TypeError, ValueError):
        raise StorageError("Numărul de bucăți nu este un număr.")

    temp_dir = os.path.join(_temp_root(), upload_id)
    staged = os.path.join(temp_dir, "complet.accdb")
    digest = hashlib.sha256()
    written = 0

    try:
        with open(staged, "wb") as out:
            for i in range(total_chunks):
                part = os.path.join(temp_dir, "%06d.part" % i)
                if not os.path.isfile(part):
                    raise StorageError("Bucata %d lipsește — încărcarea este incompletă." % i)
                with open(part, "rb") as fh:
                    while True:
                        block = fh.read(1024 * 1024)
                        if not block:
                            break
                        digest.update(block)
                        written += len(block)
                        out.write(block)

        if written != session["total_size"]:
            raise StorageError(
                "Fișierul asamblat are %d octeți, se așteptau %d." % (written, session["total_size"]))
        if digest.hexdigest() != session["sha256"]:
            raise StorageError("Amprenta fișierului asamblat nu corespunde — încărcarea a eșuat.")

        target = pushed_path(session["nume"])
        shutil.move(staged, target)
        logger.info("migrare: fișier salvat în «%s» (%d octeți)", target, written)
        return {"nume": session["nume"], "octeti": written, "cale": target}

    finally:
        _sessions.pop(upload_id, None)
        shutil.rmtree(temp_dir, ignore_errors=True)


def _prune_sessions():
    """Sesiunile abandonate nu se acumuleaza pe disc."""
    now = time.time()
    for upload_id in [k for k, v in _sessions.items()
                      if now - v["started"] > SESSION_TTL_SECONDS]:
        _sessions.pop(upload_id, None)
        shutil.rmtree(os.path.join(_temp_root(), upload_id), ignore_errors=True)
