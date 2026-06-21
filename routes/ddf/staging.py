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
from typing import Dict, List

from . import _dlog


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
def _commit_staging_add(cursor, token: str) -> dict:
    _dlog(f"[commit_add] START token={token}")

    # --- FX_DDF : FARA IdUnitate / IdPartener / CodPartener / SS ---
    cursor.execute("""
        SELECT CodAngajament, Cual,
               DataCreare, DataDef, ObiectDDF, Program, Comp, Stare,
               PartAng, DC, Incarcat, Preluat, Salarii, CodFiscal, NumePartener
        FROM stg_DocFund WHERE Token = %s
    """, (token,))
    ddf_row = cursor.fetchone()

    cursor.execute("""
        INSERT INTO FX_DDF (
            CodAngajament, Cual,
            DataCreare, DataDef, ObiectDDF, Program, Comp, Stare,
            PartAng, DC, Incarcat, Preluat, Salarii, CodFiscal, NumePartener
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """, (
        ddf_row['CodAngajament'], ddf_row['Cual'],
        ddf_row['DataCreare'], ddf_row['DataDef'], ddf_row['ObiectDDF'],
        ddf_row['Program'], ddf_row['Comp'], ddf_row['Stare'],
        ddf_row['PartAng'], ddf_row['DC'], ddf_row['Incarcat'],
        ddf_row['Preluat'], ddf_row['Salarii'], ddf_row['CodFiscal'], ddf_row['NumePartener']
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


# ===========================================================================
# COMMIT MOD
# ===========================================================================
def _commit_staging_mod(cursor, token: str) -> dict:
    _dlog(f"[commit_mod] START token={token}")

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