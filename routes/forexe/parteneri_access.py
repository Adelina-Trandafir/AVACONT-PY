# routes/forexe/parteneri_access.py
"""
«Preia date din Access» of the «Parteneri» window (slice 0093-01, operator 03.11.2026): the NEW partners of the
Access application's unit file are added to K-BOT's Parteneri.

    POST /api/forexe/nomenclatoare/parteneri/preia-access
        { "id_unitate": n,
          "partners": [ { "cod_partener", "denumire", "cod_fiscal", "cont_iban", "banca", "adresa", "tip", "ascuns" } ] }
        -> 200 { "primite": n, "adaugate": n, "existente": n, "sarite": n, "ids": [IdPartener of the added rows],
                 "probleme": [Romanian sentences, at most 20] }
        -> 400 bad body / unknown unit | 409 a row appeared meanwhile

The PC reads the Access file (the unit's own `Parteneri` table, which has no IdUnitate column: the unit is the file's)
and sends it one unit at a time, in batches; this route only writes.

RULES
  * A partner is NEW when the unit has no Parteneri row with its CodPartener (the table's own unique key,
    UNIQUE (IdUnitate, CodPartener); compared the way MariaDB compares it: without case and trailing spaces).
    A partner that exists is left exactly as it is -- nothing is updated, so what the operator edited here stays.
  * Nothing else is checked: Access carries partners without a fiscal code, of another Tip, or with a fiscal code
    another partner has, and the Migrator brings them as they are. The «Parteneri» window shows the ones it handles
    (slice 0093); the client tells how many of the added ones it does not list.
  * A row without CodPartener, or with a CodPartener over 50 characters, is skipped and named in `probleme`.
  * Provenienta = 'avacont' (the value the Access sync routes always wrote).
"""
import logging

import mysql.connector
from flask import g, request

from routes.auth.guard import require_session
from utils.database import get_kbot_connection

from . import forexe_bp
from .parteneri_edit import _json_utf8, _rollback, _text

logger = logging.getLogger(__name__)

_MAX_BATCH = 1000
_MAX_COD = 50
_MAX_TEXT = 255
_MAX_PROBLEMS = 20
_PROVENIENTA = "avacont"

_SQL_UNIT = "SELECT IdUnitate FROM Unitati WHERE IdUnitate = %s"
_SQL_INSERT = (
    "INSERT INTO Parteneri (IdUnitate, CodPartener, DenumirePartener, CodFiscal, ContIBAN, "
    "Banca, Adresa, Tip, Ascuns, Provenienta) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, '" + _PROVENIENTA + "')"
)


def _key(cod: str) -> str:
    """The way the unique key compares codes: no case, no trailing spaces."""
    return cod.rstrip().casefold()


def _cut(value) -> "str | None":
    text = _text(value)
    return text[:_MAX_TEXT] if text else None


def _existing_keys(cursor, id_unitate: int, codes: list) -> set:
    found = set()
    for start in range(0, len(codes), 500):
        part = codes[start:start + 500]
        marks = ",".join(["%s"] * len(part))
        cursor.execute(f"SELECT CodPartener FROM Parteneri WHERE IdUnitate = %s AND CodPartener IN ({marks})",
                       (id_unitate, *part))
        found.update(_key(_text(r["CodPartener"])) for r in cursor.fetchall())
    return found


def _ids_of(cursor, id_unitate: int, codes: list) -> list:
    ids = []
    for start in range(0, len(codes), 500):
        part = codes[start:start + 500]
        marks = ",".join(["%s"] * len(part))
        cursor.execute(f"SELECT IdPartener FROM Parteneri WHERE IdUnitate = %s AND CodPartener IN ({marks})",
                       (id_unitate, *part))
        ids.extend(int(r["IdPartener"]) for r in cursor.fetchall())
    return ids


def _import(cursor, id_unitate: int, partners: list) -> dict:
    problems = []
    seen = set()
    valid = []          # (cod, row) in the order Access sent them
    skipped = 0
    for row in partners:
        if not isinstance(row, dict):
            skipped += 1
            continue
        cod = _text(row.get("cod_partener"))
        if not cod:
            skipped += 1
            if len(problems) < _MAX_PROBLEMS:
                problems.append(f"Partenerul «{_text(row.get('denumire'))}» nu are cod partener.")
            continue
        if len(cod) > _MAX_COD:
            skipped += 1
            if len(problems) < _MAX_PROBLEMS:
                problems.append(f"Codul partenerului «{cod[:20]}…» are peste {_MAX_COD} de caractere.")
            continue
        if _key(cod) in seen:
            skipped += 1
            continue
        seen.add(_key(cod))
        valid.append((cod, row))

    present = _existing_keys(cursor, id_unitate, [cod for cod, _ in valid])
    fresh = [(cod, row) for cod, row in valid if _key(cod) not in present]
    if fresh:
        cursor.executemany(_SQL_INSERT, [
            (id_unitate, cod, _cut(row.get("denumire")), _cut(row.get("cod_fiscal")), _cut(row.get("cont_iban")),
             _cut(row.get("banca")), _cut(row.get("adresa")), _cut(row.get("tip")),
             1 if row.get("ascuns") else 0)
            for cod, row in fresh])
    return {"primite": len(partners), "adaugate": len(fresh), "existente": len(valid) - len(fresh),
            "sarite": skipped, "ids": _ids_of(cursor, id_unitate, [cod for cod, _ in fresh]) if fresh else [],
            "probleme": problems}


@forexe_bp.route("/api/forexe/nomenclatoare/parteneri/preia-access", methods=["POST"])
@require_session
def post_preia_parteneri_access():
    """Adds the partners of one Access unit file that K-BOT does not have yet (all or nothing for the batch)."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _json_utf8({"error": "Corp JSON lipsă sau nevalid."}, 400)
    partners = body.get("partners")
    try:
        id_unitate = int(body.get("id_unitate"))
    except (TypeError, ValueError):
        return _json_utf8({"error": "Lipsește unitatea."}, 400)
    if not isinstance(partners, list):
        return _json_utf8({"error": "Lipsește lista de parteneri."}, 400)
    if len(partners) > _MAX_BATCH:
        return _json_utf8({"error": f"Se pot trimite cel mult {_MAX_BATCH} de parteneri odată."}, 400)

    db_name = g.session.db_name
    conn = None
    try:
        conn = get_kbot_connection(db_name)
        conn.autocommit = False
        cursor = conn.cursor(dictionary=True, buffered=True)
        if not conn.in_transaction:
            conn.start_transaction()
        cursor.execute(_SQL_UNIT, (id_unitate,))
        if cursor.fetchone() is None:
            _rollback(conn)
            return _json_utf8({"error": f"Unitatea {id_unitate} nu există în această bază de date."}, 400)
        result = _import(cursor, id_unitate, partners)
        conn.commit()
        logger.info("[forexe.parteneri_access] %s unit %s: %s received, %s added, %s existing, %s skipped",
                    db_name, id_unitate, result["primite"], result["adaugate"], result["existente"], result["sarite"])
        return _json_utf8(result, 200)
    except mysql.connector.IntegrityError as e:
        _rollback(conn)
        if e.errno == 1062:
            return _json_utf8({"error": "Un partener a apărut între timp în unitate; repetați preluarea."}, 409)
        logger.error("[forexe.parteneri_access] %s: %s", db_name, e, exc_info=True)
        return _json_utf8({"error": f"Eroare la preluarea partenerilor: {e}"}, 500)
    except Exception as e:
        _rollback(conn)
        logger.error("[forexe.parteneri_access] %s: %s", db_name, e, exc_info=True)
        return _json_utf8({"error": f"Eroare la preluarea partenerilor: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()
