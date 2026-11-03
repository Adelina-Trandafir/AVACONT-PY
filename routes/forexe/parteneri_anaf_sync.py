# routes/forexe/parteneri_anaf_sync.py
"""
«Selecteaza Parteneri Sincronizare Anaf» / «Sincronizeaza ANAF» of the «Parteneri» window (slice 0093-02, operator 03.11.2026):
the name and the address of ONE partner are read again from ANAF and written into Parteneri.

    POST /api/forexe/nomenclatoare/parteneri/<id_partener>/anaf-sync
        -> 200 { "rezultat": "ok" | "negasit" | "incomplet" | "cod_invalid",
                 "denumire": "...", "adresa": "...", "modificat": true|false, "sincronizat": "yyyy-mm-dd hh:mm:ss" | null }
        -> 404 the partner is gone | 502 ANAF down | 500 error (also: the table Parteneri_Anaf does not exist yet)

The window calls it once per ticked partner, one after the other, so it can show the progress and stop on the first
«ANAF down». One call = one ANAF request.

RULES
  * Only a fiscal code that is plain digits, longer than 5 and shorter than 13 (operator's rule: the field also holds CNPs and
    other junk). The leading «RO» and spaces are ignored; anything else makes the code «cod_invalid» and nothing is asked.
  * "ok" = ANAF knew the code: DenumirePartener and Adresa are overwritten with ANAF's (an empty ANAF address leaves the stored
    one), and the partner is marked synchronised (table Parteneri_Anaf, sql/0093_02_Parteneri_Anaf.sql). "negasit" / "incomplet" /
    "cod_invalid" change nothing and leave the partner NOT synchronised, so «Selecteaza TOTI partenerii NESINCRONIZATI» finds it again.
  * The row that was asked for is written, and (slice 0093-03) so are the other Parteneri rows of the same CodFiscal (other units): the window
    shows one row per fiscal code, the rest of K-BOT groups by it. Only the asked row is marked synchronised.
  * The calls are spaced at least _MIN_GAP seconds apart (per server process). UNVERIFIED: ANAF's rate limit on v9; one request per
    second is the figure usually quoted for this service.
"""
import logging
import threading
import time

import mysql.connector
from flask import g

from routes.auth.guard import require_session
from routes.inregistrare import anaf
from utils.database import get_kbot_connection

from . import forexe_bp
from .parteneri_edit import _json_utf8, _rollback, _text, propagate_to_same_cf

logger = logging.getLogger(__name__)

_MIN_DIGITS = 6
_MAX_DIGITS = 12
_MAX_TEXT = 255
_MIN_GAP = 1.1

_gap_lock = threading.Lock()
_last_call = 0.0

_SQL_ONE = "SELECT IdPartener, CodFiscal, DenumirePartener, Adresa FROM Parteneri WHERE IdPartener = %s"
_SQL_UPDATE = "UPDATE Parteneri SET DenumirePartener = %s, Adresa = %s WHERE IdPartener = %s"
_SQL_MARK = (
    "INSERT INTO Parteneri_Anaf (IdPartener, DataSincronizare) VALUES (%s, NOW()) "
    "ON DUPLICATE KEY UPDATE DataSincronizare = NOW()"
)
_SQL_MARKED = "SELECT DataSincronizare FROM Parteneri_Anaf WHERE IdPartener = %s"
_ER_NO_TABLE = 1146


def sync_cf(raw) -> str:
    """The digits of a fiscal code the synchronisation may ask ANAF about, or '' (RO prefix and spaces ignored)."""
    text = _text(raw).upper().replace(" ", "")
    if text.startswith("RO"):
        text = text[2:]
    if not text.isdigit() or not (_MIN_DIGITS <= len(text) <= _MAX_DIGITS):
        return ""
    return text


def _wait_turn():
    """Keeps two ANAF requests of this process at least _MIN_GAP seconds apart."""
    global _last_call
    with _gap_lock:
        pause = _MIN_GAP - (time.monotonic() - _last_call)
        if pause > 0:
            time.sleep(pause)
        _last_call = time.monotonic()


def _cut(value) -> str:
    return _text(value)[:_MAX_TEXT]


def _stamp(value):
    return value.strftime("%Y-%m-%d %H:%M:%S") if value is not None else None


@forexe_bp.route("/api/forexe/nomenclatoare/parteneri/<int:id_partener>/anaf-sync", methods=["POST"])
@require_session
def post_partener_anaf_sync(id_partener):
    """Reads one partner's name and address again from ANAF and stores them."""
    db_name = g.session.db_name
    conn = None
    try:
        conn = get_kbot_connection(db_name)
        conn.autocommit = False
        cursor = conn.cursor(dictionary=True, buffered=True)
        if not conn.in_transaction:
            conn.start_transaction()
        cursor.execute(_SQL_ONE, (id_partener,))
        stored = cursor.fetchone()
        if stored is None:
            _rollback(conn)
            return _json_utf8({"error": "Partenerul nu mai există în baza de date."}, 404)

        cf = sync_cf(stored["CodFiscal"])
        if not cf:
            _rollback(conn)
            return _json_utf8({"rezultat": "cod_invalid", "modificat": False, "sincronizat": None}, 200)

        try:
            _wait_turn()
            found = anaf.lookup(cf)
        except anaf.AnafNotFound:
            _rollback(conn)
            return _json_utf8({"rezultat": "negasit", "modificat": False, "sincronizat": None}, 200)
        except anaf.AnafIncomplete:
            _rollback(conn)
            return _json_utf8({"rezultat": "incomplet", "modificat": False, "sincronizat": None}, 200)
        except anaf.AnafUnavailable as e:
            _rollback(conn)
            return _json_utf8({"error": f"Serverul ANAF nu răspunde acum ({e})."}, 502)

        denumire = _cut(found["denumire"])
        adresa = _cut(found["adresa"]) or _text(stored["Adresa"])
        changed = denumire != _text(stored["DenumirePartener"]) or adresa != _text(stored["Adresa"])
        if changed:
            cursor.execute(_SQL_UPDATE, (denumire, adresa or None, id_partener))
        # The same supplier in other units (rest of K-BOT groups by CodFiscal) gets ANAF's name and address too, and counts as synchronised.
        propagate_to_same_cf(cursor, id_partener, stored["CodFiscal"], denumire, adresa or None)
        cursor.execute(_SQL_MARK, (id_partener,))
        cursor.execute(_SQL_MARKED, (id_partener,))
        marked = cursor.fetchone()
        conn.commit()
        logger.info("[forexe.parteneri_anaf_sync] %s: partner %s synchronised (changed=%s)", db_name, id_partener, changed)
        return _json_utf8({"rezultat": "ok", "denumire": denumire, "adresa": adresa, "modificat": changed,
                           "sincronizat": _stamp(marked["DataSincronizare"]) if marked else None}, 200)
    except mysql.connector.Error as e:
        _rollback(conn)
        if getattr(e, "errno", None) == _ER_NO_TABLE:
            logger.error("[forexe.parteneri_anaf_sync] %s: table Parteneri_Anaf is missing", db_name)
            return _json_utf8({"error": "Tabela Parteneri_Anaf nu există încă în baza de date "
                                        "(scriptul 0093_02_Parteneri_Anaf.sql trebuie rulat)."}, 500)
        logger.error("[forexe.parteneri_anaf_sync] %s/%s: %s", db_name, id_partener, e, exc_info=True)
        return _json_utf8({"error": f"Eroare la sincronizarea cu ANAF: {e}"}, 500)
    except Exception as e:
        _rollback(conn)
        logger.error("[forexe.parteneri_anaf_sync] %s/%s: %s", db_name, id_partener, e, exc_info=True)
        return _json_utf8({"error": f"Eroare la sincronizarea cu ANAF: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()
