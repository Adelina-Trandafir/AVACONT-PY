# Offline tests for the application update channel (slice 0067):
#   python -m pytest tests/test_update_routes.py
#
# The blueprint is mounted on a bare Flask app (no main.py, no DB, no config.py)
# and pointed at a temporary folder, so the suite runs on any machine. What is
# covered: the JSON contract of /latest, every way latest.json can be wrong, and
# that /download sends exactly the package latest.json names -- with the
# integrity headers the client reads.
import hashlib
import json

import pytest
from flask import Flask

import routes.update as update_mod
from routes.update import update_bp, read_latest, LatestInvalid, LatestUnavailable


GOOD = {
    "version": "1.0.31.0",
    "minimum": "1.0.30.0",
    "file": "KBot_1.0.31.0.zip",
    "size": 3,
    "sha256": "a" * 64,
    "published_utc": "2026-09-18T10:00:00Z",
    "notes": "Notă de lansare cu diacritice: ăâîșț",
}


@pytest.fixture
def update_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(update_mod, "get_update_dir", lambda: str(tmp_path))
    return tmp_path


@pytest.fixture
def client():
    app = Flask(__name__)
    app.config["TESTING"] = True
    try:
        app.json.ensure_ascii = False
    except Exception:
        app.config["JSON_AS_ASCII"] = False
    app.register_blueprint(update_bp)
    with app.test_client() as c:
        yield c


def _write_latest(update_dir, data):
    (update_dir / "latest.json").write_text(json.dumps(data), encoding="utf-8")


def _write_package(update_dir, name, content=b"zip"):
    (update_dir / name).write_bytes(content)
    return hashlib.sha256(content).hexdigest()


# ---------------------------------------------------------------------------
# read_latest -- the validation, in isolation
# ---------------------------------------------------------------------------

def test_read_latest_missing_file_raises_unavailable(tmp_path):
    with pytest.raises(LatestUnavailable):
        read_latest(str(tmp_path))


def test_read_latest_returns_the_dict(tmp_path):
    _write_latest(tmp_path, GOOD)
    assert read_latest(str(tmp_path)) == GOOD


@pytest.mark.parametrize("broken", [
    "not json at all",
    json.dumps([1, 2, 3]),
    json.dumps({k: v for k, v in GOOD.items() if k != "sha256"}),
    json.dumps({**GOOD, "version": "one.two"}),
    json.dumps({**GOOD, "minimum": ""}),
    json.dumps({**GOOD, "file": "../etc/passwd"}),
    json.dumps({**GOOD, "file": "sub/dir.zip"}),
    json.dumps({**GOOD, "size": "big"}),
    json.dumps({**GOOD, "size": -1}),
    json.dumps({**GOOD, "sha256": "abc"}),
])
def test_read_latest_rejects_broken_files(tmp_path, broken):
    (tmp_path / "latest.json").write_text(broken, encoding="utf-8")
    with pytest.raises(LatestInvalid):
        read_latest(str(tmp_path))


def test_read_latest_accepts_two_part_versions(tmp_path):
    # System.Version on the client accepts 2..4 parts; so does the server.
    _write_latest(tmp_path, {**GOOD, "version": "1.0", "minimum": "1.0"})
    assert read_latest(str(tmp_path))["version"] == "1.0"


# ---------------------------------------------------------------------------
# GET /api/update/latest
# ---------------------------------------------------------------------------

def test_latest_404_when_nothing_published(client, update_dir):
    r = client.get("/api/update/latest")
    assert r.status_code == 404
    body = r.get_json()
    assert body["reason"] == "NO_UPDATE_PUBLISHED"
    assert "error" in body


def test_latest_returns_contract_keys(client, update_dir):
    _write_latest(update_dir, GOOD)
    r = client.get("/api/update/latest")
    assert r.status_code == 200
    body = r.get_json()
    assert body == {
        "version": "1.0.31.0",
        "minimum": "1.0.30.0",
        "file": "KBot_1.0.31.0.zip",
        "size": 3,
        "sha256": "a" * 64,
        "published_utc": "2026-09-18T10:00:00Z",
        "notes": GOOD["notes"],
    }
    # diacritics travel literally, never as \uXXXX escapes
    assert "ăâîșț" in r.get_data(as_text=True)


def test_latest_optional_keys_default(client, update_dir):
    _write_latest(update_dir, {k: v for k, v in GOOD.items() if k in ("version", "minimum", "file", "size", "sha256")})
    body = client.get("/api/update/latest").get_json()
    assert body["published_utc"] is None
    assert body["notes"] == ""


def test_latest_500_when_file_invalid(client, update_dir):
    (update_dir / "latest.json").write_text("{", encoding="utf-8")
    r = client.get("/api/update/latest")
    assert r.status_code == 500
    assert r.get_json()["reason"] == "LATEST_INVALID"


def test_latest_needs_no_authorization_header(client, update_dir):
    # The check runs before login: the route must answer to a bare GET.
    _write_latest(update_dir, GOOD)
    r = client.get("/api/update/latest", headers={})
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# GET /api/update/download
# ---------------------------------------------------------------------------

def test_download_404_when_nothing_published(client, update_dir):
    r = client.get("/api/update/download")
    assert r.status_code == 404
    assert r.get_json()["reason"] == "NO_UPDATE_PUBLISHED"


def test_download_500_when_package_missing_on_disk(client, update_dir):
    _write_latest(update_dir, GOOD)
    r = client.get("/api/update/download")
    assert r.status_code == 500
    assert r.get_json()["reason"] == "PACKAGE_MISSING"


def test_download_sends_the_named_package_with_headers(client, update_dir):
    content = b"PK\x03\x04 pretend zip"
    sha = _write_package(update_dir, GOOD["file"], content)
    _write_latest(update_dir, {**GOOD, "size": len(content), "sha256": sha})

    r = client.get("/api/update/download")
    assert r.status_code == 200
    assert r.data == content
    assert r.headers["X-KBot-Version"] == "1.0.31.0"
    assert r.headers["X-KBot-Sha256"] == sha
    assert r.headers["Content-Type"] == "application/zip"
    assert GOOD["file"] in r.headers["Content-Disposition"]


def test_download_supports_range_requests(client, update_dir):
    # The client may resume a cut download; conditional=True turns Range on.
    content = bytes(range(256))
    sha = _write_package(update_dir, GOOD["file"], content)
    _write_latest(update_dir, {**GOOD, "size": len(content), "sha256": sha})

    r = client.get("/api/update/download", headers={"Range": "bytes=10-19"})
    assert r.status_code == 206
    assert r.data == content[10:20]


def test_download_never_leaves_the_update_dir(client, update_dir):
    # A latest.json that names a path (not a plain file) is refused as invalid,
    # so the file system is never touched with it.
    outside = update_dir.parent / "secret.txt"
    outside.write_text("nope", encoding="utf-8")
    _write_latest(update_dir, {**GOOD, "file": "../secret.txt"})
    r = client.get("/api/update/download")
    assert r.status_code == 500
    assert r.get_json()["reason"] == "LATEST_INVALID"


# ---------------------------------------------------------------------------
# get_update_dir -- the config seam
# ---------------------------------------------------------------------------

def test_get_update_dir_prefers_config_value(monkeypatch):
    import sys
    import types
    cfg = types.ModuleType("config")
    cfg.UPDATE_DIR = "/srv/kbot/updates"
    monkeypatch.setitem(sys.modules, "config", cfg)
    assert update_mod.get_update_dir() == "/srv/kbot/updates"


def test_get_update_dir_defaults_next_to_routes(monkeypatch):
    import sys
    import types
    cfg = types.ModuleType("config")   # no UPDATE_DIR attribute at all
    monkeypatch.setitem(sys.modules, "config", cfg)
    d = update_mod.get_update_dir()
    assert d.endswith("updates")
    assert "routes" not in d.replace("\\", "/").split("/")[-2:]
