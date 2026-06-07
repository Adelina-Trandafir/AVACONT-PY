# routes/ord/doc.py
"""
Entitate DOC (documente justificative) — FX_ORD_DOC.

  _add_doc : INSERT toate randurile (commit ADD).
  _sync_doc: diff UPDATE/INSERT/DELETE pe semnul IDORDDOCP (commit MOD).

Ambele returneaza doc_map: [{TmpID, IDORDDOCP}] — randuri NOI (DOC_Map din response).

TmpID_OrdPart: optional — None = DOC global (nivel ORD, nu PART).
FK PART parinte (cand exista): rezolvat din tmp_to_real via _resolve_fk_opt.

NOTA diferenta ADD vs MOD pe NumeDoc:
  _add_doc  foloseste _strict_str_nonempty(d.get("DocJust")) si _opt_str(NumeDoc)
            (NumeDoc poate fi null pentru TipDoc='text').
  _sync_doc foloseste _opt_str(DocJust) si _strict_str_nonempty(NumeDoc).
  Comportament pastrat verbatim din monolit — NU armonizat.
"""
from utils.parsing import (
    _strict_int,
    _strict_pos_int,
    _strict_str_nonempty,
    _opt_str,
)

from . import logger, _resolve_fk_opt


def _add_doc(cursor, idordp: int, token: str, tmp_to_real: dict) -> list:
    """INSERT FX_ORD_DOC pentru toate randurile din staging (commit ADD)."""
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
    return doc_map


def _sync_doc(cursor, token: str, idordp: int, tmp_to_real: dict) -> list:
    """
    Diff sync FX_ORD_DOC (commit MOD).

    IDORDDOCP > 0 → UPDATE. IDORDDOCP < 0 → INSERT.
    DELETE: DOC-uri cu IDORDDOCP absent din payload.
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