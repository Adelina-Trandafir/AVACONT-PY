# routes/forexe/ddf_flux.py
"""
The routes of the DDF signing flow for DDFs created in K-BOT (slice 0124-03). Rules and the restricted roles:
`ddf_flux_core.py`.

    GET  /api/forexe/ddf/de-semnat                      what is addressed to the logged-in user, every role
    GET  /api/forexe/ddf/flux/comp-rol                  compartments of the unit + the compartment -> role table
    PUT  /api/forexe/ddf/flux/comp-rol                  replaces the table

The list spans every unit the user has in `Unitati_Utilizatori`, each read with the role the user has THERE
(like the director's list, ddf_director.py). A unit whose database cannot be read is named in `unitati_necitite`
and does not stop the list. A unit where sql/0124 has not been carried yet has no flow and adds nothing.

A revision enters the flow when it is CREATED (ddf_edit.py's save sets DeSemnat from the compartment, see ddf_flux_core.rol_pentru_revizie_noua).
"""
import logging

import mysql.connector
from flask import g, request

from routes.auth.guard import require_session
from utils.database import get_kbot_comun_connection, get_kbot_connection

from . import forexe_bp
from .ddf_flux_core import (
    ROLURI_FLUX, ROLURI_RESTRANSE, are_comp_rol, are_de_semnat, json_utf8, rol_sesiune, roluri_vizibile,
)

logger = logging.getLogger(__name__)

_SQL_UNITATILE_UTILIZATORULUI = (
    "SELECT ua.DC AS DC, u.NumeUnitate AS NumeUnitate, ua.Rol AS Rol "
    "  FROM Unitati_Utilizatori ua JOIN Unitati u ON u.DC = ua.DC "
    " WHERE ua.UN = %s ORDER BY u.NumeUnitate"
)

# {roluri} = one %s per visible role. LEFT JOIN: at section A nothing is stored yet -- the signer generates the document on their own PC.
_SQL_DE_SEMNAT = (
    "SELECT r.IDREV, r.IDDF, d.CUAL, d.CodAngajament, d.ObiectDDF, r.NumarRev, r.DataRev, "
    "       r.Desc_Scurta, r.Semnatura, r.DeSemnat, r.Incarcat, r.Preluat, p.Sha256, "
    "       COALESCE((SELECT SUM(sa.ValCur) FROM FX_DDF_REV_SA sa WHERE sa.IDREV = r.IDREV), 0) AS Total "
    "  FROM FX_DDF_REV r "
    "  JOIN FX_DDF d ON d.IDDF = r.IDDF "
    "  LEFT JOIN FX_DDF_PDF p ON p.IDREV = r.IDREV "
    " WHERE r.DeSemnat IN ({roluri}) "
    " ORDER BY r.DataRev, d.CodAngajament, r.NumarRev"
)


def _revizii_din(dc: str, nume_unitate: str, rol: str) -> list:
    vizibile = roluri_vizibile(rol)
    if not vizibile:
        return []
    conn = None
    try:
        conn = get_kbot_connection(dc)
        cursor = conn.cursor()
        if not are_de_semnat(cursor, dc):
            return []
        cursor.execute(_SQL_DE_SEMNAT.format(roluri=", ".join(["%s"] * len(vizibile))), vizibile)
        rezultat = []
        for (idrev, iddf, cual, cod, obiect, numar_rev, data_rev, desc_scurta, semnatura, de_semnat,
             incarcat, preluat, sha, total) in cursor.fetchall():
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
                "de_semnat": de_semnat or "",
                "incarcat": bool(incarcat),
                "preluat": bool(preluat),
            })
        return rezultat
    finally:
        if conn is not None and conn.is_connected():
            conn.close()


@forexe_bp.route("/api/forexe/ddf/de-semnat", methods=["GET"])
@require_session
def get_ddf_de_semnat():
    """{"revizii": [...], "unitati_necitite": [...]}: the revisions addressed to the user, in every unit."""
    s = g.session
    conn = None
    try:
        conn = get_kbot_comun_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(_SQL_UNITATILE_UTILIZATORULUI, (s.username,))
        unitati = cursor.fetchall()
    except mysql.connector.Error as err:
        logger.error("[forexe.ddf_flux] Unitati_Utilizatori %s: %s", s.username, err)
        return json_utf8({"error": "Unitățile utilizatorului nu au putut fi citite."}, 500)
    finally:
        if conn is not None and conn.is_connected():
            conn.close()

    revizii, necitite = [], []
    for u in unitati:
        dc, nume, rol = str(u.get("DC") or ""), str(u.get("NumeUnitate") or ""), str(u.get("Rol") or "")
        try:
            revizii.extend(_revizii_din(dc, nume, rol))
        except mysql.connector.Error as err:
            logger.error("[forexe.ddf_flux] %s: %s", dc, err)
            necitite.append(nume or dc)
    return json_utf8({"revizii": revizii, "unitati_necitite": necitite}, 200)


# ---------------------------------------------------------------------------------------
# The compartment -> role table
# ---------------------------------------------------------------------------------------
@forexe_bp.route("/api/forexe/ddf/flux/comp-rol", methods=["GET"])
@require_session
def get_ddf_flux_comp_rol():
    """The compartments in use on the unit's documents, the table, and the roles that can be given."""
    if rol_sesiune() in ROLURI_RESTRANSE:
        return json_utf8({"error": "Acțiune nepermisă pentru acest rol."}, 403)
    db_name = g.session.db_name
    conn = None
    try:
        conn = get_kbot_connection(db_name)
        cursor = conn.cursor()
        if not are_comp_rol(cursor, db_name):
            return json_utf8({"error": "Tabela compartiment → rol nu există pe această unitate "
                                       "(scriptul 0124 nu a ajuns pe baza unității)."}, 409)
        cursor.execute("SELECT DISTINCT Comp FROM FX_DDF WHERE COALESCE(Comp, '') <> '' ORDER BY Comp")
        compartimente = [str(r[0]).strip() for r in cursor.fetchall()]
        cursor.execute("SELECT Comp, Rol FROM FX_DDF_COMP_ROL ORDER BY Comp")
        mapare = [{"comp": str(r[0]), "rol": str(r[1])} for r in cursor.fetchall()]
        return json_utf8({"compartimente": compartimente, "mapare": mapare, "roluri": list(ROLURI_FLUX)}, 200)
    except Exception as e:
        logger.error("[forexe.ddf_flux] comp-rol GET: %s", e, exc_info=True)
        return json_utf8({"error": f"Eroare la citirea tabelei compartiment → rol: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()


@forexe_bp.route("/api/forexe/ddf/flux/comp-rol", methods=["PUT"])
@require_session
def put_ddf_flux_comp_rol():
    """Replaces the whole table with {"mapare": [{"comp": "...", "rol": "..."}]}. All or nothing."""
    if rol_sesiune() in ROLURI_RESTRANSE:
        return json_utf8({"error": "Acțiune nepermisă pentru acest rol."}, 403)
    corp = request.get_json(silent=True) or {}
    primite = corp.get("mapare")
    if not isinstance(primite, list):
        return json_utf8({"error": "Lipsește lista «mapare»."}, 400)
    mapare, vazute = [], set()
    for rand in primite:
        comp = str((rand or {}).get("comp") or "").strip()
        rol = str((rand or {}).get("rol") or "").strip()
        if not comp or len(comp) > 255:
            return json_utf8({"error": "Compartiment gol sau prea lung."}, 400)
        if rol not in ROLURI_FLUX:
            return json_utf8({"error": f"Rol necunoscut pentru «{comp}»: «{rol}»."}, 400)
        if comp.lower() in vazute:
            return json_utf8({"error": f"Compartimentul «{comp}» apare de două ori."}, 400)
        vazute.add(comp.lower())
        mapare.append((comp, rol))
    db_name = g.session.db_name
    conn = None
    try:
        conn = get_kbot_connection(db_name)
        cursor = conn.cursor()
        if not are_comp_rol(cursor, db_name):
            return json_utf8({"error": "Tabela compartiment → rol nu există pe această unitate."}, 409)
        cursor.execute("DELETE FROM FX_DDF_COMP_ROL")
        for comp, rol in mapare:
            cursor.execute("INSERT INTO FX_DDF_COMP_ROL (Comp, Rol) VALUES (%s, %s)", (comp, rol))
        conn.commit()
        logger.info("[forexe.ddf_flux] %s: tabela compartiment -> rol salvata (%d randuri)", db_name, len(mapare))
        return json_utf8({"randuri": len(mapare)}, 200)
    except Exception as e:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                logger.warning("[forexe.ddf_flux] rollback esuat dupa eroarea de mai jos", exc_info=True)
        logger.error("[forexe.ddf_flux] comp-rol PUT: %s", e, exc_info=True)
        return json_utf8({"error": f"Eroare la salvarea tabelei: {e}"}, 500)
    finally:
        if conn is not None:
            conn.close()
