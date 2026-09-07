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


def _accumulation_spring_df(
    base_low: float = 195.0,
    base_high: float = 205.0,
    first_half_volume: float = 150_000.0,
    second_half_volume: float = 80_000.0,
    breakout_volume: float = 300_000.0,
    retest_low_mult: float = 1.015,
    retest_bullish: bool = True,
    retest_volume: float = 90_000.0,
    filler_dip: bool = False,
) -> pd.DataFrame:
    """Ramp up to `base_low`, then a DAILY_SWING_BASE_LENGTH-day tight base
    between `base_low`/`base_high` whose second half trades on lower
    volume than its first half (accumulation: real supply drying up) -
    then a breakout candle closing above the base on a volume surge, a
    few quiet filler days holding above the base, and finally a bullish,
    low-volume retest candle dipping back down near the base high (now
    support). All the breakout/filler/retest prices are expressed as
    multiples of `base_high` so the geometry holds regardless of the
    base's own absolute level."""
    base_length = config.DAILY_SWING_BASE_LENGTH
    half = base_length // 2
    df = _ramp_then_flat_df("B", ramp_weeks=150, flat_weeks=0, start=50, plateau=base_low)

    mid = (base_high + base_low) / 2
    base_rows = pd.DataFrame(
        {
            "open": [mid] * base_length,
            "high": [base_high] * base_length,
            "low": [base_low] * base_length,
            "close": [mid] * base_length,
            "volume": [first_half_volume] * half + [second_half_volume] * (base_length - half),
        },
        index=[df.index[-1] + pd.Timedelta(days=i + 1) for i in range(base_length)],
    )
    df = pd.concat([df, base_rows])

    breakout_row = pd.DataFrame(
        {
            "open": [base_high * 1.005],
            "high": [base_high * 1.073],
            "low": [base_high * 1.010],
            "close": [base_high * 1.063],
            "volume": [breakout_volume],
        },
        index=[df.index[-1] + pd.Timedelta(days=1)],
    )
    df = pd.concat([df, breakout_row])

    for i in range(4):
        filler_low = base_high * (0.9 if filler_dip and i == 0 else 1.005)
        filler_row = pd.DataFrame(
            {"open": [base_high * 1.025], "high": [base_high * 1.030], "low": [filler_low], "close": [base_high * 1.025], "volume": [100_000.0]},
            index=[df.index[-1] + pd.Timedelta(days=1)],
        )
        df = pd.concat([df, filler_row])

    retest_open = base_high * 1.010
    retest_close = base_high * (1.024 if retest_bullish else 1.005)
    retest_row = pd.DataFrame(
        {
            "open": [retest_open],
            "high": [base_high * 1.024],
            "low": [base_high * retest_low_mult],
            "close": [retest_close],
            "volume": [retest_volume],
        },
        index=[df.index[-1] + pd.Timedelta(days=1)],
    )
    return pd.concat([df, retest_row])


class TestDailySwing:
    def test_detects_accumulation_spring(self):
        base_low, base_high = 195.0, 205.0
        df = _accumulation_spring_df(base_low=base_low, base_high=base_high)
        signals = daily_swing.scan({"TESTCO": df})
        assert len(signals) == 1
        sig = signals[0]
        assert sig.stop_loss < sig.entry
        assert sig.targets == []  # no fixed target - exit is a trailing stop (see backtest.simulate_trailing)

        # Entry is the retest candle's high; stop-loss is the lower of the
        # retest candle's own low and the previous candle's low.
        row, prev_row = df.iloc[-1], df.iloc[-2]
        raw_entry = float(row["high"])
        raw_stop = float(min(row["low"], prev_row["low"]))
        assert sig.entry == round(raw_entry, 2)
        assert sig.stop_loss == round(raw_stop, 2)

    def test_no_signal_when_base_too_wide(self):
        # (205 - 140) / 140 = 46% range - not a genuine tight accumulation.
        df = _accumulation_spring_df(base_low=140.0, base_high=205.0)
        signals = daily_swing.scan({"TESTCO": df})
        assert signals == []

    def test_no_signal_without_volume_dryup(self):
        # second half (90,000) isn't meaningfully below first half (100,000)
        df = _accumulation_spring_df(first_half_volume=100_000.0, second_half_volume=90_000.0)
        signals = daily_swing.scan({"TESTCO": df})
        assert signals == []

    def test_no_signal_when_breakout_volume_is_weak(self):
        # base's own average volume is 115,000; 150,000 isn't the
        # multi-x surge a real breakout needs.
        df = _accumulation_spring_df(breakout_volume=150_000.0)
        signals = daily_swing.scan({"TESTCO": df})
        assert signals == []

    def test_no_signal_when_breakout_fails_and_reenters_base(self):
        # A filler day dips back below the base low - a failed breakout,
        # not a real accumulation spring.
        df = _accumulation_spring_df(filler_dip=True)
        signals = daily_swing.scan({"TESTCO": df})
        assert signals == []

    def test_no_signal_when_retest_is_too_far_from_base_high(self):
        df = _accumulation_spring_df(retest_low_mult=1.05)  # never comes back down to retest support
        signals = daily_swing.scan({"TESTCO": df})
        assert signals == []

    def test_no_signal_when_retest_candle_is_red(self):
        df = _accumulation_spring_df(retest_bullish=False)
        signals = daily_swing.scan({"TESTCO": df})
        assert signals == []

    def test_no_signal_when_retest_volume_is_heavy(self):
        # base's own average volume is 115,000 - a retest at or above that
        # shows real supply, not the absence of sellers a spring needs.
        df = _accumulation_spring_df(retest_volume=120_000.0)
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
