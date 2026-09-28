# routes/forexe/capturi.py
"""
The captures K-BOT takes out of the FOREXE page while the OPERATOR works there
(operator, 28.09.2026).

  PUT /api/forexe/capturi/rezervare/<idrev>   -> a row of FX_Rezervarii_IMG
  PUT /api/forexe/capturi/receptie/<idrh>     -> a row of FX_Receptii_IMG

WHY THESE TWO TABLES. The ALOP guide (Surse/GHID UTILIZARE_ALOP_V2.pdf) asks for captures
of the control system inside both documents the operator signs: the reservations in the
document of fundamentare (p.11-14, example p.41) and two reception pictures in the
ordonantare de plata (p.21-22 -- «Captura cu receptii» and «Captura cu sectiunea Informatii
complete contract»). Until now the operator took them by hand. `FX_Rezervarii_IMG` and
`FX_Receptii_IMG` came over from Access empty, keyed exactly on the records a picture is
about (IDRZ = one reservation row, IDRR = one reception), so a picture lands on the record
and travels with it -- and when a revision or an ordonantare is built, it is READ from
there instead of being asked of the operator again.

WHAT THE NUMBER IN THE ADDRESS IS. The K-BOT marker (slice 0076, marcaj.py) that the page
wrote into what the operator typed:
  * a reservation session -> «(IDREV: n)», the DDF revision to come. The reservation rows
    of that session are found through their history rows (FX_Istoric.IDREV, written by the
    ingest), exactly as ddf_edit.py finds them when the revision is saved.
  * a reception save -> «(IDRH: n; IDR: m)», the snapshot. FX_Receptii_H.IDRR says which
    reception it belongs to.

A number that cannot be placed is REFUSED with a Romanian message; the client keeps the
picture on disk and says so. Nothing here guesses: a picture on the wrong record would be
evidence of something that did not happen.

Every route: `@require_session`, the session's database, one transaction, `ensure_ascii=False`.
The import (prelucrare_pasi.py) and the DDF/ORD write paths are not touched from here.
"""
import base64
import hashlib
import json
import logging

from flask import g, current_app, request

from routes.auth.guard import require_session
from utils.database import get_kbot_connection

from . import forexe_bp

logger = logging.getLogger(__name__)

# One tag per table, so every operation on it is found in api_server.log with one grep
# (operator, 28.09.2026). ddf_edit.py / ord_edit.py use the same two when they read them.
LOG_REZ = "[forexe.rezervari.img]"
LOG_REC = "[forexe.receptii.img]"

MAX_CAPTURA_BYTES = 16 * 1024 * 1024
# JPEG (the client compresses: the pictures end up inside a PDF -- operator, 28.09.2026)
# and PNG, should the client ever be switched back to it.
JPEG_MAGIC = b"\xff\xd8\xff"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
H_SHA = "X-Sha256"
H_NUME = "X-Nume-Fisier"
H_MOMENT = "X-Moment"
H_COD = "X-Cod-Angajament"

FEL_REZERVARE = "rezervare"
FEL_RECEPTIE = "receptie"


class Refuz(Exception):
    """The request is refused before anything is written. The message is already Romanian."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _json_utf8(payload, status):
    body = json.dumps(payload, ensure_ascii=False, default=str)
    return current_app.response_class(body, status=status, mimetype="application/json")


def _in_tranzactie(treaba, tag: str, eticheta: str):
    """Run `treaba(cursor)` in ONE transaction; `Refuz` -> its status, anything else -> 500.

    The arrival is logged BEFORE anything is read, so every request that reaches the route
    leaves a line: no line means the request never got here.
    """
    db_name = g.session.db_name
    logger.info("%s %s: %s primita (%s octeti, cod=%s, moment=%s, nume=%s)",
                tag, db_name, eticheta, request.content_length,
                (request.headers.get(H_COD) or "-").strip(),
                (request.headers.get(H_MOMENT) or "-").strip(),
                (request.headers.get(H_NUME) or "-").strip())
    conn = None
    try:
        conn = get_kbot_connection(db_name)
        conn.autocommit = False
        cursor = conn.cursor(dictionary=True)
        if not conn.in_transaction:
            conn.start_transaction()
        rezultat = treaba(cursor)
        conn.commit()
        logger.info("%s %s: %s ok -> %s", tag, db_name, eticheta, rezultat)
        return _json_utf8(rezultat, 200)
    except Refuz as e:
        if conn is not None:
            conn.rollback()
        logger.warning("%s %s: %s refuzata (%s) -- %s", tag, db_name, eticheta, e.status, e)
        return _json_utf8({"error": str(e)}, e.status)
    except Exception:
        if conn is not None:
            conn.rollback()
        logger.exception("%s %s: %s a esuat", tag, db_name, eticheta)
        return _json_utf8({"error": "Captura nu a putut fi salvată. Detalii în jurnalul serverului."}, 500)
    finally:
        if conn is not None:
            conn.close()


def _citeste_cererea():
    """The bytes and the three headers, checked. Raises `Refuz` on anything wrong."""
    octeti = request.get_data()
    if not octeti:
        raise Refuz("Captura este goală.")
    if len(octeti) > MAX_CAPTURA_BYTES:
        raise Refuz(f"Captura depășește {MAX_CAPTURA_BYTES // (1024 * 1024)} MB.", 413)
    if not (octeti.startswith(JPEG_MAGIC) or octeti.startswith(PNG_MAGIC)):
        raise Refuz("Captura nu este o imagine JPEG sau PNG.")
    sha = hashlib.sha256(octeti).hexdigest()
    sha_client = (request.headers.get(H_SHA) or "").strip().lower()
    if sha_client and sha_client != sha:
        raise Refuz("Suma de control a capturii nu se potrivește: s-a stricat pe drum.")
    nume = (request.headers.get(H_NUME) or "").strip()
    if not nume or len(nume) > 255 or not nume.isascii():
        raise Refuz(f"Antet lipsă sau nevalid: {H_NUME}.")
    moment = (request.headers.get(H_MOMENT) or "").strip()[:50]
    cod = (request.headers.get(H_COD) or "").strip()[:50]
    return octeti, sha, nume, moment, cod


def _base64(octeti: bytes) -> str:
    """The IMG columns of the two tables are LONGTEXT: Access wrote base64 there."""
    return base64.b64encode(octeti).decode("ascii")


def _exista_deja(cursor, tabela: str, cheie: str, valoare: int, continut: str) -> int:
    """The id of a picture of the same record with the SAME bytes, or 0.

    A download repeated after a failure brings its pictures a second time; stored twice,
    they would show twice in the document.
    """
    id_col = "IDRZC" if tabela == "FX_Rezervarii_IMG" else "IDRDC"
    cursor.execute(
        f"SELECT {id_col} AS id FROM {tabela} WHERE {cheie} = %s AND IMG = %s LIMIT 1",
        (valoare, continut))
    row = cursor.fetchone() or {}
    return int(row.get("id") or 0)


# ---------------------------------------------------------------------------
# Reservations -> FX_Rezervarii_IMG
# ---------------------------------------------------------------------------
def _rezervarea_reviziei(cursor, idrev: int, cod: str) -> int:
    """The reservation row the picture hangs off: the FIRST of the session the marker named.

    The session's saves carry «(IDREV: n)» in their motive; the ingest (step 3b) stored that
    number on the history rows, and every reservation row points at its history row. The
    same join ddf_edit.py uses when the revision is written -- so a picture and the revision
    that will show it always agree.
    """
    cursor.execute(
        "SELECT R.IDRZ AS idrz FROM FX_Rezervari R "
        " INNER JOIN FX_Istoric H ON H.ID = R.IDH "
        " WHERE H.IDREV = %s AND (%s = '' OR R.CodAngajament = %s) "
        " ORDER BY R.IDRZ LIMIT 1",
        (idrev, cod, cod))
    row = cursor.fetchone() or {}
    idrz = int(row.get("idrz") or 0)
    if idrz > 0:
        return idrz
    # A revision that is already written names its reservations directly.
    cursor.execute(
        "SELECT IDRZ AS idrz FROM FX_Rezervari "
        " WHERE IDREV = %s AND (%s = '' OR CodAngajament = %s) ORDER BY IDRZ LIMIT 1",
        (idrev, cod, cod))
    row = cursor.fetchone() or {}
    return int(row.get("idrz") or 0)


@forexe_bp.route("/api/forexe/capturi/rezervare/<int:idrev>", methods=["PUT"])
@require_session
def put_captura_rezervare(idrev):
    """One picture of a reservation session, kept on the session's first reservation row."""

    def treaba(cursor):
        octeti, _sha, nume, moment, cod = _citeste_cererea()
        idrz = _rezervarea_reviziei(cursor, idrev, cod)
        if idrz <= 0:
            raise Refuz(
                f"Nu găsesc rezervările reviziei {idrev}" +
                (f" pentru angajamentul «{cod}»" if cod else "") +
                ". Captura rămâne pe calculator până când rezervările intră în K-BOT.", 409)
        continut = _base64(octeti)
        existent = _exista_deja(cursor, "FX_Rezervarii_IMG", "IDRZ", idrz, continut)
        if existent > 0:
            logger.info("%s idrev=%s idrz=%s: aceeasi captura exista deja (IDRZC=%s), nu se scrie",
                        LOG_REZ, idrev, idrz, existent)
            return {"id": existent, "exista_deja": True, "idrz": idrz}
        cursor.execute(
            "INSERT INTO FX_Rezervarii_IMG (IDRZ, IMG, Nume) VALUES (%s, %s, %s)",
            (idrz, continut, nume))
        idrzc = int(cursor.lastrowid or 0)
        logger.info("%s INSERT IDRZC=%s idrev=%s idrz=%s moment=%s nume=%s %s octeti",
                    LOG_REZ, idrzc, idrev, idrz, moment or "-", nume, len(octeti))
        if idrzc <= 0:
            raise RuntimeError("INSERT FX_Rezervarii_IMG did not return a key")
        return {"id": idrzc, "exista_deja": False, "idrz": idrz}

    return _in_tranzactie(treaba, LOG_REZ, f"PUT rezervare idrev={idrev}")


# ---------------------------------------------------------------------------
# Receptions -> FX_Receptii_IMG
# ---------------------------------------------------------------------------
def _receptia_instantaneului(cursor, idrh: int, cod: str):
    """(IDRR, IDRH) of the snapshot the marker named; IDRR 0 when it is not grouped yet."""
    cursor.execute(
        "SELECT IDRR AS idrr, CodAngajament AS cod FROM FX_Receptii_H WHERE IDRH = %s",
        (idrh,))
    row = cursor.fetchone()
    if not row:
        return 0, ""
    return int(row.get("idrr") or 0), (row.get("cod") or "")


@forexe_bp.route("/api/forexe/capturi/receptie/<int:idrh>", methods=["PUT"])
@require_session
def put_captura_receptie(idrh):
    """One of the two pictures the guide asks for the ordonantare (p.21-22)."""

    def treaba(cursor):
        octeti, _sha, nume, moment, cod = _citeste_cererea()
        idrr, cod_h = _receptia_instantaneului(cursor, idrh, cod)
        if idrr <= 0:
            raise Refuz(
                f"Instantaneul {idrh} nu este legat de o recepție" +
                (f" a angajamentului «{cod}»" if cod else "") +
                ". Captura rămâne pe calculator până când recepția intră în K-BOT.", 409)
        if cod and cod_h and cod.strip().upper() != cod_h.strip().upper():
            raise Refuz(
                f"Instantaneul {idrh} este al angajamentului «{cod_h}», nu al lui «{cod}».", 409)
        continut = _base64(octeti)
        existent = _exista_deja(cursor, "FX_Receptii_IMG", "IDRR", idrr, continut)
        if existent > 0:
            logger.info("%s idrh=%s idrr=%s: aceeasi captura exista deja (IDRDC=%s), nu se scrie",
                        LOG_REC, idrh, idrr, existent)
            return {"id": existent, "exista_deja": True, "idrr": idrr}
        cursor.execute(
            "INSERT INTO FX_Receptii_IMG (IDRR, IDRH, IMG, Nume) VALUES (%s, %s, %s, %s)",
            (idrr, idrh, continut, nume))
        idrdc = int(cursor.lastrowid or 0)
        logger.info("%s INSERT IDRDC=%s idrh=%s idrr=%s moment=%s nume=%s %s octeti",
                    LOG_REC, idrdc, idrh, idrr, moment or "-", nume, len(octeti))
        if idrdc <= 0:
            raise RuntimeError("INSERT FX_Receptii_IMG did not return a key")
        return {"id": idrdc, "exista_deja": False, "idrr": idrr}

    return _in_tranzactie(treaba, LOG_REC, f"PUT receptie idrh={idrh}")
