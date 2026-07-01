# routes/forexe/angajamente.py
"""
Ruta upsert pentru FX_Angajamente (fluxul ListaAngajamente).

Contract (POST /api/forexe/angajamente/upsert):
    { "db_name": "<unit db>",
      "rows": [ { "Cod": "...", "Descriere": "...", "Stare": "..." }, ... ] }

Semantica upsert (decizie blocata): INSERT seteaza DC + Preluat=1; la duplicat
se reimprospateaza DOAR Descriere si Stare (Preluat/DC NU se ating la update).
"""
import logging

from flask import request, jsonify

from utils.security import require_api_key       # R5 verificat: security.py:8
from utils.database import get_db_connection     # R5 verificat: database.py:9
# R5 verificat: validatorul canonic db_name traieste in routes/admin.py:24
# (DB_NAME_REGEX = ^[A-Za-z0-9_]+$, ridica ValueError("db_name invalid")).
from routes.admin import _validate_db_name

from . import forexe_bp

logger = logging.getLogger(__name__)

# Bulk-upsert. Insert seteaza DC + Preluat=1; update reimprospateaza doar
# Descriere/Stare (decizie 1). CodAngajament este PRIMARY KEY.
_UPSERT_SQL = (
    "INSERT INTO FX_Angajamente (CodAngajament, Descriere, Stare, DC, Preluat) "
    "VALUES (%s, %s, %s, %s, 1) "
    "ON DUPLICATE KEY UPDATE Descriere = VALUES(Descriere), Stare = VALUES(Stare)"
)


@forexe_bp.route("/api/forexe/angajamente/upsert", methods=["POST"])
@require_api_key
def upsert_angajamente():
    data = request.json or {}
    db_name = data.get("db_name")
    rows = data.get("rows")

    # `is None`: un payload cu rows=[] este valid (0 randuri), nu lipsa.
    if db_name is None or rows is None:
        return jsonify({"error": "Parametri lipsa: db_name / rows"}), 400
    if not isinstance(rows, list):
        return jsonify({"error": "rows trebuie sa fie lista"}), 400

    try:
        db_name = _validate_db_name(db_name)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    # Construim valorile; sarim peste randurile cu Cod gol
    # (mirror VBA: If IsEmpty(dRow("Cod")) Then skip).
    values = []
    for r in rows:
        cod = r.get("Cod")
        if cod is None or str(cod).strip() == "":
            continue
        values.append((cod, r.get("Descriere"), r.get("Stare"), db_name))

    if not values:
        return jsonify({"status": "success", "received": len(rows), "written": 0}), 200

    conn = None
    try:
        conn = get_db_connection(db_name)
        cursor = conn.cursor()
        conn.start_transaction()
        cursor.executemany(_UPSERT_SQL, values)
        conn.commit()
        logger.info(
            f"[forexe.angajamente.upsert] {db_name}: received={len(rows)} "
            f"candidates={len(values)}"
        )
        # NOTA: cu ON DUPLICATE KEY, MariaDB numara 1 per insert, 2 per update,
        # deci rowcount NU este un "written" curat. Returnat doar diagnostic.
        return jsonify({
            "status": "success",
            "received": len(rows),
            "candidates": len(values),
            "rowcount": cursor.rowcount,
        }), 200
    except Exception as e:
        if conn is not None:
            conn.rollback()
        logger.error(f"[forexe.angajamente.upsert] {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500
    finally:
        if conn is not None:
            conn.close()
