"""US equity session calendar: which daily bar a scan *should* be grading.

Finbot grades whatever the last daily bar from Yahoo is. Shortly after the
post-market close (8pm US/Eastern, i.e. 00:00 UTC during EDT), Yahoo's daily
history can briefly stop returning the session that just closed, so a late
GitHub Actions run would silently grade (and re-alert) the previous session.
``expected_session`` gives the session a scan started at ``now`` must see so
the caller can detect that stale feed instead of trusting it.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

import pandas as pd
from pandas.tseries.holiday import (
    AbstractHolidayCalendar,
    GoodFriday,
    Holiday,
    USLaborDay,
    USMartinLutherKingJr,
    USMemorialDay,
    USPresidentsDay,
    USThanksgivingDay,
    nearest_workday,
    sunday_to_monday,
)

NY = ZoneInfo("America/New_York")
REGULAR_CLOSE = time(16, 0)


class _NYSEHolidayCalendar(AbstractHolidayCalendar):
    """Full-day NYSE closures (early-close days still trade)."""

    rules = [
        # NYSE does not observe a Saturday New Year's Day on the prior Friday.
        Holiday("New Year's Day", month=1, day=1, observance=sunday_to_monday),
        USMartinLutherKingJr,
        USPresidentsDay,
        GoodFriday,
        USMemorialDay,
        Holiday("Juneteenth", month=6, day=19, start_date="2022-01-01", observance=nearest_workday),
        Holiday("Independence Day", month=7, day=4, observance=nearest_workday),
        USLaborDay,
        USThanksgivingDay,
        Holiday("Christmas Day", month=12, day=25, observance=nearest_workday),
    ]


@lru_cache(maxsize=None)
def _holidays(year: int) -> frozenset[date]:
    days = _NYSEHolidayCalendar().holidays(start=f"{year}-01-01", end=f"{year}-12-31")
    return frozenset(pd.Timestamp(d).date() for d in days)


def is_trading_day(day: date) -> bool:
    return day.weekday() < 5 and day not in _holidays(day.year)


def previous_trading_day(day: date) -> date:
    candidate = day - timedelta(days=1)
    while not is_trading_day(candidate):
        candidate -= timedelta(days=1)
    return candidate


def expected_session(now: datetime | None = None) -> date:
    """Latest session whose regular close (16:00 US/Eastern) has passed at ``now``.

    A scan at 00:23 UTC on Tuesday is still Monday evening in New York, so the
    expected session is Monday, not Friday.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    local = now.astimezone(NY)
    today = local.date()
    if is_trading_day(today) and local.time() >= REGULAR_CLOSE:
        return today
    return previous_trading_day(today)
