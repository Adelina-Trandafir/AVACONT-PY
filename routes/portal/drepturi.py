"""
Rights per SECTION of the site (slice AD10-01).

A user holds ROLES per unit (AVACONT_COMUN.Utilizatori_Roluri); a role belongs to one section
(AVACONT_COMUN.Roluri.Sectiune: AD = ADECHIT, later VR, AV) and stands for one operation
(Roluri.Operatie), e.g. AD_CITIRE = read. Everything is read from the server on each call -- never from the client.

    sections_of(email, db_name)             -> ['AD', ...] the sections the user may enter in that unit
    operations_of(email, db_name, section)  -> {'read', 'catalog', ...}
    allowed(email, db_name, section, op)    -> bool
"""
import logging

import mysql.connector

from utils.database import get_kbot_comun_connection

logger = logging.getLogger(__name__)


def _rows(sql, params):
    conn = None
    try:
        conn = get_kbot_comun_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute(sql, params)
        return cur.fetchall()
    except Exception:
        logger.exception("drepturi: read failed")
        raise
    finally:
        if conn is not None and conn.is_connected():
            conn.close()


def _safe_rows(sql, params):
    """Backwards compatibility: while the role tables are not in AVACONT_COMUN yet (script
    sql/AD10_01 not applied) or the read fails, the user simply has NO section rights -- closed, not
    open -- and the rest of the portal (sign-in, units, data views) keeps working as before."""
    try:
        return _rows(sql, params)
    except mysql.connector.Error:
        return []


SECTION_KB = "KB"


def sections_of(email, db_name):
    """The sections the user may enter in this unit. KB: anybody with a row in Unitati_Utilizatori
    for the unit; the other sections: the roles granted in Utilizatori_Roluri."""
    if not email or not db_name:
        return []
    granted = _safe_rows(
        "SELECT DISTINCT r.Sectiune AS Sectiune FROM Utilizatori_Roluri ur "
        "JOIN Roluri r ON r.IdRol = ur.IdRol WHERE ur.UN = %s AND ur.DC = %s",
        (email, db_name),
    )
    member = _safe_rows("SELECT 1 AS x FROM Unitati_Utilizatori WHERE UN = %s AND DC = %s", (email, db_name))
    found = {r["Sectiune"] for r in granted}
    if member:
        found.add(SECTION_KB)
    return sorted(found)


def operations_of(email, db_name, section):
    """The operations the user may do in this section and unit. KB: from the role named in
    Unitati_Utilizatori.Rol (role code KB_<ROL>); the other sections: from the granted roles."""
    if not email or not db_name or not section:
        return set()
    if section == SECTION_KB:
        rows = _safe_rows(
            "SELECT DISTINCT ro.Operatie AS Operatie FROM Unitati_Utilizatori uu "
            "JOIN Roluri r ON r.Cod = CONCAT('KB_', UPPER(uu.Rol)) AND r.Sectiune = %s "
            "JOIN Roluri_Operatii ro ON ro.IdRol = r.IdRol WHERE uu.UN = %s AND uu.DC = %s",
            (section, email, db_name),
        )
    else:
        rows = _safe_rows(
            "SELECT DISTINCT ro.Operatie AS Operatie FROM Utilizatori_Roluri ur "
            "JOIN Roluri r ON r.IdRol = ur.IdRol AND r.Sectiune = %s "
            "JOIN Roluri_Operatii ro ON ro.IdRol = r.IdRol WHERE ur.UN = %s AND ur.DC = %s",
            (section, email, db_name),
        )
    return {r["Operatie"] for r in rows}


def allowed(email, db_name, section, operation):
    return operation in operations_of(email, db_name, section)
