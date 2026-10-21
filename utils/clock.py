"""UTC for technical instants; Romanian calendar dates for business rules.

Existing MariaDB DATETIME columns and API contracts use naive datetimes. The
UTC helpers return naive UTC explicitly, independent of the host timezone.
Date-only document values are calendar dates and must not be shifted to UTC.
"""
from datetime import datetime, timezone
import logging
import os
import time
from zoneinfo import ZoneInfo


def utc_now():
    """Current instant as naive UTC, suitable for MariaDB DATETIME."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def utc_from_timestamp(value):
    """Unix seconds to the naive UTC representation used in the database."""
    return datetime.fromtimestamp(value, tz=timezone.utc).replace(tzinfo=None)


def parse_access_time(value):
    """Gunicorn access-log time to UTC, respecting its explicit numeric offset."""
    value = value.strip()
    if ' ' in value:
        return datetime.strptime(value, '%d/%b/%Y:%H:%M:%S %z').astimezone(
            timezone.utc).replace(tzinfo=None)
    # Older offset-free log records follow the application's UTC convention.
    return datetime.strptime(value, '%d/%b/%Y:%H:%M:%S')


def bucharest_today():
    """Today's business date, including Romania's automatic DST transitions."""
    return datetime.now(ZoneInfo('Europe/Bucharest')).date()


class UtcFormatter(logging.Formatter):
    converter = time.gmtime


def configure_utc_process():
    """Pin Linux worker/library clocks and standard logging to UTC at startup."""
    os.environ['TZ'] = 'UTC'
    if hasattr(time, 'tzset'):
        time.tzset()
    logging.Formatter.converter = time.gmtime
