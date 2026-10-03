# routes/access.py
"""
Slice 0104 -- what kind of client is this e-mail: with or without the Access application?

Route (PUBLIC, pre-login -- the client asks BEFORE it checks for updates, and the update
check itself runs before login):
    POST /api/access/client-type
    body  { "email": "<operator e-mail>" }
    200   { "access": 1 }   an Access client: the package WITH the Migrare folder + KBot.Access.dll
          { "access": 0 }   not an Access client (or an unknown e-mail): the package WITHOUT
    400   the e-mail is empty or does not look like one
    429   too many requests from this IP (a client asks once at start and once per changed e-mail)

The answer comes from the `Setari` table of EVERY database the e-mail may open
(AVACONT_COMUN.Unitati_Utilizatori -> DC = the database name): the row `Cheie = 'Access'`,
`Valoare = '1'`. One unit with the row on makes the person an Access client. A unit with no
`Setari` table, no row, or another value counts as 0 -- "no row = off", like the other server
settings (routes/setari.py). An e-mail that opens no unit answers 0, never an error, so the
route does not tell a stranger which addresses exist; all it can tell is "this one is an
Access client".

Injection: the e-mail is checked against a strict pattern and travels ONLY as a bound
parameter. The database names come from our own table, but they are put in the SQL as
identifiers (they cannot be parameters), so each one is checked against a plain-name
pattern and quoted first -- a name that fails is skipped and logged, never executed.
"""
import logging
import re
import threading
import time
from collections import defaultdict, deque

import mysql.connector
from flask import Blueprint, jsonify, request

from utils.database import get_kbot_comun_connection

logger = logging.getLogger(__name__)

access_bp = Blueprint("access", __name__)

SETARI_KEY = "Access"

# One e-mail: no spaces, one "@", a dot in the domain, at most 254 characters (RFC 5321).
_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]+$")
_EMAIL_MAX = 254

# A database name that is safe to put between backticks.
_DB_NAME_RE = re.compile(r"^[A-Za-z0-9_]{1,64}$")

# 1 = unit says Access; anything else = not.
_ACCESS_ON = "1"

# MySQL / MariaDB: table does not exist, unknown database.
_ER_NO_SUCH_TABLE = 1146
_ER_BAD_DB = 1049

# Every call counts, not only the failures (there is no password here to guess): 30 per
# minute per IP is far above what one K-BOT does (one at start, one per changed e-mail) and
# far below a scan. In-process like routes/auth/ratelimit.py -- single worker.
_WINDOW = 60
_MAX_PER_WINDOW = 30
_hits = defaultdict(deque)
_hits_lock = threading.Lock()


def _rate_limited(ip):
    """Records this request; True when the IP is over the limit."""
    now = time.time()
    with _hits_lock:
        q = _hits[ip]
        while q and q[0] < now - _WINDOW:
            q.popleft()
        if len(q) >= _MAX_PER_WINDOW:
            return True
        q.append(now)
        # An IP that stopped calling must not stay in the dict forever.
        if len(_hits) > 4096:
            for key in [k for k, v in _hits.items() if not v or v[-1] < now - _WINDOW]:
                del _hits[key]
        return False


def _error(message, reason, status):
    return jsonify({"error": message, "reason": reason}), status


def normalize_email(raw):
    """The e-mail as the login tables hold it (stripped, lowercase), or None when it is not an e-mail."""
    if not isinstance(raw, str):
        return None
    email = raw.strip().lower()
    if not email or len(email) > _EMAIL_MAX or not _EMAIL_RE.match(email):
        return None
    return email


def access_flag(conn, email):
    """
    1 when any database the e-mail may open has Setari.Access = '1', else 0.
    `conn` is a connection to AVACONT_COMUN.
    """
    cursor = conn.cursor()
    cursor.execute(
        "SELECT DISTINCT DC FROM Unitati_Utilizatori WHERE UN = %s",
        (email,),
    )
    databases = [row[0] for row in cursor.fetchall()]

    for db in databases:
        if not isinstance(db, str) or not _DB_NAME_RE.match(db):
            logger.warning("[access] un nume de baza neobisnuit este sarit: %r", db)
            continue
        try:
            cursor.execute(
                f"SELECT Valoare FROM `{db}`.`Setari` WHERE Cheie = %s",
                (SETARI_KEY,),
            )
            row = cursor.fetchone()
        except mysql.connector.Error as err:
            if getattr(err, "errno", None) in (_ER_NO_SUCH_TABLE, _ER_BAD_DB):
                continue                      # no Setari table / no such database = off
            raise
        if row is not None and row[0] is not None and str(row[0]).strip() == _ACCESS_ON:
            return 1
    return 0


@access_bp.route("/api/access/client-type", methods=["POST"])
def client_type():
    ip = request.remote_addr
    if _rate_limited(ip):
        return _error("Prea multe cereri. Reîncercați peste un minut.", "RATE_LIMITED", 429)

    data = request.get_json(silent=True) or {}
    email = normalize_email(data.get("email"))
    if email is None:
        return _error("Adresa de e-mail lipsește sau nu este validă.", "EMAIL_INVALID", 400)

    conn = None
    try:
        conn = get_kbot_comun_connection()
        return jsonify({"access": access_flag(conn, email)}), 200
    except Exception as e:
        # No swallowing: the client reads this as "unknown" and does not check for updates.
        logger.error("[access] client-type: %s", e, exc_info=True)
        return _error("Tipul clientului nu a putut fi citit.", "CLIENT_TYPE_FAILED", 500)
    finally:
        if conn is not None:
            conn.close()
