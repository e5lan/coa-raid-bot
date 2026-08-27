from __future__ import annotations

import os
import re
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import dateparser

RAID_TIMEZONE = ZoneInfo(os.getenv('RAID_TIMEZONE', 'UTC'))

# Matches hour values outside 1-12 paired with an am/pm suffix, e.g. "21pm" or
# "13am" — dateparser silently drops these instead of failing, so reject them
# up front rather than let it fall back to the current time of day.
_INVALID_12H_HOUR_RE = re.compile(r'\b(?:1[3-9]|2[0-4])\s*(?:am|pm)\b', re.IGNORECASE)

# "at 21" (a bare 24h hour with no colon and no am/pm) isn't reliably read as a
# time by dateparser — it can be dropped or misread as a day-of-month — so spell
# it out as "at 21:00" first.
_BARE_HOUR_RE = re.compile(r'\bat\s+([01]?\d|2[0-3])\b(?!\s*(?::|am|pm))', re.IGNORECASE)


def parse_event_time(text: str) -> datetime | None:
    if _INVALID_12H_HOUR_RE.search(text):
        return None
    text = _BARE_HOUR_RE.sub(lambda m: f'at {int(m.group(1)):02d}:00', text)
    dt = dateparser.parse(
        text,
        settings={
            'TIMEZONE': str(RAID_TIMEZONE),
            'TO_TIMEZONE': 'UTC',
            'RETURN_AS_TIMEZONE_AWARE': True,
            'PREFER_DATES_FROM': 'future',
        },
    )
    if dt is None:
        return None
    return dt.astimezone(UTC)


def format_date(dt: datetime) -> str:
    return dt.astimezone(RAID_TIMEZONE).strftime('%d %b')


def format_time(dt: datetime) -> str:
    return dt.astimezone(RAID_TIMEZONE).strftime('%H:%M')


def format_countdown(dt: datetime) -> str:
    delta = dt - datetime.now(UTC)
    seconds = delta.total_seconds()
    if seconds <= 0:
        return 'started'

    days = int(seconds // 86400)
    if days >= 1:
        return f'in {days} day{"s" if days != 1 else ""}'

    hours = int(seconds // 3600)
    if hours >= 1:
        return f'in {hours} hour{"s" if hours != 1 else ""}'

    minutes = max(1, int(seconds // 60))
    return f'in {minutes} minute{"s" if minutes != 1 else ""}'
