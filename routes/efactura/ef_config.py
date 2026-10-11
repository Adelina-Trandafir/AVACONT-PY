# routes/efactura/ef_config.py
"""
The client data of the application registered at ANAF, read from the SERVER ENVIRONMENT (slice 00EF-04).

They are the five lines of the old `token.txt` plus the key that encrypts the tokens. On the VPS they
sit in `/etc/avacont/efactura.env` (root, mode 600), handed to the service by the systemd drop-in
`tools/efactura/efactura.conf` (`EnvironmentFile=`). The template is `tools/efactura/efactura.env.example`.

    EF_CLIENT_ID      client id                                  (not secret: it is in the address ANAF sees)
    EF_CLIENT_SECRET  client secret                              SECRET, never leaves this server
    EF_REDIRECT_URI   redirect address, EXACTLY as registered    (not secret)
    EF_AUTH_URL       ANAF authorise address                     (not secret)
    EF_TOKEN_URL      ANAF token address                         (not secret)
    EF_TOKEN_KEY      Fernet key (32 random bytes, url-safe base64) SECRET, encrypts the tokens in the database
    EF_REFRESH_DAYS   assumed life of a refresh token in days     optional, 365

Read LAZILY, on every call: the service picks the file up at start, and a missing variable must answer
a clear error to the operator, not break the import of the whole server.

NOTHING here prints a value. `EfNotConfigured` carries variable NAMES only; `EfConfig.__repr__` hides
every field, so a stray `logger.info(cfg)` cannot leak the secret.
"""
import os
from dataclasses import dataclass

REQUIRED = (
    "EF_CLIENT_ID",
    "EF_CLIENT_SECRET",
    "EF_REDIRECT_URI",
    "EF_AUTH_URL",
    "EF_TOKEN_URL",
    "EF_TOKEN_KEY",
)
_URLS = ("EF_REDIRECT_URI", "EF_AUTH_URL", "EF_TOKEN_URL")

DEFAULT_REFRESH_DAYS = 365
MIN_REFRESH_DAYS = 8          # below this the 7-day warning window would swallow the whole life
MAX_REFRESH_DAYS = 3650


class EfNotConfigured(RuntimeError):
    """One or more variables are missing or unusable. `names` lists the VARIABLE NAMES, never values."""

    def __init__(self, names):
        self.names = tuple(names)
        super().__init__("E-Factura neconfigurat pe server: " + ", ".join(self.names))


@dataclass(frozen=True)
class EfConfig:
    client_id: str
    client_secret: str
    redirect_uri: str
    auth_url: str
    token_url: str
    token_key: str
    refresh_days: int

    def __repr__(self):                                   # never prints a value
        return "EfConfig(<hidden>)"

    __str__ = __repr__


def _value(name):
    return (os.environ.get(name) or "").strip()


def missing_names():
    """Names of the required variables that are empty or absent (and the https rule for the addresses)."""
    bad = []
    for name in REQUIRED:
        value = _value(name)
        if not value:
            bad.append(name)
        elif name in _URLS and not value.lower().startswith("https://"):
            bad.append(name)
    return bad


def is_configured():
    return not missing_names()


def _refresh_days():
    raw = _value("EF_REFRESH_DAYS")
    if not raw:
        return DEFAULT_REFRESH_DAYS
    try:
        days = int(raw)
    except ValueError as err:
        raise EfNotConfigured(["EF_REFRESH_DAYS"]) from err
    if not MIN_REFRESH_DAYS <= days <= MAX_REFRESH_DAYS:
        raise EfNotConfigured(["EF_REFRESH_DAYS"])
    return days


def load():
    """The configuration, or EfNotConfigured naming what is missing."""
    bad = missing_names()
    if bad:
        raise EfNotConfigured(bad)
    return EfConfig(
        client_id=_value("EF_CLIENT_ID"),
        client_secret=_value("EF_CLIENT_SECRET"),
        redirect_uri=_value("EF_REDIRECT_URI"),
        auth_url=_value("EF_AUTH_URL"),
        token_url=_value("EF_TOKEN_URL"),
        token_key=_value("EF_TOKEN_KEY"),
        refresh_days=_refresh_days(),
    )
