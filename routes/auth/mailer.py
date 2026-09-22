# routes/auth/mailer.py
"""
Outgoing e-mail for the auth routes (slice 0072).

One job: put the one-time code of a password change in the operator's inbox.
The address IS the user name (K-BOT logs in by e-mail), so there is nothing to
look up -- the session already carries it.

Configuration comes from config.py (SMTP_*), which itself reads the environment,
so the real account lives on the VPS and never in the repository. An empty
SMTP_HOST means "not configured" and raises MailNotConfigured; the route turns
that into a 503 with a Romanian sentence, never into a pretend success.

Every failure is raised, never swallowed: a code the operator never received
must show as an error on their screen, not as a silent wait.
"""
import logging
import smtplib
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

logger = logging.getLogger(__name__)


class MailNotConfigured(RuntimeError):
    """SMTP_HOST is empty: the server cannot send mail."""


def _read_config():
    try:
        import config as _config
    except Exception:
        return None
    return _config


def _get(cfg, name, default):
    return getattr(cfg, name, default) if cfg is not None else default


def is_configured():
    cfg = _read_config()
    return bool(str(_get(cfg, "SMTP_HOST", "")).strip())


def send_password_code(to_address, code, minutes):
    """
    Sends the one-time code. Raises MailNotConfigured without SMTP_HOST, and lets
    smtplib / socket errors propagate -- the caller reports them.
    """
    cfg = _read_config()
    host = str(_get(cfg, "SMTP_HOST", "")).strip()
    if not host:
        raise MailNotConfigured("SMTP_HOST lipseste din config.")

    port = int(_get(cfg, "SMTP_PORT", 587))
    user = str(_get(cfg, "SMTP_USER", "") or "")
    password = str(_get(cfg, "SMTP_PASSWORD", "") or "")
    sender = str(_get(cfg, "SMTP_FROM", "K-BOT <no-reply@avatarsoft.ro>"))
    use_tls = bool(_get(cfg, "SMTP_USE_TLS", True))
    timeout = int(_get(cfg, "SMTP_TIMEOUT", 15))

    msg = EmailMessage()
    msg["Subject"] = "K-BOT: codul de confirmare pentru schimbarea parolei"
    msg["From"] = sender
    msg["To"] = to_address
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid()
    # Romanian, literal diacritics: this is what the operator reads.
    msg.set_content(
        "Bună ziua,\n\n"
        "Ați cerut schimbarea parolei contului K-BOT.\n\n"
        f"Codul de confirmare este:  {code}\n\n"
        f"Codul este valabil {minutes} minute și poate fi folosit o singură dată.\n"
        "Dacă nu ați cerut dumneavoastră schimbarea, ignorați acest mesaj: parola rămâne neschimbată.\n\n"
        "K-BOT\n"
    )

    logger.info("password code mail -> %s via %s:%s", _mask(to_address), host, port)
    with smtplib.SMTP(host, port, timeout=timeout) as smtp:
        if use_tls:
            smtp.starttls()
        if user:
            smtp.login(user, password)
        smtp.send_message(msg)


def _mask(address):
    """a***@domain -- for logs and for the answer sent back to the client."""
    if not address or "@" not in address:
        return "***"
    local, _, domain = address.partition("@")
    if len(local) <= 1:
        return f"{local}***@{domain}"
    return f"{local[0]}***@{domain}"


mask_address = _mask
