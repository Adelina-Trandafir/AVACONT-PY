# routes/ord/validation.py
"""
Validare payload ORD (Sectiunile 2-3 din monolit).

Parsarea generica (_strict_*, _opt_int, _opt_str) este refolosita din
utils.parsing. _mariadb_pk ramane aici pentru ca incapsuleaza regula
ADD/MOD specifica ORD (semnul PK ca discriminator nou/existent).

ATENTIE semantica _opt_int (din utils.parsing): converteste 0 -> None
(conventia Access pentru FK nesetat). ORD se bazeaza pe aceasta
conventie pentru IDRR/IDRH/IDRP/IdClsf etc.
"""
from flask import request

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

from . import (
    logger,
    MAX_PAYLOAD_BYTES,
    MAX_PARTS,
    MAX_TBLS,
    MAX_ATTS,
    MAX_DOCS,
    MAX_RECS,
)


# ===========================================================================
# HELPER SPECIFIC ORD — MariaDB PK discriminator ADD/MOD
# ===========================================================================

def _mariadb_pk(v, field: str, tip: str) -> int:
    """
    Valideaza un camp MariaDB PK conform regulii ADD/MOD:
      ADD: trebuie < 0 (rand nou — niciodata pozitiv la adaugare initiala)
      MOD: poate fi < 0 (rand nou adaugat in MOD) sau > 0 (rand existent)
      Niciodata 0.
    """
    result = _strict_int(v, field)
    if result == 0:
        raise ValueError(
            f"Camp '{field}': 0 invalid — trebuie < 0 (nou) sau > 0 (existent)"
        )
    if tip == "ADD" and result > 0:
        raise ValueError(
            f"Camp '{field}': {result} > 0 invalid la ADD — "
            "la adaugare MariaDB PK trebuie sa fie negativ"
        )
    return result


# ===========================================================================
# VALIDARE PAYLOAD
# ===========================================================================

def _check_content_length():
    """Reject payload > 2 MB inainte de parsare JSON."""
    if request.content_length and request.content_length > MAX_PAYLOAD_BYTES:
        raise ValueError(
            f"Payload prea mare: {request.content_length:,} bytes "
            f"(maxim {MAX_PAYLOAD_BYTES:,} bytes = 2 MB)"
        )


def _validate_ord_header(ord_: dict, tip: str):
    """
    Valideaza header-ul ORD (campurile din FX_ORD / stg_Ord).

    IDRR (nullable):
      Flux 1 (date FOREXE existente): IDRR > 0 (receptia reala).
      Flux 2 (creat in AVACONT): IDRR = NULL (receptia nu exista inca).
      Serverul nu valideaza existenta IDRR in FX_Receptii_R —
      responsabilitatea consistentei apartine Access/VBA.

    IDRH (nullable):
      FK catre FX_Receptii_H (istoricul receptiei).
      Flux 1: IDRH > 0 (intrarea din registrul istoric existenta).
      Flux 2: IDRH = NULL (nu exista inca intrare in istoric).
      Independent de IDRR — pot coexista, pot fi ambele NULL, sau oricare singur.
      Serverul nu valideaza existenta IDRH in FX_Receptii_H —
      responsabilitatea consistentei apartine Access/VBA.
    """
    if not isinstance(ord_, dict):
        raise ValueError("'ord' trebuie sa fie dict")

    # IDORDP: MariaDB PK — ADD < 0 (rand nou), MOD > 0 (rand existent)
    idordp = _strict_int(ord_.get("IDORDP"), "ord.IDORDP")
    if tip == "ADD" and idordp >= 0:
        raise ValueError(f"ord.IDORDP={idordp}: la ADD trebuie sa fie negativ")
    if tip == "MOD" and idordp <= 0:
        raise ValueError(f"ord.IDORDP={idordp}: la MOD trebuie sa fie > 0")

    # IDORD: Access PK, mereu > 0 (pre-calculat de VBA)
    _strict_pos_int(ord_.get("IDORD"), "ord.IDORD")

    # Campuri obligatorii header
    _strict_int(ord_.get("NrORD"),             "ord.NrORD")
    _strict_str_nonempty(ord_.get("DataORD"),  "ord.DataORD")
    _strict_str_nonempty(ord_.get("Comp"),     "ord.Comp")
    _strict_bool(ord_.get("Incarcat"),         "ord.Incarcat")
    _strict_bool(ord_.get("Preluat"),          "ord.Preluat")

    # IDRR: nullable — NULL flux 2, pozitiv flux 1
    _opt_int(ord_.get("IDRR"), "ord.IDRR")

    # IDRH: nullable — FK catre FX_Receptii_H (istoricul receptiei, v6)
    _opt_int(ord_.get("IDRH"), "ord.IDRH")


def _validate_part(p: dict, idx: int, tip: str):
    """Valideaza un rand PART (beneficiar plata)."""
    pfx = f"parts[{idx}]"

    _strict_pos_int(p.get("TmpID"),     f"{pfx}.TmpID")

    # IDORDPARTP: MariaDB PK — ADD < 0, MOD < 0 (nou) sau > 0 (existent)
    _mariadb_pk(p.get("IDORDPARTP"),    f"{pfx}.IDORDPARTP", tip)

    # IDORDPART: Access PK, mereu > 0
    _strict_pos_int(p.get("IDORDPART"), f"{pfx}.IDORDPART")

    _strict_str_nonempty(p.get("DenBene"),  f"{pfx}.DenBene")
    _strict_str(p.get("Counter"),           f"{pfx}.Counter")
    _strict_str(p.get("CodFiscal"),         f"{pfx}.CodFiscal")
    _strict_str(p.get("ContIBAN"),          f"{pfx}.ContIBAN")
    _strict_str(p.get("Banca"),             f"{pfx}.Banca")
    _opt_str(p.get("CodPartener"))
    _opt_int(p.get("IdPartener"),           f"{pfx}.IdPartener")


def _validate_tbl(t: dict, idx: int, valid_tmpid_set: set, tip: str):
    """
    Valideaza un rand TBL (indicator/clasificatie per beneficiar).

    TmpID_OrdPart: obligatoriu, trebuie sa corespunda unui TmpID din parts.
    IDRP (nullable):
      Flux 1: IDRP > 0 (referinta read-only la FX_Receptii_Plati).
      Flux 2: IDRP = NULL.
      Serverul NU valideaza existenta IDRP in FX_Receptii_Plati —
      responsabilitatea consistentei apartine Access/VBA.
      Unicitatea IDRP per ORD este gestionata de Access, nu de MariaDB.
    IDRD: eliminat din v5 — nu se mai trimite si nu se mai valideaza.
    """
    pfx = f"tbls[{idx}]"

    _strict_pos_int(t.get("TmpID"), f"{pfx}.TmpID")

    # TmpID_OrdPart: FK catre PART parinte — obligatoriu in TBL
    tmp_id_part = _strict_pos_int(t.get("TmpID_OrdPart"), f"{pfx}.TmpID_OrdPart")
    if tmp_id_part not in valid_tmpid_set:
        raise ValueError(
            f"{pfx}.TmpID_OrdPart={tmp_id_part} nu corespunde "
            "niciunui TmpID din lista parts"
        )

    # IDORDTBLP: MariaDB PK
    _mariadb_pk(t.get("IDORDTBLP"), f"{pfx}.IDORDTBLP", tip)

    # IDORDTBL: Access PK, mereu > 0
    _strict_pos_int(t.get("IDORDTBL"), f"{pfx}.IDORDTBL")

    _strict_str_nonempty(t.get("CodAI"),       f"{pfx}.CodAI")
    _strict_str(t.get("CodAngajament"),         f"{pfx}.CodAngajament")
    _strict_str(t.get("CodIndicator"),          f"{pfx}.CodIndicator")
    _strict_str(t.get("CodSSI"),                f"{pfx}.CodSSI")
    _strict_float(t.get("TotalReceptii"),       f"{pfx}.TotalReceptii")
    _strict_float(t.get("PlatiAnt"),            f"{pfx}.PlatiAnt")
    _strict_float(t.get("Valoare"),             f"{pfx}.Valoare")
    _strict_float(t.get("Ramas"),               f"{pfx}.Ramas")
    _opt_int(t.get("IdClsf"),                   f"{pfx}.IdClsf")
    _opt_int(t.get("IdClsfAcc"),                f"{pfx}.IdClsfAcc")
    # IDRP: nullable — NULL flux 2, pozitiv flux 1
    _opt_int(t.get("IDRP"),                     f"{pfx}.IDRP")
    # IDRD: eliminat din v5 — nu se mai valideaza


def _validate_tbl_rec(r: dict, idx: int, valid_tbl_tmpid_set: set):
    pfx = f"tbl_recs[{idx}]"

    _strict_pos_int(r.get("TmpID"),        f"{pfx}.TmpID")

    tmp_id_tbl = _strict_pos_int(r.get("TmpID_OrdTbl"), f"{pfx}.TmpID_OrdTbl")
    if tmp_id_tbl not in valid_tbl_tmpid_set:
        raise ValueError(
            f"{pfx}.TmpID_OrdTbl={tmp_id_tbl} nu corespunde niciunui TmpID din tbls"
        )

    _strict_int(r.get("IDORDREC"),  f"{pfx}.IDORDREC")   # negativ=ADD, pozitiv=EDIT
    _strict_int(r.get("IDORDRECP"), f"{pfx}.IDORDRECP")   # negativ=ADD, pozitiv=EDIT
    _strict_pos_int(r.get("IDRP"),  f"{pfx}.IDRP")
    _strict_float(r.get("Valoare"), f"{pfx}.Valoare")


def _validate_att(a: dict, idx: int, valid_tmpid_set: set, tip: str):
    """
    Valideaza un rand ATT (atasament imagine).
    TmpID_OrdPart: optional — ATT poate fi global (fara PART parinte).
    """
    pfx = f"atts[{idx}]"

    _strict_pos_int(a.get("TmpID"), f"{pfx}.TmpID")

    # TmpID_OrdPart: optional in ATT (poate fi atasat la nivel ORD, nu PART)
    tmp_id_part = _opt_int(a.get("TmpID_OrdPart"), f"{pfx}.TmpID_OrdPart")
    if tmp_id_part is not None and tmp_id_part not in valid_tmpid_set:
        raise ValueError(
            f"{pfx}.TmpID_OrdPart={tmp_id_part} nu corespunde "
            "niciunui TmpID din lista parts"
        )

    # IDORDATTP: MariaDB PK
    _mariadb_pk(a.get("IDORDATTP"), f"{pfx}.IDORDATTP", tip)

    # IDORDATT: Access PK, mereu > 0
    _strict_pos_int(a.get("IDORDATT"), f"{pfx}.IDORDATT")

    _strict_str_nonempty(a.get("Imagine"), f"{pfx}.Imagine")


def _validate_doc(d: dict, idx: int, valid_tmpid_set: set, tip: str):
    """
    Valideaza un rand DOC (document justificativ).
    TmpID_OrdPart: optional — DOC poate fi global (fara PART parinte).
    TipDoc == 'text' → NumeDoc poate fi null (text liber fara fisier).
    TipDoc != 'text' → NumeDoc obligatoriu (nume fisier).
    """
    logger.debug(f"Validating DOC index={idx} data={d}")
    pfx = f"docs[{idx}]"

    _strict_pos_int(d.get("TmpID"), f"{pfx}.TmpID")

    # TmpID_OrdPart: optional in DOC
    tmp_id_part = _opt_int(d.get("TmpID_OrdPart"), f"{pfx}.TmpID_OrdPart")
    if tmp_id_part is not None and tmp_id_part not in valid_tmpid_set:
        raise ValueError(
            f"{pfx}.TmpID_OrdPart={tmp_id_part} nu corespunde "
            "niciunui TmpID din lista parts"
        )

    # IDORDDOCP: MariaDB PK
    _mariadb_pk(d.get("IDORDDOCP"), f"{pfx}.IDORDDOCP", tip)

    # IDORDDOC: Access PK, mereu > 0
    _strict_pos_int(d.get("IDORDDOC"), f"{pfx}.IDORDDOC")

    tip_doc = _strict_str_nonempty(d.get("TipDoc"), f"{pfx}.TipDoc")
    if tip_doc != "text":
        # Document real — nume fisier obligatoriu
        _strict_str_nonempty(d.get("NumeDoc"), f"{pfx}.NumeDoc")
    else:
        # Text liber — NumeDoc poate lipsi
        _opt_str(d.get("NumeDoc"))

    # DocJust: optional pe ambele tipuri
    _opt_str(d.get("DocJust"))


def _validate_payload(data: dict, tip: str):
    """
    Valideaza complet payload-ul primit de la VBA.
    Ordinea: header → limits → parts → valid_tmpid_set → tbls/atts/docs → tbl_recs.
    valid_tmpid_set este construit din TmpID-urile PART validate,
    si folosit pentru cross-validarea TmpID_OrdPart din TBL/ATT/DOC.
    valid_tbl_tmpid_set este construit din TmpID-urile TBL validate,
    si folosit pentru cross-validarea TmpID_OrdTbl din TBL_REC.
    """
    if not isinstance(data, dict):
        raise ValueError("Payload trebuie sa fie dict")
    if "ord" not in data:
        raise ValueError("Cheie 'ord' lipsa din payload")

    _validate_ord_header(data["ord"], tip)

    for key in ("parts", "tbls", "atts", "docs", "tbl_recs"):
        if not isinstance(data.get(key, []), list):
            raise ValueError(f"'{key}' trebuie sa fie list")

    parts = data.get("parts", [])
    tbls  = data.get("tbls",  [])
    atts  = data.get("atts",  [])
    docs  = data.get("docs",  [])

    if len(parts) > MAX_PARTS:
        raise ValueError(f"Prea multe parts: {len(parts)} (maxim {MAX_PARTS})")
    if len(tbls)  > MAX_TBLS:
        raise ValueError(f"Prea multe tbls: {len(tbls)} (maxim {MAX_TBLS})")
    if len(atts)  > MAX_ATTS:
        raise ValueError(f"Prea multe atts: {len(atts)} (maxim {MAX_ATTS})")
    if len(docs)  > MAX_DOCS:
        raise ValueError(f"Prea multe docs: {len(docs)} (maxim {MAX_DOCS})")

    tbl_recs = data.get("tbl_recs", [])
    if len(tbl_recs) > MAX_RECS:
        raise ValueError(f"Prea multe tbl_recs: {len(tbl_recs)} (maxim {MAX_RECS})")

    for i, p in enumerate(parts):
        _validate_part(p, i, tip)

    # Construieste set TmpID PART pentru cross-validare copii
    valid_tmpid_set = {
        _strict_pos_int(p["TmpID"], f"parts[{i}].TmpID")
        for i, p in enumerate(parts)
    }

    # Verifica unicitate TmpID in parts (fiecare rand tmp are ID unic)
    tmp_ids = [_strict_pos_int(p["TmpID"], "TmpID") for p in parts]
    if len(tmp_ids) != len(set(tmp_ids)):
        raise ValueError("TmpID duplicat in 'parts'")

    for i, t in enumerate(tbls):
        _validate_tbl(t, i, valid_tmpid_set, tip)

    for i, a in enumerate(atts):
        _validate_att(a, i, valid_tmpid_set, tip)

    for i, d in enumerate(docs):
        _validate_doc(d, i, valid_tmpid_set, tip)

    valid_tbl_tmpid_set = {
        _strict_pos_int(t["TmpID"], f"tbls[{i}].TmpID")
        for i, t in enumerate(tbls)
    }
    for i, r in enumerate(tbl_recs):
        _validate_tbl_rec(r, i, valid_tbl_tmpid_set)