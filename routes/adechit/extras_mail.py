"""Reads new statement PDFs from a mailbox over IMAP (ADECHIT, SLICE-AD13-01).

Read-only on the mailbox: it is opened read-only and messages are fetched with BODY.PEEK, so nothing
is marked as read, moved or deleted. Only PDF attachments whose name contains the configured text
(default «ExtrasEP») are returned; everything else in the mail is ignored.

The place already processed is a UID (UIDVALIDITY + last UID). On the first run, or when the server
renumbered the mailbox, the search starts from a date instead. Either way a statement seen twice is
harmless: the import deduplicates (extras_intake).

No database here. The password arrives as an argument and is never logged or put in a message.
"""
import email
import email.policy
import imaplib
import ipaddress
import logging
import os
import re
import socket
import ssl
from email.utils import parsedate_to_datetime

from .domain import DomainError

logger = logging.getLogger(__name__)

PORT = 993                       # IMAP over TLS only; no plain-text connection is offered
MAX_MESSAGES = 60                # per run; the page says when more are waiting
MAX_MESSAGE_BYTES = 25 * 1024 * 1024
MAX_ATTACHMENT_BYTES = 12 * 1024 * 1024
TIMEOUT_SECONDS = 30
_HOST = re.compile(r'^(?=.{1,253}$)([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,}$')
_MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']


def check_host(host):
    """Refuse anything that is not a public mail server: the host is typed by an operator and the
    connection is made from the server, so a local address would let the page probe the server's
    own network."""
    if not isinstance(host, str) or not _HOST.match(host.strip()):
        raise DomainError('MAIL_HOST', 'Serverul de e-mail trebuie să fie un nume de forma imap.exemplu.ro.', 400)
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host.strip(), PORT, type=socket.SOCK_STREAM)}
    except socket.gaierror as error:
        raise DomainError('MAIL_HOST', f'Serverul de e-mail «{host.strip()}» nu a fost găsit.', 400) from error
    if not addresses or not all(ipaddress.ip_address(address).is_global for address in addresses):
        raise DomainError('MAIL_HOST', 'Serverul de e-mail trebuie să fie public (nu o adresă internă).', 400)


def _imap_date(value):
    return f'{value.day:02d}-{_MONTHS[value.month - 1]}-{value.year}'


def _quoted(value):
    """An IMAP quoted string; quotes and backslashes are dropped, a mailbox name never needs them."""
    return '"' + re.sub(r'["\\\r\n]', '', value) + '"'


def _blob(data):
    return b' '.join(part for item in data or [] for part in (item if isinstance(item, tuple) else (item,))
                     if isinstance(part, bytes))


def _checked(result, step):
    status, data = result
    if status != 'OK':
        raise DomainError('MAIL_SERVER', f'Serverul de e-mail a refuzat comanda «{step}».', 502)
    return data


def _attachments(raw, name_part):
    """(sender, subject, mail date, [(file name, bytes)]) of the matching PDF attachments."""
    message = email.message_from_bytes(raw, policy=email.policy.default)
    found = []
    for part in message.walk():
        name = part.get_filename()
        if not name:
            continue
        name = os.path.basename(name.replace('\\', '/')).strip()[:255]
        if not name.lower().endswith('.pdf') or name_part.lower() not in name.lower():
            continue
        payload = part.get_payload(decode=True)
        if payload and len(payload) <= MAX_ATTACHMENT_BYTES:
            found.append((name, payload))
    try:
        mail_date = parsedate_to_datetime(message['Date']).replace(tzinfo=None) if message['Date'] else None
    except (TypeError, ValueError):
        mail_date = None
    return str(message['From'] or '')[:255], str(message['Subject'] or '')[:255], mail_date, found


def fetch_new(settings, password):
    """Connect, find the mails after the saved place and return their matching PDFs.

    `settings`: host, user, folder, sender, name_part, since (date), uidvalidity, last_uid.
    Returns {uidvalidity, last_uid, scanned, more, files: [{uid, sender, subject, mail_date,
    file, data}]}; the caller saves uidvalidity / last_uid only after it stored the files."""
    check_host(settings['host'])
    if settings.get('sender') and not settings['sender'].isascii():
        raise DomainError('MAIL_FILTER', 'Filtrul de expeditor trebuie să conțină doar litere ASCII.', 400)
    context = ssl.create_default_context()
    try:
        box = imaplib.IMAP4_SSL(settings['host'].strip(), PORT, ssl_context=context, timeout=TIMEOUT_SECONDS)
    except (OSError, imaplib.IMAP4.error) as error:
        raise DomainError('MAIL_CONNECT', 'Nu s-a putut stabili conexiunea cu serverul de e-mail. '
                          'Verificați numele serverului.', 502) from error
    try:
        try:
            box.login(settings['user'], password)
        except imaplib.IMAP4.error as error:
            raise DomainError('MAIL_LOGIN', 'Serverul de e-mail a refuzat autentificarea. La Gmail și Yahoo este '
                              'necesară o «parolă de aplicație», nu parola contului, iar IMAP trebuie activat.',
                              400) from error
        _checked(box.select(_quoted(settings['folder']), readonly=True), 'select')
        validity = int(((box.response('UIDVALIDITY')[1] or [b'0'])[0]) or 0)
        next_uid = int(((box.response('UIDNEXT')[1] or [b'0'])[0]) or 0)
        resume = settings.get('last_uid') is not None and settings.get('uidvalidity') == validity
        criteria = ['UID', f"{int(settings['last_uid']) + 1}:*"] if resume else ['SINCE', _imap_date(settings['since'])]
        if settings.get('sender'):
            criteria += ['FROM', _quoted(settings['sender'])]
        found = _blob(_checked(box.uid('SEARCH', None, *criteria), 'search')).split()
        # `UID n:*` always includes the newest message even when it is older than n.
        uids = sorted(int(item) for item in found if not resume or int(item) > int(settings['last_uid']))
        batch, more = uids[:MAX_MESSAGES], len(uids) > MAX_MESSAGES
        files = []
        for uid in batch:
            info = _blob(_checked(box.uid('FETCH', str(uid), '(BODYSTRUCTURE RFC822.SIZE)'), 'fetch'))
            size = re.search(rb'RFC822\.SIZE (\d+)', info)
            if b'pdf' not in info.lower() or (size and int(size.group(1)) > MAX_MESSAGE_BYTES):
                continue
            data = _checked(box.uid('FETCH', str(uid), '(BODY.PEEK[])'), 'fetch')
            raw = next((item[1] for item in data if isinstance(item, tuple)), b'')
            sender, subject, mail_date, attachments = _attachments(raw, settings['name_part'])
            files.extend({'uid': uid, 'sender': sender, 'subject': subject, 'mail_date': mail_date,
                          'file': name, 'data': payload} for name, payload in attachments)
        # Everything up to the end of the batch is done; if the batch was the whole result the place
        # moves to the end of the mailbox, so mails that do not match are not searched again.
        done = batch[-1] if batch else int(settings['last_uid'] or 0) if resume else 0
        last_uid = done if more else max(done, next_uid - 1)
        return {'uidvalidity': validity, 'last_uid': last_uid, 'scanned': len(batch), 'more': more, 'files': files}
    except (OSError, imaplib.IMAP4.error) as error:
        logger.warning('ADE mail fetch failed: %s', type(error).__name__)
        raise DomainError('MAIL_CONNECT', 'Conexiunea cu serverul de e-mail a fost întreruptă. Reîncercați.', 502) from error
    finally:
        try:
            box.logout()
        except (OSError, imaplib.IMAP4.error):
            logger.debug('ADE mail logout failed', exc_info=True)


def test_login(settings, password):
    """Connect, log in, open the folder; raises DomainError with a Romanian message otherwise."""
    check_host(settings['host'])
    try:
        box = imaplib.IMAP4_SSL(settings['host'].strip(), PORT, ssl_context=ssl.create_default_context(),
                                timeout=TIMEOUT_SECONDS)
    except (OSError, imaplib.IMAP4.error) as error:
        raise DomainError('MAIL_CONNECT', 'Nu s-a putut stabili conexiunea cu serverul de e-mail.', 502) from error
    try:
        try:
            box.login(settings['user'], password)
        except imaplib.IMAP4.error as error:
            raise DomainError('MAIL_LOGIN', 'Serverul de e-mail a refuzat autentificarea. La Gmail și Yahoo este '
                              'necesară o «parolă de aplicație», nu parola contului.', 400) from error
        status, data = box.select(_quoted(settings['folder']), readonly=True)
        if status != 'OK':
            raise DomainError('MAIL_FOLDER', f"Dosarul «{settings['folder']}» nu există în căsuța de e-mail.", 400)
        return int((data[0] or b'0').decode('ascii', 'ignore') or 0)
    except (OSError, imaplib.IMAP4.error) as error:
        raise DomainError('MAIL_CONNECT', 'Conexiunea cu serverul de e-mail a fost întreruptă.', 502) from error
    finally:
        try:
            box.logout()
        except (OSError, imaplib.IMAP4.error):
            logger.debug('ADE mail logout failed', exc_info=True)
