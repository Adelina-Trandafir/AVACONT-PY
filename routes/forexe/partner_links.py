# routes/forexe/partner_links.py
"""
Supplier <-> angajament links keyed by the supplier's normalized CodFiscal (slice 0121).

Two tables (sql/P121_01_parteneri_angajamente.sql), in the unit database of the session:

    Parteneri_Angajamente              IdLegAng, CodFiscal (normalized), CodAng        unique (CodFiscal, CodAng)
    Parteneri_Angajamente_Indicatori   IdLegInd, IdLegAng -> link (cascade), CodAI, CodInd, ContBancar
                                                                                      unique (IdLegAng, CodAI)

WHY BY CodFiscal. `Parteneri` is per unit (unique IdUnitate + CodPartener) and one DC database holds several units; a link keyed
by the CUI belongs to the supplier, not to one unit's copy of it. `Parteneri` and `Parteneri_Coduri` are not touched here.

CodFiscal is written NORMALIZED and read normalized: `normalize_cui` below is the rule of the DDF (routes.efactura.primite_ubl
.normalize_cui, EfCui.Normalize in KBot.Migrator). It is a copy on purpose -- importing the efactura package from here would pull
all its blueprint modules. Change it in all places or in none.

No foreign key to FX_Angajamente / FX_Indicatori: a CodAI is read LEFT JOIN, and one missing from FX_Indicatori simply has no
classification. CodAI = CodAng + "-" + CodInd (the key of FX_Indicatori).

HOOKS (slice 0121-04). `ensure_link` / `ensure_for_partners` fill the link of a supplier with an angajament, and the
angajament's indicators (FX_Indicatori.CodAngajament), WHEN THEY ARE MISSING: they only insert, never change a row that exists
(ContBancar, CodInd of a saved indicator stay as the operator left them) and never delete. They run inside the caller's
transaction, only when something is SAVED (never on a mere view):
  * the DDF's partner association: ddf_parteneri.sincronizeaza_parteneri (the editor's save) and adauga_parteneri (the Sumar
    button) -- every partner the save names gets the link with the DDF's angajament;
  * the pick of an angajament for a supplier (page «Receptii», slice 0121-05):

    POST   /api/forexe/parteneri/angajament
        { "cod_fiscal": "RO 123", "cod_angajament": "..." }
        -> 200 { "id_leg": n, "legatura_noua": bool, "indicatori_adaugati": k }
           400 no usable fiscal code / the tables are missing, 404 unknown angajament

    POST   /api/forexe/parteneri/angajament/scoate
        { "cod_fiscal": "RO 123", "cod_angajament": "..." }
        -> 200 { "sterse": 0|1 }       the link and, with it (ON DELETE CASCADE), its indicators. Page «Receptii», slice 0121-05.

A database that does not have the two tables yet (sql/P121_01_parteneri_angajamente.sql not run and synced) keeps working: the
hooks of the DDF save skip silently (logged once), the route answers with the file to run.
"""
import logging

from flask import current_app, g, request

from routes.auth.guard import require_session
from utils.database import get_kbot_connection

from . import forexe_bp

logger = logging.getLogger(__name__)

FISIER_SQL = "sql/P121_01_parteneri_angajamente.sql"


class LinkRefused(ValueError):
    """A request the operator can fix; the message is Romanian and goes to the `error` field."""


def normalize_cui(value) -> str:
    """Letters and digits only, upper case, a leading «RO» before digits dropped."""
    cleaned = "".join(ch for ch in str(value or "") if ch.isalnum()).upper()
    if len(cleaned) > 2 and cleaned.startswith("RO") and cleaned[2].isdigit():
        cleaned = cleaned[2:]
    return cleaned


def cod_ai(cod_ang: str, cod_ind: str) -> str:
    return f"{cod_ang}-{cod_ind}"


# Databases where both tables were found. Only a POSITIVE answer is remembered: the operator runs the DDL while the server is up.
_TABELE_PREZENTE = set()
_SQL_ARE_TABELE = (
    "SELECT COUNT(*) AS n FROM information_schema.TABLES WHERE TABLE_SCHEMA = %s "
    "AND TABLE_NAME IN ('Parteneri_Angajamente', 'Parteneri_Angajamente_Indicatori')"
)
_SQL_ANGAJAMENT_EXISTA = "SELECT 1 FROM FX_Angajamente WHERE CodAngajament = %s LIMIT 1"
_SQL_INDICATORI_ANGAJAMENT = "SELECT CodAI, CodIndicator FROM FX_Indicatori WHERE CodAngajament = %s ORDER BY CodAI"
_SQL_INDICATORI_LEGATURII = "SELECT CodAI FROM Parteneri_Angajamente_Indicatori WHERE IdLegAng = %s"
_SQL_IND_INSERT = "INSERT INTO Parteneri_Angajamente_Indicatori (IdLegAng, CodAI, CodInd) VALUES (%s, %s, %s)"
_SQL_LINK_INSERT = "INSERT INTO Parteneri_Angajamente (CodFiscal, CodAng) VALUES (%s, %s)"


def tables_present(cursor, db_name: str) -> bool:
    """Does this database have both link tables? A failed probe counts as «absent»."""
    if db_name in _TABELE_PREZENTE:
        return True
    try:
        cursor.execute(_SQL_ARE_TABELE, (db_name,))
        present = int((cursor.fetchone() or {}).get("n") or 0) == 2
    except Exception:
        logger.warning("[forexe.partner_links] %s: the probe of the link tables failed; assumed absent",
                       db_name, exc_info=True)
        return False
    if present:
        _TABELE_PREZENTE.add(db_name)
    else:
        logger.warning("[forexe.partner_links] %s: the link tables are missing (run %s); links not written",
                       db_name, FISIER_SQL)
    return present


def ensure_link(cursor, cod_fiscal, cod_angajament) -> dict:
    """
    Makes sure the supplier with this fiscal code is linked to the angajament, and that the link holds every indicator of the
    angajament. Insert only: an existing link or indicator is left exactly as it is. The caller checked `tables_present`.

    Returns {"id_leg", "legatura_noua", "indicatori_adaugati"}, or None when the fiscal code normalizes to nothing or the
    angajament code is empty (nothing to link).
    """
    cf = normalize_cui(cod_fiscal)
    cod_ang = _text(cod_angajament)
    if not cf or not cod_ang:
        return None

    cursor.execute(_SQL_LINK_ID, (cf, cod_ang))
    row = cursor.fetchone()
    new_link = row is None
    if new_link:
        cursor.execute(_SQL_LINK_INSERT, (cf, cod_ang))
        id_leg = int(cursor.lastrowid)
    else:
        id_leg = int(row["IdLegAng"])

    cursor.execute(_SQL_INDICATORI_LEGATURII, (id_leg,))
    have = {_text(r["CodAI"]).lower() for r in cursor.fetchall()}
    cursor.execute(_SQL_INDICATORI_ANGAJAMENT, (cod_ang,))
    added = 0
    for r in cursor.fetchall():
        key = _text(r["CodAI"])
        if not key or key.lower() in have:
            continue
        have.add(key.lower())
        cursor.execute(_SQL_IND_INSERT, (id_leg, key, _text(r["CodIndicator"]) or None))
        added += 1
    return {"id_leg": id_leg, "legatura_noua": new_link, "indicatori_adaugati": added}


def ensure_for_partners(cursor, db_name: str, cod_angajament, cods_fiscale) -> int:
    """
    The DDF hook: links every fiscal code of `cods_fiscale` to the angajament (see `ensure_link`). Skips silently when the
    database has no link tables, so a DDF save never depends on them. Returns how many links were NEW.
    """
    if not _text(cod_angajament) or not tables_present(cursor, db_name):
        return 0
    created = 0
    seen = set()
    for cod_fiscal in cods_fiscale:
        cf = normalize_cui(cod_fiscal)
        if not cf or cf in seen:
            continue
        seen.add(cf)
        result = ensure_link(cursor, cf, cod_angajament)
        if result is not None and result["legatura_noua"]:
            created += 1
    return created


# One row per indicator; a link with no indicator is one row with the I.* columns empty.
_SQL_READ = (
    "SELECT L.IdLegAng, L.CodFiscal, L.CodAng, I.IdLegInd, I.CodAI, I.CodInd, I.ContBancar, "
    "       F.IdClsf, C.Clsf, C.Denumire "
    "  FROM Parteneri_Angajamente L "
    "  LEFT JOIN Parteneri_Angajamente_Indicatori I ON I.IdLegAng = L.IdLegAng "
    "  LEFT JOIN FX_Indicatori F ON F.CodAI = I.CodAI "
    "  LEFT JOIN Clasificatii C ON C.IDClsf = F.IdClsf "
    " ORDER BY L.CodFiscal, L.CodAng, I.CodAI"
)
_SQL_IND_BY_ID = (
    "SELECT I.IdLegInd, I.IdLegAng, I.CodAI, L.CodAng "
    "  FROM Parteneri_Angajamente_Indicatori I JOIN Parteneri_Angajamente L ON L.IdLegAng = I.IdLegAng "
    " WHERE I.IdLegInd = %s AND L.CodFiscal = %s"
)
_SQL_IND_DELETE = "DELETE FROM Parteneri_Angajamente_Indicatori WHERE IdLegInd = %s"
_SQL_IND_UPDATE = "UPDATE Parteneri_Angajamente_Indicatori SET CodInd = %s, ContBancar = %s WHERE IdLegInd = %s"
_SQL_IND_UPSERT = (
    "INSERT INTO Parteneri_Angajamente_Indicatori (IdLegAng, CodAI, CodInd, ContBancar) VALUES (%s, %s, %s, %s) "
    "ON DUPLICATE KEY UPDATE CodInd = VALUES(CodInd), ContBancar = VALUES(ContBancar)"
)
_SQL_LINK_UPSERT = (
    "INSERT INTO Parteneri_Angajamente (CodFiscal, CodAng) VALUES (%s, %s) "
    "ON DUPLICATE KEY UPDATE IdLegAng = IdLegAng"
)
_SQL_LINK_ID = "SELECT IdLegAng FROM Parteneri_Angajamente WHERE CodFiscal = %s AND CodAng = %s"
_SQL_LINK_OF = "SELECT IdLegAng, CodAng FROM Parteneri_Angajamente WHERE IdLegAng = %s AND CodFiscal = %s"
# Only a link with no indicator left may go (an indicator added by someone else in the meantime keeps it).
_SQL_LINK_DELETE_EMPTY = (
    "DELETE FROM Parteneri_Angajamente WHERE IdLegAng = %s AND CodFiscal = %s "
    "AND NOT EXISTS (SELECT 1 FROM Parteneri_Angajamente_Indicatori I WHERE I.IdLegAng = Parteneri_Angajamente.IdLegAng)"
)


def _text(value) -> str:
    return "" if value is None else str(value).strip()


def read_links(cursor) -> dict:
    """Every link of the database as {normalized CodFiscal: [row, ...]} (the window's «Coduri angajament»)."""
    cursor.execute(_SQL_READ)
    result = {}
    for r in cursor.fetchall():
        has_indicator = r["IdLegInd"] is not None
        result.setdefault(_text(r["CodFiscal"]), []).append({
            "id": int(r["IdLegInd"]) if has_indicator else None,
            "id_leg": int(r["IdLegAng"]),
            "id_clsf": int(r["IdClsf"]) if r["IdClsf"] is not None else None,
            "clsf": _text(r["Clsf"]),
            "denumire_clsf": _text(r["Denumire"]),
            "cont_bancar": _text(r["ContBancar"]),
            "cod_ang": _text(r["CodAng"]),
            "cod_ind": _text(r["CodInd"]),
        })
    return result


def _field(row: dict, key: str, label: str, index: int) -> str:
    value = _text(row.get(key))
    if len(value) > 255:
        raise LinkRefused(f"Codurile de angajament, rândul {index}: «{label}» are peste 255 de caractere.")
    return value


def _as_int(value):
    try:
        return int(value) if value else None
    except (TypeError, ValueError):
        return None


def save_codes(cursor, cf: str, rows, deleted_inds, deleted_legs) -> None:
    """
    Writes the «Coduri angajament» of the supplier with the normalized CodFiscal `cf`, inside the caller's transaction.

    rows          every row the grid shows: {id (IdLegInd) | None, id_leg, cod_ang, cod_ind, cont_bancar}
    deleted_inds  IdLegInd of saved indicators removed in the window
    deleted_legs  IdLegAng of saved links with no indicator removed in the window

    Everything touched is checked against `cf`: an id of another supplier is ignored (deletes) or refused (rows).
    A link left with no indicator by the window's changes is deleted, unless a row of the grid still names it.
    """
    if not isinstance(rows, list):
        raise LinkRefused("Lista codurilor de angajament nu are forma așteptată.")
    for name, value in (("coduri_sterse", deleted_inds), ("legaturi_sterse", deleted_legs)):
        if not isinstance(value, list):
            raise LinkRefused(f"Lista {name} nu are forma așteptată.")
    if (rows or deleted_inds or deleted_legs) and not cf:
        raise LinkRefused("Codurile de angajament se leagă de codul fiscal al partenerului; partenerul nu are unul.")

    touched = set()  # IdLegAng of links that may be left empty

    for raw_id in deleted_inds:
        cursor.execute(_SQL_IND_BY_ID, (int(raw_id), cf))
        old = cursor.fetchone()
        if old is not None:
            cursor.execute(_SQL_IND_DELETE, (int(old["IdLegInd"]),))
            touched.add(int(old["IdLegAng"]))
    for raw_id in deleted_legs:
        cursor.execute(_SQL_LINK_DELETE_EMPTY, (int(raw_id), cf))

    # Pass 1: validate every row and remove the old identity of the rows that change it, so two rows may swap codes.
    prepared = []
    seen = set()
    final_angs = set()
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise LinkRefused(f"Codurile de angajament, rândul {index} nu are forma așteptată.")
        cod_ang = _field(row, "cod_ang", "Cod ang.", index)
        cod_ind = _field(row, "cod_ind", "Cod ind.", index)
        cont = _field(row, "cont_bancar", "Cont bancar asociat", index)
        if not cod_ang:
            raise LinkRefused(f"Codurile de angajament, rândul {index}: completați «Cod ang.».")
        key = (cod_ang.lower(), cod_ind.lower())
        if key in seen:
            raise LinkRefused(f"Codurile de angajament, rândul {index}: {cod_ai(cod_ang, cod_ind) if cod_ind else cod_ang} "
                              f"apare de două ori.")
        seen.add(key)
        final_angs.add(cod_ang.lower())

        keep_in_place = None
        old_ind = _as_int(row.get("id"))
        old_leg = _as_int(row.get("id_leg"))
        if old_ind:
            cursor.execute(_SQL_IND_BY_ID, (old_ind, cf))
            old = cursor.fetchone()
            if old is None:
                raise LinkRefused(f"Codurile de angajament, rândul {index}: rândul nu mai există în baza de date "
                                  f"(a fost schimbat în altă parte). Reîncărcați partenerul.")
            same = (_text(old["CodAng"]).lower() == cod_ang.lower() and cod_ind
                    and _text(old["CodAI"]).lower() == cod_ai(cod_ang, cod_ind).lower())
            if same:
                keep_in_place = old_ind
            else:
                cursor.execute(_SQL_IND_DELETE, (old_ind,))
                touched.add(int(old["IdLegAng"]))
        elif old_leg:
            touched.add(old_leg)
        prepared.append((cod_ang, cod_ind, cont, keep_in_place))

    # Pass 2: the links and indicators the grid shows.
    for cod_ang, cod_ind, cont, keep_in_place in prepared:
        cursor.execute(_SQL_LINK_UPSERT, (cf, cod_ang))
        cursor.execute(_SQL_LINK_ID, (cf, cod_ang))
        leg = int(cursor.fetchone()["IdLegAng"])
        if keep_in_place:
            cursor.execute(_SQL_IND_UPDATE, (cod_ind, cont or None, keep_in_place))
        elif cod_ind:
            cursor.execute(_SQL_IND_UPSERT, (leg, cod_ai(cod_ang, cod_ind), cod_ind, cont or None))

    for leg in touched:
        cursor.execute(_SQL_LINK_OF, (leg, cf))
        link = cursor.fetchone()
        if link is not None and _text(link["CodAng"]).lower() not in final_angs:
            cursor.execute(_SQL_LINK_DELETE_EMPTY, (leg, cf))


def _json_utf8(payload, status):
    """A JSON response with LITERAL diacritics (ensure_ascii=False)."""
    import json
    body = json.dumps(payload, ensure_ascii=False, default=str)
    return current_app.response_class(body, status=status, mimetype="application/json")


def _rollback(conn):
    if conn is None:
        return
    try:
        conn.rollback()
    except Exception:
        logger.warning("[forexe.partner_links] rollback failed", exc_info=True)


@forexe_bp.route("/api/forexe/parteneri/angajament", methods=["POST"])
@require_session
def post_partener_angajament():
    """Links a supplier (by fiscal code) to an angajament and fills the angajament's indicators when missing."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _json_utf8({"error": "Corp JSON lipsă sau nevalid."}, 400)
    cod_fiscal = _text(body.get("cod_fiscal"))
    cod_angajament = _text(body.get("cod_angajament"))
    if not normalize_cui(cod_fiscal):
        return _json_utf8({"error": "Codul fiscal al furnizorului lipsește."}, 400)
    if not cod_angajament:
        return _json_utf8({"error": "Codul angajamentului lipsește."}, 400)

    db_name = g.session.db_name
    conn = None
    try:
        conn = get_kbot_connection(db_name)
        conn.autocommit = False
        cursor = conn.cursor(dictionary=True, buffered=True)
        if not conn.in_transaction:
            conn.start_transaction()
        if not tables_present(cursor, db_name):
            _rollback(conn)
            return _json_utf8({"error": "Tabelele legăturilor furnizor–angajament nu există pe această bază. "
                                        f"Rulați {FISIER_SQL}."}, 400)
        cursor.execute(_SQL_ANGAJAMENT_EXISTA, (cod_angajament,))
        if cursor.fetchone() is None:
            _rollback(conn)
            return _json_utf8({"error": f"Angajamentul «{cod_angajament}» nu există în baza de date."}, 404)
        result = ensure_link(cursor, cod_fiscal, cod_angajament)
        conn.commit()
        logger.info("[forexe.partner_links] %s: cod=%s link=%s new=%s indicators=%s", db_name, cod_angajament,
                    result["id_leg"], result["legatura_noua"], result["indicatori_adaugati"])
        return _json_utf8(result, 200)
    except Exception as e:
        _rollback(conn)
        logger.error("[forexe.partner_links] link %s/%s: %s", db_name, cod_angajament, e, exc_info=True)
        return _json_utf8({"error": f"Eroare la legarea furnizorului de angajament: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()


@forexe_bp.route("/api/forexe/parteneri/angajament/scoate", methods=["POST"])
@require_session
def post_scoate_partener_angajament():
    """Removes the link of a supplier (by fiscal code) with an angajament, with its indicators."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _json_utf8({"error": "Corp JSON lipsă sau nevalid."}, 400)
    cf = normalize_cui(body.get("cod_fiscal"))
    cod_angajament = _text(body.get("cod_angajament"))
    if not cf:
        return _json_utf8({"error": "Codul fiscal al furnizorului lipsește."}, 400)
    if not cod_angajament:
        return _json_utf8({"error": "Codul angajamentului lipsește."}, 400)

    db_name = g.session.db_name
    conn = None
    try:
        conn = get_kbot_connection(db_name)
        conn.autocommit = False
        cursor = conn.cursor(dictionary=True, buffered=True)
        if not conn.in_transaction:
            conn.start_transaction()
        if not tables_present(cursor, db_name):
            _rollback(conn)
            return _json_utf8({"error": "Tabelele legăturilor furnizor–angajament nu există pe această bază. "
                                        f"Rulați {FISIER_SQL}."}, 400)
        cursor.execute("DELETE FROM Parteneri_Angajamente WHERE CodFiscal = %s AND CodAng = %s", (cf, cod_angajament))
        removed = cursor.rowcount
        conn.commit()
        logger.info("[forexe.partner_links] %s: cod=%s link removed=%s", db_name, cod_angajament, removed)
        return _json_utf8({"sterse": removed}, 200)
    except Exception as e:
        _rollback(conn)
        logger.error("[forexe.partner_links] unlink %s/%s: %s", db_name, cod_angajament, e, exc_info=True)
        return _json_utf8({"error": f"Eroare la scoaterea legăturii: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()
