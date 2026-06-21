# routes/ddf/sync_acc_mdb.py
# POST /api/ddf/sync_acc_mdb
# Sincronizare bulk DDF: Access -> MariaDB.
# Cate un IDDF per request.
#
# Mapari coloana Access -> coloana MariaDB (non-triviale):
#   SA/SB/PRT: Access.IdClsf   -> JSON.IdClsfAcc -> MariaDB.IdClsfAcc
#              Access.IdClsfPY -> JSON.IdClsf    -> MariaDB.IdClsf (FK Clasificatii)
#   SA/SB: Access.IdUnitate -> JSON.IdUnitate -> MariaDB.IdUnitate
#          Access.SS        -> JSON.SS        -> MariaDB.SS
#          Access.IdSalarii -> JSON.IdSalarii -> MariaDB.IdSalarii
#
# Coloane omise din FX_DDF (prezente in Access, absente din MariaDB 06-06-26):
#   IdUnitate, IdPartener, CodPartener, SS, DTQ
# Coloane omise din FX_DDF_REV (prezente in Access, absente din MariaDB):
#   ArePDFDDF, CalePDFDDF, AreDDF, CaleDDF
# Coloane auto-gestionate pe server (nu se trimit):
#   DataAdaugare, DataModificare

import logging

from flask import jsonify, request

from utils.security import require_api_key
from utils.db_retry import _run_with_retry
from utils.parsing import (
    _strict_int, _strict_pos_int, _strict_str_nonempty,
    _opt_int, _opt_str,
)

from . import ddf_bp

logger = logging.getLogger(__name__)


def _opt_float(val, field: str):
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        raise ValueError(f"{field}: nu poate fi convertit la float: {val!r}")


def _as_tiny(val, field: str, nullable: bool = True):
    if val is None:
        if nullable:
            return None
        raise ValueError(f"{field}: None, asteptat 0 sau 1")
    return 1 if val else 0


def _insert_ddf_one(cursor, data: dict):
    ddf  = data["ddf"]
    revs = data.get("revs", [])

    iddf = _strict_pos_int(ddf["IDDF"], "ddf.IDDF")

    cursor.execute("DELETE FROM FX_DDF WHERE IDDF = %s", (iddf,))
    logger.debug(f"[SYNC_DDF] DELETE IDDF={iddf} deleted={cursor.rowcount}")

    cursor.execute("""
        INSERT INTO FX_DDF
            (IDDF, CodAngajament, CUAL, ObiectDDF, Comp,
             Salarii, Buget, DataCreare, DC, Program, DataDef,
             IdSalarii, Incarcat, Preluat, Manual, Stare, PartAng)
        VALUES (%s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s)
    """, (
        iddf,
        _opt_str(ddf.get("CodAngajament")),
        _strict_int(ddf["Cual"],               "ddf.Cual"),
        _strict_str_nonempty(ddf["ObiectDDF"], "ddf.ObiectDDF"),
        _strict_str_nonempty(ddf["Comp"],      "ddf.Comp"),
        _as_tiny(ddf.get("Salarii"),  "ddf.Salarii"),
        _as_tiny(ddf.get("Buget"),    "ddf.Buget"),
        ddf.get("DataCreare"),
        _opt_str(ddf.get("DC")),
        _opt_str(ddf.get("Program")),
        ddf.get("DataDef"),
        _opt_int(ddf.get("IdSalarii"), "ddf.IdSalarii"),
        _as_tiny(ddf.get("Incarcat"), "ddf.Incarcat", nullable=False),
        _as_tiny(ddf.get("Preluat"),  "ddf.Preluat",  nullable=False),
        _as_tiny(ddf.get("Manual"),   "ddf.Manual"),
        _opt_str(ddf.get("Stare")),
        _as_tiny(ddf.get("PartAng"),  "ddf.PartAng"),
    ))

    rev_cnt = sa_cnt = sb_cnt = prt_cnt = att_cnt = 0

    for rev in revs:
        idrev = _strict_pos_int(rev["IDREV"], "rev.IDREV")

        cursor.execute("""
            INSERT INTO FX_DDF_REV
                (IDREV, IDDF, CodAngajament, Tip, NumarRev, DataRev,
                 Desc_Scurta, Desc_Lunga, Desc_Lunga_ANSI,
                 ESpeciala, Incarcat, Preluat, DC)
            VALUES (%s, %s, %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s, %s)
        """, (
            idrev, iddf,
            _opt_str(rev.get("CodAngajament")),
            _opt_str(rev.get("Tip")),
            _opt_int(rev.get("NumarRev"), "rev.NumarRev"),
            rev.get("DataRev"),
            _opt_str(rev.get("Desc_Scurta")),
            rev.get("Desc_Lunga"),
            rev.get("Desc_Lunga_ANSI"),
            _as_tiny(rev.get("ESpeciala"), "rev.ESpeciala"),
            _as_tiny(rev.get("Incarcat"), "rev.Incarcat", nullable=False),
            _as_tiny(rev.get("Preluat"),  "rev.Preluat",  nullable=False),
            _opt_str(rev.get("DC")),
        ))
        rev_cnt += 1

        for sa in rev.get("sa", []):
            cursor.execute("""
                INSERT INTO FX_DDF_REV_SA
                    (IdSecA, IDDF, IDREV,
                     IdUnitate, CodAngajament, CodIndicator,
                     CodPartener, IdPartener,
                     IdClsfAcc, IdClsf, Clsf,
                     ElementFund, ParametriiFund,
                     ValPrec, ValCur, ValTot,
                     PartInd, Ramane, SS)
                VALUES (%s, %s, %s,
                        %s, %s, %s,
                        %s, %s,
                        %s, %s, %s,
                        %s, %s,
                        %s, %s, %s,
                        %s, %s, %s)
            """, (
                _strict_pos_int(sa["IdSecA"],    "sa.IdSecA"),
                iddf, idrev,
                _opt_int(sa.get("IdUnitate"),    "sa.IdUnitate"),
                _opt_str(sa.get("CodAngajament")),
                _opt_str(sa.get("CodIndicator")),
                _opt_str(sa.get("CodPartener")),
                _opt_int(sa.get("IdPartener"),   "sa.IdPartener"),
                _strict_int(sa["IdClsfAcc"],     "sa.IdClsfAcc"),
                _strict_pos_int(sa["IdClsf"],    "sa.IdClsf"),
                _opt_str(sa.get("Clsf")),
                _opt_str(sa.get("ElementFund")),
                _opt_str(sa.get("ParametriiFund")),
                _opt_float(sa.get("ValPrec"),    "sa.ValPrec"),
                _opt_float(sa.get("ValCur"),     "sa.ValCur"),
                _opt_float(sa.get("ValTot"),     "sa.ValTot"),
                _as_tiny(sa.get("PartInd"),      "sa.PartInd"),
                _opt_float(sa.get("Ramane"),     "sa.Ramane"),
                _opt_str(sa.get("SS")),
            ))
            sa_cnt += 1

        for sb in rev.get("sb", []):
            cursor.execute("""
                INSERT INTO FX_DDF_REV_SB
                    (IdSecB, IDDF, IDREV,
                     CodAngajament, CodIndicator, CodPartener,
                     IdUnitate, IdPartener,
                     IdClsfAcc, IdClsf, CodSSI,
                     CA_Anterior, Inf1, CA_Curent,
                     CB_Anterior, Inf2, CB_Curent, SS)
                VALUES (%s, %s, %s,
                        %s, %s, %s,
                        %s, %s,
                        %s, %s, %s,
                        %s, %s, %s,
                        %s, %s, %s, %s)
            """, (
                _strict_pos_int(sb["IdSecB"],    "sb.IdSecB"),
                iddf, idrev,
                _opt_str(sb.get("CodAngajament")),
                _opt_str(sb.get("CodIndicator")),
                _opt_str(sb.get("CodPartener")),
                _opt_int(sb.get("IdUnitate"),    "sb.IdUnitate"),
                _opt_int(sb.get("IdPartener"),   "sb.IdPartener"),
                _strict_int(sb["IdClsfAcc"],     "sb.IdClsfAcc"),
                _strict_pos_int(sb["IdClsf"],    "sb.IdClsf"),
                _opt_str(sb.get("CodSSI")),
                _opt_float(sb.get("CA_Anterior"), "sb.CA_Anterior"),
                _opt_float(sb.get("Inf1"),        "sb.Inf1"),
                _opt_float(sb.get("CA_Curent"),   "sb.CA_Curent"),
                _opt_float(sb.get("CB_Anterior"), "sb.CB_Anterior"),
                _opt_float(sb.get("Inf2"),        "sb.Inf2"),
                _opt_float(sb.get("CB_Curent"),   "sb.CB_Curent"),
                _opt_str(sb.get("SS")),
            ))
            sb_cnt += 1

        for prt in rev.get("prt", []):
            cursor.execute("""
                INSERT INTO FX_DDF_REV_PRT
                    (IDREVP, IDDF, IDREV,
                     IdClsfAcc, IdClsf,
                     CodAngajament, DateFisier, Expl, Tip)
                VALUES (%s, %s, %s,
                        %s, %s,
                        %s, %s, %s, %s)
            """, (
                _strict_pos_int(prt["IDREVP"],   "prt.IDREVP"),
                iddf, idrev,
                _opt_int(prt.get("IdClsfAcc"),   "prt.IdClsfAcc"),
                _opt_int(prt.get("IdClsf"),      "prt.IdClsf"),
                _opt_str(prt.get("CodAngajament")),
                prt.get("DateFisier"),
                _opt_str(prt.get("Expl")),
                _opt_str(prt.get("Tip")),
            ))
            prt_cnt += 1

        for att in rev.get("att", []):
            cursor.execute("""
                INSERT INTO FX_DDF_REV_ATT
                    (IdRevAtt, IDDF, IDREV,
                     IDVBNET, CaleFisier, PrtScr, DateFisier)
                VALUES (%s, %s, %s,
                        %s, %s, %s, %s)
            """, (
                _strict_pos_int(att["IdRevAtt"], "att.IdRevAtt"),
                iddf, idrev,
                _opt_int(att.get("IDVBNET"),     "att.IDVBNET"),
                _opt_str(att.get("CaleFisier")),
                _as_tiny(att.get("PrtScr"),      "att.PrtScr"),
                att.get("DateFisier"),
            ))
            att_cnt += 1

    return {
        "IDDF": iddf,
        "revs": rev_cnt, "sa": sa_cnt, "sb": sb_cnt,
        "prt":  prt_cnt, "att": att_cnt,
    }


@ddf_bp.route("/api/ddf/sync_acc_mdb", methods=["POST"])
@require_api_key
def sync_ddf():
    data = request.json
    if not data:
        return jsonify({"error": "Payload JSON lipsa"}), 400
    if not data.get("db_name"):
        return jsonify({"error": "db_name lipsa"}), 400
    try:
        result = _run_with_retry(
            lambda cursor: _insert_ddf_one(cursor, data), data
        )
        logger.info(
            f"[SYNC_DDF] OK IDDF={result['IDDF']} "
            f"revs={result['revs']} sa={result['sa']} sb={result['sb']} "
            f"prt={result['prt']} att={result['att']}"
        )
        return jsonify(result), 200
    except Exception as e:
        logger.error(f"[SYNC_DDF] ERROR: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500