import csv
import pandas as pd
import pytest

from signals import backtest, config
from signals.models import Signal


def _daily_df(closes: list[float]) -> pd.DataFrame:
    dates = pd.date_range("2024-01-01", periods=len(closes), freq="B")
    df = pd.DataFrame({"close": closes}, index=dates)
    df["open"] = df["close"]
    df["high"] = df["close"] * 1.01
    df["low"] = df["close"] * 0.99
    df["volume"] = 1000.0
    return df


def _signal(entry=100.0, stop_loss=95.0, targets=(110.0, 120.0), extra=None, direction="long") -> Signal:
    return Signal(
        symbol="TESTCO", entry=entry, stop_loss=stop_loss, targets=list(targets),
        extra=extra or {}, direction=direction,
    )


class TestSimulateForward:
    def test_hits_target1_first(self):
        df = _daily_df([100, 101, 102, 111, 112])  # day index 3 (close=111) -> high=112.11 crosses 110
        signal_date = df.index[0]
        result = backtest.simulate_forward("weekly_breakout", _signal(), signal_date, df)
        assert result.outcome == "target1"
        assert result.exit_price == 110.0
        assert result.return_pct > 0

    def test_return_is_net_of_round_trip_transaction_cost(self):
        df = _daily_df([100, 101, 102, 111, 112])
        signal_date = df.index[0]
        result = backtest.simulate_forward("weekly_breakout", _signal(), signal_date, df)
        gross_return_pct = (110.0 / 100.0 - 1) * 100
        assert result.return_pct == pytest.approx(gross_return_pct - config.ROUND_TRIP_COST_PCT)

    def test_hits_stop_loss_first(self):
        df = _daily_df([100, 98, 94, 90])  # day index 2: low=94*0.99=93.06 <= stop 95
        signal_date = df.index[0]
        result = backtest.simulate_forward("weekly_breakout", _signal(), signal_date, df)
        assert result.outcome == "stop_loss"
        assert result.exit_price == 95.0
        assert result.return_pct < 0

    def test_stop_loss_wins_when_both_hit_same_day(self):
        # a single day whose range spans below stop and above target - stop
        # is assumed to trigger first (conservative, no intraday sequencing)
        dates = pd.date_range("2024-01-01", periods=2, freq="B")
        df = pd.DataFrame(
            {"open": [100.0, 100.0], "high": [101.0, 130.0], "low": [99.0, 90.0], "close": [100.0, 105.0], "volume": [1000.0, 1000.0]},
            index=dates,
        )
        result = backtest.simulate_forward("weekly_breakout", _signal(), dates[0], df)
        assert result.outcome == "stop_loss"

    def test_hits_highest_target_reached_same_day(self):
        dates = pd.date_range("2024-01-01", periods=2, freq="B")
        df = pd.DataFrame(
            {"open": [100.0, 105.0], "high": [101.0, 125.0], "low": [99.0, 104.0], "close": [100.0, 122.0], "volume": [1000.0, 1000.0]},
            index=dates,
        )
        result = backtest.simulate_forward("weekly_breakout", _signal(), dates[0], df)
        assert result.outcome == "target2"
        assert result.exit_price == 120.0

    def test_still_open_when_neither_hit(self):
        df = _daily_df([100, 101, 102, 103])
        signal_date = df.index[0]
        result = backtest.simulate_forward("weekly_breakout", _signal(), signal_date, df)
        assert result.outcome == "open"
        assert result.exit_price == df["close"].iloc[-1]

    def test_open_with_no_future_data_falls_back_to_entry(self):
        df = _daily_df([100.0])
        signal_date = df.index[0]  # no rows after the signal date at all
        result = backtest.simulate_forward("weekly_breakout", _signal(), signal_date, df)
        assert result.outcome == "open"
        assert result.exit_price == 100.0
        assert result.holding_days == 0

    def test_stop_style_entry_unfilled_when_never_reached(self):
        # entry (106) sits above the signal candle's own close (100) - a
        # resting buy-stop, not an immediate fill. Price never trades back
        # up to 106, so this should never count as a real trade.
        df = _daily_df([100, 101, 102, 103])
        signal_date = df.index[0]
        result = backtest.simulate_forward("cip_daily", _signal(entry=106.0), signal_date, df)
        assert result.outcome == "unfilled"
        assert result.return_pct == 0.0
        assert result.holding_days == 0

    def test_stop_style_entry_tracks_outcome_only_after_fill(self):
        # entry=106 isn't reached until day 2 (high=107.07); before that,
        # a stop-loss breach shouldn't count since the order wasn't live yet.
        dates = pd.date_range("2024-01-01", periods=4, freq="B")
        df = pd.DataFrame(
            {
                "open": [100.0, 90.0, 106.0, 106.0],
                "high": [100.0, 91.0, 107.0, 96.0],
                "low": [100.0, 89.0, 106.0, 94.0],  # day1 low=89 would look like a stop breach if checked too early
                "close": [100.0, 90.0, 106.5, 95.0],
                "volume": [1000.0] * 4,
            },
            index=dates,
        )
        result = backtest.simulate_forward("cip_daily", _signal(entry=106.0, stop_loss=95.0), dates[0], df)
        assert result.outcome == "stop_loss"
        assert result.exit_date == dates[3]

    def test_carries_months_gap_from_signal_extra(self):
        # Monthly ATH Breakout stamps how long the stock was below its old
        # high onto the signal - that should ride along on every outcome.
        df = _daily_df([100, 101, 102, 103])
        signal_date = df.index[0]
        result = backtest.simulate_forward(
            "monthly_breakout", _signal(extra={"months_gap": 14}), signal_date, df
        )
        assert result.months_gap == 14

    def test_months_gap_is_none_when_not_set(self):
        df = _daily_df([100, 101, 102, 103])
        signal_date = df.index[0]
        result = backtest.simulate_forward("weekly_breakout", _signal(), signal_date, df)
        assert result.months_gap is None


class TestSimulateForwardShort:
    """Mirror of TestSimulateForward for direction="short" - stop-loss sits
    above entry, targets below, and every OHLC comparison flips (a short
    is stopped out by price rising, hits target as price falls)."""

    def _short_signal(self, entry=100.0, stop_loss=105.0, targets=(90.0, 80.0)):
        return _signal(entry=entry, stop_loss=stop_loss, targets=targets, direction="short")

    def test_hits_target1_first(self):
        df = _daily_df([100, 99, 98, 89, 88])  # day index 3 (close=89) -> low=88.11 crosses below 90
        signal_date = df.index[0]
        result = backtest.simulate_forward("generic_short", self._short_signal(), signal_date, df)
        assert result.outcome == "target1"
        assert result.exit_price == 90.0
        assert result.return_pct > 0  # short profits as price falls

    def test_return_is_net_of_round_trip_transaction_cost(self):
        df = _daily_df([100, 99, 98, 89, 88])
        signal_date = df.index[0]
        result = backtest.simulate_forward("generic_short", self._short_signal(), signal_date, df)
        gross_return_pct = (1 - 90.0 / 100.0) * 100
        assert result.return_pct == pytest.approx(gross_return_pct - config.ROUND_TRIP_COST_PCT)

    def test_hits_stop_loss_first(self):
        df = _daily_df([100, 102, 106, 110])  # day index 2: high=106*1.01=107.06 >= stop 105
        signal_date = df.index[0]
        result = backtest.simulate_forward("generic_short", self._short_signal(), signal_date, df)
        assert result.outcome == "stop_loss"
        assert result.exit_price == 105.0
        assert result.return_pct < 0  # short loses as price rises

    def test_stop_loss_wins_when_both_hit_same_day(self):
        # a single day whose range spans above stop and below target - stop
        # is assumed to trigger first, same conservative assumption as long.
        dates = pd.date_range("2024-01-01", periods=2, freq="B")
        df = pd.DataFrame(
            {"open": [100.0, 100.0], "high": [101.0, 110.0], "low": [99.0, 70.0], "close": [100.0, 95.0], "volume": [1000.0, 1000.0]},
            index=dates,
        )
        result = backtest.simulate_forward("generic_short", self._short_signal(), dates[0], df)
        assert result.outcome == "stop_loss"

    def test_hits_farthest_target_reached_same_day(self):
        dates = pd.date_range("2024-01-01", periods=2, freq="B")
        df = pd.DataFrame(
            {"open": [100.0, 95.0], "high": [101.0, 96.0], "low": [99.0, 75.0], "close": [100.0, 78.0], "volume": [1000.0, 1000.0]},
            index=dates,
        )
        result = backtest.simulate_forward("generic_short", self._short_signal(), dates[0], df)
        assert result.outcome == "target2"
        assert result.exit_price == 80.0

    def test_still_open_when_neither_hit(self):
        df = _daily_df([100, 99, 98, 97])
        signal_date = df.index[0]
        result = backtest.simulate_forward("generic_short", self._short_signal(), signal_date, df)
        assert result.outcome == "open"
        assert result.exit_price == df["close"].iloc[-1]
        assert result.return_pct > 0  # price drifted down, favorable for a short

    def test_resting_sell_stop_unfilled_when_never_reached(self):
        # entry (94) sits below the signal candle's own close (100) - a
        # resting sell-stop, not an immediate fill. Price never trades back
        # down to 94, so this should never count as a real trade.
        df = _daily_df([100, 101, 102, 103])
        signal_date = df.index[0]
        result = backtest.simulate_forward(
            "generic_short", self._short_signal(entry=94.0, stop_loss=99.0, targets=(85.0,)), signal_date, df
        )
        assert result.outcome == "unfilled"
        assert result.return_pct == 0.0
        assert result.holding_days == 0

    def test_resting_sell_stop_tracks_outcome_only_after_fill(self):
        # entry=94 isn't reached until day 2 (low=93.06); before that, a
        # stop-loss breach shouldn't count since the order wasn't live yet.
        dates = pd.date_range("2024-01-01", periods=4, freq="B")
        df = pd.DataFrame(
            {
                "open": [100.0, 110.0, 94.0, 94.0],
                "high": [100.0, 111.0, 94.0, 106.0],  # day1 high=111 would look like a stop breach if checked too early
                "low": [100.0, 109.0, 93.0, 92.0],
                "close": [100.0, 110.0, 93.5, 105.0],
                "volume": [1000.0] * 4,
            },
            index=dates,
        )
        result = backtest.simulate_forward(
            "generic_short", self._short_signal(entry=94.0, stop_loss=105.0), dates[0], df
        )
        assert result.outcome == "stop_loss"
        assert result.exit_date == dates[3]


class TestSimulateForwardMaxHoldingDays:
    """max_holding_days - a "quick trade, forced exit after N trading days"
    option no current strategy uses (they all pass None, the default, for
    which this never fires - see the other TestSimulateForward* classes),
    kept as generic infra since simulate_forward supports it directly."""

    def test_force_exits_at_close_after_max_holding_days(self):
        # Drifts gently upward, never touching the stop (95) or target (110)
        # within 3 trading days - should force-exit at day 3's close rather
        # than keep riding as "open".
        df = _daily_df([100, 100.5, 101, 101.5, 108, 109])
        signal_date = df.index[0]
        result = backtest.simulate_forward("generic_short", _signal(), signal_date, df, max_holding_days=3)
        assert result.outcome == "time_exit"
        assert result.exit_date == df.index[3]  # 3rd trading day after signal_date
        assert result.exit_price == 101.5

    def test_stop_or_target_still_wins_before_max_holding_days(self):
        # Target (110) is hit on day 2 - well before the day-3 cap - so the
        # cap should never come into play.
        df = _daily_df([100, 101, 111, 101, 101])
        signal_date = df.index[0]
        result = backtest.simulate_forward("generic_short", _signal(), signal_date, df, max_holding_days=3)
        assert result.outcome == "target1"

    def test_no_forced_exit_when_max_holding_days_is_none(self):
        # The default for every current strategy - drifting sideways for a
        # long time should stay "open", not force-exit.
        df = _daily_df([100, 100.5, 101, 101.2, 101.4, 101.6, 101.8, 102])
        signal_date = df.index[0]
        result = backtest.simulate_forward("weekly_breakout", _signal(), signal_date, df)
        assert result.outcome == "open"

    def test_holding_days_counted_from_fill_not_signal_date(self):
        # A resting stop-style entry (106, above the signal close of 100)
        # only fills on day 2 (high=107.07 >= 106) - the 2-trading-day cap
        # should count from THAT fill, not from signal_date, so day 2 after
        # fill is day 4 overall.
        dates = pd.date_range("2024-01-01", periods=5, freq="B")
        df = pd.DataFrame(
            {
                "open": [100.0, 100.0, 106.0, 106.0, 106.0],
                "high": [100.0, 101.0, 107.0, 106.5, 106.5],
                "low": [100.0, 99.0, 106.0, 105.5, 105.5],
                "close": [100.0, 100.0, 106.5, 106.0, 106.2],
                "volume": [1000.0] * 5,
            },
            index=dates,
        )
        result = backtest.simulate_forward(
            "generic_short", _signal(entry=106.0), dates[0], df, max_holding_days=2,
        )
        assert result.outcome == "time_exit"
        assert result.exit_date == dates[3]  # 2nd trading day after the day-2 fill


def _weekly_df(closes: list[float]) -> pd.DataFrame:
    dates = pd.date_range("2024-01-05", periods=len(closes), freq="W-FRI")
    df = pd.DataFrame({"close": closes}, index=dates)
    df["open"] = df["close"]
    df["high"] = [c * 1.01 for c in closes]
    df["low"] = [c * 0.99 for c in closes]
    df["volume"] = 100_000.0
    return df


class TestSimulateWeeklyTrailingSma:
    """value_breakout.py's exit mechanic - no fixed stop-loss or target,
    just a trailing weekly-close-vs-SMA condition, filled the next trading
    day after the breach (not at the triggering week's own close)."""

    def test_exits_next_trading_day_open_after_sma_breach(self):
        # sma_period=3: rolling mean of the last 3 closes (incl. today).
        # index0=100 (signal week); index1=110 (sma NaN, <3 points yet);
        # index2=112 (sma3=107.33, 112 >= that - no breach);
        # index3=108 (sma3=110.0, 108 < that - BREACH here).
        weekly = _weekly_df([100, 110, 112, 108, 90, 95])
        signal_date = weekly.index[0]
        breach_date = weekly.index[3]
        next_day = breach_date + pd.tseries.offsets.BDay(1)
        daily = pd.DataFrame(
            {"open": [107.5], "high": [108.0], "low": [106.0], "close": [107.0], "volume": [1000.0]},
            index=[next_day],
        )
        signal = _signal(entry=100.0, stop_loss=95.0, targets=[])

        result = backtest.simulate_weekly_trailing_sma("value_breakout", signal, signal_date, weekly, daily, sma_period=3)
        assert result.outcome == "trailing_sma_exit"
        # Exit fills at the NEXT trading day's open, not the breaching
        # week's own close (108) - can't react to a Friday close until
        # markets reopen.
        assert result.exit_price == 107.5
        assert result.exit_date == next_day

    def test_return_is_net_of_round_trip_transaction_cost(self):
        weekly = _weekly_df([100, 110, 112, 108, 90, 95])
        signal_date = weekly.index[0]
        breach_date = weekly.index[3]
        next_day = breach_date + pd.tseries.offsets.BDay(1)
        daily = pd.DataFrame(
            {"open": [107.5], "high": [108.0], "low": [106.0], "close": [107.0], "volume": [1000.0]},
            index=[next_day],
        )
        signal = _signal(entry=100.0, targets=[])

        result = backtest.simulate_weekly_trailing_sma("value_breakout", signal, signal_date, weekly, daily, sma_period=3)
        gross_return_pct = (107.5 / 100.0 - 1) * 100
        assert result.return_pct == pytest.approx(gross_return_pct - config.ROUND_TRIP_COST_PCT)

    def test_ignores_signal_stop_loss_entirely(self):
        # A stop_loss far above every future close would trigger a "stop_loss"
        # outcome instantly under simulate_forward's model - this function
        # never checks it at all; only the trailing-SMA condition drives
        # the exit, since signal.stop_loss is informational-only here (see
        # value_breakout.py's docstring).
        weekly = _weekly_df([100, 110, 112, 108, 90, 95])
        signal_date = weekly.index[0]
        breach_date = weekly.index[3]
        next_day = breach_date + pd.tseries.offsets.BDay(1)
        daily = pd.DataFrame(
            {"open": [107.5], "high": [108.0], "low": [106.0], "close": [107.0], "volume": [1000.0]},
            index=[next_day],
        )
        signal = _signal(entry=100.0, stop_loss=999.0, targets=[])

        result = backtest.simulate_weekly_trailing_sma("value_breakout", signal, signal_date, weekly, daily, sma_period=3)
        assert result.outcome == "trailing_sma_exit"

    def test_still_open_when_no_daily_data_past_breach_week(self):
        # The SMA is breached at the very last available weekly candle, but
        # there's no later daily bar yet to fill the exit on - reported
        # "open" rather than guessing a fill that hasn't happened.
        weekly = _weekly_df([100, 110, 112, 108])  # sma3 breach at index3 (last week)
        signal_date = weekly.index[0]
        daily = pd.DataFrame(columns=["open", "high", "low", "close", "volume"], index=pd.DatetimeIndex([]))
        signal = _signal(entry=100.0, targets=[])

        result = backtest.simulate_weekly_trailing_sma("value_breakout", signal, signal_date, weekly, daily, sma_period=3)
        assert result.outcome == "open"

    def test_still_open_when_sma_never_breached(self):
        weekly = _weekly_df([100, 105, 110, 115, 120])  # steadily rising, always above its own trailing SMA
        signal_date = weekly.index[0]
        daily = pd.DataFrame(columns=["open", "high", "low", "close", "volume"], index=pd.DatetimeIndex([]))
        signal = _signal(entry=100.0, targets=[])

        result = backtest.simulate_weekly_trailing_sma("value_breakout", signal, signal_date, weekly, daily, sma_period=3)
        assert result.outcome == "open"


class TestRunBacktest:
    def test_monthly_signal_for_symbol_missing_from_daily_data_is_skipped(self, monkeypatch):
        # monthly_data can come from a separate Upstox fetch than daily_data
        # (data.fetch_monthly_ath_history vs data.fetch_daily) - a symbol
        # that fails one fetch but not the other shouldn't blow up the
        # simulate_forward walk-forward, which needs daily bars.
        from signals.strategies import monthly_breakout as mb

        today = pd.Timestamp.today().normalize()
        daily_data = {"HASDAILY": _daily_df([100, 101, 102, 103, 104])}
        monthly_data = {
            "HASDAILY": pd.DataFrame({"close": [100.0]}, index=[today]),
            "NODAILY": pd.DataFrame({"close": [100.0]}, index=[today]),
        }

        def fake_scan(sliced):
            return [Signal(symbol=sym, entry=100.0, stop_loss=95.0, targets=[110.0]) for sym in sliced]

        monkeypatch.setattr(mb, "scan", fake_scan)

        results = backtest.run_backtest(daily_data, months=1, monthly_data=monthly_data)
        assert [t.symbol for t in results["monthly_breakout"]] == ["HASDAILY"]

    def test_monthly_data_defaults_to_resampling_daily_data(self):
        # omitting monthly_data shouldn't raise - it falls back to
        # resampling daily_data (config.DAILY_HISTORY_YEARS-capped, but
        # still usable for a quick local backtest).
        daily_data = {"TESTCO": _daily_df([100, 101, 102, 103, 104])}
        results = backtest.run_backtest(daily_data, months=1)
        assert results["monthly_breakout"] == []  # too little history to signal, but no error

    def test_weekly_signal_for_symbol_missing_from_daily_data_is_skipped(self, monkeypatch):
        # Same reasoning as the monthly case above: weekly_data can come
        # from a separate Upstox fetch (data.fetch_weekly_history) than
        # daily_data, so the two symbol sets can diverge.
        from signals.strategies import weekly_breakout as wb

        today = pd.Timestamp.today().normalize()
        daily_data = {"HASDAILY": _daily_df([100, 101, 102, 103, 104])}
        weekly_data = {
            "HASDAILY": pd.DataFrame({"close": [100.0]}, index=[today]),
            "NODAILY": pd.DataFrame({"close": [100.0]}, index=[today]),
        }

        def fake_scan(sliced):
            return [Signal(symbol=sym, entry=100.0, stop_loss=95.0, targets=[110.0]) for sym in sliced]

        monkeypatch.setattr(wb, "scan", fake_scan)

        results = backtest.run_backtest(daily_data, months=1, weekly_data=weekly_data)
        assert [t.symbol for t in results["weekly_breakout"]] == ["HASDAILY"]

    def test_weekly_data_defaults_to_resampling_daily_data(self):
        # omitting weekly_data shouldn't raise - it falls back to
        # resampling daily_data, same fallback pattern as monthly_data.
        daily_data = {"TESTCO": _daily_df([100, 101, 102, 103, 104])}
        results = backtest.run_backtest(daily_data, months=1)
        assert results["weekly_breakout"] == []  # too little history to signal, but no error

    def test_value_breakout_uses_its_own_universe_not_the_nifty500_one(self, monkeypatch):
        # value_breakout screens the full NSE via screener.in
        # (value_universe), not the fixed Nifty 500 weekly_data/daily_data
        # every other weekly strategy here uses - it should be scanned
        # against value_weekly_data/value_daily_data instead, so a symbol
        # that only exists there (outside the Nifty 500 fetch) still gets
        # backtested.
        from signals.strategies import value_breakout as vb

        today = pd.Timestamp.today().normalize()
        nifty_weekly = {"NIFTYCO": pd.DataFrame({"close": [100.0]}, index=[today])}
        nifty_daily = {"NIFTYCO": _daily_df([100, 101, 102, 103, 104])}
        value_weekly = {"OUTSIDECO": pd.DataFrame({"close": [100.0]}, index=[today])}
        value_daily = {"OUTSIDECO": _daily_df([100, 101, 102, 103, 104])}

        seen_universes = []

        def fake_scan(weekly_data, value_universe):
            seen_universes.append(set(weekly_data))
            return [Signal(symbol="OUTSIDECO", entry=100.0, stop_loss=95.0, targets=[])]

        monkeypatch.setattr(vb, "scan", fake_scan)

        results = backtest.run_backtest(
            nifty_daily, months=1, weekly_data=nifty_weekly,
            strategies={"value_breakout"}, value_universe={"OUTSIDECO"},
            value_weekly_data=value_weekly, value_daily_data=value_daily,
        )

        assert seen_universes and all(u == {"OUTSIDECO"} for u in seen_universes)
        assert [t.symbol for t in results["value_breakout"]] == ["OUTSIDECO"]

    def test_value_breakout_defaults_to_weekly_data_and_daily_data(self):
        # Omitting value_weekly_data/value_daily_data falls back to
        # weekly_data/daily_data - a quick local backtest without the
        # extra Upstox fetches, same fallback pattern weekly_data/
        # monthly_data already have.
        today = pd.Timestamp.today().normalize()
        weekly_data = {"TESTCO": pd.DataFrame({"close": [100.0]}, index=[today])}
        daily_data = {"TESTCO": _daily_df([100, 101, 102, 103, 104])}

        results = backtest.run_backtest(
            daily_data, months=1, weekly_data=weekly_data,
            strategies={"value_breakout"}, value_universe={"TESTCO"},
        )
        assert results["value_breakout"] == []  # too little history to signal, but no error

    def test_strategies_filter_limits_which_keys_come_back(self):
        daily_data = {"TESTCO": _daily_df([100, 101, 102, 103, 104])}
        results = backtest.run_backtest(
            daily_data, months=1, strategies={"price_action_breakout_daily", "daily_swing"},
        )
        assert set(results) == {"price_action_breakout_daily", "daily_swing"}

    def test_strategies_filter_skips_excluded_strategies_scan_entirely(self, monkeypatch):
        # Not just filtered from the output - the excluded strategy's scan()
        # should never even be called, since that's the actual point of
        # scoping (skipping its per-date compute, not just its Telegram line).
        from signals.strategies import daily_swing as ds

        called = []
        monkeypatch.setattr(ds, "scan", lambda *a, **k: called.append(1) or [])

        daily_data = {"TESTCO": _daily_df([100, 101, 102, 103, 104])}
        results = backtest.run_backtest(daily_data, months=1, strategies={"price_action_breakout_daily"})

        assert called == []
        assert "daily_swing" not in results

    def test_strategies_none_runs_everything_same_as_before(self):
        daily_data = {"TESTCO": _daily_df([100, 101, 102, 103, 104])}
        results = backtest.run_backtest(daily_data, months=1)
        assert set(results) == backtest.ALL_STRATEGIES

    def test_short_eligible_is_threaded_through_to_both_price_action_legs(self, monkeypatch):
        # _dates_in_window only walks dates that actually fall inside the
        # trailing `months` window, so (unlike the other tests in this
        # class, which use fixed 2024 dates and never need scan() to
        # actually fire) this needs data dated near "today".
        from signals.strategies import price_action_breakout as pab

        seen_kwargs = []
        monkeypatch.setattr(pab, "scan", lambda *a, **k: seen_kwargs.append(k) or [])

        dates = pd.date_range(end=pd.Timestamp.today().normalize(), periods=5, freq="B")
        recent_df = pd.DataFrame({"close": [100.0, 101, 102, 103, 104]}, index=dates)
        recent_df["open"] = recent_df["close"]
        recent_df["high"] = recent_df["close"] * 1.01
        recent_df["low"] = recent_df["close"] * 0.99
        recent_df["volume"] = 1000.0
        daily_data = {"TESTCO": recent_df}

        backtest.run_backtest(
            daily_data, months=1,
            strategies={"price_action_breakout_daily", "price_action_breakout_weekly"},
            short_eligible={"TESTCO"},
        )

        assert len(seen_kwargs) >= 2  # daily leg and weekly leg both called at least once
        assert all(k["short_eligible"] == {"TESTCO"} for k in seen_kwargs)


class TestWriteCsv:
    def test_months_gap_column(self, tmp_path):
        trades = [
            backtest.TradeResult(
                "monthly_breakout", "A", pd.Timestamp("2024-01-01"), 100, 95, [115],
                "target1", pd.Timestamp("2024-02-01"), 115, 15.0, 31, months_gap=14,
            ),
            backtest.TradeResult(
                "weekly_breakout", "B", pd.Timestamp("2024-01-01"), 100, 95, [110],
                "stop_loss", pd.Timestamp("2024-01-03"), 95, -5.0, 2,
            ),
        ]
        path = tmp_path / "trades.csv"
        backtest.write_csv(trades, str(path))

        with open(path, newline="") as f:
            rows = list(csv.DictReader(f))
        assert rows[0]["months_gap"] == "14"
        assert rows[1]["months_gap"] == ""

    def test_diagnostics_columns(self, tmp_path):
        # Diagnostic-only fields (see backtest.DIAGNOSTIC_KEYS) ride along
        # per-trade so a backtest's CSV can be mined for what separates good
        # signals from bad ones - a trade whose signal didn't set a given
        # key just leaves that column blank.
        trades = [
            backtest.TradeResult(
                "daily_swing", "A", pd.Timestamp("2024-01-01"), 100, 95, [110],
                "target1", pd.Timestamp("2024-01-10"), 110, 10.0, 9,
                diagnostics={"rsi14": 62.5, "vol_ratio": 1.8},
            ),
            backtest.TradeResult(
                "weekly_breakout", "B", pd.Timestamp("2024-01-01"), 100, 95, [110],
                "stop_loss", pd.Timestamp("2024-01-03"), 95, -5.0, 2,
            ),
        ]
        path = tmp_path / "trades.csv"
        backtest.write_csv(trades, str(path))

        with open(path, newline="") as f:
            rows = list(csv.DictReader(f))
        assert rows[0]["rsi14"] == "62.5"
        assert rows[0]["vol_ratio"] == "1.8"
        assert rows[0]["confluence_gap_pct"] == ""  # not set on this trade
        assert rows[1]["rsi14"] == ""


class TestSummarize:
    def test_empty_trades(self):
        assert backtest.summarize([]) == "No signals in this window."

    def test_win_rate_and_stats(self):
        trades = [
            backtest.TradeResult("weekly_breakout", "A", pd.Timestamp("2024-01-01"), 100, 95, [110], "target1", pd.Timestamp("2024-01-05"), 110, 10.0, 4),
            backtest.TradeResult("weekly_breakout", "B", pd.Timestamp("2024-01-01"), 100, 95, [110], "stop_loss", pd.Timestamp("2024-01-03"), 95, -5.0, 2),
        ]
        text = backtest.summarize(trades)
        assert "2 signals" in text
        assert "1W-1L-0Open" in text
        assert "50% win rate" in text

    def test_trailing_stop_win_counts_as_a_win_not_a_loss(self):
        # a "trailing_stop" outcome with a positive return is a real win -
        # classification goes by the actual return, not the outcome label.
        trades = [
            backtest.TradeResult("weekly_breakout", "A", pd.Timestamp("2024-01-01"), 100, 95, [], "trailing_stop", pd.Timestamp("2024-01-10"), 114, 14.0, 9),
            backtest.TradeResult("weekly_breakout", "B", pd.Timestamp("2024-01-01"), 100, 95, [], "trailing_stop", pd.Timestamp("2024-01-03"), 95, -5.0, 2),
        ]
        text = backtest.summarize(trades)
        assert "1W-1L-0Open" in text
        assert "50% win rate" in text

    def test_profit_factor_and_avg_win_loss(self):
        trades = [
            backtest.TradeResult("weekly_breakout", "A", pd.Timestamp("2024-01-01"), 100, 95, [], "trailing_stop", pd.Timestamp("2024-01-10"), 120, 20.0, 9),
            backtest.TradeResult("weekly_breakout", "B", pd.Timestamp("2024-01-01"), 100, 95, [], "trailing_stop", pd.Timestamp("2024-01-03"), 95, -5.0, 2),
            backtest.TradeResult("weekly_breakout", "C", pd.Timestamp("2024-01-01"), 100, 95, [], "trailing_stop", pd.Timestamp("2024-01-03"), 95, -5.0, 2),
        ]
        text = backtest.summarize(trades)
        assert "Avg win: +20.0%" in text
        assert "Avg loss: -5.0%" in text
        # gross win 20.0 / gross loss 10.0 = 2.00
        assert "Profit factor: 2.00" in text

    def test_by_shape_breakdown_only_appears_when_breakout_type_is_set(self):
        # Price Action Breakout's shape diagnostic - only present on that
        # strategy's trades, so this line should only appear when at least
        # one trade actually carries it.
        trades = [
            backtest.TradeResult(
                "price_action_breakout_daily", "A", pd.Timestamp("2024-01-01"), 100, 95, [110],
                "target1", pd.Timestamp("2024-01-05"), 110, 10.0, 4,
                diagnostics={"breakout_type": "Range"},
            ),
            backtest.TradeResult(
                "price_action_breakout_daily", "B", pd.Timestamp("2024-01-01"), 100, 95, [110],
                "stop_loss", pd.Timestamp("2024-01-03"), 95, -5.0, 2,
                diagnostics={"breakout_type": "Range"},
            ),
            backtest.TradeResult(
                "price_action_breakout_daily", "C", pd.Timestamp("2024-01-01"), 100, 95, [110],
                "target1", pd.Timestamp("2024-01-05"), 110, 10.0, 4,
                diagnostics={"breakout_type": "Ascending Triangle"},
            ),
        ]
        text = backtest.summarize(trades)
        assert "By shape: Range 2 (50%) | Ascending Triangle 1 (100%)" in text

        no_shape_trades = [
            backtest.TradeResult("weekly_breakout", "A", pd.Timestamp("2024-01-01"), 100, 95, [110], "target1", pd.Timestamp("2024-01-05"), 110, 10.0, 4),
        ]
        assert "By shape" not in backtest.summarize(no_shape_trades)

    def test_by_base_length_breakdown_only_appears_when_base_candles_is_set(self):
        # Same reasoning as the shape breakdown - base_candles is only set
        # by Price Action Breakout, bucketed into fixed "Under 15"/15-25/25-35/35+
        # bands, in that order, skipping any band with no trades.
        trades = [
            backtest.TradeResult(
                "price_action_breakout_daily", "A", pd.Timestamp("2024-01-01"), 100, 95, [110],
                "target1", pd.Timestamp("2024-01-05"), 110, 10.0, 4,
                diagnostics={"base_candles": 10},
            ),
            backtest.TradeResult(
                "price_action_breakout_daily", "B", pd.Timestamp("2024-01-01"), 100, 95, [110],
                "stop_loss", pd.Timestamp("2024-01-03"), 95, -5.0, 2,
                diagnostics={"base_candles": 12},
            ),
            backtest.TradeResult(
                "price_action_breakout_daily", "C", pd.Timestamp("2024-01-01"), 100, 95, [110],
                "target1", pd.Timestamp("2024-01-05"), 110, 10.0, 4,
                diagnostics={"base_candles": 38},
            ),
        ]
        text = backtest.summarize(trades)
        assert "By base length: Under 15 2 (50%) | 35+ 1 (100%)" in text

        no_base_len_trades = [
            backtest.TradeResult("weekly_breakout", "A", pd.Timestamp("2024-01-01"), 100, 95, [110], "target1", pd.Timestamp("2024-01-05"), 110, 10.0, 4),
        ]
        assert "By base length" not in backtest.summarize(no_base_len_trades)

    def test_win_rate_ignores_open_trades(self):
        # 1 win, 1 loss, 2 open -> win rate should be 50% of the 2 DECIDED
        # trades, not 25% of all 4 (open positions haven't resolved yet,
        # they shouldn't silently drag the headline win rate down)
        trades = [
            backtest.TradeResult("weekly_breakout", "A", pd.Timestamp("2024-01-01"), 100, 95, [110], "target1", pd.Timestamp("2024-01-05"), 110, 10.0, 4),
            backtest.TradeResult("weekly_breakout", "B", pd.Timestamp("2024-01-01"), 100, 95, [110], "stop_loss", pd.Timestamp("2024-01-03"), 95, -5.0, 2),
            backtest.TradeResult("weekly_breakout", "C", pd.Timestamp("2024-01-01"), 100, 95, [110], "open", pd.Timestamp("2024-01-10"), 102, 2.0, 9),
            backtest.TradeResult("weekly_breakout", "D", pd.Timestamp("2024-01-01"), 100, 95, [110], "open", pd.Timestamp("2024-01-10"), 103, 3.0, 9),
        ]
        text = backtest.summarize(trades)
        assert "4 signals" in text
        assert "1W-1L-2Open" in text
        assert "50% win rate of 2 decided" in text

    def test_win_rate_all_open(self):
        trades = [
            backtest.TradeResult("weekly_breakout", "A", pd.Timestamp("2024-01-01"), 100, 95, [110], "open", pd.Timestamp("2024-01-10"), 102, 2.0, 9),
        ]
        text = backtest.summarize(trades)
        assert "no decided trades yet" in text

    def test_open_trades_are_named_not_just_counted(self):
        # "how many are open" (the headline count) isn't enough to actually
        # act on - someone tracking a strategy needs to know *which*
        # symbols, sorted best-to-worst so it's not just an unordered dump.
        trades = [
            backtest.TradeResult("value_breakout", "LOWRET", pd.Timestamp("2024-01-01"), 100, 95, [], "open", pd.Timestamp("2024-06-01"), 105, 5.0, 150),
            backtest.TradeResult("value_breakout", "HIGHRET", pd.Timestamp("2024-01-01"), 100, 95, [], "open", pd.Timestamp("2024-09-01"), 150, 50.0, 240),
        ]
        text = backtest.summarize(trades)
        assert "Open: HIGHRET +50.0% (240d), LOWRET +5.0% (150d)" in text

    def test_no_open_line_when_no_open_trades(self):
        trades = [
            backtest.TradeResult("weekly_breakout", "A", pd.Timestamp("2024-01-01"), 100, 95, [110], "target1", pd.Timestamp("2024-01-05"), 110, 10.0, 4),
        ]
        assert "Open:" not in backtest.summarize(trades)
