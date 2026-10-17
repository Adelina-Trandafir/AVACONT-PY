"""Portal session duration, read from config.py when the service starts."""
try:
    import config
except ModuleNotFoundError as error:
    if error.name != 'config':
        raise
    config = None


SESSION_MINUTES = getattr(config, 'PORTAL_SESSION_MINUTES', 30)
if isinstance(SESSION_MINUTES, bool) or not isinstance(SESSION_MINUTES, int) or SESSION_MINUTES <= 0:
    raise ValueError('PORTAL_SESSION_MINUTES din config.py trebuie să fie un număr întreg pozitiv de minute.')

SESSION_SECONDS = SESSION_MINUTES * 60
