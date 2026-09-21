import pandas as pd
import pytest

from signals import config, data
from signals.strategies import daily_swing, monthly_breakout, price_action_breakout, weekly_breakout


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
    # Range candles: range_low=180, range_high=200 (10% wide, within the
    # 20% tightness cap). A valid breakout candle sits BREAKOUT_MIN/MAX_
    # EXTENSION above 200 - 216.0 (8% extension) is used throughout as the
    # default "properly extended" breakout close.
    VALID_BREAKOUT_ROW = {"open": [210.0], "high": [216.5], "low": [209.0], "close": [216.0], "volume": [400_000.0]}

    def _tight_accumulation_range(self, df: pd.DataFrame) -> pd.DataFrame:
        """Overwrite the trailing BREAKOUT_RANGE_WEEKS rows into a tight,
        green-candle-dominated range (open < close throughout) so the
        accumulation check (more volume on up candles than down ones)
        passes trivially."""
        for i in range(1, config.BREAKOUT_RANGE_WEEKS + 1):
            idx = -i
            df.iloc[idx, df.columns.get_loc("open")] = 182.0
            df.iloc[idx, df.columns.get_loc("high")] = 200.0
            df.iloc[idx, df.columns.get_loc("low")] = 180.0
            df.iloc[idx, df.columns.get_loc("close")] = 198.0
        return df

    def test_detects_range_breakout_with_volume(self):
        df = _ramp_then_flat_df("W-FRI", ramp_weeks=200, flat_weeks=config.BREAKOUT_RANGE_WEEKS, start=50, plateau=200)
        df = self._tight_accumulation_range(df)

        breakout_row = pd.DataFrame(self.VALID_BREAKOUT_ROW, index=[df.index[-1] + pd.Timedelta(weeks=1)])
        df = pd.concat([df, breakout_row])

        signals = weekly_breakout.scan({"TESTCO": df})
        assert len(signals) == 1
        sig = signals[0]
        assert sig.stop_loss < sig.entry < sig.targets[0] < sig.targets[1]

        # Entry is a resting buy-stop at the breakout candle's own high, not
        # an immediate fill at its close.
        assert sig.entry == round(216.5, 2)
        # Stop-loss sits at the midpoint of the consolidation range
        # (180-200), not anchored to the breakout level itself.
        assert sig.stop_loss == round((200.0 + 180.0) / 2, 2)
        assert sig.candle_date == df.index[-1].date()
        # Diagnostic-only fields (not gated on) that ride along so a
        # backtest's CSV can be mined for what separates good and bad
        # signals - see backtest.DIAGNOSTIC_KEYS.
        assert sig.extra["extension_pct"] == pytest.approx((216.0 - 200.0) / 200.0 * 100, abs=0.01)
        assert sig.extra["tightness_pct"] == pytest.approx((200.0 - 180.0) / 180.0 * 100, abs=0.01)
        assert "dist_from_sma200_pct" in sig.extra
        assert 0 <= sig.extra["rsi14"] <= 100

    def test_no_signal_when_breakout_extension_too_small(self):
        # closes barely above the range (well under BREAKOUT_MIN_EXTENSION)
        # - a weak, low-conviction break, not the decisive move the
        # strategy requires.
        df = _ramp_then_flat_df("W-FRI", ramp_weeks=200, flat_weeks=config.BREAKOUT_RANGE_WEEKS, start=50, plateau=200)
        df = self._tight_accumulation_range(df)

        breakout_row = pd.DataFrame(
            {"open": [199.0], "high": [201.5], "low": [198.0], "close": [201.0], "volume": [400_000.0]},
            index=[df.index[-1] + pd.Timedelta(weeks=1)],
        )
        df = pd.concat([df, breakout_row])
        signals = weekly_breakout.scan({"TESTCO": df})
        assert signals == []

    def test_no_signal_when_breakout_extension_too_large(self):
        # closes far beyond BREAKOUT_MAX_EXTENSION - already extended,
        # exactly the scenario where a fixed measured-move target can end
        # up sitting behind the entry price before the trade even starts.
        df = _ramp_then_flat_df("W-FRI", ramp_weeks=200, flat_weeks=config.BREAKOUT_RANGE_WEEKS, start=50, plateau=200)
        df = self._tight_accumulation_range(df)

        breakout_row = pd.DataFrame(
            {"open": [235.0], "high": [242.0], "low": [233.0], "close": [240.0], "volume": [400_000.0]},
            index=[df.index[-1] + pd.Timedelta(weeks=1)],
        )
        df = pd.concat([df, breakout_row])
        signals = weekly_breakout.scan({"TESTCO": df})
        assert signals == []

    def test_no_signal_when_breakout_candle_is_red(self):
        # a properly extended breakout (within the valid band) that closes
        # near its own high (small upper wick, would pass is_proper_close)
        # but still closed below its own open - a red candle, not the
        # bullish breakout the strategy requires.
        df = _ramp_then_flat_df("W-FRI", ramp_weeks=200, flat_weeks=config.BREAKOUT_RANGE_WEEKS, start=50, plateau=200)
        df = self._tight_accumulation_range(df)

        breakout_row = pd.DataFrame(
            {"open": [216.5], "high": [216.6], "low": [209.0], "close": [216.0], "volume": [400_000.0]},
            index=[df.index[-1] + pd.Timedelta(weeks=1)],
        )
        df = pd.concat([df, breakout_row])

        signals = weekly_breakout.scan({"TESTCO": df})
        assert signals == []

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

    def test_signal_fires_with_flat_200sma_but_rising_30sma(self):
        # a long flat base (dominates the 200 SMA, keeping it essentially
        # flat) followed by a rising run into the range - price is above a
        # flat 200 SMA (fine, no slope required there) and the faster 30
        # SMA is genuinely rising, so the signal should still fire.
        n_flat = config.SMA_LONG
        ramp_weeks = 24
        flat_part = [180.0] * n_flat
        ramp_part = [180.0 + (199.0 - 180.0) * i / ramp_weeks for i in range(ramp_weeks)]
        dates = pd.date_range(
            end=pd.Timestamp.today().normalize(),
            periods=n_flat + ramp_weeks + config.BREAKOUT_RANGE_WEEKS,
            freq="W-FRI",
        )
        df = pd.DataFrame({"close": flat_part + ramp_part + [200.0] * config.BREAKOUT_RANGE_WEEKS}, index=dates)
        df["open"] = df["close"]
        df["high"] = df["close"] * 1.005
        df["low"] = df["close"] * 0.995
        df["volume"] = 100_000.0
        df = self._tight_accumulation_range(df)
        breakout_row = pd.DataFrame(self.VALID_BREAKOUT_ROW, index=[df.index[-1] + pd.Timedelta(weeks=1)])
        df = pd.concat([df, breakout_row])
        signals = weekly_breakout.scan({"TESTCO": df})
        assert len(signals) == 1

    def test_no_signal_when_30sma_not_rising(self):
        # price sits above a flat 200 SMA, but there was no recent run-up -
        # the faster 30 SMA is flat too, not a real uptrend.
        margin = config.SMA_SLOPE_LOOKBACK + 20
        n_bulk = config.SMA_LONG + config.BREAKOUT_RANGE_WEEKS + margin
        dates = pd.date_range(end=pd.Timestamp.today().normalize(), periods=n_bulk, freq="W-FRI")
        df = pd.DataFrame({"close": [210.0] * n_bulk}, index=dates)
        df["open"] = df["close"]
        df["high"] = df["close"] * 1.005
        df["low"] = df["close"] * 0.995
        df["volume"] = 100_000.0
        df = self._tight_accumulation_range(df)
        breakout_row = pd.DataFrame(self.VALID_BREAKOUT_ROW, index=[df.index[-1] + pd.Timedelta(weeks=1)])
        df = pd.concat([df, breakout_row])
        signals = weekly_breakout.scan({"TESTCO": df})
        assert signals == []

    def test_no_signal_when_range_is_distribution_not_accumulation(self):
        # same tight range as the passing test, but every range candle is
        # red (open > close) with real volume behind it - sellers, not
        # buyers, were in control while the range built.
        df = _ramp_then_flat_df("W-FRI", ramp_weeks=200, flat_weeks=config.BREAKOUT_RANGE_WEEKS, start=50, plateau=200)
        for i in range(1, config.BREAKOUT_RANGE_WEEKS + 1):
            idx = -i
            df.iloc[idx, df.columns.get_loc("open")] = 201.0
            df.iloc[idx, df.columns.get_loc("high")] = 202.0
            df.iloc[idx, df.columns.get_loc("low")] = 198.0
            df.iloc[idx, df.columns.get_loc("close")] = 199.0
            df.iloc[idx, df.columns.get_loc("volume")] = 150_000.0
        # range_high here is 202 (not the shared helper's 200), so the
        # breakout close needs its own valid-extension value: ~8% above 202.
        breakout_row = pd.DataFrame(
            {"open": [212.0], "high": [218.6], "low": [211.0], "close": [218.0], "volume": [400_000.0]},
            index=[df.index[-1] + pd.Timedelta(weeks=1)],
        )
        df = pd.concat([df, breakout_row])
        signals = weekly_breakout.scan({"TESTCO": df})
        assert signals == []


def _append_support_row(df: pd.DataFrame, sma_period: int, freq_offset, low_mult=0.995, close_mult=1.008, high_mult=1.01) -> pd.DataFrame:
    """Append a support-test candle anchored to the trailing SMA rather than
    the last close - with a drifting series the two diverge, and a support
    test is defined relative to the SMA, not the most recent price."""
    anchor = df["close"].tail(sma_period - 1).mean()
    support_row = pd.DataFrame(
        {
            "open": [anchor],
            "high": [anchor * high_mult],
            "low": [anchor * low_mult],
            "close": [anchor * close_mult],
            "volume": [100_000.0],
        },
        index=[df.index[-1] + freq_offset],
    )
    return pd.concat([df, support_row])


class TestDailySwing:
    def _scan(self, daily_data: dict, weekly_data: dict | None = None):
        """Default weekly_data to a resample of daily_data itself (the same
        thing run_signals.py does live) so most tests don't need to think
        about the multi-timeframe filter at all - only the tests that
        target it directly pass an explicit weekly_data."""
        if weekly_data is None:
            weekly_data = data.to_weekly(daily_data)
        return daily_swing.scan(daily_data, weekly_data)

    def test_detects_confluence_support(self):
        # gentle continued drift (not dead-flat) so the 44 SMA is clearly
        # rising; small enough not to blow out the SMA44/lower-BB confluence
        df = _ramp_then_flat_df("B", ramp_weeks=230, flat_weeks=44, start=50, plateau=200, plateau_drift=0.05)
        df = _append_support_row(df, 44, pd.Timedelta(days=1))

        signals = self._scan({"TESTCO": df})
        assert len(signals) == 1
        sig = signals[0]
        assert sig.stop_loss < sig.entry < sig.targets[0] < sig.targets[1]

        # Entry is the signal candle's high; stop-loss is the lower of the
        # signal candle's own low and the previous candle's low.
        row, prev_row = df.iloc[-1], df.iloc[-2]
        assert sig.entry == round(float(row["high"]), 2)
        assert sig.stop_loss == round(float(min(row["low"], prev_row["low"])), 2)
        # candle_date is the actual date of the signal candle, not "today" -
        # scan() has no notion of "today" at all, only whatever the caller
        # handed it as the last row.
        assert sig.candle_date == row.name.date()
        # Diagnostic-only fields (not gated on) that ride along so a
        # backtest's CSV can be mined for what separates good and bad
        # signals - see backtest.DIAGNOSTIC_KEYS.
        assert "confluence_gap_pct" in sig.extra
        assert "dist_from_sma200_pct" in sig.extra
        assert "vol_ratio" in sig.extra
        assert 0 <= sig.extra["rsi14"] <= 100

    def test_no_signal_below_long_term_trend(self):
        # downtrend -> close is below its own 200 SMA, should never qualify
        df = _ramp_then_flat_df("B", ramp_weeks=230, flat_weeks=44, start=200, plateau=50)
        signals = self._scan({"TESTCO": df})
        assert signals == []

    def test_no_signal_when_sma44_declining(self):
        df = _ramp_then_flat_df("B", ramp_weeks=230, flat_weeks=44, start=50, plateau=200, plateau_drift=-0.5)
        df = _append_support_row(df, 44, pd.Timedelta(days=1))
        signals = self._scan({"TESTCO": df})
        assert signals == []

    def test_no_signal_when_weekly_sma30_not_rising(self):
        # Same daily fixture as test_detects_confluence_support (every daily
        # condition passes), but paired with a flat weekly series instead of
        # a resample of it - the multi-timeframe confirmation should reject
        # it even though the daily chart alone looks fine.
        df = _ramp_then_flat_df("B", ramp_weeks=230, flat_weeks=44, start=50, plateau=200, plateau_drift=0.05)
        df = _append_support_row(df, 44, pd.Timedelta(days=1))

        n_weekly = config.BREAKOUT_TREND_SMA + config.SMA_SLOPE_LOOKBACK + 20
        dates = pd.date_range(end=pd.Timestamp.today().normalize(), periods=n_weekly, freq="W-FRI")
        flat_weekly = pd.DataFrame({"close": [150.0] * n_weekly}, index=dates)
        flat_weekly["open"] = flat_weekly["close"]
        flat_weekly["high"] = flat_weekly["close"] * 1.005
        flat_weekly["low"] = flat_weekly["close"] * 0.995
        flat_weekly["volume"] = 100_000.0

        signals = daily_swing.scan({"TESTCO": df}, {"TESTCO": flat_weekly})
        assert signals == []

    def test_no_signal_when_weekly_data_missing(self):
        # Same otherwise-valid daily fixture, but the symbol has no entry in
        # weekly_data at all - fails closed rather than assuming confirmed.
        df = _ramp_then_flat_df("B", ramp_weeks=230, flat_weeks=44, start=50, plateau=200, plateau_drift=0.05)
        df = _append_support_row(df, 44, pd.Timedelta(days=1))
        signals = daily_swing.scan({"TESTCO": df}, {})
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
        assert sig.candle_date == df.index[-1].date()

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

    def test_no_signal_when_breakout_too_soon_after_ath(self):
        # the prior ATH is set just one month before the breakout - too
        # soon to be a genuine breakout out of a real base (noise, per
        # MONTHLY_MIN_GAP_MONTHS).
        df = self._monthly_df(months=20)
        old_ath = df["close"].iloc[:-2].max()
        new_ath = old_ath * 1.05
        df.iloc[-2, df.columns.get_loc("close")] = new_ath
        df.iloc[-2, df.columns.get_loc("high")] = new_ath * 1.01
        df.iloc[-1, df.columns.get_loc("close")] = new_ath * 1.10
        df.iloc[-1, df.columns.get_loc("high")] = new_ath * 1.11
        df.iloc[-1, df.columns.get_loc("volume")] = 200_000.0

        signals = monthly_breakout.scan({"TESTCO": df})
        assert signals == []

    def test_no_signal_when_breakout_candle_is_red(self):
        # closes at a fresh all-time high on a closing basis (110% of the
        # prior ATH), with a small upper wick (would pass is_proper_close),
        # but still closed below its own open - a gap-up-then-fade month,
        # not the bullish breakout the strategy requires.
        df = self._monthly_df()
        prior_ath = df["close"].iloc[:-1].max()
        df.iloc[-1, df.columns.get_loc("open")] = prior_ath * 1.13
        df.iloc[-1, df.columns.get_loc("high")] = prior_ath * 1.13
        df.iloc[-1, df.columns.get_loc("close")] = prior_ath * 1.10
        df.iloc[-1, df.columns.get_loc("low")] = prior_ath * 1.05
        df.iloc[-1, df.columns.get_loc("volume")] = 200_000.0

        signals = monthly_breakout.scan({"TESTCO": df})
        assert signals == []


class TestPriceActionBreakout:
    """Daily-timeframe fixtures throughout (config's *_DAILY constants) -
    scan() itself is timeframe-agnostic, so these exercise the same code
    path the weekly/monthly legs use with different window sizes."""

    def _next_business_day(self, d: pd.Timestamp) -> pd.Timestamp:
        d = d + pd.Timedelta(days=1)
        while d.weekday() >= 5:
            d += pd.Timedelta(days=1)
        return d

    def _base_breakout_retest_df(
        self,
        base_len: int = 20,
        base_low_start: float = 280.0,
        base_low_end: float = 305.0,
        base_high: float = 310.0,
        breakout_volume: float = 250_000.0,
        retest_low: float = 305.0,
        retest_close: float = 308.0,
        today_open: float = 309.0,
        today_high: float = 317.0,
        today_low: float = 307.0,
        today_close: float = 315.0,
    ) -> pd.DataFrame:
        """A long, flat-at-150 lead-in (so any lookback window reaching
        past the base's own start immediately fails tightness - the
        detected base length is exactly `base_len`, not accidentally
        longer) -> a `base_len`-candle ascending-triangle-shaped base
        (flat high, rising low, accumulation-biased) -> a breakout candle
        on 2.5x volume -> a retest candle pulling back near the base's
        high -> today's confirmation candle. Defaults produce a valid
        signal; each test overrides exactly the field it's checking."""
        n_ramp = config.SMA_LONG + config.PRICE_ACTION_PATTERN_MAX_LOOKBACK_DAILY
        dates = pd.date_range(end=pd.Timestamp.today().normalize() - pd.Timedelta(days=40), periods=n_ramp, freq="B")
        ramp = pd.DataFrame({"close": [150.0] * n_ramp}, index=dates)
        ramp["open"] = 150.0
        ramp["high"] = 151.0
        ramp["low"] = 149.0
        ramp["volume"] = 100_000.0

        base_dates = pd.bdate_range(start=dates[-1] + pd.Timedelta(days=1), periods=base_len)
        lows = [base_low_start + (base_low_end - base_low_start) * i / max(base_len - 1, 1) for i in range(base_len)]
        base = pd.DataFrame(
            {
                "open": [low + 1 for low in lows],
                "high": [base_high] * base_len,
                "low": lows,
                "close": [low + 2 for low in lows],  # green throughout (close > open) - accumulation-biased
                "volume": 100_000.0,
            },
            index=base_dates,
        )

        breakout_date = self._next_business_day(base_dates[-1])
        breakout = pd.DataFrame(
            {"open": [base_high - 2], "high": [base_high + 12], "low": [base_high - 3], "close": [base_high + 10], "volume": [breakout_volume]},
            index=[breakout_date],
        )

        retest_date = self._next_business_day(breakout_date)
        retest = pd.DataFrame(
            {"open": [base_high + 2], "high": [base_high + 4], "low": [retest_low], "close": [retest_close], "volume": [90_000.0]},
            index=[retest_date],
        )

        today_date = self._next_business_day(retest_date)
        today = pd.DataFrame(
            {"open": [today_open], "high": [today_high], "low": [today_low], "close": [today_close], "volume": [120_000.0]},
            index=[today_date],
        )

        return pd.concat([ramp, base, breakout, retest, today])

    def _scan(self, df: pd.DataFrame) -> list:
        return price_action_breakout.scan(
            {"TESTCO": df},
            pattern_min_lookback=config.PRICE_ACTION_PATTERN_MIN_LOOKBACK_DAILY,
            pattern_max_lookback=config.PRICE_ACTION_PATTERN_MAX_LOOKBACK_DAILY,
            breakout_window=config.PRICE_ACTION_BREAKOUT_WINDOW_DAILY,
            volume_lookback=config.PRICE_ACTION_VOLUME_LOOKBACK_DAILY,
        )

    def test_detects_base_breakout_retest_confirmation(self):
        df = self._base_breakout_retest_df()
        signals = self._scan(df)
        assert len(signals) == 1
        sig = signals[0]

        # Entry is a resting buy-stop at today's (the confirmation candle's)
        # own high, not the breakout candle's.
        assert sig.entry == 317.0
        # Stop-loss is the lower of the retest low and today's own low.
        assert sig.stop_loss == 305.0
        assert sig.stop_loss < sig.entry < sig.targets[0] < sig.targets[1]
        assert sig.candle_date == df.index[-1].date()

        # The base's detected length matches what the fixture actually
        # built (20), not some other window the lead-in accidentally
        # also satisfied.
        assert sig.extra["base_candles"] == 20
        assert sig.extra["breakout_type"] == "Ascending Triangle"  # flat high, rising low
        assert "base_start" in sig.extra and "base_end" in sig.extra
        assert sig.extra["tightness_pct"] == pytest.approx((310.0 - 280.0) / 280.0 * 100, abs=0.1)
        # sort_key is the base's own range %, per explicit request to sort
        # "higher range first" - not volume or recency.
        assert sig.sort_key == pytest.approx(sig.extra["tightness_pct"], abs=0.01)
        assert "Ascending Triangle" in sig.note
        assert "Base 20 candles" in sig.note

    def test_classifies_range_shape(self):
        # Flat high AND flat low (base_low_start == base_low_end) - a
        # rectangle, not a triangle.
        df = self._base_breakout_retest_df(base_low_start=300.0, base_low_end=300.0, base_high=310.0)
        signals = self._scan(df)
        assert len(signals) == 1
        assert signals[0].extra["breakout_type"] == "Range"

    def test_base_length_varies_with_the_fixture(self):
        # A shorter base (12 candles instead of 20) should be detected as
        # exactly 12, not the fixed value the old implementation always
        # reported - proving the length is genuinely detected, not a
        # constant.
        df = self._base_breakout_retest_df(base_len=12)
        signals = self._scan(df)
        assert len(signals) == 1
        assert signals[0].extra["base_candles"] == 12

    def test_no_signal_when_breakout_volume_not_elevated(self):
        # 1.3x average - clears the other strategies' volume bar but not
        # this one's explicitly higher "high volumes" requirement (2.0x).
        df = self._base_breakout_retest_df(breakout_volume=130_000.0)
        assert self._scan(df) == []

    def test_no_signal_when_retest_invalidates_the_level(self):
        # Retest candle closes well below the base - the broken-out level
        # failed as support rather than being genuinely retested.
        df = self._base_breakout_retest_df(retest_close=280.0)
        assert self._scan(df) == []

    def test_no_signal_when_today_is_not_bullish(self):
        df = self._base_breakout_retest_df(today_close=308.0)  # closes below today's own open
        assert self._scan(df) == []

    def test_no_signal_when_today_has_a_large_upper_wick(self):
        df = self._base_breakout_retest_df(today_high=330.0, today_close=310.0)
        assert self._scan(df) == []

    def test_no_signal_when_today_does_not_reclaim_the_breakout_level(self):
        # Green and properly closed, but never closes back above the
        # base's high - still below the level being retested, not a
        # genuine confirmation of the breakout resuming.
        df = self._base_breakout_retest_df(today_open=307.0, today_high=309.5, today_close=309.0)
        assert self._scan(df) == []

    def test_no_signal_without_a_prior_retest(self):
        # Today immediately follows the breakout candle with no in-between
        # candle at all - nothing has actually pulled back to test the
        # level yet, so there's nothing to confirm.
        df = self._base_breakout_retest_df()
        retest_date = df.index[-2]
        df = df.drop(index=retest_date)
        assert self._scan(df) == []

    def test_no_signal_when_risk_exceeds_max_risk_pct(self):
        # A retest that wicks much further below the breakout level than
        # normal still passes the retest/invalidation checks (its close
        # stays near the level; only its low goes deep) but implies far
        # more than PRICE_ACTION_MAX_RISK_PCT (5%) of risk from entry to
        # stop-loss - a messy retest, not a tight setup, so it's skipped
        # rather than taken with an oversized stop.
        df = self._base_breakout_retest_df(retest_low=290.0)
        assert self._scan(df) == []

    def test_no_signal_when_base_is_not_tight_enough(self):
        # Base low starts far below the high - well past
        # PRICE_ACTION_RANGE_TIGHTNESS (20%) even at the shortest window,
        # so no length between MIN and MAX ever qualifies as a real base.
        df = self._base_breakout_retest_df(base_low_start=150.0, base_low_end=305.0, base_high=310.0)
        assert self._scan(df) == []

    def _short_base_breakdown_retest_df(
        self,
        base_len: int = 20,
        base_high_start: float = 320.0,
        base_high_end: float = 295.0,
        base_low: float = 290.0,
        breakdown_volume: float = 250_000.0,
        retest_high: float = 295.0,
        retest_close: float = 292.0,
        today_open: float = 291.0,
        today_high: float = 293.0,
        today_low: float = 284.0,
        today_close: float = 285.0,
    ) -> pd.DataFrame:
        """Exact mirror of _base_breakout_retest_df: a long, flat-at-400
        lead-in (high, so a trailing SMA dominated by it sits well above
        the base/breakdown/today prices below - is_below_sma) -> a
        `base_len`-candle descending-triangle-shaped base (flat low, falling
        high, distribution-biased - the mirror of the long fixture's
        ascending triangle) -> a breakdown candle on 2.5x volume -> a
        retest candle rallying back near the base's low -> today's bearish
        confirmation candle. Defaults produce a valid short signal."""
        n_ramp = config.SMA_LONG + config.PRICE_ACTION_PATTERN_MAX_LOOKBACK_DAILY
        dates = pd.date_range(end=pd.Timestamp.today().normalize() - pd.Timedelta(days=40), periods=n_ramp, freq="B")
        ramp = pd.DataFrame({"close": [400.0] * n_ramp}, index=dates)
        ramp["open"] = 400.0
        ramp["high"] = 401.0
        ramp["low"] = 399.0
        ramp["volume"] = 100_000.0

        base_dates = pd.bdate_range(start=dates[-1] + pd.Timedelta(days=1), periods=base_len)
        highs = [base_high_start + (base_high_end - base_high_start) * i / max(base_len - 1, 1) for i in range(base_len)]
        base = pd.DataFrame(
            {
                "open": [high - 1 for high in highs],
                "high": highs,
                "low": [base_low] * base_len,
                "close": [high - 2 for high in highs],  # red throughout (close < open) - distribution-biased
                "volume": 100_000.0,
            },
            index=base_dates,
        )

        breakdown_date = self._next_business_day(base_dates[-1])
        breakdown = pd.DataFrame(
            {"open": [base_low + 2], "high": [base_low + 3], "low": [base_low - 12], "close": [base_low - 10], "volume": [breakdown_volume]},
            index=[breakdown_date],
        )

        retest_date = self._next_business_day(breakdown_date)
        retest = pd.DataFrame(
            {"open": [base_low - 2], "high": [retest_high], "low": [base_low - 4], "close": [retest_close], "volume": [90_000.0]},
            index=[retest_date],
        )

        today_date = self._next_business_day(retest_date)
        today = pd.DataFrame(
            {"open": [today_open], "high": [today_high], "low": [today_low], "close": [today_close], "volume": [120_000.0]},
            index=[today_date],
        )

        return pd.concat([ramp, base, breakdown, retest, today])

    def _scan_short(self, df: pd.DataFrame, short_eligible=frozenset({"TESTCO"})) -> list:
        return price_action_breakout.scan(
            {"TESTCO": df},
            pattern_min_lookback=config.PRICE_ACTION_PATTERN_MIN_LOOKBACK_DAILY,
            pattern_max_lookback=config.PRICE_ACTION_PATTERN_MAX_LOOKBACK_DAILY,
            breakout_window=config.PRICE_ACTION_BREAKOUT_WINDOW_DAILY,
            volume_lookback=config.PRICE_ACTION_VOLUME_LOOKBACK_DAILY,
            short_eligible=short_eligible,
        )

    def test_detects_short_base_breakdown_retest_confirmation(self):
        df = self._short_base_breakdown_retest_df()
        signals = self._scan_short(df)
        assert len(signals) == 1
        sig = signals[0]

        assert sig.direction == "short"
        # Entry is a resting sell-stop at today's (the confirmation
        # candle's) own low, not the breakdown candle's.
        assert sig.entry == 284.0
        # Stop-loss is the higher of the retest high and today's own high.
        assert sig.stop_loss == 295.0
        assert sig.stop_loss > sig.entry > sig.targets[0] > sig.targets[1]
        assert sig.extra["base_candles"] == 20
        assert sig.extra["breakout_type"] == "Descending Triangle"  # falling high, flat low

    def test_no_short_signal_when_symbol_is_not_fo_eligible(self):
        # Same otherwise-valid setup, but the symbol isn't in short_eligible -
        # the long trigger doesn't fire either (today is bearish, not
        # bullish), so nothing should come back at all.
        df = self._short_base_breakdown_retest_df()
        assert self._scan_short(df, short_eligible=frozenset()) == []
        assert self._scan_short(df, short_eligible=None) == []

    def test_no_short_signal_when_breakdown_volume_not_elevated(self):
        df = self._short_base_breakdown_retest_df(breakdown_volume=130_000.0)
        assert self._scan_short(df) == []

    def test_no_short_signal_when_retest_invalidates_the_level(self):
        # Retest candle closes well above the base - the broken-down level
        # failed as resistance (reclaimed) rather than being genuinely retested.
        df = self._short_base_breakdown_retest_df(retest_close=320.0)
        assert self._scan_short(df) == []

    def test_no_short_signal_when_today_does_not_rebreak_the_breakdown_level(self):
        # Red and properly closed, but never closes back below the base's
        # low - still above the level being retested, not a genuine
        # confirmation of the breakdown resuming.
        df = self._short_base_breakdown_retest_df(today_open=293.0, today_high=293.5, today_low=290.5, today_close=291.0)
        assert self._scan_short(df) == []

    def test_no_short_signal_when_risk_exceeds_max_risk_pct(self):
        # Mirror of the long-side risk-cap test: a retest that rallies much
        # further above the breakdown level than normal still passes the
        # retest/invalidation checks (close stays near the level, only the
        # high goes far) but implies far more than PRICE_ACTION_MAX_RISK_PCT
        # (5%) of risk from entry to stop-loss.
        df = self._short_base_breakdown_retest_df(retest_high=330.0)
        assert self._scan_short(df) == []


class TestClassifyShape:
    """Direct tests of the slope-based shape heuristic, independent of the
    full scan() pipeline."""

    def _pattern(self, n, high_start, high_end, low_start, low_end):
        x = list(range(n))
        highs = [high_start + (high_end - high_start) * i / max(n - 1, 1) for i in x]
        lows = [low_start + (low_end - low_start) * i / max(n - 1, 1) for i in x]
        closes = [(h + low) / 2 for h, low in zip(highs, lows)]
        return pd.DataFrame({"high": highs, "low": lows, "close": closes})

    def test_range(self):
        assert price_action_breakout._classify_shape(self._pattern(20, 100, 100, 90, 90)) == "Range"

    def test_ascending_triangle(self):
        assert price_action_breakout._classify_shape(self._pattern(20, 100, 100, 80, 95)) == "Ascending Triangle"

    def test_descending_triangle(self):
        assert price_action_breakout._classify_shape(self._pattern(20, 110, 95, 90, 90)) == "Descending Triangle"

    def test_symmetrical_triangle(self):
        assert price_action_breakout._classify_shape(self._pattern(20, 110, 100, 80, 90)) == "Symmetrical Triangle"

    def test_rising_wedge(self):
        assert price_action_breakout._classify_shape(self._pattern(20, 90, 100, 70, 95)) == "Rising Wedge"

    def test_falling_wedge(self):
        assert price_action_breakout._classify_shape(self._pattern(20, 110, 90, 100, 85)) == "Falling Wedge"


class TestDetectBase:
    """Direct tests of the variable-length base search, independent of
    the full scan() pipeline."""

    def _df(self, lead_in_level, base_lows, base_high):
        # Integer-indexed frame: lead-in candles (wide/incompatible with
        # the base) followed by the actual base candles.
        n_lead = 30
        lead = pd.DataFrame(
            {
                "open": lead_in_level, "high": lead_in_level + 1, "low": lead_in_level - 1,
                "close": lead_in_level, "volume": 100_000.0,
            },
            index=range(n_lead),
        )
        base = pd.DataFrame(
            {
                "open": [low + 1 for low in base_lows],
                "high": [base_high] * len(base_lows),
                "low": base_lows,
                "close": [low + 2 for low in base_lows],
                "volume": 100_000.0,
            },
            index=range(n_lead, n_lead + len(base_lows)),
        )
        return pd.concat([lead, base]), n_lead + len(base_lows)

    def test_finds_the_longest_qualifying_base(self):
        base_lows = [95.0] * 15  # flat, tight (100 vs 95 = 5.3%, within 20%)
        df, breakout_idx = self._df(lead_in_level=50.0, base_lows=base_lows, base_high=100.0)
        high_bands, low_bands = price_action_breakout._rolling_bands(df, pattern_min_lookback=5, pattern_max_lookback=25)
        result = price_action_breakout._detect_base(
            df, breakout_idx, pattern_min_lookback=5, pattern_max_lookback=25, high_bands=high_bands, low_bands=low_bands
        )
        assert result is not None
        pattern, pattern_high, pattern_low = result
        # Longest window that still qualifies is exactly the 15 base
        # candles - anything longer pulls in the wide lead-in and fails
        # tightness.
        assert len(pattern) == 15
        assert pattern_high == 100.0
        assert pattern_low == 95.0

    def test_returns_none_when_nothing_qualifies(self):
        base_lows = [50.0] * 15  # (100-50)/50 = 100%, nowhere near tight
        df, breakout_idx = self._df(lead_in_level=50.0, base_lows=base_lows, base_high=100.0)
        high_bands, low_bands = price_action_breakout._rolling_bands(df, pattern_min_lookback=5, pattern_max_lookback=25)
        result = price_action_breakout._detect_base(
            df, breakout_idx, pattern_min_lookback=5, pattern_max_lookback=25, high_bands=high_bands, low_bands=low_bands
        )
        assert result is None
