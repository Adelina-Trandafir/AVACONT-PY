# routes/ord/part.py
"""
Entitate PART (beneficiari plata) — FX_ORD_PART.

  _add_part : INSERT toate randurile (commit ADD).
  _sync_part: diff UPDATE/INSERT/DELETE pe semnul IDORDPARTP (commit MOD).

Ambele returneaza (tmp_to_real, part_map):
  tmp_to_real: {TmpID → IDORDPARTP real} — folosit de TBL/ATT/DOC pentru FK parinte.
  part_map:    [{TmpID, IDORDPARTP}] — randuri NOI (pentru Part_Map din response).
"""
from typing import Tuple

from utils.parsing import (
    _strict_int,
    _strict_pos_int,
    _strict_str,
    _strict_str_nonempty,
)

from . import logger


def _add_part(cursor, idordp: int, token: str) -> Tuple[dict, list]:
    """
    INSERT FX_ORD_PART pentru toate randurile din staging (commit ADD).

    tmp_to_real: TmpID (Access autonumber din tmpFX_ORD_PART.ID)
                 → IDORDPARTP (MariaDB autoincrement generat la INSERT).
    Folosit pentru a rezolva FK PART parinte la copiii TBL/ATT/DOC.
    """
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
                 CodFiscal, ContIBAN, Banca)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (
            idordp,
            _strict_pos_int(p["IDORDPART"],    "IDORDPART"),
            _strict_str(p["Counter"],          "Counter"),
            _strict_str_nonempty(p["DenBene"], "DenBene"),
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
    return tmp_to_real, part_map


def _sync_part(cursor, token: str, idordp: int) -> Tuple[dict, list]:
    """
    Diff sync FX_ORD_PART (commit MOD).

    IDORDPARTP > 0 → UPDATE rand existent (rowcount=0 = eroare: rand disparut din DB).
    IDORDPARTP < 0 → INSERT rand nou, captureaza lastrowid in tmp_to_real.
    DELETE: PART-uri din DB cu IDORDPARTP absent din payload (sterse in Access).
      Cascade FK MariaDB sterge automat TBL/ATT/DOC orfane.
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
                    ContIBAN=%s, Banca=%s
                WHERE IDORDPARTP=%s AND IDORDP=%s
            """, (
                _strict_pos_int(p["IDORDPART"], "IDORDPART"),
                _strict_str(p["Counter"],       "Counter"),
                _strict_str_nonempty(p["DenBene"], "DenBene"),
                _strict_str(p["CodFiscal"],     "CodFiscal"),
                _strict_str(p["ContIBAN"],      "ContIBAN"),
                _strict_str(p["Banca"],         "Banca"),
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
                     CodFiscal, ContIBAN, Banca)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (
                idordp,
                _strict_pos_int(p["IDORDPART"], "IDORDPART"),
                _strict_str(p["Counter"],       "Counter"),
                _strict_str_nonempty(p["DenBene"], "DenBene"),
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