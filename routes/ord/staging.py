# routes/ord/staging.py
"""
Staging insert ORD (Sectiunea 5 din monolit) — functii pure, fara rute.

Toate functiile _stg_insert_* scriu datele din payload in tabelele stg_*.
Apelate in ordinea: Ord → Parts → Tbls → Atts → Docs → TblRecs
(ordinea impusa de FK-urile din staging).
Toate ruleaza in aceeasi tranzactie prin _run_with_retry (apelant: core.py).

Parsarea generica e refolosita din utils.parsing.
"""
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


def _stg_insert_ord(cursor, token: str, tip: str, data: dict):
    """
    Insereaza header-ul ORD in stg_Ord.

    IDRR: nullable — NULL pentru flux 2 (receptia nu exista inca),
    pozitiv pentru flux 1 (receptia deja existenta in FOREXE).

    IDRH: nullable — FK catre FX_Receptii_H (istoricul receptiei, v6).
    NULL pentru flux 2, pozitiv pentru flux 1.
    Independent de IDRR.

    _opt_int converteste 0 → None pentru ambele, deci Access poate trimite
    0 sau null pentru campurile nesetate.
    """
    ord_ = data["ord"]
    cursor.execute("""
        INSERT INTO stg_Ord
            (Token, TipOperatie, IDORD, IDORDP, IDDF, IDRR, IDRH,
             NrORD, DataORD, Comp, CUAL, IdUnitate,
             Incarcat, Preluat, CodAngajament)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (
        token, tip,
        _strict_pos_int(ord_["IDORD"],        "ord.IDORD"),
        _strict_int(ord_["IDORDP"],           "ord.IDORDP"),
        _opt_int(ord_.get("IDDF"),            "ord.IDDF"),
        _opt_int(ord_.get("IDRR"),            "ord.IDRR"),   # v5: IDRR
        _opt_int(ord_.get("IDRH"),            "ord.IDRH"),   # v6: IDRH adaugat
        _strict_int(ord_["NrORD"],            "ord.NrORD"),
        _strict_str_nonempty(ord_["DataORD"], "ord.DataORD"),
        _strict_str_nonempty(ord_["Comp"],    "ord.Comp"),
        _opt_str(ord_.get("CUAL")),
        _opt_int(ord_.get("IdUnitate"),       "ord.IdUnitate"),
        _strict_bool(ord_.get("Incarcat"),    "ord.Incarcat"),
        _strict_bool(ord_.get("Preluat"),     "ord.Preluat"),
        _strict_str_nonempty(ord_.get("CodAngajament"), "ord.CodAngajament"),
    ))

    logger.debug(
        f"[STG][ORD] token={token} tip={tip} "
        f"IDORD={ord_['IDORD']} IDORDP={ord_['IDORDP']} "
        f"IDRR={ord_.get('IDRR')} IDRH={ord_.get('IDRH')}"
    )


def _stg_insert_parts(cursor, token: str, parts: list) -> dict:
    """
    Insereaza randurile PART (beneficiari) in stg_OrdPart.

    Returneaza map {TmpID: StgPartID} unde StgPartID este autoincrement-ul
    generat de MariaDB (lastrowid) la fiecare INSERT.
    Map-ul este propagat de _stg_insert_all catre _stg_insert_tbls /
    _stg_insert_atts / _stg_insert_docs pentru a popula coloana FK
    StgPartID din tabelele copil (NOT NULL, fara default value).
    """
    tmpid_to_stgpartid = {}   # TmpID (int) → StgPartID (int, lastrowid)

    for p in parts:
        tmp_id = _strict_pos_int(p["TmpID"], "TmpID")
        cursor.execute("""
            INSERT INTO stg_OrdPart
                (Token, TmpID, IDORDPART, IDORDPARTP,
                 Counter, DenBene, CodFiscal, ContIBAN,
                 Banca, CodPartener, IdPartener)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            token,
            tmp_id,
            _strict_pos_int(p["IDORDPART"],    "IDORDPART"),
            _strict_int(p["IDORDPARTP"],       "IDORDPARTP"),
            _strict_str(p["Counter"],          "Counter"),
            _strict_str_nonempty(p["DenBene"], "DenBene"),
            _strict_str(p["CodFiscal"],        "CodFiscal"),
            _strict_str(p["ContIBAN"],         "ContIBAN"),
            _strict_str(p["Banca"],            "Banca"),
            _opt_str(p.get("CodPartener")),
            _opt_int(p.get("IdPartener"),      "IdPartener"),
        ))
        tmpid_to_stgpartid[tmp_id] = cursor.lastrowid

    logger.debug(
        f"[STG][PART] token={token} count={len(parts)} "
        f"stgpart_map={tmpid_to_stgpartid}"
    )
    return tmpid_to_stgpartid


def _stg_insert_tbls(cursor, token: str, tbls: list, tmpid_to_stgpartid: dict):
    """
    Insereaza randurile TBL (indicatori/clasificatii) in stg_OrdTbl.

    tmpid_to_stgpartid: map {TmpID: StgPartID} returnat de _stg_insert_parts.
    StgPartID este FK NOT NULL catre stg_OrdPart.StgPartID — obligatoriu
    pentru fiecare rand TBL. Raise daca TmpID_OrdPart lipseste din map
    (indica inconsistenta intre payload parts si tbls).

    IDRP: nullable — FK read-only catre FX_Receptii_Plati.
      NULL = flux 2 (receptia nu exista inca).
      > 0  = flux 1 (randul RecPl existent, referinta de citire).
    IDRD: eliminat din v5 — coloana stearsa din FX_ORD_TBL si stg_OrdTbl.
    TmpID_OrdPart: obligatoriu in TBL (FK catre PART parinte din staging).
    """
    for t in tbls:
        tmp_id_part = _strict_pos_int(t["TmpID_OrdPart"], "TmpID_OrdPart")
        stg_part_id = tmpid_to_stgpartid.get(tmp_id_part)
        if stg_part_id is None:
            raise ValueError(
                f"stg_insert_tbls: TmpID_OrdPart={tmp_id_part} absent din "
                "map stg_OrdPart. Inconsistenta intre parts si tbls din payload."
            )
        cursor.execute("""
            INSERT INTO stg_OrdTbl
                (Token, TmpID, TmpID_OrdPart, StgPartID,
                 IDORDTBL, IDORDTBLP,
                 CodAI, CodAngajament, CodIndicator, CodSSI,
                 TotalReceptii, PlatiAnt, Valoare, Ramas,
                 IdClsf, IdClsfAcc, Explicatie, IDRP)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            token,
            _strict_pos_int(t["TmpID"],          "TmpID"),
            tmp_id_part,
            stg_part_id,                              # FK → stg_OrdPart.StgPartID
            _strict_pos_int(t["IDORDTBL"],       "IDORDTBL"),
            _strict_int(t["IDORDTBLP"],          "IDORDTBLP"),
            _strict_str_nonempty(t["CodAI"],     "CodAI"),
            _strict_str(t["CodAngajament"],      "CodAngajament"),
            _strict_str(t["CodIndicator"],       "CodIndicator"),
            _strict_str(t["CodSSI"],             "CodSSI"),
            _strict_float(t["TotalReceptii"],    "TotalReceptii"),
            _strict_float(t["PlatiAnt"],         "PlatiAnt"),
            _strict_float(t["Valoare"],          "Valoare"),
            _strict_float(t["Ramas"],            "Ramas"),
            _opt_int(t.get("IdClsf"),            "IdClsf"),
            _opt_int(t.get("IdClsfAcc"),         "IdClsfAcc"),
            _opt_str(t.get("Explicatie")),
            _opt_int(t.get("IDRP"),              "IDRP"),    # v5: IDRP adaugat, IDRD eliminat
        ))
    logger.debug(f"[STG][TBL] token={token} count={len(tbls)}")


def _stg_insert_atts(cursor, token: str, atts: list, tmpid_to_stgpartid: dict):
    """
    Insereaza randurile ATT (atasamente imagini) in stg_OrdAtt.
    TmpID_OrdPart: optional — None inseamna ATT global (nivel ORD, nu PART).
    StgPartID: NULL daca ATT e global, altfel FK → stg_OrdPart.StgPartID.
    """
    for a in atts:
        tmp_id_part = _opt_int(a.get("TmpID_OrdPart"), "TmpID_OrdPart")
        if tmp_id_part is not None:
            stg_part_id = tmpid_to_stgpartid.get(tmp_id_part)
            if stg_part_id is None:
                raise ValueError(
                    f"stg_insert_atts: TmpID_OrdPart={tmp_id_part} absent din "
                    "map stg_OrdPart. Inconsistenta intre parts si atts din payload."
                )
        else:
            stg_part_id = None
        cursor.execute("""
            INSERT INTO stg_OrdAtt
                (Token, TmpID, TmpID_OrdPart, StgPartID,
                 IDORDATT, IDORDATTP, Imagine)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (
            token,
            _strict_pos_int(a["TmpID"],        "TmpID"),
            tmp_id_part,
            stg_part_id,                           # NULL daca ATT global
            _strict_pos_int(a["IDORDATT"],     "IDORDATT"),
            _strict_int(a["IDORDATTP"],        "IDORDATTP"),
            _strict_str_nonempty(a["Imagine"], "Imagine"),
        ))
    logger.debug(f"[STG][ATT] token={token} count={len(atts)}")


def _stg_insert_docs(cursor, token: str, docs: list, tmpid_to_stgpartid: dict):
    """
    Insereaza randurile DOC (documente justificative) in stg_OrdDoc.
    TmpID_OrdPart: optional — None inseamna DOC global (nivel ORD, nu PART).
    StgPartID: NULL daca DOC e global, altfel FK → stg_OrdPart.StgPartID.
    TipDoc == 'text' → NumeDoc poate fi null.
    """
    for d in docs:
        tmp_id_part = _opt_int(d.get("TmpID_OrdPart"), "TmpID_OrdPart")
        if tmp_id_part is not None:
            stg_part_id = tmpid_to_stgpartid.get(tmp_id_part)
            if stg_part_id is None:
                raise ValueError(
                    f"stg_insert_docs: TmpID_OrdPart={tmp_id_part} absent din "
                    "map stg_OrdPart. Inconsistenta intre parts si docs din payload."
                )
        else:
            stg_part_id = None
        cursor.execute("""
            INSERT INTO stg_OrdDoc
                (Token, TmpID, TmpID_OrdPart, StgPartID,
                 IDORDDOC, IDORDDOCP,
                 DocJust, NumeDoc, TipDoc)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            token,
            _strict_pos_int(d["TmpID"],            "TmpID"),
            tmp_id_part,
            stg_part_id,                               # NULL daca DOC global
            _strict_pos_int(d["IDORDDOC"],         "IDORDDOC"),
            _strict_int(d["IDORDDOCP"],            "IDORDDOCP"),
            _strict_str_nonempty(d.get("DocJust"), "DocJust"),
            _opt_str(d.get("NumeDoc")),
            _strict_str_nonempty(d["TipDoc"],      "TipDoc"),
        ))
    logger.debug(f"[STG][DOC] token={token} count={len(docs)}")


def _stg_insert_tbl_recs(cursor, token: str, tbl_recs: list):
    """
    Insereaza randurile TBL_REC (plati individuale per TBL) in stg_OrdTblRec.
    TmpID_OrdTbl: FK catre tmpFX_ORD_TBL.ID (parintele TBL din Access).
    IDORDREC: Access PK (pozitiv = pre-calculat de VBA, negativ = generat de server).
    IDRP: FK catre FX_Receptii_Plati (obligatoriu pozitiv).
    """
    for r in tbl_recs:
        cursor.execute("""
            INSERT INTO stg_OrdTblRec
                (Token, TmpID, TmpID_OrdTbl, IDORDREC, IDORDRECP, IDRP, Valoare)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (
            token,
            _strict_pos_int(r["TmpID"],         "TmpID"),
            _strict_pos_int(r["TmpID_OrdTbl"],  "TmpID_OrdTbl"),
            _strict_int(r["IDORDREC"],          "IDORDREC"),
            _strict_int(r["IDORDRECP"],         "IDORDRECP"),
            _strict_pos_int(r["IDRP"],          "IDRP"),
            _strict_float(r["Valoare"],         "Valoare"),
        ))
    logger.debug(f"[STG][REC] token={token} count={len(tbl_recs)}")


def _stg_insert_all(cursor, token: str, tip: str, data: dict):
    """
    Insereaza complet un payload in staging (toate tabelele stg_Ord*).
    Ordinea este impusa de FK: Ord → Parts → Tbls → Atts → Docs → TblRecs.
    Totul ruleaza in aceeasi tranzactie (apelant: _run_with_retry).

    _stg_insert_parts returneaza map {TmpID: StgPartID} care este
    propagat explicit catre TBL/ATT/DOC pentru popularea coloanei
    FK StgPartID (NOT NULL in stg_OrdTbl, nullable in stg_OrdAtt/Doc).
    """
    _stg_insert_ord(cursor,   token, tip, data)
    tmpid_to_stgpartid = _stg_insert_parts(cursor, token, data.get("parts", []))
    _stg_insert_tbls(cursor,  token, data.get("tbls",  []), tmpid_to_stgpartid)
    _stg_insert_atts(cursor,  token, data.get("atts",  []), tmpid_to_stgpartid)
    _stg_insert_docs(cursor,  token, data.get("docs",  []), tmpid_to_stgpartid)
    _stg_insert_tbl_recs(cursor, token, data.get("tbl_recs", []))
    logger.info(f"[STG][ALL] token={token} tip={tip} payload inserted in staging")