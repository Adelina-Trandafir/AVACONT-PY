# routes/ord/commit.py
"""
Commit ORD (Sectiunile 6-8 din monolit) — functii pure, fara rute.

  - _commit_add: INSERT in tabelele reale FX_ORD* (TipOperatie=ADD).
  - _commit_mod: diff sync UPDATE/INSERT/DELETE pe semnul MariaDB PK (MOD).
  - _sync_*: sync differential per entitate.
  - _cleanup_stg_children: sterge copiii din staging dupa commit.

_resolve_fk / _resolve_fk_opt sunt specifice ORD (rezolva TmpID → MariaDB PK
real generat la INSERT in staging/commit) si raman in pachet.
Parsarea generica e refolosita din utils.parsing.
"""
from typing import Tuple

from utils.parsing import (
    _strict_bool,
    _strict_int,
    _strict_pos_int,
    _strict_float,
    _strict_str,
    _strict_str_nonempty,
    _opt_int,
    _opt_str,
)

from . import logger


# ===========================================================================
# HELPERI SPECIFICI ORD — rezolvare FK TmpID → MariaDB PK
# ===========================================================================

def _resolve_fk(tmp_id_part: int, tmp_to_real: dict, entity: str) -> int:
    """
    Rezolva TmpID_OrdPart → IDORDPARTP real (MariaDB auto-increment).
    Folosit pentru TBL unde TmpID_OrdPart este obligatoriu (NOT NULL).
    Raise daca TmpID lipseste din map — indica inconsistenta staging vs commit.
    """
    logger.debug(
        f"[RESOLVE_FK] entity={entity} tmp_id_part={tmp_id_part} "
        f"map_keys={list(tmp_to_real.keys())}"
    )
    idordpartp = tmp_to_real.get(tmp_id_part)
    if not idordpartp:
        raise ValueError(
            f"{entity}: TmpID_OrdPart={tmp_id_part} nu exista in map PART. "
            "Inconsistenta intre staging si commit."
        )
    return idordpartp


def _resolve_fk_opt(tmp_id_part: int, tmp_to_real: dict, entity: str) -> int:
    """
    Identic cu _resolve_fk dar verifica `is None` in loc de `not`.
    Apelat exclusiv cand tmp_id_part is not None (ATT/DOC cu TmpID_OrdPart optional).
    IDORDPARTP = 0 ar fi invalid, deci `is None` e verificarea corecta.
    """
    idordpartp = tmp_to_real.get(tmp_id_part)
    if idordpartp is None:
        raise ValueError(
            f"{entity}: TmpID_OrdPart={tmp_id_part} nu exista in map PART. "
            "Inconsistenta intre staging si commit."
        )
    return idordpartp


# ===========================================================================
# COMMIT ADD
# ===========================================================================
#
# _commit_add: executa INSERT-urile in tabelele reale FX_ORD*.
# Apelat din confirm dupa validarea Status=PENDING si TipOperatie=ADD.
# Returneaza dict cu IDORDP real + Map-urile TmpID → MariaDB PK
# pentru toate entitatile (Part_Map, TBL_Map, ATT_Map, DOC_Map, REC_Map).
# Aceste Map-uri sunt trimise inapoi la VBA prin /ord/confirm,
# si folosite de Confirma_Local_ORD pentru a scrie PK-urile MariaDB
# in tabelele tmp* din Access.

def _commit_add(cursor, token: str) -> dict:
    """
    Commit ADD complet: INSERT FX_ORD → PART → TBL → ATT → DOC → TBL_REC.

    FX_ORD.IDRR: scris direct din stg_Ord (nullable, v5).
    FX_ORD.IDRH: scris direct din stg_Ord (nullable, v6).
    FX_ORD_TBL.IDRP: scris direct din stg_OrdTbl (nullable, v5).
    FK PART → ORD: IDORDPARTP rezolvat prin tmp_to_real (TmpID → lastrowid).
    FK TBL/ATT/DOC → PART: rezolvat prin acelasi tmp_to_real.
    FK TBL_REC → TBL: rezolvat prin tbl_to_real (TmpID → IDORDTBLP).
    """
    logger.info(f"[ADD] START token={token}")

    # Citeste header-ul ORD din staging
    cursor.execute("SELECT * FROM stg_Ord WHERE Token=%s", (token,))
    stg_ord = cursor.fetchone()
    if not stg_ord:
        raise ValueError(f"stg_Ord negasit pentru token={token}")

    # -----------------------------------------------------------------------
    # INSERT FX_ORD
    # IDORD: pre-calculat de VBA (Access autonumber predictibil).
    # IDORDP: generat de MariaDB autoincrement → capturat cu lastrowid.
    # IDRR: nullable (v5) — NULL = flux 2, pozitiv = flux 1.
    # IDRH: nullable (v6) — FK catre FX_Receptii_H, independent de IDRR.
    # -----------------------------------------------------------------------
    cursor.execute("""
        INSERT INTO FX_ORD
            (IDORD, IDDF, IDRR, IDRH,
             NrORD, DataORD, Comp, CUAL,
             Incarcat, Preluat, CodAngajament)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (
        _strict_pos_int(stg_ord["IDORD"],            "stg_Ord.IDORD"),
        _opt_int(stg_ord.get("IDDF"),                "IDDF"),
        _opt_int(stg_ord.get("IDRR"),                "IDRR"),   # v5: IDRR
        _opt_int(stg_ord.get("IDRH"),                "IDRH"),   # v6: IDRH
        _strict_int(stg_ord["NrORD"],                "NrORD"),
        stg_ord["DataORD"],
        _strict_str_nonempty(stg_ord["Comp"],        "Comp"),
        _opt_str(stg_ord.get("CUAL")),
        _strict_bool(stg_ord.get("Incarcat"),        "Incarcat"),
        _strict_bool(stg_ord.get("Preluat"),         "Preluat"),
        _strict_str_nonempty(stg_ord.get("CodAngajament"), "CodAngajament"),
    ))
    idordp = cursor.lastrowid
    logger.debug(
        f"[ADD][ORD] IDORDP={idordp} IDORD={stg_ord['IDORD']} "
        f"IDRR={stg_ord.get('IDRR')} IDRH={stg_ord.get('IDRH')}"
    )

    # -----------------------------------------------------------------------
    # INSERT FX_ORD_PART (toate noi la ADD)
    # tmp_to_real: TmpID (Access autonumber din tmpFX_ORD_PART.ID)
    #              → IDORDPARTP (MariaDB autoincrement generat la INSERT)
    # Folosit pentru a rezolva FK PART parinte la copiii TBL/ATT/DOC.
    # -----------------------------------------------------------------------
    cursor.execute(
        "SELECT * FROM stg_OrdPart WHERE Token=%s ORDER BY TmpID", (token,)
    )
    stg_parts   = cursor.fetchall()
    tmp_to_real = {}   # TmpID → IDORDPARTP real (MariaDB)
    part_map    = []

    for p in stg_parts:
        tmp_id = _strict_pos_int(p["TmpID"], "TmpID")
        cursor.execute("""
            INSERT INTO FX_ORD_PART
                (IDORDP, IDORDPART, Counter, DenBene,
                 CodPartener, IdPartener, CodFiscal, ContIBAN, Banca)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            idordp,
            _strict_pos_int(p["IDORDPART"],    "IDORDPART"),
            _strict_str(p["Counter"],          "Counter"),
            _strict_str_nonempty(p["DenBene"], "DenBene"),
            _opt_str(p.get("CodPartener")),
            _opt_int(p.get("IdPartener"),      "IdPartener"),
            _strict_str(p["CodFiscal"],        "CodFiscal"),
            _strict_str(p["ContIBAN"],         "ContIBAN"),
            _strict_str(p["Banca"],            "Banca"),
        ))
        new_idordpartp      = cursor.lastrowid
        tmp_to_real[tmp_id] = new_idordpartp
        part_map.append({"TmpID": tmp_id, "IDORDPARTP": new_idordpartp})

    logger.debug(
        f"[ADD][PART] inserted={len(part_map)} "
        f"tmp_to_real={tmp_to_real}"
    )

    # -----------------------------------------------------------------------
    # INSERT FX_ORD_TBL (toate noi la ADD)
    # IDORDPARTP: rezolvat din tmp_to_real via TmpID_OrdPart.
    # IDRP: nullable — read-only FK catre FX_Receptii_Plati (v5).
    # IDRD: eliminat din v5.
    # -----------------------------------------------------------------------
    cursor.execute(
        "SELECT * FROM stg_OrdTbl WHERE Token=%s ORDER BY TmpID", (token,)
    )
    tbl_map = []
    for t in cursor.fetchall():
        logger.debug(
            f"[ADD][TBL] TmpID={t['TmpID']} "
            f"TmpID_OrdPart={t['TmpID_OrdPart']} "
            f"IDRP={t.get('IDRP')}"
        )
        tmp_id_part = _strict_pos_int(t["TmpID_OrdPart"], "TmpID_OrdPart")
        idordpartp  = _resolve_fk(
            tmp_id_part, tmp_to_real, f"TBL TmpID={t['TmpID']}"
        )
        cursor.execute("""
            INSERT INTO FX_ORD_TBL
                (IDORDP, IDORDPARTP, IDORDTBL,
                 CodAI, CodAngajament, CodIndicator, CodSSI,
                 TotalReceptii, PlatiAnt, Valoare, Ramas,
                 IdClsf, IdClsfAcc, Explicatie, IDRP)
            VALUES (%s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            idordp, idordpartp,
            _strict_pos_int(t["IDORDTBL"],    "IDORDTBL"),
            _strict_str_nonempty(t["CodAI"],  "CodAI"),
            _strict_str(t["CodAngajament"],   "CodAngajament"),
            _strict_str(t["CodIndicator"],    "CodIndicator"),
            _strict_str(t["CodSSI"],          "CodSSI"),
            _strict_float(t["TotalReceptii"], "TotalReceptii"),
            _strict_float(t["PlatiAnt"],      "PlatiAnt"),
            _strict_float(t["Valoare"],       "Valoare"),
            _strict_float(t["Ramas"],         "Ramas"),
            _opt_int(t.get("IdClsf"),         "IdClsf"),
            _opt_int(t.get("IdClsfAcc"),      "IdClsfAcc"),
            _opt_str(t.get("Explicatie")),
            _opt_int(t.get("IDRP"),           "IDRP"),       # v5: IDRP, fara IDRD
        ))
        tbl_map.append({
            "TmpID":     _strict_pos_int(t["TmpID"], "TmpID"),
            "IDORDTBLP": cursor.lastrowid,
        })

    logger.debug(f"[ADD][TBL] inserted={len(tbl_map)}")

    # -----------------------------------------------------------------------
    # INSERT FX_ORD_ATT (toate noi la ADD)
    # TmpID_OrdPart: optional — None = ATT global (nivel ORD).
    # -----------------------------------------------------------------------
    cursor.execute(
        "SELECT * FROM stg_OrdAtt WHERE Token=%s ORDER BY TmpID", (token,)
    )
    att_map = []
    for a in cursor.fetchall():
        tmp_id_part = a["TmpID_OrdPart"]   # None sau int
        idordpartp  = (
            _resolve_fk_opt(tmp_id_part, tmp_to_real, f"ATT TmpID={a['TmpID']}")
            if tmp_id_part is not None
            else None
        )
        cursor.execute("""
            INSERT INTO FX_ORD_ATT
                (IDORDP, IDORDPARTP, IDORDATT, Imagine)
            VALUES (%s, %s, %s, %s)
        """, (
            idordp, idordpartp,
            _strict_pos_int(a["IDORDATT"],     "IDORDATT"),
            _strict_str_nonempty(a["Imagine"], "Imagine"),
        ))
        att_map.append({
            "TmpID":     _strict_pos_int(a["TmpID"], "TmpID"),
            "IDORDATTP": cursor.lastrowid,
        })

    logger.debug(f"[ADD][ATT] inserted={len(att_map)}")

    # -----------------------------------------------------------------------
    # INSERT FX_ORD_DOC (toate noi la ADD)
    # TmpID_OrdPart: optional — None = DOC global (nivel ORD).
    # -----------------------------------------------------------------------
    cursor.execute(
        "SELECT * FROM stg_OrdDoc WHERE Token=%s ORDER BY TmpID", (token,)
    )
    doc_map = []
    for d in cursor.fetchall():
        tmp_id_part = d["TmpID_OrdPart"]   # None sau int
        idordpartp  = (
            _resolve_fk_opt(tmp_id_part, tmp_to_real, f"DOC TmpID={d['TmpID']}")
            if tmp_id_part is not None
            else None
        )
        cursor.execute("""
            INSERT INTO FX_ORD_DOC
                (IDORDP, IDORDPARTP, IDORDDOC,
                 DocJust, NumeDoc, TipDoc)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (
            idordp, idordpartp,
            _strict_pos_int(d["IDORDDOC"],         "IDORDDOC"),
            _strict_str_nonempty(d.get("DocJust"), "DocJust"),
            _opt_str(d.get("NumeDoc")),
            _strict_str_nonempty(d["TipDoc"],      "TipDoc"),
        ))
        doc_map.append({
            "TmpID":     _strict_pos_int(d["TmpID"], "TmpID"),
            "IDORDDOCP": cursor.lastrowid,
        })

    logger.debug(f"[ADD][DOC] inserted={len(doc_map)}")

    # -----------------------------------------------------------------------
    # INSERT FX_ORD_TBL_REC (toate noi la ADD)
    # tbl_to_real: TmpID (din stg_OrdTblRec.TmpID_OrdTbl)
    #              → IDORDTBLP real (capturat la INSERT TBL mai sus)
    # DELETE e no-op la ADD (toate IDORDTBLP sunt noi, FX_ORD_TBL_REC e gol).
    # -----------------------------------------------------------------------
    tbl_to_real = {entry["TmpID"]: entry["IDORDTBLP"] for entry in tbl_map}
    rec_map     = _sync_tbl_rec(cursor, token, tbl_to_real)
    logger.debug(f"[ADD][REC] new={len(rec_map)}")

    logger.info(
        f"[ADD] DONE IDORDP={idordp} "
        f"parts={len(part_map)} tbls={len(tbl_map)} "
        f"atts={len(att_map)} docs={len(doc_map)} recs={len(rec_map)}"
    )
    return {
        "IDORDP":   idordp,
        "Part_Map": part_map,
        "TBL_Map":  tbl_map,
        "ATT_Map":  att_map,
        "DOC_Map":  doc_map,
        "REC_Map":  rec_map,
    }


# ===========================================================================
# COMMIT MOD (DIFF SYNC PE MARIADB PK)
# ===========================================================================
#
# _commit_mod: sync differential pe tabelele reale FX_ORD*.
# Discriminatorul ADD/UPDATE pentru fiecare rand este semnul MariaDB PK:
#   ...P > 0 → rand existent → UPDATE
#   ...P < 0 → rand nou adaugat in MOD → INSERT
# DELETE: randuri din DB cu PK absent din payload (sterse de user in Access).
# Returneaza acelasi format ca _commit_add (Map-uri doar pentru randuri NOI).

def _sync_part(cursor, token: str, idordp: int) -> Tuple[dict, list]:
    """
    Diff sync FX_ORD_PART.

    IDORDPARTP > 0 → UPDATE rand existent (rowcount=0 = eroare: rand disparut din DB).
    IDORDPARTP < 0 → INSERT rand nou, captureaza lastrowid in tmp_to_real.
    DELETE: PART-uri din DB cu IDORDPARTP absent din payload (sterse in Access).
      Cascade FK MariaDB sterge automat TBL/ATT/DOC orfane.

    Returneaza:
      tmp_to_real: {TmpID → IDORDPARTP real} — folosit pentru FK copii
      part_map:    [{TmpID, IDORDPARTP}] — doar randuri NOI (pentru Part_Map)
    """
    logger.debug(f"[MOD][PART] START idordp={idordp}")

    cursor.execute(
        "SELECT * FROM stg_OrdPart WHERE Token=%s ORDER BY TmpID", (token,)
    )
    stg_parts      = cursor.fetchall()
    tmp_to_real    = {}
    part_map       = []
    incoming_partp = set()   # IDORDPARTP > 0 pastrate in payload

    for p in stg_parts:
        tmp_id     = _strict_pos_int(p["TmpID"],    "TmpID")
        idordpartp = _strict_int(p["IDORDPARTP"],   "IDORDPARTP")

        if idordpartp > 0:
            # Rand existent — UPDATE
            cursor.execute("""
                UPDATE FX_ORD_PART
                SET IDORDPART=%s, Counter=%s, DenBene=%s, CodFiscal=%s,
                    ContIBAN=%s, Banca=%s, CodPartener=%s, IdPartener=%s
                WHERE IDORDPARTP=%s AND IDORDP=%s
            """, (
                _strict_pos_int(p["IDORDPART"], "IDORDPART"),
                _strict_str(p["Counter"],       "Counter"),
                _strict_str_nonempty(p["DenBene"], "DenBene"),
                _strict_str(p["CodFiscal"],     "CodFiscal"),
                _strict_str(p["ContIBAN"],      "ContIBAN"),
                _strict_str(p["Banca"],         "Banca"),
                _opt_str(p.get("CodPartener")),
                _opt_int(p.get("IdPartener"),   "IdPartener"),
                idordpartp, idordp,
            ))
            if cursor.rowcount == 0:
                raise ValueError(
                    f"[MOD][PART] IDORDPARTP={idordpartp} nu exista in FX_ORD_PART "
                    f"sau nu apartine IDORDP={idordp}"
                )
            tmp_to_real[tmp_id] = idordpartp
            incoming_partp.add(idordpartp)

        else:
            # Rand nou adaugat in MOD — INSERT
            cursor.execute("""
                INSERT INTO FX_ORD_PART
                    (IDORDP, IDORDPART, Counter, DenBene,
                     CodPartener, IdPartener, CodFiscal, ContIBAN, Banca)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                idordp,
                _strict_pos_int(p["IDORDPART"], "IDORDPART"),
                _strict_str(p["Counter"],       "Counter"),
                _strict_str_nonempty(p["DenBene"], "DenBene"),
                _opt_str(p.get("CodPartener")),
                _opt_int(p.get("IdPartener"),   "IdPartener"),
                _strict_str(p["CodFiscal"],     "CodFiscal"),
                _strict_str(p["ContIBAN"],      "ContIBAN"),
                _strict_str(p["Banca"],         "Banca"),
            ))
            new_idordpartp      = cursor.lastrowid
            tmp_to_real[tmp_id] = new_idordpartp
            part_map.append({"TmpID": tmp_id, "IDORDPARTP": new_idordpartp})

    # DELETE PART-uri absente din payload
    # Cascade FK sterge automat TBL/ATT/DOC asociate PART-ului sters.
    if incoming_partp:
        ph = ",".join(["%s"] * len(incoming_partp))
        cursor.execute(
            f"DELETE FROM FX_ORD_PART "
            f"WHERE IDORDP=%s AND IDORDPARTP NOT IN ({ph})",
            [idordp] + list(incoming_partp),
        )
    else:
        # Niciun PART existent pastrat → sterge tot
        cursor.execute("DELETE FROM FX_ORD_PART WHERE IDORDP=%s", (idordp,))

    logger.debug(
        f"[MOD][PART] DONE upd={len(incoming_partp)} "
        f"ins={len(part_map)} del={cursor.rowcount}"
    )
    return tmp_to_real, part_map


def _sync_tbl(cursor, token: str, idordp: int, tmp_to_real: dict) -> Tuple[dict, list]:
    """
    Diff sync FX_ORD_TBL.

    IDORDTBLP > 0 → UPDATE rand existent.
    IDORDTBLP < 0 → INSERT rand nou.
    DELETE: TBL-uri cu IDORDTBLP absent din payload.

    IDRP: nullable — inclus in UPDATE si INSERT (v5).
    IDRD: eliminat din v5 — nu mai apare in nicio operatie SQL pe FX_ORD_TBL.

    valori_date: tuplu cu campurile comune UPDATE si INSERT,
    construit o singura data per rand pentru consistenta.

    Returneaza:
      tbl_to_real: {TmpID → IDORDTBLP real} — folosit pentru FK copii REC
      tbl_map:     [{TmpID, IDORDTBLP}] — doar randuri NOI (pentru TBL_Map)
    """
    logger.debug(f"[MOD][TBL] START idordp={idordp}")

    cursor.execute(
        "SELECT * FROM stg_OrdTbl WHERE Token=%s ORDER BY TmpID", (token,)
    )
    stg_tbls      = cursor.fetchall()
    tbl_map       = []
    tbl_to_real   = {}          # TmpID → IDORDTBLP (toate, noi + existente)
    incoming_tblp = set()

    for t in stg_tbls:
        idordtblp   = _strict_int(t["IDORDTBLP"], "IDORDTBLP")
        tmp_id_part = _strict_pos_int(t["TmpID_OrdPart"], "TmpID_OrdPart")
        idordpartp  = _resolve_fk(
            tmp_id_part, tmp_to_real, f"TBL TmpID={t['TmpID']}"
        )

        # Campuri comune UPDATE si INSERT — construite o singura data per rand
        valori_date = (
            idordpartp,
            _strict_pos_int(t["IDORDTBL"],    "IDORDTBL"),
            _strict_str_nonempty(t["CodAI"],  "CodAI"),
            _strict_str(t["CodAngajament"],   "CodAngajament"),
            _strict_str(t["CodIndicator"],    "CodIndicator"),
            _strict_str(t["CodSSI"],          "CodSSI"),
            _strict_float(t["TotalReceptii"], "TotalReceptii"),
            _strict_float(t["PlatiAnt"],      "PlatiAnt"),
            _strict_float(t["Valoare"],       "Valoare"),
            _strict_float(t["Ramas"],         "Ramas"),
            _opt_int(t.get("IdClsf"),         "IdClsf"),
            _opt_int(t.get("IdClsfAcc"),      "IdClsfAcc"),
            _opt_str(t.get("Explicatie")),
            _opt_int(t.get("IDRP"),           "IDRP"),    # v5: IDRP, fara IDRD
        )

        if idordtblp > 0:
            # Rand existent — UPDATE
            cursor.execute("""
                UPDATE FX_ORD_TBL
                SET IDORDPARTP=%s, IDORDTBL=%s,
                    CodAI=%s, CodAngajament=%s, CodIndicator=%s, CodSSI=%s,
                    TotalReceptii=%s, PlatiAnt=%s, Valoare=%s, Ramas=%s,
                    IdClsf=%s, IdClsfAcc=%s, Explicatie=%s, IDRP=%s
                WHERE IDORDTBLP=%s AND IDORDP=%s
            """, valori_date + (idordtblp, idordp))
            if cursor.rowcount == 0:
                raise ValueError(
                    f"[MOD][TBL] IDORDTBLP={idordtblp} nu exista in FX_ORD_TBL "
                    f"sau nu apartine IDORDP={idordp}"
                )
            tmp_id = _strict_pos_int(t["TmpID"], "TmpID")
            tbl_to_real[tmp_id] = idordtblp
            incoming_tblp.add(idordtblp)

        else:
            # Rand nou — INSERT
            cursor.execute("""
                INSERT INTO FX_ORD_TBL
                    (IDORDP, IDORDPARTP, IDORDTBL,
                     CodAI, CodAngajament, CodIndicator, CodSSI,
                     TotalReceptii, PlatiAnt, Valoare, Ramas,
                     IdClsf, IdClsfAcc, Explicatie, IDRP)
                VALUES (%s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s)
            """, (idordp,) + valori_date)
            new_idordtblp = cursor.lastrowid
            tmp_id        = _strict_pos_int(t["TmpID"], "TmpID")
            tbl_to_real[tmp_id] = new_idordtblp
            tbl_map.append({
                "TmpID":     tmp_id,
                "IDORDTBLP": new_idordtblp,
            })

    # DELETE TBL-uri absente din payload
    if incoming_tblp:
        ph = ",".join(["%s"] * len(incoming_tblp))
        cursor.execute(
            f"DELETE FROM FX_ORD_TBL "
            f"WHERE IDORDP=%s AND IDORDTBLP NOT IN ({ph})",
            [idordp] + list(incoming_tblp),
        )
    else:
        cursor.execute("DELETE FROM FX_ORD_TBL WHERE IDORDP=%s", (idordp,))

    logger.debug(
        f"[MOD][TBL] DONE upd={len(incoming_tblp)} "
        f"ins={len(tbl_map)} del={cursor.rowcount}"
    )
    return tbl_to_real, tbl_map


def _sync_tbl_rec(cursor, token: str, tbl_to_real: dict) -> list:
    """
    Sync FX_ORD_TBL_REC: DELETE+INSERT per IDORDTBLP.

    Strategie DELETE+INSERT (nu diff) — IDORDRECP (MariaDB AutoInc) nu este
    tracked in Access, deci nu exista un discriminator de UPDATE/INSERT per rand.

    IDORDREC < 0 → Python genereaza MAX(IDORDREC pozitiv din grup)+1 per grup.
    IDORDREC > 0 → se pastreaza valoarea trimisa din VBA.

    DELETE ALL pentru TOATE IDORDTBLP din tbl_to_real, inclusiv cele fara
    randuri in stg (acopera stergerea completa a REC-urilor unui TBL).

    Returneaza REC_Map: [{TmpID, IDORDRECP}] — toate randurile inserate.
    """
    logger.debug(f"[SYNC][REC] START token={token}")

    cursor.execute(
        "SELECT * FROM stg_OrdTblRec WHERE Token=%s ORDER BY TmpID_OrdTbl, TmpID",
        (token,)
    )
    stg_recs = cursor.fetchall()
    rec_map  = []

    # Grupeaza randurile pe IDORDTBLP (rezolva TmpID_OrdTbl → IDORDTBLP)
    groups = {}   # {idordtblp: [row, ...]}
    for r in stg_recs:
        tmp_id_tbl = _strict_pos_int(r["TmpID_OrdTbl"], "TmpID_OrdTbl")
        idordtblp  = _resolve_fk(tmp_id_tbl, tbl_to_real, f"REC TmpID={r['TmpID']}")
        if idordtblp not in groups:
            groups[idordtblp] = []
        groups[idordtblp].append(r)

    # DELETE ALL pentru TOATE IDORDTBLP din tbl_to_real
    # (acopera si cazul cand toate REC-urile unui TBL au fost sterse din Access)
    all_idordtblp = list(tbl_to_real.values())
    if all_idordtblp:
        ph = ",".join(["%s"] * len(all_idordtblp))
        cursor.execute(
            f"DELETE FROM FX_ORD_TBL_REC WHERE IDORDTBLP IN ({ph})",
            all_idordtblp,
        )
        logger.debug(f"[SYNC][REC] DELETE rows={cursor.rowcount}")

    # INSERT din stg, grup cu grup
    for idordtblp, rows in groups.items():
        # Dupa DELETE, baza pentru generarea IDORDREC nou este maximul
        # valorilor pozitive existente in payload pentru acest grup.
        # Randurile cu IDORDREC > 0 (existente anterior) isi pastreaza valoarea.
        existing_max = max(
            (
                _strict_int(r["IDORDREC"], "IDORDREC")
                for r in rows
                if _strict_int(r["IDORDREC"], "IDORDREC") > 0
            ),
            default=0,
        )
        counter = existing_max   # incrementat pentru fiecare rand cu IDORDREC < 0

        for r in rows:
            idordrec = _strict_int(r["IDORDREC"], "IDORDREC")
            if idordrec < 0:
                counter  += 1
                idordrec  = counter
            cursor.execute("""
                INSERT INTO FX_ORD_TBL_REC (IDORDTBLP, IDORDREC, IDRP, Valoare)
                VALUES (%s, %s, %s, %s)
            """, (
                idordtblp,
                idordrec,
                _strict_pos_int(r["IDRP"],  "IDRP"),
                _strict_float(r["Valoare"], "Valoare"),
            ))
            rec_map.append({
                "TmpID":     _strict_pos_int(r["TmpID"], "TmpID"),
                "IDORDRECP": cursor.lastrowid,    # MariaDB AUTO_INCREMENT — capturat dupa INSERT
            })

    logger.debug(
        f"[SYNC][REC] DONE groups={len(groups)} new={len(rec_map)}"
    )
    return rec_map


def _sync_att(cursor, token: str, idordp: int, tmp_to_real: dict) -> list:
    """
    Diff sync FX_ORD_ATT.

    IDORDATTP > 0 → UPDATE. IDORDATTP < 0 → INSERT.
    DELETE: ATT-uri cu IDORDATTP absent din payload.
    TmpID_OrdPart: optional — None = ATT global (nivel ORD).
    """
    logger.debug(f"[MOD][ATT] START idordp={idordp}")

    cursor.execute(
        "SELECT * FROM stg_OrdAtt WHERE Token=%s ORDER BY TmpID", (token,)
    )
    stg_atts      = cursor.fetchall()
    att_map       = []
    incoming_attp = set()

    for a in stg_atts:
        idordattp   = _strict_int(a["IDORDATTP"], "IDORDATTP")
        tmp_id_part = a["TmpID_OrdPart"]   # None sau int
        idordpartp  = (
            _resolve_fk_opt(tmp_id_part, tmp_to_real, f"ATT TmpID={a['TmpID']}")
            if tmp_id_part is not None
            else None
        )
        imagine = _strict_str_nonempty(a["Imagine"], "Imagine")

        if idordattp > 0:
            cursor.execute("""
                UPDATE FX_ORD_ATT
                SET IDORDPARTP=%s, IDORDATT=%s, Imagine=%s
                WHERE IDORDATTP=%s AND IDORDP=%s
            """, (
                idordpartp,
                _strict_pos_int(a["IDORDATT"], "IDORDATT"),
                imagine,
                idordattp, idordp,
            ))
            if cursor.rowcount == 0:
                raise ValueError(
                    f"[MOD][ATT] IDORDATTP={idordattp} nu exista in FX_ORD_ATT "
                    f"sau nu apartine IDORDP={idordp}"
                )
            incoming_attp.add(idordattp)

        else:
            cursor.execute("""
                INSERT INTO FX_ORD_ATT
                    (IDORDP, IDORDPARTP, IDORDATT, Imagine)
                VALUES (%s, %s, %s, %s)
            """, (
                idordp, idordpartp,
                _strict_pos_int(a["IDORDATT"], "IDORDATT"),
                imagine,
            ))
            att_map.append({
                "TmpID":     _strict_pos_int(a["TmpID"], "TmpID"),
                "IDORDATTP": cursor.lastrowid,
            })

    if incoming_attp:
        ph = ",".join(["%s"] * len(incoming_attp))
        cursor.execute(
            f"DELETE FROM FX_ORD_ATT "
            f"WHERE IDORDP=%s AND IDORDATTP NOT IN ({ph})",
            [idordp] + list(incoming_attp),
        )
    else:
        cursor.execute("DELETE FROM FX_ORD_ATT WHERE IDORDP=%s", (idordp,))

    logger.debug(
        f"[MOD][ATT] DONE upd={len(incoming_attp)} "
        f"ins={len(att_map)} del={cursor.rowcount}"
    )
    return att_map


def _sync_doc(cursor, token: str, idordp: int, tmp_to_real: dict) -> list:
    """
    Diff sync FX_ORD_DOC.

    IDORDDOCP > 0 → UPDATE. IDORDDOCP < 0 → INSERT.
    DELETE: DOC-uri cu IDORDDOCP absent din payload.
    TmpID_OrdPart: optional — None = DOC global (nivel ORD).
    TipDoc == 'text' → NumeDoc poate fi null (text liber fara fisier).
    """
    logger.debug(f"[MOD][DOC] START idordp={idordp}")

    cursor.execute(
        "SELECT * FROM stg_OrdDoc WHERE Token=%s ORDER BY TmpID", (token,)
    )
    stg_docs      = cursor.fetchall()
    doc_map       = []
    incoming_docp = set()

    for d in stg_docs:
        idorddocp   = _strict_int(d["IDORDDOCP"], "IDORDDOCP")
        tmp_id_part = d["TmpID_OrdPart"]   # None sau int
        idordpartp  = (
            _resolve_fk_opt(tmp_id_part, tmp_to_real, f"DOC TmpID={d['TmpID']}")
            if tmp_id_part is not None
            else None
        )

        if idorddocp > 0:
            cursor.execute("""
                UPDATE FX_ORD_DOC
                SET IDORDPARTP=%s, IDORDDOC=%s,
                    DocJust=%s, NumeDoc=%s, TipDoc=%s
                WHERE IDORDDOCP=%s AND IDORDP=%s
            """, (
                idordpartp,
                _strict_pos_int(d["IDORDDOC"],     "IDORDDOC"),
                _opt_str(d.get("DocJust")),
                _strict_str_nonempty(d["NumeDoc"], "NumeDoc"),
                _strict_str_nonempty(d["TipDoc"],  "TipDoc"),
                idorddocp, idordp,
            ))
            if cursor.rowcount == 0:
                raise ValueError(
                    f"[MOD][DOC] IDORDDOCP={idorddocp} nu exista in FX_ORD_DOC "
                    f"sau nu apartine IDORDP={idordp}"
                )
            incoming_docp.add(idorddocp)

        else:
            cursor.execute("""
                INSERT INTO FX_ORD_DOC
                    (IDORDP, IDORDPARTP, IDORDDOC,
                     DocJust, NumeDoc, TipDoc)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (
                idordp, idordpartp,
                _strict_pos_int(d["IDORDDOC"],         "IDORDDOC"),
                _opt_str(d.get("DocJust")),
                _strict_str_nonempty(d["NumeDoc"],     "NumeDoc"),
                _strict_str_nonempty(d["TipDoc"],      "TipDoc"),
            ))
            doc_map.append({
                "TmpID":     _strict_pos_int(d["TmpID"], "TmpID"),
                "IDORDDOCP": cursor.lastrowid,
            })

    if incoming_docp:
        ph = ",".join(["%s"] * len(incoming_docp))
        cursor.execute(
            f"DELETE FROM FX_ORD_DOC "
            f"WHERE IDORDP=%s AND IDORDDOCP NOT IN ({ph})",
            [idordp] + list(incoming_docp),
        )
    else:
        cursor.execute("DELETE FROM FX_ORD_DOC WHERE IDORDP=%s", (idordp,))

    logger.debug(
        f"[MOD][DOC] DONE upd={len(incoming_docp)} "
        f"ins={len(doc_map)} del={cursor.rowcount}"
    )
    return doc_map


def _commit_mod(cursor, token: str) -> dict:
    """
    Commit MOD complet: UPDATE FX_ORD → diff sync PART → TBL → TBL_REC → ATT → DOC.

    Lock FX_ORD (SELECT FOR UPDATE) inainte de orice write —
    previne write concurent pe acelasi ORD.

    FX_ORD.IDRR: actualizat la UPDATE (nullable, v5).
      Permite modificarea legaturii la FX_Receptii_R (ex: flux 2 devine flux 1
      dupa ce receptia a fost creata in FOREXE si adusa inapoi).
    FX_ORD.IDRH: actualizat la UPDATE (nullable, v6).
    IDRD: eliminat din UPDATE FX_ORD (v5) — coloana nu mai exista in stg_Ord.
    """
    logger.info(f"[MOD] START token={token}")

    cursor.execute("SELECT * FROM stg_Ord WHERE Token=%s", (token,))
    stg_ord = cursor.fetchone()
    if not stg_ord:
        raise ValueError(f"stg_Ord negasit pentru token={token}")

    idordp = _strict_pos_int(stg_ord["IDORDP"], "stg_Ord.IDORDP")
    logger.info(f"[MOD] IDORDP={idordp}")

    # Lock pe ORD parinte — previne concurenta la MOD simultan pe acelasi ORD
    cursor.execute(
        "SELECT IDORDP FROM FX_ORD WHERE IDORDP=%s FOR UPDATE", (idordp,)
    )
    if not cursor.fetchone():
        raise ValueError(f"[MOD] FX_ORD cu IDORDP={idordp} nu exista in DB")

    # UPDATE header ORD
    # IDRR: nullable — permite actualizarea legaturii la receptie (v5).
    # IDRH: nullable — permite actualizarea legaturii la istoricul receptiei (v6).
    #   Ambele pot fi actualizate independent: flux 2 → flux 1 inseamna
    #   setarea IDRR si/sau IDRH dupa ce entitatile din Receptii au fost create.
    # IDRD: eliminat din v5 (coloana stearsa, nu mai exista in stg_Ord).
    cursor.execute("""
        UPDATE FX_ORD
        SET IDORD=%s, NrORD=%s, DataORD=%s,
            Comp=%s, CUAL=%s,
            Incarcat=%s, Preluat=%s,
            IDRR=%s, IDRH=%s
        WHERE IDORDP=%s
    """, (
        _strict_pos_int(stg_ord["IDORD"],      "IDORD"),
        _strict_int(stg_ord["NrORD"],          "NrORD"),
        stg_ord["DataORD"],
        _strict_str_nonempty(stg_ord["Comp"],  "Comp"),
        _opt_str(stg_ord.get("CUAL")),
        _strict_bool(stg_ord.get("Incarcat"),  "Incarcat"),
        _strict_bool(stg_ord.get("Preluat"),   "Preluat"),
        _opt_int(stg_ord.get("IDRR"),          "IDRR"),    # v5: IDRR
        _opt_int(stg_ord.get("IDRH"),          "IDRH"),    # v6: IDRH
        idordp,
    ))
    logger.debug(
        f"[MOD] FX_ORD UPDATE rows={cursor.rowcount} "
        f"IDRR={stg_ord.get('IDRR')} IDRH={stg_ord.get('IDRH')}"
    )

    tmp_to_real, part_map = _sync_part(cursor, token, idordp)
    tbl_to_real, tbl_map  = _sync_tbl(cursor,  token, idordp, tmp_to_real)
    rec_map               = _sync_tbl_rec(cursor, token, tbl_to_real)
    att_map               = _sync_att(cursor,  token, idordp, tmp_to_real)
    doc_map               = _sync_doc(cursor,  token, idordp, tmp_to_real)

    logger.info(
        f"[MOD] DONE IDORDP={idordp} "
        f"parts_new={len(part_map)} tbls_new={len(tbl_map)} "
        f"atts_new={len(att_map)} docs_new={len(doc_map)} recs_new={len(rec_map)}"
    )
    return {
        "IDORDP":   idordp,
        "Part_Map": part_map,
        "TBL_Map":  tbl_map,
        "ATT_Map":  att_map,
        "DOC_Map":  doc_map,
        "REC_Map":  rec_map,
    }


# ===========================================================================
# CLEANUP STAGING
# ===========================================================================

def _cleanup_stg_children(cursor, token: str):
    """
    Sterge randurile copil din stg_Ord* dupa commit (CONFIRMED sau FAIL).
    stg_Ord insusi ramane pentru audit (Status + DataConfirm).

    Guard: refuza stergerea daca Status=PENDING — ar indica un bug logic
    (cleanup apelat inainte de confirm sau in paralel cu alta sesiune).
    """
    cursor.execute("SELECT Status FROM stg_Ord WHERE Token=%s", (token,))
    row = cursor.fetchone()
    if not row:
        raise ValueError(
            f"_cleanup_stg_children: token={token} negasit in stg_Ord"
        )
    if row["Status"] == "PENDING":
        raise ValueError(
            f"_cleanup_stg_children: token={token} are Status=PENDING. "
            "Nu se sterge staging activ. Bug logic."
        )
    for tabel in ("stg_OrdPart", "stg_OrdTbl", "stg_OrdAtt", "stg_OrdDoc", "stg_OrdTblRec"):
        cursor.execute(f"DELETE FROM {tabel} WHERE Token=%s", (token,))
        logger.debug(f"[CLEANUP_STG] {tabel} rows={cursor.rowcount}")