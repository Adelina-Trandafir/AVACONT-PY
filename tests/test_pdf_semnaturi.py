# Offline tests for slice 0079: the X-Semnaturi / X-Statie parser of routes/forexe/pdf.py.
# Run from the PYTHON folder:  python -m pytest tests/test_pdf_semnaturi.py
import base64
import json
from datetime import datetime, timezone, timedelta

import pytest

try:
    from routes.forexe import pdf as pdf_route
except Exception as e:                              # pragma: no cover - off-host
    pdf_route = None
    _skip_reason = f"routes import unavailable: {e}"
else:
    _skip_reason = ""


def _b64(value):
    return base64.b64encode(json.dumps(value, ensure_ascii=False).encode("utf-8")).decode("ascii")


@pytest.mark.skipif(pdf_route is None, reason=_skip_reason or "n/a")
class TestParseAudit:
    def test_absent_header_means_no_records(self):
        assert pdf_route._parse_audit(pdf_route._DDF, None, None) == ([], {}, None)

    def test_record_and_station_are_read(self):
        sigs = _b64([{"camp": "form1[0].SubformSemnaturaA[0].SignatureField1[0]", "rol": "A",
                      "semnatar": "Ștefan AD.CREDIT", "data": "2026-09-23T16:00:00+03:00"}])
        station = _b64({"ip_local": "192.168.1.20", "calculator": "PC-01", "extra": "dropped"})
        records, st, err = pdf_route._parse_audit(pdf_route._DDF, sigs, station)
        assert err is None
        assert records[0]["rol"] == "A"
        assert records[0]["semnatar"] == "Ștefan AD.CREDIT"
        expected = datetime(2026, 9, 23, 16, 0, tzinfo=timezone(timedelta(hours=3))) \
            .astimezone().replace(tzinfo=None)
        assert records[0]["data"] == expected
        assert st["ip_local"] == "192.168.1.20" and st["calculator"] == "PC-01"
        assert "extra" not in st

    def test_empty_date_is_null(self):
        records, _, err = pdf_route._parse_audit(pdf_route._DDF, _b64([{"camp": "f", "data": ""}]), None)
        assert err is None and records[0]["data"] is None and records[0]["rol"] is None

    def test_role_of_the_other_family_rejected(self):
        _, _, err = pdf_route._parse_audit(pdf_route._DDF, _b64([{"camp": "f", "rol": "AB"}]), None)
        assert err

    def test_missing_field_name_rejected(self):
        _, _, err = pdf_route._parse_audit(pdf_route._ORD, _b64([{"rol": "AB"}]), None)
        assert err

    def test_bad_date_rejected(self):
        _, _, err = pdf_route._parse_audit(pdf_route._DDF, _b64([{"camp": "f", "data": "ieri"}]), None)
        assert err

    def test_not_base64_rejected(self):
        _, _, err = pdf_route._parse_audit(pdf_route._DDF, "{not base64}", None)
        assert err

    def test_too_many_records_rejected(self):
        many = [{"camp": f"f{i}"} for i in range(pdf_route.SIGN_MAX_RECORDS + 1)]
        _, _, err = pdf_route._parse_audit(pdf_route._DDF, _b64(many), None)
        assert err

    def test_long_station_values_are_cut(self):
        station = _b64({"versiune": "9" * 100})
        _, st, err = pdf_route._parse_audit(pdf_route._DDF, _b64([{"camp": "f"}]), station)
        assert err is None and len(st["versiune"]) == 32
