# routes/efactura/cripto.py
"""
Encryption of the two ANAF tokens before they reach the database (slice 00EF-04).

Fernet (AES-128-CBC + HMAC-SHA256, from the `cryptography` package). The key is `EF_TOKEN_KEY`
(`ef_config`): 32 random bytes, url-safe base64 -- exactly the shape Fernet wants and the shape the
template `tools/efactura/efactura.env.example` tells the operator to generate. The result is ASCII
(base64), so it fits the `text utf8mb3` columns of `EF_Token`.

PREREQUISITE: `pip install cryptography` in the server's virtual environment. It is not in the repo's
local venv. Without it every call here raises CriptoError, with a message that says so.

A key that changed (or never matched) makes `decrypt` fail with CriptoError: the stored tokens are
unreadable and the unit has to do the certificate step again. Nothing here tries a fallback.
"""


class CriptoError(RuntimeError):
    """The token could not be encrypted or decrypted. The message never carries a key or a token."""


def _fernet(key):
    try:
        from cryptography.fernet import Fernet
    except ImportError as err:
        raise CriptoError(
            "Biblioteca «cryptography» nu este instalată pe server (pip install cryptography)."
        ) from err
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, TypeError, UnicodeEncodeError) as err:
        raise CriptoError("Cheia de criptare EF_TOKEN_KEY nu este o cheie Fernet validă.") from err


def encrypt(text, key):
    """`text` -> ASCII token."""
    if not text:
        raise CriptoError("Nu se poate cripta o valoare goală.")
    return _fernet(key).encrypt(text.encode("utf-8")).decode("ascii")


def decrypt(blob, key):
    """ASCII token -> `text`. CriptoError when the key does not match."""
    from cryptography.fernet import InvalidToken  # the import in _fernet already proved it exists
    fernet = _fernet(key)
    try:
        return fernet.decrypt(blob.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeError) as err:
        raise CriptoError(
            "Tokenul stocat nu poate fi citit cu cheia curentă (cheia s-a schimbat?). "
            "Unitatea trebuie să facă din nou pasul cu certificatul."
        ) from err
