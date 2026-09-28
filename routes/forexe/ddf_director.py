# routes/forexe/ddf_director.py
"""
The director's list (slice 0081-06): the DDF revisions waiting for the Ordonator's signature,
across EVERY unit the logged-in director has in `Unitati_Utilizatori` with `Rol = 'Director'`.

    GET /api/forexe/ddf/director/de-semnat

A director works across several units, like the accountant (`Contabil`): one row per unit in
`AVACONT_COMUN.Unitati_Utilizatori`. A session is still bound to ONE unit database (the one
picked at login); only this list reads the others, through the service account. Opening and
signing a document stays on the normal routes of the session's database -- the client logs in
again on the document's unit first (see KBot.App DirectorForm).

A revision is waiting when:
  * it has a PDF on the server (`FX_DDF_PDF`), and
  * it is past the send: `StareTrimitere = 3` (final PDF made by K-BOT), or forexecab already has
    it (`Incarcat` / `Preluat` -- the revisions sent before slice 0081), and
  * `Semnatura` carries A and B and not Ordonator.
A unit whose database cannot be read is left out and named in `unitati_necitite`; it does not
stop the list.
"""
import json
import logging

import mysql.connector
from flask import g, current_app

from routes.auth.guard import require_session
from utils.database import get_kbot_comun_connection, get_kbot_connection

from . import forexe_bp
from .ddf_stare import STAGE_FINAL_PDF, are_stare_trimitere

logger = logging.getLogger(__name__)

ROL_DIRECTOR = "Director"

_SQL_UNITATI = (
    "SELECT ua.DC AS DC, u.NumeUnitate AS NumeUnitate "
    "  FROM Unitati_Utilizatori ua JOIN Unitati u ON u.DC = ua.DC "
    " WHERE ua.UN = %s AND ua.Rol = %s "
    " ORDER BY u.NumeUnitate"
)

# {stare} = "r.StareTrimitere = 3" or "0" on a database without the 0081 column.
# Signatures compared as a comma list with the spaces taken out ("A, B" == "A,B").
_SQL_DE_SEMNAT = (
    "SELECT r.IDREV, r.IDDF, d.CUAL, d.CodAngajament, d.ObiectDDF, r.NumarRev, r.DataRev, "
    "       r.Desc_Scurta, r.Semnatura, p.Sha256, "
    "       COALESCE((SELECT SUM(sa.ValCur) FROM FX_DDF_REV_SA sa WHERE sa.IDREV = r.IDREV), 0) AS Total "
    "  FROM FX_DDF_REV r "
    "  JOIN FX_DDF d ON d.IDDF = r.IDDF "
    "  JOIN FX_DDF_PDF p ON p.IDREV = r.IDREV "
    " WHERE ({stare} OR COALESCE(r.Incarcat, 0) = 1 OR COALESCE(r.Preluat, 0) = 1) "
    "   AND FIND_IN_SET('A', REPLACE(COALESCE(r.Semnatura, ''), ' ', '')) > 0 "
    "   AND FIND_IN_SET('B', REPLACE(COALESCE(r.Semnatura, ''), ' ', '')) > 0 "
    "   AND FIND_IN_SET('Ordonator', REPLACE(COALESCE(r.Semnatura, ''), ' ', '')) = 0 "
    " ORDER BY r.DataRev, d.CodAngajament, r.NumarRev"
)


def _json_utf8(payload, status):
    body = json.dumps(payload, ensure_ascii=False, default=str)
    return current_app.response_class(body, status=status, mimetype="application/json")


def _unitati_directorului(username: str) -> list:
    conn = None
    try:
        conn = get_kbot_comun_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(_SQL_UNITATI, (username, ROL_DIRECTOR))
        return cursor.fetchall()
    finally:
        if conn is not None and conn.is_connected():
            conn.close()


def _revizii_din(dc: str, nume_unitate: str) -> list:
    conn = None
    try:
        conn = get_kbot_connection(dc)
        cursor = conn.cursor()
        stare = f"r.StareTrimitere = {STAGE_FINAL_PDF}" if are_stare_trimitere(cursor, dc) else "0"
        cursor.execute(_SQL_DE_SEMNAT.format(stare=stare))
        rezultat = []
        for (idrev, iddf, cual, cod, obiect, numar_rev, data_rev, desc_scurta, semnatura,
             sha, total) in cursor.fetchall():
            rezultat.append({
                "db_name": dc,
                "nume_unitate": nume_unitate,
                "idrev": int(idrev),
                "iddf": int(iddf or 0),
                "cual": int(cual or 0),
                "cod_angajament": cod or "",
                "obiect_ddf": obiect or "",
                "numar_rev": int(numar_rev or 0),
                "data_rev": data_rev.isoformat() if data_rev is not None else None,
                "desc_scurta": desc_scurta or "",
                "total": float(total or 0),
                "semnatura": semnatura or "",
                "pdf_sha256": sha or "",
            })
        return rezultat
    finally:
        if conn is not None and conn.is_connected():
            conn.close()


@forexe_bp.route("/api/forexe/ddf/director/de-semnat", methods=["GET"])
@require_session
def get_ddf_director_de_semnat():
    """{"revizii": [...], "unitati_necitite": ["<NumeUnitate>", ...]}. Only for a director
    session (the role of the unit logged into); anyone else gets 403."""
    s = g.session
    rol = str((s.ctx or {}).get("Role") or "")
    if rol != ROL_DIRECTOR:
        return _json_utf8({"error": "Lista documentelor de semnat este doar pentru director."}, 403)
    try:
        unitati = _unitati_directorului(s.username)
    except mysql.connector.Error as err:
        logger.error("[forexe.ddf_director] Unitati_Utilizatori %s: %s", s.username, err)
        return _json_utf8({"error": "Unitățile directorului nu au putut fi citite."}, 500)

    revizii, necitite = [], []
    for u in unitati:
        dc, nume = str(u.get("DC") or ""), str(u.get("NumeUnitate") or "")
        try:
            revizii.extend(_revizii_din(dc, nume))
        except mysql.connector.Error as err:
            logger.error("[forexe.ddf_director] %s: %s", dc, err)
            necitite.append(nume or dc)
    return _json_utf8({"revizii": revizii, "unitati_necitite": necitite}, 200)
