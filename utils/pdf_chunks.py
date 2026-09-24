# utils/pdf_chunks.py
"""
Shared-chunk storage for signed PDFs (slice 0078-05).

WHY: every DDF / ORD PDF carries the same form template, fonts and scripts. Measured on the
real signed files (23.09.2026): about 97% of each DDF is byte-identical to every other DDF.
Compressing one file alone saves ~9% (its streams are already Flate-compressed); storing the
repeated pieces ONCE saves ~98% (first DDF ~250 KB, every further one ~5 KB).

HOW: content-defined chunking. A rolling "gear" hash walks the bytes and cuts a chunk wherever
the low 12 bits of the hash are zero (average ~4 KB), never below MIN_CHUNK and never above
MAX_CHUNK. Because the cut points depend on the CONTENT, not on offsets, an insertion early in
the file does not shift every later chunk -- identical regions produce identical chunks.

A document is stored as the ordered list of its chunk hashes (32 bytes each, concatenated).
Rebuilding just joins the chunks back in that order, so the result is byte-identical to what
was uploaded -- a digital signature (whose /ByteRange covers every byte) stays valid. The
caller still verifies the whole-file SHA-256 after rebuilding.

The cutting algorithm is NOT part of the stored format: rebuilding never re-cuts anything.
Changing GEAR / the sizes later only changes how NEW uploads are split; old documents keep
rebuilding from their stored list.

Pure module: no Flask, no database. The route passes a `fetch` callable in.
"""
import hashlib
import zlib

# Cut when (hash & MASK) == 0 -> one cut every 2**12 bytes on average (~4 KB).
MASK = (1 << 12) - 1
MIN_CHUNK = 1024
MAX_CHUNK = 64 * 1024
HASH_LEN = 32          # raw SHA-256 digest length, the unit of the stored list

# Fixed 256-entry gear table. Derived deterministically (SHA-256 of the index) so it is the
# same on every host and every Python version -- never from `random`, whose sequence is not
# a stable contract.
GEAR = tuple(
    int.from_bytes(hashlib.sha256(bytes([i])).digest()[:4], "big") for i in range(256)
)


def split(data: bytes):
    """Cut `data` into content-defined chunks. Returns a list of (sha256_digest, chunk_bytes)
    in file order. Joining the chunks gives `data` back exactly."""
    if not data:
        return []
    out = []
    gear = GEAR
    n = len(data)
    start = 0
    h = 0
    i = 0
    while i < n:
        h = ((h << 1) + gear[data[i]]) & 0xFFFFFFFF
        i += 1
        length = i - start
        if (length >= MIN_CHUNK and (h & MASK) == 0) or length >= MAX_CHUNK or i == n:
            chunk = data[start:i]
            out.append((hashlib.sha256(chunk).digest(), chunk))
            start = i
            h = 0
    return out


def pack_list(digests) -> bytes:
    """The stored form of a document: its chunk digests, concatenated, in order."""
    return b"".join(digests)


def unpack_list(packed: bytes):
    """Inverse of pack_list. A length that is not a multiple of 32 is corruption -> ValueError
    (never a silently truncated list)."""
    packed = bytes(packed or b"")
    if len(packed) % HASH_LEN != 0:
        raise ValueError(f"chunk list length {len(packed)} is not a multiple of {HASH_LEN}")
    return [packed[i:i + HASH_LEN] for i in range(0, len(packed), HASH_LEN)]


def compress(chunk: bytes) -> bytes:
    """What goes into FX_PDF_BUCATI.Continut."""
    return zlib.compress(chunk, 6)


def decompress(stored: bytes, digest: bytes) -> bytes:
    """Inverse of compress, plus a per-chunk integrity check against its own key."""
    chunk = zlib.decompress(bytes(stored))
    if hashlib.sha256(chunk).digest() != bytes(digest):
        raise ValueError(f"chunk {bytes(digest).hex()[:12]} failed its own checksum")
    return chunk


def rebuild(digests, fetch) -> bytes:
    """Join the chunks back. `fetch(list_of_distinct_digests) -> {digest: stored_bytes}`.
    A missing chunk or a chunk failing its checksum raises ValueError -- the caller must never
    send a partially rebuilt signed PDF."""
    wanted = list(dict.fromkeys(bytes(d) for d in digests))
    stored = fetch(wanted) if wanted else {}
    plain = {}
    for d in wanted:
        blob = stored.get(d)
        if blob is None:
            raise ValueError(f"chunk {d.hex()[:12]} is missing from the store")
        plain[d] = decompress(blob, d)
    return b"".join(plain[bytes(d)] for d in digests)
