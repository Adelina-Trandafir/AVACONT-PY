# routes/inregistrare/parola.py
"""
The one-time link a new user gets after approval (slice 0075-03, plan 5.6 step 8, D13).

The provisioning job created the MariaDB account with a random password nobody knows
and mailed a link carrying a token. Only the token's SHA-256 is stored, on the request
itself (`FX_Inregistrari.ParolaHash` / `ParolaExpira`), so the link survives a restart
of the server and the approval page can see whether it was used.

Using the link: the page sends the token and the chosen password; the account's
password is set with `ALTER USER` through the provisioning account (the only one
allowed to change another account's password), and the hash is cleared in the same
breath -- the link works once.

WHY NOT `SET PASSWORD` AS THE USER, the way the password change of slice 0072 does:
that needs the current password, and here nobody has one.
"""
import logging
import re

from routes.inregistrare import cerere as cerere_mod
from routes.inregistrare.provizionare import hash_link_token
from utils.database import get_kbot_comun_connection, get_kbot_provisioning_connection

logger = logging.getLogger(__name__)

# Same floor as the password change of slice 0072 (auth._PWD_MIN_LENGTH).
MIN_LENGTH = 8
# A ceiling so the form cannot be used to post megabytes at the server.
MAX_LENGTH = 128

# `secrets.token_urlsafe(32)` is 43 characters of this alphabet; anything else is not
# one of ours and is refused before it reaches the database.
_TOKEN_SHAPE = re.compile(r"^[A-Za-z0-9_-]{20,100}$")


def is_token_shaped(token) -> bool:
    return bool(token) and _TOKEN_SHAPE.match(token) is not None


def find(token):
    """
    `{id_cerere, email, denumire, ramas}` for a live link, or None -- unknown, used,
    expired, or a request that is no longer approved all read the same.
    """
    if not is_token_shaped(token):
        return None
    conn = get_kbot_comun_connection()
    try:
        cur = conn.cursor(dictionary=True, buffered=True)
        cur.execute(
            "SELECT IdCerere, Email, Denumire, "
            "TIMESTAMPDIFF(SECOND, NOW(), ParolaExpira) AS Ramas "
            "FROM FX_Inregistrari "
            "WHERE ParolaHash = %s AND Stare = %s AND ParolaExpira > NOW() LIMIT 1",
            (hash_link_token(token), cerere_mod.STARE_APROBATA),
        )
        row = cur.fetchone()
    finally:
        if conn.is_connected():
            conn.close()
    if row is None:
        return None
    return {
        "id_cerere": int(row["IdCerere"]),
        "email": row["Email"],
        "denumire": row["Denumire"],
        "ramas": int(row["Ramas"] or 0),
    }


def password_problem(parola):
    """The Romanian sentence for a password that will not do, or None."""
    if not isinstance(parola, str) or len(parola) < MIN_LENGTH:
        return f"Parola trebuie să aibă cel puțin {MIN_LENGTH} caractere."
    if len(parola) > MAX_LENGTH:
        return f"Parola nu poate depăși {MAX_LENGTH} de caractere."
    if parola.strip() != parola:
        return "Parola nu poate începe sau se termina cu spațiu."
    return None


def set_password(link, token, parola):
    """
    Sets the account's password and spends the link. Raises mysql.connector.Error on
    any refusal; the route reports it.

    The password first, then the hash: if clearing the hash failed after the password
    was set, the worst case is a link that can be used once more by the same person.
    The other order could spend the link and leave the account without a password.
    """
    conn = get_kbot_provisioning_connection()
    try:
        cur = conn.cursor(buffered=True)
        cur.execute("ALTER USER %s@'%' IDENTIFIED BY %s", (link["email"], parola))
        cur.execute(
            "UPDATE FX_Inregistrari SET ParolaHash = NULL, ParolaExpira = NULL "
            "WHERE IdCerere = %s AND ParolaHash = %s",
            (link["id_cerere"], hash_link_token(token)),
        )
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception as err:
            logger.warning("rollback after a failed password set failed: %s", err)
        raise
    finally:
        if conn.is_connected():
            conn.close()
