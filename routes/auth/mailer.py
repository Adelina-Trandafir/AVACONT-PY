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
from email.utils import formatdate, make_msgid, parseaddr

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


def _smtp_config():
    """
    The SMTP block from config.py. Raises MailNotConfigured when SMTP_HOST is empty,
    so a missing account can never read as a sent message.

    Two ways to encrypt, and they are not interchangeable. Port 465 (SMTPS) is
    IMPLICIT TLS: the socket is encrypted from the first byte and STARTTLS is never
    sent -- talking plain SMTP to it hangs until the timeout. Ports 587 and 25 start
    in the clear and upgrade with STARTTLS. The port decides by itself; SMTP_USE_SSL
    forces the implicit form on a provider that runs SMTPS elsewhere.
    """
    cfg = _read_config()
    host = str(_get(cfg, "SMTP_HOST", "")).strip()
    if not host:
        raise MailNotConfigured("SMTP_HOST lipseste din config.")

    port = int(_get(cfg, "SMTP_PORT", 587))
    return {
        "host": host,
        "port": port,
        "user": str(_get(cfg, "SMTP_USER", "") or ""),
        "password": str(_get(cfg, "SMTP_PASSWORD", "") or ""),
        "sender": str(_get(cfg, "SMTP_FROM", "K-BOT <no-reply@avatarsoft.ro>")),
        "use_tls": bool(_get(cfg, "SMTP_USE_TLS", True)),
        "use_ssl": bool(_get(cfg, "SMTP_USE_SSL", port == 465)),
        "timeout": int(_get(cfg, "SMTP_TIMEOUT", 15)),
    }


def _deliver(msg, conf):
    """Opens the session the way `conf` says and sends one message. Errors propagate."""
    opener = smtplib.SMTP_SSL if conf["use_ssl"] else smtplib.SMTP
    with opener(conf["host"], conf["port"], timeout=conf["timeout"]) as smtp:
        # STARTTLS on an already-encrypted session is an error, not a second layer.
        if conf["use_tls"] and not conf["use_ssl"]:
            smtp.starttls()
        if conf["user"]:
            smtp.login(conf["user"], conf["password"])
        smtp.send_message(msg)


def _message_id(conf):
    """
    A Message-ID on the SENDER's domain. Bare `make_msgid()` uses the machine's own
    hostname (`vps-123.localdomain` or similar), a domain that matches nothing else in
    the message -- one of the small signals spam filters add up.
    """
    domain = parseaddr(conf["sender"])[1].rpartition("@")[2].strip()
    return make_msgid(domain=domain) if domain else make_msgid()


def is_configured():
    cfg = _read_config()
    return bool(str(_get(cfg, "SMTP_HOST", "")).strip())


def send_password_code(to_address, code, minutes):
    """
    Sends the one-time code. Raises MailNotConfigured without SMTP_HOST, and lets
    smtplib / socket errors propagate -- the caller reports them.
    """
    conf = _smtp_config()

    msg = EmailMessage()
    msg["Subject"] = "K-BOT: codul de confirmare pentru schimbarea parolei"
    msg["From"] = conf["sender"]
    msg["To"] = to_address
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = _message_id(conf)
    # Romanian, literal diacritics: this is what the operator reads.
    msg.set_content(
        "Bună ziua,\n\n"
        "Ați cerut schimbarea parolei contului K-BOT.\n\n"
        f"Codul de confirmare este:  {code}\n\n"
        f"Codul este valabil {minutes} minute și poate fi folosit o singură dată.\n"
        "Dacă nu ați cerut dumneavoastră schimbarea, ignorați acest mesaj: parola rămâne neschimbată.\n\n"
        "K-BOT\n"
    )

    logger.info(
        "password code mail -> %s via %s:%s", _mask(to_address), conf["host"], conf["port"]
    )
    _deliver(msg, conf)


def send_registration_code(to_address, code, minutes):
    """
    The same one-time code, for someone who does not have an account yet
    (slice 0075-01, the public registration page).

    Deliberately a separate function rather than a parameter on the one above: the
    two are read by different people in different situations, and the sentences say
    different things. A person changing their password knows what K-BOT is; a person
    registering has just typed a fiscal code into a web page and needs to be told
    which request this code belongs to, and what happens if it was not them.

    Same contract as send_password_code: MailNotConfigured without SMTP_HOST, and
    smtplib / socket errors propagate to the caller.
    """
    conf = _smtp_config()

    msg = EmailMessage()
    msg["Subject"] = "K-BOT: codul de confirmare a adresei de e-mail"
    msg["From"] = conf["sender"]
    msg["To"] = to_address
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = _message_id(conf)
    # Romanian, literal diacritics: this is what the applicant reads.
    msg.set_content(
        "Bună ziua,\n\n"
        "Ați început înregistrarea unei unități noi în K-BOT și ați indicat "
        "această adresă de e-mail.\n\n"
        f"Codul de confirmare este:  {code}\n\n"
        f"Codul este valabil {minutes} minute și poate fi folosit o singură dată.\n"
        "Dacă nu ați cerut dumneavoastră înregistrarea, ignorați acest mesaj: "
        "fără cod, cererea nu merge mai departe.\n\n"
        "K-BOT\n"
    )

    logger.info(
        "registration code mail -> %s via %s:%s", _mask(to_address), conf["host"], conf["port"]
    )
    _deliver(msg, conf)


def send_account_ready(to_address, denumire, link, hours):
    """
    Tells a new user their unit was approved and hands them the one-time link to
    choose a password (slice 0075-03, plan 5.6 step 8, D13).

    The database name is deliberately absent: the applicant never saw it on the page
    (operator, 22.09.2026) and does not need it -- K-BOT lists their unit after login.

    Same contract as the others: MailNotConfigured without SMTP_HOST, smtplib /
    socket errors propagate to the caller.
    """
    conf = _smtp_config()

    msg = EmailMessage()
    msg["Subject"] = "K-BOT: cererea de înregistrare a fost aprobată"
    msg["From"] = conf["sender"]
    msg["To"] = to_address
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = _message_id(conf)
    # Romanian, literal diacritics: this is what the new user reads.
    msg.set_content(
        "Bună ziua,\n\n"
        f"Cererea de înregistrare pentru «{denumire}» a fost aprobată, iar contul "
        "dumneavoastră K-BOT este gata.\n\n"
        "Alegeți parola contului deschizând linkul de mai jos:\n\n"
        f"{link}\n\n"
        f"Linkul este valabil {hours} de ore și poate fi folosit o singură dată.\n"
        f"După ce alegeți parola, vă autentificați în K-BOT cu adresa {to_address} "
        "și parola aleasă.\n\n"
        "Dacă nu ați cerut dumneavoastră înregistrarea, ignorați acest mesaj.\n\n"
        "K-BOT\n"
    )

    logger.info("account ready mail -> %s via %s:%s",
                _mask(to_address), conf["host"], conf["port"])
    _deliver(msg, conf)


def send_operator_code(to_address, code, minutes):
    """
    The second factor of the operator's sign-in to the approval page (slice 0075-05).
    That page creates databases and MariaDB accounts, so the password alone is not
    enough. Same contract as the others.
    """
    conf = _smtp_config()

    msg = EmailMessage()
    msg["Subject"] = "K-BOT: codul de acces la pagina de cereri"
    msg["From"] = conf["sender"]
    msg["To"] = to_address
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = _message_id(conf)
    # Romanian, literal diacritics: this is what the operator reads.
    msg.set_content(
        "Bună ziua,\n\n"
        "Cineva s-a autentificat cu parola dumneavoastră pe pagina de aprobare a cererilor "
        "de înregistrare K-BOT.\n\n"
        f"Codul de acces este:  {code}\n\n"
        f"Codul este valabil {minutes} minute și poate fi folosit o singură dată.\n"
        "Dacă nu ați fost dumneavoastră, schimbați parola contului K-BOT cât mai repede.\n\n"
        "K-BOT\n"
    )

    logger.info("operator code mail -> %s via %s:%s",
                _mask(to_address), conf["host"], conf["port"])
    _deliver(msg, conf)


def send_registration_rejected(to_address, denumire, motiv):
    """
    Tells the applicant their request was turned down, with the operator's reason
    (slice 0075-05, plan 7). Same contract as the others.
    """
    conf = _smtp_config()

    msg = EmailMessage()
    msg["Subject"] = "K-BOT: cererea de înregistrare nu a fost aprobată"
    msg["From"] = conf["sender"]
    msg["To"] = to_address
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = _message_id(conf)
    # Romanian, literal diacritics: this is what the applicant reads.
    msg.set_content(
        "Bună ziua,\n\n"
        f"Cererea de înregistrare pentru «{denumire}» nu a fost aprobată.\n\n"
        "Motivul:\n"
        f"{motiv}\n\n"
        "Puteți depune o cerere nouă după ce rezolvați problema de mai sus, "
        "sau ne puteți răspunde la acest mesaj.\n\n"
        "K-BOT\n"
    )

    logger.info("registration rejected mail -> %s", _mask(to_address))
    _deliver(msg, conf)


def operator_page_link():
    """Where the approval page lives, for the operator's notice mail (0075-05)."""
    base = str(_get(_read_config(), "PUBLIC_BASE_URL", "") or "https://kbot.avatarsoft.ro")
    return f"{base.rstrip('/')}/operator"


def operator_address():
    """
    Where a waiting registration is announced (slice 0075-02, plan 5.5).

    `OPERATOR_EMAIL` in config.py. Empty means nobody is told automatically, which
    the caller reports rather than treats as a failure: the request is already
    recorded and visible on the approval page either way.
    """
    return str(_get(_read_config(), "OPERATOR_EMAIL", "") or "").strip()


def send_registration_notice(to_address, id_cerere, denumire, cf, email, randuri):
    """
    Tells the operator a registration is waiting for their decision.

    Deliberately thin: the figures that matter for deciding whether to look now, and
    nothing that would let the mailbox stand in for the approval page. The row count
    is in it because it is the one number that can be alarming -- a request worth a
    few hundred classifications reads very differently from one worth fifty thousand.
    """
    conf = _smtp_config()

    msg = EmailMessage()
    msg["Subject"] = f"K-BOT: cerere de înregistrare nouă ({id_cerere})"
    msg["From"] = conf["sender"]
    msg["To"] = to_address
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = _message_id(conf)
    # Romanian, literal diacritics: this is what the operator reads.
    msg.set_content(
        "O cerere de înregistrare așteaptă aprobare.\n\n"
        f"Număr cerere:  {id_cerere}\n"
        f"Unitate:       {denumire}\n"
        f"Cod fiscal:    {cf}\n"
        f"E-mail:        {email}\n"
        f"Clasificații:  {randuri} rânduri\n\n"
        "Detaliile complete și butoanele de aprobare sunt pe pagina de cereri:\n"
        f"{operator_page_link()}\n\n"
        "K-BOT\n"
    )

    logger.info("registration notice for request %s -> %s", id_cerere, _mask(to_address))
    _deliver(msg, conf)


def _mask(address):
    """a***@domain -- for logs and for the answer sent back to the client."""
    if not address or "@" not in address:
        return "***"
    local, _, domain = address.partition("@")
    if len(local) <= 1:
        return f"{local}***@{domain}"
    return f"{local[0]}***@{domain}"


mask_address = _mask
