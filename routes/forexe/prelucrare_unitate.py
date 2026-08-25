# routes/forexe/prelucrare_unitate.py
"""
Resolving the UNIT (and, through it, the classification) of a FOREXE indicator --
step 2 of the ingest, and the operator round trip that happens when the answer is
not unique. Slice 0048-02; plan docs/PLAN_ForexeIngest.md sections 5.2a / 7 step 2,
as corrected by decision D17 of slice 0048-01.

WHAT THIS PORTS
---------------
`Obtine_IdUnitate_Din` in mdl_FX_Tasks_Receive_DWN, verbatim:

    SELECT CG.IdUnitate, Cai.AlteDetalii, Count(CG.IdUnitate) as Cnt
      FROM ClasificatiiG AS CG INNER JOIN Cai ON CG.IdUnitate = Cai.IdUnitate
     WHERE SS = '<SS>' AND ClsfE = '<Right(ClsfE,8) without dots>'
       AND Cai.DC = '<DC()>'
     GROUP BY CG.IdUnitate, Cai.AlteDetalii

    0 rows  -> FX_IdUnitate = -1, returns False -> the caller GoTo Iesire, so the
               whole of Prelucrare_Indicatori ends WITHOUT success. A blocking error.
    1 row   -> that IdUnitate.
    n rows  -> DoCmd.OpenForm "FX_Unitate", ..., acDialog  -- Access ASKED THE
               OPERATOR, modally, every single time, and used whatever they picked.

So the ambiguity is not new and it is not a defect: the Access system had a dialog
for it. What K-BOT adds is that the question travels over HTTP (the server cannot
open a window on the operator's screen) and that the operator may tick a box to
stop being asked for that one pair.

THE MariaDB TRANSLATION
-----------------------
  * `ClasificatiiG` (an Access query over Clasificatii) -> `Clasificatii`.
  * `Cai` was the Access registry of unit databases; its `DC` column named the
    database. On MariaDB one database IS one unit set, so the `Cai.DC = DC()`
    filter is implicit -- we are already connected to that database -- and the
    INNER JOIN moves to `Unitati`, which is the per-database unit list.
  * `Cai.AlteDetalii` ('LOCAL' / 'ISJ' / 'VEN' ...) was NOT migrated: see
    docs/MAPARE_NOMENCLATOARE.md section 2, where only IdUnitate / NumeUnitate /
    SURSA reach `Unitati`. The readable label is therefore `Unitati.Detalii`
    (= `cai.NumeUnitate`), which is the better label anyway -- it is the same text
    the operator picks from in the migrator's unit list.
  * The INNER JOIN is kept deliberately: `Clasificatii.IdUnitate` is NULLable, and
    a classification with no unit cannot answer "which unit", so it drops out --
    exactly as it dropped out of the Access join.

No `LIMIT 1` anywhere in this file. The read routes use `LIMIT 1` over the same
nomenclator because picking either duplicate only changes a label; here it would
attach an indicator to the wrong subunit, and nothing would surface for months.

HOW TO READ THIS FILE (the operator asked for this explicitly -- comments say WHAT
a line does, not only why; the reader knows SQL and VB.NET, not Python):

  * `Tuple[str, str]`      an immutable pair. Used as a dictionary KEY here -- a
                           list cannot be one, a tuple can.
  * `dict.get(k)`          returns None instead of raising when the key is absent.
  * `set`                  a dictionary with keys and no values; `x in s` is fast.
  * `raise X(...) from err` re-raise as a different type while KEEPING the original
                           exception chained underneath. Never loses the cause.
  * `%s`                   the SQL parameter placeholder. The driver escapes the
                           value. Never build SQL with f-strings.
"""
import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import mysql.connector

logger = logging.getLogger(__name__)

# MariaDB error number for "table does not exist". The remembered-choice table is
# created by sql/0048_alegeri_unitate.sql, which is applied per database; until it
# has been run this is the error the driver raises.
_ER_NO_SUCH_TABLE = 1146

# The reason code the client keys on. It travels in the "reason" field of the error
# body, which KBot.Api.ApiException already carries as `Reason`.
REASON_UNIT_CHOICE = "ALEGERE_UNITATE"

# Operator-facing text (Romanian, literal diacritics -- house rule). The client
# shows the dialog and never these strings verbatim, but a 409 read by anything
# else must still say what happened.
MSG_UNIT_CHOICE = (
    "O clasificație se potrivește cu mai multe unități. "
    "Alegeți unitatea și trimiteți din nou."
)
MSG_TABLE_MISSING = (
    "Tabela FX_Alegeri_Unitate lipsește din această bază. "
    "Rulați sql/0048_alegeri_unitate.sql pe ea, apoi reluați."
)


class UnitChoiceRequired(Exception):
    """
    Raised when at least one (SS, ClsfE) pair matched more than one unit and the
    request carried no answer for it -- neither an explicit choice nor a
    remembered one.

    Carries the full list so the operator answers ALL of them in one pass. The
    alternative (raise on the first, ask, resend, hit the second) would be one
    round trip per ambiguous pair, and the transaction would be opened and rolled
    back once per question.
    """

    def __init__(self, pending: List[dict]):
        # The message is for logs; the route builds the operator-facing body.
        super().__init__(f"{len(pending)} clasificatii au nevoie de alegerea unitatii")
        self.pending = pending


class UnitChoiceTableMissing(Exception):
    """
    Raised when FX_Alegeri_Unitate does not exist in this database.

    Deliberately NOT degraded into "no remembered choices". A silent fallback
    would mean the tick box appears to work, the operator ticks it, and the next
    run asks again -- with no way to tell why. Loud is better: the message names
    the file to run.

    Note WHEN this can happen: only after a real ambiguity has been found. A
    database that never hits one never touches the table, so an un-migrated
    database keeps working until the day the question actually arises.
    """


# ---------------------------------------------------------------------------
# Candidates
# ---------------------------------------------------------------------------
# GROUP BY, not DISTINCT, to stay shaped like the Access query. The nomenclator has
# real duplicates on (IdClsfAcc, IdUnitate) -- see MAPARE_NOMENCLATOARE.md 3.2 --
# so several Clasificatii rows can carry the same unit; they must collapse to one
# candidate, or the operator would be offered the same unit twice.
_CANDIDATES_SQL = (
    "SELECT C.IdUnitate AS IdUnitate, U.Detalii AS Detalii, "
    "U.SursaSector AS SursaSector, U.CodProgram AS CodProgram, "
    "COUNT(*) AS Cnt "
    "FROM Clasificatii C "
    "INNER JOIN Unitati U ON U.IdUnitate = C.IdUnitate "
    "WHERE C.SS = %s AND C.ClsfE = %s "
    "GROUP BY C.IdUnitate, U.Detalii, U.SursaSector, U.CodProgram "
    "ORDER BY U.Detalii, C.IdUnitate"
)


def find_unit_candidates(cursor, ss: str, clsf_e: str) -> List[dict]:
    """
    Every unit this (SS, ClsfE) pair can mean, most readable field first.

    `cursor` must be a dictionary cursor (conn.cursor(dictionary=True)), so each
    row comes back as a dict keyed by column name rather than a positional tuple.
    """
    cursor.execute(_CANDIDATES_SQL, (ss, clsf_e))
    rows = cursor.fetchall()
    # Rebuild each row into the wire shape (ASCII keys -- rule 0 applies on BOTH
    # sides of the wire). `Cnt` stays out: it is how many nomenclator rows produced
    # the candidate, which is a data-quality fact, not something to choose between.
    return [
        {
            "id_unitate": int(r["IdUnitate"]),
            "detalii": r["Detalii"] or "",
            "sursa_sector": r["SursaSector"] or "",
            "cod_program": r["CodProgram"] or "",
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Remembered choices
# ---------------------------------------------------------------------------
_REMEMBERED_SELECT_SQL = "SELECT SS, ClsfE, IdUnitate FROM FX_Alegeri_Unitate"

# Re-ticking the box for a pair REPLACES the answer instead of adding a second row;
# the unique key on (SS, ClsfE) is what makes that possible. UN and DataAlegere are
# refreshed too, so the trail always names whoever set the answer in force.
_REMEMBERED_UPSERT_SQL = (
    "INSERT INTO FX_Alegeri_Unitate (SS, ClsfE, IdUnitate, UN, DataAlegere) "
    "VALUES (%s, %s, %s, %s, %s) "
    "ON DUPLICATE KEY UPDATE IdUnitate = VALUES(IdUnitate), "
    "UN = VALUES(UN), DataAlegere = VALUES(DataAlegere)"
)


def load_remembered_choices(cursor) -> Dict[Tuple[str, str], int]:
    """
    Read the whole remembered table into a dictionary keyed by (SS, ClsfE).

    Reading all of it is deliberate: the table holds one row per pair an operator
    ever ticked, so it is tiny, and one round trip beats one per ambiguous pair.
    """
    try:
        cursor.execute(_REMEMBERED_SELECT_SQL)
    except mysql.connector.Error as err:
        if getattr(err, "errno", None) == _ER_NO_SUCH_TABLE:
            # Translate, do not swallow: `from err` keeps the driver error chained.
            raise UnitChoiceTableMissing(MSG_TABLE_MISSING) from err
        raise
    return {(r["SS"], r["ClsfE"]): int(r["IdUnitate"]) for r in cursor.fetchall()}


def save_remembered_choice(cursor, ss: str, clsf_e: str, id_unitate: int,
                           un: str, moment: Optional[datetime] = None) -> None:
    """
    Store (or replace) the answer for one pair.

    Runs inside the ingest transaction, on purpose. If a later step fails and the
    whole run rolls back, this row goes with it and the operator is asked again
    next time. The alternative -- committing the choice separately -- would leave
    a remembered answer behind for a save that never happened, which is the one
    outcome the "nothing half-written" rule exists to prevent.
    """
    try:
        cursor.execute(
            _REMEMBERED_UPSERT_SQL,
            (ss, clsf_e, int(id_unitate), un or "", moment or datetime.now()),
        )
    except mysql.connector.Error as err:
        if getattr(err, "errno", None) == _ER_NO_SUCH_TABLE:
            raise UnitChoiceTableMissing(MSG_TABLE_MISSING) from err
        raise


# ---------------------------------------------------------------------------
# Choices arriving in the request
# ---------------------------------------------------------------------------
def normalize_supplied_choices(raw) -> Dict[Tuple[str, str], Tuple[int, bool]]:
    """
    Turn the request's `alegeri` array into {(SS, ClsfE): (IdUnitate, retine)}.

    Shape accepted (ASCII keys on both sides of the wire, rule 0):

        [ { "ss": "02E", "clsfe": "200101", "id_unitate": 76, "retine": true } ]

    A malformed entry raises rather than being skipped: a choice we cannot read is
    not the same as no choice, and quietly dropping it would re-ask the operator a
    question they already answered.
    """
    result: Dict[Tuple[str, str], Tuple[int, bool]] = {}
    if not raw:
        return result
    if not isinstance(raw, list):
        raise ValueError("Câmpul «alegeri» trebuie să fie o listă.")
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"«alegeri»[{i}] nu este un obiect.")
        ss = str(item.get("ss") or "").strip()
        clsf_e = str(item.get("clsfe") or "").strip()
        if ss == "" or clsf_e == "":
            raise ValueError(f"«alegeri»[{i}]: «ss» și «clsfe» sunt obligatorii.")
        try:
            # int() on a str or a float both work; on None or "" it raises, which
            # is why the guard above runs first.
            id_unitate = int(item.get("id_unitate"))
        except (TypeError, ValueError) as err:
            raise ValueError(
                f"«alegeri»[{i}]: «id_unitate» lipsește sau nu este un număr."
            ) from err
        # bool(None) is False, bool(0) is False, bool("false") would be True --
        # so accept only a real boolean or the two integers, never a string.
        retine_raw = item.get("retine", False)
        if isinstance(retine_raw, bool):
            retine = retine_raw
        elif retine_raw in (0, 1):
            retine = bool(retine_raw)
        else:
            raise ValueError(f"«alegeri»[{i}]: «retine» trebuie să fie true sau false.")
        result[(ss, clsf_e)] = (id_unitate, retine)
    return result


# ---------------------------------------------------------------------------
# The resolution pass
# ---------------------------------------------------------------------------
def resolve_units(cursor, indicators: List[dict], supplied, un: str,
                  warnings: List[str]) -> Dict[Tuple[str, str], int]:
    """
    Resolve EVERY indicator's unit before anything is written.

    `indicators` is a list of dicts, one per row of TabelIndicatori_results, each
    carrying at least:
        cod_indicator, ss, clsf_sal, clsf_e, clsf_raw

    Returns {(SS, ClsfE): IdUnitate} covering every pair in the input.

    Raises:
        UnitChoiceRequired    -- at least one pair is ambiguous and unanswered
        ValueError            -- a pair matched no unit at all, or an answer named
                                 a unit that is not among the candidates
        UnitChoiceTableMissing-- an ambiguity was found but the memory table is not
                                 there (only reachable via the ambiguous branch)

    WHY ALL OF THEM, UP FRONT: the Access version resolved a unit in the middle of
    the per-indicator write loop, so an ambiguity on the fourth indicator asked its
    question after three had already been written. Inside one transaction that is
    not a difference the operator can see -- a rollback undoes those three -- but
    collecting the questions first means the operator answers once for the whole
    angajament instead of once per interruption.
    """
    remembered: Optional[Dict[Tuple[str, str], int]] = None   # loaded lazily
    resolved: Dict[Tuple[str, str], int] = {}
    pending: List[dict] = []
    # Which indicator codes share each pair -- so the question can name them all.
    by_pair: Dict[Tuple[str, str], List[str]] = {}
    for ind in indicators:
        by_pair.setdefault((ind["ss"], ind["clsf_e"]), []).append(ind["cod_indicator"])

    for ind in indicators:
        key = (ind["ss"], ind["clsf_e"])
        # Already answered on an earlier indicator sharing the same pair.
        if key in resolved or any(p["ss"] == key[0] and p["clsfe"] == key[1]
                                  for p in pending):
            continue

        candidates = find_unit_candidates(cursor, ind["ss"], ind["clsf_e"])

        if len(candidates) == 0:
            # Access: FX_IdUnitate = -1, False, GoTo Iesire -- the step failed and
            # the orchestrator stopped. Same here, with a message that names the
            # pair instead of a silent exit.
            raise ValueError(
                f"Clasificația «{ind['clsf_raw']}» (SS {ind['ss']}, ClsfE "
                f"{ind['clsf_e']}) nu aparține niciunei unități din această bază. "
                f"Indicator: {ind['cod_indicator']}."
            )

        if len(candidates) == 1:
            resolved[key] = candidates[0]["id_unitate"]
            continue

        # --- ambiguous from here on --------------------------------------
        allowed = {c["id_unitate"] for c in candidates}

        # 1. An answer that came with THIS request wins over anything stored.
        choice = supplied.get(key) if supplied else None
        if choice is not None:
            id_unitate, retine = choice
            if id_unitate not in allowed:
                # Never trust a client-supplied key blindly: a stale dialog or a
                # hand-made request could name a unit this pair cannot mean.
                raise ValueError(
                    f"Unitatea aleasă ({id_unitate}) nu este printre cele posibile "
                    f"pentru clasificația «{ind['clsf_raw']}»."
                )
            resolved[key] = id_unitate
            if retine:
                save_remembered_choice(cursor, key[0], key[1], id_unitate, un)
            continue

        # 2. Otherwise, an answer the operator ticked "do not ask again" for.
        if remembered is None:
            remembered = load_remembered_choices(cursor)
        stored = remembered.get(key)
        if stored is not None:
            if stored in allowed:
                resolved[key] = stored
                continue
            # The stored answer names a unit this pair no longer matches -- the
            # nomenclator changed under it. Ask again rather than write it, and
            # say so, because a remembered answer going stale is exactly the
            # failure mode the tick box risks.
            warnings.append(
                f"Alegerea reținută pentru {key[0]} / {key[1]} arăta către unitatea "
                f"{stored}, care nu mai este printre cele posibile. Se întreabă din nou."
            )

        # 3. Nothing to go on -- the operator has to answer.
        pending.append({
            "ss": key[0],
            "clsfe": key[1],
            "clsf": ind["clsf_raw"],
            "cod_indicator": ind["cod_indicator"],
            "indicatori": by_pair.get(key, []),
            "unitati": candidates,
        })

    if pending:
        raise UnitChoiceRequired(pending)
    return resolved


# ---------------------------------------------------------------------------
# The second lookup: the classification id, inside the resolved unit
# ---------------------------------------------------------------------------
# Access: FX_DicClsf("IdClsf", "ClsfSal", IdUnit)(clsfRaw) -- a dictionary of that
# unit's classifications keyed by ClsfSal. D7 settles which id it is on MariaDB:
# `IdClsfAcc`, the Access id, because that is what FX_Indicatori.IdClsf holds and
# what every read route joins on.
_ID_CLSF_SQL = (
    "SELECT DISTINCT IdClsfAcc FROM Clasificatii "
    "WHERE IdUnitate = %s AND ClsfSal = %s"
)


def find_id_clsf_acc(cursor, id_unitate: int, clsf_sal: str,
                     clsf_raw: str, cod_indicator: str,
                     warnings: List[str]) -> Optional[int]:
    """
    The `IdClsfAcc` for this ClsfSal inside this unit, or None.

    None is NOT an error -- decision D19. The Access line is

        If Not IsNull(IdClsf) Then RC!IdClsf = IdClsf

    so a classification that does not resolve leaves the column unwritten and the
    row is still saved. Ported, but with a warning instead of silence.

    DISTINCT because the nomenclator has real duplicates on (IdClsfAcc, IdUnitate);
    several rows carrying the SAME id are normal and collapse here. Several rows
    carrying DIFFERENT ids are not normal -- the Access dictionary would have kept
    whichever it read last, arbitrarily, and that is the one behaviour worth not
    porting, because "arbitrary" here means the indicator lands on the wrong
    classification.
    """
    cursor.execute(_ID_CLSF_SQL, (int(id_unitate), clsf_sal))
    rows = cursor.fetchall()
    if not rows:
        warnings.append(
            f"Clasificația «{clsf_raw}» nu a fost găsită la unitatea {id_unitate}; "
            f"indicatorul {cod_indicator} rămâne fără clasificație."
        )
        return None
    if len(rows) > 1:
        ids = ", ".join(str(r["IdClsfAcc"]) for r in rows)
        raise ValueError(
            f"Clasificația «{clsf_raw}» are mai multe coduri la unitatea "
            f"{id_unitate} ({ids}). Nomenclatorul trebuie corectat."
        )
    return int(rows[0]["IdClsfAcc"])
