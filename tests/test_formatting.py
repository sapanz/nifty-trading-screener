from datetime import date

from signals.formatting import format_strategy_message
from signals.models import Signal


def _signal(candle_date=None) -> Signal:
    return Signal(symbol="TESTCO", entry=100.0, stop_loss=95.0, targets=[110.0], candle_date=candle_date)


def test_no_warning_when_candle_date_matches_run_date():
    run_date = date(2026, 9, 8)
    msg = format_strategy_message("Daily Swing", "📈", [_signal(candle_date=run_date)], run_date)
    assert "Candle date" not in msg


def test_warns_when_candle_date_is_stale():
    # The signal's own candle is a day older than the run date - exactly
    # the gap that motivated tracking candle_date at all: a stale/delayed
    # Upstox fetch, or a run landing very late (past IST midnight), can
    # leave the last available candle behind whatever day the screener
    # actually ran on.
    run_date = date(2026, 9, 8)
    stale_date = date(2026, 9, 7)
    msg = format_strategy_message("Daily Swing", "📈", [_signal(candle_date=stale_date)], run_date)
    assert "⚠️ Candle date: 07 Sep 2026 (not today)" in msg


def test_no_warning_when_candle_date_is_unset():
    run_date = date(2026, 9, 8)
    msg = format_strategy_message("Daily Swing", "📈", [_signal(candle_date=None)], run_date)
    assert "Candle date" not in msg
