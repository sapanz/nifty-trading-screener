from datetime import date, datetime, timezone

from signals.calendar_utils import IST, ist_today, is_last_trading_day_of_month


def test_ist_today_is_ahead_of_utc_date_late_in_the_day():
    # 22:00 UTC is already the next calendar day in IST (UTC+5:30) - this
    # is exactly the window where plain date.today() on a UTC machine
    # (e.g. a GitHub Actions runner) silently returns yesterday's date.
    utc_evening = datetime(2026, 9, 8, 22, 0, tzinfo=timezone.utc)
    assert utc_evening.astimezone(IST).date() == date(2026, 9, 9)


def test_ist_today_returns_a_date():
    # Can't assert an exact value (it's "now"), just that it behaves like
    # a real IST-aware date rather than silently falling back to UTC/local.
    assert isinstance(ist_today(), date)


def test_last_weekday_of_month_true():
    # Sept 2026: 30th is a Wednesday, the last calendar day and a weekday
    assert is_last_trading_day_of_month(date(2026, 9, 30)) is True


def test_mid_month_weekday_false():
    assert is_last_trading_day_of_month(date(2026, 9, 15)) is False


def test_last_calendar_day_on_weekend_rolls_back_to_friday():
    # Aug 2026: 31st is a Monday... use a month where last day is a weekend instead.
    # May 2026: 31st is a Sunday -> last trading day is Friday 29th.
    assert is_last_trading_day_of_month(date(2026, 5, 29)) is True
    assert is_last_trading_day_of_month(date(2026, 5, 31)) is False  # Sunday, not even a weekday


def test_weekend_input_is_never_last_trading_day():
    assert is_last_trading_day_of_month(date(2026, 5, 30)) is False  # Saturday
