from datetime import date

from signals.calendar_utils import is_last_trading_day_of_month


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
