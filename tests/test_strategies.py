import pandas as pd

from signals import config
from signals.strategies import cip_daily, cip_weekly, monthly_breakout, weekly_breakout


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

        # Stop-loss is anchored to the bottom of the consolidation range.
        assert sig.stop_loss == round(198.0 * (1 - config.SL_BUFFER), 2)

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
    """Ramp up towards `resistance`, hold flat there for `touch_lookback`
    periods (repeated resistance touches), break out above it on high
    volume, drift for a few periods, then close with a bullish candle that
    dips back to the old resistance and holds it as new support (the
    "change in polarity")."""
    df = _ramp_then_flat_df(freq, ramp_weeks=ramp_periods, flat_weeks=touch_lookback, start=50, plateau=resistance)

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
    """Spread the resistance "touch" block's levels >5% apart so no two
    highs ever cluster within CIP_ZONE_TOLERANCE of each other - i.e. no
    genuine, repeatedly-tested resistance zone ever forms."""
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

    def test_no_signal_without_repeated_touches(self):
        df = _cip_setup_df("W-FRI", pd.Timedelta(weeks=1), config.CIP_WEEKLY_TOUCH_LOOKBACK)
        df = _scramble_touch_block(df, config.CIP_WEEKLY_TOUCH_LOOKBACK)
        signals = cip_weekly.scan({"TESTCO": df})
        assert signals == []


class TestCipDaily:
    def test_detects_polarity_flip(self):
        df = _cip_setup_df("B", pd.Timedelta(days=1), config.CIP_DAILY_TOUCH_LOOKBACK)
        signals = cip_daily.scan({"TESTCO": df})
        assert len(signals) == 1
        sig = signals[0]
        assert sig.stop_loss < sig.entry < sig.targets[0] < sig.targets[1]

        row, prev_row = df.iloc[-1], df.iloc[-2]
        assert sig.entry == round(float(row["high"]), 2)
        assert sig.stop_loss == round(float(min(row["low"], prev_row["low"])), 2)

    def test_no_signal_without_repeated_touches(self):
        df = _cip_setup_df("B", pd.Timedelta(days=1), config.CIP_DAILY_TOUCH_LOOKBACK)
        df = _scramble_touch_block(df, config.CIP_DAILY_TOUCH_LOOKBACK)
        signals = cip_daily.scan({"TESTCO": df})
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
