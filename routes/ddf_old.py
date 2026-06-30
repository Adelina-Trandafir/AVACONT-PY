# routes/ddf.py
import logging
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional, Set, Tuple, Union
import uuid

from flask import Blueprint, jsonify, request
from utils.database import get_db_connection
from utils.security import require_api_key

ddf_bp = Blueprint("ddf", __name__)
logger = logging.getLogger(__name__)

ColCache = Dict[str, Set[str]]

# ---------------------------------------------------------------------------
# Switch logging verbose (debug). Seteaza False in productie.
# ---------------------------------------------------------------------------
DEBUG_LOG: bool = True

def _dlog(msg: str) -> None:
    """Log verbose doar daca DEBUG_LOG este activ."""
    if DEBUG_LOG:
        logger.debug(msg)

"""
Refactorizare save_complex / update_complex + endpoint confirm.

Flux:
  1. Access apeleaza /api/ddf/save_staging sau /api/ddf/update_staging
     -> Flask scrie in stg_* si returneaza token UUID
  2. Access salveaza local (tranzactie DAO)
  3. Access trimite /api/ddf/confirm cu token + status
     -> OK:       Flask muta din stg_* in tabele reale
     -> FAIL:     Flask sterge stg_* (CASCADE curata tot)
     -> FAIL_MOD: Flask marcheaza FAIL_MOD, log pentru reconciliere
"""

# ---------------------------------------------------------------------------
# SECTIUNEA 1 — CONSTANTE
# ---------------------------------------------------------------------------

MAX_PAYLOAD_BYTES    = 2 * 1024 * 1024   # 2 MB
MAX_PARTS            = 50
MAX_TBLS             = 500
MAX_ATTS             = 150
MAX_DOCS             = 150
MAX_DEADLOCK_RETRIES = 3
DEADLOCK_RETRY_SLEEP = 0.2               # seconds (se inmulteste cu attempt)

ERRNO_DEADLOCK       = 1213
ERRNO_LOCK_TIMEOUT   = 1205

# ===========================================================================
# SECTIUNEA 2 — PARSARE STRICTA
# ===========================================================================
def _strict_bool(v, field: str) -> int:
    """
    Parseaza un boolean Access (0/1/-1).
    Access trimite -1 pentru True, 0 pentru False.
    Returneaza 1 sau 0. Raise daca null/gol/invalid.
    """
    if v is None or (isinstance(v, str) and v.strip() == ""):
        raise ValueError(f"Camp '{field}': null sau gol (0/1 obligatoriu)")
    try:
        result = int(v)
    except (TypeError, ValueError):
        raise ValueError(f"Camp '{field}': '{v}' nu este 0/1 valid")
    if result == -1:   # Access TRUE
        return 1
    if result not in (0, 1):
        raise ValueError(f"Camp '{field}': {result} trebuie sa fie 0 sau 1")
    return result


def _strict_int(v, field: str) -> int:
    """Parseaza int, reject null/gol/non-numeric."""
    if v is None or (isinstance(v, str) and v.strip() == ""):
        raise ValueError(f"Camp '{field}': null sau gol (int obligatoriu)")
    try:
        return int(v)
    except (TypeError, ValueError):
        raise ValueError(f"Camp '{field}': '{v}' nu este int valid")


def _strict_pos_int(v, field: str) -> int:
    """Parseaza int strict pozitiv (> 0). Reject 0, negativ, null."""
    result = _strict_int(v, field)
    if result <= 0:
        raise ValueError(f"Camp '{field}': {result} trebuie sa fie > 0")
    return result


def _strict_float(v, field: str) -> float:
    """Parseaza float, reject null/gol/non-numeric."""
    if v is None or (isinstance(v, str) and v.strip() == ""):
        raise ValueError(f"Camp '{field}': null sau gol (float obligatoriu)")
    try:
        return float(v)
    except (TypeError, ValueError):
        raise ValueError(f"Camp '{field}': '{v}' nu este float valid")


def _strict_str(v, field: str) -> str:
    """Parseaza string, reject null. String gol admis."""
    if v is None:
        raise ValueError(f"Camp '{field}': null (string obligatoriu)")
    return str(v)


def _strict_str_nonempty(v, field: str) -> str:
    """Parseaza string non-gol (dupa strip). Reject null si whitespace-only."""
    s = _strict_str(v, field)
    if s.strip() == "":
        raise ValueError(f"Camp '{field}': string gol (valoare obligatorie)")
    return s


def _opt_int(v, field: str) -> Optional[int]:
    """
    Camp int optional (FK nullable):
      None   → NULL (FK neset)
      0      → NULL (FK neset — conventia Access pentru camp gol)
      int    → int
      string gol → raise (trimite null explicit, nu string gol)
      non-numeric → raise
    """
    if v is None:
        return None
    if isinstance(v, str) and v.strip() == "":
        raise ValueError(
            f"Camp optional '{field}': string gol invalid — trimite null sau int"
        )
    try:
        result = int(v)
        return result if result != 0 else None
    except (TypeError, ValueError):
        raise ValueError(f"Camp optional '{field}': '{v}' nu este int valid")


def _opt_str(v) -> Optional[str]:
    """None ramane None. Orice altceva devine str."""
    return None if v is None else str(v)

# ============================================================
# HELPERS
# ============================================================
def _insert_staging(cursor, token: str, tip: str, data: dict) -> None:
    ddf  = data['ddf']
    rev  = data['rev']
    revA = data.get('revA', [])
    revB = data.get('revB', [])
    att  = data.get('att',  [])

    _dlog(f"[insert_staging] token={token} tip={tip} "
          f"IDDF={ddf.get('IDDF')} IDREV={rev.get('IDREV')} "
          f"revA={len(revA)} revB={len(revB)} att={len(att)}")

    cursor.execute("""
        INSERT INTO stg_DocFund (
            Token, TipOperatie,
            IDDF, IdUnitate, IdPartener, CodPartener, CodAngajament, Cual,
            DataCreare, DataDef, ObiectDDF, Program, SS, Comp, Stare,
            PartAng, DC, Incarcat, Preluat, Salarii
        ) VALUES (
            %s, %s,
            %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s
        )
    """, (
        token, tip,
        ddf['IDDF'],        ddf['IdUnitate'],   ddf.get('IdPartener'),
        ddf.get('CodPartener'), ddf.get('CodAngajament'), ddf.get('Cual'),
        ddf.get('DataCreare'),  ddf.get('DataDef'),       ddf.get('ObiectDDF'),
        ddf.get('Program'),     ddf.get('SS'),             ddf.get('Comp'),
        ddf.get('Stare'),       ddf.get('PartAng'),        ddf.get('DC'),
        ddf.get('Incarcat'),    ddf.get('Preluat'),        ddf.get('Salarii'),
    ))
    _dlog(f"[insert_staging] stg_DocFund OK")

    cursor.execute("""
        INSERT INTO stg_Revizii (
            Token, IDREV, IDDF, NumarRev, DataRev,
            Desc_Scurta, Desc_Lunga, Desc_Lunga_ANSI,
            DC, CodAngajament, Tip, Incarcat, Preluat
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (
        token,
        rev['IDREV'],           rev['IDDF'],          rev.get('NumarRev'),
        rev.get('DataRev'),     rev.get('Desc_Scurta'), rev.get('Desc_Lunga'),
        rev.get('Desc_Lunga_ANSI'), rev.get('DC'),    rev.get('CodAngajament'),
        rev.get('Tip'),         rev.get('Incarcat'),   rev.get('Preluat'),
    ))
    _dlog(f"[insert_staging] stg_Revizii OK")

    for i, row in enumerate(revA):
        cursor.execute("""
            INSERT INTO stg_RevA (
                Token, TmpID, IdSecA, IDDF, IDREV,
                IdPartener, CodPartener, IdClsf, IdClsfAcc, Clsf,
                ElementFund, ParametriiFund, ValPrec, ValCur, ValTot,
                PartInd, CodAngajament, CodIndicator, Ramane
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            token,
            row.get('TmpID'),
            row.get('IdSecA'),        row['IDDF'],           row['IDREV'],
            row.get('IdPartener'),    row.get('CodPartener'), row.get('IdClsf'),
            row.get('IdClsfAcc'),     row.get('Clsf'),        row.get('ElementFund'),
            row.get('ParametriiFund'), row.get('ValPrec'),   row.get('ValCur'),
            row.get('ValTot'),        row.get('PartInd'),     row.get('CodAngajament'),
            row.get('CodIndicator'),  row.get('Ramane'),
        ))
    _dlog(f"[insert_staging] stg_RevA: {len(revA)} randuri inserate")

    for i, row in enumerate(revB):
        cursor.execute("""
            INSERT INTO stg_RevB (
                Token, TmpID, IdSecB, IDDF, IDREV,
                IdPartener, CodPartener, IdClsf, IdClsfAcc, CodSSI,
                CodAngajament, CodIndicator,
                CA_Anterior, Inf1, CA_Curent, CB_Anterior, Inf2, CB_Curent
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            token,
            row.get('TmpID'),
            row.get('IdSecB'),      row['IDDF'],           row['IDREV'],
            row.get('IdPartener'),  row.get('CodPartener'), row.get('IdClsf'),
            row.get('IdClsfAcc'),   row.get('CodSSI'),      row.get('CodAngajament'),
            row.get('CodIndicator'),
            row.get('CA_Anterior'), row.get('Inf1'),        row.get('CA_Curent'),
            row.get('CB_Anterior'), row.get('Inf2'),        row.get('CB_Curent'),
        ))
    _dlog(f"[insert_staging] stg_RevB: {len(revB)} randuri inserate")

    for i, row in enumerate(att):
        cursor.execute("""
            INSERT INTO stg_Att (
                Token, TmpID, IdRevAtt, IDDF, IDREV, IDVBNET,
                CaleFisier, DateFisier, PrtScr
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            token,
            row.get('TmpID'),
            row.get('IdRevAtt'), row['IDDF'], row['IDREV'], row.get('IDVBNET'),
            row.get('CaleFisier'), row.get('DateFisier'), row.get('PrtScr'),
        ))
    _dlog(f"[insert_staging] stg_Att: {len(att)} randuri inserate")


def _commit_staging_add(cursor, token: str) -> dict:
    _dlog(f"[commit_add] START token={token}")

    cursor.execute("""
        SELECT IdUnitate, IdPartener, CodPartener, CodAngajament, Cual,
               DataCreare, DataDef, ObiectDDF, Program, SS, Comp, Stare,
               PartAng, DC, Incarcat, Preluat, Salarii
        FROM stg_DocFund WHERE Token = %s
    """, (token,))
    ddf_row = cursor.fetchone()

    cursor.execute("""
        INSERT INTO FX_DDF (
            IdUnitate, IdPartener, CodPartener, CodAngajament, Cual,
            DataCreare, DataDef, ObiectDDF, Program, SS, Comp, Stare,
            PartAng, DC, Incarcat, Preluat, Salarii
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """, (
        ddf_row['IdUnitate'], ddf_row['IdPartener'], ddf_row['CodPartener'],
        ddf_row['CodAngajament'], ddf_row['Cual'],
        ddf_row['DataCreare'], ddf_row['DataDef'], ddf_row['ObiectDDF'],
        ddf_row['Program'], ddf_row['SS'], ddf_row['Comp'], ddf_row['Stare'],
        ddf_row['PartAng'], ddf_row['DC'], ddf_row['Incarcat'],
        ddf_row['Preluat'], ddf_row['Salarii'],
    ))
    final_iddf = cursor.lastrowid
    _dlog(f"[commit_add] FX_DDF INSERT → IDDF={final_iddf}")

    cursor.execute("""
        SELECT NumarRev, DataRev,
               Desc_Scurta, Desc_Lunga, Desc_Lunga_ANSI,
               DC, CodAngajament, Tip, Incarcat, Preluat
        FROM stg_Revizii WHERE Token = %s
    """, (token,))
    rev_row = cursor.fetchone()

    final_idrev = None
    if rev_row:
        cursor.execute("""
            INSERT INTO FX_DDF_REV (
                IDDF, NumarRev, DataRev,
                Desc_Scurta, Desc_Lunga, Desc_Lunga_ANSI,
                DC, CodAngajament, Tip, Incarcat, Preluat
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (
            final_iddf,
            rev_row['NumarRev'], rev_row['DataRev'],
            rev_row['Desc_Scurta'], rev_row['Desc_Lunga'], rev_row['Desc_Lunga_ANSI'],
            rev_row['DC'], rev_row['CodAngajament'], rev_row['Tip'],
            rev_row['Incarcat'], rev_row['Preluat'],
        ))
        final_idrev = cursor.lastrowid
        _dlog(f"[commit_add] FX_DDF_REV INSERT → IDREV={final_idrev}")
    else:
        _dlog(f"[commit_add] stg_Revizii: fara revizie, skip FX_DDF_REV")

    reva_map: List[Dict] = []
    revb_map: List[Dict] = []
    att_map:  List[Dict] = []

    if final_idrev is not None:
        cursor.execute("""
            SELECT TmpID, IdPartener, CodPartener, IdClsf, IdClsfAcc, Clsf,
                   ElementFund, ParametriiFund, ValPrec, ValCur, ValTot,
                   PartInd, CodAngajament, CodIndicator, Ramane
            FROM stg_RevA WHERE Token = %s
        """, (token,))
        for row in cursor.fetchall():
            cursor.execute("""
                INSERT INTO FX_DDF_REV_SA (
                    IDDF, IDREV, IdPartener, CodPartener, IdClsf, IdClsfAcc, Clsf,
                    ElementFund, ParametriiFund, ValPrec, ValCur, ValTot,
                    PartInd, CodAngajament, CodIndicator, Ramane
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                final_iddf, final_idrev,
                row['IdPartener'], row['CodPartener'], row['IdClsf'], row['IdClsfAcc'],
                row['Clsf'], row['ElementFund'], row['ParametriiFund'],
                row['ValPrec'], row['ValCur'], row['ValTot'],
                row['PartInd'], row['CodAngajament'], row['CodIndicator'], row['Ramane'],
            ))
            reva_map.append({'TmpID': row['TmpID'], 'IdSecA': cursor.lastrowid})
        _dlog(f"[commit_add] FX_DDF_REV_SA INSERT: {len(reva_map)} randuri")

        cursor.execute("""
            SELECT TmpID, IdPartener, CodPartener, IdClsf, IdClsfAcc, CodSSI,
                   CodAngajament, CodIndicator,
                   CA_Anterior, Inf1, CA_Curent, CB_Anterior, Inf2, CB_Curent
            FROM stg_RevB WHERE Token = %s
        """, (token,))
        for row in cursor.fetchall():
            cursor.execute("""
                INSERT INTO FX_DDF_REV_SB (
                    IDDF, IDREV, IdPartener, CodPartener, IdClsf, IdClsfAcc, CodSSI,
                    CodAngajament, CodIndicator,
                    CA_Anterior, Inf1, CA_Curent, CB_Anterior, Inf2, CB_Curent
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                final_iddf, final_idrev,
                row['IdPartener'], row['CodPartener'], row['IdClsf'], row['IdClsfAcc'],
                row['CodSSI'], row['CodAngajament'], row['CodIndicator'],
                row['CA_Anterior'], row['Inf1'], row['CA_Curent'],
                row['CB_Anterior'], row['Inf2'], row['CB_Curent'],
            ))
            revb_map.append({'TmpID': row['TmpID'], 'IdSecB': cursor.lastrowid})
        _dlog(f"[commit_add] FX_DDF_REV_SB INSERT: {len(revb_map)} randuri")

        cursor.execute("""
            SELECT TmpID, CaleFisier, DateFisier, PrtScr
            FROM stg_Att WHERE Token = %s
        """, (token,))
        for row in cursor.fetchall():
            cursor.execute("""
                INSERT INTO FX_DDF_REV_ATT (
                    IDDF, IDREV, CaleFisier, DateFisier, PrtScr
                ) VALUES (%s,%s,%s,%s,%s)
            """, (
                final_iddf, final_idrev,
                row['CaleFisier'], row['DateFisier'], row['PrtScr'],
            ))
            att_map.append({'TmpID': row['TmpID'], 'IdRevAtt': cursor.lastrowid})
        _dlog(f"[commit_add] FX_DDF_REV_ATT INSERT: {len(att_map)} randuri")

    _dlog(f"[commit_add] DONE IDDF={final_iddf} IDREV={final_idrev}")
    return {
        'IDDF':     final_iddf,
        'IDREV':    final_idrev,
        'RevA_Map': reva_map,
        'RevB_Map': revb_map,
        'Att_Map':  att_map,
    }


def _commit_staging_mod(cursor, token: str) -> dict:
    _dlog(f"[commit_mod] START token={token}")

    cursor.execute("""
        SELECT IDDF, IdUnitate, IdPartener, CodPartener, CodAngajament, Cual,
               DataCreare, DataDef, ObiectDDF, Program, SS, Comp, Stare,
               PartAng, DC, Incarcat, Preluat, Salarii
        FROM stg_DocFund WHERE Token = %s
    """, (token,))
    ddf_row = cursor.fetchone()
    final_iddf = ddf_row['IDDF']

    cursor.execute("""
        UPDATE FX_DDF
        SET IdUnitate=%s, IdPartener=%s, CodPartener=%s, CodAngajament=%s, Cual=%s,
            DataCreare=%s, DataDef=%s, ObiectDDF=%s, Program=%s, SS=%s, Comp=%s,
            Stare=%s, PartAng=%s, DC=%s, Incarcat=%s, Preluat=%s, Salarii=%s
        WHERE IDDF=%s
    """, (
        ddf_row['IdUnitate'], ddf_row['IdPartener'], ddf_row['CodPartener'],
        ddf_row['CodAngajament'], ddf_row['Cual'],
        ddf_row['DataCreare'], ddf_row['DataDef'], ddf_row['ObiectDDF'],
        ddf_row['Program'], ddf_row['SS'], ddf_row['Comp'], ddf_row['Stare'],
        ddf_row['PartAng'], ddf_row['DC'], ddf_row['Incarcat'],
        ddf_row['Preluat'], ddf_row['Salarii'],
        final_iddf,
    ))
    _dlog(f"[commit_mod] FX_DDF UPDATE IDDF={final_iddf} rowcount={cursor.rowcount}")

    cursor.execute("""
        SELECT IDREV, NumarRev, DataRev,
               Desc_Scurta, Desc_Lunga, Desc_Lunga_ANSI,
               DC, CodAngajament, Tip, Incarcat, Preluat
        FROM stg_Revizii WHERE Token = %s
    """, (token,))
    rev_row = cursor.fetchone()

    final_idrev = None
    if rev_row:
        if rev_row['IDREV'] <= 0:
            # Revizie noua adaugata pe un DDF existent
            cursor.execute("""
                INSERT INTO FX_DDF_REV (
                    IDDF, NumarRev, DataRev,
                    Desc_Scurta, Desc_Lunga, Desc_Lunga_ANSI,
                    DC, CodAngajament, Tip, Incarcat, Preluat
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                final_iddf,
                rev_row['NumarRev'], rev_row['DataRev'],
                rev_row['Desc_Scurta'], rev_row['Desc_Lunga'], rev_row['Desc_Lunga_ANSI'],
                rev_row['DC'], rev_row['CodAngajament'], rev_row['Tip'],
                rev_row['Incarcat'], rev_row['Preluat'],
            ))
            final_idrev = cursor.lastrowid
            _dlog(f"[commit_mod] FX_DDF_REV INSERT (revizie noua) → IDREV={final_idrev}")
        else:
            final_idrev = rev_row['IDREV']
            cursor.execute("""
                UPDATE FX_DDF_REV
                SET NumarRev=%s, DataRev=%s,
                    Desc_Scurta=%s, Desc_Lunga=%s, Desc_Lunga_ANSI=%s,
                    DC=%s, CodAngajament=%s, Tip=%s, Incarcat=%s, Preluat=%s
                WHERE IDREV=%s
            """, (
                rev_row['NumarRev'], rev_row['DataRev'],
                rev_row['Desc_Scurta'], rev_row['Desc_Lunga'], rev_row['Desc_Lunga_ANSI'],
                rev_row['DC'], rev_row['CodAngajament'], rev_row['Tip'],
                rev_row['Incarcat'], rev_row['Preluat'],
                final_idrev,
            ))
            _dlog(f"[commit_mod] FX_DDF_REV UPDATE IDREV={final_idrev} rowcount={cursor.rowcount}")

    reva_map: List[Dict] = []
    revb_map: List[Dict] = []
    att_map:  List[Dict] = []

    if final_idrev is not None:
        # --- SA ---
        cursor.execute("""
            DELETE FROM FX_DDF_REV_SA
            WHERE IDREV = %s
              AND IdSecA NOT IN (
                  SELECT IdSecA FROM stg_RevA WHERE Token = %s AND IdSecA > 0
              )
        """, (final_idrev, token))
        _dlog(f"[commit_mod] SA DELETE disparute: {cursor.rowcount}")

        cursor.execute("""
            UPDATE FX_DDF_REV_SA f
            INNER JOIN stg_RevA s ON f.IdSecA = s.IdSecA
            SET f.IdPartener     = s.IdPartener,
                f.CodPartener    = s.CodPartener,
                f.IdClsf         = s.IdClsf,
                f.IdClsfAcc      = s.IdClsfAcc,
                f.Clsf           = s.Clsf,
                f.ElementFund    = s.ElementFund,
                f.ParametriiFund = s.ParametriiFund,
                f.ValPrec        = s.ValPrec,
                f.ValCur         = s.ValCur,
                f.ValTot         = s.ValTot,
                f.PartInd        = s.PartInd,
                f.CodAngajament  = s.CodAngajament,
                f.CodIndicator   = s.CodIndicator,
                f.Ramane         = s.Ramane
            WHERE s.Token = %s AND s.IdSecA > 0
        """, (token,))
        _dlog(f"[commit_mod] SA UPDATE existente: {cursor.rowcount}")

        cursor.execute("""
            SELECT TmpID, IdPartener, CodPartener, IdClsf, IdClsfAcc, Clsf,
                   ElementFund, ParametriiFund, ValPrec, ValCur, ValTot,
                   PartInd, CodAngajament, CodIndicator, Ramane
            FROM stg_RevA WHERE Token = %s AND IdSecA <= 0
        """, (token,))
        for row in cursor.fetchall():
            cursor.execute("""
                INSERT INTO FX_DDF_REV_SA (
                    IDDF, IDREV, IdPartener, CodPartener, IdClsf, IdClsfAcc, Clsf,
                    ElementFund, ParametriiFund, ValPrec, ValCur, ValTot,
                    PartInd, CodAngajament, CodIndicator, Ramane
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                final_iddf, final_idrev,
                row['IdPartener'], row['CodPartener'], row['IdClsf'], row['IdClsfAcc'],
                row['Clsf'], row['ElementFund'], row['ParametriiFund'],
                row['ValPrec'], row['ValCur'], row['ValTot'],
                row['PartInd'], row['CodAngajament'], row['CodIndicator'], row['Ramane'],
            ))
            reva_map.append({'TmpID': row['TmpID'], 'IdSecA': cursor.lastrowid})
        _dlog(f"[commit_mod] SA INSERT noi: {len(reva_map)}")

        # --- SB ---
        cursor.execute("""
            DELETE FROM FX_DDF_REV_SB
            WHERE IDREV = %s
              AND IdSecB NOT IN (
                  SELECT IdSecB FROM stg_RevB WHERE Token = %s AND IdSecB > 0
              )
        """, (final_idrev, token))
        _dlog(f"[commit_mod] SB DELETE disparute: {cursor.rowcount}")

        cursor.execute("""
            UPDATE FX_DDF_REV_SB f
            INNER JOIN stg_RevB s ON f.IdSecB = s.IdSecB
            SET f.IdPartener    = s.IdPartener,
                f.CodPartener   = s.CodPartener,
                f.IdClsf        = s.IdClsf,
                f.IdClsfAcc     = s.IdClsfAcc,
                f.CodSSI        = s.CodSSI,
                f.CodAngajament = s.CodAngajament,
                f.CodIndicator  = s.CodIndicator,
                f.CA_Anterior   = s.CA_Anterior,
                f.Inf1          = s.Inf1,
                f.CA_Curent     = s.CA_Curent,
                f.CB_Anterior   = s.CB_Anterior,
                f.Inf2          = s.Inf2,
                f.CB_Curent     = s.CB_Curent
            WHERE s.Token = %s AND s.IdSecB > 0
        """, (token,))
        _dlog(f"[commit_mod] SB UPDATE existente: {cursor.rowcount}")

        cursor.execute("""
            SELECT TmpID, IdPartener, CodPartener, IdClsf, IdClsfAcc, CodSSI,
                   CodAngajament, CodIndicator,
                   CA_Anterior, Inf1, CA_Curent, CB_Anterior, Inf2, CB_Curent
            FROM stg_RevB WHERE Token = %s AND IdSecB <= 0
        """, (token,))
        for row in cursor.fetchall():
            cursor.execute("""
                INSERT INTO FX_DDF_REV_SB (
                    IDDF, IDREV, IdPartener, CodPartener, IdClsf, IdClsfAcc, CodSSI,
                    CodAngajament, CodIndicator,
                    CA_Anterior, Inf1, CA_Curent, CB_Anterior, Inf2, CB_Curent
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                final_iddf, final_idrev,
                row['IdPartener'], row['CodPartener'], row['IdClsf'], row['IdClsfAcc'],
                row['CodSSI'], row['CodAngajament'], row['CodIndicator'],
                row['CA_Anterior'], row['Inf1'], row['CA_Curent'],
                row['CB_Anterior'], row['Inf2'], row['CB_Curent'],
            ))
            revb_map.append({'TmpID': row['TmpID'], 'IdSecB': cursor.lastrowid})
        _dlog(f"[commit_mod] SB INSERT noi: {len(revb_map)}")

        # --- ATT ---
        cursor.execute("""
            DELETE FROM FX_DDF_REV_ATT
            WHERE IDREV = %s
              AND IdRevAtt NOT IN (
                  SELECT IdRevAtt FROM stg_Att WHERE Token = %s AND IdRevAtt > 0
              )
        """, (final_idrev, token))
        _dlog(f"[commit_mod] ATT DELETE disparute: {cursor.rowcount}")

        cursor.execute("""
            UPDATE FX_DDF_REV_ATT f
            INNER JOIN stg_Att s ON f.IdRevAtt = s.IdRevAtt
            SET f.CaleFisier = s.CaleFisier,
                f.DateFisier = s.DateFisier,
                f.PrtScr     = s.PrtScr
            WHERE s.Token = %s AND s.IdRevAtt > 0
        """, (token,))
        _dlog(f"[commit_mod] ATT UPDATE existente: {cursor.rowcount}")

        cursor.execute("""
            SELECT TmpID, IDVBNET, CaleFisier, DateFisier, PrtScr
            FROM stg_Att WHERE Token = %s AND IdRevAtt <= 0
        """, (token,))
        for row in cursor.fetchall():
            cursor.execute("""
                INSERT INTO FX_DDF_REV_ATT (
                    IDDF, IDREV, IDVBNET, CaleFisier, DateFisier, PrtScr
                ) VALUES (%s,%s,%s,%s,%s,%s)
            """, (
                final_iddf, final_idrev, row['IDVBNET'],
                row['CaleFisier'], row['DateFisier'], row['PrtScr'],
            ))
            att_map.append({'TmpID': row['TmpID'], 'IdRevAtt': cursor.lastrowid})
        _dlog(f"[commit_mod] ATT INSERT noi: {len(att_map)}")

    _dlog(f"[commit_mod] DONE IDDF={final_iddf} IDREV={final_idrev}")
    return {
        'IDDF':     final_iddf,
        'IDREV':    final_idrev,
        'RevA_Map': reva_map,
        'RevB_Map': revb_map,
        'Att_Map':  att_map,
    }


def _commit_staging_upd_ang(cursor, token: str) -> dict:
    """
    Actualizeaza CodAngajament in toate tabelele legate de FX_DDF.
    IDDF, IdUnitate si CodAngajament nou vin din stg_DocFund.
    """
    _dlog(f"[commit_upd_ang] START token={token}")

    cursor.execute("""
        SELECT IDDF, IdUnitate, CodAngajament
        FROM stg_DocFund WHERE Token = %s
    """, (token,))
    row = cursor.fetchone()
    iddf        = row['IDDF']           # type: ignore[index]
    id_unitate  = row['IdUnitate']      # type: ignore[index]
    cod_ang_nou = row['CodAngajament']  # type: ignore[index]
    _dlog(f"[commit_upd_ang] IDDF={iddf} IdUnitate={id_unitate} CodAngajament={cod_ang_nou}")

    cursor.execute("UPDATE FX_DDF SET CodAngajament = %s WHERE IDDF = %s", (cod_ang_nou, iddf))
    _dlog(f"[commit_upd_ang] FX_DDF rowcount={cursor.rowcount}")

    cursor.execute("UPDATE FX_DDF_REV SET CodAngajament = %s WHERE IDDF = %s", (cod_ang_nou, iddf))
    _dlog(f"[commit_upd_ang] FX_DDF_REV rowcount={cursor.rowcount}")

    cursor.execute("UPDATE FX_DDF_REV_SA SET CodAngajament = %s WHERE IDDF = %s", (cod_ang_nou, iddf))
    _dlog(f"[commit_upd_ang] FX_DDF_REV_SA rowcount={cursor.rowcount}")

    cursor.execute("UPDATE FX_DDF_REV_SB SET CodAngajament = %s WHERE IDDF = %s", (cod_ang_nou, iddf))
    _dlog(f"[commit_upd_ang] FX_DDF_REV_SB rowcount={cursor.rowcount}")

    cursor.execute("UPDATE FX_DDF_REV_PRT SET CodAngajament = %s WHERE IDDF = %s", (cod_ang_nou, iddf))
    _dlog(f"[commit_upd_ang] FX_DDF_REV_PRT rowcount={cursor.rowcount}")

    _dlog(f"[commit_upd_ang] DONE IDDF={iddf}")
    return {'IDDF': iddf}


def _run_with_retry(operation, data: dict):
    """
    Executa operation(cursor) → result in tranzactie explicita.
    Retry automat la deadlock (errno 1213) sau lock timeout (errno 1205),
    cu sleep progresiv: attempt * DEADLOCK_RETRY_SLEEP secunde.

    IntegrityError (FK violation, duplicate key) → raise imediat, fara retry
    (sunt erori de date, nu de concurenta).

    Orice alta exceptie → rollback + raise.
    """
    last_err = None
    for attempt in range(1, MAX_DEADLOCK_RETRIES + 1):
        conn = cursor = None
        try:
            conn, cursor = _get_conn_cursor(data)
            result = operation(cursor)
            conn.commit()
            return result

        except mysql.connector.errors.IntegrityError as e:
            # Eroare de integritate (FK, UNIQUE) — nu are sens sa reincerci
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
            raise ValueError(f"Eroare integritate DB: {e.msg}") from e

        except mysql.connector.errors.DatabaseError as e:
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass

            if e.errno in (ERRNO_DEADLOCK, ERRNO_LOCK_TIMEOUT):
                last_err = e
                if attempt < MAX_DEADLOCK_RETRIES:
                    sleep_time = DEADLOCK_RETRY_SLEEP * attempt
                    logger.warning(
                        f"[RETRY] errno={e.errno} attempt={attempt}/{MAX_DEADLOCK_RETRIES} "
                        f"sleep={sleep_time:.2f}s"
                    )
                    time.sleep(sleep_time)
                    continue
                raise ValueError(
                    f"Deadlock/lock timeout dupa {MAX_DEADLOCK_RETRIES} "
                    f"incercari: {e.msg}"
                ) from e

            raise

        except Exception:
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
            raise

        finally:
            _close(conn, cursor)

    raise last_err
    
# ============================================================
# ENDPOINTS
# ============================================================

@ddf_bp.route('/api/ddf/save_staging', methods=['POST'])
@require_api_key
def save_staging():
    """
    Inlocuieste save_complex.
    Scrie in stg_* si returneaza token.
    Access salveaza local, apoi trimite /api/ddf/confirm.
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'error': 'Body JSON lipsa'}), 400

    token = str(uuid.uuid4())
    conn = None
    cursor = None
    try:
        db_name = data.get("db_name")
        _dlog(f"[save_staging] db={db_name} token={token}")
        conn   = get_db_connection(db_name)
        cursor = conn.cursor(dictionary=True)
        _insert_staging(cursor, token, 'ADD', data)
        conn.commit()
        return jsonify({'ok': True, 'token': token}), 200

    except Exception as e:
        if conn: conn.rollback()
        logger.exception("save_staging error token=%s", token)
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor: cursor.close()
        if conn:   conn.close()


# ===========================================================================
# SECTIUNEA 4 — DB HELPERS (conexiune + retry)
# ===========================================================================

def _get_conn_cursor(data: dict):
    """Deschide conexiune si cursor dictionary pentru db_name din payload."""
    conn   = get_db_connection(data.get("db_name"))
    cursor = conn.cursor(dictionary=True)
    return conn, cursor


def _close(conn, cursor):
    """Inchide cursor si conexiune silentios (ignore erori la close)."""
    for obj in (cursor, conn):
        if obj:
            try:
                obj.close()
            except Exception:
                pass


def _run_with_retry(operation, data: dict):
    """
    Executa operation(cursor) → result in tranzactie explicita.
    Retry automat la deadlock (errno 1213) sau lock timeout (errno 1205),
    cu sleep progresiv: attempt * DEADLOCK_RETRY_SLEEP secunde.

    IntegrityError (FK violation, duplicate key) → raise imediat, fara retry
    (sunt erori de date, nu de concurenta).

    Orice alta exceptie → rollback + raise.
    """
    last_err = None
    for attempt in range(1, MAX_DEADLOCK_RETRIES + 1):
        conn = cursor = None
        try:
            conn, cursor = _get_conn_cursor(data)
            result = operation(cursor)
            conn.commit()
            return result

        except mysql.connector.errors.IntegrityError as e:
            # Eroare de integritate (FK, UNIQUE) — nu are sens sa reincerci
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
            raise ValueError(f"Eroare integritate DB: {e.msg}") from e

        except mysql.connector.errors.DatabaseError as e:
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass

            if e.errno in (ERRNO_DEADLOCK, ERRNO_LOCK_TIMEOUT):
                last_err = e
                if attempt < MAX_DEADLOCK_RETRIES:
                    sleep_time = DEADLOCK_RETRY_SLEEP * attempt
                    logger.warning(
                        f"[RETRY] errno={e.errno} attempt={attempt}/{MAX_DEADLOCK_RETRIES} "
                        f"sleep={sleep_time:.2f}s"
                    )
                    time.sleep(sleep_time)
                    continue
                raise ValueError(
                    f"Deadlock/lock timeout dupa {MAX_DEADLOCK_RETRIES} "
                    f"incercari: {e.msg}"
                ) from e

            raise

        except Exception:
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
            raise

        finally:
            _close(conn, cursor)

    raise last_err


@ddf_bp.route('/api/ddf/update_staging', methods=['POST'])
@require_api_key
def update_staging():
    """
    Inlocuieste update_complex.
    Scrie in stg_* si returneaza token.
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'error': 'Body JSON lipsa'}), 400

    token = str(uuid.uuid4())
    conn = None
    cursor = None
    try:
        db_name = data.get("db_name")
        _dlog(f"[update_staging] db={db_name} token={token}")
        conn   = get_db_connection(db_name)
        cursor = conn.cursor(dictionary=True)
        _insert_staging(cursor, token, 'MOD', data)
        conn.commit()
        return jsonify({'ok': True, 'token': token}), 200

    except Exception as e:
        if conn: conn.rollback()
        logger.exception("update_staging error token=%s", token)
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor: cursor.close()
        if conn:   conn.close()


@ddf_bp.route('/api/ddf/update_ang_staging', methods=['POST'])
@require_api_key
def update_ang_staging():
    """
    Scrie in stg_DocFund cu TipOperatie='UPD_ANG'.
    Payload: {db_name, IDDF, IdUnitate, CodAngajament}
    Access actualizeaza local, apoi trimite /api/ddf/confirm.
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'error': 'Body JSON lipsa'}), 400

    iddf       = data.get('IDDF')
    id_unitate = data.get('IdUnitate')
    cod_ang    = data.get('CodAngajament')
    if not iddf or not id_unitate or cod_ang is None:
        return jsonify({'error': 'IDDF, IdUnitate si CodAngajament sunt obligatorii'}), 400

    token = str(uuid.uuid4())
    conn = None
    cursor = None
    try:
        db_name = data.get("db_name")
        _dlog(f"[update_ang_staging] db={db_name} IDDF={iddf} IdUnitate={id_unitate} CodAngajament={cod_ang} token={token}")
        conn   = get_db_connection(db_name)
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            INSERT INTO stg_DocFund (Token, TipOperatie, IDDF, IdUnitate, CodAngajament)
            VALUES (%s, 'UPD_ANG', %s, %s, %s)
        """, (token, iddf, id_unitate, cod_ang))
        conn.commit()
        return jsonify({'ok': True, 'token': token}), 200

    except Exception as e:
        if conn: conn.rollback()
        logger.exception("update_ang_staging error token=%s", token)
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor: cursor.close()
        if conn:   conn.close()


@ddf_bp.route('/api/ddf/confirm', methods=['POST'])
@require_api_key
def ddf_confirm():
    """
    Primeste ACK de la Access.
    OK       -> muta din stg_* in tabele reale
    FAIL     -> sterge stg_* (CASCADE curata tot)
    FAIL_MOD -> marcheaza pentru reconciliere, nu atinge tabelele reale
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'error': 'Body JSON lipsa'}), 400

    token  = data.get('token')
    status = data.get('status')
    if not token or not status:
        return jsonify({'error': 'token si status sunt obligatorii'}), 400

    _dlog(f"[ddf_confirm] token={token} status={status}")

    conn = None
    cursor = None
    try:
        db_name = data.get("db_name")
        conn    = get_db_connection(db_name)
        cursor  = conn.cursor(dictionary=True)

        cursor.execute(
            "SELECT TipOperatie FROM stg_DocFund WHERE Token = %s AND Status = 'PENDING'",
            (token,)
        )
        row = cursor.fetchone()
        if not row:
            return jsonify({'error': 'Token invalid sau deja procesat'}), 404

        tip_operatie = row["TipOperatie"]  # type: ignore[index]
        _dlog(f"[ddf_confirm] TipOperatie={tip_operatie}")

        new_ids: dict = {}
        if status == 'OK':
            if tip_operatie == 'ADD':
                new_ids = _commit_staging_add(cursor, token)
            elif tip_operatie == 'MOD':
                new_ids = _commit_staging_mod(cursor, token)
            elif tip_operatie == 'UPD_ANG':
                new_ids = _commit_staging_upd_ang(cursor, token)
            else:
                return jsonify({'error': f'TipOperatie necunoscut: {tip_operatie}'}), 400

            cursor.execute("""
                UPDATE stg_DocFund SET Status='CONFIRMED', DataConfirm=NOW()
                WHERE Token = %s
            """, (token,))

        elif status == 'FAIL':
            cursor.execute("DELETE FROM stg_DocFund WHERE Token = %s", (token,))
            _dlog(f"[ddf_confirm] FAIL → DELETE token={token}")

        elif status == 'FAIL_MOD':
            cursor.execute("""
                UPDATE stg_DocFund SET Status='FAIL_MOD', DataConfirm=NOW()
                WHERE Token = %s
            """, (token,))
            logger.warning("FAIL_MOD token=%s - reconciliere necesara", token)

        else:
            return jsonify({'error': f'Status necunoscut: {status}'}), 400

        conn.commit()
        return jsonify({'ok': True, 'status': status, **new_ids}), 200

    except Exception as e:
        if conn: conn.rollback()
        logger.exception("ddf_confirm error token=%s", token)
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor: cursor.close()
        if conn:   conn.close()


@ddf_bp.route('/api/ddf/patch', methods=['POST'])
@require_api_key
def ddf_patch():
    """
    Actualizeaza DOAR campurile trimise in payload pentru un FX_DDF.
    Fara staging, fara confirm.
    Payload: {db_name, IDDF, <orice camp non-critic>}
    Campurile critice (ID-uri) sunt ignorate chiar daca sunt trimise.
    """
    # Campuri blocate - nu pot fi modificate prin acest endpoint
    BLOCKED: set = {
        'IDDF', 'IdUnitate', 'DC', 'db_name', 
    }
    # Campuri permise explicit
    ALLOWED: set = {
        'CodPartener', 'CodAngajament', 'Cual', 
        'IdPartener', 'PartAng', 'CodPartener',
        'DataCreare', 'DataDef', 'ObiectDDF', 'Program',
        'SS', 'Comp', 'Stare', 'Salarii',
        'Incarcat', 'Preluat',
    }

    data = request.get_json(silent=True)
    if not data:
        return jsonify({'error': 'Body JSON lipsa'}), 400

    iddf = data.get('IDDF')
    if not iddf:
        return jsonify({'error': 'IDDF este obligatoriu'}), 400

    # Extrage doar campurile permise din payload
    fields = {k: v for k, v in data.items() if k in ALLOWED}
    if not fields:
        return jsonify({'error': 'Niciun camp valid de actualizat'}), 400

    set_clause = ', '.join(f"{col} = %s" for col in fields)
    values     = list(fields.values()) + [iddf]
    _dlog(f"[ddf_patch] set_clause={set_clause} | values={values}")
    _dlog(f"[ddf_patch] IDDF={iddf} fields={list(fields.keys())}")

    conn = None
    cursor = None
    try:
        db_name = data.get("db_name")
        conn   = get_db_connection(db_name)
        cursor = conn.cursor()
        cursor.execute(
            f"UPDATE FX_DDF SET {set_clause} WHERE IDDF = %s",
            values
        )
        if cursor.rowcount == 0:
            return jsonify({'error': f'IDDF={iddf} nu exista'}), 404
        conn.commit()
        _dlog(f"[ddf_patch] updated {cursor.rowcount} row(s) IDDF={iddf}")
        return jsonify({'ok': True, 'IDDF': iddf, 'updated': list(fields.keys())}), 200

    except Exception as e:
        if conn: conn.rollback()
        logger.exception("ddf_patch error IDDF=%s", iddf)
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor: cursor.close()
        if conn:   conn.close()


@ddf_bp.route('/api/ddf/prt/insert', methods=['POST'])
@require_api_key
def prt_insert():
    """
    INSERT direct in FX_DDF_REV_PRT. Fara staging.
    Payload: {db_name, rows: [{TmpID, IDDF, IDREV, IdClsf, IdClsfAcc, DateFisier, Expl, Tip, CodAngajament}]}
    Returneaza PRT_Map: [{TmpID, IDREVP}]
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'error': 'Body JSON lipsa'}), 400

    rows = data.get('rows', [])
    if not rows:
        return jsonify({'error': 'rows este obligatoriu si nu poate fi gol'}), 400

    conn = None
    cursor = None
    try:
        db_name = data.get("db_name")
        _dlog(f"[prt_insert] db={db_name} rows={len(rows)}")
        conn   = get_db_connection(db_name)
        cursor = conn.cursor(dictionary=True)

        prt_map: List[Dict] = []
        for row in rows:
            cursor.execute("""
                INSERT INTO FX_DDF_REV_PRT (
                    IDDF, IDREV, IdClsf, IdClsfAcc,
                    DateFisier, Expl, Tip, CodAngajament
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                row.get('IDDF'),    row.get('IDREV'),
                row.get('IdClsf'),  row.get('IdClsfAcc'),
                row.get('DateFisier'), row.get('Expl'),
                row.get('Tip'),        row.get('CodAngajament'),
            ))
            prt_map.append({'TmpID': row.get('TmpID'), 'IDREVP': cursor.lastrowid})

        conn.commit()
        _dlog(f"[prt_insert] PRT_Map={prt_map}")
        return jsonify({'ok': True, 'PRT_Map': prt_map}), 200

    except Exception as e:
        if conn: conn.rollback()
        logger.exception("prt_insert error")
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor: cursor.close()
        if conn:   conn.close()


@ddf_bp.route('/api/ddf/prt/update', methods=['POST'])
@require_api_key
def prt_update():
    """
    UPDATE direct in FX_DDF_REV_PRT. Fara staging.
    Payload: {db_name, IDREVP, Expl, Tip}
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'error': 'Body JSON lipsa'}), 400

    idrevp = data.get('IDREVP')
    if not idrevp:
        return jsonify({'error': 'IDREVP este obligatoriu'}), 400

    conn = None
    cursor = None
    try:
        db_name = data.get("db_name")
        _dlog(f"[prt_update] db={db_name} IDREVP={idrevp}")
        conn   = get_db_connection(db_name)
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            UPDATE FX_DDF_REV_PRT
            SET Expl=%s, Tip=%s
            WHERE IDREVP = %s
        """, (
            data.get('Expl'), data.get('Tip'),
            idrevp,
        ))
        if cursor.rowcount == 0:
            return jsonify({'error': f'IDREVP={idrevp} nu exista'}), 404
        conn.commit()
        _dlog(f"[prt_update] IDREVP={idrevp} updated")
        return jsonify({'ok': True, 'IDREVP': idrevp}), 200

    except Exception as e:
        if conn: conn.rollback()
        logger.exception("prt_update error")
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor: cursor.close()
        if conn:   conn.close()


@ddf_bp.route('/api/ddf/prt/delete', methods=['POST'])
@require_api_key
def prt_delete():
    """
    DELETE direct din FX_DDF_REV_PRT. Fara staging.
    Payload: {db_name, IDREVP}
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'error': 'Body JSON lipsa'}), 400

    idrevp = data.get('IDREVP')
    if not idrevp:
        return jsonify({'error': 'IDREVP este obligatoriu'}), 400

    conn = None
    cursor = None
    try:
        db_name = data.get("db_name")
        _dlog(f"[prt_delete] db={db_name} IDREVP={idrevp}")
        conn   = get_db_connection(db_name)
        cursor = conn.cursor(dictionary=True)
        cursor.execute("DELETE FROM FX_DDF_REV_PRT WHERE IDREVP = %s", (idrevp,))
        if cursor.rowcount == 0:
            return jsonify({'error': f'IDREVP={idrevp} nu exista'}), 404
        conn.commit()
        _dlog(f"[prt_delete] IDREVP={idrevp} deleted")
        return jsonify({'ok': True, 'IDREVP': idrevp}), 200

    except Exception as e:
        if conn: conn.rollback()
        logger.exception("prt_delete error")
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor: cursor.close()
        if conn:   conn.close()


@ddf_bp.route('/api/ddf/cleanup_staging', methods=['POST'])
@require_api_key
def cleanup_staging():
    """
    Sterge toate inregistrarile PENDING din stg_* mai vechi de 5 minute.
    ON DELETE CASCADE curata automat stg_Revizii, stg_RevA, stg_RevB, stg_Att.
    Apelat din VBA inainte de orice salvare.
    """
    data = request.get_json(silent=True) or {}
    db_name = data.get("db_name")

    conn = None
    cursor = None
    try:
        conn   = get_db_connection(db_name)
        cursor = conn.cursor()
        cursor.execute("""
            DELETE FROM stg_DocFund
            WHERE Status = 'PENDING'
              AND DTQ < NOW() - INTERVAL 5 MINUTE
        """)
        deleted = cursor.rowcount
        conn.commit()
        _dlog(f"[cleanup_staging] deleted={deleted}")
        return jsonify({'ok': True, 'deleted': deleted}), 200

    except Exception as e:
        if conn: conn.rollback()
        logger.exception("cleanup_staging error")
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor: cursor.close()
        if conn:   conn.close()


@ddf_bp.route('/api/ddf/att/insert', methods=['POST'])
@require_api_key
def att_insert():
    """
    INSERT direct in FX_DDF_REV_ATT. Fara staging.
    Payload: {db_name, rows: [{TmpID, IDDF, IDREV, IDVNET, CaleFisier, PrtScr, DateFisier}]}
    Returneaza ATT_Map: [{TmpID, IdRevAtt}]
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'error': 'Body JSON lipsa'}), 400

    rows = data.get('rows', [])
    if not rows:
        return jsonify({'error': 'rows este obligatoriu si nu poate fi gol'}), 400

    conn = None
    cursor = None
    try:
        db_name = data.get("db_name")
        _dlog(f"[att_insert] db={db_name} rows={len(rows)}")
        conn   = get_db_connection(db_name)
        cursor = conn.cursor()

        att_map: List[Dict] = []
        for row in rows:
            cursor.execute("""
                INSERT INTO FX_DDF_REV_ATT (
                    IDDF, IDREV, IDVBNET, CaleFisier, PrtScr, DateFisier
                ) VALUES (%s, %s, %s, %s, %s, %s)
            """, (
                row.get('IDDF'),       row.get('IDREV'),
                row.get('IDVBNET'),     row.get('CaleFisier'),
                row.get('PrtScr'),     row.get('DateFisier'),
            ))
            att_map.append({'TmpID': row.get('TmpID'), 'IdRevAtt': cursor.lastrowid})

        conn.commit()
        _dlog(f"[att_insert] ATT_Map={att_map}")
        return jsonify({'ok': True, 'ATT_Map': att_map}), 200

    except Exception as e:
        if conn: conn.rollback()
        logger.exception("att_insert error")
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor: cursor.close()
        if conn:   conn.close()


# ---------------------------------------------------------------------------
# DELETE DDF
# ---------------------------------------------------------------------------
@ddf_bp.route("/api/ddf/delete", methods=["POST"])
@require_api_key
def delete_ddf():
    """
    Sterge un DDF complet din DB.
    Cascade FK MariaDB sterge automat:
        REV → SA / SB / ATT / PRT

    Payload:
        {db_name, IDDF}

    Response:
        {ok: True, IDDF: N}

    NOTA:
        Endpointul sterge DIRECT din tabelele finale.
        Nu foloseste staging.
    """
    data = request.json

    try:
        iddf = _strict_pos_int(data.get("IDDF"), "IDDF")

        def operation(cursor):
            # Lock inainte de stergere — previne concurenta
            cursor.execute(
                "SELECT IDDF FROM FX_DDF WHERE IDDF=%s FOR UPDATE",
                (iddf,)
            )

            if not cursor.fetchone():
                raise ValueError(
                    f"FX_DDF cu IDDF={iddf} nu exista"
                )

            cursor.execute(
                "DELETE FROM FX_DDF WHERE IDDF=%s",
                (iddf,)
            )

            if cursor.rowcount == 0:
                raise ValueError(
                    f"DELETE FX_DDF IDDF={iddf}: 0 randuri afectate"
                )

            return {
                "ok": True,
                "IDDF": iddf
            }

        result = _run_with_retry(operation, data)

        logger.info(f"[delete_ddf] OK IDDF={iddf}")

        return jsonify(result), 200

    except Exception as e:
        logger.error(
            f"[delete_ddf] ERROR: {e}",
            exc_info=True
        )

        return jsonify({
            "error": str(e)
        }), 500


# ---------------------------------------------------------------------------
# DELETE REVIZIE DDF
# ---------------------------------------------------------------------------
@ddf_bp.route("/api/ddf/rev/delete", methods=["POST"])
@require_api_key
def delete_ddf_rev():
    """
    Sterge DOAR o revizie DDF.

    Cascade FK MariaDB sterge automat:
        SA / SB / ATT / PRT

    Payload:
        {db_name, IDREV}

    Response:
        {ok: True, IDREV: N}

    NOTA:
        FX_DDF ramane intact.
        Se sterg doar datele dependente de revizie.
    """
    data = request.json

    try:
        idrev = _strict_pos_int(data.get("IDREV"), "IDREV")

        def operation(cursor):
            # Lock revizie
            cursor.execute("""
                SELECT IDREV, IDDF
                FROM FX_DDF_REV
                WHERE IDREV=%s
                FOR UPDATE
            """, (idrev,))

            row = cursor.fetchone()

            if not row:
                raise ValueError(
                    f"FX_DDF_REV cu IDREV={idrev} nu exista"
                )

            iddf = row["IDDF"]

            # DELETE revizie
            cursor.execute("""
                DELETE FROM FX_DDF_REV
                WHERE IDREV=%s
            """, (idrev,))

            if cursor.rowcount == 0:
                raise ValueError(
                    f"DELETE FX_DDF_REV IDREV={idrev}: 0 randuri afectate"
                )

            return {
                "ok": True,
                "IDDF": iddf,
                "IDREV": idrev
            }

        result = _run_with_retry(operation, data)

        logger.info(
            f"[delete_ddf_rev] OK IDREV={idrev}"
        )

        return jsonify(result), 200

    except Exception as e:
        logger.error(
            f"[delete_ddf_rev] ERROR: {e}",
            exc_info=True
        )

        return jsonify({
            "error": str(e)
        }), 500