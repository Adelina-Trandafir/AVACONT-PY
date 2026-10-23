# routes/forexe/ddf_program.py
"""
Programul unui document de fundamentare (FX_DDF.Program) legat de SS-ul datelor salvate (slice 0114-27).

REGULA (operator, 23.10.2026)
=============================
Programul il da SS-ul (sursa + sectorul), NU sesiunea: cu «Ordoneaza dupa data crearii» (modul in care
lucreaza toti utilizatorii) selectorul de sector / sursa e ascuns, deci SS-ul sesiunii nu spune nimic
despre angajament.

    SS = 01A   ->  0000002510
    altfel     ->  0000000000

(Acelasi lucru il spune AVACONT_COMUN.DefaProgram: 01A -> 0000002510, 02A / 02E -> 0000000000.)

CE SE COMPARA LA DESCHIDERE (aceeasi cale ca eroarea rezervarilor si a receptiilor)
===================================================================================
Antetul DDF (FX_DDF.Program) se compara cu programul care rezulta din SS-ul:
  * indicatorilor angajamentului (FX_Indicatori.SS);
  * randurilor fiecarei revizii DDF salvate (FX_DDF_REV_SA.SS);
  * randurilor fiecarei ordonantari salvate (FX_ORD_TBL.CodSSI, primele 3 caractere = SS).
Daca vreuna dintre ele da alt program decat antetul, angajamentul e raportat, cu fiecare loc care nu se
potriveste. Totul in cateva interogari pe toata baza, ca sa mearga la incarcarea arborelui.

CE FACE
=======
  * `program_pentru_surse` / `program_pentru_angajament`: regula; o foloseste si `ddf_edit.py` la
    `POST /api/forexe/ddf/genereaza` (revizia 0 a unui angajament din FOREXE).
  * `gaseste_programe_gresite`: angajamentele cu neconcordante (arborele: coloana `ProgramGresit`).
  * `POST /api/forexe/ddf/repara-program` {cod, aplica}: proba sau scriere. Scrie DOAR FX_DDF.Program,
    cand exista un singur program corect; un PDF deja generat sau semnat nu se schimba.

NEVERIFICAT: nu a rulat pe MariaDB. Programul scris INAUNTRUL PDF-urilor stocate (datele formularului) nu se
vede din server si nu e comparat aici.
"""
import json
import logging
from typing import Dict, Iterable, List, Optional

from flask import request, g, current_app

from routes.auth.guard import require_session
from utils.database import get_kbot_connection

from . import forexe_bp

logger = logging.getLogger(__name__)

SS_PROGRAM_01A = "01A"
PROGRAM_01A = "0000002510"
PROGRAM_IMPLICIT = "0000000000"

TIP_INDICATORI = "IND"
TIP_REVIZIE = "REV"
TIP_ORDONANTARE = "ORD"

_MAX_DETALII = 60

_SQL_ANTET = "SELECT CodAngajament, IDDF, Program FROM FX_DDF{filtru}"
_SQL_INDICATORI = (
    "SELECT CodAngajament, SS FROM FX_Indicatori "
    " WHERE SS IS NOT NULL AND SS <> ''{filtru} GROUP BY CodAngajament, SS"
)
_SQL_REVIZII = (
    "SELECT D.CodAngajament, V.IDREV, V.NumarRev, S.SS "
    "  FROM FX_DDF D JOIN FX_DDF_REV V ON V.IDDF = D.IDDF JOIN FX_DDF_REV_SA S ON S.IDREV = V.IDREV "
    " WHERE S.SS IS NOT NULL AND S.SS <> ''{filtru} "
    " GROUP BY D.CodAngajament, V.IDREV, V.NumarRev, S.SS "
    " ORDER BY D.CodAngajament, V.NumarRev"
)
_SQL_ORDONANTARI = (
    "SELECT O.CodAngajament, O.IDORDP, O.NrORD, UPPER(LEFT(TRIM(T.CodSSI), 3)) AS SS "
    "  FROM FX_ORD O JOIN FX_ORD_TBL T ON T.IDORDP = O.IDORDP "
    " WHERE T.CodSSI IS NOT NULL AND TRIM(T.CodSSI) <> ''{filtru} "
    " GROUP BY O.CodAngajament, O.IDORDP, O.NrORD, UPPER(LEFT(TRIM(T.CodSSI), 3)) "
    " ORDER BY O.CodAngajament, O.NrORD"
)


def _json_utf8(payload, status):
    body = json.dumps(payload, ensure_ascii=False, default=str)
    return current_app.response_class(body, status=status, mimetype="application/json")


def program_pentru_surse(surse: Iterable[Optional[str]]) -> Optional[str]:
    """The program of a set of SSs. No SS at all -> the default («altfel»). 01A together with another SS ->
    None: the data span programs, nothing is guessed."""
    unice = {str(s).strip().upper() for s in surse if s is not None and str(s).strip()}
    are_01a = SS_PROGRAM_01A in unice
    if are_01a and len(unice) > 1:
        return None
    return PROGRAM_01A if are_01a else PROGRAM_IMPLICIT


def program_pentru_ss(ss: Optional[str]) -> str:
    """The program of ONE SS (never ambiguous)."""
    return PROGRAM_01A if str(ss or "").strip().upper() == SS_PROGRAM_01A else PROGRAM_IMPLICIT


def aceleasi_programe(a: Optional[str], b: Optional[str]) -> bool:
    """Two program codes are the same program: trimmed, and compared as numbers when both are digits."""
    x = (a or "").strip()
    y = (b or "").strip()
    if x.upper() == y.upper():
        return True
    if not x or not y:
        return False
    if x.isdigit() and y.isdigit():
        return (x.lstrip("0") or "0") == (y.lstrip("0") or "0")
    return False


def program_pentru_angajament(cursor, cod: str) -> str:
    """The program of `cod` from the SS of its indicators; the default when it has none or they span programs."""
    cursor.execute("SELECT DISTINCT SS FROM FX_Indicatori WHERE CodAngajament = %s", (cod,))
    program = program_pentru_surse(r["SS"] if isinstance(r, dict) else r[0] for r in cursor.fetchall())
    return program or PROGRAM_IMPLICIT


def _tabela_exista(cursor, nume: str) -> bool:
    cursor.execute("SELECT COUNT(*) AS n FROM information_schema.TABLES "
                   "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s", (nume,))
    return int((cursor.fetchone() or {}).get("n") or 0) > 0


def _interogheaza(cursor, sql: str, cod: Optional[str], alias: str = "") -> List[dict]:
    filtru = ""
    parametri = ()
    if cod:
        filtru = f" AND {alias}CodAngajament = %s"
        parametri = (cod,)
    cursor.execute(sql.format(filtru=filtru), parametri)
    return cursor.fetchall()


def _analizeaza(cursor, cod: Optional[str]) -> Dict[str, dict]:
    """
    For every angajament with a DDF: the program its header holds, the places whose SS calls for another
    program and the one correct program when there is exactly one. `cod` None = the whole database.
    """
    filtru_antet = " WHERE CodAngajament = %s" if cod else ""
    cursor.execute(_SQL_ANTET.format(filtru=filtru_antet), (cod,) if cod else ())
    rezultat: Dict[str, dict] = {}
    for r in cursor.fetchall():
        rezultat[str(r["CodAngajament"])] = {
            "iddf": int(r["IDDF"]), "actual": (r["Program"] or "").strip(),
            "ind": [], "dovezi": [],   # ind = the indicators' SSs; dovezi = [(tip, numar, ss)]
        }
    if not rezultat:
        return {}

    for r in _interogheaza(cursor, _SQL_INDICATORI, cod):
        a = rezultat.get(str(r["CodAngajament"]))
        if a is not None:
            a["ind"].append(str(r["SS"]).strip().upper())
    for r in _interogheaza(cursor, _SQL_REVIZII, cod, "D."):
        a = rezultat.get(str(r["CodAngajament"]))
        if a is not None:
            a["dovezi"].append((TIP_REVIZIE, str(r["NumarRev"] if r["NumarRev"] is not None else r["IDREV"]),
                                str(r["SS"]).strip().upper()))
    if _tabela_exista(cursor, "FX_ORD") and _tabela_exista(cursor, "FX_ORD_TBL"):
        for r in _interogheaza(cursor, _SQL_ORDONANTARI, cod, "O."):
            a = rezultat.get(str(r["CodAngajament"]))
            if a is not None and r["SS"]:
                a["dovezi"].append((TIP_ORDONANTARE, str(r["NrORD"] if r["NrORD"] is not None else r["IDORDP"]),
                                    str(r["SS"]).strip().upper()))
    return rezultat


def gaseste_programe_gresite(cursor, cod: Optional[str] = None) -> Dict[str, dict]:
    """
    Angajamente whose header program (FX_DDF.Program) differs from the program of the SS of their indicators,
    of the lines of a saved DDF revision or of the lines of a saved ordonantare. `cod` None = the whole
    database (the tree). Returns {CodAngajament: {iddf, actual, asteptat, surse, dovezi}} where `dovezi` is
    [{tip, numar, ss, program}] -- only the places that disagree with the header -- and `asteptat` is the one
    correct program, or None when the data call for more than one. The cursor is a DICTIONARY one.
    """
    gresite: Dict[str, dict] = {}
    for cod_ang, a in _analizeaza(cursor, cod).items():
        indicatori = sorted(set(a["ind"]))
        programe_ind = program_pentru_surse(indicatori)
        locuri = []
        if indicatori:
            locuri.append((TIP_INDICATORI, "", ",".join(indicatori), programe_ind))
        for tip, numar, ss in a["dovezi"]:
            locuri.append((tip, numar, ss, program_pentru_ss(ss)))
        if not locuri:
            continue
        dovezi = []
        for tip, numar, ss, program in locuri:
            # Indicators on 01A together with another SS span programs: that is always a disagreement.
            if program is None:
                dovezi.append({"tip": tip, "numar": numar, "ss": ss, "program": ""})
            elif not aceleasi_programe(a["actual"], program):
                dovezi.append({"tip": tip, "numar": numar, "ss": ss, "program": program})
        if not dovezi:
            continue
        # The correct program: the indicators' when they give one; else the single one all the data agree on.
        asteptat = programe_ind
        if asteptat is None or not indicatori:
            toate = {p for _, _, _, p in locuri if p}
            asteptat = next(iter(toate)) if len(toate) == 1 else None
        gresite[cod_ang] = {"iddf": a["iddf"], "actual": a["actual"], "asteptat": asteptat,
                            "surse": ",".join(indicatori), "dovezi": dovezi}
    return gresite


def sir_pentru_arbore(gresit: Optional[dict]) -> Optional[str]:
    """«actual~asteptat~tip^numar^ss^program|...», None = nothing disagrees. The pieces hold no «~», «|» or «^»."""
    if not gresit:
        return None
    def curat(v):
        return str(v).replace("~", " ").replace("|", " ").replace("^", " ")
    dovezi = "|".join("^".join((curat(d["tip"]), curat(d["numar"]), curat(d["ss"]), curat(d["program"])))
                      for d in gresit["dovezi"][:_MAX_DETALII])
    return "~".join((curat(gresit["actual"]), curat(gresit["asteptat"] or ""), dovezi))


def repara_program(cursor, cod: str, aplica: bool) -> dict:
    """The repair itself; writes only when `aplica`."""
    rez = {"de_reparat": 0, "reparate": 0, "iddf": 0, "actual": "", "asteptat": "", "surse": "",
           "detalii": [], "avertismente": []}
    gresit = gaseste_programe_gresite(cursor, cod).get(cod)
    if gresit is None:
        analiza = _analizeaza(cursor, cod).get(cod)
        if analiza is None:
            rez["avertismente"].append("Angajamentul nu are document de fundamentare.")
        return rez
    rez.update(iddf=gresit["iddf"], actual=gresit["actual"], asteptat=gresit["asteptat"] or "",
               surse=gresit["surse"], detalii=gresit["dovezi"][:_MAX_DETALII])
    if gresit["asteptat"] is None:
        rez["avertismente"].append(
            "Datele angajamentului țin de mai multe programe; nu se alege unul singur, deci nu se repară.")
        return rez
    rez["de_reparat"] = 1
    if not aplica:
        return rez
    cursor.execute("UPDATE FX_DDF SET Program = %s WHERE IDDF = %s", (gresit["asteptat"], gresit["iddf"]))
    rez["reparate"] = 1
    return rez


@forexe_bp.route("/api/forexe/ddf/repara-program", methods=["POST"])
@require_session
def post_repara_program():
    """
    Puts the program of the angajament's DDF header right: the one its indicators', revisions' and ordonantari's
    SS call for. Writes only `FX_DDF.Program`; sums, revisions, files and saved PDFs are not touched.

    Body:   { cod, aplica? } -- `aplica` missing / false = a PROBE (writes nothing).
    Answer: { cod, aplicat, de_reparat, reparate, iddf, actual, asteptat, surse,
              detalii: [ {tip, numar, ss, program} ], avertismente }.
    """
    date_in = request.get_json(silent=True)
    if not isinstance(date_in, dict) or str(date_in.get("cod") or "").strip() == "":
        return _json_utf8({"error": "Câmp lipsă: cod"}, 400)
    cod = str(date_in["cod"]).strip()
    aplica = bool(date_in.get("aplica", False))

    conn = None
    try:
        conn = get_kbot_connection(g.session.db_name)
        cursor = conn.cursor(dictionary=True)
        rezultat = repara_program(cursor, cod, aplica)
        if aplica:
            conn.commit()
        else:
            conn.rollback()
        rezultat["cod"] = cod
        rezultat["aplicat"] = aplica
        logger.info("[forexe.ddf_program] %s: cod=%s aplica=%s -> de_reparat=%s reparate=%s",
                    g.session.db_name, cod, aplica, rezultat["de_reparat"], rezultat["reparate"])
        return _json_utf8(rezultat, 200)
    except Exception as e:
        if conn is not None:
            conn.rollback()
        logger.error(f"[forexe.ddf_program] {e}", exc_info=True)
        return _json_utf8({"error": f"Eroare la repararea programului documentului: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()
