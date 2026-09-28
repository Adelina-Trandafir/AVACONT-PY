import logging
from logging.handlers import RotatingFileHandler
from flask import request, has_request_context, g

# The file the root logger writes. routes/logs.py reads it back (and its
# rotated copies .1 .. .5), so both sides take the name from here.
SERVER_LOG_PATH = 'api_server.log'
SERVER_LOG_BACKUPS = 5


class RequestIPFilter(logging.Filter):
    def filter(self, record):
        if has_request_context():
            record.ip = request.remote_addr
        else:
            record.ip = '-'
        return True


class SessionTagFilter(logging.Filter):
    """
    Slice 0089: ties every line written inside an authenticated K-BOT request to
    its login. Sets `record.session_tag` to

        {s=<first 8 chars of the token> u=<user> dc=<unit database>}

    followed by one space, or to '' when there is no session (startup, the
    legacy X-Api-Key path, the login route itself). routes/logs.py uses the tag
    to hand an operator ONLY their own lines, grouped by login.

    Only 8 characters of the token, the same prefix the AUTH_* lines already
    log -- never the whole bearer credential.
    """
    def filter(self, record):
        record.session_tag = ''
        if has_request_context():
            try:
                session = getattr(g, 'session', None)
                token = getattr(g, 'session_token', None) or ''
                if session is not None and token:
                    record.session_tag = '{s=%s u=%s dc=%s} ' % (
                        token[:8], session.username or '-', session.db_name or '-')
            except Exception:          # a log line must never fail because of its tag
                record.session_tag = ''
        return True


def setup_logger():
    log_formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(ip)s - %(session_tag)s%(message)s')

    log_handler = RotatingFileHandler(SERVER_LOG_PATH, maxBytes=10*1024*1024,
                                      backupCount=SERVER_LOG_BACKUPS)
    log_handler.setFormatter(log_formatter)

    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)

    if not logger.handlers:
        ip_filter = RequestIPFilter()
        session_filter = SessionTagFilter()

        log_handler.addFilter(ip_filter)
        log_handler.addFilter(session_filter)
        logger.addHandler(log_handler)

        console_handler = logging.StreamHandler()
        console_handler.setFormatter(log_formatter)
        console_handler.addFilter(ip_filter)
        console_handler.addFilter(session_filter)
        logger.addHandler(console_handler)

    logger.info("--- SERVER LOGGING INITIALIZED ---")
    return logger
