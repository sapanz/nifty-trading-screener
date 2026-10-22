import numpy as np
import pandas as pd
import pytest

from signals import config, trendline_backtest as tb
from signals.strategies import trendline_break_short as strat
from signals.trendlines import find_break, swing_lows


def _channel(n_before=40, slope=0.02, spacing=6):
    """A rising channel whose swing lows (every `spacing` bars) sit exactly on a
    straight line, ending in a bar that closes well under it. Returns the OHLC
    arrays (break bar last) and the line values."""
    n = n_before + 1
    base = 100 + slope * np.arange(n)
    low = base + 0.15
    low[3::spacing] = base[3::spacing]            # the swing lows, on the line
    high = base + 0.35
    close = base + 0.25
    open_ = base + 0.15
    t = n - 1
    open_[t], high[t], low[t], close[t] = base[t] + 0.10, base[t] + 0.12, base[t] - 0.55, base[t] - 0.45
    return open_, high, low, close, base


def test_swing_lows_found_on_the_line():
    o, h, l, c, _ = _channel()
    assert swing_lows(l[:-1])[:4] == [3, 9, 15, 21]


def test_break_of_a_rising_trendline_is_detected_with_levels():
    o, h, l, c, base = _channel()
    t = len(o) - 1
    brk = find_break(o, h, l, c, t)
    assert brk is not None
    assert brk.anchor_idx == 3 and brk.touches == 6 and brk.width_bars == t - 3
    assert brk.entry == pytest.approx(c[t])
    assert brk.stop > h[33:t + 1].max()                       # above the highest high since the last touch
    assert brk.target == pytest.approx(brk.entry - config.TRENDLINE_TARGET_R * (brk.stop - brk.entry))
    assert brk.line_at_break == pytest.approx(base[t])


def test_no_break_while_price_stays_above_the_line():
    o, h, l, c, base = _channel()
    t = len(o) - 1
    o[t], h[t], l[t], c[t] = base[t] + 0.2, base[t] + 0.4, base[t] + 0.1, base[t] + 0.3
    assert find_break(o, h, l, c, t) is None


def test_bullish_break_candle_is_not_a_break():
    o, h, l, c, base = _channel()
    t = len(o) - 1
    o[t] = c[t] - 0.1  # closes above its open
    assert find_break(o, h, l, c, t) is None


def test_line_already_broken_earlier_is_not_a_fresh_break():
    o, h, l, c, base = _channel()
    t = len(o) - 1
    c[t - 3] = base[t - 3] - 0.5   # an earlier close under the line
    assert find_break(o, h, l, c, t) is None


def test_wick_through_the_line_is_not_a_break():
    o, h, l, c, base = _channel()
    t = len(o) - 1
    l[t - 4] = base[t - 4] - 0.3   # wick pokes under, close holds
    assert find_break(o, h, l, c, t) is not None


def test_too_narrow_or_too_few_touches_is_rejected(monkeypatch):
    o, h, l, c, _ = _channel(n_before=10)
    assert find_break(o, h, l, c, len(o) - 1) is None            # shorter than the minimum width
    o, h, l, c, _ = _channel()
    monkeypatch.setattr(config, "TRENDLINE_MIN_TOUCHES", 7)
    assert find_break(o, h, l, c, len(o) - 1) is None


def test_flat_or_falling_lows_are_not_a_rising_trendline():
    o, h, l, c, base = _channel(slope=0.0)
    assert find_break(o, h, l, c, len(o) - 1) is None


def test_risk_outside_bounds_is_skipped(monkeypatch):
    o, h, l, c, _ = _channel()
    monkeypatch.setattr(config, "TRENDLINE_MAX_RISK_PCT", 0.1)
    assert find_break(o, h, l, c, len(o) - 1) is None


def test_result_does_not_depend_on_bars_after_the_break():
    o, h, l, c, _ = _channel()
    t = len(o) - 1
    pad = lambda a: np.concatenate([a, a[-1] + np.arange(1, 8) * 0.05])
    assert find_break(pad(o), pad(h), pad(l), pad(c), t).width_bars == find_break(o, h, l, c, t).width_bars


# --- strategy.scan / backtest on 5-minute frames ---------------------------------

def _frame(arrays, day="2026-10-14", extra_after=None, start="09:15"):
    o, h, l, c, _ = arrays
    n = len(o)
    idx = pd.date_range(f"{day} {start}", periods=n, freq="5min")
    df = pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": 1000.0}, index=idx)
    if extra_after:
        more = pd.DataFrame(extra_after, columns=["open", "high", "low", "close"])
        more["volume"] = 1000.0
        more.index = pd.date_range(df.index[-1] + pd.Timedelta(minutes=5), periods=len(more), freq="5min")
        df = pd.concat([df, more])
    return df


def test_scan_returns_short_signal_widest_first():
    wide = _frame(_channel(n_before=44))
    narrow = _frame(_channel(n_before=26))
    flat = _frame(_channel(n_before=40, slope=0.0))
    signals = strat.scan({"NARROW": narrow, "WIDE": wide, "FLAT": flat})
    assert [s.symbol for s in signals] == ["WIDE", "NARROW"]
    sig = signals[0]
    assert sig.direction == "short" and sig.stop_loss > sig.entry > sig.targets[0]
    assert sig.extra["width_bars"] > signals[1].extra["width_bars"]


def test_scan_ignores_breaks_outside_the_entry_window():
    late = _frame(_channel(n_before=44), start="13:00")   # break lands after 14:30
    assert strat.scan({"LATE": late}) == []


def test_scan_only_considers_the_latest_candle():
    after = [[99.0, 99.1, 98.9, 99.0]]
    df = _frame(_channel(), extra_after=after)
    assert strat.scan({"X": df}) == []     # the break is no longer the last bar


def _trade_frame(after):
    return _frame(_channel(), extra_after=after)


def test_backtest_target_hit():
    # next bar opens 100.30, then falls through the ~98.7 target
    trades = tb.run_trendline_backtest({"X": _trade_frame([[100.30, 100.4, 100.1, 100.2], [100.2, 100.2, 98.2, 98.3]])})
    (t,) = trades
    assert t.outcome == "target" and t.entry == pytest.approx(100.30)
    assert t.gross_pct > 0 and t.net_pct == pytest.approx(t.gross_pct - config.TRENDLINE_ROUND_TRIP_COST_PCT)
    assert t.r_multiple > 1.5


def test_backtest_stop_hit_and_stop_wins_a_same_bar_tie():
    stop_bar = [[100.30, 101.5, 100.2, 101.0]]
    (t,) = tb.run_trendline_backtest({"X": _trade_frame(stop_bar)})
    assert t.outcome == "stop" and t.gross_pct < 0 and t.r_multiple < 0
    both = [[100.30, 101.5, 98.0, 100.0]]            # touches stop and target in one bar
    (t2,) = tb.run_trendline_backtest({"X": _trade_frame(both)})
    assert t2.outcome == "stop"


def test_backtest_squares_off_at_the_end_of_the_session():
    (t,) = tb.run_trendline_backtest({"X": _trade_frame([[100.30, 100.5, 100.0, 100.1]] * 3)})
    assert t.outcome == "square_off" and t.exit_price == pytest.approx(100.1)


def test_backtest_skips_when_the_next_open_is_already_past_the_stop():
    assert tb.run_trendline_backtest({"X": _trade_frame([[102.0, 102.5, 101.9, 102.1], [102, 102, 101, 101]])}) == []


def test_backtest_takes_one_trade_per_stock_per_session():
    trades = tb.run_trendline_backtest({"X": _trade_frame([[100.3, 100.4, 100.1, 100.2]] * 5)})
    assert len(trades) == 1


def test_backtest_ranks_same_scan_signals_by_width():
    after = [[100.3, 100.4, 100.1, 100.2]] * 2
    # both break on the 12:55 candle: 44 bars after a 09:15 open, 30 after a 10:25 open
    wide = _frame(_channel(n_before=44), extra_after=after, start="09:15")
    narrow = _frame(_channel(n_before=30), extra_after=after, start="10:25")
    by_symbol = {t.symbol: t for t in tb.run_trendline_backtest({"WIDE": wide, "NARROW": narrow})}
    assert by_symbol["WIDE"].bar_time == by_symbol["NARROW"].bar_time == "12:55"
    assert by_symbol["WIDE"].rank_at_bar == 1 and by_symbol["NARROW"].rank_at_bar == 2
    assert by_symbol["WIDE"].signals_at_bar == 2


def test_summary_and_csv(tmp_path):
    trades = []
    for i in range(12):
        after = [[100.30, 100.4, 100.1, 100.2], [100.2, 100.2, 98.2, 98.3]] if i % 2 else [[100.30, 101.5, 100.2, 101.0]]
        trades += tb.run_trendline_backtest({"X": _frame(_channel(), day=f"2026-10-{i + 1:02d}", extra_after=after)})
    text = tb.summarize_trendline(trades, months=1)
    assert "12 trades over 12 sessions" in text and "Does width matter?" in text and "width_bars:" in text
    assert "In-sample" in text and "Out-of-sample" in text and "By month" in text
    path = tmp_path / "t.csv"
    tb.write_trendline_csv(trades, str(path))
    assert path.read_text().splitlines()[0].startswith("symbol,date,bar_time")
    assert tb.summarize_trendline([], 1).startswith("No trendline-break trades")
