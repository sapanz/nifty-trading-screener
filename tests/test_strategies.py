import pandas as pd

from signals import config
from signals.strategies import daily_swing, monthly_breakout, weekly_breakout, weekly_sma_support


def _ramp_then_flat_df(freq: str, ramp_weeks: int, flat_weeks: int, start: float, plateau: float) -> pd.DataFrame:
    """Rising trend for `ramp_weeks` periods up to `plateau`, then flat for `flat_weeks`."""
    n = ramp_weeks + flat_weeks
    dates = pd.date_range(end=pd.Timestamp.today().normalize(), periods=n, freq=freq)
    ramp = [start + (plateau - start) * i / ramp_weeks for i in range(ramp_weeks)]
    flat = [plateau] * flat_weeks
    closes = ramp + flat
    df = pd.DataFrame({"close": closes}, index=dates)
    df["open"] = df["close"]
    df["high"] = df["close"] * 1.005
    df["low"] = df["close"] * 0.995
    df["volume"] = 100_000.0
    return df


class TestWeeklySmaSupport:
    def test_detects_support_bounce_with_volume(self):
        df = _ramp_then_flat_df("W-FRI", ramp_weeks=180, flat_weeks=29, start=50, plateau=200)
        # append the support-test candle: dips to plateau, closes higher, big volume
        support_row = pd.DataFrame(
            {"open": [200.0], "high": [205.0], "low": [198.0], "close": [204.0], "volume": [300_000.0]},
            index=[df.index[-1] + pd.Timedelta(weeks=1)],
        )
        df = pd.concat([df, support_row])

        signals = weekly_sma_support.scan({"TESTCO": df})
        assert len(signals) == 1
        sig = signals[0]
        assert sig.symbol == "TESTCO"
        assert sig.stop_loss < sig.entry < sig.targets[0] < sig.targets[1]

    def test_no_signal_without_volume_spike(self):
        df = _ramp_then_flat_df("W-FRI", ramp_weeks=180, flat_weeks=30, start=50, plateau=200)
        signals = weekly_sma_support.scan({"TESTCO": df})
        assert signals == []


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


class TestDailySwing:
    def test_detects_confluence_support(self):
        df = _ramp_then_flat_df("B", ramp_weeks=230, flat_weeks=44, start=50, plateau=200)
        support_row = pd.DataFrame(
            {"open": [200.0], "high": [204.0], "low": [197.0], "close": [203.0], "volume": [300_000.0]},
            index=[df.index[-1] + pd.Timedelta(days=1)],
        )
        df = pd.concat([df, support_row])

        signals = daily_swing.scan({"TESTCO": df})
        assert len(signals) == 1
        sig = signals[0]
        assert sig.stop_loss < sig.entry < sig.targets[0] < sig.targets[1]

    def test_no_signal_below_long_term_trend(self):
        # downtrend -> close is below its own 200 SMA, should never qualify
        df = _ramp_then_flat_df("B", ramp_weeks=230, flat_weeks=44, start=200, plateau=50)
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
