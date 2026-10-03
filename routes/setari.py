# routes/setari.py
"""
Slice 0100-02 -- the settings the SERVER decides (table `Setari`, one per database).

Route:
    GET /api/setari
    200 { "db_name": "<unit db>", "count": N,
          "rows": [ {"Cheie", "TextVizibil", "Valoare", "Tip"}, ... ] }

`Valoare` is stored as text (sql/0100_02_setari.sql) and answered in the kind `Tip` says:
    int  -> a JSON number          date -> an ISO string ("2026-10-01" / "2026-10-01T12:30:00")
    text -> a JSON string          a value that does not fit its Tip -> null (logged)

Scope: the connected database IS the unit (g.session.db_name), like every /api/forexe/* route --
never a parameter of the request.

A database the DDL has not reached (no `Setari` table) answers 200 with NO rows, not an error:
K-BOT reads «no row» as «setting off», so a unit that was never given the table simply keeps
every server-controlled feature off.
"""
import json
import logging
from datetime import datetime

from flask import Blueprint, g, current_app

from routes.auth.guard import require_session
from utils.database import get_kbot_connection

logger = logging.getLogger(__name__)

setari_bp = Blueprint("setari", __name__)

_SQL_TABLE = ("SELECT COUNT(*) FROM information_schema.TABLES "
              "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'Setari'")
_SQL_ROWS = "SELECT Cheie, TextVizibil, Valoare, Tip FROM Setari ORDER BY Cheie"


def _json_utf8(payload, status):
    """JSON answer with LITERAL diacritics (ensure_ascii=False): TextVizibil is Romanian."""
    body = json.dumps(payload, ensure_ascii=False)
    return current_app.response_class(body, status=status, mimetype="application/json")


def _typed(cheie, valoare, tip):
    """Valoare in the kind Tip names; None when it is NULL or does not fit (the miss is logged)."""
    if valoare is None:
        return None
    text = str(valoare).strip()
    try:
        if tip == "int":
            return int(text)
        if tip == "date":
            if len(text) <= 10:
                return datetime.strptime(text, "%Y-%m-%d").date().isoformat()
            return datetime.fromisoformat(text).isoformat()
        return str(valoare)
    except ValueError:
        logger.warning("[setari] %s: valoarea %r nu este de tipul %s", cheie, valoare, tip)
        return None


@setari_bp.route("/api/setari", methods=["GET"])
@require_session
def get_setari():
    db_name = g.session.db_name
    conn = None
    try:
        conn = get_kbot_connection(db_name)
        cursor = conn.cursor()
        cursor.execute(_SQL_TABLE, (db_name,))
        if int(cursor.fetchone()[0] or 0) == 0:
            logger.info("[setari] %s: tabelul Setari nu exista (sql/0100_02_setari.sql) -> 0 randuri", db_name)
            return _json_utf8({"db_name": db_name, "count": 0, "rows": []}, 200)
        cursor.execute(_SQL_ROWS)
        rows = [{"Cheie": cheie,
                 "TextVizibil": text,
                 "Valoare": _typed(cheie, valoare, tip),
                 "Tip": tip}
                for (cheie, text, valoare, tip) in cursor.fetchall()]
        return _json_utf8({"db_name": db_name, "count": len(rows), "rows": rows}, 200)
    except Exception as e:
        # No swallowing: a read error says why, it does not pretend the settings are all off.
        logger.error(f"[setari] {e}", exc_info=True)
        return _json_utf8({"error": f"Eroare la citirea setărilor: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()
