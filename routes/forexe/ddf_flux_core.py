# routes/forexe/ddf_flux_core.py
"""
The rules of the DDF signing flow for DDFs created in K-BOT (slice 0124-03). No routes here: this module
is imported by `routes/auth/guard.py` (the restricted roles), by `pdf.py` (who may read / write a revision's
PDF, and where `DeSemnat` moves on) and by `ddf_flux.py` (the routes of the flow).

`FX_DDF_REV.DeSemnat` (sql/0124) = the ROLE that has to sign the revision next. NULL = the revision is not in
the flow (every revision that existed before the column, every DDF that comes from FOREXE).

    generated (section A only)  -> DeSemnat = the role of the document's compartment (FX_DDF_COMP_ROL)
    A signed                    -> 'Contabil'   (the accountant or the FOREXE proxy signs B)
    B signed                    -> 'Director'
    Ordonator signed            -> NULL

THE CRITICAL RULE (operator, 30.10.2026): a `Secretar` or an `Administrator` must not see any DDF other than the ones
addressed to their role. EVERYTHING ELSE they could do before stays as it was (operator, same day: «in rest au voie sa faca
ce aveau voie»). It is enforced on the server, in two places:
  * `refuza_restrans` -- called by the guard for those roles on the DDF routes (`/api/forexe/ddf*`) and the angajament dump: a route
    that names a revision is allowed only if the revision is addressed to the role; a DDF route nobody listed stays closed;
    routes outside the DDF area are not touched;
  * `filtreaza_ddf` -- `GET /api/forexe/ddf` (all revisions of an angajament) answers with the addressed revisions only.
"""
import json
import logging
import re

from flask import current_app, g, request

from utils.database import get_kbot_connection

logger = logging.getLogger(__name__)

ROL_ADMINISTRATOR = "Administrator"
ROL_SECRETAR = "Secretar"
ROL_IMPUTERNICIT = "Imputernicit"
ROL_CONTABIL = "Contabil"
ROL_DIRECTOR = "Director"

# The roles an operator may give a compartment (who signs section A for it).
ROLURI_FLUX = (ROL_ADMINISTRATOR, ROL_SECRETAR, ROL_IMPUTERNICIT, ROL_CONTABIL, ROL_DIRECTOR)

# See the module note: these roles reach the allow-list below and nothing else.
ROLURI_RESTRANSE = frozenset({ROL_SECRETAR, ROL_ADMINISTRATOR})

# What a signature moves DeSemnat to.
DUPA_A = ROL_CONTABIL
DUPA_B = ROL_DIRECTOR

_ROLURI_SEMNATURA = ("A", "B", "Ordonator")

# The routes a restricted role may call. Everything else answers 403.
_PREFIXE_AUTH = ("/api/auth/",)
_RUTA_LISTA = "/api/forexe/ddf/de-semnat"
_RUTA_PDF = re.compile(r"^/api/forexe/ddf/pdf/\d+$")


def rol_sesiune() -> str:
    """The role of the unit the session is logged into (`ctx["Role"]`, set at login)."""
    s = getattr(g, "session", None)
    return str(((getattr(s, "ctx", None) or {}).get("Role")) or "")


def roluri_vizibile(rol: str) -> tuple:
    """The `DeSemnat` values a role finds addressed to it. A proxy also sees what is the accountant's."""
    if rol == ROL_IMPUTERNICIT:
        return (ROL_IMPUTERNICIT, ROL_CONTABIL)
    if rol in (ROL_ADMINISTRATOR, ROL_SECRETAR, ROL_CONTABIL, ROL_DIRECTOR):
        return (rol,)
    return ()


def json_utf8(payload, status):
    body = json.dumps(payload, ensure_ascii=False, default=str)
    return current_app.response_class(body, status=status, mimetype="application/json")


# ---------------------------------------------------------------------------------------
# Columns / tables that may not exist yet on a unit database (sql/0124 not carried there)
# ---------------------------------------------------------------------------------------
_PREZENTA = {}

_SQL_ARE_COLOANA = (
    "SELECT COUNT(*) FROM information_schema.COLUMNS "
    " WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'FX_DDF_REV' AND COLUMN_NAME = 'DeSemnat'"
)
_SQL_ARE_TABELA = (
    "SELECT COUNT(*) FROM information_schema.TABLES "
    " WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'FX_DDF_COMP_ROL'"
)


def _prezent(cursor, db_name: str, cheie: str, sql: str) -> bool:
    """Probed once per database; only a True answer is remembered (a database where the script is run later
    starts working without a restart)."""
    if _PREZENTA.get((db_name, cheie)):
        return True
    cursor.execute(sql, (db_name,))
    row = cursor.fetchone()
    value = next(iter(row.values())) if isinstance(row, dict) else (row[0] if row else 0)
    present = bool(value)
    if present:
        _PREZENTA[(db_name, cheie)] = True
    return present


def are_de_semnat(cursor, db_name: str) -> bool:
    return _prezent(cursor, db_name, "desemnat", _SQL_ARE_COLOANA)


def are_comp_rol(cursor, db_name: str) -> bool:
    return _prezent(cursor, db_name, "comprol", _SQL_ARE_TABELA)


# ---------------------------------------------------------------------------------------
# DeSemnat is set when a K-BOT-made revision is CREATED (called by ddf_edit.py's save)
# ---------------------------------------------------------------------------------------
def rol_pentru_revizie_noua(cursor, db_name: str, comp: str):
    """The role that signs section A of a revision being created, by the document's compartment.

    Returns (False, None) when the unit has no flow (sql/0124 not carried there): the revision is made the old way, outside the flow.
    Returns (True, role) otherwise; role is None when the compartment has no role -- the caller refuses the save with a message.
    Works with a tuple cursor and a dictionary cursor alike.
    """
    if not are_de_semnat(cursor, db_name) or not are_comp_rol(cursor, db_name):
        return False, None
    cursor.execute("SELECT Rol FROM FX_DDF_COMP_ROL WHERE Comp = %s", ((comp or "").strip(),))
    row = cursor.fetchone()
    if not row:
        return True, None
    rol = next(iter(row.values())) if isinstance(row, dict) else row[0]
    return True, (rol or None)


# ---------------------------------------------------------------------------------------
# DeSemnat moves with every signature (called by pdf.py inside the upload's transaction)
# ---------------------------------------------------------------------------------------
def urmatorul(semnatura: str, curent):
    """Who signs next, given the roles now in the PDF and the current `DeSemnat`."""
    roluri = {r.strip() for r in (semnatura or "").split(",") if r.strip()}
    if "Ordonator" in roluri:
        return None
    if "B" in roluri:
        return DUPA_B
    if "A" in roluri:
        return DUPA_A
    return curent


def avanseaza(cursor, db_name: str, idrev: int, semnatura: str) -> None:
    """Moves `DeSemnat` of a revision that is in the flow. A revision outside the flow (NULL), or a database
    without the column, is left alone. Runs in the caller's transaction (no commit here)."""
    if not are_de_semnat(cursor, db_name):
        return
    cursor.execute("SELECT DeSemnat FROM FX_DDF_REV WHERE IDREV = %s", (idrev,))
    row = cursor.fetchone()
    if not row or row[0] is None:
        return
    nou = urmatorul(semnatura, row[0])
    if nou != row[0]:
        cursor.execute("UPDATE FX_DDF_REV SET DeSemnat = %s WHERE IDREV = %s", (nou, idrev))
        logger.info("[forexe.ddf_flux] %s: IDREV=%s DeSemnat %s -> %s (semnatura=%s)",
                    db_name, idrev, row[0], nou, semnatura)


# ---------------------------------------------------------------------------------------
# What a restricted role may see of the DDFs (called by routes/auth/guard.py, and by ddf.py)
# ---------------------------------------------------------------------------------------
_RX_PDF = re.compile(r"^/api/forexe/ddf/pdf/(\d+)(?:/print)?$")
_RX_DRAFT = re.compile(r"^/api/forexe/ddf/draft/\d+/(\d+)$")
_RX_REV = re.compile(r"^/api/forexe/ddf/rev/(\d+)$")
_RX_TRIMITERE = re.compile(r"^/api/forexe/ddf/trimitere/(\d+)(?:/.*)?$")
_RX_ATT = re.compile(r"^/api/forexe/ddf/att/(\d+)/imagine$")
_RUTA_DUMP = "/api/forexe/angajament/dump"
_RUTA_DDF = "/api/forexe/ddf"
_RUTA_LISTA = "/api/forexe/ddf/de-semnat"
# Lookup lists of the DDF editor: they show no document, so they stay open to everybody.
_CITIRI_LIBERE = frozenset({
    "/api/forexe/ddf/clasificatii", "/api/forexe/ddf/parteneri",
    "/api/forexe/ddf/comp", "/api/forexe/ddf/surse-program",
})


def _interzis():
    return json_utf8({"error": "Cu acest rol se văd doar documentele de fundamentare care vă sunt adresate.",
                      "reason": "ROL_RESTRANS"}, 403)


def _idrev_al_atasamentului(cursor, id_rev_att: int):
    cursor.execute("SELECT IDREV FROM FX_DDF_REV_ATT WHERE IdRevAtt = %s", (id_rev_att,))
    row = cursor.fetchone()
    return row[0] if row else None


def _revizie_adresata(idrev, rol: str, id_rev_att=None) -> bool:
    """Is the revision (or the one the attachment belongs to) addressed to the role? Fails closed on any error."""
    db_name = g.session.db_name
    conn = None
    try:
        conn = get_kbot_connection(db_name)
        cursor = conn.cursor()
        if id_rev_att is not None:
            idrev = _idrev_al_atasamentului(cursor, id_rev_att)
        if idrev is None or not are_de_semnat(cursor, db_name):
            return False
        cursor.execute("SELECT DeSemnat FROM FX_DDF_REV WHERE IDREV = %s", (idrev,))
        row = cursor.fetchone()
        return bool(row and row[0] in roluri_vizibile(rol))
    except Exception as e:
        logger.error("[forexe.ddf_flux] verificarea accesului la revizie: %s", e, exc_info=True)
        return False
    finally:
        if conn is not None:
            conn.close()


def refuza_restrans(path: str, method: str):
    """None = go on. A response = stop with it. Only a Secretar / Administrator is ever refused here.

    Inside the DDF area a route that names a revision is allowed for the revisions addressed to the role; an upload of a PDF
    must carry exactly the signature `A` (they sign section A and nothing else). A DDF route not listed here is closed.
    """
    rol = rol_sesiune()
    if rol not in ROLURI_RESTRANSE:
        return None
    if path == _RUTA_DUMP:
        return _interzis()
    if not (path == _RUTA_DDF or path.startswith(_RUTA_DDF + "/")):
        return None                                   # outside the DDF area: as it always was
    if path == _RUTA_LISTA or (path == _RUTA_DDF and method == "GET"):
        return None                                   # the list of what is addressed; the DDF read is filtered in ddf.py
    if method == "GET" and path in _CITIRI_LIBERE:
        return None

    m = _RX_PDF.match(path)
    if m:
        if method == "PUT":
            roluri = [r.strip() for r in (request.headers.get("X-Semnatura") or "").split(",") if r.strip()]
            if roluri != ["A"]:
                return json_utf8({"error": "Cu acest rol se semnează doar Secțiunea A."}, 403)
        return None if _revizie_adresata(int(m.group(1)), rol) else json_utf8(
            {"error": "Documentul nu există sau nu vă este adresat."}, 404)
    for rx in (_RX_DRAFT, _RX_REV, _RX_TRIMITERE):
        m = rx.match(path)
        if m:
            return None if _revizie_adresata(int(m.group(1)), rol) else _interzis()
    m = _RX_ATT.match(path)
    if m:
        return None if _revizie_adresata(None, rol, id_rev_att=int(m.group(1))) else _interzis()
    return _interzis()


def filtreaza_ddf(revizii: list, linii: list, sectiuneb: list, atasamente: list):
    """For a restricted role the DDF read keeps only the addressed revisions (and their rows); everybody else gets it whole.
    A revision is addressed when its `de_semnat` is one of the role's. Returns the four lists."""
    rol = rol_sesiune()
    if rol not in ROLURI_RESTRANSE:
        return revizii, linii, sectiuneb, atasamente
    vizibile = roluri_vizibile(rol)
    ramase = {r["idrev"] for r in revizii if r.get("de_semnat") in vizibile}
    return ([r for r in revizii if r["idrev"] in ramase],
            [x for x in linii if x.get("idrev") in ramase],
            [x for x in sectiuneb if x.get("idrev") in ramase],
            [x for x in atasamente if x.get("idrev") in ramase])
