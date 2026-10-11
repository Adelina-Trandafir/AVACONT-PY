"""
Country of an IP address, OFFLINE (slice 0110-09).

The visitor page of the portal shows a flag next to every address. The country comes from the free
DB-IP «IP to Country Lite» file (https://db-ip.com/db/download/ip-to-country-lite, format MMDB,
licence CC-BY 4.0: the site that shows the result must credit DB-IP.com), read from the server's
own disk through the `maxminddb` package. No address ever leaves the server.

WHERE THE FILE IS. `KBOT_GEOIP_DB` in the environment, else `GEOIP_DB_PATH` in config.py, else
`PYTHON/data/dbip-country-lite.mmdb`. A new file is picked up by restarting the server (the
reader is opened once). The file is refreshed by hand, about once a month.

WHEN IT IS NOT THERE. `country()` answers '' for everything and `status()` says why, so the
admin page can tell the operator in plain words instead of showing empty flags. Nothing raises:
a missing country must never break the page that asks for it.
"""
import ipaddress
import logging
import os
import threading

try:                     # the offline tests stand a stub `config` in sys.modules
    import config
except Exception:        # pragma: no cover - config is always there on the server
    config = None

logger = logging.getLogger(__name__)

_DEFAULT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "data", "dbip-country-lite.mmdb")

_lock = threading.Lock()
_reader = None
_reason = "not opened yet"
_opened = False
_cache = {}
_CACHE_MAX = 5000


def _path():
    return (os.environ.get("KBOT_GEOIP_DB")
            or getattr(config, "GEOIP_DB_PATH", None)
            or _DEFAULT_PATH)


def _open():
    """Opens the file once; the outcome (reader or reason) stays for the life of the process."""
    global _reader, _reason, _opened
    with _lock:
        if _opened:
            return _reader
        _opened = True
        path = _path()
        try:
            import maxminddb
        except ImportError:
            _reason = "the maxminddb package is not installed (pip install maxminddb)"
            logger.warning("geoip: %s", _reason)
            return None
        if not os.path.isfile(path):
            _reason = "the file %s is missing" % path
            logger.warning("geoip: %s", _reason)
            return None
        try:
            _reader = maxminddb.open_database(path)
            _reason = ""
        except Exception as err:
            _reason = "the file %s could not be opened: %s" % (path, err)
            logger.error("geoip: %s", _reason)
            _reader = None
        return _reader


def status():
    """{"ready": bool, "reason": str, "path": str} -- for the admin page."""
    reader = _open()
    return {"ready": reader is not None, "reason": _reason, "path": _path()}


def country(ip):
    """The two-letter ISO code of the address, or '' (private address, unknown, no file)."""
    ip = str(ip or "").strip()
    if not ip:
        return ""
    hit = _cache.get(ip)
    if hit is not None:
        return hit
    code = ""
    try:
        addr = ipaddress.ip_address(ip)
        reader = _open()
        if reader is not None and addr.is_global:
            record = reader.get(ip) or {}
            code = str((record.get("country") or {}).get("iso_code") or "").upper()
            if len(code) != 2 or not code.isalpha():
                code = ""
    except Exception as err:
        logger.warning("geoip: lookup of %s failed: %s", ip, err)
        code = ""
    if len(_cache) >= _CACHE_MAX:
        _cache.clear()
    _cache[ip] = code
    return code
