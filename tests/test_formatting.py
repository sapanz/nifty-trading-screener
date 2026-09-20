from datetime import date

from signals.formatting import format_strategy_message
from signals.models import Signal


def _signal(candle_date=None, direction="long") -> Signal:
    return Signal(
        symbol="TESTCO", entry=100.0, stop_loss=95.0, targets=[110.0],
        candle_date=candle_date, direction=direction,
    )


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


def test_no_direction_tag_for_long_signal():
    # No current strategy produces a short signal - no reason to clutter
    # their messages with a tag that's always the same.
    run_date = date(2026, 9, 8)
    msg = format_strategy_message("Daily Swing", "📈", [_signal(direction="long")], run_date)
    assert "SHORT" not in msg


def test_direction_tag_for_short_signal():
    # A short's SL sits above entry and targets below - without an
    # explicit tag that geometry could easily read as a data error.
    run_date = date(2026, 9, 8)
    short_signal = Signal(
        symbol="TESTCO", entry=100.0, stop_loss=105.0, targets=[90.0], direction="short",
    )
    msg = format_strategy_message("Generic Short Strategy", "⚡", [short_signal], run_date)
    assert "SHORT" in msg
    # risk_per_share must still come out positive for a short (stop_loss -
    # entry, not entry - stop_loss) so the displayed risk % isn't negative.
    assert "5.0% risk" in msg
