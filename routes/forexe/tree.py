# routes/forexe/tree.py
"""
Ruta arborelui de angajamente pentru MainForm (felia 0008).

Contract (GET /api/forexe/tree?ss=<SS>&an=<AN>&include_hidden=0|1):
    { "db_name": "<unit db>", "count": N, "rows": [ {...}, ... ] }

Scope: baza conectata ESTE unitatea (in MariaDB o baza = o unitate). Nu exista
parametru id_unitate si nici filtru DC — db_name vine din sesiune (g.session.db_name),
nu din cerere, deci un token nu poate tinti alta baza decat cea pe care s-a logat.

Sursa (citita verbatim din FX_System_Export/QUERIES/ la 2026-07-15) — endpoint-ul
compune DOUA query-uri Access, nu una:
  - row-source : qFX_MAIN_TREE_DESCRIERE (populeaza NODURILE arborelui prin
                 mdl_FX_PopulareTree.Angajamente_SQL) -> coloanele de afisare;
  - flags      : qFX_MAIN_TREE (sursa rcAngInd in frmFX_MAIN.RefreshTreeQuery)
                 -> cele noua Are*.

Traducere Access -> MariaDB:
  CBool((SELECT Count(*) ...)>0)  -> EXISTS (SELECT 1 ...)   [best practice MariaDB]
  ConcatRelated('SS','FX_Indicatori',...) -> GROUP_CONCAT(DISTINCT i.SS SEPARATOR ';')
  Nz([FX_DDF]![IDDF],0)<>0        -> IDDF IS NOT NULL (IDDF e coloana pe FX_Angajamente)

Costul nu conteaza: arborele are cel mult cateva sute de randuri, deci noua EXISTS
corelate per rand sunt acceptabile in schimbul claritatii.
"""
import logging

from flask import request, jsonify, g, current_app
import json

from routes.auth.guard import require_session    # bearer opac (Felia 1 auth)
from utils.database import get_kbot_connection   # serverul K-BOT (DB_CONFIG_NEW)

from . import forexe_bp
from .prelucrare_helpers import SNAPSHOT_COUNTS_SQL

logger = logging.getLogger(__name__)

# Slice 0101: the receptions of the angajament whose chain does not close -- the LAST snapshot
# of the chain (DataH, then IDRH) is not a deletion row and its Total differs from the
# reception's current value (FX_Receptii_R.SumaAntet), both rounded to 2 decimals. The same test
# as AsociereForm.ColoreazaRosuLanturileNeinchise / the «Lantul nu se inchide» tooltip line: keep
# the two in step. Only snapshots that count (they have lines) are looked at, like the form.
#
# One scalar column: NULL when every chain closes, otherwise «YYYY-MM-DD~total~suma» per
# reception, joined with «|», oldest reception first. The client formats it (Romanian
# numbers); CAST(... AS CHAR) and not DATE_FORMAT, so no «%» has to survive the driver.
_LANT_NEINCHIS = (
    "(SELECT GROUP_CONCAT(CONCAT(CAST(DATE(lr.DataR) AS CHAR), '~', "
    "                           CAST(ROUND(COALESCE(lh.Total, 0), 2) AS DECIMAL(18,2)), '~', "
    "                           CAST(ROUND(COALESCE(lr.SumaAntet, 0), 2) AS DECIMAL(18,2))) "
    "                     ORDER BY lr.DataR, lr.IDRR SEPARATOR '|') "
    " FROM FX_Receptii_R lr JOIN FX_Receptii_H lh ON lh.IDRR = lr.IDRR "
    " WHERE lr.CodAngajament = a.CodAngajament "
    "   AND COALESCE(lh.EsteStergere, 0) = 0 "
    "   AND EXISTS (SELECT 1 FROM FX_Receptii ll WHERE ll.IDRH = lh.IDRH) "
    "   AND NOT EXISTS (SELECT 1 FROM FX_Receptii_H lh2 WHERE lh2.IDRR = lh.IDRR "
    "         AND (COALESCE(lh2.DataH, '1900-01-01') > COALESCE(lh.DataH, '1900-01-01') "
    "              OR (COALESCE(lh2.DataH, '1900-01-01') = COALESCE(lh.DataH, '1900-01-01') "
    "                  AND lh2.IDRH > lh.IDRH))) "
    "   AND ROUND(COALESCE(lh.Total, 0), 2) <> ROUND(COALESCE(lr.SumaAntet, 0), 2))"
)

# Cele noua flag-uri, fiecare un EXISTS corelat — deci NICIUN join spre FX_DDF.
# Asta nu e doar stil: un LEFT JOIN FX_DDF ar dubla randul angajamentului daca
# vreodata apar doua DDF-uri pe acelasi CodAngajament (planul spune 1-la-cel-mult-1,
# dar datele nu au constrangere care sa o impuna), iar un GROUP BY care contine
# d.IDDF NU ar repara dublajul. Fara join, „un rand per CodAngajament” e garantat
# de PRIMARY KEY, fara GROUP BY deloc.
#   AreDDF      : IDDF e coloana PE FX_Angajamente -> IS NOT NULL e raspunsul complet.
#   ArePartener : FX_DDF.PartAng = 1; fara DDF nu exista rand -> EXISTS fals.
#   AreOrd      : lantul angajament -> DDF -> ORD (FX_ORD.IDDF = FX_DDF.IDDF).
_SELECT = (
    "SELECT a.CodAngajament, a.IDDF, a.Descriere, a.Stare, a.DataCreare, "
    "a.DataDefinitivare, a.Incarcat, a.Preluat, a.Salarii, a.ASCUNS, "
    # Slice 0100: when the last FOREXE download of the angajament was saved. The column comes
    # from sql/0100_fx_angajamente_data_actualizare.sql; where it is not applied yet, _sql()
    # puts a literal NULL here (every angajament then reads as «never updated»).
    "{data_actualizare} AS DataActualizare, "
    "(SELECT GROUP_CONCAT(DISTINCT i.SS ORDER BY i.SS SEPARATOR ';') "
    " FROM FX_Indicatori i WHERE i.CodAngajament = a.CodAngajament) AS Surse, "
    # Slice 0777: the moment the angajament was made, as FOREXE's own history says it --
    # the FX_Istoric row «Angajament nou.» (Access literal, trailing dot; matched with or
    # without it). DataFX is DATETIME and the tree sorts on it WITH the time part, so it is
    # sent whole. MIN() keeps the subquery scalar should a second such row ever appear.
    "(SELECT MIN(x.DataFX) FROM FX_Istoric x "
    " WHERE x.CodAngajament = a.CodAngajament "
    "   AND TRIM(TRAILING '.' FROM TRIM(x.Descriere)) = 'Angajament nou') AS DataAngajamentNou, "
    "EXISTS (SELECT 1 FROM FX_Indicatori i WHERE i.CodAngajament = a.CodAngajament) AS AreIndicatori, "
    "EXISTS (SELECT 1 FROM FX_Istoric x WHERE x.CodAngajament = a.CodAngajament) AS AreIstoric, "
    "EXISTS (SELECT 1 FROM FX_DDF_REV_SA r WHERE r.CodAngajament = a.CodAngajament) AS AreRevizii, "
    "EXISTS (SELECT 1 FROM FX_Rezervari z WHERE z.CodAngajament = a.CodAngajament) AS AreRezervari, "
    # F32: un antet fara linii (si care nu e stergere) nu e instantaneu, deci nu aprinde
    # steagul; acelasi filtru ca vederea Receptii (`SNAPSHOT_COUNTS_SQL`, alias `H`).
    "EXISTS (SELECT 1 FROM FX_Receptii_H H WHERE H.CodAngajament = a.CodAngajament "
    "        AND " + SNAPSHOT_COUNTS_SQL + ") AS AreReceptii, "
    "EXISTS (SELECT 1 FROM FX_Plati p WHERE p.CodAngajament = a.CodAngajament) AS ArePlati, "
    "(a.IDDF IS NOT NULL) AS AreDDF, "
    "EXISTS (SELECT 1 FROM FX_DDF d "
    "        WHERE d.CodAngajament = a.CodAngajament AND d.PartAng = 1) AS ArePartener, "
    "EXISTS (SELECT 1 FROM FX_DDF d JOIN FX_ORD o ON o.IDDF = d.IDDF "
    "        WHERE d.CodAngajament = a.CodAngajament) AS AreOrd, "
    # Slice 0080-02: the Extrase view -- a bank operation carrying this angajament in
    # CodContract (FX_Extrase.CodContract = CodAngajament).
    "EXISTS (SELECT 1 FROM FX_Extrase e WHERE e.CodContract = a.CodAngajament) AS AreExtrase, "
    # Slice 0097: the «Note corectie» view -- a CAB correction note corrects an operation on
    # this angajament. The notes tables (slice 0088) may not exist on a unit database yet, so
    # this column is filled in by _sql(): EXISTS when they do, a literal 0 when they do not.
    "{are_note_cab} AS AreNoteCab, "
    # Slice 00EF-22: the «E-Factura» view -- the angajament's DDF has received e-invoices (a partner's tax code, or a link the operator
    # made). The tables (slice 00EF-02 / 00EF-17) may not exist on a unit database yet: _sql() fills EXISTS when they do, a literal 0 when not.
    "{are_primite} AS ArePrimite, "
    # Slice 0008-02: the name the operator gave (column from sql/0008_02_alias_grupe.sql; where it
    # is not applied yet, _sql() puts a literal NULL here).
    "{alias} AS Alias, "
    # Slice 0101: the receptions whose chain does not close (see _LANT_NEINCHIS).
    + _LANT_NEINCHIS + " AS LantNeinchis "
    "FROM FX_Angajamente a "
)

# Slice 0097: the AreNoteCab column, in its two shapes (see _SELECT).
_ARE_NOTE_CAB = ("EXISTS (SELECT 1 FROM FX_NoteCAB_Corectii k "
                 "        WHERE k.CodAngajament = a.CodAngajament)")
_NO_NOTE_CAB = "0"
# Slice 00EF-22: the ArePrimite column, in its two shapes. No join spreads the row: both are EXISTS. The tax code of a partner is brought
# to the form EF_Primite.CuiNormalizat has (letters and digits, upper case, a leading «RO» before digits dropped -- the same rule as
# routes/efactura/primite.py and EfCui in the Migrator).
_ARE_PRIMITE = ("(EXISTS (SELECT 1 FROM FX_DDF_Parteneri dp JOIN EF_Primite ep "
                "         ON ep.CuiNormalizat = UPPER(REGEXP_REPLACE(REGEXP_REPLACE(dp.CodFiscal, '[^[:alnum:]]', ''), '^RO([0-9])', '\\\\1')) "
                "         WHERE dp.IDDF = a.IDDF) "
                " OR EXISTS (SELECT 1 FROM EF_PrimiteAsocieri pa WHERE pa.IDDF = a.IDDF))")
_NO_PRIMITE = "0"
_SQL_PRIMITE_TABLES = ("SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA = %s "
                       "AND TABLE_NAME IN ('EF_Primite', 'EF_PrimiteAsocieri', 'FX_DDF_Parteneri')")
_SQL_NOTE_TABLE = ("SELECT COUNT(*) FROM information_schema.TABLES "
                   "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'FX_NoteCAB_Corectii'")

# WHERE-ul (PLAN_TreeDataApi.md):
#   An     : randurile cu DataCreare NULL se arata INTOTDEAUNA — nu sunt inca
#            descarcate din FOREXE, deci nu au an.
#   SS     : ingusteaza CARE angajamente apar; Surse ramane lista completa (afisare).
#            ESCAPE ORFANI: un angajament cu ZERO indicatori (creat de curand, „randuri
#            doar in FX_Angajamente") NU are SS, deci un filtru SS strict l-ar ascunde.
#            Legacy il pastra printr-o ramura UNION ALL separata in
#            qFX_MAIN_TREE_DESCRIERE / qFX_MAIN_TREE_DATA (angajamentele fara indicatori).
#            O oglindim cu „EXISTS(indicator pe SS) OR NOT EXISTS(niciun indicator)":
#            filtrul SS ingusteaza DOAR angajamentele care CHIAR au indicatori; orfanii
#            raman mereu vizibili. Slice 0777: a leading "%s = 1 OR" lifts the whole SS
#            block when ss=* (all sources); bind tuple is (an, an, all_ss, ss, include_hidden).
#   Slice 0008-02: an = 0 lifts the YEAR filter too (a group of angajamente spans years).
#   Ascuns : implicit exclude ASCUNS<>0; include_hidden=1 le readuce (btnOpt).
#   Stare  : exclude Anulat/Suspendat. NU vine din qFX_MAIN_TREE (acela nu are WHERE
#            deloc) — vine din qFX_MAIN_TREE_DATA:7 si mdl_FX_PopulareTree.md:253,
#            unde conditia legacy este
#            ((InStr(1,[Stare],'Anulat')>0) Or (InStr(1,[Stare],'Suspendat')>0)
#             Or [Ascuns]) = False.
#            LIKE e case-insensitive pe colatia utf8mb4_general_ci a bazei, deci
#            oglindeste InStr() din Access fara functii suplimentare.
_WHERE = (
    "WHERE (%s = 0 OR YEAR(a.DataCreare) = %s OR a.DataCreare IS NULL) "
    "AND ( "
    "    %s = 1 OR "
    "    EXISTS (SELECT 1 FROM FX_Indicatori i "
    "            WHERE i.CodAngajament = a.CodAngajament AND i.SS = %s) "
    "    OR NOT EXISTS (SELECT 1 FROM FX_Indicatori i "
    "                   WHERE i.CodAngajament = a.CodAngajament) "
    ") "
    "AND (%s = 1 OR a.ASCUNS = 0) "
    "AND COALESCE(a.Stare, '') NOT LIKE '%%Anulat%%' "
    "AND COALESCE(a.Stare, '') NOT LIKE '%%Suspendat%%' "
)

# Fara GROUP BY: nu exista join care sa multiplice randurile (vezi nota de la _SELECT),
# iar CodAngajament e PRIMARY KEY pe FX_Angajamente -> exact un rand per angajament.
# Ordinea oglindeste qFX_MAIN_TREE_DESCRIERE ("ORDER BY O, Descriere"; O e ramura de
# orfani, care aici nu exista — nu filtram pe indicatori decat prin SS).
_ORDER = "ORDER BY a.Descriere"

# Slice 0100: the DataActualizare column, in its two shapes (see _SELECT).
_DATA_ACTUALIZARE = "a.DataActualizare"
_NO_DATA_ACTUALIZARE = "NULL"
_SQL_DATA_ACTUALIZARE_COLUMN = ("SELECT COUNT(*) FROM information_schema.COLUMNS "
                                "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'FX_Angajamente' "
                                "AND COLUMN_NAME = 'DataActualizare'")


# Slice 0008-02: the Alias column, in its two shapes (see _SELECT).
_ALIAS = "a.Alias"
_NO_ALIAS = "NULL"
_SQL_ALIAS_COLUMN = ("SELECT COUNT(*) FROM information_schema.COLUMNS "
                     "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'FX_Angajamente' "
                     "AND COLUMN_NAME = 'Alias'")


def _sql(has_note_tables: bool, has_data_actualizare: bool = True, has_alias: bool = True, has_primite: bool = True) -> str:
    """The tree query, with AreNoteCab read from the notes table only where it exists, and
    DataActualizare only where its column was added (slice 0100)."""
    are_note_cab = _ARE_NOTE_CAB if has_note_tables else _NO_NOTE_CAB
    data_act = _DATA_ACTUALIZARE if has_data_actualizare else _NO_DATA_ACTUALIZARE
    return (_SELECT.replace("{are_note_cab}", are_note_cab)
            .replace("{are_primite}", _ARE_PRIMITE if has_primite else _NO_PRIMITE)
            .replace("{data_actualizare}", data_act)
            .replace("{alias}", _ALIAS if has_alias else _NO_ALIAS) + _WHERE + _ORDER)


_SQL = _sql(True)

# The ss value that lifts the SS filter (slice 0777). Bind order of _SQL:
# (an, an, all_ss, ss, include_hidden).
ALL_SOURCES = "*"


def _json_utf8(payload, status):
    """Raspuns JSON cu diacritice LITERALE (ensure_ascii=False): Descriere/Stare
    contin «În derulare» si trebuie sa ajunga la client ca UTF-8 real, nu \\uXXXX."""
    body = json.dumps(payload, ensure_ascii=False)
    return current_app.response_class(body, status=status, mimetype="application/json")


@forexe_bp.route("/api/forexe/tree", methods=["GET"])
@require_session
def get_tree():
    """Arborele de angajamente al bazei conectate, filtrat pe an + SS.

    Query: an (obligatoriu, intreg), ss (obligatoriu; "*" = all sources, slice 0777), include_hidden (0/1, implicit 0).
    Returneaza { db_name, count, rows: [ {CodAngajament, IDDF, Descriere, Stare,
    DataCreare, DataDefinitivare, Incarcat, Preluat, Salarii, Ascuns, DataActualizare, Surse, DataAngajamentNou,
    AreIndicatori, AreIstoric, AreRevizii, AreRezervari, AreReceptii, ArePlati,
    AreDDF, ArePartener, AreOrd, AreExtrase, AreNoteCab, Alias, LantNeinchis}, ... ] }.
    """
    an_raw = request.args.get("an")
    if an_raw is None or str(an_raw).strip() == "":
        return _json_utf8({"error": "Parametru lipsă: an"}, 400)
    try:
        an = int(an_raw)
    except (TypeError, ValueError):
        return _json_utf8({"error": "an invalid (așteptat întreg)"}, 400)

    ss = request.args.get("ss")
    if ss is None or str(ss).strip() == "":
        return _json_utf8({"error": "Parametru lipsă: ss"}, 400)
    ss = str(ss).strip()
    # Slice 0777: ss=* = every source. The tree sorted by creation date is a timeline of
    # the whole year, not of one SS (operator, 23.09.2026) -- the client shows the SURSE
    # column there so the operator still sees where each row belongs.
    all_ss = 1 if ss == ALL_SOURCES else 0

    include_hidden = 1 if str(request.args.get("include_hidden", "0")).strip() == "1" else 0

    # Scope: baza sesiunii, niciodata din cerere (vezi nota din capul fisierului).
    db_name = g.session.db_name

    conn = None
    try:
        conn = get_kbot_connection(db_name)
        cursor = conn.cursor()
        cursor.execute(_SQL_NOTE_TABLE, (db_name,))
        has_note_tables = int(cursor.fetchone()[0] or 0) > 0
        cursor.execute(_SQL_DATA_ACTUALIZARE_COLUMN, (db_name,))
        has_data_actualizare = int(cursor.fetchone()[0] or 0) > 0
        cursor.execute(_SQL_ALIAS_COLUMN, (db_name,))
        has_alias = int(cursor.fetchone()[0] or 0) > 0
        cursor.execute(_SQL_PRIMITE_TABLES, (db_name,))
        has_primite = int(cursor.fetchone()[0] or 0) == 3
        cursor.execute(_sql(has_note_tables, has_data_actualizare, has_alias, has_primite),
                       (an, an, all_ss, ss, include_hidden))
        rows = []
        for (cod, iddf, descriere, stare, data_creare, data_def, incarcat, preluat,
             salarii, ascuns, data_actualizare, surse, data_ang_nou, are_indicatori, are_istoric, are_revizii,
             are_rezervari, are_receptii, are_plati, are_ddf, are_partener,
             are_ord, are_extrase, are_note_cab, are_primite, alias, lant_neinchis) in cursor.fetchall():
            rows.append({
                "CodAngajament": cod,
                "IDDF": iddf,
                "Descriere": descriere,
                "Stare": stare,
                "DataCreare": data_creare.isoformat() if data_creare is not None else None,
                "DataDefinitivare": data_def.isoformat() if data_def is not None else None,
                "Incarcat": bool(incarcat),
                "Preluat": bool(preluat),
                "Salarii": bool(salarii),
                "Ascuns": bool(ascuns),
                "Surse": surse,
                # Slice 0100: when the last download was saved; None = none since the column exists.
                "DataActualizare": data_actualizare.isoformat() if data_actualizare is not None else None,
                # Full datetime (time included): the date sort orders on it (slice 0777).
                "DataAngajamentNou": data_ang_nou.isoformat() if data_ang_nou is not None else None,
                "AreIndicatori": bool(are_indicatori),
                "AreIstoric": bool(are_istoric),
                "AreRevizii": bool(are_revizii),
                "AreRezervari": bool(are_rezervari),
                "AreReceptii": bool(are_receptii),
                "ArePlati": bool(are_plati),
                "AreDDF": bool(are_ddf),
                "ArePartener": bool(are_partener),
                "AreOrd": bool(are_ord),
                "AreExtrase": bool(are_extrase),
                "AreNoteCab": bool(are_note_cab),
                "ArePrimite": bool(are_primite),
                # Slice 0101: NULL = every chain closes (see _LANT_NEINCHIS for the shape).
                # Slice 0008-02: the operator's name for the angajament (None = none).
                "Alias": alias,
                "LantNeinchis": lant_neinchis,
            })
        logger.info("[forexe.tree] %s: an=%s ss=%s include_hidden=%s -> %s randuri",
                    db_name, an, ss, include_hidden, len(rows))
        return _json_utf8({"db_name": db_name, "count": len(rows), "rows": rows}, 200)
    except Exception as e:
        # Fara inghitire: o eroare de baza intoarce motivul, NU un arbore gol —
        # un arbore gol ar minti operatorul ca unitatea nu are angajamente.
        logger.error(f"[forexe.tree] {e}", exc_info=True)
        return _json_utf8({"error": f"Eroare la citirea arborelui: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()
