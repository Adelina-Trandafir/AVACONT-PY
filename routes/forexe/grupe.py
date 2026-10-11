# routes/forexe/grupe.py
"""
Groups of angajamente (slice 0008-02, operator 09.10.2026): the «Grupe angajamente» window and
the group filter of the main tree.

    GET  /api/forexe/grupe
        -> 200 { "grupe": [ { "idgr", "denumire", "culoare", "coduri": [CodAngajament] } ],
                 "angajamente": [ { "cod", "descriere", "alias",
                                    "indicatori": [ { "clsf", "denumire", "ss" } ] } ] }
        The angajamente are the visible ones (not hidden, not Anulat / Suspendat), every year:
        the window ticks them into groups and shows the ones that belong to no group.

    POST /api/forexe/grupe
        { "idgr": null | n, "denumire", "culoare": "#RRGGBB", "coduri": [CodAngajament] }
        -> 200 { "idgr": n }.  A group is saved WHOLE: its name, its colour and its members
        (the members not in "coduri" are removed). 400 for a missing name / bad colour / no
        angajament / an unknown code; 404 for an unknown idgr; 409 for a name another group has.

    POST /api/forexe/grupe/<idgr>/angajamente   { "cod": CodAngajament }
        -> 200 { "idgr": n }.  Adds one angajament to a group (the drag and drop of the window);
        already there = nothing to do.

    POST /api/forexe/grupe/alias   { "cod": CodAngajament, "alias": "..." }
        -> 200 { "cod", "alias" }.  Empty alias = removed (NULL).

TABLE (sql/0008_02_alias_grupe.sql): FX_Angajamente_Grupe(IDGR, DenumireGrupa, CuloareGrupa,
CodAngajament), primary key (IDGR, CodAngajament); the group's name and colour repeat on its
rows. IDGR = MAX + 1 taken inside the save's transaction; a concurrent save that takes the same
number hits the primary key and gets a 409 to retry.

JOINS (house rule: count what a join returns). FX_Indicatori -> Clasificatii on
C.IDClsf = I.IdClsf (the primary key: never more than one row). The indicators are read in a
query of their own and put under their angajament here, so the angajamente list never
multiplies.
"""
import json
import logging
import re

import mysql.connector
from flask import g, current_app, request
from mysql.connector import errorcode

from routes.auth.guard import require_session
from utils.database import get_kbot_connection

from . import forexe_bp

logger = logging.getLogger(__name__)

_RE_CULOARE = re.compile(r"^#[0-9A-Fa-f]{6}$")
_DENUMIRE_MAX = 100
_ALIAS_MAX = 255

_SQL_GRUPE = (
    "SELECT IDGR, DenumireGrupa, CuloareGrupa, CodAngajament "
    "  FROM FX_Angajamente_Grupe ORDER BY DenumireGrupa, IDGR, CodAngajament"
)
_SQL_ANGAJAMENTE = (
    "SELECT A.CodAngajament, A.Descriere, A.Alias "
    "  FROM FX_Angajamente A "
    " WHERE COALESCE(A.Ascuns, 0) = 0 "
    "   AND COALESCE(A.Stare, '') NOT LIKE '%Anulat%' "
    "   AND COALESCE(A.Stare, '') NOT LIKE '%Suspendat%' "
    " ORDER BY A.Descriere, A.CodAngajament"
)
_SQL_INDICATORI = (
    "SELECT I.CodAngajament, C.Clsf, C.Denumire, COALESCE(NULLIF(I.SS, ''), C.SS) AS SS "
    "  FROM FX_Indicatori I "
    "  LEFT JOIN Clasificatii C ON C.IDClsf = I.IdClsf "
    " ORDER BY I.CodAngajament, I.CodIndicator"
)


def _json_utf8(payload, status):
    """A JSON response with LITERAL diacritics (ensure_ascii=False)."""
    body = json.dumps(payload, ensure_ascii=False, default=str)
    return current_app.response_class(body, status=status, mimetype="application/json")


def _text(d: dict, key: str) -> str:
    return str(d.get(key) or "").strip()


def _rollback(conn):
    if conn is not None:
        try:
            conn.rollback()
        except Exception:                                   # pragma: no cover - best effort
            logger.warning("[forexe.grupe] rollback a esuat", exc_info=True)


@forexe_bp.route("/api/forexe/grupe", methods=["GET"])
@require_session
def get_grupe():
    """The groups with their members, and the visible angajamente with their indicators."""
    db_name = g.session.db_name
    conn = None
    try:
        conn = get_kbot_connection(db_name)
        cursor = conn.cursor()

        grupe = {}
        cursor.execute(_SQL_GRUPE)
        for idgr, denumire, culoare, cod in cursor.fetchall():
            grupa = grupe.setdefault(int(idgr), {
                "idgr": int(idgr), "denumire": denumire or "",
                "culoare": culoare or "#000000", "coduri": []})
            grupa["coduri"].append(cod)

        indicatori = {}
        cursor.execute(_SQL_INDICATORI)
        for cod, clsf, denumire, ss in cursor.fetchall():
            indicatori.setdefault(cod, []).append(
                {"clsf": clsf or "", "denumire": denumire or "", "ss": ss or ""})

        angajamente = []
        cursor.execute(_SQL_ANGAJAMENTE)
        for cod, descriere, alias in cursor.fetchall():
            angajamente.append({"cod": cod, "descriere": descriere or "", "alias": alias or "",
                                "indicatori": indicatori.get(cod, [])})

        logger.info("[forexe.grupe] %s: %s grupe, %s angajamente", db_name, len(grupe), len(angajamente))
        return _json_utf8({"grupe": sorted(grupe.values(), key=lambda x: (x["denumire"].lower(), x["idgr"])),
                           "angajamente": angajamente}, 200)
    except Exception as e:
        logger.error("[forexe.grupe] citire %s: %s", db_name, e, exc_info=True)
        return _json_utf8({"error": f"Eroare la citirea grupelor de angajamente: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()


def _valideaza_grupa(corp: dict):
    """(values, error): the cleaned body of a group save, or the Romanian reason it is refused."""
    denumire = _text(corp, "denumire")
    if not denumire:
        return None, "Introduceți denumirea grupei."
    if len(denumire) > _DENUMIRE_MAX:
        return None, f"Denumirea grupei are cel mult {_DENUMIRE_MAX} de caractere."
    culoare = _text(corp, "culoare") or "#000000"
    if not _RE_CULOARE.match(culoare):
        return None, "Culoarea grupei nu este validă."
    brut = corp.get("coduri")
    if not isinstance(brut, list):
        return None, "Lista angajamentelor lipsește."
    coduri = []
    for cod in brut:
        cod = str(cod or "").strip()
        if cod and cod not in coduri:
            coduri.append(cod)
    if not coduri:
        return None, "Bifați cel puțin un angajament."
    idgr = corp.get("idgr")
    if idgr is not None:
        try:
            idgr = int(idgr)
        except (TypeError, ValueError):
            return None, "Numărul grupei nu este valid."
    return {"idgr": idgr, "denumire": denumire, "culoare": culoare.upper(), "coduri": coduri}, None


@forexe_bp.route("/api/forexe/grupe", methods=["POST"])
@require_session
def post_grupa():
    """Saves one group whole: name, colour and members."""
    corp = request.get_json(silent=True)
    if not isinstance(corp, dict):
        return _json_utf8({"error": "Corp JSON lipsă sau nevalid."}, 400)
    v, eroare = _valideaza_grupa(corp)
    if eroare:
        return _json_utf8({"error": eroare}, 400)

    db_name = g.session.db_name
    conn = None
    try:
        conn = get_kbot_connection(db_name)
        conn.autocommit = False
        cursor = conn.cursor()
        if not conn.in_transaction:
            conn.start_transaction()

        # Every code must be an angajament that exists (the table's FK would say it as a bare error).
        marcaje = ", ".join(["%s"] * len(v["coduri"]))
        cursor.execute(f"SELECT CodAngajament FROM FX_Angajamente WHERE CodAngajament IN ({marcaje})",
                       tuple(v["coduri"]))
        gasite = {r[0] for r in cursor.fetchall()}
        lipsa = [c for c in v["coduri"] if c not in gasite]
        if lipsa:
            conn.rollback()
            return _json_utf8({"error": f"Angajamentul {lipsa[0]} nu există."}, 400)

        # A name belongs to one group (compared the way MariaDB's collation does: case-insensitive).
        cursor.execute("SELECT 1 FROM FX_Angajamente_Grupe WHERE DenumireGrupa = %s AND IDGR <> %s LIMIT 1",
                       (v["denumire"], v["idgr"] if v["idgr"] is not None else -1))
        if cursor.fetchone():
            conn.rollback()
            return _json_utf8({"error": "Există deja o grupă cu această denumire."}, 409)

        idgr = v["idgr"]
        if idgr is None:
            cursor.execute("SELECT COALESCE(MAX(IDGR), 0) + 1 FROM FX_Angajamente_Grupe FOR UPDATE")
            idgr = int(cursor.fetchone()[0])
        else:
            cursor.execute("SELECT 1 FROM FX_Angajamente_Grupe WHERE IDGR = %s LIMIT 1", (idgr,))
            if not cursor.fetchone():
                conn.rollback()
                return _json_utf8({"error": "Grupa nu mai există."}, 404)
            cursor.execute(f"DELETE FROM FX_Angajamente_Grupe WHERE IDGR = %s "
                           f"AND CodAngajament NOT IN ({marcaje})", (idgr, *v["coduri"]))
            cursor.execute("UPDATE FX_Angajamente_Grupe SET DenumireGrupa = %s, CuloareGrupa = %s "
                           "WHERE IDGR = %s", (v["denumire"], v["culoare"], idgr))

        cursor.executemany(
            "INSERT IGNORE INTO FX_Angajamente_Grupe (IDGR, DenumireGrupa, CuloareGrupa, CodAngajament) "
            "VALUES (%s, %s, %s, %s)",
            [(idgr, v["denumire"], v["culoare"], c) for c in v["coduri"]])
        conn.commit()
        logger.info("[forexe.grupe] %s: grupa %s «%s», %s angajamente", db_name, idgr, v["denumire"], len(v["coduri"]))
        return _json_utf8({"idgr": idgr}, 200)
    except mysql.connector.Error as e:
        _rollback(conn)
        if e.errno == errorcode.ER_DUP_ENTRY:
            return _json_utf8({"error": "Între timp s-a salvat o altă grupă cu același număr. Reîncercați."}, 409)
        logger.error("[forexe.grupe] salvare %s: %s", db_name, e, exc_info=True)
        return _json_utf8({"error": f"Eroare la salvarea grupei: {e}"}, 500)
    except Exception as e:
        _rollback(conn)
        logger.error("[forexe.grupe] salvare %s: %s", db_name, e, exc_info=True)
        return _json_utf8({"error": f"Eroare la salvarea grupei: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()


@forexe_bp.route("/api/forexe/grupe/<int:idgr>/angajamente", methods=["POST"])
@require_session
def post_grupa_angajament(idgr: int):
    """Adds one angajament to an existing group (the drag and drop of the window)."""
    corp = request.get_json(silent=True)
    cod = _text(corp, "cod") if isinstance(corp, dict) else ""
    if not cod:
        return _json_utf8({"error": "Codul angajamentului lipsește."}, 400)

    db_name = g.session.db_name
    conn = None
    try:
        conn = get_kbot_connection(db_name)
        conn.autocommit = False
        cursor = conn.cursor()
        if not conn.in_transaction:
            conn.start_transaction()
        cursor.execute("SELECT DenumireGrupa, CuloareGrupa FROM FX_Angajamente_Grupe WHERE IDGR = %s LIMIT 1",
                       (idgr,))
        grupa = cursor.fetchone()
        if not grupa:
            conn.rollback()
            return _json_utf8({"error": "Grupa nu mai există."}, 404)
        cursor.execute("SELECT 1 FROM FX_Angajamente WHERE CodAngajament = %s LIMIT 1", (cod,))
        if not cursor.fetchone():
            conn.rollback()
            return _json_utf8({"error": f"Angajamentul {cod} nu există."}, 400)
        cursor.execute(
            "INSERT IGNORE INTO FX_Angajamente_Grupe (IDGR, DenumireGrupa, CuloareGrupa, CodAngajament) "
            "VALUES (%s, %s, %s, %s)", (idgr, grupa[0], grupa[1], cod))
        conn.commit()
        logger.info("[forexe.grupe] %s: %s adaugat in grupa %s", db_name, cod, idgr)
        return _json_utf8({"idgr": idgr}, 200)
    except Exception as e:
        _rollback(conn)
        logger.error("[forexe.grupe] adaugare %s: %s", db_name, e, exc_info=True)
        return _json_utf8({"error": f"Eroare la adăugarea angajamentului în grupă: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()


@forexe_bp.route("/api/forexe/grupe/alias", methods=["POST"])
@require_session
def post_alias():
    """Sets (or clears) the alias of one angajament."""
    corp = request.get_json(silent=True)
    if not isinstance(corp, dict):
        return _json_utf8({"error": "Corp JSON lipsă sau nevalid."}, 400)
    cod = _text(corp, "cod")
    alias = _text(corp, "alias")
    if not cod:
        return _json_utf8({"error": "Codul angajamentului lipsește."}, 400)
    if len(alias) > _ALIAS_MAX:
        return _json_utf8({"error": f"Aliasul are cel mult {_ALIAS_MAX} de caractere."}, 400)

    db_name = g.session.db_name
    conn = None
    try:
        conn = get_kbot_connection(db_name)
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM FX_Angajamente WHERE CodAngajament = %s LIMIT 1", (cod,))
        if not cursor.fetchone():
            return _json_utf8({"error": f"Angajamentul {cod} nu există."}, 404)
        cursor.execute("UPDATE FX_Angajamente SET Alias = %s WHERE CodAngajament = %s",
                       (alias or None, cod))
        conn.commit()
        logger.info("[forexe.grupe] %s: alias %s", db_name, cod)
        return _json_utf8({"cod": cod, "alias": alias}, 200)
    except Exception as e:
        logger.error("[forexe.grupe] alias %s: %s", db_name, e, exc_info=True)
        return _json_utf8({"error": f"Eroare la salvarea aliasului: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()
