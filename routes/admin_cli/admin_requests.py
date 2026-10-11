"""
admin_requests.py -- the registration requests (FX_Inregistrari) for KBotAdmin (slice ME01).

This is what the web page /operator used to do (routes/inregistrare/operator.py, removed in this slice):
list the requests, show one with the plan of its approval, approve it, reject it, send a new password link.
The work itself is still routes.inregistrare.provizionare -- nothing about HOW a unit is created changed; only
the door did: it is now this module, reached over SSH by the owner's desktop program, instead of a page on
the public internet.

Every function here returns plain data (dicts of ASCII keys) for admin_cli to print, or raises
admin_cli.AdminError with the sentence the operator reads.
"""
import json
import sys

import mysql.connector

from routes.inregistrare import cerere as cerere_mod
from routes.inregistrare import nomenclatoare, provizionare
from utils.database import get_kbot_comun_connection, get_kbot_provisioning_connection

LIST_LIMIT = 500
MOTIV_MIN = 5
MOTIV_MAX = 2000

STATES = (cerere_mod.STARE_IN_ASTEPTARE, cerere_mod.STARE_ESUATA,
          cerere_mod.STARE_APROBATA, cerere_mod.STARE_RESPINSA)
_APPROVABLE = (cerere_mod.STARE_IN_ASTEPTARE, cerere_mod.STARE_ESUATA)


def _text(value):
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d %H:%M")
    return value


def _payload(text):
    """{"ss", "f", "e"} from the stored JSON; a broken payload shows as empty lists."""
    try:
        data = json.loads(text or "{}")
    except ValueError:
        data = {}
    return {key: [str(v) for v in data.get(key) or []] for key in ("ss", "f", "e")}


def _captions():
    conn = get_kbot_comun_connection()
    try:
        return {
            "ss": {r["cod"]: r["denumire"] for r in nomenclatoare.read_sursasector(conn)},
            "f": {r["cod"]: r["denumire"] for r in nomenclatoare.read_clasificatii(conn, "F")},
            "e": {r["cod"]: r["denumire"] for r in nomenclatoare.read_clasificatii(conn, "E")},
        }
    finally:
        if conn.is_connected():
            conn.close()


def list_requests(state, error):
    if state != "toate" and state not in STATES:
        raise error("Filtru de stare necunoscut.")
    conn = get_kbot_provisioning_connection()
    try:
        cur = conn.cursor(dictionary=True, buffered=True)
        sql = ("SELECT IdCerere, DataCerere, Stare, CF, Email, Denumire, An, DbName, "
               "DataDecizie, Decis, Payload FROM FX_Inregistrari")
        params = ()
        if state != "toate":
            sql += " WHERE Stare = %s"
            params = (state,)
        cur.execute(sql + " ORDER BY IdCerere DESC LIMIT %d" % LIST_LIMIT, params)
        rows = cur.fetchall()
        cur.execute("SELECT Stare, COUNT(*) AS n FROM FX_Inregistrari GROUP BY Stare")
        counts = {r["Stare"]: int(r["n"]) for r in cur.fetchall()}
        conn.rollback()
    finally:
        if conn.is_connected():
            conn.close()
    out = []
    for r in rows:
        payload = _payload(r["Payload"])
        out.append({
            "id": r["IdCerere"], "date": _text(r["DataCerere"]), "state": r["Stare"], "cf": r["CF"],
            "email": r["Email"], "name": r["Denumire"], "year": r["An"], "db_name": r["DbName"] or "",
            "decided_on": _text(r["DataDecizie"]) or "", "decided_by": r["Decis"] or "",
            "rows": len(payload["ss"]) * len(payload["f"]) * len(payload["e"]),
        })
    return {"requests": out, "counts": counts, "limit": LIST_LIMIT}


def _run_plan(id_request, codes, db_name):
    lines = []
    try:
        result = provizionare.plan(id_request, codes, progress=lines.append, db_name=db_name)
        return {"ok": True, "result": result, "lines": lines, "problem": ""}
    except provizionare.ProvizionareRefuzata as err:
        return {"ok": False, "result": None, "lines": lines, "problem": str(err)}
    except mysql.connector.Error as err:
        return {"ok": False, "result": None, "lines": lines, "problem": "Eroare MariaDB: %s" % err}


def detail(id_request, error):
    conn = get_kbot_provisioning_connection()
    try:
        cur = conn.cursor(dictionary=True, buffered=True)
        cur.execute(
            "SELECT IdCerere, Email, CF, DenumireAnaf, Denumire, An, Payload, Stare, DbName, "
            "Motiv, DataCerere, DataDecizie, Decis, IpAddress, "
            "ParolaHash IS NOT NULL AS LinkActiv, ParolaExpira, ParolaExpira > NOW() AS LinkValabil "
            "FROM FX_Inregistrari WHERE IdCerere = %s", (id_request,))
        row = cur.fetchone()
        conn.rollback()
    finally:
        if conn.is_connected():
            conn.close()
    if row is None:
        raise error("Cererea %s nu exista." % id_request)

    payload = _payload(row["Payload"])
    captions = _captions()
    approvable = row["Stare"] in _APPROVABLE
    return {
        "id": row["IdCerere"], "email": row["Email"], "cf": row["CF"], "anaf_name": row["DenumireAnaf"] or "",
        "name": row["Denumire"], "year": row["An"], "state": row["Stare"], "db_name": row["DbName"] or "",
        "reason": row["Motiv"] or "", "requested_on": _text(row["DataCerere"]),
        "decided_on": _text(row["DataDecizie"]) or "", "decided_by": row["Decis"] or "",
        "ip": row["IpAddress"] or "", "link_active": bool(row["LinkActiv"]), "link_valid": bool(row["LinkValabil"]),
        "ss": [{"cod": c, "name": captions["ss"].get(c, "")} for c in payload["ss"]],
        "f": [{"cod": c, "name": captions["f"].get(c, "")} for c in payload["f"]],
        "e": [{"cod": c, "name": captions["e"].get(c, "")} for c in payload["e"]],
        "rows": len(payload["ss"]) * len(payload["f"]) * len(payload["e"]),
        "approvable": approvable,
        "plan": _run_plan(id_request, None, None) if approvable else None,
    }


def parse_codes(text, error):
    """--codes: a JSON object {"SURSA-SECTOR": "CodProgram"}, or empty."""
    if not text:
        return {}
    try:
        raw = json.loads(text)
    except ValueError:
        raise error("CodProgram trebuie trimis ca JSON.")
    if not isinstance(raw, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in raw.items()):
        raise error("CodProgram trebuie trimis ca lista sursa-sector -> valoare.")
    return {k.strip().upper(): v.strip() for k, v in raw.items()}


def plan(id_request, codes, db_name):
    return _run_plan(id_request, codes, (db_name or "").strip().upper() or None)


def approve(id_request, decided_by, codes, db_name, error):
    """Runs the whole approval, printing every progress line as `LINE<TAB>text` on stdout as it happens."""
    db_name = (db_name or "").strip().upper()
    if not db_name:
        raise error("Lipseste numele bazei.")

    def say(line):
        sys.stdout.buffer.write(("LINE\t%s\n" % str(line).replace("\n", " ")).encode("utf-8"))
        sys.stdout.buffer.flush()

    try:
        return provizionare.approve(id_request, decided_by, codes, progress=say, db_name=db_name)
    except provizionare.ProvizionareRefuzata as err:
        raise error("Refuzat: %s" % err)
    except provizionare.ProvizionareEsuata as err:
        left = ("  Ramas de curatat: %s." % ", ".join(str(x) for x in err.ramas)) if err.ramas else ""
        raise error("Esuat: %s%s" % (err.message, left))


def reject(id_request, decided_by, motive, error):
    motive = " ".join(str(motive or "").split())
    if len(motive) < MOTIV_MIN:
        raise error("Scrieti motivul respingerii (il primeste solicitantul).")
    if len(motive) > MOTIV_MAX:
        raise error("Motivul poate avea cel mult %d caractere." % MOTIV_MAX)
    try:
        row = provizionare.reject(id_request, decided_by, motive)
    except provizionare.ProvizionareRefuzata as err:
        raise error(str(err))
    announced = True
    try:
        from routes.auth import mailer
        mailer.send_registration_rejected(row["email"], row["denumire"], motive)
    except Exception:  # the rejection is recorded either way; a mail that does not leave is reported
        announced = False
    return {"email": row["email"], "announced": announced}


def new_link(id_request, error):
    lines = []
    try:
        result = provizionare.link_nou(id_request, progress=lines.append)
    except provizionare.ProvizionareRefuzata as err:
        raise error(str(err))
    result["lines"] = lines
    return result
