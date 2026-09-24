# routes/inregistrare/store.py
"""
The pre-authentication registration store (slice 0075-01, plan 5.1).

A registrant has no session, so there is no session token to hang anything on. What
there IS, already built and already tested, is `session_store.STORE.put_note /
get_note / delete_note`: they take ANY token string, keep it under a key of their own
(`<prefix>note:<name>:<token>`), and never touch the session keys the bearer guard
validates. Finding F5 of the plan confirmed that on the code. So the registration
"session" is one note, named `register`, on a token this module mints itself.

Nothing new is needed in either backend: memory and Redis behave the same, which is
why the value below is plain JSON types only (the Redis backend calls json.dumps on
it). No datetimes, no objects, no tuples.

WHY THE TOKEN IS BORN AT THE ANAF LOOKUP, not at the e-mail step. The note carries
`cf` and `anaf` -- the fiscal code and the answer ANAF gave for it. Both are step-1
data, and both must survive, server-side, until the request is submitted at step 6:

  * `anaf.denumire` becomes `FX_Inregistrari.DenumireAnaf`, which the approval page
    shows NEXT TO the name the applicant typed. The applicant is allowed to edit the
    typed name (operator decision, 22.09.2026). If the ANAF name travelled in the
    browser instead, the approval page would be comparing the applicant's typing
    against the applicant's typing.
  * `cf` is what decision D11 refuses a duplicate on. Held in the browser, an
    applicant could pass that check at step 1 with a free code and submit a different
    one at step 6.

The plan's 4 says "the registration token from step 2"; its own 5.1 lists `cf` and
`anaf` among the note's fields. They contradict each other, the operator settled it
on 22.09.2026, and this module is the settlement: the token comes from `/anaf`.

LIFETIME. Thirty minutes, absolute, from the ANAF lookup -- the wizard is six short
screens. Every write re-puts the note with the time that is LEFT, never a fresh
thirty minutes, so the window cannot be walked forward by asking for code after code.
"""
import secrets
import time

from routes.auth.session_store import STORE

# Name of the note. One per token; a registrant never has two.
NOTE_NAME = "register"

# Absolute life of a registration, in seconds, counted from the ANAF lookup.
TTL_SECONDS = 30 * 60

# 256 bits from the CSPRNG, same width as a session token (session_store).
_TOKEN_NBYTES = 32


def new_token() -> str:
    """An opaque registration token. Never derived from the CF or the e-mail."""
    return secrets.token_urlsafe(_TOKEN_NBYTES)


def create(cf, anaf) -> tuple:
    """
    Opens a registration around a successful ANAF lookup.

    `cf` is the normalized fiscal code (digits only) and `anaf` the field dictionary
    the lookup returned. Answers `(token, expires_in)`.
    """
    token = new_token()
    data = {
        "cf": cf,
        "anaf": anaf,
        "email": None,
        "code_hash": None,
        "code_attempts": 0,
        "code_expires_at": 0.0,
        "verified": False,
        "expires_at": time.time() + TTL_SECONDS,
    }
    STORE.put_note(token, NOTE_NAME, data, TTL_SECONDS)
    return token, TTL_SECONDS


def read(token):
    """
    The registration behind a token, or None when there is none left.

    The store enforces its own TTL; `expires_at` is checked here as well because the
    two backends express expiry differently (a dict of deadlines against Redis key
    TTLs) and a registration that has run out must read as absent on both.
    """
    if not token:
        return None
    data = STORE.get_note(token, NOTE_NAME)
    if not isinstance(data, dict):
        return None
    if float(data.get("expires_at", 0)) <= time.time():
        STORE.delete_note(token, NOTE_NAME)
        return None
    return data


def save(token, data) -> int:
    """
    Writes the registration back under what is LEFT of its thirty minutes, and
    answers that remainder in whole seconds.

    Never extends the window: `expires_at` was fixed at `create` and is the only
    thing consulted here.
    """
    remaining = int(float(data.get("expires_at", 0)) - time.time())
    if remaining <= 0:
        STORE.delete_note(token, NOTE_NAME)
        return 0
    STORE.put_note(token, NOTE_NAME, data, remaining)
    return remaining


def discard(token):
    """
    Throws the registration away.

    Used when the applicant goes back to screen one and looks up a different fiscal
    code: the old registration is finished with, and leaving it to time out would
    keep a proven e-mail attached to a code nobody is pursuing any more.
    """
    if token:
        STORE.delete_note(token, NOTE_NAME)
