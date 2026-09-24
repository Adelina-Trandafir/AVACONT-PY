# routes/forexe/marcaj.py
"""
The K-BOT id markers (slice 0076) -- POST /api/forexe/marcaj/rezerva, plus the helpers the
ingest (prelucrare_pasi.py) and the DDF save (ddf_edit.py) use to honour them.

WHAT A MARKER IS. When the operator saves in the FOREXE page, the K-BOT menu inside the page
appends a short text to what they typed, naming the ids the save WILL become in K-BOT:

  * a changed reservation row: its «Motiv» gets "(IDREV: n)"  -- the DDF revision to come;
  * a reception (new or changed): its «Descriere» gets "(IDRH: n; IDR: m)" -- the snapshot
    (FX_Receptii_H) the save produces and the first of its lines (FX_Receptii).

FOREXE copies the text into its history, so when the history is read back the ingest knows
directly which record a history row belongs to, instead of deducing it. The format lives in
prelucrare_helpers (`compune_marcaj` / `extract_marcaj`); this file RESERVES the numbers.

HOW A NUMBER IS RESERVED. Reading MAX(id)+1 is not a reservation: the next AUTO_INCREMENT
insert of anyone else would take the same number. So the table's own counter is advanced:
a placeholder row is inserted and deleted again in the same transaction. InnoDB never hands
out a counter value twice (MariaDB keeps the counter across restarts since 10.2.4), so the
number is ours, and later an INSERT naming it explicitly cannot collide with an automatic
one. The placeholder is the only row this route writes into the three data tables, and it
is gone before the commit.

The number is also written to FX_NumberLock (the lock table of slice 0051) as bookkeeping:
which angajament it was reserved for, by whom, until when. The ingest consumes it there.

ONE REVISION PER ANGAJAMENT AT A TIME. A reservation edit session is several saves
(operator, 23.09.2026: «ask after each save if they're done») that together become ONE DDF
revision. So while an IDREV lock is alive for an angajament, the route hands the SAME number
back; a new one is reserved only when that one was consumed (the revision was saved) or
expired. Receptions are the opposite: every save is a new snapshot, so every request is a
new pair.
"""
import json
import logging
from datetime import datetime, timedelta

from flask import request, g, current_app

from routes.auth.guard import require_session
from utils.database import get_kbot_connection

from . import forexe_bp
from .prelucrare_helpers import compune_marcaj

logger = logging.getLogger(__name__)

TIP_REZERVARE = "rezervare"
TIP_RECEPTIE = "receptie"

LOCK_IDREV = "IDREV"
LOCK_IDRH = "IDRH"
LOCK_IDR = "IDR"

# An IDREV waits for the DDF revision, which the operator may write days later.
TTL_IDREV = timedelta(days=30)
# An IDRH / IDR is consumed by the download that follows the save, minutes later. Two days
# cover a download that failed and was repeated the next morning.
TTL_RECEPTIE = timedelta(days=2)

# The three counters a marker can name: table, key column, and the placeholder INSERT.
# Only these names ever reach an SQL string (they are not user input).
_CONTOARE = {
    LOCK_IDREV: ("FX_DDF_REV", "IDREV",
                 "INSERT INTO FX_DDF_REV (CodAngajament, Desc_Scurta) VALUES (%s, 'K-BOT marcaj')"),
    LOCK_IDRH: ("FX_Receptii_H", "IDRH",
                "INSERT INTO FX_Receptii_H (Descriere) VALUES ('K-BOT marcaj')"),
    LOCK_IDR: ("FX_Receptii", "IDR",
               "INSERT INTO FX_Receptii (Descriere) VALUES ('K-BOT marcaj')"),
}

_SQL_SWEEP = "DELETE FROM FX_NumberLock WHERE ExpiraLa < NOW()"
_SQL_LOCK_VIU = (
    "SELECT IdLock, Valoare, ExpiraLa FROM FX_NumberLock "
    " WHERE Tip = %s AND DC = %s AND CodAngajament = %s AND ExpiraLa >= NOW() "
    " ORDER BY IdLock LIMIT 1"
)
_SQL_LOCK_INSERT = (
    "INSERT INTO FX_NumberLock (Tip, DC, Valoare, CodAngajament, Token, Utilizator, ExpiraLa) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s)"
)
_SQL_CONTOR = (
    "SELECT AUTO_INCREMENT AS ai FROM information_schema.TABLES "
    " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s"
)


def _json_utf8(payload, status):
    """A JSON response with LITERAL diacritics (ensure_ascii=False)."""
    body = json.dumps(payload, ensure_ascii=False, default=str)
    return current_app.response_class(body, status=status, mimetype="application/json")


def _avanseaza_contorul(cursor, tip: str, cod: str) -> int:
    """Advance the table's AUTO_INCREMENT by one and return the number it gave."""
    tabela, cheie, insert_sql = _CONTOARE[tip]
    if tip == LOCK_IDREV:
        cursor.execute(insert_sql, (cod,))
    else:
        cursor.execute(insert_sql)
    valoare = int(cursor.lastrowid or 0)
    if valoare <= 0:
        # AUTO_INCREMENT is gone from the key. Loud, never a silent zero.
        raise RuntimeError(f"{tabela}.{cheie} nu a întors o cheie (AUTO_INCREMENT lipsă?)")
    cursor.execute(f"DELETE FROM {tabela} WHERE {cheie} = %s", (valoare,))
    return valoare


def _tine(cursor, tip: str, dc: str, cod: str, valoare: int, token: str,
          utilizator: str, ttl: timedelta) -> datetime:
    expira = datetime.now() + ttl
    cursor.execute(_SQL_LOCK_INSERT, (tip, dc, valoare, cod, token, utilizator, expira))
    return expira


@forexe_bp.route("/api/forexe/marcaj/rezerva", methods=["POST"])
@require_session
def post_marcaj_rezerva():
    """Reserve the ids of the marker the page is about to write.

    Body:   {"tip": "rezervare" | "receptie", "cod": "<CodAngajament>"}
    Answer: rezervare -> {"tip", "cod", "idrev", "marcaj", "expira_la", "refolosit"}
            receptie  -> {"tip", "cod", "idrh", "idr", "marcaj", "expira_la"}
    """
    sarcina = request.get_json(silent=True)
    if not isinstance(sarcina, dict):
        return _json_utf8({"error": "Corp JSON lipsă sau nevalid."}, 400)
    tip = str(sarcina.get("tip") or "").strip().lower()
    if tip not in (TIP_REZERVARE, TIP_RECEPTIE):
        return _json_utf8(
            {"error": f"Tipul de marcaj «{tip}» nu este cunoscut (rezervare sau receptie)."}, 400)
    cod = str(sarcina.get("cod") or "").strip()
    if not cod:
        return _json_utf8({"error": "Câmpul «cod» lipsește."}, 400)

    db_name = g.session.db_name
    # One database is one DC (the same convention the DDF locks follow).
    dc = db_name
    token = getattr(g, "session_token", "") or ""
    utilizator = getattr(g.session, "username", "") or ""

    conn = None
    try:
        conn = get_kbot_connection(db_name)
        conn.autocommit = False
        cursor = conn.cursor(dictionary=True)
        if not conn.in_transaction:
            conn.start_transaction()

        cursor.execute(_SQL_SWEEP)

        if tip == TIP_REZERVARE:
            cursor.execute(_SQL_LOCK_VIU, (LOCK_IDREV, dc, cod))
            viu = cursor.fetchone()
            if viu is not None:
                # The same revision for every save of this edit session; the lock's life is
                # pushed out so a long session does not lose it half way.
                idrev = int(viu["Valoare"])
                expira = datetime.now() + TTL_IDREV
                cursor.execute("UPDATE FX_NumberLock SET ExpiraLa = %s WHERE IdLock = %s",
                               (expira, int(viu["IdLock"])))
                refolosit = True
            else:
                idrev = _avanseaza_contorul(cursor, LOCK_IDREV, cod)
                expira = _tine(cursor, LOCK_IDREV, dc, cod, idrev, token, utilizator, TTL_IDREV)
                refolosit = False
            conn.commit()
            logger.info("[forexe.marcaj] %s: IDREV %s pentru %s (refolosit=%s)",
                        db_name, idrev, cod, refolosit)
            return _json_utf8({"tip": tip, "cod": cod, "idrev": idrev,
                               "marcaj": compune_marcaj(IDREV=idrev),
                               "expira_la": expira.isoformat(), "refolosit": refolosit}, 200)

        idrh = _avanseaza_contorul(cursor, LOCK_IDRH, cod)
        idr = _avanseaza_contorul(cursor, LOCK_IDR, cod)
        expira = _tine(cursor, LOCK_IDRH, dc, cod, idrh, token, utilizator, TTL_RECEPTIE)
        _tine(cursor, LOCK_IDR, dc, cod, idr, token, utilizator, TTL_RECEPTIE)
        conn.commit()
        logger.info("[forexe.marcaj] %s: IDRH %s / IDR %s pentru %s", db_name, idrh, idr, cod)
        return _json_utf8({"tip": tip, "cod": cod, "idrh": idrh, "idr": idr,
                           "marcaj": compune_marcaj(IDRH=idrh, IDR=idr),
                           "expira_la": expira.isoformat()}, 200)
    except Exception as e:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                logger.warning("[forexe.marcaj] rollback esuat", exc_info=True)
        logger.error(f"[forexe.marcaj] rezerva {tip} {cod}: {e}", exc_info=True)
        return _json_utf8({"error": f"Eroare la rezervarea marcajului: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()


# =========================================================================================
# Used by the ingest and by the DDF save
# =========================================================================================

def id_marcaj_utilizabil(cursor, tip: str, valoare) -> bool:
    """
    May a record be inserted with this marker id?

    Two conditions, and both are needed:
      * the id is NOT in the table yet -- a history re-read, or the save phase after the
        proposal phase (which rolled its own insert back), must not collide;
      * the id is BELOW the table's AUTO_INCREMENT counter -- i.e. it was really handed out
        by the counter (by `_avanseaza_contorul`). A number typed by hand above the counter
        would be taken later by an automatic insert, and THAT insert would fail.
    """
    if valoare is None:
        return False
    valoare = int(valoare)
    if valoare <= 0:
        return False
    tabela, cheie, _ = _CONTOARE[tip]
    cursor.execute(f"SELECT 1 FROM {tabela} WHERE {cheie} = %s LIMIT 1", (valoare,))
    if cursor.fetchone() is not None:
        return False
    cursor.execute(_SQL_CONTOR, (tabela,))
    rand = cursor.fetchone()
    contor = int((rand or {}).get("ai") or 0)
    return 0 < valoare < contor


def consuma_lacatul(cursor, tip: str, valoare, cod: str) -> None:
    """The number is used now: its bookkeeping lock goes. Quiet when there is none."""
    if valoare is None:
        return
    cursor.execute("DELETE FROM FX_NumberLock WHERE Tip = %s AND Valoare = %s "
                   " AND CodAngajament = %s", (tip, int(valoare), cod))


def idrev_tinut(cursor, dc: str, cod: str):
    """The IDREV held for this angajament right now (a reservation edit session), or None."""
    cursor.execute(_SQL_LOCK_VIU, (LOCK_IDREV, dc, cod))
    rand = cursor.fetchone()
    return None if rand is None else int(rand["Valoare"])
