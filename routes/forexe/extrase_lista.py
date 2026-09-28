# routes/forexe/extrase_lista.py
"""
Reading the bank statements for the «Extrase» view and the «Extrase de cont» window
(slice 0080-02 / 0080-03). The import lives in extrase.py; this file only reads.

CONTRACT
--------
GET /api/forexe/extrase/lista?cod=<CodAngajament>   -- the view: one angajament
GET /api/forexe/extrase/lista                       -- the window: everything
    -> 200 { "antete": [ {...}, ... ], "operatiuni": [ {...}, ... ] }

One round trip, two flat lists; the client builds the tree and both grids from them,
the same way RezervariView / DdfView do.

  antete     = FX_Extrase_H, one row per account header, with its statement's date
               (FX_Extrase_F.DataExtras) and the classification (Clasificatii.Clsf by
               the MariaDB key -- `IdClsf` is Clasificatii.IDClsf since 0080-01).
  operatiuni = FX_Extrase, one row per operation, linked to its header by `idfxh`.

WITH `cod`: operations whose `CodContract` is the angajament (CodContract = CodAngajament,
RandContract = CodIndicator; a row can have only CodContract, or neither -- operator,
24.09.2026), and only the headers that own at least one of them.
WITHOUT `cod`: every header, with or without operations, and every operation -- including
the ones with no CodContract at all, which stand for something other than an angajament.

WHERE A HEADER SITS IN THE TREE (operator, 24.09.2026) is the client's job: under every
day on which one of its operations has `DataBanca`; a header with no operation sits on its
statement's `DataExtras`. Its «Data» column shows `DataExtras`.

Dates travel as ISO `YYYY-MM-DD`. `DataDoc` is a DATE since 0080-01.

Scope: the session's database, never the request's -- same as every forexe reader.
"""
import json
import logging

from flask import request, g, current_app

from routes.auth.guard import require_session
from utils.database import get_kbot_connection

from . import forexe_bp

logger = logging.getLogger(__name__)

_ANTETE_SQL = (
    "SELECT H.IDEXH, H.IDEXF, F.DataExtras, F.NumarExtras, H.IdClsf, "
    "  (SELECT C.Clsf FROM Clasificatii C WHERE C.IDClsf = H.IdClsf LIMIT 1) AS Clsf, "
    "  (SELECT C.Denumire FROM Clasificatii C WHERE C.IDClsf = H.IdClsf LIMIT 1) AS Denumire, "
    "  H.CodIBAN, H.Cont, H.SID, H.SIC, H.RPD, H.RPC, H.TSD, H.TSC, H.SFD, H.SFC "
    "FROM FX_Extrase_H H "
    "LEFT JOIN FX_Extrase_F F ON F.IDEXF = H.IDEXF "
    "{filtru}"
    "ORDER BY F.DataExtras, Clsf, H.IDEXH"
)
_ANTETE_FILTRU_COD = (
    "WHERE EXISTS (SELECT 1 FROM FX_Extrase E "
    "              WHERE E.IDFXH = H.IDEXH AND E.CodContract = %s) "
)

_OPERATIUNI_SQL = (
    "SELECT E.IDFXE, E.IDFXH, E.DataBanca, E.DataDoc, E.NrDoc, E.Referinta, "
    "  E.ReferintaDest, E.platitor_nume, E.platitor_cui, E.platitor_iban, "
    "  E.suma_debit, E.suma_credit, E.Explicatii, E.CodContract, E.RandContract, "
    "  E.CodProgram, E.CodAI "
    "FROM FX_Extrase E "
    "{filtru}"
    "ORDER BY E.DataBanca, E.IDFXE"
)
_OPERATIUNI_FILTRU_COD = "WHERE E.CodContract = %s "


def _json_utf8(payload, status):
    """JSON with LITERAL diacritics: names and explanations are Romanian text."""
    body = json.dumps(payload, ensure_ascii=False)
    return current_app.response_class(body, status=status, mimetype="application/json")


def _iso(value):
    """DATE / DATETIME -> 'YYYY-MM-DD', or None. The time of day is not shown anywhere."""
    if value is None:
        return None
    try:
        return value.date().isoformat()
    except AttributeError:
        return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _num(value):
    """Money column -> float; None -> 0.0, so the grid shows «0,00» and totals add up."""
    return float(value) if value is not None else 0.0


def _int(value):
    return int(value) if value is not None else None


def antet_row(r) -> dict:
    (idexh, idexf, data_extras, numar_extras, id_clsf, clsf, denumire, cod_iban, cont,
     sid, sic, rpd, rpc, tsd, tsc, sfd, sfc) = r
    return {
        "idexh": _int(idexh),
        "idexf": _int(idexf),
        "data_extras": _iso(data_extras),
        "numar_extras": numar_extras,
        "id_clsf": _int(id_clsf),
        "clsf": clsf,
        "denumire": denumire,
        "cod_iban": cod_iban,
        "cont": cont,
        "sid": _num(sid), "sic": _num(sic),
        "rpd": _num(rpd), "rpc": _num(rpc),
        "tsd": _num(tsd), "tsc": _num(tsc),
        "sfd": _num(sfd), "sfc": _num(sfc),
    }


def operatiune_row(r) -> dict:
    (idfxe, idfxh, data_banca, data_doc, nr_doc, referinta, referinta_dest,
     platitor_nume, platitor_cui, platitor_iban, suma_debit, suma_credit, explicatii,
     cod_contract, rand_contract, cod_program, cod_ai) = r
    return {
        "idfxe": _int(idfxe),
        "idfxh": _int(idfxh),
        "data_banca": _iso(data_banca),
        "data_doc": _iso(data_doc),
        "nr_doc": nr_doc,
        "referinta": referinta,
        "referinta_dest": referinta_dest,
        "platitor_nume": platitor_nume,
        "platitor_cui": platitor_cui,
        "platitor_iban": platitor_iban,
        "suma_debit": _num(suma_debit),
        "suma_credit": _num(suma_credit),
        "explicatii": explicatii,
        "cod_contract": cod_contract,
        "rand_contract": rand_contract,
        "cod_program": cod_program,
        "cod_ai": cod_ai,
    }


@forexe_bp.route("/api/forexe/extrase/lista", methods=["GET"])
@require_session
def get_extrase_lista():
    """Headers + operations, for one angajament (`cod`) or for the whole database.

    An angajament without statements is not a 404: 200 with two empty lists.
    """
    cod = request.args.get("cod")
    cod = None if cod is None or str(cod).strip() == "" else str(cod).strip()
    db_name = g.session.db_name

    conn = None
    try:
        conn = get_kbot_connection(db_name)
        cursor = conn.cursor()
        params = (cod,) if cod is not None else ()

        cursor.execute(_ANTETE_SQL.format(
            filtru=_ANTETE_FILTRU_COD if cod is not None else ""), params)
        antete = [antet_row(r) for r in cursor.fetchall()]

        cursor.execute(_OPERATIUNI_SQL.format(
            filtru=_OPERATIUNI_FILTRU_COD if cod is not None else ""), params)
        operatiuni = [operatiune_row(r) for r in cursor.fetchall()]

        logger.info("[forexe.extrase.lista] %s: cod=%s -> %s antete, %s operatiuni",
                    db_name, cod or "(toate)", len(antete), len(operatiuni))
        return _json_utf8({"antete": antete, "operatiuni": operatiuni}, 200)
    except Exception as e:
        # No swallowing: an empty list would tell the operator there are no statements.
        logger.error(f"[forexe.extrase.lista] {e}", exc_info=True)
        return _json_utf8({"error": f"Eroare la citirea extraselor: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()
