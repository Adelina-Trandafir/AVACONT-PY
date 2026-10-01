# routes/forexe/ddf_parteneri.py
"""
The partners associated with a DDF (slice 0084-02 for the Sumar button, 0094-02 for the
DDF editor page «Parteneri»).

`FX_DDF` has one pair of columns for the partner (`CodFiscal`, `NumePartener`), so a document
can carry exactly one there. The whole list lives in `FX_DDF_Parteneri` (sql/0084_02_...): one
row per (document, fiscal code). The `FX_DDF` pair stays the HEADER partner -- the one the
editor's combo picks, the one written on every section-A / section-B line, the one in the
signed PDF -- and that partner is also a row of the table, so the table alone answers «which
partners does this DDF have».

Routes (both `@require_session`; the database comes from the session):

    GET   /api/forexe/ddf/parteneri-asociati?cod=<CodAngajament>
        -> 200 { "iddf": n, "parteneri": [ { "cod_fiscal", "nume_partener", "din_antet" } ] }
           404 when the angajament has no DDF (the Sumar button is not shown there either)

    POST  /api/forexe/ddf/parteneri-asociati
        { "cod_angajament": "...", "parteneri": [ { "cod_fiscal", "nume_partener" } ] }
        -> 200 { "iddf": n, "adaugati": k, "parteneri": [ ... as in GET ... ] }
        ADD ONLY: a partner already associated is skipped (that is the whole rule of the Sumar
        button), nothing is ever removed here -- removing is the editor's job, on its page.

Two functions are shared with `ddf_edit.py`, which calls them inside its save transaction:
`citeste_parteneri` (the draft / generation answers carry the list) and
`sincronizeaza_parteneri` (the save writes the list it received).

THE KEY IS THE FISCAL CODE, compared as digits only (`anaf.normalize_cf`: «RO 123» = «123»),
the same rule the «Parteneri» window uses for «one partner per fiscal code».

A DATABASE WITHOUT THE TABLE keeps working: a save that carries only the header partner skips
it; a save (or the Sumar button) that needs a second partner answers with the file to run.
"""
import json
import logging
from datetime import date, datetime

from flask import current_app, g, request

from routes.auth.guard import require_session
from routes.inregistrare import anaf
from utils.database import get_kbot_connection

from . import forexe_bp

logger = logging.getLogger(__name__)

FISIER_SQL = "sql/0084_02_fx_ddf_parteneri.sql"
LUNGIME_MAXIMA = 255

# Databases where the table was found. Only a POSITIVE answer is remembered: the operator runs
# the DDL while the server is up, and a remembered «absent» would need a restart.
_TABELA_PREZENTA = set()

_SQL_ARE_TABELA = (
    "SELECT COUNT(*) AS n FROM information_schema.TABLES "
    " WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'FX_DDF_Parteneri'"
)
_SQL_LISTA = (
    "SELECT IdDdfPartener, CodFiscal, NumePartener FROM FX_DDF_Parteneri "
    " WHERE IDDF = %s ORDER BY IdDdfPartener"
)
_SQL_INSERT = "INSERT INTO FX_DDF_Parteneri (IDDF, CodFiscal, NumePartener) VALUES (%s, %s, %s)"
_SQL_UPDATE = (
    "UPDATE FX_DDF_Parteneri SET CodFiscal = %s, NumePartener = %s WHERE IdDdfPartener = %s"
)
_SQL_DDF_DUPA_COD = (
    "SELECT IDDF, PartAng, CodFiscal, NumePartener FROM FX_DDF "
    " WHERE CodAngajament = %s ORDER BY IDDF, CUAL"
)


class ParteneriInvalizi(Exception):
    """The request is refused before any write. The message is already in Romanian."""


# -----------------------------------------------------------------------------------------
# Small helpers (kept here: ddf_edit.py imports this module, so it cannot be imported back)
# -----------------------------------------------------------------------------------------
def _txt(v):
    return "" if v is None else str(v)


def _serializeaza(v):
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return str(v)


def _json_utf8(payload, status):
    """A JSON response with LITERAL diacritics (ensure_ascii=False)."""
    body = json.dumps(payload, ensure_ascii=False, default=_serializeaza)
    return current_app.response_class(body, status=status, mimetype="application/json")


def cheie_cf(cod_fiscal) -> str:
    """The comparison key of a fiscal code: its digits. A code with no digit at all (a foreign
    identifier) is compared as typed, trimmed and upper-cased, so it still dedupes."""
    cifre = anaf.normalize_cf(cod_fiscal)
    return cifre if cifre else _txt(cod_fiscal).strip().upper()


def tabela_prezenta(cursor, db_name: str) -> bool:
    """Does this database have `FX_DDF_Parteneri`? A failed probe counts as «absent»."""
    if db_name in _TABELA_PREZENTA:
        return True
    try:
        cursor.execute(_SQL_ARE_TABELA, (db_name,))
        prezenta = int((cursor.fetchone() or {}).get("n") or 0) > 0
    except Exception:
        logger.warning("[forexe.ddf_parteneri] %s: proba FX_DDF_Parteneri a esuat; "
                       "se presupune ca lipseste", db_name, exc_info=True)
        return False
    if prezenta:
        _TABELA_PREZENTA.add(db_name)
    return prezenta


def _mesaj_tabela_lipsa() -> str:
    return ("Tabela FX_DDF_Parteneri nu există pe această bază, deci mai mulți parteneri nu se "
            f"pot asocia. Rulați {FISIER_SQL}.")


def _curata(parteneri):
    """The request's partners -> an ordered list of (cheie, cod_fiscal, nume), duplicates by
    fiscal code dropped (the first one wins), empty codes skipped."""
    rezultat = []
    vazute = set()
    for p in parteneri or []:
        if not isinstance(p, dict):
            continue
        cod = _txt(p.get("cod_fiscal")).strip()[:LUNGIME_MAXIMA]
        if not cod:
            continue
        cheie = cheie_cf(cod)
        if cheie in vazute:
            continue
        vazute.add(cheie)
        rezultat.append((cheie, cod, _txt(p.get("nume_partener")).strip()[:LUNGIME_MAXIMA]))
    return rezultat


def citeste_parteneri(cursor, db_name: str, iddf: int, part_ang, antet_cf, antet_nume) -> list:
    """The partners of a document: the header partner first (marked `din_antet`, added even
    when the table has no row for it -- a database not backfilled yet), then the rest in the
    order they were associated. Never raises for a missing table: it answers with the header
    partner alone."""
    pe_cheie = {}
    if iddf and tabela_prezenta(cursor, db_name):
        cursor.execute(_SQL_LISTA, (iddf,))
        for r in cursor.fetchall():
            cod = _txt(r.get("CodFiscal")).strip()
            if not cod:
                continue
            pe_cheie.setdefault(cheie_cf(cod), {
                "cod_fiscal": cod,
                "nume_partener": _txt(r.get("NumePartener")),
                "din_antet": False,
            })

    antet_cheie = cheie_cf(antet_cf) if part_ang and _txt(antet_cf).strip() else ""
    rezultat = []
    if antet_cheie:
        deja = pe_cheie.pop(antet_cheie, None)
        rezultat.append({
            "cod_fiscal": _txt(antet_cf).strip(),
            "nume_partener": _txt(antet_nume) or (deja["nume_partener"] if deja else ""),
            "din_antet": True,
        })
    rezultat.extend(pe_cheie.values())
    return rezultat


def sincronizeaza_parteneri(cursor, db_name: str, iddf: int, parteneri,
                            part_ang, antet_cf, antet_nume) -> dict:
    """Makes the table hold EXACTLY `parteneri` (plus the header partner, which is always a
    row). The editor's save: the list the operator saw is the list that is stored.

    `parteneri` None = the client did not send the list (an older build): nothing is touched.
    Raises `ParteneriInvalizi` when a second partner needs a table the database does not have.
    """
    if parteneri is None:
        return {"adaugati": 0, "stersi": 0, "actualizati": 0}

    dorite = _curata(parteneri)
    antet_cheie = cheie_cf(antet_cf) if part_ang and _txt(antet_cf).strip() else ""
    if antet_cheie and all(c != antet_cheie for c, _, _ in dorite):
        dorite.insert(0, (antet_cheie, _txt(antet_cf).strip()[:LUNGIME_MAXIMA],
                          _txt(antet_nume).strip()[:LUNGIME_MAXIMA]))

    if not tabela_prezenta(cursor, db_name):
        if any(c != antet_cheie for c, _, _ in dorite):
            raise ParteneriInvalizi(_mesaj_tabela_lipsa())
        return {"adaugati": 0, "stersi": 0, "actualizati": 0}

    cursor.execute(_SQL_LISTA, (iddf,))
    existente = {}
    de_sters = []
    for r in cursor.fetchall():
        cheie = cheie_cf(r.get("CodFiscal"))
        if cheie in existente:
            de_sters.append(int(r["IdDdfPartener"]))      # a second row of the same code
        else:
            existente[cheie] = r

    dorite_chei = {c for c, _, _ in dorite}
    for cheie, r in existente.items():
        if cheie not in dorite_chei:
            de_sters.append(int(r["IdDdfPartener"]))
    if de_sters:
        sabloane = ", ".join(["%s"] * len(de_sters))
        cursor.execute(f"DELETE FROM FX_DDF_Parteneri WHERE IdDdfPartener IN ({sabloane})",
                       tuple(de_sters))

    adaugati = actualizati = 0
    for cheie, cod, nume in dorite:
        r = existente.get(cheie)
        if r is None:
            cursor.execute(_SQL_INSERT, (iddf, cod, nume or None))
            adaugati += 1
        elif _txt(r.get("CodFiscal")) != cod or _txt(r.get("NumePartener")) != nume:
            cursor.execute(_SQL_UPDATE, (cod, nume or None, int(r["IdDdfPartener"])))
            actualizati += 1
    return {"adaugati": adaugati, "stersi": len(de_sters), "actualizati": actualizati}


def adauga_parteneri(cursor, db_name: str, iddf: int, parteneri, part_ang, antet_cf, antet_nume) -> int:
    """ADD ONLY (the Sumar button): inserts the partners the document does not have yet, and
    the header partner when its row is missing. Returns how many rows were added."""
    if not tabela_prezenta(cursor, db_name):
        raise ParteneriInvalizi(_mesaj_tabela_lipsa())

    cursor.execute(_SQL_LISTA, (iddf,))
    cheile = {cheie_cf(r.get("CodFiscal")) for r in cursor.fetchall()}

    de_adaugat = []
    antet_cheie = cheie_cf(antet_cf) if part_ang and _txt(antet_cf).strip() else ""
    if antet_cheie:
        de_adaugat.append((antet_cheie, _txt(antet_cf).strip()[:LUNGIME_MAXIMA],
                           _txt(antet_nume).strip()[:LUNGIME_MAXIMA]))
    de_adaugat.extend(_curata(parteneri))

    adaugati = 0
    for cheie, cod, nume in de_adaugat:
        if cheie in cheile:
            continue
        cheile.add(cheie)
        cursor.execute(_SQL_INSERT, (iddf, cod, nume or None))
        adaugati += 1
    return adaugati


def _ddf_dupa_cod(cursor, cod: str) -> dict:
    """The document's header row. Raises when there is none or when the code carries several."""
    cursor.execute(_SQL_DDF_DUPA_COD, (cod,))
    randuri = cursor.fetchall()
    if not randuri:
        raise LookupError(f"Angajamentul «{cod}» nu are încă un document de fundamentare.")
    iddf_uri = {int(r["IDDF"]) for r in randuri}
    if len(iddf_uri) > 1:
        raise ParteneriInvalizi(
            f"Angajamentul «{cod}» are {len(iddf_uri)} documente de fundamentare distincte în "
            "baza de date. Corectați datele mai întâi.")
    return randuri[0]


def _raspuns(cursor, db_name: str, rand: dict) -> dict:
    iddf = int(rand["IDDF"])
    return {
        "iddf": iddf,
        "parteneri": citeste_parteneri(cursor, db_name, iddf, rand.get("PartAng"),
                                       rand.get("CodFiscal"), rand.get("NumePartener")),
    }


@forexe_bp.route("/api/forexe/ddf/parteneri-asociati", methods=["GET"])
@require_session
def get_ddf_parteneri_asociati():
    """The partners associated with the angajament's DDF. Query: `cod` (required)."""
    cod = _txt(request.args.get("cod")).strip()
    if not cod:
        return _json_utf8({"error": "Parametrul «cod» lipsește."}, 400)

    db_name = g.session.db_name
    conn = None
    try:
        conn = get_kbot_connection(db_name)
        cursor = conn.cursor(dictionary=True)
        return _json_utf8(_raspuns(cursor, db_name, _ddf_dupa_cod(cursor, cod)), 200)
    except LookupError as e:
        return _json_utf8({"error": str(e)}, 404)
    except ParteneriInvalizi as e:
        return _json_utf8({"error": str(e)}, 400)
    except Exception as e:
        logger.error(f"[forexe.ddf_parteneri] get {cod}: {e}", exc_info=True)
        return _json_utf8({"error": f"Eroare la citirea partenerilor asociați: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()


@forexe_bp.route("/api/forexe/ddf/parteneri-asociati", methods=["POST"])
@require_session
def post_ddf_parteneri_asociati():
    """Associates partners with the angajament's DDF. Add only; see the module docstring."""
    corp = request.get_json(silent=True)
    if not isinstance(corp, dict):
        return _json_utf8({"error": "Corp JSON lipsă sau nevalid."}, 400)
    cod = _txt(corp.get("cod_angajament")).strip()
    if not cod:
        return _json_utf8({"error": "Codul angajamentului lipsește din cerere."}, 400)
    parteneri = _curata(corp.get("parteneri"))
    if not parteneri:
        return _json_utf8({"error": "Alege cel puțin un partener."}, 400)

    db_name = g.session.db_name
    conn = None
    try:
        conn = get_kbot_connection(db_name)
        conn.autocommit = False
        cursor = conn.cursor(dictionary=True)
        if not conn.in_transaction:
            conn.start_transaction()

        rand = _ddf_dupa_cod(cursor, cod)
        adaugati = adauga_parteneri(
            cursor, db_name, int(rand["IDDF"]),
            [{"cod_fiscal": c, "nume_partener": n} for _, c, n in parteneri],
            rand.get("PartAng"), rand.get("CodFiscal"), rand.get("NumePartener"))
        conn.commit()

        raspuns = _raspuns(cursor, db_name, rand)
        raspuns["adaugati"] = adaugati
        logger.info("[forexe.ddf_parteneri] %s: cod=%s iddf=%s adaugati=%s",
                    db_name, cod, raspuns["iddf"], adaugati)
        return _json_utf8(raspuns, 200)
    except LookupError as e:
        if conn is not None:
            conn.rollback()
        return _json_utf8({"error": str(e)}, 404)
    except ParteneriInvalizi as e:
        if conn is not None:
            conn.rollback()
        return _json_utf8({"error": str(e)}, 400)
    except Exception as e:
        if conn is not None:
            conn.rollback()
        logger.error(f"[forexe.ddf_parteneri] post {cod}: {e}", exc_info=True)
        return _json_utf8({"error": f"Eroare la asocierea partenerilor: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()
