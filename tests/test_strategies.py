import pandas as pd

from signals import config
from signals.strategies import cip_weekly, daily_swing, monthly_breakout, weekly_breakout


def _ramp_then_flat_df(
    freq: str, ramp_weeks: int, flat_weeks: int, start: float, plateau: float, plateau_drift: float = 0.0
) -> pd.DataFrame:
    """Rising trend for `ramp_weeks` periods up to `plateau`, then flat (or,
    with `plateau_drift` > 0, still gently rising) for `flat_weeks`."""
    n = ramp_weeks + flat_weeks
    dates = pd.date_range(end=pd.Timestamp.today().normalize(), periods=n, freq=freq)
    ramp = [start + (plateau - start) * i / ramp_weeks for i in range(ramp_weeks)]
    flat = [plateau + plateau_drift * i for i in range(flat_weeks)]
    closes = ramp + flat
    df = pd.DataFrame({"close": closes}, index=dates)
    df["open"] = df["close"]
    df["high"] = df["close"] * 1.005
    df["low"] = df["close"] * 0.995
    df["volume"] = 100_000.0
    return df


class TestWeeklyBreakout:
    def test_detects_range_breakout_with_volume(self):
        df = _ramp_then_flat_df("W-FRI", ramp_weeks=200, flat_weeks=config.BREAKOUT_RANGE_WEEKS, start=50, plateau=200)
        # tighten the consolidation range explicitly
        for i in range(1, config.BREAKOUT_RANGE_WEEKS + 1):
            df.iloc[-i, df.columns.get_loc("high")] = 202.0
            df.iloc[-i, df.columns.get_loc("low")] = 198.0
            df.iloc[-i, df.columns.get_loc("close")] = 200.0

        breakout_row = pd.DataFrame(
            {"open": [202.0], "high": [203.5], "low": [201.0], "close": [203.3], "volume": [400_000.0]},
            index=[df.index[-1] + pd.Timedelta(weeks=1)],
        )
        df = pd.concat([df, breakout_row])

        signals = weekly_breakout.scan({"TESTCO": df})
        assert len(signals) == 1
        sig = signals[0]
        assert sig.stop_loss < sig.entry < sig.targets[0] < sig.targets[1]

        # Stop-loss is anchored to the breakout level itself (range_high),
        # not the bottom of the consolidation range.
        assert sig.stop_loss == round(202.0 * (1 - config.SL_BUFFER), 2)

    def test_no_signal_when_range_too_wide(self):
        df = _ramp_then_flat_df("W-FRI", ramp_weeks=200, flat_weeks=config.BREAKOUT_RANGE_WEEKS, start=50, plateau=200)
        for i in range(1, config.BREAKOUT_RANGE_WEEKS + 1):
            df.iloc[-i, df.columns.get_loc("high")] = 260.0  # 30%+ range, not a tight consolidation
            df.iloc[-i, df.columns.get_loc("low")] = 198.0
        breakout_row = pd.DataFrame(
            {"open": [260.0], "high": [280.0], "low": [259.0], "close": [279.0], "volume": [400_000.0]},
            index=[df.index[-1] + pd.Timedelta(weeks=1)],
        )
        df = pd.concat([df, breakout_row])
        signals = weekly_breakout.scan({"TESTCO": df})
        assert signals == []


def _cip_setup_df(
    freq: str, step: pd.Timedelta, touch_lookback: int, resistance: float = 200.0, ramp_periods: int = 400, gap_periods: int = 3
) -> pd.DataFrame:
    """Ramp up towards `resistance`, then alternate peak/pullback candles
    at that level for `touch_lookback` periods - each
    peak a confirmed, distinct swing-high test of the resistance *zone*
    (a flat run has no local maxima at all, so this has to actually
    oscillate to produce real touches) - break out above it on high
    volume, drift for a few periods, then close with a bullish candle that
    dips back to the old zone and holds it as new support (the "change in
    polarity")."""
    df = _ramp_then_flat_df(freq, ramp_weeks=ramp_periods, flat_weeks=0, start=50, plateau=resistance)

    for i in range(touch_lookback):
        level = resistance if i % 2 == 0 else resistance * 0.95
        touch_row = pd.DataFrame(
            {"open": [level], "high": [level * 1.005], "low": [level * 0.995], "close": [level], "volume": [100_000.0]},
            index=[df.index[-1] + step],
        )
        df = pd.concat([df, touch_row])

    breakout_close = resistance * 1.045
    breakout_row = pd.DataFrame(
        {"open": [resistance * 1.005], "high": [resistance * 1.05], "low": [resistance], "close": [breakout_close], "volume": [500_000.0]},
        index=[df.index[-1] + step],
    )
    df = pd.concat([df, breakout_row])

    for i in range(1, gap_periods + 1):
        gap_close = breakout_close - i * (resistance * 0.01)
        gap_row = pd.DataFrame(
            {"open": [gap_close * 1.005], "high": [gap_close * 1.01], "low": [gap_close * 0.995], "close": [gap_close], "volume": [100_000.0]},
            index=[df.index[-1] + step],
        )
        df = pd.concat([df, gap_row])

    retest_row = pd.DataFrame(
        {"open": [resistance * 1.005], "high": [resistance * 1.025], "low": [resistance * 0.998], "close": [resistance * 1.02], "volume": [100_000.0]},
        index=[df.index[-1] + step],
    )
    df = pd.concat([df, retest_row])
    return df


def _scramble_touch_block(df: pd.DataFrame, touch_lookback: int, gap_periods: int = 3) -> pd.DataFrame:
    """Spread the resistance "touch" block's levels >5% apart, strictly
    increasing, so none of them are confirmed swing-high peaks (each is
    immediately topped by the next) and none cluster within
    CIP_ZONE_TOLERANCE of each other either - i.e. no genuine,
    repeatedly-tested resistance zone ever forms."""
    tail_len = gap_periods + 2  # breakout + gap rows + retest
    flat_idx = df.index[-(tail_len + touch_lookback) : -tail_len]
    level = 100.0
    for idx in flat_idx:
        level *= 1.05
        df.loc[idx, ["open", "high", "low", "close"]] = [level, level * 1.005, level * 0.995, level]
    return df


class TestCipWeekly:
    def test_detects_polarity_flip(self):
        df = _cip_setup_df("W-FRI", pd.Timedelta(weeks=1), config.CIP_WEEKLY_TOUCH_LOOKBACK)
        signals = cip_weekly.scan({"TESTCO": df})
        assert len(signals) == 1
        sig = signals[0]
        assert sig.stop_loss < sig.entry < sig.targets[0] < sig.targets[1]

        # Entry is the retest candle's high; stop-loss is the lower of the
        # retest candle's own low and the previous candle's low.
        row, prev_row = df.iloc[-1], df.iloc[-2]
        assert sig.entry == round(float(row["high"]), 2)
        assert sig.stop_loss == round(float(min(row["low"], prev_row["low"])), 2)

    def test_no_signal_with_zero_zone_points(self):
        df = _cip_setup_df("W-FRI", pd.Timedelta(weeks=1), config.CIP_WEEKLY_TOUCH_LOOKBACK)
        df = _scramble_touch_block(df, config.CIP_WEEKLY_TOUCH_LOOKBACK)
        signals = cip_weekly.scan({"TESTCO": df})
        assert signals == []

    def test_no_signal_with_only_one_zone_point(self):
        touch_lookback = config.CIP_WEEKLY_TOUCH_LOOKBACK
        gap_periods = 3
        df = _cip_setup_df("W-FRI", pd.Timedelta(weeks=1), touch_lookback, gap_periods=gap_periods)
        tail_len = gap_periods + 2
        touch_idx = df.index[-(tail_len + touch_lookback) : -tail_len]
        # Collapse every touch to a pullback level except one lone peak in
        # the middle - a single confirmed swing-high isn't a "zone" on its
        # own; CIP_WEEKLY_MIN_ZONE_POINTS=2 needs at least one more.
        peak_pos = len(touch_idx) // 2
        for pos, idx in enumerate(touch_idx):
            level = 200.0 if pos == peak_pos else 190.0
            df.loc[idx, ["open", "high", "low", "close"]] = [level, level * 1.005, level * 0.995, level]
        signals = cip_weekly.scan({"TESTCO": df})
        assert signals == []


def _pocket_pivot_df(
    l1_low: float = 90.0,
    l2_low: float = 100.0,
    pivot_close: float | None = None,
    pivot_bullish: bool = True,
    pivot_volume: float = 300_000.0,
    down_day_volume: float = 200_000.0,
    base_volume: float = 50_000.0,
) -> pd.DataFrame:
    """A long quiet padding history, then an explicit 26-day tail: an
    early confirmed swing low at `l1_low`, a flat rally plateau, a more
    recent confirmed swing low at `l2_low` (a genuine higher low when
    l2_low > l1_low), a heavier-volume "down day" (`down_day_volume`)
    planted a few days later, and finally a pivot candle whose volume
    (`pivot_volume`) is the actual signal under test. Every level between
    the two swing lows is derived from `l1_low`/`l2_low` (rather than
    hardcoded), so overriding either still produces a clean, unambiguous
    two-swing-low structure with no accidental extra local minima."""
    if pivot_close is None:
        pivot_close = l2_low * 1.10  # a modest, in-bounds pivot by default

    padding = _ramp_then_flat_df("B", ramp_weeks=70, flat_weeks=0, start=50, plateau=50)
    padding["volume"] = base_volume

    lead_low = l1_low + 4
    rows = [{"open": lead_low + 1, "high": lead_low + 2, "low": lead_low, "close": lead_low + 1, "volume": base_volume} for _ in range(3)]
    rows.append({"open": lead_low + 1, "high": lead_low + 3, "low": lead_low + 1, "close": lead_low + 2, "volume": base_volume})  # day4: confirms day5

    rows.append({"open": l1_low + 1, "high": l1_low + 2, "low": l1_low, "close": l1_low + 1, "volume": base_volume})  # day5: L1
    rows.append({"open": l1_low + 5, "high": l1_low + 7, "low": l1_low + 5, "close": l1_low + 6, "volume": base_volume})  # day6: confirms day5

    # days 7-13: a flat plateau safely above both swing lows - flat means
    # no interior local minima, regardless of how l1_low/l2_low compare.
    plateau = max(l1_low, l2_low) + 15
    for _ in range(7):
        rows.append({"open": plateau + 1, "high": plateau + 3, "low": plateau, "close": plateau + 2, "volume": base_volume})

    rows.append({"open": l2_low + 5, "high": l2_low + 7, "low": l2_low + 5, "close": l2_low + 6, "volume": base_volume})  # day14: confirms day15
    rows.append({"open": l2_low + 1, "high": l2_low + 2, "low": l2_low, "close": l2_low + 1, "volume": base_volume})  # day15: L2
    rows.append({"open": l2_low + 5, "high": l2_low + 7, "low": l2_low + 5, "close": l2_low + 6, "volume": base_volume})  # day16: confirms day15

    # days 17-25: strictly-rising lows (no interior local minima) with one
    # heavier-volume down day (day20, by closing price) planted inside -
    # the level the pivot has to beat.
    offsets = [7, 8, 9, 10, 11, 12, 13, 14, 15]
    down_day_pos = 3  # day20 is the 4th of these 9 days
    prior_close = l2_low + 6
    for pos, off in enumerate(offsets):
        low = l2_low + off
        if pos == down_day_pos:
            close = min(prior_close - 1, low + 1)  # a down day, but its low still sits above the prior day's low
        else:
            close = max(prior_close + 1, low + 2)
        rows.append({"open": low + 1, "high": close + 2, "low": low, "close": close, "volume": down_day_volume if pos == down_day_pos else base_volume})
        prior_close = close

    # day26: the pivot candle itself
    pivot_open = pivot_close - 4 if pivot_bullish else pivot_close + 4
    rows.append(
        {
            "open": pivot_open,
            "high": max(pivot_open, pivot_close) + 1,
            "low": min(pivot_open, pivot_close) - 1,
            "close": pivot_close,
            "volume": pivot_volume,
        }
    )

    tail = pd.DataFrame(rows, index=[padding.index[-1] + pd.Timedelta(days=i + 1) for i in range(len(rows))])
    return pd.concat([padding, tail])


class TestDailySwing:
    def test_detects_pocket_pivot(self):
        df = _pocket_pivot_df()
        signals = daily_swing.scan({"TESTCO": df})
        assert len(signals) == 1
        sig = signals[0]
        assert sig.stop_loss < sig.entry
        assert sig.targets == []  # no fixed target - exit is a trailing stop (see backtest.simulate_trailing)

        # Entry is the pivot candle's high; stop-loss is the lower of the
        # pivot candle's own low and the previous candle's low.
        row, prev_row = df.iloc[-1], df.iloc[-2]
        raw_entry = float(row["high"])
        raw_stop = float(min(row["low"], prev_row["low"]))
        assert sig.entry == round(raw_entry, 2)
        assert sig.stop_loss == round(raw_stop, 2)

    def test_no_signal_without_a_higher_low(self):
        # the more recent swing low (95) sits BELOW the earlier one (100) -
        # not a genuine uptrend structure.
        df = _pocket_pivot_df(l1_low=100.0, l2_low=95.0)
        signals = daily_swing.scan({"TESTCO": df})
        assert signals == []

    def test_no_signal_when_too_extended_from_the_last_higher_low(self):
        # last higher low is 100; a close of 130 is 30% above it - well
        # past DAILY_SWING_MAX_EXTENSION (18%), no longer an early entry.
        df = _pocket_pivot_df(pivot_close=130.0)
        signals = daily_swing.scan({"TESTCO": df})
        assert signals == []

    def test_no_signal_when_pivot_candle_is_red(self):
        df = _pocket_pivot_df(pivot_bullish=False)
        signals = daily_swing.scan({"TESTCO": df})
        assert signals == []

    def test_no_signal_when_volume_is_below_its_own_average(self):
        # base rate is 50,000/day; a below-average pivot day isn't the
        # volume anomaly this setup needs, whatever the down-day
        # comparison says.
        df = _pocket_pivot_df(pivot_volume=40_000.0, down_day_volume=30_000.0)
        signals = daily_swing.scan({"TESTCO": df})
        assert signals == []

    def test_no_signal_when_pivot_volume_is_below_the_worst_down_day(self):
        # the heaviest down-day in the trailing window (250,000) beats the
        # pivot's own volume (200,000) - no real buying-vs-selling edge.
        df = _pocket_pivot_df(pivot_volume=200_000.0, down_day_volume=250_000.0)
        signals = daily_swing.scan({"TESTCO": df})
        assert signals == []


class TestMonthlyBreakout:
    def _monthly_df(self, months=36):
        dates = pd.date_range(end=pd.Timestamp.today().normalize(), periods=months, freq="ME")
        closes = [100.0 + 5 * (i % 6) for i in range(months)]  # oscillates, historical ATH = 100 + 5*5 = 125
        df = pd.DataFrame({"close": closes}, index=dates)
        df["open"] = df["close"]
        df["high"] = df["close"] * 1.01
        df["low"] = df["close"] * 0.99
        df["volume"] = 50_000.0
        return df

    def test_detects_ath_breakout_and_reports_gap(self):
        df = self._monthly_df()
        prior_ath = df["close"].iloc[:-1].max()
        ath_month_idx = df["close"].iloc[:-1].values.tolist().index(prior_ath)
        df.iloc[-1, df.columns.get_loc("close")] = prior_ath * 1.10
        df.iloc[-1, df.columns.get_loc("high")] = prior_ath * 1.11
        df.iloc[-1, df.columns.get_loc("volume")] = 200_000.0

        signals = monthly_breakout.scan({"TESTCO": df})
        assert len(signals) == 1
        sig = signals[0]
        assert sig.extra["months_gap"] == len(df) - 1 - ath_month_idx
        assert sig.stop_loss < sig.entry < sig.targets[0] < sig.targets[1]

    def test_sorted_biggest_gap_first(self):
        df_a = self._monthly_df(months=36)
        df_b = self._monthly_df(months=18)
        for df in (df_a, df_b):
            prior_ath = df["close"].iloc[:-1].max()
            df.iloc[-1, df.columns.get_loc("close")] = prior_ath * 1.10
            df.iloc[-1, df.columns.get_loc("high")] = prior_ath * 1.11
            df.iloc[-1, df.columns.get_loc("volume")] = 200_000.0

        signals = monthly_breakout.scan({"LONGGAP": df_a, "SHORTGAP": df_b})
        assert [s.symbol for s in signals] == ["LONGGAP", "SHORTGAP"]

    def test_no_signal_without_breakout(self):
        df = self._monthly_df()
        signals = monthly_breakout.scan({"TESTCO": df})
        assert signals == []
