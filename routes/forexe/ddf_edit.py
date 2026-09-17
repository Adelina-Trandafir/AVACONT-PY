# routes/forexe/ddf_edit.py
"""
The WRITE routes of the fundamentation document (slice 0051) -- the port of `frmFX_DDF`,
`frmFX_DDF_REV`, `frmFX_DDF_REV_SECT_A`, `frmFX_DDF_REV_SECT_B`, `frmFX_DDF_ATT` and the
three `mdl_FX_DDF*` modules.

Routes (all `@require_session`; the database comes from the session -- one MariaDB database
is one unit, so there is NO `db_name` / `id_unitate` parameter anywhere):

    POST   /api/forexe/ddf/genereaza                      -> the PROPOSED graph, nothing written
    GET    /api/forexe/ddf/draft/<iddf>/<idrev>           -> an existing revision, for editing
    GET    /api/forexe/ddf/clasificatii                   -> the section-A combo source
    GET    /api/forexe/ddf/parteneri                      -> the header partner combo
    GET    /api/forexe/ddf/comp                           -> the compartment combo
    POST   /api/forexe/ddf/save                           -> the whole graph, one transaction
    DELETE /api/forexe/ddf/rev/<idrev>                    -> one revision
    DELETE /api/forexe/ddf/<iddf>                         -> the whole document
    DELETE /api/forexe/ddf/<iddf>/luna/<an>/<luna>        -> a month's revisions
    GET    /api/forexe/ddf/att/<idrevatt>/imagine         -> attachment bytes
    PUT    /api/forexe/ddf/att/<idrevatt>/imagine         -> replace-or-insert the bytes
    DELETE /api/forexe/ddf/att/<idrevatt>/imagine         -> delete the bytes
    POST   /api/forexe/ddf/numar/rezerva                  -> take a number and lock it
    POST   /api/forexe/ddf/numar/<idlock>/schimba         -> move the lock to another number
    POST   /api/forexe/ddf/numar/<idlock>/prelungeste     -> heartbeat
    DELETE /api/forexe/ddf/numar/<idlock>                 -> release

`routes/forexe/ddf.py` (the read view of slice 0020) is NOT modified, and `routes/ddf/*`
(the legacy Access/VBA client on X-Api-Key) is not touched at all -- the two systems overlap
for as long as Access still runs. Decision D19; verified with `git diff`.

=========================================================================================
WHAT THIS REPLACES
=========================================================================================
The five-step VBA chain -- `Curatare_Staging` -> `Adauga_DDF_REV` / `Modifica_DDF_REV` ->
`SalveazaLocal(NoCommit:=True)` -> `Confirma_DDF` -> `Confirma_Local` ->
`SalveazaLocal(NoCommit:=False)` -- is NOT ported, and neither are the `stg_DocFund`,
`stg_Revizii`, `stg_RevA`, `stg_RevB`, `stg_Att` tables. One POST, one transaction here, and
the real keys go back to the client. With the chain goes its worst failure mode, the one
Access announced as "the data is on the server but not local".

The six `tmpFX_DDF*` tables have no successor either: the draft lives in client memory.

=========================================================================================
THE TRAPS OF THE FX_DDF FAMILY (read from the DDL, not deduced from names)
=========================================================================================
1. `FX_DDF`'s primary key is the COMPOSITE `(IDDF, CUAL)`, and nothing constrains
   `CodAngajament` to be unique. NEVER join on `IDDF` alone -- one `IDDF` carrying two
   `CUAL` rows fans every revision out. Every read filters `IDDF IN (SELECT ...)`. Same
   lesson as slice 0011-03, applied in 0020-01.

2. THE CLASSIFICATION KEY POINTS BOTH WAYS, on different tables:
     - `FX_DDF_REV_SA.IdClsf` / `_SB.IdClsf` = the MariaDB key `Clasificatii.IDClsf`
       (confirmed by the foreign keys `FX_DDF_REV_SA_ibfk_4` and `tblDocFund_SB_ibfk_4`);
     - `FX_Indicatori.IdClsf`, `FX_Rezervari.IdClsf`, `FX_Receptii.IdClsf` = the ACCESS id,
       which matches `Clasificatii.IdClsfAcc`. None of those three has a foreign key, which
       is the tell.
   So Access's join `Clasificatii.IDClsf = FX_Indicatori.IdClsf` -- where both sides were
   Access ids -- must be TRANSLATED here to
   `C.IdClsfAcc = I.IdClsf AND C.IdUnitate = I.IdUnitate`, and the value written into
   `FX_DDF_REV_SA.IdClsf` is `C.IDClsf`. Copying the Access join literally returns zero rows.

3. `IdClsfAcc` is `NOT NULL` on both `_SA` and `_SB`. The client never sends it; the server
   resolves it from `Clasificatii`.

4. `Clasificatii` has NO `CodSSI` column, though Access did. It has `SS` (Sector+Sursa) and
   `ClsfSal`, both `GENERATED ... PERSISTENT`, so `CodSSI = CONCAT(SS, ClsfSal)`. Verified
   against stored rows: '02A' + '650402200103' = '02A650402200103'.

5. `FX_DDF.CUAL` is `int(11) NOT NULL` -- a NUMBER. `FX_ORD.CUAL` is a `varchar(255)`. The
   two families disagree and the code must not assume otherwise.

6. TWO COLUMN NAMES ARE MISSPELLED IN THE DDL: `FX_DDF_REV.DataAdugare` and
   `FX_DDF_REV_ATT.DataAdugare` (not "DataAdaugare"). Both default to `current_timestamp()`,
   so nothing here writes them. Do not "fix" the spelling and do not let a typo in our SQL
   pass for the schema's typo.

7. `FX_DDF.Comp` and `FX_DDF.ObiectDDF` are `NOT NULL`. A new document cannot be inserted
   without a compartment -- see the note on `/comp` below.

8. `FX_Angajamente.Descriere` is `varchar(255)` while `FX_DDF.ObiectDDF` is `varchar(500)`.
   The cascade of decision D10 therefore has to truncate, and it says so rather than letting
   MariaDB do it quietly.

=========================================================================================
WHAT IS DELIBERATELY NOT PORTED
=========================================================================================
- the staging chain and the `stg_*` tables (decision D1);
- `frmFX_DDF_PRTSCR` / `FX_DDF_REV_PRT` (decision D17, retired);
- the salaries branch: `frmFX_DDF_STATE`, `IdSalariiS`, `tmpFX_Salarii` and the post-save
  `SalariiH` updates (decision D14). `FX_DDF.Salarii` rides along as a plain flag;
  `FX_DDF.IdSalarii` is not written;
- `Save_ParteneriAng` / `qFX_Ang_Part` (decision D15) -- it wrote to `ParteneriAng IN A`, an
  external Access database with no MariaDB counterpart;
- `FX_DDF_REV_ATT.IDVBNET` (decision D11) and `.DateFisier` (decision D12: the bytes live in
  `FX_DDF_REV_ATT_IMG`, see sql/0051_ddf_rev_att_img.sql);
- `ModNume` (decision D10): the `FX_Angajamente.Descriere` cascade is unconditional now.

`Desc_Lunga_ANSI` IS still written, reversing the plan's decision D9 on the operator's
instruction. Two reasons, both load-bearing: the frozen read route of slice 0020 serves that
column as the wire field `desc_lunga`, and `DdfXmlBuilder` puts that value into the signed
XFA node `DescrieObFundRevizuireLung`. It is the PLAIN-TEXT rendition; `Desc_Lunga` is the
RTF one, and the XFA cannot take RTF.
"""
import hashlib
import json
import logging
from datetime import date, datetime, timedelta

from flask import request, g, current_app

from routes.auth.guard import require_session
from utils.database import get_kbot_connection

from . import forexe_bp

logger = logging.getLogger(__name__)


# =========================================================================================
# Constants
# =========================================================================================

# The `Program` combo's row source is a literal two-item list in Access
# (`frmFX_DDF.Program.RowSource` = "0000000000;0000002510"). Kept as a named constant so it
# is one visible place rather than a magic pair scattered through the code.
PROGRAME = ("0000000000", "0000002510")

# The synthetic separator row of `qFX_DDF_SA_CLSF`, part two of its three-part UNION ALL.
# Picking it is refused, both here and on the client (`cmbClsf_BeforeUpdate`).
CLSF_SEPARATOR_ID = -1
CLSF_SEPARATOR_CLSF = "============="
CLSF_SEPARATOR_DENUMIRE = "=== ADAUGA CLASIFICATIE ==="

# How long a number lock lives without a heartbeat, and how far a heartbeat pushes it out.
# Sixty minutes because the form is modal and an operator can sit in section A for a long
# time; the client renews every five, so a lock only ever expires after a crash.
LOCK_TTL_MINUTE = 60

LOCK_TIP_CUAL = "CUAL"
LOCK_TIP_NUMARREV = "NUMARREV"
LOCK_TIPURI = (LOCK_TIP_CUAL, LOCK_TIP_NUMARREV)

# `FX_Angajamente.Descriere` is varchar(255); `FX_DDF.ObiectDDF` is varchar(500).
LUNGIME_DESCRIERE_ANGAJAMENT = 255

# The practical ceiling for one attachment. The column is LONGBLOB (it does not stop here),
# but past this we answer 413 with a message that SAYS the limit. nginx cuts at 20 MB.
MAX_FISIER_BYTES = 16 * 1024 * 1024

# Integrity / concurrency headers, identical to routes/forexe/pdf.py and ord_edit.py.
H_SHA = "X-Sha256"
H_SHA_PREC = "X-Sha-Precedent"
H_NUME = "X-Nume-Fisier"
NO_ROW = "-"

# File type signatures, following the `bChoose_Click` filters of `frmFX_DDF_ATT`:
# images "*.bmp;*.jpg;*.png;*.ico", documents "*.doc;*.docx;*.pdf", sheets "*.xls;*.xlsx".
# The DDF attachment list is wider than the ORD one, which took images only.
SEMNATURI_FISIER = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"BM", "image/bmp"),
    (b"\x00\x00\x01\x00", "image/x-icon"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"%PDF-", "application/pdf"),
    # ZIP container -> the OOXML formats (.docx / .xlsx). Which of the two cannot be told
    # from the first bytes, so the extension decides; see `_tip_fisier`.
    (b"PK\x03\x04", "application/zip"),
    # OLE2 compound file -> the old .doc / .xls.
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "application/x-ole-storage"),
)

EXTENSII_OOXML = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
EXTENSII_OLE = {
    ".doc": "application/msword",
    ".xls": "application/vnd.ms-excel",
}


class DateInvalide(Exception):
    """The payload is refused before any write. The message is already in Romanian."""


# =========================================================================================
# Response helpers
# =========================================================================================
def _json_utf8(payload, status):
    """A JSON response with LITERAL diacritics (ensure_ascii=False)."""
    body = json.dumps(payload, ensure_ascii=False, default=_serializeaza)
    return current_app.response_class(body, status=status, mimetype="application/json")


def _serializeaza(v):
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return str(v)


def _iso_zi(v):
    """DateTime/Date -> 'YYYY-MM-DD', or None."""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    return str(v)


def _iso_dt(v):
    """DateTime -> ISO with the time, or None."""
    if v is None:
        return None
    return v.isoformat() if hasattr(v, "isoformat") else str(v)


def _num(v):
    """A money column -> float; None becomes 0.0 so the grid shows "0,00"."""
    return float(v) if v is not None else 0.0


def _int0(v):
    """A key column -> int; None becomes 0 (the client's "no key yet")."""
    if v is None:
        return 0
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _int_or_none(v):
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _txt(v):
    """A value -> cleaned text, or '' (never None in the text fields of a response)."""
    return "" if v is None else str(v)


def _bool(v):
    return bool(v) if v is not None else False


def _sha256(data: bytes) -> str:
    """Lowercase hex SHA-256 over the bytes -- the same format as on the client."""
    return hashlib.sha256(data).hexdigest()


def _zi_ceruta(brut, nume_camp: str):
    """'YYYY-MM-DD' (or ISO with a time) -> date; None when the field is absent.

    Raises `DateInvalide` with a Romanian message for anything else -- a date that could not
    be understood is NEVER quietly replaced with today.
    """
    if brut is None or (isinstance(brut, str) and not brut.strip()):
        return None
    if isinstance(brut, date) and not isinstance(brut, datetime):
        return brut
    if isinstance(brut, datetime):
        return brut.date()
    try:
        text = str(brut).strip()
        if "T" in text:
            text = text.split("T", 1)[0]
        return datetime.strptime(text[:10], "%Y-%m-%d").date()
    except Exception:
        raise DateInvalide(f"Câmpul «{nume_camp}» nu conține o dată validă: «{brut}».")


def _lista(sarcina: dict, cheie: str) -> list:
    """A list field of the payload, defended: anything that is not a list is refused."""
    valoare = sarcina.get(cheie)
    if valoare is None:
        return []
    if not isinstance(valoare, list):
        raise DateInvalide(f"Câmpul «{cheie}» trebuie să fie o listă.")
    return [v for v in valoare if isinstance(v, dict)]


# =========================================================================================
# Shared reads
# =========================================================================================

# The header of an existing document. `IDDF IN (SELECT ...)` is not used here because the
# lookup is by `CodAngajament` already; what matters is that `LIMIT 1` is NOT enough on its
# own when a document has two CUAL rows, so the caller is told when that happens.
_SQL_DDF_DUPA_COD = (
    "SELECT IDDF, CUAL, CodAngajament, Comp, Salarii, DataCreare, DC, Program, DataDef, "
    "       Incarcat, Preluat, Buget, Manual, ObiectDDF, Stare, PartAng, CodFiscal, "
    "       NumePartener "
    "  FROM FX_DDF WHERE CodAngajament = %s ORDER BY IDDF, CUAL"
)

_SQL_ANGAJAMENT = (
    "SELECT CodAngajament, IDDF, DataCreare, DataDefinitivare, Descriere, Stare, DC, "
    "       Incarcat, Preluat, Salarii "
    "  FROM FX_Angajamente WHERE CodAngajament = %s"
)


def _citeste_angajament(cursor, cod: str) -> dict:
    cursor.execute(_SQL_ANGAJAMENT, (cod,))
    rand = cursor.fetchone()
    if rand is None:
        raise DateInvalide(f"Angajamentul «{cod}» nu există.")
    return rand


def _citeste_ddf(cursor, cod: str):
    """The document's header, or None. Raises when a code carries several documents."""
    cursor.execute(_SQL_DDF_DUPA_COD, (cod,))
    randuri = cursor.fetchall()
    if not randuri:
        return None
    # Several CUAL rows under one IDDF are legal (composite key); several IDDF values under
    # one code are not something this editor can represent, so it says so instead of
    # silently taking the first.
    iddf_uri = {int(r["IDDF"]) for r in randuri}
    if len(iddf_uri) > 1:
        raise DateInvalide(
            f"Angajamentul «{cod}» are {len(iddf_uri)} documente de fundamentare distincte "
            "în baza de date. Editorul nu poate alege între ele; corectați datele mai întâi."
        )
    return randuri[0]


def _antet_din_ddf(rand: dict, nou: bool) -> dict:
    return {
        "iddf": _int0(rand.get("IDDF")),
        "cual": _int0(rand.get("CUAL")),
        "cod_angajament": _txt(rand.get("CodAngajament")),
        "comp": _txt(rand.get("Comp")),
        "salarii": _bool(rand.get("Salarii")),
        "data_creare": _iso_zi(rand.get("DataCreare")),
        "dc": _txt(rand.get("DC")),
        "program": _txt(rand.get("Program")),
        "data_def": _iso_zi(rand.get("DataDef")),
        "incarcat": _bool(rand.get("Incarcat")),
        "preluat": _bool(rand.get("Preluat")),
        "buget": _bool(rand.get("Buget")),
        "manual": _bool(rand.get("Manual")),
        "obiect_ddf": _txt(rand.get("ObiectDDF")),
        "stare": _txt(rand.get("Stare")),
        "part_ang": _bool(rand.get("PartAng")),
        "cod_fiscal": _txt(rand.get("CodFiscal")),
        "nume_partener": _txt(rand.get("NumePartener")),
        "nou": nou,
    }


# =========================================================================================
# GENERATION -- the port of `Genereaza_DDF_Buget` (mdl_FX_DDF.FX_Adaugare_DDF)
# =========================================================================================

# The port of `QFX_DDF_REZERVARI`. Five translations, each deliberate:
#
#   `Forms!frmFX_MAIN!CodAngajament` -> the `cod` parameter, PARAMETERISED, never interpolated.
#   `ConcatRelated("IDRZ", ...)`     -> GROUP_CONCAT(DISTINCT ...), carried as `grp_idrz`;
#                                       it feeds the post-save `FX_Rezervari` update.
#   `ClasificatiiG` (a union across  -> `Clasificatii`, but the `IdUnitate` predicate STAYS.
#   units)                              The standing rule is that shared nomenclatoare keep
#                                       it: `Clasificatii` holds several units per database
#                                       (eight on 000_DEMO), and 0011-03 measured the cost of
#                                       dropping it -- 67 rows where 25 were expected.
#   `Clasificatii.IdClsf`            -> see trap 2 in the module docstring. The JOIN goes
#                                       through `IdClsfAcc`; the VALUE emitted is `IDClsf`.
#   `Clasificatii.CodSSI`            -> CONCAT(SS, ClsfSal); the column does not exist here.
#
# The WHERE clause carries a row-selection rule that is easy to miss and changes the result
# completely: only the EARLIEST un-DDF'd reservation date, and within that date only the
# LOWEST operation type (Initiala = 1, Marire = 2, Micsorare = 3). Without it, one generated
# revision would sweep up rows from several dates and several operations at once.
_SQL_GEN_REZERVARI = (
    "SELECT "
    "  GROUP_CONCAT(DISTINCT R.IDRZ) AS grp_idrz, "
    "  CASE WHEN R.EInitiala THEN 'Initiala' "
    "       WHEN R.EMarire   THEN 'Marire' "
    "       ELSE 'Micsorare' END                       AS TipOperatie, "
    "  C.IdUnitate, C.Clsf, C.IDClsf, C.IdClsfAcc, C.SS, C.Denumire, "
    "  CONCAT(C.SS, C.ClsfSal)                         AS CodSSI, "
    "  R.DataRezervare, R.CodAI, R.CodAngajament, R.CodIndicator, "
    "  R.R_CreditBug                                   AS Buget, "
    "  COALESCE(P.RezPrec, 0)                          AS ValPrec, "
    "  SUM(CASE WHEN R.EInitiala THEN R.R_Initiala ELSE R.R_Valoare END) AS Suma "
    "FROM FX_Rezervari R "
    "JOIN FX_Indicatori I ON I.CodAI = R.CodAI "
    "JOIN Clasificatii  C ON C.IdClsfAcc = I.IdClsf AND C.IdUnitate = I.IdUnitate "
    "LEFT JOIN (SELECT IdClsf, SUM(ValCur) AS RezPrec "
    "             FROM FX_DDF_REV_SA WHERE CodAngajament = %s GROUP BY IdClsf) P "
    "       ON P.IdClsf = C.IDClsf "
    "WHERE R.AreDDF = 0 AND R.CodAngajament = %s "
    "  AND R.DataRezervare = (SELECT MIN(DataRezervare) FROM FX_Rezervari "
    "                          WHERE AreDDF = 0 AND CodAngajament = %s) "
    "  AND (CASE WHEN R.EInitiala THEN 1 WHEN R.EMarire THEN 2 ELSE 3 END) = "
    "      (SELECT MIN(CASE WHEN EInitiala THEN 1 WHEN EMarire THEN 2 ELSE 3 END) "
    "         FROM FX_Rezervari "
    "        WHERE AreDDF = 0 AND CodAngajament = %s "
    "          AND DataRezervare = (SELECT MIN(DataRezervare) FROM FX_Rezervari "
    "                                 WHERE AreDDF = 0 AND CodAngajament = %s)) "
    "GROUP BY C.IdUnitate, C.Clsf, C.IDClsf, C.IdClsfAcc, C.SS, C.ClsfSal, C.Denumire, "
    "         R.DataRezervare, R.CodAI, R.CodAngajament, R.CodIndicator, R.R_CreditBug, "
    "         P.RezPrec, R.EInitiala, R.EMarire, R.EMicsorare "
    "HAVING SUM(CASE WHEN R.EInitiala THEN R.R_Initiala ELSE R.R_Valoare END) <> 0 "
    "ORDER BY C.Clsf"
)

# The port of `qFX_DDF_INDICATORI`. Same key translation; `grp_idrz` is empty because no
# reservation produced these rows, which is exactly what stops the post-save
# `FX_Rezervari` update from touching anything.
_SQL_GEN_INDICATORI = (
    "SELECT "
    "  ''                                              AS grp_idrz, "
    "  'Initiala'                                      AS TipOperatie, "
    "  C.IdUnitate, C.Clsf, C.IDClsf, C.IdClsfAcc, C.SS, "
    "  A.Descriere                                     AS Denumire, "
    "  CONCAT(C.SS, C.ClsfSal)                         AS CodSSI, "
    "  A.DataCreare                                    AS DataRezervare, "
    "  H.CodAI, H.CodAngajament, H.CodIndicator, "
    "  I.Prevedere_Bugetara_Initiala                   AS Buget, "
    "  0                                               AS ValPrec, "
    "  H.Val_Rezervare_I                               AS Suma "
    "FROM FX_Istoric H "
    "JOIN FX_Angajamente A ON A.CodAngajament = H.CodAngajament "
    "JOIN FX_Indicatori  I ON I.CodAI = H.CodAI "
    "JOIN Clasificatii   C ON C.IdClsfAcc = I.IdClsf AND C.IdUnitate = I.IdUnitate "
    "WHERE H.CodAngajament = %s AND H.TipRand = 'Rez_Initiala' "
    "ORDER BY C.Clsf"
)

# Does the angajament have un-DDF'd reservations? First half of decision D5.
_SQL_ARE_REZERVARI = (
    "SELECT COUNT(*) AS n FROM FX_Rezervari WHERE CodAngajament = %s AND AreDDF = 0"
)

# Does it have initial-reservation history rows? Second half of decision D5.
_SQL_ARE_ISTORIC = (
    "SELECT COUNT(*) AS n FROM FX_Istoric "
    " WHERE CodAngajament = %s AND TipRand = 'Rez_Initiala'"
)

# Receptions BEFORE the revision date, per indicator -- the port of the ad-hoc QueryDef in
# `FX_Adaugare_DDF`. Note this is a DIFFERENT rule from the one in `cmbClsf_AfterUpdate`,
# which sums ALL receptions for a classification; both are ported, each where it belongs.
_SQL_RECEPTII_PANA_LA = (
    "SELECT CodIndicator, SUM(Valoare) AS TotalReceptii "
    "  FROM FX_Receptii "
    " WHERE CodAngajament = %s AND DATE(Data) < %s "
    " GROUP BY CodIndicator"
)

_SQL_MAX_NUMAR_REV = (
    "SELECT MAX(NumarRev) AS m FROM FX_DDF_REV WHERE CodAngajament = %s AND DC = %s"
)


def _alege_sursa(cursor, cod: str) -> str:
    """Decision D5 -- the SERVER picks the line source, from the data, never the caller.

    Un-DDF'd `FX_Rezervari` rows exist -> the reservations query. Otherwise `FX_Istoric`
    rows with `TipRand = 'Rez_Initiala'` -> the indicators query. Otherwise REFUSE LOUDLY,
    naming both conditions. An empty graph is never returned.

    This differs from Access, which chose on the angajament's STATE (`dinIndicatori` when it
    was "Initial" and not manual). The plan locks the data-driven rule instead, because a
    state string is a weaker signal than the rows themselves; the difference is recorded in
    the worklog.
    """
    cursor.execute(_SQL_ARE_REZERVARI, (cod,))
    if int((cursor.fetchone() or {}).get("n") or 0) > 0:
        return "rezervari"

    cursor.execute(_SQL_ARE_ISTORIC, (cod,))
    if int((cursor.fetchone() or {}).get("n") or 0) > 0:
        return "istoric"

    raise DateInvalide(
        f"Nu pot genera documentul de fundamentare pentru «{cod}»: angajamentul nu are nici "
        "rezervări fără DDF (FX_Rezervari cu AreDDF = 0), nici rânduri de rezervare inițială "
        "în istoric (FX_Istoric cu TipRand = «Rez_Initiala»). Descărcați întâi datele din "
        "FOREXE."
    )


def _construieste_linii(randuri: list, cod: str, receptii: dict) -> tuple:
    """The section-A and section-B lines of the proposal, from the source query's rows.

    Ported from the loop at the foot of `FX_Adaugare_DDF`. Section B is derived here as well
    as on the client: the client recomputes it on every edit (decision D8), and the server
    seeds it so a proposal that is saved untouched is already correct.
    """
    linii_a = []
    linii_b = []
    for i, r in enumerate(randuri):
        temp_id = -(i + 1)
        val_prec = _num(r.get("ValPrec"))
        val_cur = _num(r.get("Suma"))
        val_tot = round(val_prec + val_cur, 2)
        cod_ind = _txt(r.get("CodIndicator"))

        linii_a.append({
            "temp_id": temp_id,
            "id_sec_a": 0,
            "cod_angajament": cod,
            "cod_indicator": cod_ind,
            "id_clsf": _int0(r.get("IDClsf")),
            "id_clsf_acc": _int0(r.get("IdClsfAcc")),
            "clsf": _txt(r.get("Clsf")),
            "ss": _txt(r.get("SS")),
            # The unit comes from the DATA (FX_Indicatori.IdUnitate through Clasificatii),
            # never from the session -- the session carries no unit id (id_unitate is 0 for
            # every session and nothing reads it).
            "id_unitate": _int0(r.get("IdUnitate")),
            "element_fund": _txt(r.get("Denumire")),
            "parametrii_fund": "",
            "cod_partener": "",
            "id_partener": 0,
            "part_ind": False,
            "val_prec": val_prec,
            "val_cur": val_cur,
            "val_tot": val_tot,
            "ramane": 0.0,
            # Display only -- no column on FX_DDF_REV_SA. Dropped at the wire on the way back.
            "buget": _num(r.get("Buget")),
            "val_rec": _num(receptii.get(cod_ind, 0.0)),
            "grp_idrz": _txt(r.get("grp_idrz")),
        })

        linii_b.append({
            "temp_id": temp_id,
            "id_sec_b": 0,
            "cod_angajament": cod,
            "cod_indicator": cod_ind,
            "id_clsf": _int0(r.get("IDClsf")),
            "id_clsf_acc": _int0(r.get("IdClsfAcc")),
            "cod_ssi": _txt(r.get("CodSSI")),
            "ss": _txt(r.get("SS")),
            "id_unitate": _int0(r.get("IdUnitate")),
            "cod_partener": "",
            "id_partener": 0,
            "ca_anterior": val_prec,
            "inf1": val_cur,
            "ca_curent": val_tot,
            "cb_anterior": val_prec,
            "inf2": val_cur,
            "cb_curent": val_tot,
        })

    return linii_a, linii_b


@forexe_bp.route("/api/forexe/ddf/genereaza", methods=["POST"])
@require_session
def post_ddf_genereaza():
    """The proposed graph. NOTHING IS WRITTEN -- not even the numbers, which the lock holds.

    Body: {"cod": "<CodAngajament>", "rev0": true|false}

    `rev0` says which of the two Access branches produced the call -- the initial revision
    (`ADD_DDF0`) or a subsequent one (`ADD_DDF1`). It selects the HEADER treatment, not the
    line source: the line source is decision D5's, taken from the data by `_alege_sursa`.
    """
    sarcina = request.get_json(silent=True)
    if not isinstance(sarcina, dict):
        return _json_utf8({"error": "Corp JSON lipsă sau nevalid."}, 400)

    cod = _txt(sarcina.get("cod")).strip()
    if not cod:
        return _json_utf8({"error": "Câmpul «cod» (codul angajamentului) lipsește."}, 400)
    rev0 = bool(sarcina.get("rev0"))

    db_name = g.session.db_name
    conn = None
    try:
        conn = get_kbot_connection(db_name)
        cursor = conn.cursor(dictionary=True)

        angajament = _citeste_angajament(cursor, cod)
        dc = _txt(angajament.get("DC"))
        stare = _txt(angajament.get("Stare"))
        avertismente = []

        # ---- the header -------------------------------------------------------------
        if rev0:
            # No FX_DDF row exists yet. Built from FX_Angajamente, exactly as the `Else`
            # branch of `Genereaza_DDF_Buget` does. CUAL is NOT allocated here -- the number
            # lock does that, so what the operator sees in the header is genuinely held.
            existent = _citeste_ddf(cursor, cod)
            if existent is not None:
                raise DateInvalide(
                    f"Angajamentul «{cod}» are deja un document de fundamentare "
                    f"(IDDF {int(existent['IDDF'])}). Adăugați o revizie nouă, nu un document nou."
                )
            antet = {
                "iddf": 0,
                "cual": 0,
                "cod_angajament": cod,
                # NOT NULL in the table and there is no compartment nomenclator in MariaDB
                # (Access read `Oper`, a linked table that did not migrate). The operator
                # picks it from previous documents or types a new one; see `/comp`.
                "comp": "",
                "salarii": _bool(angajament.get("Salarii")),
                "data_creare": _iso_zi(angajament.get("DataCreare")),
                "dc": dc,
                "program": PROGRAME[0],
                "data_def": _iso_zi(angajament.get("DataDefinitivare")),
                "incarcat": True,
                "preluat": True,
                "buget": True,
                "manual": cod.startswith("!"),
                "obiect_ddf": _txt(angajament.get("Descriere")),
                "stare": stare,
                "part_ang": False,
                "cod_fiscal": "",
                "nume_partener": "",
                "nou": True,
            }
        else:
            existent = _citeste_ddf(cursor, cod)
            if existent is None:
                raise DateInvalide(
                    f"Angajamentul «{cod}» nu are încă un document de fundamentare. "
                    "Generați întâi revizia inițială."
                )
            antet = _antet_din_ddf(existent, nou=False)
            antet["incarcat"] = True
            antet["preluat"] = True
            if not antet["part_ang"]:
                # Access carries CodFiscal / NumePartener forward only when PartAng is set.
                antet["cod_fiscal"] = ""
                antet["nume_partener"] = ""

        # ---- the lines ---------------------------------------------------------------
        sursa = _alege_sursa(cursor, cod)
        if sursa == "rezervari":
            cursor.execute(_SQL_GEN_REZERVARI, (cod, cod, cod, cod, cod))
        else:
            cursor.execute(_SQL_GEN_INDICATORI, (cod,))
        randuri = cursor.fetchall()

        if not randuri:
            # `_alege_sursa` said there were rows, so an empty result means the joins found
            # no classification for them. Loud, and it names the likely cause.
            raise DateInvalide(
                f"Sursa «{sursa}» are rânduri pentru «{cod}», dar niciunul nu s-a putut lega "
                "de nomenclatorul de clasificații (Clasificatii.IdClsfAcc + IdUnitate). "
                "Verificați clasificațiile indicatorilor înainte de a genera documentul."
            )

        # ---- the revision header ------------------------------------------------------
        cursor.execute(_SQL_MAX_NUMAR_REV, (cod, dc))
        maxim = (cursor.fetchone() or {}).get("m")
        # Access uses `Nz(DMax(...), -1) + 1`, so THE INITIAL REVISION IS NUMBER 0.
        numar_rev_propus = (int(maxim) + 1) if maxim is not None else 0

        if rev0:
            data_rev = _iso_zi(angajament.get("DataCreare"))
            descriere = _txt(angajament.get("Descriere"))
        else:
            data_rev = _iso_zi(randuri[0].get("DataRezervare"))
            descriere = _txt(randuri[0].get("TipOperatie"))

        # `ValRec` at generation time is the sum of receptions STRICTLY BEFORE the revision
        # date -- not all of them. The dictionary is keyed by CodIndicator, as in Access.
        receptii = {}
        if data_rev:
            cursor.execute(_SQL_RECEPTII_PANA_LA, (cod, data_rev))
            for r in cursor.fetchall():
                receptii[_txt(r.get("CodIndicator"))] = _num(r.get("TotalReceptii"))

        linii_a, linii_b = _construieste_linii(randuri, cod, receptii)

        revizie = {
            "idrev": 0,
            "iddf": antet["iddf"],
            "cod_angajament": cod,
            # Reported, not allocated: the lock is what actually takes it.
            "numar_rev": numar_rev_propus,
            "data_rev": data_rev,
            "tip": stare,
            "desc_scurta": descriere,
            "desc_lunga": descriere,
            "desc_lunga_ansi": descriere,
            "incarcat": False,
            "preluat": bool(randuri) and ("derulare" in stare.lower()),
            "noua": True,
        }

        if antet["manual"]:
            avertismente.append(
                "Angajamentul este creat manual. După salvare trebuie încărcat în FOREXEBUG "
                "printr-un flux separat, care nu există încă."
            )
        if len(antet["obiect_ddf"]) > LUNGIME_DESCRIERE_ANGAJAMENT:
            avertismente.append(
                f"Obiectul documentului are {len(antet['obiect_ddf'])} caractere. La salvare, "
                f"descrierea angajamentului se scurtează la {LUNGIME_DESCRIERE_ANGAJAMENT}."
            )

        raspuns = {
            "antet": antet,
            "revizie": revizie,
            "linii_a": linii_a,
            "linii_b": linii_b,
            "atasamente": [],
            "avertismente": avertismente,
            "sursa": sursa,
        }
        logger.info("[forexe.ddf_edit] %s: genereaza cod=%s rev0=%s sursa=%s linii=%s",
                    db_name, cod, rev0, sursa, len(linii_a))
        return _json_utf8(raspuns, 200)
    except DateInvalide as e:
        return _json_utf8({"error": str(e)}, 400)
    except Exception as e:
        logger.error(f"[forexe.ddf_edit] genereaza: {e}", exc_info=True)
        return _json_utf8({"error": f"Eroare la generarea documentului: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()


# =========================================================================================
# THE EXISTING DRAFT -- the equivalent of `FX_Modificare_DDF`
# =========================================================================================

# `IDDF IN (SELECT ...)` and not a join on the header: see trap 1. The revision is pinned by
# its own primary key, so the filter is belt and braces -- it makes a mismatched
# (iddf, idrev) pair return nothing instead of the wrong revision.
_SQL_DRAFT_REV = (
    "SELECT IDREV, IDDF, CodAngajament, NumarRev, DataRev, Tip, Desc_Scurta, Desc_Lunga, "
    "       Desc_Lunga_ANSI, Incarcat, Preluat "
    "  FROM FX_DDF_REV "
    " WHERE IDREV = %s "
    "   AND IDDF IN (SELECT IDDF FROM FX_DDF WHERE IDDF = %s)"
)

_SQL_DRAFT_SA = (
    "SELECT IdSecA, IDDF, IDREV, CodAngajament, CodIndicator, CodPartener, IdPartener, "
    "       IdClsfAcc, IdClsf, Clsf, ElementFund, ParametriiFund, ValPrec, ValCur, ValTot, "
    "       PartInd, Ramane, IdUnitate, SS "
    "  FROM FX_DDF_REV_SA WHERE IDREV = %s ORDER BY Clsf, IdSecA"
)

_SQL_DRAFT_SB = (
    "SELECT IdSecB, IDDF, IDREV, CodAngajament, CodIndicator, CodPartener, IdPartener, "
    "       IdClsfAcc, IdClsf, CodSSI, CA_Anterior, Inf1, CA_Curent, CB_Anterior, Inf2, "
    "       CB_Curent, IdUnitate, SS "
    "  FROM FX_DDF_REV_SB WHERE IDREV = %s ORDER BY IdSecB"
)

# The attachment rows WITHOUT the bytes. `FX_DDF_REV_ATT` has no `NumeFisier` column, so the
# name comes from the blob table; a row with no bytes yet falls back to its `CaleFisier`.
# LEFT JOIN and not a subquery because `FX_DDF_REV_ATT_IMG.IdRevAtt` is UNIQUE -> at most one
# row per parent, so fan-out is impossible (the same justification as FX_ORD_PART in ord.py).
_SQL_DRAFT_ATT = (
    "SELECT A.IdRevAtt, A.IDDF, A.IDREV, A.CaleFisier, A.PrtScr, "
    "       M.NumeFisier, M.TipMime, M.Dimensiune, M.Sha256 "
    "  FROM FX_DDF_REV_ATT A "
    "  LEFT JOIN FX_DDF_REV_ATT_IMG M ON M.IdRevAtt = A.IdRevAtt "
    " WHERE A.IDREV = %s ORDER BY A.IdRevAtt"
)

# Is `FX_DDF_REV_ATT_IMG` present on this database? sql/0051_ddf_rev_att_img.sql has to be
# run per unit, and a database where it has not been run must still open the editor rather
# than fail with an SQL error nobody can read. Probed ONCE per database.
_SQL_ARE_ATT_IMG = (
    "SELECT COUNT(*) AS n FROM information_schema.TABLES "
    " WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'FX_DDF_REV_ATT_IMG'"
)

_ATT_IMG_PREZENT = {}


def _are_att_img(cursor, db_name: str) -> bool:
    """Does this database have the blob table? Probed once per database, then remembered."""
    if db_name in _ATT_IMG_PREZENT:
        return _ATT_IMG_PREZENT[db_name]
    try:
        cursor.execute(_SQL_ARE_ATT_IMG, (db_name,))
        prezent = int((cursor.fetchone() or {}).get("n") or 0) > 0
    except Exception:
        logger.warning("[forexe.ddf_edit] %s: proba FX_DDF_REV_ATT_IMG a esuat; "
                       "se presupune ca lipseste", db_name, exc_info=True)
        prezent = False
    _ATT_IMG_PREZENT[db_name] = prezent
    return prezent


def _linie_a_din_rand(r: dict) -> dict:
    return {
        "temp_id": 0,
        "id_sec_a": _int0(r.get("IdSecA")),
        "cod_angajament": _txt(r.get("CodAngajament")),
        "cod_indicator": _txt(r.get("CodIndicator")),
        "id_clsf": _int0(r.get("IdClsf")),
        "id_clsf_acc": _int0(r.get("IdClsfAcc")),
        "clsf": _txt(r.get("Clsf")),
        "ss": _txt(r.get("SS")),
        "id_unitate": _int0(r.get("IdUnitate")),
        "element_fund": _txt(r.get("ElementFund")),
        "parametrii_fund": _txt(r.get("ParametriiFund")),
        "cod_partener": _txt(r.get("CodPartener")),
        "id_partener": _int0(r.get("IdPartener")),
        "part_ind": _bool(r.get("PartInd")),
        "val_prec": _num(r.get("ValPrec")),
        "val_cur": _num(r.get("ValCur")),
        "val_tot": _num(r.get("ValTot")),
        "ramane": _num(r.get("Ramane")),
        # Not stored anywhere -- filled in below from FX_Receptii, display only.
        "buget": 0.0,
        "val_rec": 0.0,
        "grp_idrz": "",
    }


def _linie_b_din_rand(r: dict) -> dict:
    return {
        "temp_id": 0,
        "id_sec_b": _int0(r.get("IdSecB")),
        "cod_angajament": _txt(r.get("CodAngajament")),
        "cod_indicator": _txt(r.get("CodIndicator")),
        "id_clsf": _int0(r.get("IdClsf")),
        "id_clsf_acc": _int0(r.get("IdClsfAcc")),
        "cod_ssi": _txt(r.get("CodSSI")),
        "ss": _txt(r.get("SS")),
        "id_unitate": _int0(r.get("IdUnitate")),
        "cod_partener": _txt(r.get("CodPartener")),
        "id_partener": _int0(r.get("IdPartener")),
        "ca_anterior": _num(r.get("CA_Anterior")),
        "inf1": _num(r.get("Inf1")),
        "ca_curent": _num(r.get("CA_Curent")),
        "cb_anterior": _num(r.get("CB_Anterior")),
        "inf2": _num(r.get("Inf2")),
        "cb_curent": _num(r.get("CB_Curent")),
    }


@forexe_bp.route("/api/forexe/ddf/draft/<int:iddf>/<int:idrev>", methods=["GET"])
@require_session
def get_ddf_draft(iddf, idrev):
    """One header, one revision, its section A, its section B and its attachment rows.

    The attachment BYTES are not included -- only name, size and checksum. The form fetches
    them one at a time, so a large document opens without waiting for every file.
    """
    db_name = g.session.db_name
    conn = None
    try:
        conn = get_kbot_connection(db_name)
        cursor = conn.cursor(dictionary=True)

        cursor.execute(_SQL_DRAFT_REV, (idrev, iddf))
        rev = cursor.fetchone()
        if rev is None:
            return _json_utf8(
                {"error": f"Revizia {idrev} nu există în documentul {iddf}."}, 404)

        cod = _txt(rev.get("CodAngajament"))
        cap = _citeste_ddf(cursor, cod)
        if cap is None:
            return _json_utf8(
                {"error": f"Documentul de fundamentare al angajamentului «{cod}» nu există."},
                404)

        cursor.execute(_SQL_DRAFT_SA, (idrev,))
        linii_a = [_linie_a_din_rand(r) for r in cursor.fetchall()]
        cursor.execute(_SQL_DRAFT_SB, (idrev,))
        linii_b = [_linie_b_din_rand(r) for r in cursor.fetchall()]

        # `ValRec` is display only and is not stored, so it is recomputed on every open.
        # Here it is ALL receptions of the indicator -- the rule of `cmbClsf_AfterUpdate`,
        # which is what the value rule in the grid compares against.
        if linii_a:
            cursor.execute(
                "SELECT CodIndicator, SUM(Valoare) AS T FROM FX_Receptii "
                " WHERE CodAngajament = %s GROUP BY CodIndicator", (cod,))
            receptii = {_txt(r["CodIndicator"]): _num(r["T"]) for r in cursor.fetchall()}
            for a in linii_a:
                a["val_rec"] = receptii.get(a["cod_indicator"], 0.0)

        atasamente = []
        avertismente = []
        if _are_att_img(cursor, db_name):
            cursor.execute(_SQL_DRAFT_ATT, (idrev,))
            for r in cursor.fetchall():
                nume = _txt(r.get("NumeFisier"))
                if not nume:
                    cale = _txt(r.get("CaleFisier"))
                    nume = cale.replace("\\", "/").rsplit("/", 1)[-1] if cale else ""
                atasamente.append({
                    "temp_id": 0,
                    "id_rev_att": _int0(r.get("IdRevAtt")),
                    "nume_fisier": nume,
                    "cale_fisier": _txt(r.get("CaleFisier")),
                    "tip_mime": _txt(r.get("TipMime")),
                    "dimensiune": _int0(r.get("Dimensiune")),
                    "sha256": _txt(r.get("Sha256")),
                    "prt_scr": _bool(r.get("PrtScr")),
                })
        else:
            avertismente.append(
                "Tabela FX_DDF_REV_ATT_IMG nu există pe această bază, deci fișierele atașate "
                "nu se pot citi sau salva. Rulați sql/0051_ddf_rev_att_img.sql.")

        raspuns = {
            "antet": _antet_din_ddf(cap, nou=False),
            "revizie": {
                "idrev": _int0(rev.get("IDREV")),
                "iddf": _int0(rev.get("IDDF")),
                "cod_angajament": cod,
                "numar_rev": _int0(rev.get("NumarRev")),
                "data_rev": _iso_zi(rev.get("DataRev")),
                "tip": _txt(rev.get("Tip")),
                "desc_scurta": _txt(rev.get("Desc_Scurta")),
                "desc_lunga": _txt(rev.get("Desc_Lunga")),
                "desc_lunga_ansi": _txt(rev.get("Desc_Lunga_ANSI")),
                "incarcat": _bool(rev.get("Incarcat")),
                "preluat": _bool(rev.get("Preluat")),
                "noua": False,
            },
            "linii_a": linii_a,
            "linii_b": linii_b,
            "atasamente": atasamente,
            "avertismente": avertismente,
            "sursa": "existent",
        }
        return _json_utf8(raspuns, 200)
    except DateInvalide as e:
        return _json_utf8({"error": str(e)}, 400)
    except Exception as e:
        logger.error(f"[forexe.ddf_edit] draft {iddf}/{idrev}: {e}", exc_info=True)
        return _json_utf8({"error": f"Eroare la citirea reviziei: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()


# =========================================================================================
# THE COMBO SOURCES
# =========================================================================================

# The port of `qFX_DDF_SA_CLSF`. NOT A FLAT LIST -- three parts, and all three are kept:
#   sort_ord 1 : classifications already on this angajament's FX_Indicatori;
#   sort_ord 2 : ONE synthetic separator row, IDClsf = -1 (added in Python, not in SQL, so
#                the statement stays a plain SELECT and the caption lives in one constant);
#   sort_ord 3 : every other classification sharing a Titlu with the group above.
#
# Two exclusions from the Access text move or disappear:
#   `Not In (SELECT IdClsf FROM tmpFX_DDF_REV_SA)` -- there is no tmp table. THE CLIENT
#       filters this locally against the draft's current section A, and re-filters after
#       every add or delete. The draft is never sent here for it.
#   `IdClsfPY <> 0` -- Access's test for "this row has a MariaDB counterpart". Meaningless
#       on the server, where every row HAS an IDClsf. Dropped, and recorded as dropped.
#
# The four caption sub-selects (qDenTitlu, qDenTitlu2, qDenClsfF, qDenClsfE) are dropped too:
# KBotComboBox has no multi-column dropdown, so they have nowhere to render. That also
# removes this slice's dependency on AVACONT_COMUN.DefaTitlu2, which does not exist on the
# server yet. They come back with the tree picker, not before.
_SQL_CLSF_PE_ANGAJAMENT = (
    "SELECT C.IDClsf, C.IdClsfAcc, C.Clsf, C.Denumire, C.SS, C.Titlu, C.IdUnitate, "
    "       (SELECT COALESCE(SUM(sa.ValCur), 0) FROM FX_DDF_REV_SA sa          WHERE sa.CodAngajament = %s AND sa.IdClsf = C.IDClsf)            AS ValPrec,        (SELECT COALESCE(SUM(r.Valoare), 0) FROM FX_Receptii r          WHERE r.CodAngajament = %s AND r.IdClsf = C.IdClsfAcc)           AS ValRec,        (SELECT sa.CodIndicator FROM FX_DDF_REV_SA sa          WHERE sa.CodAngajament = %s AND sa.IdClsf = C.IDClsf LIMIT 1)    AS CodIndicator,        CONCAT(C.SS, C.ClsfSal) AS CodSSI, 1 AS SortOrd "
    "  FROM Clasificatii C "
    " WHERE C.IDClsf IN (SELECT CC.IDClsf FROM Clasificatii CC "
    "                      JOIN FX_Indicatori I ON CC.IdClsfAcc = I.IdClsf "
    "                                          AND CC.IdUnitate = I.IdUnitate "
    "                     WHERE I.CodAngajament = %s) "
    " ORDER BY C.Clsf"
)

_SQL_CLSF_ACELASI_TITLU = (
    "SELECT C.IDClsf, C.IdClsfAcc, C.Clsf, C.Denumire, C.SS, C.Titlu, C.IdUnitate, "
    "       (SELECT COALESCE(SUM(sa.ValCur), 0) FROM FX_DDF_REV_SA sa          WHERE sa.CodAngajament = %s AND sa.IdClsf = C.IDClsf)            AS ValPrec,        (SELECT COALESCE(SUM(r.Valoare), 0) FROM FX_Receptii r          WHERE r.CodAngajament = %s AND r.IdClsf = C.IdClsfAcc)           AS ValRec,        (SELECT sa.CodIndicator FROM FX_DDF_REV_SA sa          WHERE sa.CodAngajament = %s AND sa.IdClsf = C.IDClsf LIMIT 1)    AS CodIndicator,        CONCAT(C.SS, C.ClsfSal) AS CodSSI, 3 AS SortOrd "
    "  FROM Clasificatii C "
    " WHERE C.Titlu IN (SELECT CC.Titlu FROM Clasificatii CC "
    "                     JOIN FX_Indicatori I ON CC.IdClsfAcc = I.IdClsf "
    "                                         AND CC.IdUnitate = I.IdUnitate "
    "                    WHERE I.CodAngajament = %s GROUP BY CC.Titlu) "
    "   AND C.IDClsf NOT IN (SELECT CC.IDClsf FROM Clasificatii CC "
    "                          JOIN FX_Indicatori I ON CC.IdClsfAcc = I.IdClsf "
    "                                              AND CC.IdUnitate = I.IdUnitate "
    "                         WHERE I.CodAngajament = %s) "
    " ORDER BY C.Clsf"
)

# The port of `qFX_DDF_SA_CLSF_MANUAL`. Access restricted it with
# `Titlu Like Nz(DLookUp('Mid(Clsf,13,2)','tmpFX_DDF_REV_SA'),'*')` -- the Titlu of the first
# line already in section A, or everything when section A is empty. `Mid(Clsf,13,2)` is
# Access arithmetic over a fixed-width string; here `Titlu` is a real generated column
# (`left(Articol, 2)`), so the client passes it as a parameter and no substring is computed.
_SQL_CLSF_MANUAL = (
    "SELECT C.IDClsf, C.IdClsfAcc, C.Clsf, C.Denumire, C.SS, C.Titlu, C.IdUnitate, "
    "       (SELECT COALESCE(SUM(sa.ValCur), 0) FROM FX_DDF_REV_SA sa          WHERE sa.CodAngajament = %s AND sa.IdClsf = C.IDClsf)            AS ValPrec,        (SELECT COALESCE(SUM(r.Valoare), 0) FROM FX_Receptii r          WHERE r.CodAngajament = %s AND r.IdClsf = C.IdClsfAcc)           AS ValRec,        (SELECT sa.CodIndicator FROM FX_DDF_REV_SA sa          WHERE sa.CodAngajament = %s AND sa.IdClsf = C.IDClsf LIMIT 1)    AS CodIndicator,        CONCAT(C.SS, C.ClsfSal) AS CodSSI, 1 AS SortOrd "
    "  FROM Clasificatii C "
    " WHERE (%s IS NULL OR C.Titlu = %s) "
    " ORDER BY C.Clsf"
)


def _clsf_din_rand(r: dict) -> dict:
    return {
        "id_clsf": _int0(r.get("IDClsf")),
        "id_clsf_acc": _int0(r.get("IdClsfAcc")),
        "clsf": _txt(r.get("Clsf")),
        "denumire": _txt(r.get("Denumire")),
        "ss": _txt(r.get("SS")),
        "cod_ssi": _txt(r.get("CodSSI")),
        "titlu": _txt(r.get("Titlu")),
        "id_unitate": _int0(r.get("IdUnitate")),
        "sort_ord": _int0(r.get("SortOrd")),
        # The three values `cmbClsf_AfterUpdate` looked up one at a time, precomputed here so
        # picking a classification costs the client no round trip. `val_rec` keys on
        # `IdClsfAcc` because `FX_Receptii.IdClsf` holds the ACCESS id (trap 2), while
        # `val_prec` keys on `IDClsf` because `FX_DDF_REV_SA.IdClsf` holds the MariaDB one.
        "val_prec": _num(r.get("ValPrec")),
        "val_rec": _num(r.get("ValRec")),
        # Empty = no indicator exists for this classification yet, so the client mints one.
        "cod_indicator": _txt(r.get("CodIndicator")),
    }


@forexe_bp.route("/api/forexe/ddf/clasificatii", methods=["GET"])
@require_session
def get_ddf_clasificatii():
    """The section-A classification combo.

    Query: `cod` (required), `manual` (0|1), `titlu` (optional, MANUAL variant only).

    Access chose between the two queries on `DDF_UPL = Left(CodAngajament, 1) <> "!"` -- a
    code starting with "!" is a manually created angajament and takes the MANUAL query. The
    client sends the flag; the server does not re-derive it.
    """
    cod = _txt(request.args.get("cod")).strip()
    if not cod:
        return _json_utf8({"error": "Parametrul «cod» lipsește."}, 400)
    manual = _txt(request.args.get("manual")).strip() in ("1", "true", "True")
    titlu = _txt(request.args.get("titlu")).strip() or None

    db_name = g.session.db_name
    conn = None
    try:
        conn = get_kbot_connection(db_name)
        cursor = conn.cursor(dictionary=True)

        if manual:
            cursor.execute(_SQL_CLSF_MANUAL, (cod, cod, cod, titlu, titlu))
            randuri = [_clsf_din_rand(r) for r in cursor.fetchall()]
        else:
            cursor.execute(_SQL_CLSF_PE_ANGAJAMENT, (cod, cod, cod, cod))
            grup1 = [_clsf_din_rand(r) for r in cursor.fetchall()]
            cursor.execute(_SQL_CLSF_ACELASI_TITLU, (cod, cod, cod, cod, cod))
            grup3 = [_clsf_din_rand(r) for r in cursor.fetchall()]

            randuri = list(grup1)
            # The separator only earns its place when there is something on both sides of it.
            if grup1 and grup3:
                randuri.append({
                    "id_clsf": CLSF_SEPARATOR_ID,
                    "id_clsf_acc": 0,
                    "clsf": CLSF_SEPARATOR_CLSF,
                    "denumire": CLSF_SEPARATOR_DENUMIRE,
                    "ss": "",
                    "cod_ssi": "",
                    "titlu": "",
                    "id_unitate": 0,
                    "sort_ord": 2,
                    "val_prec": 0.0,
                    "val_rec": 0.0,
                    "cod_indicator": "",
                })
            randuri.extend(grup3)

        return _json_utf8({"clasificatii": randuri}, 200)
    except Exception as e:
        logger.error(f"[forexe.ddf_edit] clasificatii: {e}", exc_info=True)
        return _json_utf8({"error": f"Eroare la citirea clasificațiilor: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()


# Access sourced the header partner combo from `ParteneriG` filtered `TIP = '1'`.
# `ParteneriG` was a union across units; MariaDB has `Parteneri`, which carries `IdUnitate`
# and keeps it (shared nomenclatoare keep the unit predicate). The unit is taken from the
# angajament's own indicators, because the SESSION HAS NO UNIT ID: every session is minted
# with `id_unitate = 0` and nothing reads it.
#
# `CodFiscal` is authoritative, not `CodPartener`: one CodFiscal can map to several
# IdUnitate, hence to several CodPartener / IdPartener rows. `FX_DDF` stores only CodFiscal
# and NumePartener -- it has no IdPartener column at all.
_SQL_PARTENERI = (
    "SELECT P.CodFiscal, "
    "       MIN(COALESCE(P.DenumirePartener, P.CodPartener)) AS NumePartener, "
    "       COUNT(*) AS Randuri "
    "  FROM Parteneri P "
    " WHERE P.Tip = '1' "
    "   AND COALESCE(P.Ascuns, 0) = 0 "
    "   AND COALESCE(P.CodFiscal, '') <> '' "
    "   AND P.IdUnitate IN (SELECT DISTINCT I.IdUnitate FROM FX_Indicatori I "
    "                        WHERE I.CodAngajament = %s AND I.IdUnitate IS NOT NULL) "
    " GROUP BY P.CodFiscal "
    " ORDER BY NumePartener"
)


@forexe_bp.route("/api/forexe/ddf/parteneri", methods=["GET"])
@require_session
def get_ddf_parteneri():
    """The header partner combo. Query: `cod` (required, to scope the units).

    `routes/parteneri.py` is NOT reused: it is guarded by X-Api-Key, which by the standing
    decision routes to the OLD server through `DB_CONFIG`, and it is a codes-to-ids lookup
    rather than a list.
    """
    cod = _txt(request.args.get("cod")).strip()
    if not cod:
        return _json_utf8({"error": "Parametrul «cod» lipsește."}, 400)

    db_name = g.session.db_name
    conn = None
    try:
        conn = get_kbot_connection(db_name)
        cursor = conn.cursor(dictionary=True)
        cursor.execute(_SQL_PARTENERI, (cod,))
        parteneri = [{
            "cod_fiscal": _txt(r.get("CodFiscal")),
            "nume_partener": _txt(r.get("NumePartener")),
            # More than one row behind a CodFiscal is normal (several units). Carried so the
            # client can say so rather than pretending the mapping is one-to-one.
            "randuri": _int0(r.get("Randuri")),
        } for r in cursor.fetchall()]
        return _json_utf8({"parteneri": parteneri}, 200)
    except Exception as e:
        logger.error(f"[forexe.ddf_edit] parteneri: {e}", exc_info=True)
        return _json_utf8({"error": f"Eroare la citirea partenerilor: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()


# THE COMPARTMENT COMBO, and why it looks like this.
#
# Access read `SELECT Comp FROM Oper WHERE Nz(Comp,'') <> '' GROUP BY Comp`. `Oper` is a
# LINKED TABLE from another Access database: it is not in MariaDB, not in AVACONT_COMUN, and
# not even in the Access export's TABLES/ directory. There is nothing to port.
#
# The operator's decision: read the compartments from PREVIOUS DOCUMENTS. When there are
# none the list comes back empty, and the combo lets the operator type a new value -- which
# is why it is editable on the client rather than a closed dropdown. `FX_DDF.Comp` is
# NOT NULL, so on a database with no documents yet the first compartment can only come from
# the keyboard.
_SQL_COMP = (
    "SELECT DISTINCT Comp FROM FX_DDF "
    " WHERE COALESCE(Comp, '') <> '' ORDER BY Comp"
)


@forexe_bp.route("/api/forexe/ddf/comp", methods=["GET"])
@require_session
def get_ddf_comp():
    """The compartments already used on this unit's documents. May legitimately be empty."""
    db_name = g.session.db_name
    conn = None
    try:
        conn = get_kbot_connection(db_name)
        cursor = conn.cursor(dictionary=True)
        cursor.execute(_SQL_COMP)
        return _json_utf8(
            {"comp": [_txt(r.get("Comp")) for r in cursor.fetchall()]}, 200)
    except Exception as e:
        logger.error(f"[forexe.ddf_edit] comp: {e}", exc_info=True)
        return _json_utf8({"error": f"Eroare la citirea compartimentelor: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()


# =========================================================================================
# THE NUMBER LOCK
# =========================================================================================
#
# `CUAL` and `NumarRev` are allocated by the operator, not by the database, and neither
# column carries a unique constraint. Access computed them with `Nz(DMax(...), -1) + 1` when
# the editor opened and nothing stopped a second operator computing the same value a second
# later. Here the number is genuinely HELD for as long as the form is open, so the header can
# show it plainly rather than as a guess -- the deliberate difference from slice 0049, where
# `NrORD` is only guessed ("probabil N") and allocated inside the save transaction.
#
# The `DC` predicate stays in both allocations even though one database is one `DC`. That is
# the operator's explicit instruction and it keeps the lock key identical to the `DMax`
# predicates Access used. Slice 0049 dropped it for `NrORD`; the two are NOT to be
# harmonised, and the worklog says so.

_SQL_LOCK_SWEEP = "DELETE FROM FX_NumberLock WHERE ExpiraLa < NOW()"

_SQL_LOCK_MAX_CUAL = (
    "SELECT GREATEST("
    "  COALESCE((SELECT MAX(CUAL) FROM FX_DDF WHERE DC = %s), 0), "
    "  COALESCE((SELECT MAX(Valoare) FROM FX_NumberLock "
    "             WHERE Tip = 'CUAL' AND DC = %s AND ExpiraLa >= NOW()), 0)"
    ") AS m"
)

# `Nz(DMax(...), -1) + 1` for NumarRev, so the initial revision is number 0. -1 is therefore
# the floor, not 0, and GREATEST is taken against -1 rather than against 0.
_SQL_LOCK_MAX_NUMARREV = (
    "SELECT GREATEST("
    "  COALESCE((SELECT MAX(NumarRev) FROM FX_DDF_REV "
    "             WHERE CodAngajament = %s AND DC = %s), -1), "
    "  COALESCE((SELECT MAX(Valoare) FROM FX_NumberLock "
    "             WHERE Tip = 'NUMARREV' AND DC = %s AND CodAngajament = %s "
    "               AND ExpiraLa >= NOW()), -1)"
    ") AS m"
)

_SQL_LOCK_INSERT = (
    "INSERT INTO FX_NumberLock (Tip, DC, Valoare, CodAngajament, Token, Utilizator, ExpiraLa) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s)"
)

_SQL_LOCK_DUPA_ID = "SELECT * FROM FX_NumberLock WHERE IdLock = %s"


def _lock_expira() -> datetime:
    return datetime.now() + timedelta(minutes=LOCK_TTL_MINUTE)


def _numar_folosit(cursor, tip: str, dc: str, cod: str, valoare: int) -> bool:
    """Is the number already committed to the real tables?"""
    if tip == LOCK_TIP_CUAL:
        cursor.execute("SELECT 1 FROM FX_DDF WHERE DC = %s AND CUAL = %s LIMIT 1",
                       (dc, valoare))
    else:
        cursor.execute("SELECT 1 FROM FX_DDF_REV "
                       " WHERE DC = %s AND CodAngajament = %s AND NumarRev = %s LIMIT 1",
                       (dc, cod, valoare))
    return cursor.fetchone() is not None


def _numar_blocat_de_altcineva(cursor, tip: str, dc: str, valoare: int, token: str):
    """Who else is holding the number right now? Returns their user name, or None."""
    cursor.execute(
        "SELECT Utilizator FROM FX_NumberLock "
        " WHERE Tip = %s AND DC = %s AND Valoare = %s AND ExpiraLa >= NOW() AND Token <> %s "
        " LIMIT 1", (tip, dc, valoare, token))
    rand = cursor.fetchone()
    return _txt(rand.get("Utilizator")) if rand else None


@forexe_bp.route("/api/forexe/ddf/numar/rezerva", methods=["POST"])
@require_session
def post_ddf_numar_rezerva():
    """Take the next free number of the given kind and hold it.

    Body: {"tip": "CUAL"|"NUMARREV", "cod": "<CodAngajament>", "dc": "<DC>"}
    Answer: {"id_lock", "valoare", "expira_la"}
    """
    sarcina = request.get_json(silent=True)
    if not isinstance(sarcina, dict):
        return _json_utf8({"error": "Corp JSON lipsă sau nevalid."}, 400)

    tip = _txt(sarcina.get("tip")).strip().upper()
    if tip not in LOCK_TIPURI:
        return _json_utf8(
            {"error": f"Tipul de număr «{tip}» nu este cunoscut (CUAL sau NUMARREV)."}, 400)
    dc = _txt(sarcina.get("dc")).strip()
    if not dc:
        return _json_utf8({"error": "Câmpul «dc» lipsește."}, 400)
    cod = _txt(sarcina.get("cod")).strip()
    if tip == LOCK_TIP_NUMARREV and not cod:
        return _json_utf8(
            {"error": "Câmpul «cod» este obligatoriu pentru numărul de revizie."}, 400)

    db_name = g.session.db_name
    token = getattr(g, "session_token", "") or ""
    utilizator = getattr(g.session, "username", "") or ""

    conn = None
    try:
        conn = get_kbot_connection(db_name)
        conn.autocommit = False
        cursor = conn.cursor(dictionary=True)
        if not conn.in_transaction:
            conn.start_transaction()

        # Sweep first: a lock left behind by a crashed editor must not block the next one.
        cursor.execute(_SQL_LOCK_SWEEP)

        if tip == LOCK_TIP_CUAL:
            cursor.execute(_SQL_LOCK_MAX_CUAL, (dc, dc))
            valoare = int((cursor.fetchone() or {}).get("m") or 0) + 1
            cod_lock = None
        else:
            cursor.execute(_SQL_LOCK_MAX_NUMARREV, (cod, dc, dc, cod))
            valoare = int((cursor.fetchone() or {}).get("m")) + 1
            cod_lock = cod

        expira = _lock_expira()
        cursor.execute(_SQL_LOCK_INSERT,
                       (tip, dc, valoare, cod_lock, token, utilizator, expira))
        id_lock = cursor.lastrowid
        if not id_lock:
            # AUTO_INCREMENT is gone from IdLock. Loud, never a silent zero key.
            raise RuntimeError("FX_NumberLock.IdLock nu a intors o cheie (AUTO_INCREMENT lipsa?)")
        conn.commit()

        logger.info("[forexe.ddf_edit] %s: lock %s=%s (id=%s, dc=%s, cod=%s)",
                    db_name, tip, valoare, id_lock, dc, cod_lock)
        return _json_utf8({"id_lock": int(id_lock), "tip": tip, "valoare": valoare,
                           "expira_la": _iso_dt(expira)}, 200)
    except Exception as e:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                logger.warning("[forexe.ddf_edit] rollback esuat", exc_info=True)
        logger.error(f"[forexe.ddf_edit] numar/rezerva: {e}", exc_info=True)
        return _json_utf8({"error": f"Eroare la rezervarea numărului: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()


@forexe_bp.route("/api/forexe/ddf/numar/<int:idlock>/schimba", methods=["POST"])
@require_session
def post_ddf_numar_schimba(idlock):
    """Move the lock to a number the operator typed.

    Refusals distinguish "already used" from "currently held by another operator", because
    they call for different things: one is permanent, the other is worth waiting out.
    """
    sarcina = request.get_json(silent=True)
    if not isinstance(sarcina, dict):
        return _json_utf8({"error": "Corp JSON lipsă sau nevalid."}, 400)
    valoare = _int_or_none(sarcina.get("valoare"))
    if valoare is None or valoare < 0:
        return _json_utf8({"error": "Numărul cerut nu este valid."}, 400)

    db_name = g.session.db_name
    token = getattr(g, "session_token", "") or ""
    conn = None
    try:
        conn = get_kbot_connection(db_name)
        conn.autocommit = False
        cursor = conn.cursor(dictionary=True)
        if not conn.in_transaction:
            conn.start_transaction()

        cursor.execute(_SQL_LOCK_SWEEP)
        cursor.execute(_SQL_LOCK_DUPA_ID, (idlock,))
        lacat = cursor.fetchone()
        if lacat is None:
            conn.rollback()
            return _json_utf8(
                {"error": "Rezervarea numărului a expirat. Închideți și redeschideți "
                          "documentul pentru a primi un număr nou."}, 409)
        if _txt(lacat.get("Token")) != token:
            conn.rollback()
            return _json_utf8({"error": "Rezervarea numărului aparține altei sesiuni."}, 403)

        tip = _txt(lacat.get("Tip"))
        dc = _txt(lacat.get("DC"))
        cod = _txt(lacat.get("CodAngajament"))

        if _numar_folosit(cursor, tip, dc, cod, valoare):
            conn.rollback()
            return _json_utf8({"error": f"Numărul {valoare} a mai fost folosit."}, 409)

        detinator = _numar_blocat_de_altcineva(cursor, tip, dc, valoare, token)
        if detinator is not None:
            conn.rollback()
            return _json_utf8(
                {"error": f"Numărul {valoare} este rezervat acum de «{detinator}». "
                          "Alegeți alt număr sau așteptați."}, 409)

        expira = _lock_expira()
        cursor.execute("UPDATE FX_NumberLock SET Valoare = %s, ExpiraLa = %s WHERE IdLock = %s",
                       (valoare, expira, idlock))
        conn.commit()
        return _json_utf8({"id_lock": idlock, "tip": tip, "valoare": valoare,
                           "expira_la": _iso_dt(expira)}, 200)
    except Exception as e:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                logger.warning("[forexe.ddf_edit] rollback esuat", exc_info=True)
        logger.error(f"[forexe.ddf_edit] numar/schimba {idlock}: {e}", exc_info=True)
        return _json_utf8({"error": f"Eroare la schimbarea numărului: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()


@forexe_bp.route("/api/forexe/ddf/numar/<int:idlock>/prelungeste", methods=["POST"])
@require_session
def post_ddf_numar_prelungeste(idlock):
    """Heartbeat: push the lock's expiry out by another TTL."""
    db_name = g.session.db_name
    token = getattr(g, "session_token", "") or ""
    conn = None
    try:
        conn = get_kbot_connection(db_name)
        cursor = conn.cursor(dictionary=True)
        expira = _lock_expira()
        cursor.execute(
            "UPDATE FX_NumberLock SET ExpiraLa = %s WHERE IdLock = %s AND Token = %s",
            (expira, idlock, token))
        conn.commit()
        if cursor.rowcount == 0:
            # The lock is gone (expired and swept, or released). The client is told plainly:
            # the save will refuse later anyway, and knowing now is better.
            return _json_utf8(
                {"error": "Rezervarea numărului nu mai există."}, 404)
        return _json_utf8({"id_lock": idlock, "expira_la": _iso_dt(expira)}, 200)
    except Exception as e:
        logger.error(f"[forexe.ddf_edit] numar/prelungeste {idlock}: {e}", exc_info=True)
        return _json_utf8({"error": f"Eroare la prelungirea rezervării: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()


@forexe_bp.route("/api/forexe/ddf/numar/<int:idlock>", methods=["DELETE"])
@require_session
def delete_ddf_numar(idlock):
    """Release a lock. Called from `FormClosed`, whatever closed the form."""
    db_name = g.session.db_name
    token = getattr(g, "session_token", "") or ""
    conn = None
    try:
        conn = get_kbot_connection(db_name)
        cursor = conn.cursor(dictionary=True)
        cursor.execute("DELETE FROM FX_NumberLock WHERE IdLock = %s AND Token = %s",
                       (idlock, token))
        conn.commit()
        return _json_utf8({"id_lock": idlock, "eliberat": cursor.rowcount > 0}, 200)
    except Exception as e:
        logger.error(f"[forexe.ddf_edit] numar DELETE {idlock}: {e}", exc_info=True)
        return _json_utf8({"error": f"Eroare la eliberarea numărului: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()


# =========================================================================================
# SAVE
# =========================================================================================

def _valideaza_graf(cursor, sarcina: dict) -> dict:
    """Every reason the graph cannot be written, gathered, BEFORE the first INSERT.

    The port of the `msgEroare` block at the head of `btnSav_Click`, with one deliberate
    correction: Access checked only the FIRST row of each recordset (`If Not Rs.EOF Then`,
    with no loop), so a bad value on row two went through. Every row is checked here, and
    the divergence is recorded in the worklog.

    The client validates the same things first, so the operator gets a fast message naming
    all the problems at once; this is the copy that cannot be bypassed.
    """
    motive = []

    antet = sarcina.get("antet")
    if not isinstance(antet, dict):
        raise DateInvalide("Antetul documentului lipsește din cerere.")
    revizie = sarcina.get("revizie")
    if not isinstance(revizie, dict):
        raise DateInvalide("Revizia lipsește din cerere.")

    cod = _txt(antet.get("cod_angajament")).strip()
    if not cod:
        motive.append("Cod angajament lipsă.")
    if not _txt(antet.get("obiect_ddf")).strip():
        motive.append("Obiectul documentului este obligatoriu.")
    if not _txt(antet.get("comp")).strip():
        motive.append("Compartimentul lipsește.")
    if _zi_ceruta(antet.get("data_creare"), "data_creare") is None:
        motive.append("Data creării lipsește.")
    if _int_or_none(antet.get("cual")) in (None, 0):
        motive.append("CUAL lipsește.")

    if _int_or_none(revizie.get("numar_rev")) is None:
        motive.append("Numărul reviziei lipsește.")
    if _zi_ceruta(revizie.get("data_rev"), "data_rev") is None:
        motive.append("Data reviziei lipsește.")
    if not _txt(revizie.get("desc_scurta")).strip():
        motive.append("Descrierea scurtă lipsește.")
    if not _txt(revizie.get("desc_lunga_ansi")).strip():
        motive.append("Descrierea lungă lipsește.")
    if not _txt(revizie.get("tip")).strip():
        motive.append("Tipul reviziei lipsește.")

    part_ang = bool(antet.get("part_ang"))
    linii_a = _lista(sarcina, "linii_a")
    if not linii_a:
        motive.append("Lipsește cel puțin un rând în secțiunea A.")
    for i, a in enumerate(linii_a, start=1):
        if not _txt(a.get("cod_angajament")).strip():
            motive.append(f"Cod angajament lipsă pe rândul {i} din secțiunea A.")
        if not _txt(a.get("cod_indicator")).strip():
            motive.append(f"Cod indicator lipsă pe rândul {i} din secțiunea A.")
        if not _txt(a.get("element_fund")).strip():
            motive.append(f"Element de fundamentare lipsă pe rândul {i} din secțiunea A.")
        if _num(a.get("val_cur")) == 0.0:
            motive.append(f"Valoarea curentă este 0 pe rândul {i} din secțiunea A.")
        # `IdClsf` is a foreign key to Clasificatii and `IdClsfAcc` is NOT NULL, so a zero
        # here stops the transaction with an errno that names nothing.
        if _int0(a.get("id_clsf")) <= 0:
            motive.append(f"Clasificația lipsește pe rândul {i} din secțiunea A.")
        if _int0(a.get("id_unitate")) <= 0:
            motive.append(f"Unitatea lipsește pe rândul {i} din secțiunea A.")
        if part_ang and not _txt(a.get("cod_partener")).strip():
            motive.append(
                f"Dacă asociezi documentul cu un partener, câmpul «Partener» devine "
                f"obligatoriu (rândul {i} din secțiunea A).")

    linii_b = _lista(sarcina, "linii_b")
    for i, b in enumerate(linii_b, start=1):
        if not _txt(b.get("cod_indicator")).strip():
            motive.append(f"Cod indicator lipsă pe rândul {i} din secțiunea B.")
        if _num(b.get("inf1")) == 0.0:
            motive.append(f"Influența C.A. este 0 pe rândul {i} din secțiunea B.")
        if _num(b.get("inf2")) == 0.0:
            motive.append(f"Influența C.B. este 0 pe rândul {i} din secțiunea B.")

    for t in _lista(sarcina, "atasamente"):
        if not _txt(t.get("nume_fisier")).strip():
            motive.append("Un fișier atașat nu are nume.")
            break

    if motive:
        raise DateInvalide("Nu pot salva din următoarele motive:\n- " + "\n- ".join(motive))

    return antet


def _rezolva_clasificatii(cursor, linii_a: list, linii_b: list) -> dict:
    """`IdClsfAcc`, `SS` and `CodSSI` for every classification in the graph.

    The client sends only `IDClsf`, the MariaDB key. `IdClsfAcc` is NOT NULL on both `_SA`
    and `_SB`, `CodSSI` has no column in `Clasificatii` at all (it is `CONCAT(SS, ClsfSal)`),
    and none of the three may be trusted from the client -- so all three are read here.
    """
    id_uri = {_int0(x.get("id_clsf")) for x in list(linii_a) + list(linii_b)}
    id_uri.discard(0)
    if not id_uri:
        return {}

    sabloane = ", ".join(["%s"] * len(id_uri))
    cursor.execute(
        f"SELECT IDClsf, IdClsfAcc, Clsf, SS, CONCAT(SS, ClsfSal) AS CodSSI, IdUnitate "
        f"  FROM Clasificatii WHERE IDClsf IN ({sabloane})", tuple(id_uri))
    gasite = {int(r["IDClsf"]): r for r in cursor.fetchall()}

    lipsa = sorted(id_uri - set(gasite.keys()))
    if lipsa:
        raise DateInvalide(
            "Aceste clasificații nu există în nomenclator: "
            + ", ".join(str(x) for x in lipsa) + ".")
    return gasite


_SQL_SA_INSERT = (
    "INSERT INTO FX_DDF_REV_SA "
    "  (IDDF, IDREV, CodAngajament, CodIndicator, CodPartener, IdPartener, IdClsfAcc, "
    "   IdClsf, Clsf, ElementFund, ParametriiFund, ValPrec, ValCur, ValTot, PartInd, "
    "   Ramane, IdUnitate, SS) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
)

_SQL_SA_UPDATE = (
    "UPDATE FX_DDF_REV_SA SET "
    "  IDDF = %s, IDREV = %s, CodAngajament = %s, CodIndicator = %s, CodPartener = %s, "
    "  IdPartener = %s, IdClsfAcc = %s, IdClsf = %s, Clsf = %s, ElementFund = %s, "
    "  ParametriiFund = %s, ValPrec = %s, ValCur = %s, ValTot = %s, PartInd = %s, "
    "  Ramane = %s, IdUnitate = %s, SS = %s "
    "WHERE IdSecA = %s"
)

_SQL_SB_INSERT = (
    "INSERT INTO FX_DDF_REV_SB "
    "  (IDDF, IDREV, CodAngajament, CodIndicator, CodPartener, IdPartener, IdClsfAcc, "
    "   IdClsf, CodSSI, CA_Anterior, Inf1, CA_Curent, CB_Anterior, Inf2, CB_Curent, "
    "   IdUnitate, SS) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
)

_SQL_SB_UPDATE = (
    "UPDATE FX_DDF_REV_SB SET "
    "  IDDF = %s, IDREV = %s, CodAngajament = %s, CodIndicator = %s, CodPartener = %s, "
    "  IdPartener = %s, IdClsfAcc = %s, IdClsf = %s, CodSSI = %s, CA_Anterior = %s, "
    "  Inf1 = %s, CA_Curent = %s, CB_Anterior = %s, Inf2 = %s, CB_Curent = %s, "
    "  IdUnitate = %s, SS = %s "
    "WHERE IdSecB = %s"
)


def _cheie_noua(cursor, tabela: str) -> int:
    """`lastrowid` after an INSERT, refused when it is 0.

    A zero means the column has lost its AUTO_INCREMENT, and every child row written against
    that key would be silently orphaned. Loud, always -- the same guard slice 0049 uses.
    """
    cheie = cursor.lastrowid
    if not cheie:
        raise RuntimeError(
            f"{tabela} nu a intors o cheie noua (AUTO_INCREMENT lipsa pe cheia primara?)")
    return int(cheie)


def _sterge_absentii(cursor, tabela: str, cheie: str, idrev: int, pastrate: set) -> int:
    """Rows stored against this revision but absent from the draft. The port of
    `Save_FX_DDF_REV_SA_Update`'s delete half."""
    cursor.execute(f"SELECT {cheie} AS k FROM {tabela} WHERE IDREV = %s", (idrev,))
    stocate = {int(r["k"]) for r in cursor.fetchall()}
    de_sters = stocate - pastrate
    if not de_sters:
        return 0
    sabloane = ", ".join(["%s"] * len(de_sters))
    cursor.execute(f"DELETE FROM {tabela} WHERE {cheie} IN ({sabloane})", tuple(de_sters))
    return len(de_sters)


def _consuma_lacatul(cursor, id_lock: int, tip: str, dc: str, cod: str,
                     valoare: int, token: str) -> None:
    """Verify the lock inside the transaction, then delete it in the same transaction.

    A save whose lock expired and was taken by someone else fails HERE, naming the number,
    rather than writing a duplicate that nothing would ever notice.
    """
    if id_lock <= 0:
        # No lock was taken for this number -- legal for an existing CUAL, and for the number
        # of a revision that is only being modified. The number must then already be ours.
        return

    cursor.execute(_SQL_LOCK_DUPA_ID, (id_lock,))
    lacat = cursor.fetchone()
    if lacat is None:
        raise DateInvalide(
            f"Rezervarea numărului {valoare} a expirat între timp. Închideți documentul, "
            "redeschideți-l și reluați salvarea.")
    if _txt(lacat.get("Token")) != token:
        raise DateInvalide(f"Numărul {valoare} este rezervat acum de altă sesiune.")
    if int(lacat.get("Valoare")) != valoare:
        raise DateInvalide(
            f"Numărul rezervat ({int(lacat.get('Valoare'))}) nu este cel trimis la salvare "
            f"({valoare}).")
    if _txt(lacat.get("Tip")) != tip:
        raise DateInvalide(f"Rezervarea {id_lock} nu este de tipul {tip}.")

    if _numar_folosit(cursor, tip, dc, cod, valoare):
        raise DateInvalide(f"Numărul {valoare} a fost folosit între timp de altcineva.")

    cursor.execute("DELETE FROM FX_NumberLock WHERE IdLock = %s", (id_lock,))


def _scrie_graf(cursor, sarcina: dict, token: str) -> dict:
    """The heart of the save transaction. Raises `DateInvalide` or propagates the DB error.

    Write order, checked against every foreign key in the schema:
        1. FX_Angajamente   (INSERT, only when Manual)
        2. FX_Indicatori    (INSERT, only when Manual)
        3. FX_DDF           (INSERT when new, UPDATE otherwise)
        4. FX_DDF_REV       (INSERT when the revision is new, UPDATE otherwise)
        5. FX_DDF_REV_SA    (INSERT / UPDATE / DELETE against the stored set)
        6. FX_DDF_REV_SB    (the same)
        7. FX_DDF_REV_ATT   (the same, metadata only)
        8. the post-save updates
        9. the number locks are verified, consumed and deleted
    """
    antet = _valideaza_graf(cursor, sarcina)
    revizie = sarcina["revizie"]
    linii_a = _lista(sarcina, "linii_a")
    linii_b = _lista(sarcina, "linii_b")
    atasamente = _lista(sarcina, "atasamente")

    cod = _txt(antet.get("cod_angajament")).strip()
    dc = _txt(antet.get("dc")).strip()
    cual = _int0(antet.get("cual"))
    iddf = _int0(antet.get("iddf"))
    idrev = _int0(revizie.get("idrev"))
    ddf_nou = bool(antet.get("nou")) or iddf <= 0
    rev_noua = bool(revizie.get("noua")) or idrev <= 0
    manual = bool(antet.get("manual"))
    obiect = _txt(antet.get("obiect_ddf"))

    clasificatii = _rezolva_clasificatii(cursor, linii_a, linii_b)

    # ---- 1 & 2: the Manual branch --------------------------------------------------------
    # A manually created angajament (its code starts with "!") does not come from FOREXE, so
    # its FX_Angajamente and FX_Indicatori rows have to be written here -- the port of
    # `Save_FX_Angajamente` and `Save_FX_Indicatori`. Both are INSERT ... ON DUPLICATE KEY so
    # a second save of the same document does not fail on the primary key.
    if manual:
        cursor.execute(
            "INSERT INTO FX_Angajamente "
            "  (CodAngajament, DataCreare, DataDefinitivare, Descriere, Stare, DC, "
            "   Incarcat, Preluat, Salarii) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "ON DUPLICATE KEY UPDATE Descriere = VALUES(Descriere), Stare = VALUES(Stare)",
            (cod,
             _zi_ceruta(antet.get("data_creare"), "data_creare"),
             _zi_ceruta(antet.get("data_def"), "data_def"),
             obiect[:LUNGIME_DESCRIERE_ANGAJAMENT],
             _txt(antet.get("stare")) or "MANUAL",
             dc,
             1 if antet.get("incarcat") else 0,
             1 if antet.get("preluat") else 0,
             1 if antet.get("salarii") else 0))

        for a in linii_a:
            cod_ai = f"{cod}-{_txt(a.get('cod_indicator'))}"
            clsf = clasificatii.get(_int0(a.get("id_clsf")), {})
            val_cur = _num(a.get("val_cur"))
            cursor.execute(
                "INSERT INTO FX_Indicatori "
                "  (CodAI, CodAngajament, CodIndicator, IdClsf, IdUnitate, "
                "   Credit_Bugetar_Initial, Angajament_Legal, Credit_Bugetar_Definitiv) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                "ON DUPLICATE KEY UPDATE "
                "  Credit_Bugetar_Initial = VALUES(Credit_Bugetar_Initial), "
                "  Angajament_Legal = VALUES(Angajament_Legal), "
                "  Credit_Bugetar_Definitiv = VALUES(Credit_Bugetar_Definitiv)",
                # NOTE the key space: FX_Indicatori.IdClsf holds the ACCESS id (trap 2), so
                # what goes in here is IdClsfAcc, NOT the IDClsf the section-A line carries.
                (cod_ai, cod, _txt(a.get("cod_indicator")),
                 _int0(clsf.get("IdClsfAcc")), _int0(a.get("id_unitate")),
                 val_cur, val_cur, val_cur))

    # ---- 3: FX_DDF -----------------------------------------------------------------------
    if ddf_nou:
        _consuma_lacatul(cursor, _int0(sarcina.get("id_lock_cual")),
                         LOCK_TIP_CUAL, dc, cod, cual, token)
        cursor.execute(
            "INSERT INTO FX_DDF "
            "  (CodAngajament, CUAL, Comp, Salarii, DataCreare, DC, Program, DataDef, "
            "   Incarcat, Preluat, Buget, Manual, ObiectDDF, Stare, PartAng, CodFiscal, "
            "   NumePartener) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (cod, cual, _txt(antet.get("comp")),
             1 if antet.get("salarii") else 0,
             _zi_ceruta(antet.get("data_creare"), "data_creare"),
             dc, _txt(antet.get("program")),
             _zi_ceruta(antet.get("data_def"), "data_def"),
             1 if antet.get("incarcat") else 0,
             1 if antet.get("preluat") else 0,
             1 if antet.get("buget") else 0,
             1 if manual else 0,
             obiect, _txt(antet.get("stare")),
             1 if antet.get("part_ang") else 0,
             _txt(antet.get("cod_fiscal")) or None,
             _txt(antet.get("nume_partener")) or None))
        iddf = _cheie_noua(cursor, "FX_DDF")
    else:
        # The composite primary key is (IDDF, CUAL), so both halves pin the row. CUAL is
        # never changed on an existing document, which is exactly why no lock is taken for it.
        cursor.execute(
            "UPDATE FX_DDF SET "
            "  Comp = %s, Salarii = %s, DataCreare = %s, Program = %s, DataDef = %s, "
            "  Incarcat = %s, Preluat = %s, Buget = %s, ObiectDDF = %s, Stare = %s, "
            "  PartAng = %s, CodFiscal = %s, NumePartener = %s "
            "WHERE IDDF = %s AND CUAL = %s",
            (_txt(antet.get("comp")),
             1 if antet.get("salarii") else 0,
             _zi_ceruta(antet.get("data_creare"), "data_creare"),
             _txt(antet.get("program")),
             _zi_ceruta(antet.get("data_def"), "data_def"),
             1 if antet.get("incarcat") else 0,
             1 if antet.get("preluat") else 0,
             1 if antet.get("buget") else 0,
             obiect, _txt(antet.get("stare")),
             1 if antet.get("part_ang") else 0,
             _txt(antet.get("cod_fiscal")) or None,
             _txt(antet.get("nume_partener")) or None,
             iddf, cual))
        if cursor.rowcount == 0:
            # Zero rows can mean "nothing changed", so it is not an error by itself -- but a
            # missing row IS, and the two are told apart rather than lumped together.
            cursor.execute("SELECT 1 FROM FX_DDF WHERE IDDF = %s AND CUAL = %s LIMIT 1",
                           (iddf, cual))
            if cursor.fetchone() is None:
                raise DateInvalide(
                    f"Documentul (IDDF {iddf}, CUAL {cual}) nu mai există în baza de date.")

    # ---- 4: FX_DDF_REV -------------------------------------------------------------------
    numar_rev = _int0(revizie.get("numar_rev"))
    if rev_noua:
        _consuma_lacatul(cursor, _int0(sarcina.get("id_lock_numar_rev")),
                         LOCK_TIP_NUMARREV, dc, cod, numar_rev, token)
        cursor.execute(
            "INSERT INTO FX_DDF_REV "
            "  (IDDF, CodAngajament, Tip, NumarRev, DataRev, Desc_Scurta, Desc_Lunga, "
            "   Desc_Lunga_ANSI, Incarcat, Preluat, DC) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (iddf, cod, _txt(revizie.get("tip")), numar_rev,
             _zi_ceruta(revizie.get("data_rev"), "data_rev"),
             _txt(revizie.get("desc_scurta")),
             _txt(revizie.get("desc_lunga")),
             _txt(revizie.get("desc_lunga_ansi")),
             1 if revizie.get("incarcat") else 0,
             1 if revizie.get("preluat") else 0,
             dc))
        idrev = _cheie_noua(cursor, "FX_DDF_REV")
    else:
        cursor.execute(
            "UPDATE FX_DDF_REV SET "
            "  IDDF = %s, CodAngajament = %s, Tip = %s, NumarRev = %s, DataRev = %s, "
            "  Desc_Scurta = %s, Desc_Lunga = %s, Desc_Lunga_ANSI = %s, "
            "  Incarcat = %s, Preluat = %s, DC = %s "
            "WHERE IDREV = %s",
            (iddf, cod, _txt(revizie.get("tip")), numar_rev,
             _zi_ceruta(revizie.get("data_rev"), "data_rev"),
             _txt(revizie.get("desc_scurta")),
             _txt(revizie.get("desc_lunga")),
             _txt(revizie.get("desc_lunga_ansi")),
             1 if revizie.get("incarcat") else 0,
             1 if revizie.get("preluat") else 0,
             dc, idrev))

    # ---- 5: FX_DDF_REV_SA ----------------------------------------------------------------
    harta_a = {}
    pastrate_a = set()
    for a in linii_a:
        clsf = clasificatii.get(_int0(a.get("id_clsf")), {})
        valori = (
            iddf, idrev, cod, _txt(a.get("cod_indicator")),
            _txt(a.get("cod_partener")) or None,
            _int0(a.get("id_partener")) or None,
            _int0(clsf.get("IdClsfAcc")),
            _int0(a.get("id_clsf")),
            _txt(clsf.get("Clsf")),
            _txt(a.get("element_fund")),
            _txt(a.get("parametrii_fund")) or None,
            _num(a.get("val_prec")), _num(a.get("val_cur")), _num(a.get("val_tot")),
            1 if a.get("part_ind") else 0,
            _num(a.get("ramane")),
            _int0(a.get("id_unitate")) or None,
            _txt(clsf.get("SS")),
        )
        id_sec_a = _int0(a.get("id_sec_a"))
        if id_sec_a > 0:
            cursor.execute(_SQL_SA_UPDATE, valori + (id_sec_a,))
        else:
            cursor.execute(_SQL_SA_INSERT, valori)
            id_sec_a = _cheie_noua(cursor, "FX_DDF_REV_SA")
            harta_a[_int0(a.get("temp_id"))] = id_sec_a
        pastrate_a.add(id_sec_a)
    _sterge_absentii(cursor, "FX_DDF_REV_SA", "IdSecA", idrev, pastrate_a)

    # ---- 6: FX_DDF_REV_SB ----------------------------------------------------------------
    harta_b = {}
    pastrate_b = set()
    for b in linii_b:
        clsf = clasificatii.get(_int0(b.get("id_clsf")), {})
        valori = (
            iddf, idrev, cod, _txt(b.get("cod_indicator")),
            _txt(b.get("cod_partener")) or None,
            _int0(b.get("id_partener")) or None,
            _int0(clsf.get("IdClsfAcc")),
            _int0(b.get("id_clsf")),
            # CodSSI is resolved here, never taken from the client.
            _txt(clsf.get("CodSSI")),
            _num(b.get("ca_anterior")), _num(b.get("inf1")), _num(b.get("ca_curent")),
            _num(b.get("cb_anterior")), _num(b.get("inf2")), _num(b.get("cb_curent")),
            _int0(b.get("id_unitate")) or None,
            _txt(clsf.get("SS")),
        )
        id_sec_b = _int0(b.get("id_sec_b"))
        if id_sec_b > 0:
            cursor.execute(_SQL_SB_UPDATE, valori + (id_sec_b,))
        else:
            cursor.execute(_SQL_SB_INSERT, valori)
            id_sec_b = _cheie_noua(cursor, "FX_DDF_REV_SB")
            harta_b[_int0(b.get("temp_id"))] = id_sec_b
        pastrate_b.add(id_sec_b)
    _sterge_absentii(cursor, "FX_DDF_REV_SB", "IdSecB", idrev, pastrate_b)

    # ---- 7: FX_DDF_REV_ATT (metadata only; the bytes are phase two) ----------------------
    harta_att = {}
    pastrate_att = set()
    for t in atasamente:
        id_rev_att = _int0(t.get("id_rev_att"))
        # PrtScr rows come from FOREXE and are never created or changed here (they are
        # read-only in the grid too); they are carried so the delete pass does not remove
        # them from under the workflow that owns them.
        prt_scr = 1 if t.get("prt_scr") else 0
        if id_rev_att > 0:
            cursor.execute(
                "UPDATE FX_DDF_REV_ATT SET IDDF = %s, IDREV = %s, CaleFisier = %s, "
                "       PrtScr = %s WHERE IdRevAtt = %s",
                (iddf, idrev, _txt(t.get("cale_fisier")) or None, prt_scr, id_rev_att))
        else:
            # DateFisier stays NULL (decision D12) and IDVBNET is never written (D11).
            cursor.execute(
                "INSERT INTO FX_DDF_REV_ATT (IDDF, IDREV, CaleFisier, PrtScr) "
                "VALUES (%s, %s, %s, %s)",
                (iddf, idrev, _txt(t.get("cale_fisier")) or None, prt_scr))
            id_rev_att = _cheie_noua(cursor, "FX_DDF_REV_ATT")
            harta_att[_int0(t.get("temp_id"))] = id_rev_att
        pastrate_att.add(id_rev_att)
    _sterge_absentii(cursor, "FX_DDF_REV_ATT", "IdRevAtt", idrev, pastrate_att)

    # ---- 8: the post-save updates, INSIDE the same transaction ---------------------------
    # 8.1 FX_Rezervari: the reservations this revision consumed. The ids come from `grp_idrz`
    # on the generated lines whose ValCur is non-zero -- the port of the QFX_DDF_REZERVARI
    # loop at the foot of `btnSav_Click`.
    idrz = set()
    for a in linii_a:
        if _num(a.get("val_cur")) == 0.0:
            continue
        for bucata in _txt(a.get("grp_idrz")).replace(";", ",").split(","):
            bucata = bucata.strip()
            if bucata.isdigit():
                idrz.add(int(bucata))
    rezervari_legate = 0
    if idrz:
        sabloane = ", ".join(["%s"] * len(idrz))
        cursor.execute(
            f"UPDATE FX_Rezervari SET IDREV = %s, AreDDF = TRUE WHERE IDRZ IN ({sabloane})",
            (idrev,) + tuple(idrz))
        rezervari_legate = cursor.rowcount

    # 8.2 and 8.3 FX_Angajamente. The Descriere cascade is UNCONDITIONAL now (decision D10
    # replaces Access's `ModNume` gate). ObiectDDF is varchar(500) and Descriere is
    # varchar(255), so the value is truncated HERE, explicitly, rather than by MariaDB.
    cursor.execute(
        "UPDATE FX_Angajamente SET IDDF = %s, Incarcat = %s, Preluat = %s, Salarii = %s, "
        "       Descriere = %s WHERE CodAngajament = %s",
        (iddf,
         1 if antet.get("incarcat") else 0,
         1 if antet.get("preluat") else 0,
         1 if antet.get("salarii") else 0,
         obiect[:LUNGIME_DESCRIERE_ANGAJAMENT],
         cod))

    # The SalariiH update from the same Access block is NOT ported (decision D14).

    return {
        "iddf": iddf,
        "cual": cual,
        "idrev": idrev,
        "numar_rev": numar_rev,
        "harta": {"linii_a": harta_a, "linii_b": harta_b, "att": harta_att},
        "rezervari_legate": rezervari_legate,
        "obiect_trunchiat": len(obiect) > LUNGIME_DESCRIERE_ANGAJAMENT,
    }


# MariaDB error numbers worth replaying the WHOLE transaction for: deadlock, lock wait
# timeout, and the duplicate key a concurrent allocation can produce. Read from `errno`, not
# from the message text -- the text is localised by the server and changes between versions.
_ERORI_DE_RELUAT = frozenset({1213, 1205, 1062})


def _e_conflict(e) -> bool:
    return getattr(e, "errno", None) in _ERORI_DE_RELUAT


@forexe_bp.route("/api/forexe/ddf/save", methods=["POST"])
@require_session
def post_ddf_save():
    """Write the WHOLE graph in one transaction and return the real keys.

    Body: { antet, revizie, linii_a[], linii_b[], atasamente[], id_lock_cual,
            id_lock_numar_rev }. New rows carry negative `temp_id`; existing ones carry
    their real primary key.

    Answer: { iddf, cual, idrev, numar_rev, harta: { linii_a, linii_b, att },
              rezervari_legate }. The client needs the `att` map to upload the bytes
    (phase two).
    """
    sarcina = request.get_json(silent=True)
    if not isinstance(sarcina, dict):
        return _json_utf8({"error": "Corp JSON lipsă sau nevalid."}, 400)

    db_name = g.session.db_name
    token = getattr(g, "session_token", "") or ""

    # The replay covers the WHOLE transaction, never a piece of it (house rule).
    ultima_eroare = None
    for incercare in range(3):
        conn = None
        try:
            conn = get_kbot_connection(db_name)
            conn.autocommit = False
            cursor = conn.cursor(dictionary=True)
            # `start_transaction()` is the connector's API; a hand-written START TRANSACTION
            # would quietly commit the implicit transaction `autocommit = False` opened.
            if not conn.in_transaction:
                conn.start_transaction()

            rezultat = _scrie_graf(cursor, sarcina, token)
            conn.commit()

            logger.info("[forexe.ddf_edit] %s: save iddf=%s cual=%s idrev=%s rev=%s "
                        "(incercarea %s)", db_name, rezultat["iddf"], rezultat["cual"],
                        rezultat["idrev"], rezultat["numar_rev"], incercare + 1)
            return _json_utf8(rezultat, 200)
        except DateInvalide as e:
            if conn is not None:
                conn.rollback()
            return _json_utf8({"error": str(e)}, 400)
        except Exception as e:
            if conn is not None:
                try:
                    conn.rollback()
                except Exception:
                    logger.warning("[forexe.ddf_edit] rollback esuat dupa eroarea de mai jos",
                                   exc_info=True)
            ultima_eroare = e
            if _e_conflict(e) and incercare < 2:
                logger.warning("[forexe.ddf_edit] %s: save a intalnit un conflict (%s); "
                               "se reia toata tranzactia", db_name, e)
                continue
            logger.error(f"[forexe.ddf_edit] save: {e}", exc_info=True)
            return _json_utf8({"error": f"Eroare la salvarea documentului: {e}"}, 500)
        finally:
            if conn is not None:
                conn.close()

    logger.error(f"[forexe.ddf_edit] save: {ultima_eroare}", exc_info=True)
    return _json_utf8({"error": f"Eroare la salvarea documentului: {ultima_eroare}"}, 500)


# =========================================================================================
# DELETE
# =========================================================================================
#
# All three routes lean on the ON DELETE CASCADE that `_SA`, `_SB`, `_ATT` and `_PRT` already
# carry on IDREV and IDDF, so the child DELETEs Access wrote by hand are not repeated -- but
# the rows ARE counted first, so the message to the operator can say what went.

def _numara_copiii(cursor, coloana: str, valoare: int) -> dict:
    conturi = {}
    for tabela, cheie in (("FX_DDF_REV_SA", "linii_a"),
                          ("FX_DDF_REV_SB", "linii_b"),
                          ("FX_DDF_REV_ATT", "atasamente")):
        cursor.execute(f"SELECT COUNT(*) AS n FROM {tabela} WHERE {coloana} = %s", (valoare,))
        conturi[cheie] = int((cursor.fetchone() or {}).get("n") or 0)
    return conturi


def _sterge_revizie(cursor, idrev: int) -> dict:
    """The port of `FX_Stergere_Revizie`. The SalariiH reset is not ported (decision D14)."""
    cursor.execute("SELECT IDREV, IDDF, CodAngajament, NumarRev FROM FX_DDF_REV "
                   " WHERE IDREV = %s", (idrev,))
    rev = cursor.fetchone()
    if rev is None:
        raise DateInvalide(f"Revizia {idrev} nu există.")

    conturi = _numara_copiii(cursor, "IDREV", idrev)
    cursor.execute("UPDATE FX_Rezervari SET IDREV = NULL, AreDDF = FALSE WHERE IDREV = %s",
                   (idrev,))
    eliberate = cursor.rowcount
    cursor.execute("DELETE FROM FX_DDF_REV WHERE IDREV = %s", (idrev,))

    return {
        "iddf": _int0(rev.get("IDDF")),
        "idrev": idrev,
        "cod": _txt(rev.get("CodAngajament")),
        "revizii": 1,
        "linii_a": conturi["linii_a"],
        "linii_b": conturi["linii_b"],
        "atasamente": conturi["atasamente"],
        "rezervari_eliberate": eliberate,
        "document_sters": False,
    }


def _sterge_document(cursor, iddf: int) -> dict:
    """The port of `FX_Stergere_DDF`, with one deliberate correction.

    Access reset `FX_Rezervari` for a SINGLE `pIDREV` passed in alongside the document id,
    so deleting a whole document released only one revision's reservations and left the rest
    marked as having a DDF that no longer existed. Here the reset covers EVERY revision of
    the document. The divergence is recorded in the worklog.

    Note also that because `FX_DDF`'s primary key is composite, the final DELETE removes
    EVERY `CUAL` row of that `IDDF`. That is what the Access code did too, and it is right --
    but it does not look like it at a glance.
    """
    cursor.execute("SELECT IDDF, CodAngajament FROM FX_DDF WHERE IDDF = %s LIMIT 1", (iddf,))
    cap = cursor.fetchone()
    if cap is None:
        raise DateInvalide(f"Documentul {iddf} nu există.")

    # The guard. `FX_ORD.IDDF` is a RESTRICT foreign key, so the database would refuse this
    # anyway -- but with an errno nobody can read. This is the readable version of the same
    # refusal, and it fires first.
    cursor.execute("SELECT 1 FROM FX_ORD WHERE IDDF = %s LIMIT 1", (iddf,))
    if cursor.fetchone() is not None:
        raise DateInvalide(
            "Nu se poate șterge complet un DDF cât timp are ORDONANȚĂRI!")

    conturi = _numara_copiii(cursor, "IDDF", iddf)
    cursor.execute("SELECT COUNT(*) AS n FROM FX_DDF_REV WHERE IDDF = %s", (iddf,))
    revizii = int((cursor.fetchone() or {}).get("n") or 0)

    cursor.execute("UPDATE FX_Angajamente SET IDDF = NULL WHERE IDDF = %s", (iddf,))
    cursor.execute(
        "UPDATE FX_Rezervari SET IDREV = NULL, AreDDF = FALSE "
        " WHERE IDREV IN (SELECT IDREV FROM FX_DDF_REV WHERE IDDF = %s)", (iddf,))
    eliberate = cursor.rowcount
    cursor.execute("DELETE FROM FX_DDF WHERE IDDF = %s", (iddf,))

    return {
        "iddf": iddf,
        "idrev": 0,
        "cod": _txt(cap.get("CodAngajament")),
        "revizii": revizii,
        "linii_a": conturi["linii_a"],
        "linii_b": conturi["linii_b"],
        "atasamente": conturi["atasamente"],
        "rezervari_eliberate": eliberate,
        "document_sters": True,
    }


def _intr_o_tranzactie(db_name: str, treaba, eticheta: str):
    """Run `treaba(cursor)` in one transaction and turn the result into a response."""
    conn = None
    try:
        conn = get_kbot_connection(db_name)
        conn.autocommit = False
        cursor = conn.cursor(dictionary=True)
        if not conn.in_transaction:
            conn.start_transaction()
        rezultat = treaba(cursor)
        conn.commit()
        return _json_utf8(rezultat, 200)
    except DateInvalide as e:
        if conn is not None:
            conn.rollback()
        return _json_utf8({"error": str(e)}, 400)
    except Exception as e:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                logger.warning("[forexe.ddf_edit] rollback esuat", exc_info=True)
        logger.error(f"[forexe.ddf_edit] {eticheta}: {e}", exc_info=True)
        return _json_utf8({"error": f"Eroare la ștergere: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()


@forexe_bp.route("/api/forexe/ddf/rev/<int:idrev>", methods=["DELETE"])
@require_session
def delete_ddf_revizie(idrev):
    """Delete one revision."""
    return _intr_o_tranzactie(g.session.db_name,
                              lambda c: _sterge_revizie(c, idrev),
                              f"rev DELETE {idrev}")


@forexe_bp.route("/api/forexe/ddf/<int:iddf>", methods=["DELETE"])
@require_session
def delete_ddf(iddf):
    """Delete the whole document.

    The int converter keeps this rule from shadowing the read route `GET /api/forexe/ddf`
    of slice 0020 (different method AND different rule), and from colliding with
    `/api/forexe/ddf/pdf/<idrev>` in pdf.py, whose second segment is not an integer.
    """
    return _intr_o_tranzactie(g.session.db_name,
                              lambda c: _sterge_document(c, iddf),
                              f"ddf DELETE {iddf}")


@forexe_bp.route("/api/forexe/ddf/<int:iddf>/luna/<int:an>/<int:luna>", methods=["DELETE"])
@require_session
def delete_ddf_luna(iddf, an, luna):
    """Delete a month's revisions.

    The port of `FX_Stergere_Revizii`, INCLUDING its ending: when the month holds every
    revision the document has, the DOCUMENT goes rather than the last revision -- otherwise
    an empty document would be left behind pointing at nothing.
    """
    if not 1 <= luna <= 12:
        return _json_utf8({"error": f"Luna {luna} nu este validă."}, 400)

    def treaba(cursor):
        cursor.execute(
            "SELECT IDREV FROM FX_DDF_REV "
            " WHERE IDDF = %s AND YEAR(DataRev) = %s AND MONTH(DataRev) = %s "
            " ORDER BY IDREV", (iddf, an, luna))
        ale_lunii = [int(r["IDREV"]) for r in cursor.fetchall()]
        if not ale_lunii:
            raise DateInvalide(
                f"Documentul {iddf} nu are revizii în luna {luna}/{an}.")

        cursor.execute("SELECT COUNT(*) AS n FROM FX_DDF_REV WHERE IDDF = %s", (iddf,))
        total = int((cursor.fetchone() or {}).get("n") or 0)

        if total == len(ale_lunii):
            # Every revision the document has is in this month -> the document goes.
            return _sterge_document(cursor, iddf)

        agregat = {"iddf": iddf, "idrev": 0, "cod": "", "revizii": 0,
                   "linii_a": 0, "linii_b": 0, "atasamente": 0,
                   "rezervari_eliberate": 0, "document_sters": False}
        for idrev in ale_lunii:
            unul = _sterge_revizie(cursor, idrev)
            agregat["cod"] = unul["cod"] or agregat["cod"]
            for cheie in ("revizii", "linii_a", "linii_b", "atasamente",
                          "rezervari_eliberate"):
                agregat[cheie] += unul[cheie]
        return agregat

    return _intr_o_tranzactie(g.session.db_name, treaba, f"luna DELETE {iddf} {an}/{luna}")


# =========================================================================================
# ATTACHMENT BYTES -- phase two of the save
# =========================================================================================

def _tip_fisier(octeti: bytes, nume: str):
    """The MIME type from the first bytes, with the extension breaking the two ties.

    A ZIP container is a .docx or an .xlsx and the header cannot tell which; an OLE2 compound
    file is a .doc or an .xls, likewise. In both cases the extension decides, and an unknown
    extension keeps the container type rather than guessing.
    """
    for semnatura, tip in SEMNATURI_FISIER:
        if octeti.startswith(semnatura):
            ext = ("." + nume.rsplit(".", 1)[-1].lower()) if "." in nume else ""
            if tip == "application/zip":
                return EXTENSII_OOXML.get(ext, tip)
            if tip == "application/x-ole-storage":
                return EXTENSII_OLE.get(ext, tip)
            return tip
    return None


def _att_exista(cursor, idrevatt: int) -> bool:
    cursor.execute("SELECT 1 FROM FX_DDF_REV_ATT WHERE IdRevAtt = %s LIMIT 1", (idrevatt,))
    return cursor.fetchone() is not None


def _att_sha(cursor, idrevatt: int):
    cursor.execute("SELECT Sha256 FROM FX_DDF_REV_ATT_IMG WHERE IdRevAtt = %s LIMIT 1",
                   (idrevatt,))
    rand = cursor.fetchone()
    return rand["Sha256"] if rand else None


@forexe_bp.route("/api/forexe/ddf/att/<int:idrevatt>/imagine", methods=["GET"])
@require_session
def get_ddf_att_imagine(idrevatt):
    """The bytes of one attachment, or 404 when the row has none yet."""
    db_name = g.session.db_name
    conn = None
    try:
        conn = get_kbot_connection(db_name)
        cursor = conn.cursor(dictionary=True)
        if not _are_att_img(cursor, db_name):
            return _json_utf8(
                {"error": "Tabela FX_DDF_REV_ATT_IMG nu există pe această bază."}, 501)

        cursor.execute(
            "SELECT NumeFisier, TipMime, Dimensiune, Sha256, Continut "
            "  FROM FX_DDF_REV_ATT_IMG WHERE IdRevAtt = %s LIMIT 1", (idrevatt,))
        rand = cursor.fetchone()
        if rand is None:
            return _json_utf8({"error": "Fișierul nu are conținut stocat."}, 404)

        raspuns = current_app.response_class(
            rand["Continut"], status=200,
            mimetype=_txt(rand["TipMime"]) or "application/octet-stream")
        raspuns.headers[H_SHA] = _txt(rand["Sha256"])
        raspuns.headers["Content-Length"] = str(_int0(rand["Dimensiune"]))
        return raspuns
    except Exception as e:
        logger.error(f"[forexe.ddf_edit] imagine GET {idrevatt}: {e}", exc_info=True)
        return _json_utf8({"error": f"Eroare la citirea fișierului: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()


@forexe_bp.route("/api/forexe/ddf/att/<int:idrevatt>/imagine", methods=["PUT"])
@require_session
def put_ddf_att_imagine(idrevatt):
    """Replace-or-insert the bytes of one attachment.

    `X-Sha-Precedent` carries what the client believes is stored ("-" for "nothing yet"), so
    two operators cannot overwrite each other without noticing.
    """
    octeti = request.get_data(cache=False) or b""
    if not octeti:
        return _json_utf8({"error": "Cererea nu conține niciun octet."}, 400)
    if len(octeti) > MAX_FISIER_BYTES:
        return _json_utf8(
            {"error": f"Fișierul depășește limita de {MAX_FISIER_BYTES // (1024 * 1024)} MB."},
            413)

    # The file name is SENT, not derived: it is the operator's choice. Same header as the
    # ORD upload path (`X-Nume-Fisier`), so the two families stay one convention.
    nume = (request.headers.get(H_NUME) or "").strip()
    if not nume:
        return _json_utf8({"error": f"Antet lipsă: {H_NUME}."}, 400)

    mime = _tip_fisier(octeti, nume)
    if mime is None:
        return _json_utf8(
            {"error": "Tipul fișierului nu este recunoscut. Sunt acceptate imagini "
                      "(bmp, jpg, png, ico, gif), documente (doc, docx, pdf) și tabele "
                      "(xls, xlsx)."}, 415)

    sha_precedent = request.headers.get(H_SHA_PREC)
    if sha_precedent is None:
        return _json_utf8({"error": f"Antetul {H_SHA_PREC} lipsește."}, 428)

    db_name = g.session.db_name
    conn = None
    try:
        conn = get_kbot_connection(db_name)
        conn.autocommit = False
        cursor = conn.cursor(dictionary=True)
        if not conn.in_transaction:
            conn.start_transaction()

        if not _are_att_img(cursor, db_name):
            conn.rollback()
            return _json_utf8(
                {"error": "Tabela FX_DDF_REV_ATT_IMG nu există pe această bază. "
                          "Rulați sql/0051_ddf_rev_att_img.sql."}, 501)
        if not _att_exista(cursor, idrevatt):
            conn.rollback()
            return _json_utf8({"error": f"Rândul de atașament {idrevatt} nu există."}, 404)

        sha_stocat = _att_sha(cursor, idrevatt)
        asteptat = NO_ROW if sha_stocat is None else sha_stocat
        if sha_precedent != asteptat:
            conn.rollback()
            return _json_utf8(
                {"error": "Fișierul a fost modificat de altcineva între timp."}, 409)

        sha_server = _sha256(octeti)
        # ON DUPLICATE KEY UPDATE rests on the UNIQUE index on IdRevAtt: one attachment row
        # is one file, so a re-upload REPLACES rather than appending a second row.
        cursor.execute(
            "INSERT INTO FX_DDF_REV_ATT_IMG "
            "       (IdRevAtt, NumeFisier, TipMime, Dimensiune, Sha256, Continut, DataModif) "
            "VALUES (%s, %s, %s, %s, %s, %s, NOW()) "
            "ON DUPLICATE KEY UPDATE "
            "       NumeFisier = VALUES(NumeFisier), TipMime = VALUES(TipMime), "
            "       Dimensiune = VALUES(Dimensiune), Sha256 = VALUES(Sha256), "
            "       Continut = VALUES(Continut), DataModif = NOW()",
            (idrevatt, nume[:255], mime, len(octeti), sha_server, octeti))
        conn.commit()

        logger.info("[forexe.ddf_edit] %s: fisier att=%s salvat (%s octeti, sha=%s..., %s)",
                    db_name, idrevatt, len(octeti), sha_server[:8], mime)
        return _json_utf8({"id_rev_att": idrevatt, "sha256": sha_server,
                           "nume_fisier": nume[:255], "tip_mime": mime,
                           "dimensiune": len(octeti)}, 200)
    except Exception as e:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                logger.warning("[forexe.ddf_edit] rollback esuat", exc_info=True)
        logger.error(f"[forexe.ddf_edit] imagine PUT {idrevatt}: {e}", exc_info=True)
        return _json_utf8({"error": f"Eroare la salvarea fișierului: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()


@forexe_bp.route("/api/forexe/ddf/att/<int:idrevatt>/imagine", methods=["DELETE"])
@require_session
def delete_ddf_att_imagine(idrevatt):
    """Delete the bytes, leaving the attachment row itself in place."""
    db_name = g.session.db_name
    conn = None
    try:
        conn = get_kbot_connection(db_name)
        cursor = conn.cursor(dictionary=True)
        if not _are_att_img(cursor, db_name):
            return _json_utf8(
                {"error": "Tabela FX_DDF_REV_ATT_IMG nu există pe această bază."}, 501)
        cursor.execute("DELETE FROM FX_DDF_REV_ATT_IMG WHERE IdRevAtt = %s", (idrevatt,))
        conn.commit()
        return _json_utf8({"id_rev_att": idrevatt, "sters": cursor.rowcount > 0}, 200)
    except Exception as e:
        logger.error(f"[forexe.ddf_edit] imagine DELETE {idrevatt}: {e}", exc_info=True)
        return _json_utf8({"error": f"Eroare la ștergerea fișierului: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()
