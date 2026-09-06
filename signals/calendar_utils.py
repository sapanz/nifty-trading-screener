"""Calendar helpers used only for scheduling decisions."""
from __future__ import annotations

import calendar
from datetime import date


def is_last_trading_day_of_month(today: date | None = None) -> bool:
    """True if `today` is the last weekday (Mon-Fri) of its calendar month.

    This is a plain calendar approximation - it does not know about NSE
    trading holidays. If the true last trading day happens to be a market
    holiday, this will fire one weekday early instead. Good enough for a
    "once a month" screener; not exchange-calendar exact.
    """
    today = today or date.today()
    if today.weekday() >= 5:
        return False
    last_calendar_day = calendar.monthrange(today.year, today.month)[1]
    remaining_weekdays = [
        d
        for d in range(today.day + 1, last_calendar_day + 1)
        if date(today.year, today.month, d).weekday() < 5
    ]
    return len(remaining_weekdays) == 0
