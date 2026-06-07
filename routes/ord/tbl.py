# routes/ord/tbl.py
"""
Entitate TBL (indicatori/clasificatii per beneficiar) — FX_ORD_TBL.

  _add_tbl : INSERT toate randurile (commit ADD).
  _sync_tbl: diff UPDATE/INSERT/DELETE pe semnul IDORDTBLP (commit MOD).

Ambele returneaza (tbl_to_real, tbl_map):
  tbl_to_real: {TmpID → IDORDTBLP real} — folosit de REC pentru FK parinte.
  tbl_map:     [{TmpID, IDORDTBLP}] — randuri NOI (pentru TBL_Map din response).

IDRP: nullable — FK read-only catre FX_Receptii_Plati (v5).
IDRD: eliminat din v5.
FK PART parinte: rezolvat din tmp_to_real via _resolve_fk.
"""
from typing import Tuple

from utils.parsing import (
    _strict_int,
    _strict_pos_int,
    _strict_float,
    _strict_str,
    _strict_str_nonempty,
    _opt_int,
    _opt_str,
)

from . import logger, _resolve_fk


def _add_tbl(cursor, idordp: int, token: str, tmp_to_real: dict) -> Tuple[dict, list]:
    """
    INSERT FX_ORD_TBL pentru toate randurile din staging (commit ADD).
    IDORDPARTP: rezolvat din tmp_to_real via TmpID_OrdPart.
    """
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

    # tbl_to_real: {TmpID → IDORDTBLP} — folosit de _sync_tbl_rec pentru FK REC.
    tbl_to_real = {entry["TmpID"]: entry["IDORDTBLP"] for entry in tbl_map}
    return tbl_to_real, tbl_map


def _sync_tbl(cursor, token: str, idordp: int, tmp_to_real: dict) -> Tuple[dict, list]:
    """
    Diff sync FX_ORD_TBL (commit MOD).

    IDORDTBLP > 0 → UPDATE rand existent.
    IDORDTBLP < 0 → INSERT rand nou.
    DELETE: TBL-uri cu IDORDTBLP absent din payload.

    valori_date: tuplu cu campurile comune UPDATE si INSERT,
    construit o singura data per rand pentru consistenta.
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