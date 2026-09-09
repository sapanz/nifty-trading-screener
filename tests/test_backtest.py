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


def _signal(entry=100.0, stop_loss=95.0, targets=(110.0, 120.0), extra=None) -> Signal:
    return Signal(symbol="TESTCO", entry=entry, stop_loss=stop_loss, targets=list(targets), extra=extra or {})


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

    def test_time_stop_exits_at_close_when_neither_hit(self):
        # flat price, never touches the stop-loss or either target - without
        # a time-stop this would just be "open"; with one, it exits once
        # held past max_holding_days.
        df = _daily_df([100] * 10)
        signal_date = df.index[0]
        result = backtest.simulate_forward("weekly_breakout", _signal(), signal_date, df, max_holding_days=5)
        assert result.outcome == "time_stop"
        assert result.holding_days > 5
        assert result.exit_price == 100.0

    def test_max_holding_days_none_disables_time_stop(self):
        # default behavior (no max_holding_days passed) is unaffected -
        # every other strategy's simulate_forward calls stay exactly as
        # they were before this parameter existed.
        df = _daily_df([100] * 20)
        signal_date = df.index[0]
        result = backtest.simulate_forward("weekly_breakout", _signal(), signal_date, df)
        assert result.outcome == "open"

    def test_stop_loss_takes_priority_over_time_stop_same_day(self):
        df = _daily_df([100, 90])  # day1 low=90*0.99=89.1 <= stop 95
        signal_date = df.index[0]
        result = backtest.simulate_forward("weekly_breakout", _signal(), signal_date, df, max_holding_days=0)
        assert result.outcome == "stop_loss"

    def test_target_takes_priority_over_time_stop_same_day(self):
        df = _daily_df([100, 111])  # day1 high=111*1.01=112.11 crosses target1 (110)
        signal_date = df.index[0]
        result = backtest.simulate_forward("weekly_breakout", _signal(), signal_date, df, max_holding_days=0)
        assert result.outcome == "target1"


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

    def test_weekly_breakout_applies_time_stop(self, monkeypatch):
        # run_backtest wires WEEKLY_MAX_HOLDING_DAYS into weekly_breakout's
        # simulate_forward call - a flat price that never hits SL or target
        # should come back "time_stop", not "open", once held long enough.
        from signals.strategies import weekly_breakout as wb

        today = pd.Timestamp.today().normalize()
        signal_date = today - pd.Timedelta(days=35)  # well past WEEKLY_MAX_HOLDING_DAYS, still in a 2-month window
        daily_dates = pd.date_range(start=signal_date - pd.Timedelta(days=5), end=today, freq="B")
        daily_df = pd.DataFrame(
            {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1000.0},
            index=daily_dates,
        )
        daily_data = {"TESTCO": daily_df}
        weekly_data = {"TESTCO": pd.DataFrame({"close": [100.0]}, index=[signal_date])}

        def fake_scan(sliced):
            return [Signal(symbol="TESTCO", entry=100.0, stop_loss=50.0, targets=[200.0])]

        monkeypatch.setattr(wb, "scan", fake_scan)

        results = backtest.run_backtest(daily_data, months=2, weekly_data=weekly_data)
        assert [t.outcome for t in results["weekly_breakout"]] == ["time_stop"]


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
