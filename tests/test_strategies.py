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


def _sma_support_setup_df(
    ramp_periods: int = 500,
    plateau: float = 300.0,
    low_mult: float = 0.995,
    close_mult: float = 1.01,
    high_mult: float = 1.012,
    volume: float = 70_000.0,
) -> pd.DataFrame:
    """Ramp up towards `plateau` (never reaching it, so it stays this
    stock's all-time-high close, and keeping the 30-SMA rising throughout),
    then append a single green, quiet-volume candle anchored to the
    trailing 30-day SMA - a textbook support test. Base rate volume is
    100,000/day, so the default `volume` (70,000) is a genuine pullback in
    turnover relative to the trailing average."""
    df = _ramp_then_flat_df("B", ramp_weeks=ramp_periods, flat_weeks=0, start=50, plateau=plateau)
    anchor = df["close"].tail(config.DAILY_SWING_SMA_SUPPORT - 1).mean()
    support_row = pd.DataFrame(
        {
            "open": [anchor],
            "high": [anchor * high_mult],
            "low": [anchor * low_mult],
            "close": [anchor * close_mult],
            "volume": [volume],
        },
        index=[df.index[-1] + pd.Timedelta(days=1)],
    )
    return pd.concat([df, support_row])


class TestDailySwing:
    def test_detects_sma_support_in_ath_stock(self):
        df = _sma_support_setup_df()
        signals = daily_swing.scan({"TESTCO": df})
        assert len(signals) == 1
        sig = signals[0]
        assert sig.stop_loss < sig.entry < sig.targets[0]
        assert len(sig.targets) == 1  # fixed single 1:3 target

        # Entry is the signal candle's high; stop-loss is the lower of the
        # signal candle's own low and the previous candle's low. The
        # target is computed from the raw (unrounded) entry/stop, same as
        # the strategy itself, to avoid a rounding-order mismatch.
        row, prev_row = df.iloc[-1], df.iloc[-2]
        raw_entry = float(row["high"])
        raw_stop = float(min(row["low"], prev_row["low"]))
        assert sig.entry == round(raw_entry, 2)
        assert sig.stop_loss == round(raw_stop, 2)
        assert sig.targets[0] == round(raw_entry + (raw_entry - raw_stop) * 3, 2)

    def test_no_signal_when_far_from_all_time_high(self):
        df = _sma_support_setup_df()
        # An early spike far above the eventual plateau (500 vs ~300),
        # placed well outside the 30-day SMA window - the stock's real
        # all-time high sits far above its current price, so it no longer
        # counts as an "all-time-high stock" even though today's candle is
        # a textbook SMA support test.
        spike_idx = df.index[100]
        df.loc[spike_idx, ["open", "high", "low", "close"]] = [500.0, 505.0, 495.0, 500.0]
        signals = daily_swing.scan({"TESTCO": df})
        assert signals == []

    def test_no_signal_when_not_touching_the_sma(self):
        df = _sma_support_setup_df(low_mult=1.05, close_mult=1.06, high_mult=1.07)  # low sits well above the SMA
        signals = daily_swing.scan({"TESTCO": df})
        assert signals == []

    def test_no_signal_when_undershoot_is_violent(self):
        df = _sma_support_setup_df(low_mult=0.90)  # low crashes 10% below the SMA - a whipsaw, not controlled support
        signals = daily_swing.scan({"TESTCO": df})
        assert signals == []

    def test_no_signal_when_candle_is_red(self):
        df = _sma_support_setup_df(close_mult=0.99)  # closes below its own open
        signals = daily_swing.scan({"TESTCO": df})
        assert signals == []

    def test_no_signal_when_pullback_volume_is_heavy(self):
        # base rate is 100,000/day - 150,000 is a heavy-volume day, not the
        # quiet, light-selling pullback this setup requires.
        df = _sma_support_setup_df(volume=150_000.0)
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
