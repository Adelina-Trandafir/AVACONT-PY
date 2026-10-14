"""SLICE-VC01-03: log lines that can be followed per user / context / operation without passwords or full
personal data. The e-mail is masked, the unit is the DC database name, the request id ties the page, the log
and the error box together."""
import logging
import re
import uuid

logger = logging.getLogger("vercon")

_REQUEST_ID = re.compile(r'^[A-Za-z0-9-]{8,64}$')


def mask_email(email):
    """ab***@d***.ro: enough to recognise the account, not enough to reuse it."""
    text = str(email or '')
    local, _, domain = text.partition('@')
    if not domain:
        return '***'
    head, _, tail = domain.rpartition('.')
    return '%s***@%s***.%s' % (local[:2], head[:1], tail) if head else '%s***@***' % local[:2]


def request_id(header_value):
    """The id the page sent, if well formed; otherwise a new one."""
    value = (header_value or '').strip()
    return value if _REQUEST_ID.match(value) else uuid.uuid4().hex


def line(operation, outcome, context, rid, reason=None):
    text = 'VR op=%s outcome=%s unit=%s user=%s rid=%s' % (
        operation, outcome, (context or {}).get('db_name'), mask_email((context or {}).get('email')), rid)
    return text + (' reason=%s' % reason if reason else '')


def info(operation, outcome, context, rid):
    logger.info(line(operation, outcome, context, rid))


def refusal(operation, context, rid, reason):
    logger.warning(line(operation, 'refused', context, rid, reason))


# SLICE-VC04-05: the audit distinguishes these outcomes. `refused` = a rule said no, `busy` = the gestiune is held by
# someone else, `rollback` = the work started and was undone, `commit` = it was applied, `unknown` = the answer was
# lost after the work may have been applied (only a check of the operation key tells which).
def busy(operation, context, rid):
    logger.warning(line(operation, 'busy', context, rid))


def rollback(operation, context, rid, reason=None):
    logger.warning(line(operation, 'rollback', context, rid, reason))


def commit(operation, context, rid):
    logger.info(line(operation, 'commit', context, rid))


def unknown(operation, context, rid, reason=None):
    logger.error(line(operation, 'unknown', context, rid, reason))
