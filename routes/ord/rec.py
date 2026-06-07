# routes/ord/rec.py
"""
Entitate TBL_REC (plati individuale per TBL) — FX_ORD_TBL_REC.

  _sync_tbl_rec: strategie DELETE+INSERT per IDORDTBLP.

O singura functie, folosita IDENTIC la ADD si la MOD (REC nu are flux
separat de "add" — la ADD, FX_ORD_TBL_REC e gol, deci DELETE devine no-op
si raman doar INSERT-urile).

FK TBL parinte: rezolvat din tbl_to_real via _resolve_fk.
"""
from utils.parsing import _strict_int, _strict_pos_int, _strict_float

from . import logger, _resolve_fk


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