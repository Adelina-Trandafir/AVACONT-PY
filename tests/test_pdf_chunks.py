# Offline tests for slice 0078: utils/pdf_chunks.py (pure) and the X-Semnatura parser of
# routes/forexe/pdf.py. Run from the PYTHON folder:  python -m pytest tests/test_pdf_chunks.py
import hashlib
import os
import random

import pytest

from utils import pdf_chunks


def _sample(n, seed):
    rnd = random.Random(seed)
    return bytes(rnd.getrandbits(8) for _ in range(n))


def _store_of(parts):
    return {d: pdf_chunks.compress(c) for d, c in parts}


def test_split_then_join_is_identical():
    data = _sample(300_000, 1)
    parts = pdf_chunks.split(data)
    assert b"".join(c for _, c in parts) == data


def test_chunk_sizes_respect_bounds():
    parts = pdf_chunks.split(_sample(500_000, 2))
    for _, chunk in parts[:-1]:
        assert pdf_chunks.MIN_CHUNK <= len(chunk) <= pdf_chunks.MAX_CHUNK
    assert len(parts[-1][1]) <= pdf_chunks.MAX_CHUNK


def test_empty_input_gives_no_chunks():
    assert pdf_chunks.split(b"") == []


def test_rebuild_round_trip_through_packed_list():
    data = _sample(200_000, 3)
    parts = pdf_chunks.split(data)
    store = _store_of(parts)
    digests = pdf_chunks.unpack_list(pdf_chunks.pack_list(d for d, _ in parts))
    back = pdf_chunks.rebuild(digests, lambda ds: {d: store[d] for d in ds})
    assert back == data
    assert hashlib.sha256(back).hexdigest() == hashlib.sha256(data).hexdigest()


def test_shared_prefix_shares_chunks():
    # Two "documents" with a large common body and a different tail: most chunks match.
    body = _sample(250_000, 4)
    a = body + b"tail-A" * 500
    b = body + b"tail-B" * 500
    da = {d for d, _ in pdf_chunks.split(a)}
    db = {d for d, _ in pdf_chunks.split(b)}
    assert len(da & db) >= len(da) - 2


def test_insertion_does_not_shift_every_later_chunk():
    body = _sample(250_000, 5)
    changed = body[:1000] + b"INSERTED" + body[1000:]
    before = {d for d, _ in pdf_chunks.split(body)}
    after = {d for d, _ in pdf_chunks.split(changed)}
    assert len(before & after) >= len(before) - 3


def test_missing_chunk_raises():
    parts = pdf_chunks.split(_sample(50_000, 6))
    store = _store_of(parts)
    store.pop(parts[0][0])
    with pytest.raises(ValueError):
        pdf_chunks.rebuild([d for d, _ in parts], lambda ds: {d: store[d] for d in ds if d in store})


def test_corrupt_chunk_raises():
    parts = pdf_chunks.split(_sample(50_000, 7))
    store = _store_of(parts)
    first = parts[0][0]
    store[first] = pdf_chunks.compress(b"not the original bytes")
    with pytest.raises(ValueError):
        pdf_chunks.rebuild([d for d, _ in parts], lambda ds: {d: store[d] for d in ds})


def test_bad_list_length_raises():
    with pytest.raises(ValueError):
        pdf_chunks.unpack_list(b"x" * 33)


def test_gear_table_is_stable():
    # The table is part of how NEW uploads are cut; pin it so an accidental change is seen.
    assert len(pdf_chunks.GEAR) == 256
    assert pdf_chunks.GEAR[0] == int.from_bytes(hashlib.sha256(bytes([0])).digest()[:4], "big")


# --- X-Semnatura parsing (needs the Flask blueprint imports; skipped off-host) ------------
try:
    from routes.forexe import pdf as pdf_route
except Exception as e:                              # pragma: no cover - off-host
    pdf_route = None
    _skip_reason = f"routes import unavailable: {e}"
else:
    _skip_reason = ""


@pytest.mark.skipif(pdf_route is None, reason=_skip_reason or "n/a")
class TestParseRoles:
    def test_absent_header_leaves_column(self):
        assert pdf_route._parse_roles(pdf_route._DDF, None) == (None, None)

    def test_canonical_order(self):
        assert pdf_route._parse_roles(pdf_route._DDF, "Ordonator, A") == ("A,Ordonator", None)
        assert pdf_route._parse_roles(pdf_route._ORD, "CD,AB") == ("AB,CD", None)

    def test_unknown_role_rejected(self):
        roles, err = pdf_route._parse_roles(pdf_route._DDF, "AB")
        assert roles is None and err

    def test_duplicate_rejected(self):
        roles, err = pdf_route._parse_roles(pdf_route._ORD, "AB,AB")
        assert roles is None and err

    def test_empty_rejected(self):
        roles, err = pdf_route._parse_roles(pdf_route._DDF, " , ")
        assert roles is None and err


@pytest.mark.skipif(not os.path.isdir(r"C:\AVACONT\FOREXE\PDF\DDF"),
                    reason="no real signed DDF files on this machine")
def test_real_ddf_files_share_most_chunks():
    import glob
    files = sorted(glob.glob(r"C:\AVACONT\FOREXE\PDF\DDF\**\*.PDF", recursive=True))[:4]
    if len(files) < 2:
        pytest.skip("need at least two DDF files")
    seen = set()
    first = None
    for f in files:
        data = open(f, "rb").read()
        parts = pdf_chunks.split(data)
        new = sum(len(c) for d, c in parts if d not in seen)
        seen.update(d for d, _ in parts)
        if first is None:
            first = new
        else:
            assert new < first * 0.1
