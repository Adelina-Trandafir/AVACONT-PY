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

from routes.auth.guard import require_session    # bearer opac (Felia 1 auth)
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


# GET list query — mirrors mdl_FX_PopulareTree.Angajamente_SQL (DESCRIERE order).
#   Main branch : FX_Angajamente FA LEFT JOIN FX_Indicatori FI, filtered by
#                 COALESCE(FI.IdUnitate,0) = :id_unitate, grouped by the FA columns,
#                 Surse = GROUP_CONCAT(DISTINCT FI.SS ...) (replaces ConcatRelated),
#                 O = 0.
#   Orphan branch: angajamente with NO rows in FX_Indicatori, filtered by
#                 FA.DC = :db_name, Surse = NULL, O = 1.
#   doar_anulate=1 : the main filter becomes the legacy anulate/suspendat/ascuns
#                 condition instead of the IdUnitate filter.
#   cod_angajament : narrows BOTH branches to that code (single-row lookup).
#   Final ORDER BY O, Descriere.
# FA.Salarii is deliberately NOT selected: the column still exists on
# FX_Angajamente (old system) but is deprecated and unused by the new one.
_MAIN_SELECT = (
    "SELECT FA.CodAngajament AS Cod, FA.IDDF AS IDDF, FA.Descriere AS Descriere, "
    "FA.Stare AS Stare, FA.Incarcat AS Incarcat, FA.Preluat AS Preluat, "
    "FA.ASCUNS AS Ascuns, FA.DataCreare AS DataCreare, "
    "GROUP_CONCAT(DISTINCT FI.SS ORDER BY FI.SS SEPARATOR ';') AS Surse, 0 AS O "
    "FROM FX_Angajamente FA "
    "LEFT JOIN FX_Indicatori FI ON FA.CodAngajament = FI.CodAngajament "
)
_MAIN_GROUP_BY = (
    " GROUP BY FA.CodAngajament, FA.IDDF, FA.Descriere, FA.Stare, FA.Incarcat, "
    "FA.Preluat, FA.ASCUNS, FA.DataCreare "
)
_ORPHAN_SELECT = (
    "SELECT FA.CodAngajament AS Cod, FA.IDDF AS IDDF, FA.Descriere AS Descriere, "
    "FA.Stare AS Stare, FA.Incarcat AS Incarcat, FA.Preluat AS Preluat, "
    "FA.ASCUNS AS Ascuns, FA.DataCreare AS DataCreare, "
    "NULL AS Surse, 1 AS O "
    "FROM FX_Angajamente FA "
    "WHERE FA.CodAngajament NOT IN "
    "(SELECT CodAngajament FROM FX_Indicatori WHERE CodAngajament IS NOT NULL) "
    "AND FA.DC = %s "
)


@forexe_bp.route("/api/forexe/angajamente", methods=["GET"])
@require_session
def get_angajamente():
    """List angajamente for the MainForm list view (mirrors Angajamente_SQL).

    Query: db_name (required), id_unitate (required, int), doar_anulate (0/1,
    default 0), cod_angajament (optional single-row lookup).
    Returns { db_name, count, rows: [ {Cod, Descriere, Stare, IDDF, Surse,
    Incarcat, Preluat, Ascuns, DataCreare}, ... ] }; Surse is null for
    orphans. Rows sort by O (main before orphans), then Descriere.
    """
    db_name = request.args.get("db_name")
    if db_name is None or str(db_name).strip() == "":
        return jsonify({"error": "Parametru lipsa: db_name"}), 400

    id_unitate_raw = request.args.get("id_unitate")
    if id_unitate_raw is None or str(id_unitate_raw).strip() == "":
        return jsonify({"error": "Parametru lipsa: id_unitate"}), 400
    try:
        id_unitate = int(id_unitate_raw)
    except (TypeError, ValueError):
        return jsonify({"error": "id_unitate invalid (asteptat intreg)"}), 400

    doar_anulate = str(request.args.get("doar_anulate", "0")).strip() == "1"
    cod_angajament = request.args.get("cod_angajament")
    if cod_angajament is not None and str(cod_angajament).strip() == "":
        cod_angajament = None

    try:
        db_name = _validate_db_name(db_name)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    # Assemble the main branch WHERE + params in placeholder order.
    main_where_parts = []
    main_params = []
    if doar_anulate:
        # Legacy: InStr('Anulat')>0 OR InStr('Suspendat')>0 OR Ascuns.
        main_where_parts.append(
            "(FA.Stare LIKE '%Anulat%' OR FA.Stare LIKE '%Suspendat%' OR FA.ASCUNS = 1)"
        )
    else:
        main_where_parts.append("COALESCE(FI.IdUnitate, 0) = %s")
        main_params.append(id_unitate)
    if cod_angajament is not None:
        main_where_parts.append("FA.CodAngajament = %s")
        main_params.append(cod_angajament)

    orphan_where_extra = ""
    orphan_params = [db_name]
    if cod_angajament is not None:
        orphan_where_extra = " AND FA.CodAngajament = %s"
        orphan_params.append(cod_angajament)

    sql = (
        "SELECT * FROM ("
        + _MAIN_SELECT
        + " WHERE " + " AND ".join(main_where_parts)
        + _MAIN_GROUP_BY
        + " UNION ALL "
        + _ORPHAN_SELECT
        + orphan_where_extra
        + ") AS T ORDER BY O, Descriere"
    )
    params = tuple(main_params) + tuple(orphan_params)

    conn = None
    try:
        conn = get_db_connection(db_name)
        cursor = conn.cursor()
        cursor.execute(sql, params)
        rows = []
        for (cod, iddf, descriere, stare, incarcat, preluat,
             ascuns, data_creare, surse, _o) in cursor.fetchall():
            rows.append({
                "Cod": cod,
                "Descriere": descriere,
                "Stare": stare,
                "IDDF": iddf,
                "Surse": surse,
                "Incarcat": bool(incarcat),
                "Preluat": bool(preluat),
                "Ascuns": bool(ascuns),
                "DataCreare": data_creare.isoformat() if data_creare is not None else None,
            })
        return jsonify({"db_name": db_name, "count": len(rows), "rows": rows}), 200
    except Exception as e:
        logger.error(f"[forexe.angajamente.get] {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500
    finally:
        if conn is not None:
            conn.close()


@forexe_bp.route("/api/forexe/angajamente/upsert", methods=["POST"])
@require_session
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
