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


def _watchlist():
    from signals.strategies.intraday_watchlist import IntradayPick, WatchlistResult

    return WatchlistResult(
        picks=[
            IntradayPick("BULLCO", "bull", "Breakout close", ["Closed above 10-day high"],
                         candle_date=date(2026, 10, 16)),
            IntradayPick("BEARCO", "bear", "Coiled in trend", ["NR7 - narrowest range in 7 days"],
                         candle_date=date(2026, 10, 16)),
        ],
        skipped_events=["TCS", "INFY"],
        skipped_shock=["XYZ"],
    )


def test_intraday_watchlist_lists_bias_and_reasons_without_levels():
    from signals.formatting import format_intraday_watchlist

    msg = format_intraday_watchlist("Intraday Watchlist", "🔭", _watchlist(), date(2026, 10, 16), True)
    assert "Bullish (1)" in msg and "Bearish (1)" in msg
    assert "<b>BULLCO</b> — Breakout close" in msg
    assert "• Closed above 10-day high" in msg
    assert "Skipped: 2 with results/events, 1 with a news-like gap/range today" in msg
    assert "Entry" not in msg and "SL" not in msg and "T1" not in msg
    assert "NOT filtered" not in msg


def test_intraday_watchlist_flags_unfiltered_events():
    from signals.formatting import format_intraday_watchlist

    msg = format_intraday_watchlist("Intraday Watchlist", "🔭", _watchlist(), date(2026, 10, 16), False)
    assert "results/events NOT filtered" in msg
    assert "with results/events" not in msg


def test_intraday_watchlist_empty_sections_and_stale_candle():
    from signals.formatting import format_intraday_watchlist
    from signals.strategies.intraday_watchlist import IntradayPick, WatchlistResult

    result = WatchlistResult(picks=[IntradayPick("OLDCO", "bull", "Coiled in trend", [], candle_date=date(2026, 10, 15))])
    msg = format_intraday_watchlist("Intraday Watchlist", "🔭", result, date(2026, 10, 16), True)
    assert "Bearish (0)</b>\nNone today." in msg
    assert "Candle date: 15 Oct 2026 (not today)" in msg


def test_intraday_watchlist_shows_top_n_of_qualified_when_capped():
    from signals.formatting import format_intraday_watchlist

    result = _watchlist()
    result.qualified_bulls = 14
    msg = format_intraday_watchlist("Intraday Watchlist", "🔭", result, date(2026, 10, 16), True)
    assert "Bullish (1 of 14)" in msg
    assert "Bearish (1)" in msg
