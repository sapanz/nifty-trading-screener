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
    """Ramp up towards `resistance` (which, since nothing in the ramp ever
    exceeds it, becomes this stock's all-time high), then alternate
    peak/pullback candles at that level for `touch_lookback` periods - each
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

    def test_no_signal_when_local_high_isnt_the_all_time_high(self):
        df = _cip_setup_df("W-FRI", pd.Timedelta(weeks=1), config.CIP_WEEKLY_TOUCH_LOOKBACK)
        # An early spike well above the local "resistance" zone (300 vs the
        # ~209 breakout close) - the local zone is no longer this stock's
        # real all-time high, so breaking it shouldn't count as a CIP setup.
        spike_idx = df.index[50]
        df.loc[spike_idx, ["open", "high", "low", "close"]] = [300.0, 301.5, 298.5, 300.0]
        signals = cip_weekly.scan({"TESTCO": df})
        assert signals == []


def _darvas_setup_df(
    ramp_periods: int = 500, box_days: int = 3, box_top: float = 201.0, box_bottom: float = 197.0, breakout_close: float = 210.0
) -> pd.DataFrame:
    """Ramp up towards 200 (never quite reaching it, so it stays this
    stock's 52-week high), hold a tight box for `box_days`, then break out
    above the box top on strong volume."""
    df = _ramp_then_flat_df("B", ramp_weeks=ramp_periods, flat_weeks=0, start=50, plateau=200)

    for _ in range(box_days):
        box_row = pd.DataFrame(
            {"open": [199.0], "high": [box_top], "low": [box_bottom], "close": [199.0], "volume": [100_000.0]},
            index=[df.index[-1] + pd.Timedelta(days=1)],
        )
        df = pd.concat([df, box_row])

    breakout_row = pd.DataFrame(
        {"open": [box_top], "high": [breakout_close * 1.01], "low": [box_top], "close": [breakout_close], "volume": [500_000.0]},
        index=[df.index[-1] + pd.Timedelta(days=1)],
    )
    df = pd.concat([df, breakout_row])
    return df


class TestDailySwing:
    def _universe(self, **testco_kwargs) -> dict[str, pd.DataFrame]:
        """TESTCO (valid Darvas box + breakout, strongest recent performer)
        plus three peers: PEER_SLOW (a slower uptrend, still above its own
        200 SMA) and two PEER_WEAK symbols in a long decline (below their
        own 200 SMA) - together giving a 4-symbol universe with market
        breadth at exactly the 50% pass threshold and TESTCO as the clear
        relative-strength leader."""
        return {
            "TESTCO": _darvas_setup_df(**testco_kwargs),
            "PEER_SLOW": _ramp_then_flat_df("B", ramp_weeks=500, flat_weeks=0, start=50, plateau=100),
            "PEER_WEAK1": _ramp_then_flat_df("B", ramp_weeks=500, flat_weeks=0, start=200, plateau=50),
            "PEER_WEAK2": _ramp_then_flat_df("B", ramp_weeks=500, flat_weeks=0, start=200, plateau=50),
        }

    def test_detects_darvas_breakout_for_the_rs_leader(self):
        signals = daily_swing.scan(self._universe())
        assert len(signals) == 1
        sig = signals[0]
        assert sig.symbol == "TESTCO"
        assert sig.stop_loss < sig.entry < sig.targets[0] < sig.targets[1]

        # Entry is the breakout candle's close; stop-loss sits just below
        # the box bottom.
        assert sig.entry == 210.0
        assert sig.stop_loss == round(197.0 * (1 - config.SL_BUFFER), 2)

    def test_no_signal_when_market_breadth_weak(self):
        universe = self._universe()
        # Flip PEER_SLOW into a decline too - only TESTCO is left above its
        # own 200 SMA (1 of 4 = 25%, below the 50% breadth threshold).
        universe["PEER_SLOW"] = _ramp_then_flat_df("B", ramp_weeks=500, flat_weeks=0, start=200, plateau=50)
        signals = daily_swing.scan(universe)
        assert signals == []

    def test_no_signal_when_not_the_rs_leader(self):
        universe = self._universe()
        # A stronger peer with an even bigger breakout outranks TESTCO,
        # pushing it out of the top 30% RS cutoff (which is just the single
        # top symbol in a 5-name universe) - PEER_STRONGER still qualifies
        # on its own merits, TESTCO no longer does.
        universe["PEER_STRONGER"] = _darvas_setup_df(breakout_close=260.0)
        signals = daily_swing.scan(universe)
        assert [s.symbol for s in signals] == ["PEER_STRONGER"]

    def test_no_signal_when_box_too_wide(self):
        universe = self._universe(box_top=230.0, box_bottom=180.0, breakout_close=235.0)
        signals = daily_swing.scan(universe)
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
