# routes/forexe/parteneri_edit.py
"""
The «Parteneri» window of K-BOT (slice 0087-02): the partners of the session's database, their
details and their «coduri angajament» (Parteneri_Coduri).

    GET    /api/forexe/nomenclatoare/parteneri
        -> 200 { "partners": [ { "id_partener", "id_unitate", "ss", "cod_partener", "denumire",
                                 "cod_fiscal", "cont_iban", "banca", "adresa", "tip", "ascuns",
                                 "activ", "coduri": [ { "id", "id_clsf", "clsf", "denumire_clsf",
                                                        "cont_bancar", "cod_ang", "cod_ind" } ] } ],
                 "tipuri": ["1", ...],
                 "clasificatii": [ { "id_clsf", "clsf", "denumire", "ss", "id_unitate" } ],
                 "units": [ { "id_unitate", "ss" } ] }

    POST   /api/forexe/nomenclatoare/parteneri
        { "partner": { "id_partener": null|n, "ss": "02A", "cod_partener", "denumire", "cod_fiscal",
                       "cont_iban", "banca", "adresa", "tip", "ascuns" },
          "coduri": [ { "id": null|n, "id_clsf", "cont_bancar", "cod_ang", "cod_ind" } ],
          "coduri_sterse": [ids] }
        -> 200 { "id_partener": n }

    DELETE /api/forexe/nomenclatoare/parteneri/<id_partener>
        -> 200 { "deleted": 1 } | 409 when the partner is used on documents

MAPPING (MariaDB_Schema/AVACONT_SURSA.sql)
  * Parteneri: IdUnitate NOT NULL, unique (IdUnitate, CodPartener). A new partner goes into the
    unit of the sector-source the window was opened on (the session's SS, sent as "ss").
  * Parteneri_Coduri: unique (IdPartener, IdClsf), CodPartener copied from the partner.
  * "activ" (a partner WITH activity): its IdPartener is on a DDF section A / B row or on an ORD
    table row, or its CodFiscal is on a DDF header. That is every place the schema links a
    partner to a document; the Access form's own rule was not in the export.

WHY DELETE REFUSES A PARTNER WITH ACTIVITY. FX_DDF_REV_SA, FX_DDF_REV_SB and FX_ORD_TBL point at
Parteneri with ON DELETE CASCADE: deleting a used partner would silently delete document rows.
Such a partner can only be hidden («Ascuns»).
"""
import json
import logging

import mysql.connector
from flask import g, current_app, request

from routes.auth.guard import require_session
from utils.database import get_kbot_connection

from . import forexe_bp

logger = logging.getLogger(__name__)

_ACTIVE = (
    "(EXISTS (SELECT 1 FROM FX_DDF_REV_SA A WHERE A.IdPartener = P.IdPartener) "
    " OR EXISTS (SELECT 1 FROM FX_DDF_REV_SB B WHERE B.IdPartener = P.IdPartener) "
    " OR EXISTS (SELECT 1 FROM FX_ORD_TBL T WHERE T.IdPartener = P.IdPartener) "
    " OR (COALESCE(P.CodFiscal, '') <> '' "
    "     AND EXISTS (SELECT 1 FROM FX_DDF D WHERE D.CodFiscal = P.CodFiscal)))"
)
_SQL_PARTNERS = (
    "SELECT P.IdPartener, P.IdUnitate, U.SursaSector, P.CodPartener, P.DenumirePartener, "
    "       P.CodFiscal, P.ContIBAN, P.Banca, P.Adresa, P.Tip, P.Ascuns, "
    f"      {_ACTIVE} AS Activ "
    "  FROM Parteneri P LEFT JOIN Unitati U ON U.IdUnitate = P.IdUnitate "
    " ORDER BY P.DenumirePartener, P.CodPartener"
)
_SQL_CODES = (
    "SELECT K.IdPartenerAng, K.IdPartener, K.IdClsf, C.Clsf, C.Denumire, K.ContBancar, "
    "       K.CodAng, K.CodInd "
    "  FROM Parteneri_Coduri K LEFT JOIN Clasificatii C ON C.IDClsf = K.IdClsf "
    " ORDER BY C.Clsf"
)
_SQL_TIPURI = (
    "SELECT DISTINCT Tip FROM Parteneri WHERE COALESCE(Tip, '') <> '' ORDER BY Tip"
)
_SQL_CLSF = (
    "SELECT IDClsf, Clsf, Denumire, SS, IdUnitate FROM Clasificatii ORDER BY Clsf, SS"
)
_SQL_UNITS = "SELECT IdUnitate, SursaSector FROM Unitati ORDER BY IdUnitate"

_SQL_ONE = "SELECT IdPartener, IdUnitate, CodPartener FROM Parteneri WHERE IdPartener = %s"
_SQL_ONE_ACTIVE = f"SELECT {_ACTIVE} AS Activ FROM Parteneri P WHERE P.IdPartener = %s"
_SQL_INSERT = (
    "INSERT INTO Parteneri (IdUnitate, CodPartener, DenumirePartener, CodFiscal, ContIBAN, "
    "Banca, Adresa, Tip, Ascuns) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
)
_SQL_UPDATE = (
    "UPDATE Parteneri SET CodPartener = %s, DenumirePartener = %s, CodFiscal = %s, ContIBAN = %s, "
    "Banca = %s, Adresa = %s, Tip = %s, Ascuns = %s WHERE IdPartener = %s"
)
_SQL_CODE_INSERT = (
    "INSERT INTO Parteneri_Coduri (IdPartener, CodPartener, IdClsf, ContBancar, CodAng, CodInd) "
    "VALUES (%s, %s, %s, %s, %s, %s)"
)
_SQL_CODE_UPDATE = (
    "UPDATE Parteneri_Coduri SET CodPartener = %s, IdClsf = %s, ContBancar = %s, CodAng = %s, "
    "CodInd = %s WHERE IdPartenerAng = %s AND IdPartener = %s"
)
_SQL_CODE_DELETE = "DELETE FROM Parteneri_Coduri WHERE IdPartenerAng = %s AND IdPartener = %s"
_SQL_CODE_REFRESH = "UPDATE Parteneri_Coduri SET CodPartener = %s WHERE IdPartener = %s"
_SQL_DELETE = "DELETE FROM Parteneri WHERE IdPartener = %s"
_SQL_UNIT_OF_SS = "SELECT IdUnitate FROM Unitati WHERE SursaSector = %s ORDER BY IdUnitate"

# Parteneri.CodPartener is varchar(50); the other text columns are varchar(255).
_MAX = {"cod_partener": 50}


class _Refused(ValueError):
    """A request the operator can fix; the message is Romanian and goes to the `error` field."""


def _json_utf8(payload, status):
    """A JSON response with LITERAL diacritics (ensure_ascii=False)."""
    body = json.dumps(payload, ensure_ascii=False, default=str)
    return current_app.response_class(body, status=status, mimetype="application/json")


def _text(value) -> str:
    return "" if value is None else str(value).strip()


def _field(row: dict, key: str, label: str):
    """Trimmed text, or None when empty; refused past the column width."""
    value = _text(row.get(key))
    limit = _MAX.get(key, 255)
    if len(value) > limit:
        raise _Refused(f"«{label}» are peste {limit} caractere.")
    return value or None


def _rollback(conn):
    if conn is None:
        return
    try:
        conn.rollback()
    except Exception:
        logger.warning("[forexe.parteneri_edit] rollback failed", exc_info=True)


@forexe_bp.route("/api/forexe/nomenclatoare/parteneri", methods=["GET"])
@require_session
def get_parteneri():
    """Every partner of the session's database, with its codes and the lists the window needs."""
    db_name = g.session.db_name
    conn = None
    try:
        conn = get_kbot_connection(db_name)
        cursor = conn.cursor(dictionary=True, buffered=True)
        cursor.execute(_SQL_CODES)
        codes = {}
        for r in cursor.fetchall():
            codes.setdefault(int(r["IdPartener"]), []).append({
                "id": int(r["IdPartenerAng"]),
                "id_clsf": int(r["IdClsf"]),
                "clsf": _text(r["Clsf"]),
                "denumire_clsf": _text(r["Denumire"]),
                "cont_bancar": _text(r["ContBancar"]),
                "cod_ang": _text(r["CodAng"]),
                "cod_ind": _text(r["CodInd"]),
            })
        cursor.execute(_SQL_PARTNERS)
        partners = [{
            "id_partener": int(r["IdPartener"]),
            "id_unitate": int(r["IdUnitate"]),
            "ss": _text(r["SursaSector"]),
            "cod_partener": _text(r["CodPartener"]),
            "denumire": _text(r["DenumirePartener"]),
            "cod_fiscal": _text(r["CodFiscal"]),
            "cont_iban": _text(r["ContIBAN"]),
            "banca": _text(r["Banca"]),
            "adresa": _text(r["Adresa"]),
            "tip": _text(r["Tip"]),
            "ascuns": bool(r["Ascuns"]),
            "activ": bool(r["Activ"]),
            "coduri": codes.get(int(r["IdPartener"]), []),
        } for r in cursor.fetchall()]
        cursor.execute(_SQL_TIPURI)
        tipuri = [_text(r["Tip"]) for r in cursor.fetchall()]
        cursor.execute(_SQL_CLSF)
        clasificatii = [{
            "id_clsf": int(r["IDClsf"]),
            "clsf": _text(r["Clsf"]),
            "denumire": _text(r["Denumire"]),
            "ss": _text(r["SS"]),
            "id_unitate": int(r["IdUnitate"]) if r["IdUnitate"] is not None else None,
        } for r in cursor.fetchall()]
        cursor.execute(_SQL_UNITS)
        units = [{"id_unitate": int(r["IdUnitate"]), "ss": _text(r["SursaSector"])}
                 for r in cursor.fetchall()]
        return _json_utf8({"partners": partners, "tipuri": tipuri,
                           "clasificatii": clasificatii, "units": units}, 200)
    except Exception as e:
        logger.error("[forexe.parteneri_edit] list %s: %s", db_name, e, exc_info=True)
        return _json_utf8({"error": f"Eroare la citirea partenerilor: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()


def _unit_for_new(cursor, ss: str) -> int:
    if not ss:
        raise _Refused("Nu se știe sursa-sectorul pentru partenerul nou.")
    cursor.execute(_SQL_UNIT_OF_SS, (ss,))
    rows = cursor.fetchall()
    if not rows:
        raise _Refused(f"Sursa-sectorul {ss} nu are o unitate în această bază de date.")
    return int(rows[0]["IdUnitate"])


def _save(cursor, body: dict) -> int:
    partner = body.get("partner")
    if not isinstance(partner, dict):
        raise _Refused("Lipsesc datele partenerului.")
    cod = _field(partner, "cod_partener", "Cod partener")
    if cod is None:
        raise _Refused("Codul partenerului este obligatoriu.")
    denumire = _field(partner, "denumire", "Denumire partener")
    if denumire is None:
        raise _Refused("Denumirea partenerului este obligatorie.")
    values = (cod, denumire,
              _field(partner, "cod_fiscal", "Cod fiscal"),
              _field(partner, "cont_iban", "Cont IBAN"),
              _field(partner, "banca", "Banca"),
              _field(partner, "adresa", "Alte detalii"),
              _field(partner, "tip", "Tip"),
              1 if partner.get("ascuns") else 0)

    id_partener = partner.get("id_partener")
    if id_partener:
        id_partener = int(id_partener)
        cursor.execute(_SQL_ONE, (id_partener,))
        if cursor.fetchone() is None:
            raise _Refused("Partenerul nu mai există în baza de date.")
        cursor.execute(_SQL_UPDATE, (*values, id_partener))
        cursor.execute(_SQL_CODE_REFRESH, (cod, id_partener))
    else:
        id_unitate = _unit_for_new(cursor, _text(partner.get("ss")))
        cursor.execute(_SQL_INSERT, (id_unitate, *values))
        id_partener = int(cursor.lastrowid)

    deleted = body.get("coduri_sterse") or []
    if not isinstance(deleted, list):
        raise _Refused("Lista codurilor șterse nu are forma așteptată.")
    for raw_id in deleted:
        cursor.execute(_SQL_CODE_DELETE, (int(raw_id), id_partener))

    codes = body.get("coduri") or []
    if not isinstance(codes, list):
        raise _Refused("Lista codurilor de angajament nu are forma așteptată.")
    for index, row in enumerate(codes, start=1):
        if not isinstance(row, dict):
            raise _Refused(f"Rândul {index} al codurilor nu are forma așteptată.")
        try:
            id_clsf = int(row.get("id_clsf"))
        except (TypeError, ValueError):
            raise _Refused(f"Rândul {index} al codurilor: alegeți clasificația.")
        cont = _field(row, "cont_bancar", "Cont bancar asociat")
        cod_ang = _field(row, "cod_ang", "Cod ang.")
        cod_ind = _field(row, "cod_ind", "Cod ind.")
        row_id = row.get("id")
        if row_id:
            cursor.execute(_SQL_CODE_UPDATE, (cod, id_clsf, cont, cod_ang, cod_ind, int(row_id), id_partener))
        else:
            cursor.execute(_SQL_CODE_INSERT, (id_partener, cod, id_clsf, cont, cod_ang, cod_ind))
    return id_partener


@forexe_bp.route("/api/forexe/nomenclatoare/parteneri", methods=["POST"])
@require_session
def post_partener():
    """Adds or updates one partner and its codes, all or nothing."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _json_utf8({"error": "Corp JSON lipsă sau nevalid."}, 400)

    db_name = g.session.db_name
    conn = None
    try:
        conn = get_kbot_connection(db_name)
        conn.autocommit = False
        cursor = conn.cursor(dictionary=True, buffered=True)
        if not conn.in_transaction:
            conn.start_transaction()
        id_partener = _save(cursor, body)
        conn.commit()
        logger.info("[forexe.parteneri_edit] %s: partner %s saved", db_name, id_partener)
        return _json_utf8({"id_partener": id_partener}, 200)
    except _Refused as e:
        _rollback(conn)
        return _json_utf8({"error": str(e)}, 400)
    except mysql.connector.IntegrityError as e:
        _rollback(conn)
        if e.errno == 1062:
            text = str(e)
            if "idx_partener_clsf" in text:
                message = "Aceeași clasificație apare de două ori în codurile partenerului."
            else:
                message = "Există deja un partener cu acest cod în unitate."
            return _json_utf8({"error": message}, 409)
        logger.error("[forexe.parteneri_edit] save %s: %s", db_name, e, exc_info=True)
        return _json_utf8({"error": f"Eroare la salvarea partenerului: {e}"}, 500)
    except Exception as e:
        _rollback(conn)
        logger.error("[forexe.parteneri_edit] save %s: %s", db_name, e, exc_info=True)
        return _json_utf8({"error": f"Eroare la salvarea partenerului: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()


@forexe_bp.route("/api/forexe/nomenclatoare/parteneri/<int:id_partener>", methods=["DELETE"])
@require_session
def delete_partener(id_partener):
    """Deletes a partner that no document uses (its codes go with it, ON DELETE CASCADE)."""
    db_name = g.session.db_name
    conn = None
    try:
        conn = get_kbot_connection(db_name)
        conn.autocommit = False
        cursor = conn.cursor(dictionary=True, buffered=True)
        if not conn.in_transaction:
            conn.start_transaction()
        cursor.execute(_SQL_ONE_ACTIVE, (id_partener,))
        row = cursor.fetchone()
        if row is None:
            _rollback(conn)
            return _json_utf8({"error": "Partenerul nu mai există în baza de date."}, 404)
        if row["Activ"]:
            _rollback(conn)
            return _json_utf8({"error": "Partenerul apare pe documente (DDF / ORD) și nu se poate "
                                        "șterge; bifați «Ascuns» ca să nu mai apară în liste."}, 409)
        cursor.execute(_SQL_DELETE, (id_partener,))
        conn.commit()
        logger.info("[forexe.parteneri_edit] %s: partner %s deleted", db_name, id_partener)
        return _json_utf8({"deleted": 1}, 200)
    except Exception as e:
        _rollback(conn)
        logger.error("[forexe.parteneri_edit] delete %s/%s: %s", db_name, id_partener, e, exc_info=True)
        return _json_utf8({"error": f"Eroare la ștergerea partenerului: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()
