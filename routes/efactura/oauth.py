# routes/efactura/oauth.py
"""
The calls this server makes to ANAF's OAuth addresses (slice 00EF-04). Stdlib `urllib` only: the server's
virtual environment has no `requests` (same choice as routes/inregistrare/anaf.py).

PORTED FROM `Surse/EF_SURSA/Program.vb` (the old EF.EXE, read 06.10.2026):

  authorise address   ATH + ?response_type=code & client_id & redirect_uri & token_content_type=jwt
                      -- NOT called here: the certificate (hardware token) is on the operator's PC,
                      so the PC does the TLS handshake and reads `code` from the final address.
  code exchange       POST <TKN>  code, token_content_type=jwt, client_id, client_secret, redirect_uri,
                      grant_type=authorization_code
  refresh             POST <TKN>  refresh_token, grant_type=refresh_token, client_id, client_secret
                      (no certificate needed)
  answer              JSON  access_token, refresh_token, expires_in (seconds)

DIFFERENCE, deliberate and UNVERIFIED against the live service: EF.EXE sent the code exchange body as
`text/plain` with the values un-encoded; here BOTH calls are `application/x-www-form-urlencoded` with
the values encoded (the refresh already worked that way in EF.EXE). A server that reads the body as a
form decodes both the same. If ANAF refuses the exchange, this is the first thing to look at.

NOTHING SECRET IS LOGGED OR RAISED: not the code, not a token, not the client secret, not the body of
ANAF's answer. A failure logs the HTTP status and ANAF's short `error` code (filtered to a safe
alphabet); the exception text is Romanian and generic.
"""
import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 20
_MAX_BODY = 64 * 1024
_SAFE_ERROR = re.compile(r"[^A-Za-z0-9_ .:-]")


class OauthError(RuntimeError):
    """ANAF refused, or could not be reached.

    `definitive` is True when ANAF answered and said the code / refresh token is no good (HTTP 4xx other
    than 408 / 429): retrying cannot help and the unit has to do the certificate step again. False for
    network failures, timeouts and 5xx: the token may well be fine, try again later.
    """

    def __init__(self, message, definitive=False, status=None):
        super().__init__(message)
        self.definitive = definitive
        self.status = status


@dataclass(frozen=True)
class TokenSet:
    access_token: str
    refresh_token: str          # "" when ANAF sent none (allowed on refresh: the old one stays)
    expires_in: int             # seconds the ACCESS token lives

    def __repr__(self):                                   # never prints a token
        return "TokenSet(<hidden>)"

    __str__ = __repr__


def authorize_url(cfg):
    """The address the PC calls with the certificate. No secret in it (client id and redirect are public)."""
    query = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": cfg.client_id,
        "redirect_uri": cfg.redirect_uri,
        "token_content_type": "jwt",
    })
    separator = "&" if "?" in cfg.auth_url else "?"
    return cfg.auth_url + separator + query


def exchange_code(cfg, code):
    """The one-time `code` -> tokens."""
    return _token_call(cfg, {
        "code": code,
        "token_content_type": "jwt",
        "client_id": cfg.client_id,
        "client_secret": cfg.client_secret,
        "redirect_uri": cfg.redirect_uri,
        "grant_type": "authorization_code",
    }, what="schimbul codului", need_refresh=True)


def refresh(cfg, refresh_token):
    """A refresh token -> new tokens (no certificate)."""
    return _token_call(cfg, {
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
        "client_id": cfg.client_id,
        "client_secret": cfg.client_secret,
    }, what="reînnoirea tokenului", need_refresh=False)


def _token_call(cfg, fields, what, need_refresh):
    body = urllib.parse.urlencode(fields).encode("ascii")
    request = urllib.request.Request(
        cfg.token_url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "User-Agent": "K-BOT",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            raw = response.read(_MAX_BODY)
    except urllib.error.HTTPError as err:
        code = _error_code(err)
        definitive = 400 <= err.code < 500 and err.code not in (408, 429)
        logger.error("ANAF token (%s): HTTP %s, error=%s", what, err.code, code or "-")
        detail = f" ({code})" if code else ""
        raise OauthError(f"ANAF a refuzat {what}: HTTP {err.code}{detail}.",
                         definitive=definitive, status=err.code) from None
    except Exception as err:                    # URLError, socket timeout, DNS, TLS
        logger.error("ANAF token (%s): unreachable: %s", what, type(err).__name__)
        raise OauthError(f"ANAF nu a răspuns la {what}.", definitive=False) from None

    return _parse(raw, what, need_refresh)


def _parse(raw, what, need_refresh):
    try:
        payload = json.loads(raw.decode("utf-8", errors="replace"))
    except ValueError:
        logger.error("ANAF token (%s): the answer is not JSON", what)
        raise OauthError(f"Răspuns ANAF ilizibil la {what}.") from None
    if not isinstance(payload, dict):
        logger.error("ANAF token (%s): the answer is not an object", what)
        raise OauthError(f"Răspuns ANAF neașteptat la {what}.")

    access = payload.get("access_token")
    refresh_token = payload.get("refresh_token")
    expires_in = payload.get("expires_in")
    if not isinstance(access, str) or not access.strip():
        logger.error("ANAF token (%s): no access_token in the answer", what)
        raise OauthError(f"ANAF nu a trimis tokenul de acces la {what}.")
    if not isinstance(refresh_token, str) or not refresh_token.strip():
        if need_refresh:
            logger.error("ANAF token (%s): no refresh_token in the answer", what)
            raise OauthError(f"ANAF nu a trimis tokenul de reînnoire la {what}.")
        refresh_token = ""
    try:
        seconds = int(expires_in)
    except (TypeError, ValueError):
        seconds = 0
    if seconds <= 0:
        logger.error("ANAF token (%s): no usable expires_in in the answer", what)
        raise OauthError(f"ANAF nu a trimis durata tokenului la {what}.")
    return TokenSet(access_token=access.strip(), refresh_token=refresh_token.strip(), expires_in=seconds)


def _error_code(http_error):
    """ANAF's short `error` field of a refusal, filtered to a harmless alphabet; "" when there is none."""
    try:
        payload = json.loads(http_error.read(_MAX_BODY).decode("utf-8", errors="replace"))
    except Exception as err:
        logger.warning("ANAF token: the refusal body could not be read (%s)", type(err).__name__)
        return ""
    value = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(value, str):
        return ""
    return _SAFE_ERROR.sub("", value)[:60]
