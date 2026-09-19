# routes/update.py
"""
Application update channel for K-BOT (slice 0067).

Two PUBLIC routes -- no bearer, no API key. The client checks BEFORE login, when it
holds no credential at all (ApiOptions: bearer only, issued at login), and the
package is the same thing handed out as the installer, so nothing here is secret.

    GET /api/update/latest    -> the small JSON below (200) or 404 when nothing was
                                 ever published.
    GET /api/update/download  -> the package named in latest.json, as a file.

Everything is read from ONE folder, UPDATE_DIR. `push-update.ps1` on the build PC
fills it over SFTP: the package `KBot_<version>.zip` and, last, `latest.json`:

    {
      "version":       "1.0.31.0",        # KBot.App FileVersion inside the package
      "minimum":       "1.0.30.0",        # clients below this MUST update
      "file":          "KBot_1.0.31.0.zip",
      "size":          40445309,
      "sha256":        "...",             # hex, lowercase
      "published_utc": "2026-09-18T10:00:00Z",
      "notes":         "free text shown to the operator"
    }

The push script uploads to `*.part` and renames into place, and writes latest.json
AFTER the package, so a reader never sees a version whose file is not there yet.
No caching: the file is small and re-read on every call, so a push is visible
without restarting the service.

UPDATE_DIR: `config.UPDATE_DIR` when the server's config.py defines it, otherwise
`<PYTHON root>/updates`. config.py is host-only (never pushed), which is why the
default lives here and not there.
"""
import json
import logging
import os
import re

from flask import Blueprint, jsonify, send_file

logger = logging.getLogger(__name__)

update_bp = Blueprint("update", __name__)

LATEST_NAME = "latest.json"

# The package name is written by the push script and read back here. Anything
# that is not a plain file name (a path, an empty string) is refused before it
# reaches the file system -- latest.json is ours, but a typo must not turn into
# an arbitrary-file read.
_SAFE_FILE_RE = re.compile(r"^[A-Za-z0-9._-]+$")

# The version strings are compared by the CLIENT (System.Version); the server only
# checks that they look like x.x.x.x so a broken latest.json is reported as such
# instead of surfacing as a parse error on the operator's screen.
_VERSION_RE = re.compile(r"^\d+(\.\d+){1,3}$")

_REQUIRED_KEYS = ("version", "minimum", "file", "size", "sha256")


def _default_update_dir():
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "updates")


def get_update_dir():
    """The folder that holds latest.json and the packages (see module docstring)."""
    try:
        import config  # host-only; may be absent in offline tests
    except Exception:
        return _default_update_dir()
    configured = getattr(config, "UPDATE_DIR", None)
    if configured:
        return configured
    return _default_update_dir()


class LatestUnavailable(Exception):
    """latest.json is missing -> nothing has been published yet (404)."""


class LatestInvalid(Exception):
    """latest.json exists but cannot be trusted (500)."""


def read_latest(update_dir=None):
    """
    Read and validate latest.json. Returns the dict as written by the push script.
    Raises LatestUnavailable when the file is absent, LatestInvalid when it is
    unreadable, not JSON, missing a key, or names a file that is not a plain name.
    """
    update_dir = update_dir or get_update_dir()
    path = os.path.join(update_dir, LATEST_NAME)
    if not os.path.isfile(path):
        raise LatestUnavailable(path)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        raise LatestInvalid(f"{LATEST_NAME}: {e}") from e
    if not isinstance(data, dict):
        raise LatestInvalid(f"{LATEST_NAME}: not an object")
    for key in _REQUIRED_KEYS:
        if key not in data:
            raise LatestInvalid(f"{LATEST_NAME}: missing key '{key}'")
    for key in ("version", "minimum"):
        if not isinstance(data[key], str) or not _VERSION_RE.match(data[key]):
            raise LatestInvalid(f"{LATEST_NAME}: '{key}' is not a version: {data[key]!r}")
    if not isinstance(data["file"], str) or not _SAFE_FILE_RE.match(data["file"]):
        raise LatestInvalid(f"{LATEST_NAME}: 'file' is not a plain file name: {data['file']!r}")
    if not isinstance(data["size"], int) or data["size"] < 0:
        raise LatestInvalid(f"{LATEST_NAME}: 'size' is not a non-negative integer")
    if not isinstance(data["sha256"], str) or len(data["sha256"]) != 64:
        raise LatestInvalid(f"{LATEST_NAME}: 'sha256' is not a 64-char hex digest")
    return data


def _error(message, reason, status):
    return jsonify({"error": message, "reason": reason}), status


@update_bp.route("/api/update/latest", methods=["GET"])
def latest():
    """The published version, or 404 when nothing has been published yet."""
    try:
        data = read_latest()
    except LatestUnavailable:
        return _error("Nu există nicio actualizare publicată.", "NO_UPDATE_PUBLISHED", 404)
    except LatestInvalid as e:
        logger.error("latest.json invalid: %s", e)
        return _error("Descrierea actualizării de pe server este invalidă.", "LATEST_INVALID", 500)

    body = {
        "version": data["version"],
        "minimum": data["minimum"],
        "file": data["file"],
        "size": data["size"],
        "sha256": data["sha256"],
        "published_utc": data.get("published_utc"),
        "notes": data.get("notes") or "",
    }
    return jsonify(body), 200


@update_bp.route("/api/update/download", methods=["GET"])
def download():
    """The package named in latest.json, streamed as a file (Range supported)."""
    try:
        data = read_latest()
    except LatestUnavailable:
        return _error("Nu există nicio actualizare publicată.", "NO_UPDATE_PUBLISHED", 404)
    except LatestInvalid as e:
        logger.error("latest.json invalid: %s", e)
        return _error("Descrierea actualizării de pe server este invalidă.", "LATEST_INVALID", 500)

    path = os.path.join(get_update_dir(), data["file"])
    if not os.path.isfile(path):
        # latest.json points at a package that is not there: the push was cut in
        # half, or someone deleted the file. Say so, do not send an empty body.
        logger.error("update package listed in latest.json is missing on disk: %s", path)
        return _error("Pachetul de actualizare lipsește de pe server.", "PACKAGE_MISSING", 500)

    try:
        response = send_file(
            path,
            mimetype="application/zip",
            as_attachment=True,
            download_name=data["file"],
            conditional=True,
        )
        response.headers["X-KBot-Version"] = data["version"]
        response.headers["X-KBot-Sha256"] = data["sha256"]
        logger.info("update package sent: %s (v%s)", data["file"], data["version"])
        return response
    except Exception as e:
        logger.error("update package could not be sent: %s", e, exc_info=True)
        return _error("Pachetul de actualizare nu a putut fi trimis.", "PACKAGE_SEND_FAILED", 500)
