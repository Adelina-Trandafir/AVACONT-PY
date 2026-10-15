"""
Security plan S2 (retirement of the shared X-Api-Key API): the whole application, as registered by
main.py, must

  * not serve any route of the retired Access/FOREXE API (404),
  * not accept the old key anywhere, on the routes that stay,
  * keep the Access sync routes behind THEIR OWN key (X-Access-Key) and administration behind X-Admin-Token.

Optional third-party packages that are not installed on a dev PC are replaced by stubs (the routes are
only inspected and called without credentials, nothing here touches a database).
"""
import importlib.abc
import importlib.machinery
import os
import sys
import types
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("KBOT_ASOCIERE", "0")

_OPTIONAL = {"pandas", "numpy", "unidecode", "pikepdf", "lxml", "bs4", "redis", "iText", "PyPDF2",
             "pypdf", "openpyxl", "xlrd", "fitz", "docx", "reportlab", "paramiko", "geoip2"}


class _StubFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Last-resort finder: only for the optional packages above, and only when the real one is absent."""

    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in _OPTIONAL:
            return importlib.machinery.ModuleSpec(fullname, self, is_package=True)
        return None

    def create_module(self, spec):
        mod = MagicMock(name=spec.name)
        mod.__path__ = []
        mod.__spec__ = spec
        mod.__name__ = spec.name
        return mod

    def exec_module(self, module):
        return None


@pytest.fixture(scope="module")
def app():
    finder = _StubFinder()
    sys.meta_path.append(finder)
    try:
        import main
        main.app.config["TESTING"] = True
        yield main.app
    finally:
        sys.meta_path.remove(finder)


RETIRED_PATHS = [
    ("POST", "/api/tools/say_hello"), ("GET", "/api/tools/pdfium_dll"), 
    # check_update / check_updates / upload are back on purpose (routes/legacy_updater.py, temporary bridge)
    ("GET", "/api/mfp/template_ddf"), ("GET", "/api/mfp/template_ord"), ("GET", "/api/mfp/get_ef_token"),
    ("GET", "/api/admin/download_baza"), ("GET", "/api/admin/download_ef"), ("GET", "/api/admin/download_forexe"),
    ("POST", "/api/admin/check_db"), ("POST", "/api/admin/setup_db"), ("POST", "/api/admin/ping_db"),
    ("POST", "/api/clasificatii/get_clasificatii_ids"), ("POST", "/api/clasificatii/mapping_clasificatii"),
    ("POST", "/api/clasificatii/rectificari"), ("POST", "/api/clasificatii/rectificari_upsert"),
    ("POST", "/api/parteneri/get_partner_ids_by_codes"), ("POST", "/api/parteneri/upsert_coduri"),
    ("POST", "/api/forexe/seed/schema"), ("POST", "/api/forexe/seed/rows"), ("GET", "/api/forexe/seed/columns"),
    ("GET", "/api/forexe/seed/ids"),
    ("POST", "/api/salarii/save_document"), ("POST", "/api/ddf/save"), ("POST", "/api/ord/save"),
]


@pytest.mark.parametrize("method,path", RETIRED_PATHS)
def test_retired_route_is_gone(app, method, path):
    c = app.test_client()
    for headers in ({}, {"X-Api-Key": "anything"}):
        assert c.open(path, method=method, headers=headers).status_code == 404


def test_no_route_depends_on_the_old_key(app):
    """Nothing registered may still be guarded by the old key or its transition guard."""
    import utils.security as sec
    assert not hasattr(sec, "require_api_key")
    import routes.auth.guard as guard
    assert not hasattr(guard, "require_session_or_api_key")


@pytest.mark.parametrize("method,path", [
    ("POST", "/api/clasificatii/insert"), ("POST", "/api/clasificatii/clasificatii_complete_upsert"),
    ("POST", "/api/clasificatii/sync_acc_kbot"), ("POST", "/api/parteneri/syncdb"),
    ("POST", "/api/parteneri/upsert"),
])
def test_access_sync_routes_need_their_own_key(app, method, path, monkeypatch):
    import config
    monkeypatch.setattr(config, "ACCESS_SYNC_KEY", "k-access", raising=False)
    monkeypatch.setattr(config, "ADMIN_TOKEN", "k-admin", raising=False)
    c = app.test_client()
    assert c.open(path, method=method).status_code == 401
    assert c.open(path, method=method, headers={"X-Api-Key": "k-access"}).status_code == 401
    assert c.open(path, method=method, headers={"X-Admin-Token": "k-admin"}).status_code == 401
    assert c.open(path, method=method, headers={"X-Access-Key": "wrong"}).status_code == 401


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/admin/users"), ("POST", "/api/admin/login/reset"),
    ("POST", "/api/admin/receptii/refacere"), ("GET", "/api/migrare/baze"),
    ("POST", "/api/migrare/schema-sync"),
])
def test_admin_routes_need_the_admin_token(app, method, path, monkeypatch):
    import config
    monkeypatch.setattr(config, "ADMIN_TOKEN", "k-admin", raising=False)
    monkeypatch.setattr(config, "ACCESS_SYNC_KEY", "k-access", raising=False)
    c = app.test_client()
    assert c.open(path, method=method).status_code == 401
    assert c.open(path, method=method, headers={"X-Api-Key": "k-admin"}).status_code == 401
    assert c.open(path, method=method, headers={"X-Access-Key": "k-access"}).status_code == 401


def test_unset_credentials_refuse_everything(app, monkeypatch):
    import config
    monkeypatch.setattr(config, "ADMIN_TOKEN", "", raising=False)
    monkeypatch.setattr(config, "ACCESS_SYNC_KEY", "", raising=False)
    c = app.test_client()
    assert c.get("/api/admin/users", headers={"X-Admin-Token": ""}).status_code == 401
    assert c.post("/api/parteneri/upsert", headers={"X-Access-Key": ""}).status_code == 401


@pytest.mark.parametrize("method,path", [
    ("POST", "/api/tools/process_excel"), ("GET", "/api/forexe/angajament/dump"),
    ("GET", "/api/mfp/templates/ddf"), ("GET", "/api/mfp/templates/ord"),
])
def test_modern_routes_take_the_bearer_only(app, method, path, monkeypatch):
    import config
    monkeypatch.setattr(config, "ADMIN_TOKEN", "k-admin", raising=False)
    c = app.test_client()
    for headers in ({}, {"X-Api-Key": "k"}, {"X-Admin-Token": "k-admin"}):
        assert c.open(path, method=method, headers=headers).status_code == 401
