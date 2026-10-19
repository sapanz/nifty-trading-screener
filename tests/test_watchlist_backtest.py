import pandas as pd
import pytest

from signals import watchlist_backtest as wb


def _trend_df(direction: int, n: int = 90) -> pd.DataFrame:
    """Steady trend ending today (the backtest window is relative to today).
    Constant +/-1% day ranges so no ordinary day is itself a watchlist pick."""
    dates = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=n)
    closes = [100.0 * (1 + direction * 0.003) ** i for i in range(n)]
    return pd.DataFrame(
        {
            "open": [c * (1 - direction * 0.002) for c in closes],
            "high": [c * 1.01 for c in closes],
            "low": [c * 0.99 for c in closes],
            "close": closes,
            "volume": [100_000.0] * n,
        },
        index=dates,
    )


def _with_signal_and_next_day(direction: int, next_open_mult: float, next_close_mult: float,
                              next_high_mult: float, next_low_mult: float) -> pd.DataFrame:
    """Signal candle at index -3 (a breakout/breakdown close), the day we
    measure at -2, one more ordinary day at -1. Next-day prices are
    multiples of the signal candle's close."""
    df = _trend_df(direction)
    prev = df["close"].iloc[-4]
    if direction == 1:
        df.iloc[-3] = [prev * 1.002, prev * 1.03, prev * 1.0, prev * 1.028, 200_000.0]
    else:
        df.iloc[-3] = [prev * 0.998, prev * 1.0, prev * 0.97, prev * 0.972, 200_000.0]
    sig_close = df["close"].iloc[-3]
    df.iloc[-2] = [sig_close * next_open_mult, sig_close * next_high_mult,
                   sig_close * next_low_mult, sig_close * next_close_mult, 100_000.0]
    return df


def _picks_on(outcomes, df, offset=-3):
    return [o for o in outcomes if o.signal_date == df.index[offset]]


def test_bull_pick_next_day_is_measured_from_the_open():
    df = _with_signal_and_next_day(1, 1.005, 1.0251, 1.0301, 1.0030)
    outcomes, _, _ = wb.run_watchlist_backtest({"BULLCO": df}, months=1, industries={})
    (o,) = _picks_on(outcomes, df)
    assert o.direction == "bull" and o.next_date == df.index[-2]
    assert o.gap_pct == pytest.approx(0.5)
    assert o.open_to_close_pct == pytest.approx((1.0251 / 1.005 - 1) * 100)
    assert o.close_to_close_pct == pytest.approx(2.51)
    assert o.favorable_pct == pytest.approx((1.0301 / 1.005 - 1) * 100)
    assert o.adverse_pct == pytest.approx((1 - 1.0030 / 1.005) * 100)
    assert o.range_pct == pytest.approx((1.0301 - 1.0030) / 1.005 * 100)


def test_bear_pick_returns_and_excursions_are_direction_adjusted():
    # next day opens down 0.5%, falls to a 3% low, closes down 2%
    df = _with_signal_and_next_day(-1, 0.995, 0.98, 0.9970, 0.9700)
    outcomes, _, _ = wb.run_watchlist_backtest({"BEARCO": df}, months=1, industries={})
    (o,) = _picks_on(outcomes, df)
    assert o.direction == "bear"
    assert o.gap_pct == pytest.approx(0.5)  # gapped down = in the bear's favor
    assert o.open_to_close_pct == pytest.approx(-(0.98 / 0.995 - 1) * 100)
    assert o.open_to_close_pct > 0          # bear played out -> positive
    assert o.favorable_pct == pytest.approx((0.995 / 0.97 - 1) * 100)
    assert o.adverse_pct == pytest.approx((0.9970 / 0.995 - 1) * 100)


def test_pick_that_reverses_is_a_negative_return():
    df = _with_signal_and_next_day(1, 1.003, 0.99, 1.0035, 0.985)  # bull closes down
    outcomes, _, _ = wb.run_watchlist_backtest({"BULLCO": df}, months=1, industries={})
    (o,) = _picks_on(outcomes, df)
    assert o.open_to_close_pct < 0
    assert o.adverse_pct == pytest.approx((1 - 0.985 / 1.003) * 100)


def test_signal_on_the_last_day_has_no_next_day_and_is_left_out():
    df = _trend_df(1)
    prev = df["close"].iloc[-2]
    df.iloc[-1] = [prev * 1.002, prev * 1.03, prev * 1.0, prev * 1.028, 200_000.0]
    outcomes, _, _ = wb.run_watchlist_backtest({"BULLCO": df}, months=1, industries={})
    assert [o for o in outcomes if o.signal_date == df.index[-1]] == []


def test_stale_symbol_is_not_measured_against_the_wrong_day():
    # BULLCO's data ends at -3 (e.g. suspended): it has no candle on the
    # later dates, so its stale last candle must never be treated as fresh.
    full = _with_signal_and_next_day(1, 1.005, 1.0251, 1.0301, 1.0030)
    stale = full.iloc[:-2]
    outcomes, _, _ = wb.run_watchlist_backtest({"BULLCO": stale, "OTHER": _trend_df(1)}, months=1, industries={})
    assert [o for o in outcomes if o.symbol == "BULLCO"] == []  # nothing follows its last candle


def test_baseline_covers_every_stock_with_a_next_day():
    df = _with_signal_and_next_day(1, 1.005, 1.0251, 1.0301, 1.0030)
    flat = _trend_df(1)
    _, baseline, days = wb.run_watchlist_backtest({"A": df, "B": flat}, months=1, industries={})
    assert days > 5
    # two stocks on every scanned day except the very last (no next day yet)
    assert len(baseline) == 2 * (days - 1)
    assert all(b.range_pct > 0 for b in baseline)


def test_summary_reports_both_sides_and_the_baseline_comparison():
    bull = _with_signal_and_next_day(1, 1.005, 1.0251, 1.0301, 1.0030)
    bear = _with_signal_and_next_day(-1, 0.995, 0.98, 0.9970, 0.9700)
    outcomes, baseline, days = wb.run_watchlist_backtest({"BULLCO": bull, "BEARCO": bear}, months=1, industries={})
    # The smooth synthetic downtrend makes every ordinary day a (correct)
    # NR7 "coiled" pick too - keep just the two signal days built above.
    outcomes = [o for o in outcomes if o.signal_date == bull.index[-3]]
    text = wb.summarize_watchlist(outcomes, baseline, days, months=1)
    assert "picks over" in text and "1 bull, 1 bear" in text
    assert "Vs. every Nifty 500 stock, same days" in text
    assert "Avg day range: picks" in text and "vs all" in text
    assert "Bull (1): open→close" in text and "Bear (1): open→close" in text
    assert "right way 100%" in text
    assert "moved ≥1% my way 100%" in text
    assert "Breakout close (1)" in text and "Breakdown close (1)" in text


def test_summary_with_no_picks_says_so():
    assert wb.summarize_watchlist([], [], 10, 1) == "No watchlist picks with a next day in this window."


def test_csv_round_trip(tmp_path):
    df = _with_signal_and_next_day(1, 1.005, 1.0251, 1.0301, 1.0030)
    outcomes, _, _ = wb.run_watchlist_backtest({"BULLCO": df}, months=1, industries={})
    path = tmp_path / "w.csv"
    wb.write_watchlist_csv(outcomes, str(path))
    lines = path.read_text().splitlines()
    assert lines[0].startswith("symbol,signal_date,next_date,direction,setup")
    assert len(lines) == 1 + len(outcomes)


def test_outcome_carries_signal_candle_features_and_market_excess():
    df = _with_signal_and_next_day(1, 1.005, 1.0251, 1.0301, 1.0030)
    outcomes, _, _ = wb.run_watchlist_backtest({"BULLCO": df}, months=1, industries={})
    (o,) = _picks_on(outcomes, df)
    assert o.features["vol_ratio"] == pytest.approx(2.0)
    assert o.features["close_strength"] > 0.75
    assert o.features["beyond_level_pct"] > 0
    assert {"range_atr", "atr_pct", "ema_spread_pct", "mom5_pct", "mkt_ret1_pct", "weekday"} <= set(o.features)
    # a single-stock universe is its own market, so the excess is ~0
    assert o.excess_open_to_close_pct == pytest.approx(0.0, abs=1e-9)


def test_bear_features_are_direction_adjusted():
    df = _with_signal_and_next_day(-1, 0.995, 0.98, 0.9970, 0.9700)
    outcomes, _, _ = wb.run_watchlist_backtest({"BEARCO": df}, months=1, industries={})
    (o,) = _picks_on(outcomes, df)
    assert o.features["close_strength"] > 0.75   # closed near the low = strong for a bear
    assert o.features["beyond_level_pct"] > 0    # closed below the prior low
    assert o.features["signal_day_pct"] > 0


def _outcome_with(direction, setup, day, excess, o2c, vol):
    ts = pd.Timestamp("2025-01-01") + pd.Timedelta(days=day)
    return wb.WatchlistOutcome(
        symbol="X", signal_date=ts, next_date=ts + pd.Timedelta(days=1), direction=direction, setup=setup,
        open_to_close_pct=o2c, close_to_close_pct=o2c, gap_pct=0.0, favorable_pct=1.0, adverse_pct=1.0,
        range_pct=2.0, excess_open_to_close_pct=excess, features={"vol_ratio": vol, "weekday": float(day % 5)},
    )


def test_mine_features_buckets_and_splits_by_date():
    outcomes = []
    for d in range(100):
        for k in range(5):
            outcomes.append(_outcome_with("bull", "Breakout close", d, 0.1 * k, 0.2, 1.0 + k))
    text = wb.mine_features(outcomes)
    assert "in-sample = signal days up to" in text
    assert "<b>Bull</b> overall: IS n=300" in text and "OOS n=200" in text
    assert "vol_ratio:" in text and "weekday:" in text
    assert "Bear" in text  # empty direction still reported without crashing


def test_mine_features_with_no_picks():
    assert wb.mine_features([]) == "No picks to mine."


def test_csv_includes_feature_columns(tmp_path):
    path = tmp_path / "w.csv"
    wb.write_watchlist_csv([_outcome_with("bull", "Breakout close", 1, 0.1, 0.2, 1.5)], str(path))
    header, row = path.read_text().splitlines()[:2]
    assert "excess_open_to_close_pct" in header and "vol_ratio" in header and "features" not in header.split(",")
    assert "1.5" in row
