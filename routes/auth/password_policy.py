# routes/auth/password_policy.py
"""
The REAL password policy of the K-BOT MariaDB server (security plan S1).

Read from the server, never copied into the client. Supports the simple_password_check plugin:
minimal_length, digits, letters_same_case (that many upper AND that many lower case letters) and
other_characters. A password is judged here BEFORE SET PASSWORD so the operator sees every
missing requirement at once; MariaDB validates again on the change itself.

Without an active validation plugin strict_password_validation enforces nothing: the policy then
reports plugin_active=False and only the application floor (FALLBACK_MIN_LENGTH) applies.
"""
import logging
import secrets
import string

from utils.database import get_kbot_comun_connection

logger = logging.getLogger(__name__)

FALLBACK_MIN_LENGTH = 8
_VARS = ("simple_password_check_minimal_length", "simple_password_check_digits",
         "simple_password_check_letters_same_case", "simple_password_check_other_characters")


def read_policy():
    """-> dict {plugin_active, strict, minimal_length, digits, letters_same_case, other_characters}."""
    conn = None
    try:
        conn = get_kbot_comun_connection()
        cur = conn.cursor()
        cur.execute("SELECT PLUGIN_NAME FROM information_schema.PLUGINS "
                    "WHERE PLUGIN_NAME = 'simple_password_check' AND PLUGIN_STATUS = 'ACTIVE'")
        active = cur.fetchone() is not None
        cur.execute("SHOW GLOBAL VARIABLES WHERE Variable_name IN "
                    "('strict_password_validation', %s, %s, %s, %s)", _VARS)
        values = {str(k).lower(): v for k, v in cur.fetchall()}
    finally:
        if conn is not None and conn.is_connected():
            conn.close()

    def _int(name, default):
        try:
            return int(values.get(name, default))
        except (TypeError, ValueError):
            return default

    strict = str(values.get("strict_password_validation", "OFF")).upper() == "ON"
    if not active:
        logger.error("password policy: no active simple_password_check plugin; only the "
                     "application floor (%d chars) applies", FALLBACK_MIN_LENGTH)
        return {"plugin_active": False, "strict": strict, "minimal_length": FALLBACK_MIN_LENGTH,
                "digits": 0, "letters_same_case": 0, "other_characters": 0}
    return {"plugin_active": True, "strict": strict,
            "minimal_length": _int("simple_password_check_minimal_length", 8),
            "digits": _int("simple_password_check_digits", 1),
            "letters_same_case": _int("simple_password_check_letters_same_case", 1),
            "other_characters": _int("simple_password_check_other_characters", 1)}


def random_compliant(policy=None, length=32):
    """A random password that passes `evaluate()` for `policy` (read from the server when None).
    For accounts whose real password the user chooses later: a plain random token fails the
    plugin whenever it happens to lack a special character or a digit."""
    if policy is None:
        try:
            policy = read_policy()
        except Exception as err:    # the plugin check on the server still decides; use the usual floor
            logger.warning("password policy unreadable, using the usual floor: %s", err)
            policy = {"minimal_length": 8, "digits": 1, "letters_same_case": 1, "other_characters": 1}
    rng = secrets.SystemRandom()
    specials = "!#$%&*+-=?@^_~"
    chars = ([rng.choice(string.digits) for _ in range(max(policy["digits"], 1))]
             + [rng.choice(string.ascii_uppercase) for _ in range(max(policy["letters_same_case"], 1))]
             + [rng.choice(string.ascii_lowercase) for _ in range(max(policy["letters_same_case"], 1))]
             + [rng.choice(specials) for _ in range(max(policy["other_characters"], 1))])
    pool = string.ascii_letters + string.digits + specials
    chars += [rng.choice(pool) for _ in range(max(length, policy["minimal_length"]) - len(chars))]
    rng.shuffle(chars)
    return "".join(chars)


def evaluate(password, policy):
    """-> list of violations {code, have, need, message}; empty when the password complies.
    The message is Romanian (operator-visible); the password itself is never echoed."""
    pw = password or ""
    out = []

    def add(code, have, need, message):
        out.append({"code": code, "have": have, "need": need, "message": message})

    def missing(n, one, many):
        return one if n == 1 else many.format(n)

    if len(pw) < policy["minimal_length"]:
        add("LENGTH", len(pw), policy["minimal_length"],
            f"Lungime: {len(pw)} din minimum {policy['minimal_length']}")
    digits = sum(c.isdigit() for c in pw)
    if digits < policy["digits"]:
        n = policy["digits"] - digits
        add("DIGITS", digits, policy["digits"], missing(n, "Lipsește o cifră", "Lipsesc {} cifre"))
    upper = sum(c.isalpha() and c.isupper() for c in pw)
    lower = sum(c.isalpha() and c.islower() for c in pw)
    need_case = policy["letters_same_case"]
    if upper < need_case:
        add("UPPER", upper, need_case,
            missing(need_case - upper, "Lipsește o majusculă", "Lipsesc {} majuscule"))
    if lower < need_case:
        add("LOWER", lower, need_case,
            missing(need_case - lower, "Lipsește o minusculă", "Lipsesc {} minuscule"))
    other = sum(not c.isalnum() for c in pw)
    if other < policy["other_characters"]:
        add("OTHER", other, policy["other_characters"],
            missing(policy["other_characters"] - other, "Lipsește un caracter special",
                    "Lipsesc {} caractere speciale"))
    return out
