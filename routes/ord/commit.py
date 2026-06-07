# routes/ord/commit.py
"""
Orchestrare commit ORD — functii pure, fara rute.

Dupa splitul pe entitate, commit.py NU mai contine logica per-entitate.
Pastreaza doar:
  - _commit_add: INSERT FX_ORD + orchestrare add_* pe entitati (TipOperatie=ADD).
  - _commit_mod: UPDATE FX_ORD + orchestrare sync_* pe entitati (TipOperatie=MOD).
  - _cleanup_stg_children: sterge copiii din staging dupa commit.

Logica per-entitate traieste in: part.py, tbl.py, att.py, doc.py, rec.py.
Resolverele FK (_resolve_fk / _resolve_fk_opt) traiesc in __init__.py.
"""
from utils.parsing import (
    _strict_bool,
    _strict_int,
    _strict_pos_int,
    _strict_str_nonempty,
    _opt_int,
    _opt_str,
)

from . import logger
from .part import _add_part, _sync_part
from .tbl import _add_tbl, _sync_tbl
from .att import _add_att, _sync_att
from .doc import _add_doc, _sync_doc
from .rec import _sync_tbl_rec


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
    FK PART → ORD: rezolvat de _add_part (tmp_to_real).
    FK TBL/ATT/DOC → PART: rezolvat prin tmp_to_real in modulele de entitate.
    FK TBL_REC → TBL: rezolvat prin tbl_to_real in rec.py.
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

    # Entitati — ordinea impusa de FK: PART → TBL → ATT → DOC → REC.
    tmp_to_real, part_map = _add_part(cursor, idordp, token)
    tbl_to_real, tbl_map  = _add_tbl(cursor,  idordp, token, tmp_to_real)
    att_map               = _add_att(cursor,  idordp, token, tmp_to_real)
    doc_map               = _add_doc(cursor,  idordp, token, tmp_to_real)
    rec_map               = _sync_tbl_rec(cursor, token, tbl_to_real)
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
# Logica per-entitate (sync_*) traieste in modulele de entitate.

def _commit_mod(cursor, token: str) -> dict:
    """
    Commit MOD complet: UPDATE FX_ORD → diff sync PART → TBL → TBL_REC → ATT → DOC.

    Lock FX_ORD (SELECT FOR UPDATE) inainte de orice write —
    previne write concurent pe acelasi ORD.

    FX_ORD.IDRR: actualizat la UPDATE (nullable, v5).
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