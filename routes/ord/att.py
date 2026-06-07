# routes/ord/att.py
"""
Entitate ATT (atasamente imagini) — FX_ORD_ATT.

  _add_att : INSERT toate randurile (commit ADD).
  _sync_att: diff UPDATE/INSERT/DELETE pe semnul IDORDATTP (commit MOD).

Ambele returneaza att_map: [{TmpID, IDORDATTP}] — randuri NOI (ATT_Map din response).

TmpID_OrdPart: optional — None = ATT global (nivel ORD, nu PART).
FK PART parinte (cand exista): rezolvat din tmp_to_real via _resolve_fk_opt.
"""
from utils.parsing import _strict_int, _strict_pos_int, _strict_str_nonempty

from . import logger, _resolve_fk_opt


def _add_att(cursor, idordp: int, token: str, tmp_to_real: dict) -> list:
    """INSERT FX_ORD_ATT pentru toate randurile din staging (commit ADD)."""
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
    return att_map


def _sync_att(cursor, token: str, idordp: int, tmp_to_real: dict) -> list:
    """
    Diff sync FX_ORD_ATT (commit MOD).

    IDORDATTP > 0 → UPDATE. IDORDATTP < 0 → INSERT.
    DELETE: ATT-uri cu IDORDATTP absent din payload.
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