# routes/avacont/configurari/jurnale/jurnale.py
"""
The accounting journals of the unit database (slice AV01-09, table TZ_Jurnale; the Access `Jurnale`).

Every journal belongs to a SECTOR + SOURCE (SursaSector, 01A ...), like the accounts: the window shows the ones of the sector+source of the title bar, or all of
them on «Toate» (then the sector+source is typed on the journal). The code is unique PER sector+source.

    GET  /api/avacont/jurnale
        -> 200 { "jurnale": [ { "idjurnal", "sursasector", "cod", "denumire" } ],
                 "surse": [ { "cod", "denumire" } ] }
        Every journal of the unit database, ordered by sector+source and code (the window filters); "surse" = every sector+source (DefaSursaSector).

    POST /api/avacont/jurnale
        { "randuri": [ { "idjurnal": n | null, "sursasector", "cod", "denumire" } ], "sterse": [ idjurnal, ... ] }
        -> 200 { "randuri": n }.  ONE transaction: the journals in "sterse" are deleted, then every row is updated (idjurnal given) or inserted
        (idjurnal null). The code is trimmed and written in capitals; it is 1 to 10 of A-Z, 0-9, _ and unique inside its sector+source. Only the
        rows the window shows are sent: a journal that is not in the list and not in "sterse" is left alone.
        400 for an invalid row / an unknown sector+source, 409 for a code that already exists or a journal that is used and cannot be deleted.

A journal that is used (a note, a template) is protected by the RESTRICT foreign keys of those tables (they come with them); until
then nothing uses a journal. A change of code is allowed: later tables point at IdJurnal, not at the code.
"""
import logging
import re

import mysql.connector
from mysql.connector import errorcode
from flask import g, request

from routes.auth.guard import require_session
from utils.database import get_kbot_connection

from ... import avacont_bp
from ...common import json_utf8, rollback

logger = logging.getLogger(__name__)

_COD = re.compile(r"^[A-Z0-9_]{1,10}$")


@avacont_bp.route("/api/avacont/jurnale", methods=["GET"])
@require_session
def get_jurnale():
    """Every journal of the unit database and every sector+source."""
    db_name = g.session.db_name
    conn = None
    try:
        conn = get_kbot_connection(db_name)
        cursor = conn.cursor()
        cursor.execute("SELECT IdJurnal, SursaSector, Cod, Denumire FROM TZ_Jurnale ORDER BY SursaSector, Cod")
        lista = [{"idjurnal": int(r[0]), "sursasector": r[1], "cod": r[2], "denumire": r[3] or ""} for r in cursor.fetchall()]
        cursor.execute("SELECT SursaSector, Denumire FROM AVACONT_COMUN.DefaSursaSector ORDER BY SursaSector")
        surse = [{"cod": r[0], "denumire": r[1] or ""} for r in cursor.fetchall()]
        logger.info("[avacont.jurnale] %s: %s jurnale", db_name, len(lista))
        return json_utf8({"jurnale": lista, "surse": surse}, 200)
    except Exception as e:
        logger.error("[avacont.jurnale] citire %s: %s", db_name, e, exc_info=True)
        return json_utf8({"error": f"Eroare la citirea jurnalelor: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()


def _valideaza(corp: dict):
    """(randuri, sterse, error): the body checked; error is the Romanian message of the first problem."""
    randuri_brut = corp.get("randuri")
    sterse_brut = corp.get("sterse", [])
    if not isinstance(randuri_brut, list) or not isinstance(sterse_brut, list):
        return None, None, "Lista de jurnale lipsește."
    sterse = []
    for s in sterse_brut:
        try:
            sterse.append(int(s))
        except (TypeError, ValueError):
            return None, None, "Un jurnal de șters nu este valid."
    randuri = []
    chei = set()
    for i, r in enumerate(randuri_brut, start=1):
        if not isinstance(r, dict):
            return None, None, f"Jurnalul, rândul {i}: date nevalide."
        ss = str(r.get("sursasector") or "").strip().upper()
        cod = str(r.get("cod") or "").strip().upper()
        denumire = str(r.get("denumire") or "").strip()
        if not ss:
            return None, None, f"Jurnalul, rândul {i}: alegeți sectorul și sursa."
        if not _COD.match(cod):
            return None, None, (f"Jurnalul, rândul {i}: codul trebuie să aibă de la 1 la 10 caractere, "
                                "doar litere, cifre și _.")
        if len(denumire) > 255:
            return None, None, f"Jurnalul {cod}: denumirea este prea lungă."
        if (ss, cod) in chei:
            return None, None, f"Codul {cod} apare de două ori la {ss}."
        chei.add((ss, cod))
        idj = r.get("idjurnal")
        if idj is not None:
            try:
                idj = int(idj)
            except (TypeError, ValueError):
                return None, None, f"Jurnalul {cod}: număr nevalid."
        randuri.append((idj, ss, cod, denumire))
    return randuri, sterse, None


@avacont_bp.route("/api/avacont/jurnale", methods=["POST"])
@require_session
def post_jurnale():
    """Writes the journals the window shows."""
    corp = request.get_json(silent=True)
    if not isinstance(corp, dict):
        return json_utf8({"error": "Corp JSON lipsă sau nevalid."}, 400)
    randuri, sterse, eroare = _valideaza(corp)
    if eroare:
        return json_utf8({"error": eroare}, 400)

    db_name = g.session.db_name
    conn = None
    try:
        conn = get_kbot_connection(db_name)
        conn.autocommit = False
        cursor = conn.cursor()
        if not conn.in_transaction:
            conn.start_transaction()
        cursor.execute("SELECT SursaSector FROM AVACONT_COMUN.DefaSursaSector")
        cunoscute = {r[0] for r in cursor.fetchall()}
        for _, ss, _, _ in randuri:
            if ss not in cunoscute:
                rollback(conn)
                return json_utf8({"error": f"Sectorul și sursa «{ss}» nu există."}, 400)
        for idj in sterse:
            cursor.execute("SELECT Cod, SursaSector FROM TZ_Jurnale WHERE IdJurnal = %s", (idj,))
            gasit = cursor.fetchone()
            if not gasit:
                continue
            try:
                cursor.execute("DELETE FROM TZ_Jurnale WHERE IdJurnal = %s", (idj,))
            except mysql.connector.Error as e:
                if e.errno == errorcode.ER_ROW_IS_REFERENCED_2:
                    rollback(conn)
                    return json_utf8({"error": f"Jurnalul {gasit[0]} ({gasit[1]}) este folosit (operații sau șabloane) și nu se poate șterge."}, 409)
                raise
        # Codes are checked row by row: a code that moves from one journal to another inside the same save is refused, not guessed.
        for idj, ss, cod, denumire in randuri:
            try:
                if idj is None:
                    cursor.execute("INSERT INTO TZ_Jurnale (SursaSector, Cod, Denumire) VALUES (%s, %s, %s)", (ss, cod, denumire))
                else:
                    cursor.execute("UPDATE TZ_Jurnale SET SursaSector = %s, Cod = %s, Denumire = %s WHERE IdJurnal = %s",
                                   (ss, cod, denumire, idj))
            except mysql.connector.Error as e:
                if e.errno == errorcode.ER_DUP_ENTRY:
                    rollback(conn)
                    return json_utf8({"error": f"Există deja un jurnal cu codul {cod} la {ss}."}, 409)
                raise
        conn.commit()
        logger.info("[avacont.jurnale] %s: %s jurnale salvate, %s sterse", db_name, len(randuri), len(sterse))
        return json_utf8({"randuri": len(randuri)}, 200)
    except mysql.connector.Error as e:
        rollback(conn)
        logger.error("[avacont.jurnale] salvare %s: %s", db_name, e, exc_info=True)
        return json_utf8({"error": f"Eroare la salvarea jurnalelor: {e}"}, 500)
    except Exception as e:
        rollback(conn)
        logger.error("[avacont.jurnale] salvare %s: %s", db_name, e, exc_info=True)
        return json_utf8({"error": f"Eroare la salvarea jurnalelor: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()
