# routes/ddf/staging.py
"""
Logica de staging si commit pentru DDF (functii pure, fara rute).

MODIFICARI DE SCHEMA (mutare SS/IdUnitate de pe antet pe linii):
  - FX_DDF / stg_DocFund : NU mai au IdPartener, CodPartener, IdUnitate, SS.
  - FX_DDF_REV_SA / stg_RevA : primesc IdUnitate, SS (aveau deja IdPartener,
    CodPartener).
  - FX_DDF_REV_SB / stg_RevB : primesc IdUnitate, SS (aveau deja IdPartener,
    CodPartener).
  - UPD_ANG : IdUnitate scos complet din flux.

`SS` si `IdUnitate` per rand vin EXPLICIT din VBA in nodurile revA/revB.
"""
import re
from typing import Dict, List

from . import _dlog


# ---------------------------------------------------------------------------
# Interim: global IDDF / IDREV across the databases the operator's accdb spans.
# The list arrives from the client on the confirm payload (db_asoc). DbName = DC.
# Removed once Access is retired.
# ---------------------------------------------------------------------------

_DBNAME_RE = re.compile(r'^[0-9]{3}_[A-Za-z0-9_]+$')


def _q_db(name):
    """Backtick-quote a database identifier after strict format validation.
    db_asoc is client-supplied, so this is the hard SQL-injection gate."""
    if not isinstance(name, str) or not _DBNAME_RE.match(name):
        raise ValueError(f"Nume bază invalid pentru scan cross-DB: {name!r}")
    return f"`{name}`"


def _parse_scan_dbs(db_name, db_asoc):
    """Build the list of databases to scan for the global MAX, or None to fall back
    to per-DB AUTO_INCREMENT.

    - db_asoc missing / empty ("" / whitespace / ",,,")  -> None  (older clients).
    - otherwise -> sorted({db_name} ∪ parsed(db_asoc)), each validated.
    The current db_name is always included so an incomplete list never misses it.
    """
    if db_asoc is None:
        return None
    if not isinstance(db_asoc, str):
        raise ValueError(f"db_asoc trebuie să fie string, primit {type(db_asoc).__name__}")

    parts = [p.strip() for p in db_asoc.split(',') if p.strip()]
    if not parts:
        return None

    if not _DBNAME_RE.match(db_name or ''):
        raise ValueError(f"db_name invalid: {db_name!r}")

    dbs = {db_name}
    for p in parts:
        if not _DBNAME_RE.match(p):
            raise ValueError(f"Nume bază invalid în db_asoc: {p!r}")
        dbs.add(p)
    return sorted(dbs)


def _existing_scan_dbs(cursor, scan_dbs):
    """Drop scan databases that don't exist on this server, logging each.

    A db_asoc entry may name a DC whose MariaDB database isn't provisioned here
    yet; scanning it would blow up the whole confirm. Instead we skip it and
    carry on with the databases that do exist. The current db_name is always in
    scan_dbs and always exists, so the result is never empty.
    """
    if not scan_dbs:
        return scan_dbs
    placeholders = ','.join(['%s'] * len(scan_dbs))
    cursor.execute(
        f"SELECT SCHEMA_NAME FROM information_schema.SCHEMATA "
        f"WHERE SCHEMA_NAME IN ({placeholders})",
        tuple(scan_dbs),
    )
    present = {r['SCHEMA_NAME'] if isinstance(r, dict) else r[0] for r in cursor.fetchall()}
    for db in scan_dbs:
        if db not in present:
            _dlog(f"[scan] baza '{db}' din db_asoc nu exista pe server — ignorata")
    return [db for db in scan_dbs if db in present]


def _next_global_id(cursor, scan_dbs, table, id_col):
    """MAX(id_col)+1 over `table` across all scan databases (db-qualified).
    Requires full read rights on all of them (the service account is full admin).
    `table` / `id_col` are caller-controlled literals, never user input.
    """
    if not scan_dbs:
        raise ValueError("_next_global_id: scan_dbs gol")
    selects = " UNION ALL ".join(
        f"SELECT MAX({id_col}) AS m FROM {_q_db(db)}.{table}"
        for db in scan_dbs
    )
    cursor.execute(f"SELECT COALESCE(MAX(m), 0) + 1 AS nxt FROM ({selects}) t")
    row = cursor.fetchone()
    return row['nxt'] if isinstance(row, dict) else row[0]


# ===========================================================================
# INSERT IN STAGING
# ===========================================================================
def _insert_staging(cursor, token: str, tip: str, data: dict) -> None:
    ddf  = data['ddf']
    rev  = data['rev']
    revA = data.get('revA', [])
    revB = data.get('revB', [])
    att  = data.get('att',  [])

    _dlog(f"[insert_staging] token={token} tip={tip} "
          f"IDDF={ddf.get('IDDF')} IDREV={rev.get('IDREV')} "
          f"revA={len(revA)} revB={len(revB)} att={len(att)}")

    # --- stg_DocFund : FARA IdUnitate / IdPartener / CodPartener / SS ---
    cursor.execute("""
        INSERT INTO stg_DocFund (
            Token, TipOperatie,
            IDDF, CodAngajament, Cual,
            DataCreare, DataDef, ObiectDDF, 
            Program, Comp, Stare,
            PartAng, DC, Incarcat, 
            Preluat, Salarii, CodFiscal, NumePartener
        ) VALUES (
            %s, %s,
            %s, %s, %s, 
            %s, %s, %s, 
            %s, %s, %s,
            %s, %s, %s, 
            %s, %s, %s, %s
        )
    """, (
        token, tip,
        ddf['IDDF'],            ddf.get('CodAngajament'), ddf.get('Cual'),
        ddf.get('DataCreare'),  ddf.get('DataDef'),       ddf.get('ObiectDDF'),
        ddf.get('Program'),     ddf.get('Comp'),          ddf.get('Stare'),
        ddf.get('PartAng'),     ddf.get('DC'),            ddf.get('Incarcat'),
        ddf.get('Preluat'),     ddf.get('Salarii'),       ddf.get('CodFiscal'),
        ddf.get('NumePartener')
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

    # --- stg_RevA : ADAUGAT IdUnitate, SS ---
    for i, row in enumerate(revA):
        cursor.execute("""
            INSERT INTO stg_RevA (
                Token, TmpID, IdSecA, IDDF, IDREV,
                IdPartener, CodPartener, IdUnitate, SS, IdClsf, IdClsfAcc, Clsf,
                ElementFund, ParametriiFund, ValPrec, ValCur, ValTot,
                PartInd, CodAngajament, CodIndicator, Ramane
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            token,
            row.get('TmpID'),
            row.get('IdSecA'),        row['IDDF'],            row['IDREV'],
            row.get('IdPartener'),    row.get('CodPartener'), row.get('IdUnitate'),
            row.get('SS'),            row.get('IdClsf'),      row.get('IdClsfAcc'),
            row.get('Clsf'),          row.get('ElementFund'), row.get('ParametriiFund'),
            row.get('ValPrec'),       row.get('ValCur'),      row.get('ValTot'),
            row.get('PartInd'),       row.get('CodAngajament'), row.get('CodIndicator'),
            row.get('Ramane'),
        ))
    _dlog(f"[insert_staging] stg_RevA: {len(revA)} randuri inserate")

    # --- stg_RevB : ADAUGAT IdUnitate, SS ---
    for i, row in enumerate(revB):
        cursor.execute("""
            INSERT INTO stg_RevB (
                Token, TmpID, IdSecB, IDDF, IDREV,
                IdPartener, CodPartener, IdUnitate, SS, IdClsf, IdClsfAcc, CodSSI,
                CodAngajament, CodIndicator,
                CA_Anterior, Inf1, CA_Curent, CB_Anterior, Inf2, CB_Curent
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            token,
            row.get('TmpID'),
            row.get('IdSecB'),      row['IDDF'],            row['IDREV'],
            row.get('IdPartener'),  row.get('CodPartener'), row.get('IdUnitate'),
            row.get('SS'),          row.get('IdClsf'),      row.get('IdClsfAcc'),
            row.get('CodSSI'),      row.get('CodAngajament'), row.get('CodIndicator'),
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


# ===========================================================================
# COMMIT ADD
# ===========================================================================
def _commit_add_ddf(cursor, token: str, scan_dbs):
    # --- FX_DDF : FARA IdUnitate / IdPartener / CodPartener / SS ---
    cursor.execute("""
        SELECT CodAngajament, Cual,
               DataCreare, DataDef, ObiectDDF, Program, Comp, Stare,
               PartAng, DC, Incarcat, Preluat, Salarii, CodFiscal, NumePartener
        FROM stg_DocFund WHERE Token = %s
    """, (token,))
    ddf_row = cursor.fetchone()

    ddf_vals = (
        ddf_row['CodAngajament'], ddf_row['Cual'],
        ddf_row['DataCreare'], ddf_row['DataDef'], ddf_row['ObiectDDF'],
        ddf_row['Program'], ddf_row['Comp'], ddf_row['Stare'],
        ddf_row['PartAng'], ddf_row['DC'], ddf_row['Incarcat'],
        ddf_row['Preluat'], ddf_row['Salarii'], ddf_row['CodFiscal'], ddf_row['NumePartener'],
    )

    if scan_dbs:
        final_iddf = _next_global_id(cursor, scan_dbs, 'FX_DDF', 'IDDF')
        cursor.execute("""
            INSERT INTO FX_DDF (
                IDDF, CodAngajament, Cual,
                DataCreare, DataDef, ObiectDDF, Program, Comp, Stare,
                PartAng, DC, Incarcat, Preluat, Salarii, CodFiscal, NumePartener
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (final_iddf, *ddf_vals))
    else:
        cursor.execute("""
            INSERT INTO FX_DDF (
                CodAngajament, Cual,
                DataCreare, DataDef, ObiectDDF, Program, Comp, Stare,
                PartAng, DC, Incarcat, Preluat, Salarii, CodFiscal, NumePartener
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, ddf_vals)
        final_iddf = cursor.lastrowid

    _dlog(f"[commit_add] FX_DDF INSERT → IDDF={final_iddf} (scan={scan_dbs or 'AUTO'})")
    return final_iddf


def _commit_add_rev(cursor, token: str, final_iddf, scan_dbs):
    cursor.execute("""
        SELECT NumarRev, DataRev,
               Desc_Scurta, Desc_Lunga, Desc_Lunga_ANSI,
               DC, CodAngajament, Tip, Incarcat, Preluat
        FROM stg_Revizii WHERE Token = %s
    """, (token,))
    rev_row = cursor.fetchone()

    final_idrev = None
    if rev_row:
        rev_vals = (
            final_iddf,
            rev_row['NumarRev'], rev_row['DataRev'],
            rev_row['Desc_Scurta'], rev_row['Desc_Lunga'], rev_row['Desc_Lunga_ANSI'],
            rev_row['DC'], rev_row['CodAngajament'], rev_row['Tip'],
            rev_row['Incarcat'], rev_row['Preluat'],
        )
        if scan_dbs:
            final_idrev = _next_global_id(cursor, scan_dbs, 'FX_DDF_REV', 'IDREV')
            cursor.execute("""
                INSERT INTO FX_DDF_REV (
                    IDREV, IDDF, NumarRev, DataRev,
                    Desc_Scurta, Desc_Lunga, Desc_Lunga_ANSI,
                    DC, CodAngajament, Tip, Incarcat, Preluat
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (final_idrev, *rev_vals))
        else:
            cursor.execute("""
                INSERT INTO FX_DDF_REV (
                    IDDF, NumarRev, DataRev,
                    Desc_Scurta, Desc_Lunga, Desc_Lunga_ANSI,
                    DC, CodAngajament, Tip, Incarcat, Preluat
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, rev_vals)
            final_idrev = cursor.lastrowid
        _dlog(f"[commit_add] FX_DDF_REV INSERT → IDREV={final_idrev}")
    else:
        _dlog(f"[commit_add] stg_Revizii: fara revizie, skip FX_DDF_REV")
    return final_idrev


def _commit_add_sa(cursor, token: str, final_iddf, final_idrev):
    reva_map: List[Dict] = []
    # --- FX_DDF_REV_SA : ADAUGAT IdUnitate, SS ---
    cursor.execute("""
            SELECT TmpID, IdPartener, CodPartener, IdUnitate, SS, IdClsf, IdClsfAcc, Clsf,
                   ElementFund, ParametriiFund, ValPrec, ValCur, ValTot,
                   PartInd, CodAngajament, CodIndicator, Ramane
            FROM stg_RevA WHERE Token = %s
        """, (token,))
    for row in cursor.fetchall():
        cursor.execute("""
                INSERT INTO FX_DDF_REV_SA (
                    IDDF, IDREV, IdPartener, CodPartener, IdUnitate, SS,
                    IdClsf, IdClsfAcc, Clsf,
                    ElementFund, ParametriiFund, ValPrec, ValCur, ValTot,
                    PartInd, CodAngajament, CodIndicator, Ramane
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
            final_iddf, final_idrev,
            row['IdPartener'], row['CodPartener'], row['IdUnitate'], row['SS'],
            row['IdClsf'], row['IdClsfAcc'], row['Clsf'],
            row['ElementFund'], row['ParametriiFund'],
            row['ValPrec'], row['ValCur'], row['ValTot'],
            row['PartInd'], row['CodAngajament'], row['CodIndicator'], row['Ramane'],
        ))
        reva_map.append({'TmpID': row['TmpID'], 'IdSecA': cursor.lastrowid})
    _dlog(f"[commit_add] FX_DDF_REV_SA INSERT: {len(reva_map)} randuri")
    return reva_map


def _commit_add_sb(cursor, token: str, final_iddf, final_idrev):
    revb_map: List[Dict] = []
    # --- FX_DDF_REV_SB : ADAUGAT IdUnitate, SS ---
    cursor.execute("""
            SELECT TmpID, IdPartener, CodPartener, IdUnitate, SS, IdClsf, IdClsfAcc, CodSSI,
                   CodAngajament, CodIndicator,
                   CA_Anterior, Inf1, CA_Curent, CB_Anterior, Inf2, CB_Curent
            FROM stg_RevB WHERE Token = %s
        """, (token,))
    for row in cursor.fetchall():
        cursor.execute("""
                INSERT INTO FX_DDF_REV_SB (
                    IDDF, IDREV, IdPartener, CodPartener, IdUnitate, SS,
                    IdClsf, IdClsfAcc, CodSSI,
                    CodAngajament, CodIndicator,
                    CA_Anterior, Inf1, CA_Curent, CB_Anterior, Inf2, CB_Curent
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
            final_iddf, final_idrev,
            row['IdPartener'], row['CodPartener'], row['IdUnitate'], row['SS'],
            row['IdClsf'], row['IdClsfAcc'], row['CodSSI'],
            row['CodAngajament'], row['CodIndicator'],
            row['CA_Anterior'], row['Inf1'], row['CA_Curent'],
            row['CB_Anterior'], row['Inf2'], row['CB_Curent'],
        ))
        revb_map.append({'TmpID': row['TmpID'], 'IdSecB': cursor.lastrowid})
    _dlog(f"[commit_add] FX_DDF_REV_SB INSERT: {len(revb_map)} randuri")
    return revb_map


def _commit_add_att(cursor, token: str, final_iddf, final_idrev):
    att_map:  List[Dict] = []
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
    return att_map


def _commit_staging_add(cursor, token: str, scan_dbs) -> dict:
    _dlog(f"[commit_add] START token={token} scan={scan_dbs or 'AUTO'}")

    if scan_dbs:
        scan_dbs = _existing_scan_dbs(cursor, scan_dbs)

    final_iddf = _commit_add_ddf(cursor, token, scan_dbs)
    final_idrev = _commit_add_rev(cursor, token, final_iddf, scan_dbs)

    reva_map: List[Dict] = []
    revb_map: List[Dict] = []
    att_map:  List[Dict] = []

    if final_idrev is not None:
        reva_map = _commit_add_sa(cursor, token, final_iddf, final_idrev)
        revb_map = _commit_add_sb(cursor, token, final_iddf, final_idrev)
        att_map = _commit_add_att(cursor, token, final_iddf, final_idrev)

    _dlog(f"[commit_add] DONE IDDF={final_iddf} IDREV={final_idrev}")
    return {
        'IDDF':     final_iddf,
        'IDREV':    final_idrev,
        'RevA_Map': reva_map,
        'RevB_Map': revb_map,
        'Att_Map':  att_map,
    }


# ===========================================================================
# COMMIT MOD
# ===========================================================================
def _commit_mod_ddf(cursor, token: str):
    # --- FX_DDF : FARA IdUnitate / IdPartener / CodPartener / SS ---
    cursor.execute("""
        SELECT IDDF, CodAngajament, Cual,
               DataCreare, DataDef, ObiectDDF, Program, Comp, Stare,
               PartAng, DC, Incarcat, Preluat, Salarii, CodFiscal, NumePartener
        FROM stg_DocFund WHERE Token = %s
    """, (token,))
    ddf_row = cursor.fetchone()
    final_iddf = ddf_row['IDDF']

    cursor.execute("""
        UPDATE FX_DDF
        SET CodAngajament=%s, Cual=%s,
            DataCreare=%s, DataDef=%s, ObiectDDF=%s, Program=%s, Comp=%s,
            Stare=%s, PartAng=%s, DC=%s, Incarcat=%s, Preluat=%s, Salarii=%s, CodFiscal=%s, NumePartener=%s
        WHERE IDDF=%s
    """, (
        ddf_row['CodAngajament'], ddf_row['Cual'],
        ddf_row['DataCreare'], ddf_row['DataDef'], ddf_row['ObiectDDF'],
        ddf_row['Program'], ddf_row['Comp'], ddf_row['Stare'],
        ddf_row['PartAng'], ddf_row['DC'], ddf_row['Incarcat'],
        ddf_row['Preluat'], ddf_row['Salarii'], ddf_row['CodFiscal'], ddf_row['NumePartener'],
        final_iddf,
    ))
    _dlog(f"[commit_mod] FX_DDF UPDATE IDDF={final_iddf} rowcount={cursor.rowcount}")
    return final_iddf


def _commit_mod_rev(cursor, token: str, final_iddf, scan_dbs):
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
            rev_vals = (
                final_iddf,
                rev_row['NumarRev'], rev_row['DataRev'],
                rev_row['Desc_Scurta'], rev_row['Desc_Lunga'], rev_row['Desc_Lunga_ANSI'],
                rev_row['DC'], rev_row['CodAngajament'], rev_row['Tip'],
                rev_row['Incarcat'], rev_row['Preluat'],
            )
            if scan_dbs:
                final_idrev = _next_global_id(cursor, scan_dbs, 'FX_DDF_REV', 'IDREV')
                cursor.execute("""
                    INSERT INTO FX_DDF_REV (
                        IDREV, IDDF, NumarRev, DataRev,
                        Desc_Scurta, Desc_Lunga, Desc_Lunga_ANSI,
                        DC, CodAngajament, Tip, Incarcat, Preluat
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """, (final_idrev, *rev_vals))
            else:
                cursor.execute("""
                    INSERT INTO FX_DDF_REV (
                        IDDF, NumarRev, DataRev,
                        Desc_Scurta, Desc_Lunga, Desc_Lunga_ANSI,
                        DC, CodAngajament, Tip, Incarcat, Preluat
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """, rev_vals)
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
    return final_idrev


def _commit_mod_sa(cursor, token: str, final_iddf, final_idrev):
    reva_map: List[Dict] = []
    # --- SA ---
    cursor.execute("""
            DELETE FROM FX_DDF_REV_SA
            WHERE IDREV = %s
              AND IdSecA NOT IN (
                  SELECT IdSecA FROM stg_RevA WHERE Token = %s AND IdSecA > 0
              )
        """, (final_idrev, token))
    _dlog(f"[commit_mod] SA DELETE disparute: {cursor.rowcount}")

    # ADAUGAT f.IdUnitate, f.SS
    cursor.execute("""
            UPDATE FX_DDF_REV_SA f
            INNER JOIN stg_RevA s ON f.IdSecA = s.IdSecA
            SET f.IdPartener     = s.IdPartener,
                f.CodPartener    = s.CodPartener,
                f.IdUnitate      = s.IdUnitate,
                f.SS             = s.SS,
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

    # ADAUGAT IdUnitate, SS la SELECT + INSERT
    cursor.execute("""
            SELECT TmpID, IdPartener, CodPartener, IdUnitate, SS, IdClsf, IdClsfAcc, Clsf,
                   ElementFund, ParametriiFund, ValPrec, ValCur, ValTot,
                   PartInd, CodAngajament, CodIndicator, Ramane
            FROM stg_RevA WHERE Token = %s AND IdSecA <= 0
        """, (token,))
    for row in cursor.fetchall():
        cursor.execute("""
                INSERT INTO FX_DDF_REV_SA (
                    IDDF, IDREV, IdPartener, CodPartener, IdUnitate, SS,
                    IdClsf, IdClsfAcc, Clsf,
                    ElementFund, ParametriiFund, ValPrec, ValCur, ValTot,
                    PartInd, CodAngajament, CodIndicator, Ramane
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
            final_iddf, final_idrev,
            row['IdPartener'], row['CodPartener'], row['IdUnitate'], row['SS'],
            row['IdClsf'], row['IdClsfAcc'], row['Clsf'],
            row['ElementFund'], row['ParametriiFund'],
            row['ValPrec'], row['ValCur'], row['ValTot'],
            row['PartInd'], row['CodAngajament'], row['CodIndicator'], row['Ramane'],
        ))
        reva_map.append({'TmpID': row['TmpID'], 'IdSecA': cursor.lastrowid})
    _dlog(f"[commit_mod] SA INSERT noi: {len(reva_map)}")
    return reva_map


def _commit_mod_sb(cursor, token: str, final_iddf, final_idrev):
    revb_map: List[Dict] = []
    # --- SB ---
    cursor.execute("""
            DELETE FROM FX_DDF_REV_SB
            WHERE IDREV = %s
              AND IdSecB NOT IN (
                  SELECT IdSecB FROM stg_RevB WHERE Token = %s AND IdSecB > 0
              )
        """, (final_idrev, token))
    _dlog(f"[commit_mod] SB DELETE disparute: {cursor.rowcount}")

    # ADAUGAT f.IdUnitate, f.SS
    cursor.execute("""
            UPDATE FX_DDF_REV_SB f
            INNER JOIN stg_RevB s ON f.IdSecB = s.IdSecB
            SET f.IdPartener    = s.IdPartener,
                f.CodPartener   = s.CodPartener,
                f.IdUnitate     = s.IdUnitate,
                f.SS            = s.SS,
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

    # ADAUGAT IdUnitate, SS la SELECT + INSERT
    cursor.execute("""
            SELECT TmpID, IdPartener, CodPartener, IdUnitate, SS, IdClsf, IdClsfAcc, CodSSI,
                   CodAngajament, CodIndicator,
                   CA_Anterior, Inf1, CA_Curent, CB_Anterior, Inf2, CB_Curent
            FROM stg_RevB WHERE Token = %s AND IdSecB <= 0
        """, (token,))
    for row in cursor.fetchall():
        cursor.execute("""
                INSERT INTO FX_DDF_REV_SB (
                    IDDF, IDREV, IdPartener, CodPartener, IdUnitate, SS,
                    IdClsf, IdClsfAcc, CodSSI,
                    CodAngajament, CodIndicator,
                    CA_Anterior, Inf1, CA_Curent, CB_Anterior, Inf2, CB_Curent
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
            final_iddf, final_idrev,
            row['IdPartener'], row['CodPartener'], row['IdUnitate'], row['SS'],
            row['IdClsf'], row['IdClsfAcc'], row['CodSSI'],
            row['CodAngajament'], row['CodIndicator'],
            row['CA_Anterior'], row['Inf1'], row['CA_Curent'],
            row['CB_Anterior'], row['Inf2'], row['CB_Curent'],
        ))
        revb_map.append({'TmpID': row['TmpID'], 'IdSecB': cursor.lastrowid})
    _dlog(f"[commit_mod] SB INSERT noi: {len(revb_map)}")
    return revb_map


def _commit_mod_att(cursor, token: str, final_iddf, final_idrev):
    att_map:  List[Dict] = []
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
    return att_map


def _commit_staging_mod(cursor, token: str, scan_dbs) -> dict:
    _dlog(f"[commit_mod] START token={token} scan={scan_dbs or 'AUTO'}")

    if scan_dbs:
        scan_dbs = _existing_scan_dbs(cursor, scan_dbs)

    final_iddf = _commit_mod_ddf(cursor, token)
    final_idrev = _commit_mod_rev(cursor, token, final_iddf, scan_dbs)

    reva_map: List[Dict] = []
    revb_map: List[Dict] = []
    att_map:  List[Dict] = []

    if final_idrev is not None:
        reva_map = _commit_mod_sa(cursor, token, final_iddf, final_idrev)
        revb_map = _commit_mod_sb(cursor, token, final_iddf, final_idrev)
        att_map = _commit_mod_att(cursor, token, final_iddf, final_idrev)

    _dlog(f"[commit_mod] DONE IDDF={final_iddf} IDREV={final_idrev}")
    return {
        'IDDF':     final_iddf,
        'IDREV':    final_idrev,
        'RevA_Map': reva_map,
        'RevB_Map': revb_map,
        'Att_Map':  att_map,
    }


# ===========================================================================
# COMMIT UPDATE ANGAJAMENT (IdUnitate scos complet)
# ===========================================================================
def _commit_staging_upd_ang(cursor, token: str) -> dict:
    """
    Actualizeaza CodAngajament in toate tabelele legate de FX_DDF.
    IDDF si CodAngajament nou vin din stg_DocFund.
    (IdUnitate nu mai face parte din flux — FX_DDF nu mai are coloana.)
    """
    _dlog(f"[commit_upd_ang] START token={token}")

    cursor.execute("""
        SELECT IDDF, CodAngajament
        FROM stg_DocFund WHERE Token = %s
    """, (token,))
    row = cursor.fetchone()
    iddf        = row['IDDF']           # type: ignore[index]
    cod_ang_nou = row['CodAngajament']  # type: ignore[index]
    _dlog(f"[commit_upd_ang] IDDF={iddf} CodAngajament={cod_ang_nou}")

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
