# routes/forexe/pdf.py
"""
Stocarea pe server a PDF-urilor SEMNATE (felia 0041).

Rute:
    GET  /api/forexe/ddf/pdf/<idrev>    -> octetii PDF-ului semnat al reviziei
    PUT  /api/forexe/ddf/pdf/<idrev>    -> inlocuieste-sau-insereaza randul
    GET  /api/forexe/ord/pdf/<idordp>   -> octetii PDF-ului semnat al ordonantarii
    PUT  /api/forexe/ord/pdf/<idordp>   -> inlocuieste-sau-insereaza randul

Scope: baza conectata ESTE unitatea (o baza MariaDB = o unitate), deci nu exista parametru
db_name / id_unitate — baza vine din sesiune (g.session.db_name), exact ca la toate
celelalte rute /api/forexe/*. Un token nu poate tinti alta baza decat cea pe care s-a logat.

CE SE STOCHEAZA: DOAR PDF-uri SEMNATE. Un PDF nesemnat e un artefact DERIVAT — clientul il
regenereaza prin XfaWriter ori de cate ori operatorul cere sa-l vada — si NU se incarca
niciodata aici. Existenta randului INSEAMNA «exista PDF semnat»; nu exista coloana `Semnat`.
Fara istoric: cheia unica pe IDREV / IDORDP face ca o re-semnare sa INLOCUIASCA randul.

CELE TREI REGULI BIT-CU-BIT (miezul feliei — protejeaza semnaturile digitale):

1. OCTETI BRUTI PE FIR. Corpurile sunt `application/octet-stream` / `application/pdf` —
   niciodata JSON, niciodata base64. Nicaieri pe drum nu se trece prin mod TEXT.

2. SHA-256 VERIFICAT LA AMANDOUA CAPETELE, IN AMANDOUA SENSURILE.
   * Incarcare: clientul calculeaza suma INAINTE de a trimite si o pune in `X-Sha256`;
     serverul o RECALCULEAZA peste corpul primit si respinge cu 400 la nepotrivire —
     nu se scrie nimic.
   * Descarcare: serverul trimite suma stocata ca `ETag`; clientul o recalculeaza peste
     octetii primiti si refuza sa scrie fisierul de cache la nepotrivire.

3. CONCURENTA OPTIMISTA. Incarcarea poarta in `X-Sha-Precedent` suma pe care clientul a
   vazut-o ultima data pentru documentul respectiv («-» cand crede ca nu exista rand).
   Daca suma randului curent difera, raspunsul e 409 si NU se scrie nimic. Nicio semnatura
   a altcuiva nu se suprascrie in tacere.

NUMELE FISIERULUI se deriva pe SERVER (sursa unica, fara incredere in client), reproducand
exact conventiile Access deja portate pe client (DdfPdfLocator / OrdPdfLocator):
    DDF: DDF_NR_{CUAL}_REV_{NumarRev}_{CodAngajament}.PDF
    ORD: ORD_NR_{NrORD}_{CodAngajament}.PDF
La DDF se tine cont ca PK-ul lui FX_DDF e COMPUS (IDDF, CUAL) — se ia randul de antet prin
`LIMIT 1` cu ordine stabila, ca in routes/forexe/ddf.py, niciodata un join care ar da fan-out.
La ORD se foloseste `NrORD`-ul REAL — defectul Access «ORD_NR_0_…» (dictionar gol pe ramura
«un singur document») NU se reproduce, aceeasi decizie ca in OrdPdfLocator.

SIGNER ROLES (slice 0078): the PUT may carry `X-Semnatura` -- the signer roles found in the
uploaded PDF, comma separated (DDF: A,B,Ordonator; ORD: AB,CD,Ordonator). When present, the
parent's `Semnatura` column is updated in the SAME transaction as the PDF row, so a stored PDF
can never exist without its roles (or the roles without the PDF). Absent header = column
untouched.

SHARED CHUNKS (slice 0078-05): when the unit database has `FX_PDF_BUCATI` and the `Bucati`
column, the PDF is stored as an ordered list of content-defined chunks (utils/pdf_chunks.py);
each distinct chunk is stored once per database. `Continut` stays NULL on those rows. Rows
written before (with `Continut`) are still served as they are. The wire contract does not
change: the client always sends and receives the whole, byte-identical file.

NU se logheaza niciodata continutul blobului — doar dimensiuni si sume de control.
"""
import base64
import binascii
import hashlib
import json
import logging
from datetime import datetime

from flask import request, g, current_app

from routes.auth.guard import require_session
from utils.database import get_kbot_connection
from utils import pdf_chunks

from . import forexe_bp

logger = logging.getLogger(__name__)

# Plafonul practic al unui PDF stocat. Coloana e LONGBLOB (nu se opreste aici), dar peste
# atat raspundem 413 cu un mesaj care SPUNE limita — vezi si MAX_CONTENT_LENGTH din main.py,
# care taie cererea mai devreme, inainte ca octetii sa ajunga in memorie.
MAX_PDF_BYTES = 17 * 1024 * 1024

# Antetul care poarta suma clientului peste octetii trimisi.
H_SHA = "X-Sha256"
# Antetul care poarta suma pe care clientul a vazut-o ULTIMA DATA pentru document.
# «-» inseamna «cred ca nu exista rand».
H_SHA_PREC = "X-Sha-Precedent"
NO_ROW = "-"
# Slice 0078: signer roles found in the uploaded PDF, comma separated, ASCII.
H_SEMN = "X-Semnatura"
# Slice 0078-05: the shared chunk store (one per unit database).
CHUNK_TABLE = "FX_PDF_BUCATI"
# Digests per IN (...) query -- keeps each statement well under max_allowed_packet.
CHUNK_BATCH = 200

# Cele doua familii de documente, intr-o singura descriere: tabela de PDF-uri, coloana-cheie
# si tabela parinte. Rutele sunt identice in afara acestor trei nume, deci logica sta o
# singura data (regula casei: fara al doilea exemplar care se poate desincroniza).
# Slice 0079: the signatures an upload ADDS and the computer it comes from -- base64 of UTF-8
# JSON (a header is ASCII only; a signer name can carry diacritics). Written to SIGN_TABLE.
H_SEMNATURI = "X-Semnaturi"
H_STATIE = "X-Statie"
SIGN_TABLE = "FX_PDF_SEMNATURI"
SIGN_MAX_RECORDS = 50
# Column widths of SIGN_TABLE (sql/0079_fx_pdf_semnaturi.sql): longer values are cut, not refused.
_STATION_FIELDS = {"ip_local": 255, "calculator": 128, "utilizator_windows": 128,
                   "sistem": 128, "versiune": 32}

_DDF = {
    "tabela": "FX_DDF_PDF",
    "cheie": "IDREV",
    "parinte": "FX_DDF_REV",
    "eticheta": "ddf",
    # Slice 0078: the signer roles a DDF can carry, in their canonical order.
    "roluri": ("A", "B", "Ordonator"),
}
_ORD = {
    "tabela": "FX_ORD_PDF",
    "cheie": "IDORDP",
    "parinte": "FX_ORD",
    "eticheta": "ord",
    "roluri": ("AB", "CD", "Ordonator"),
}


def _json_utf8(payload, status):
    """Raspuns JSON cu diacritice LITERALE (ensure_ascii=False): mesajele de eroare sunt
    romanesti si trebuie sa ajunga la operator ca UTF-8 real, nu \\uXXXX."""
    body = json.dumps(payload, ensure_ascii=False)
    return current_app.response_class(body, status=status, mimetype="application/json")


def _sha256(data: bytes) -> str:
    """SHA-256 peste octetii dati, hex MINUSCULE — acelasi format ca pe client (PdfHash)."""
    return hashlib.sha256(data).hexdigest()


def _parinte_exista(cursor, spec, cheie: int) -> bool:
    """Exista documentul parinte (revizia / ordonantarea)? Fara el nu are ce PDF sa poarte."""
    cursor.execute(
        f"SELECT 1 FROM {spec['parinte']} WHERE {spec['cheie']} = %s LIMIT 1", (cheie,))
    return cursor.fetchone() is not None


def _sha_curent(cursor, spec, cheie: int):
    """Suma randului stocat acum, sau None cand nu exista rand."""
    cursor.execute(
        f"SELECT Sha256 FROM {spec['tabela']} WHERE {spec['cheie']} = %s LIMIT 1", (cheie,))
    row = cursor.fetchone()
    return row[0] if row else None


def _parse_roles(spec, raw):
    """Validate the `X-Semnatura` header. Returns (roles_text, error_text).

    None header -> (None, None): the column is not touched. Otherwise every item must be one
    of the family's roles, without duplicates; the result is rewritten in canonical order so
    the column always reads the same way ("A,B,Ordonator", never "Ordonator,A,B").
    """
    if raw is None:
        return None, None
    items = [x.strip() for x in raw.split(",") if x.strip()]
    if not items:
        return None, f"Antetul {H_SEMN} este gol: lipsesc rolurile semnatarilor."
    allowed = spec["roluri"]
    unknown = [x for x in items if x not in allowed]
    if unknown:
        return None, (f"Rol de semnatar necunoscut pentru {spec['eticheta'].upper()}: "
                      f"{', '.join(unknown)}.")
    if len(set(items)) != len(items):
        return None, f"Antetul {H_SEMN} repetă un rol de semnatar."
    return ",".join(r for r in allowed if r in items), None


def _decode_b64_json(raw, header):
    """(value, None) or (None, error) for a base64-of-UTF-8-JSON header."""
    try:
        return json.loads(base64.b64decode(raw.strip(), validate=True).decode("utf-8")), None
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return None, f"Antetul {header} nu este JSON codat base64."


def _parse_sign_time(text):
    """ISO 8601 with offset -> naive datetime in the SERVER's local time (like NOW() in the
    other columns); "" -> None. Raises ValueError on anything else."""
    if not text:
        return None
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is not None:
        moment = moment.astimezone().replace(tzinfo=None)
    return moment


def _parse_audit(spec, raw_sigs, raw_station):
    """Slice 0079: (records, station, None) or (None, None, error). No header = ([], {}, None).

    Every record: camp (required), rol (one of the family's roles or ""), semnatar, data.
    The station is informative: unknown keys are dropped, long values cut to the column."""
    if raw_sigs is None or not raw_sigs.strip():
        return [], {}, None
    items, err = _decode_b64_json(raw_sigs, H_SEMNATURI)
    if err:
        return None, None, err
    if not isinstance(items, list) or len(items) > SIGN_MAX_RECORDS:
        return None, None, f"Antetul {H_SEMNATURI} trebuie să fie o listă de cel mult {SIGN_MAX_RECORDS} semnături."
    records = []
    for item in items:
        if not isinstance(item, dict):
            return None, None, f"Antetul {H_SEMNATURI} conține o semnătură fără câmpuri."
        camp = str(item.get("camp") or "").strip()
        rol = str(item.get("rol") or "").strip()
        if not camp or len(camp) > 255:
            return None, None, f"Antetul {H_SEMNATURI} conține o semnătură fără numele câmpului."
        if rol and rol not in spec["roluri"]:
            return None, None, f"Rol de semnatar necunoscut pentru {spec['eticheta'].upper()}: {rol}."
        try:
            data = _parse_sign_time(str(item.get("data") or "").strip())
        except ValueError:
            return None, None, f"Data semnăturii din câmpul {camp} nu este o dată validă."
        records.append({"camp": camp, "rol": rol or None,
                        "semnatar": (str(item.get("semnatar") or "").strip()[:255]) or None,
                        "data": data})
    station = {}
    if raw_station and raw_station.strip():
        value, err = _decode_b64_json(raw_station, H_STATIE)
        if err:
            return None, None, err
        if isinstance(value, dict):
            station = {k: (str(value.get(k) or "").strip()[:w] or None)
                       for k, w in _STATION_FIELDS.items()}
    return records, station, None


def _has_sign_log(cursor) -> bool:
    """Is slice 0079's table on this database? Probed per request, like the chunk store: until the
    DDL has run, uploads work exactly as before and the records are only logged as skipped."""
    cursor.execute(
        "SELECT COUNT(*) FROM information_schema.TABLES "
        " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s", (SIGN_TABLE,))
    row = cursor.fetchone()
    return bool(row) and int(row[0]) == 1


def _record_signatures(cursor, spec, cheie, records, station, sha) -> int:
    """Writes one SIGN_TABLE row per record not already there (same document, field and time) --
    a repeated upload (retry after a lost answer) never doubles a signature. Returns rows written.

    Who and where come from the SERVER: the session's account and the request address. Only the
    workstation details come from the client."""
    cheie_col = spec["cheie"]
    ip_public = (request.remote_addr or "")[:45] or None
    un = (getattr(g.session, "username", "") or "")[:128]
    written = 0
    for r in records:
        cursor.execute(
            f"SELECT COUNT(*) FROM {SIGN_TABLE} "
            f" WHERE {cheie_col} = %s AND Camp = %s AND DataSemnaturii <=> %s",
            (cheie, r["camp"], r["data"]))
        row = cursor.fetchone()
        if row and int(row[0]) > 0:
            continue
        cursor.execute(
            f"INSERT INTO {SIGN_TABLE} "
            f"       ({cheie_col}, Camp, Rol, Semnatar, DataSemnaturii, DataInregistrarii, Sha256Pdf, "
            f"        UN, IpPublic, IpLocal, NumeCalculator, UtilizatorWindows, SistemOperare, "
            f"        VersiuneKbot) "
            f"VALUES (%s, %s, %s, %s, %s, NOW(), %s, %s, %s, %s, %s, %s, %s, %s)",
            (cheie, r["camp"], r["rol"], r["semnatar"], r["data"], sha, un, ip_public,
             station.get("ip_local"), station.get("calculator"),
             station.get("utilizator_windows"), station.get("sistem"), station.get("versiune")))
        written += 1
    return written


def _has_chunk_store(cursor, spec) -> bool:
    """Is slice 0078-05's DDL applied on this database? Probed per request so the route keeps
    working (whole-file storage, as in 0041) on a database the DDL has not reached yet."""
    cursor.execute(
        "SELECT COUNT(*) FROM information_schema.COLUMNS "
        " WHERE TABLE_SCHEMA = DATABASE() "
        "   AND ((TABLE_NAME = %s AND COLUMN_NAME = 'Bucati') "
        "     OR (TABLE_NAME = %s AND COLUMN_NAME = 'Continut'))",
        (spec["tabela"], CHUNK_TABLE))
    row = cursor.fetchone()
    return bool(row) and int(row[0]) == 2


def _fetch_chunks(cursor, digests):
    """{digest: stored_bytes} for the given digests, in batches."""
    found = {}
    for i in range(0, len(digests), CHUNK_BATCH):
        part = digests[i:i + CHUNK_BATCH]
        marks = ", ".join(["%s"] * len(part))
        cursor.execute(
            f"SELECT Sha256, Continut FROM {CHUNK_TABLE} WHERE Sha256 IN ({marks})", tuple(part))
        for digest, blob in cursor.fetchall():
            found[bytes(digest)] = bytes(blob)
    return found


def _store_chunks(cursor, parts):
    """Insert the chunks this database does not have yet. Returns (new_count, new_bytes).

    Existing digests are asked for first, so repeated template chunks never travel to MariaDB
    again. The insert still uses the no-op `ON DUPLICATE KEY UPDATE` (house pattern from
    slice 0042, NOT `INSERT IGNORE`, which would also hide real errors) because a concurrent
    upload may have added the same chunk in between.
    """
    distinct = {}
    for digest, chunk in parts:
        distinct.setdefault(digest, chunk)
    digests = list(distinct)
    have = set()
    for i in range(0, len(digests), CHUNK_BATCH):
        part = digests[i:i + CHUNK_BATCH]
        marks = ", ".join(["%s"] * len(part))
        cursor.execute(f"SELECT Sha256 FROM {CHUNK_TABLE} WHERE Sha256 IN ({marks})", tuple(part))
        have.update(bytes(r[0]) for r in cursor.fetchall())
    new_count = 0
    new_bytes = 0
    for digest in digests:
        if digest in have:
            continue
        packed = pdf_chunks.compress(distinct[digest])
        cursor.execute(
            f"INSERT INTO {CHUNK_TABLE} (Sha256, Dimensiune, Continut, DataCreare) "
            f"VALUES (%s, %s, %s, NOW()) "
            f"ON DUPLICATE KEY UPDATE Sha256 = Sha256",
            (digest, len(distinct[digest]), packed))
        new_count += 1
        new_bytes += len(packed)
    return new_count, new_bytes


def _nume_fisier_ddf(cursor, idrev: int) -> str:
    """DDF_NR_{CUAL}_REV_{NumarRev}_{CodAngajament}.PDF — conventia din mdl_FX_DDF_PDF.

    `FX_DDF` are PK COMPUS (IDDF, CUAL) si nicio constrangere unica pe CodAngajament, deci
    acelasi IDDF poate purta mai multe randuri: se ia UNUL, cu ordine stabila (`ORDER BY
    IDDF, CUAL LIMIT 1`), exact ca alegerea deterministica din routes/forexe/ddf.py. Un join
    fara LIMIT ar multiplica revizia.
    """
    cursor.execute(
        "SELECT r.NumarRev, d.CUAL, d.CodAngajament "
        "  FROM FX_DDF_REV r "
        "  JOIN FX_DDF d ON d.IDDF = r.IDDF "
        " WHERE r.IDREV = %s "
        " ORDER BY d.IDDF, d.CUAL LIMIT 1", (idrev,))
    row = cursor.fetchone()
    if not row:
        return None
    numar_rev, cual, cod = row
    return f"DDF_NR_{cual}_REV_{numar_rev}_{cod}.PDF"


def _nume_fisier_ord(cursor, idordp: int) -> str:
    """ORD_NR_{NrORD}_{CodAngajament}.PDF — conventia din mdl_FX_ORD_PDF.

    Se foloseste `NrORD`-ul REAL. Access ia numarul dintr-un dictionar populat DOAR pe ramura
    «toate documentele lunii»; pe ramura «un singur document» dictionarul e gol si fisierul se
    naste «ORD_NR_0_…». Defectul NU se reproduce aici (aceeasi decizie ca in OrdPdfLocator).
    """
    cursor.execute(
        "SELECT NrORD, CodAngajament FROM FX_ORD WHERE IDORDP = %s LIMIT 1", (idordp,))
    row = cursor.fetchone()
    if not row:
        return None
    nr_ord, cod = row
    return f"ORD_NR_{nr_ord}_{cod}.PDF"


# ---------------------------------------------------------------------------------------
# DESCARCARE
# ---------------------------------------------------------------------------------------
def _descarca(spec, cheie: int):
    """Octetii PDF-ului semnat, VERBATIM.

    `If-None-Match` egal cu suma stocata -> 304 cu corp gol: asa evita cache-ul validat prin
    sha o descarcare inutila. Ruta de octeti NU poarta numele fisierului — metadatele calatoresc
    pe rutele de lista (GET /api/forexe/ddf, /ord).
    """
    db_name = g.session.db_name
    conn = None
    try:
        conn = get_kbot_connection(db_name)
        cursor = conn.cursor()
        chunked = _has_chunk_store(cursor, spec)
        cursor.execute(
            f"SELECT Sha256, Dimensiune, Continut{', Bucati' if chunked else ''} "
            f"  FROM {spec['tabela']} "
            f" WHERE {spec['cheie']} = %s LIMIT 1", (cheie,))
        row = cursor.fetchone()
        if row is None:
            return _json_utf8({"error": "Nu există PDF semnat pentru acest document."}, 404)

        sha, dimensiune, continut = row[:3]
        bucati = row[3] if chunked else None

        # ETag-ul se compara ca valoare goala de ghilimele, cum il trimitem mai jos.
        if (request.headers.get("If-None-Match", "").strip().strip('"')) == sha:
            resp = current_app.response_class(b"", status=304)
            resp.headers["ETag"] = f'"{sha}"'
            logger.info("[forexe.pdf] %s: %s %s=%s -> 304 (cache valid)",
                        db_name, spec["eticheta"], spec["cheie"], cheie)
            return resp

        if continut is not None:
            # `bytes(continut)` — conectorul poate intoarce bytearray; octetii raman identici.
            octeti = bytes(continut)
        else:
            # Slice 0078-05: rebuild from the shared chunks, then prove it is the same file.
            # A mismatch is a server fault (500), NEVER corrupt bytes sent as a signed PDF.
            digests = pdf_chunks.unpack_list(bucati)
            octeti = pdf_chunks.rebuild(digests, lambda ds: _fetch_chunks(cursor, ds))
            if _sha256(octeti) != sha:
                logger.error("[forexe.pdf] %s: %s %s=%s rebuilt file does not match its sha",
                             db_name, spec["eticheta"], spec["cheie"], cheie)
                return _json_utf8(
                    {"error": "PDF-ul stocat pe server este deteriorat (suma de control nu "
                              "corespunde). Anunțați administratorul."}, 500)
        resp = current_app.response_class(octeti, status=200, mimetype="application/pdf")
        resp.headers["Content-Length"] = str(len(octeti))
        resp.headers["ETag"] = f'"{sha}"'
        logger.info("[forexe.pdf] %s: %s %s=%s -> 200 (%s octeti, sha=%s…)",
                    db_name, spec["eticheta"], spec["cheie"], cheie, dimensiune, sha[:8])
        return resp
    except Exception as e:
        # Fara inghitire: o eroare de baza intoarce motivul, NU un 404 — un 404 ar minti
        # clientul ca documentul nu are PDF semnat si l-ar trimite sa regenereze degeaba.
        logger.error(f"[forexe.pdf] descarcare {spec['eticheta']}: {e}", exc_info=True)
        return _json_utf8({"error": f"Eroare la citirea PDF-ului: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()


# ---------------------------------------------------------------------------------------
# INCARCARE (inlocuieste-sau-insereaza)
# ---------------------------------------------------------------------------------------
def _incarca(spec, cheie: int, nume_fisier_fn):
    """Scrie PDF-ul semnat, intr-o SINGURA tranzactie, dupa verificarile din nota de modul.

    Ordinea pasilor conteaza: parinte -> PDF valid -> suma de control -> concurenta -> scriere.
    Fiecare pas care esueaza raspunde SI NU SCRIE NIMIC.
    """
    db_name = g.session.db_name

    octeti = request.get_data()
    if not octeti:
        return _json_utf8({"error": "Corpul cererii este gol: nu s-a primit niciun fișier."}, 400)
    if len(octeti) > MAX_PDF_BYTES:
        return _json_utf8(
            {"error": f"Fișierul depășește limita de {MAX_PDF_BYTES // (1024 * 1024)} MB "
                      f"acceptată de server ({len(octeti)} octeți). "
                      f"Reduceți dimensiunea capturilor de ecran atașate."}, 413)

    sha_client = (request.headers.get(H_SHA) or "").strip().lower()
    if not sha_client:
        return _json_utf8({"error": f"Antet lipsă: {H_SHA}."}, 400)
    sha_precedent = (request.headers.get(H_SHA_PREC) or "").strip().lower()
    if not sha_precedent:
        return _json_utf8({"error": f"Antet lipsă: {H_SHA_PREC}."}, 400)
    semnatura, eroare_semn = _parse_roles(spec, request.headers.get(H_SEMN))
    if eroare_semn:
        return _json_utf8({"error": eroare_semn}, 400)
    semnaturi, statie, eroare_audit = _parse_audit(
        spec, request.headers.get(H_SEMNATURI), request.headers.get(H_STATIE))
    if eroare_audit:
        return _json_utf8({"error": eroare_audit}, 400)

    conn = None
    try:
        conn = get_kbot_connection(db_name)
        cursor = conn.cursor()

        # 1. Documentul parinte trebuie sa existe.
        if not _parinte_exista(cursor, spec, cheie):
            return _json_utf8({"error": "Documentul pentru care s-a trimis PDF-ul nu există."}, 404)

        # 2. Sanity: chiar e un PDF?
        if not octeti.startswith(b"%PDF-"):
            return _json_utf8({"error": "Conținutul trimis nu este un fișier PDF valid."}, 400)

        # 3. Suma de control peste octetii CHIAR PRIMITI (regula 2 din nota de modul).
        sha_server = _sha256(octeti)
        if sha_server != sha_client:
            logger.warning("[forexe.pdf] %s: %s %s=%s sumă nepotrivită (client=%s… server=%s…)",
                           db_name, spec["eticheta"], spec["cheie"], cheie,
                           sha_client[:8], sha_server[:8])
            return _json_utf8(
                {"error": "Fișierul a sosit corupt: suma de control nu corespunde."}, 400)

        # 4. Concurenta optimista (regula 3). Nicio suprascriere tacuta.
        sha_stocat = _sha_curent(cursor, spec, cheie)
        asteptat = NO_ROW if sha_stocat is None else sha_stocat
        if sha_precedent != asteptat:
            logger.warning("[forexe.pdf] %s: %s %s=%s conflict (precedent=%s asteptat=%s)",
                           db_name, spec["eticheta"], spec["cheie"], cheie,
                           sha_precedent[:8], asteptat[:8])
            return _json_utf8(
                {"error": "Documentul a fost modificat de altcineva între timp."}, 409)

        # 5. Numele fisierului — derivat pe SERVER, sursa unica.
        nume = nume_fisier_fn(cursor, cheie)
        if not nume:
            # Parintele exista (pasul 1), dar antetul din care se compune numele lipseste.
            # Zgomotos, nu tacut: un nume inventat ar strica regasirea documentului.
            return _json_utf8(
                {"error": "Nu s-a putut compune numele fișierului: lipsesc datele de antet."}, 409)

        # 6. Scrierea propriu-zisa, atomica sub cheia unica.
        if _has_chunk_store(cursor, spec):
            # Slice 0078-05: only the chunks this database does not have yet are written;
            # the row keeps the ordered list and `Continut` goes NULL.
            parts = pdf_chunks.split(octeti)
            new_count, new_bytes = _store_chunks(cursor, parts)
            lista = pdf_chunks.pack_list(d for d, _ in parts)
            cursor.execute(
                f"INSERT INTO {spec['tabela']} "
                f"       ({spec['cheie']}, NumeFisier, Dimensiune, Sha256, Continut, Bucati, "
                f"        DataModif) "
                f"VALUES (%s, %s, %s, %s, NULL, %s, NOW()) "
                f"ON DUPLICATE KEY UPDATE "
                f"       NumeFisier = VALUES(NumeFisier), "
                f"       Dimensiune = VALUES(Dimensiune), "
                f"       Sha256     = VALUES(Sha256), "
                f"       Continut   = NULL, "
                f"       Bucati     = VALUES(Bucati), "
                f"       DataModif  = NOW()",
                (cheie, nume, len(octeti), sha_server, lista))
            stocare = f"{len(parts)} chunks, {new_count} new ({new_bytes} bytes)"
        else:
            logger.warning("[forexe.pdf] %s: %s not applied -- whole-file storage",
                           db_name, CHUNK_TABLE)
            cursor.execute(
                f"INSERT INTO {spec['tabela']} "
                f"       ({spec['cheie']}, NumeFisier, Dimensiune, Sha256, Continut, DataModif) "
                f"VALUES (%s, %s, %s, %s, %s, NOW()) "
                f"ON DUPLICATE KEY UPDATE "
                f"       NumeFisier = VALUES(NumeFisier), "
                f"       Dimensiune = VALUES(Dimensiune), "
                f"       Sha256     = VALUES(Sha256), "
                f"       Continut   = VALUES(Continut), "
                f"       DataModif  = NOW()",
                (cheie, nume, len(octeti), sha_server, octeti))
            stocare = "whole file"

        # 7. Slice 0078: the signer roles, in the SAME transaction as the PDF row.
        if semnatura is not None:
            cursor.execute(
                f"UPDATE {spec['parinte']} SET Semnatura = %s WHERE {spec['cheie']} = %s",
                (semnatura, cheie))

        # 8. Slice 0079: the signature log, same transaction -- no PDF without its record.
        inregistrate = 0
        if semnaturi:
            if _has_sign_log(cursor):
                inregistrate = _record_signatures(cursor, spec, cheie, semnaturi, statie, sha_server)
            else:
                logger.warning("[forexe.pdf] %s: %s not applied -- %d signature record(s) skipped",
                               db_name, SIGN_TABLE, len(semnaturi))
        conn.commit()

        logger.info("[forexe.pdf] %s: %s %s=%s salvat (%s octeti, sha=%s…, nume=%s, %s, "
                    "roles=%s, signatures logged=%s/%s)",
                    db_name, spec["eticheta"], spec["cheie"], cheie,
                    len(octeti), sha_server[:8], nume, stocare, semnatura,
                    inregistrate, len(semnaturi))
        return _json_utf8(
            {"sha256": sha_server, "nume_fisier": nume, "dimensiune": len(octeti),
             "semnatura": semnatura, "semnaturi_inregistrate": inregistrate}, 200)
    except Exception as e:
        # Fara inghitire: se anuleaza tranzactia si se intoarce motivul.
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                # Rollback-ul pe o conexiune deja cazuta nu are ce sa mai salveze; eroarea
                # REALA e cea de mai jos si ea trebuie sa ajunga la client.
                logger.warning("[forexe.pdf] rollback esuat dupa eroarea de mai jos", exc_info=True)
        logger.error(f"[forexe.pdf] incarcare {spec['eticheta']}: {e}", exc_info=True)
        return _json_utf8({"error": f"Eroare la salvarea PDF-ului: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()


# ---------------------------------------------------------------------------------------
# RUTELE
# ---------------------------------------------------------------------------------------
@forexe_bp.route("/api/forexe/ddf/pdf/<int:idrev>", methods=["GET"])
@require_session
def get_ddf_pdf(idrev):
    """PDF-ul semnat al unei revizii DDF (octeti bruti)."""
    return _descarca(_DDF, idrev)


@forexe_bp.route("/api/forexe/ddf/pdf/<int:idrev>", methods=["PUT"])
@require_session
def put_ddf_pdf(idrev):
    """Inlocuieste-sau-insereaza PDF-ul semnat al unei revizii DDF."""
    return _incarca(_DDF, idrev, _nume_fisier_ddf)


@forexe_bp.route("/api/forexe/ord/pdf/<int:idordp>", methods=["GET"])
@require_session
def get_ord_pdf(idordp):
    """PDF-ul semnat al unei ordonantari (octeti bruti)."""
    return _descarca(_ORD, idordp)


@forexe_bp.route("/api/forexe/ord/pdf/<int:idordp>", methods=["PUT"])
@require_session
def put_ord_pdf(idordp):
    """Inlocuieste-sau-insereaza PDF-ul semnat al unei ordonantari."""
    return _incarca(_ORD, idordp, _nume_fisier_ord)
