"""Pakistan time, the only clock a person here reads.

The business runs in Pakistan: UTC+5, with no daylight saving. An instant kept in the database is just a moment and
stays as it is. Every calendar day, hour, month and year worked out from one (today, a report's day, a bill's time,
a number that carries the year, a backup's file name) is Pakistan's, whatever zone the server machine's own clock is
set to. Use these rather than `date.today()`, `datetime.now()` or `.date()` on a UTC time.
"""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta, timezone

PKT = timezone(timedelta(hours=5))

# The hour the shop's day begins, on the Pakistan clock. Midnight unless the shop says otherwise: a shop selling past
# midnight counts the small hours as the day before, so a bill at 00:30 belongs to the evening that is still going on.
# Set once at startup from the shop's own settings (services/masters_service.py, key "trading-day"), so every figure
# the branch works out and every figure it sends head office agree on which day a bill belongs to.
_day_start_hour = 0

# SQLite's modifier that turns a stored instant into Pakistan wall time: date(at, '+5 hours') is the Pakistan day,
# strftime('%H', at, '+5 hours') the Pakistan hour.
SQL_SHIFT = "'+5 hours'"


def day_start_hour() -> int:
    """The hour the shop's day begins. 0 is midnight, 8 means a day runs 08:00 to 07:59 the next morning."""
    return _day_start_hour


def set_day_start_hour(hour: int) -> None:
    """Told once at startup and again whenever the setting changes. Out of range simply means midnight."""
    global _day_start_hour
    _day_start_hour = int(hour) if 0 <= int(hour) <= 23 else 0


def now_pk() -> datetime:
    """This moment on the Pakistan clock (an aware datetime, so it compares with any other instant)."""
    return datetime.now(PKT)


def today_pk() -> date:
    """The shop's day that is going on now, which before the day's start hour is still yesterday."""
    return (now_pk() - timedelta(hours=_day_start_hour)).date()


def pk_time(at: datetime) -> datetime:
    """An instant on the Pakistan clock. A time with no zone is taken as UTC, which is how the database keeps them."""
    return (at if at.tzinfo else at.replace(tzinfo=timezone.utc)).astimezone(PKT)


def pk_day(at: datetime | date | None = None) -> date:
    """The shop's day an instant falls on (today when none is given). A plain date is already a day.

    With a day start of 8, a bill rung at 00:30 on Tuesday belongs to Monday: the takings, the books and the figures
    head office is sent all say Monday, which is what the people who counted the drawer would say."""
    if at is None:
        return today_pk()
    if not isinstance(at, datetime):
        return at
    return (pk_time(at) - timedelta(hours=_day_start_hour)).date()


def day_start(day: date) -> datetime:
    """The instant the shop's day begins (as UTC), for filtering stored times by day: at >= day_start(d) and
    at < day_start(d + 1 day). With a day start of 8 that is 08:00 Pakistan time, not midnight."""
    return datetime.combine(day, time(hour=_day_start_hour), tzinfo=PKT).astimezone(timezone.utc)


# ── the same rule, in SQL ────────────────────────────────────────────────────────────────────────
# The queries in this codebase are written for a day that begins at midnight: date(at, '+5 hours') is the Pakistan
# day of a stored instant. Rather than teaching forty-three of them about a setting, the two services that group by
# day pass their SQL through `with_shop_day` on the way to the database, which rewrites exactly the places that mean
# a day. An hour of the day is left alone: a bill rung at one in the morning was rung at one in the morning,
# whichever day it is counted on.
# Judged on whole words: "date(" also sits inside "datetime(", and "time(" inside "strftime(", so each name is
# matched only where a function really starts. A day, a week or a weekday shifts; a clock time does not.
_DAY_CALLS = re.compile(r"(?<![A-Za-z_])(date|julianday)\(|strftime\(\s*'%[wWjuUV]'")
_CLOCK_CALLS = re.compile(r"(?<![A-Za-z_])(datetime|time)\(|strftime\(\s*'%[HMSfps]'")
_SHIFT_LITERAL = re.compile(r"'\+5 hours'")


def sql_day_shift() -> str:
    """The SQLite modifier for the shop's day: '+5 hours' at midnight, '-3 hours' when the day starts at 8."""
    return f"'{5 - _day_start_hour:+d} hours'"


def with_shop_day(sql: str) -> str:
    """A query written for a midnight day, rewritten to the shop's own day start.

    Each '+5 hours' is judged by the function it sits in: inside date(), or a day or week of strftime, it means a
    day and is shifted; inside an hour or minute it is the clock and is left as it is. Nothing happens at all while
    the day starts at midnight, which is the default and every shop until one says otherwise."""
    if _day_start_hour == 0:
        return sql
    shift = sql_day_shift()

    def last_at(pattern: re.Pattern, head: str) -> int:
        found = [m.start() for m in pattern.finditer(head)]
        return found[-1] if found else -1

    def decide(match: re.Match) -> str:
        head = sql[: match.start()]
        return match.group(0) if last_at(_CLOCK_CALLS, head) > last_at(_DAY_CALLS, head) else shift

    return _SHIFT_LITERAL.sub(decide, sql)
