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


def _signal(entry=100.0, stop_loss=95.0, targets=(110.0, 120.0)) -> Signal:
    return Signal(symbol="TESTCO", entry=entry, stop_loss=stop_loss, targets=list(targets))


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


def _weekly_df(closes: list[float]) -> pd.DataFrame:
    dates = pd.date_range("2024-01-05", periods=len(closes), freq="W-FRI")
    df = pd.DataFrame({"close": closes}, index=dates)
    df["open"] = df["close"]
    df["high"] = df["close"] * 1.02
    df["low"] = df["close"] * 0.98
    df["volume"] = 100_000.0
    return df


class TestSimulateWeeklyTrailingSma:
    def test_exits_when_close_falls_below_trailing_sma(self):
        # rides up for a few weeks, then a sharp drop closes below the
        # 3-week trailing SMA of closes - that week should trigger the exit.
        closes = [100, 110, 120, 130, 140, 90]
        df = _weekly_df(closes)
        signal_date = df.index[0]
        result = backtest.simulate_weekly_trailing_sma(
            "weekly_breakout", _signal(entry=100.0, stop_loss=80.0, targets=[]), signal_date, df, sma_period=3
        )
        assert result.outcome == "trailing_stop"
        assert result.exit_price == 90.0
        assert result.exit_date == df.index[-1]

    def test_stop_loss_breach_exits_before_sma_check(self):
        # low undercuts the hard stop the same week the close is still
        # above the trailing SMA - the stop-loss must win.
        dates = pd.date_range("2024-01-05", periods=3, freq="W-FRI")
        df = pd.DataFrame(
            {
                "open": [100.0, 105.0, 106.0],
                "high": [102.0, 107.0, 108.0],
                "low": [99.0, 104.0, 78.0],
                "close": [100.0, 106.0, 106.0],
                "volume": [100_000.0] * 3,
            },
            index=dates,
        )
        result = backtest.simulate_weekly_trailing_sma(
            "weekly_breakout", _signal(entry=100.0, stop_loss=80.0, targets=[]), dates[0], df, sma_period=2
        )
        assert result.outcome == "stop_loss"
        assert result.exit_price == 80.0
        assert result.exit_date == dates[2]

    def test_return_is_net_of_round_trip_transaction_cost(self):
        closes = [100, 110, 120, 130, 140, 90]
        df = _weekly_df(closes)
        signal_date = df.index[0]
        result = backtest.simulate_weekly_trailing_sma(
            "weekly_breakout", _signal(entry=100.0, stop_loss=80.0, targets=[]), signal_date, df, sma_period=3
        )
        gross_return_pct = (90.0 / 100.0 - 1) * 100
        assert result.return_pct == pytest.approx(gross_return_pct - config.ROUND_TRIP_COST_PCT)

    def test_still_open_when_never_falls_below_sma(self):
        closes = [100, 105, 110, 115, 120]
        df = _weekly_df(closes)
        signal_date = df.index[0]
        result = backtest.simulate_weekly_trailing_sma(
            "weekly_breakout", _signal(entry=100.0, stop_loss=80.0, targets=[]), signal_date, df, sma_period=3
        )
        assert result.outcome == "open"
        assert result.exit_price == df["close"].iloc[-1]

    def test_open_with_no_future_data_falls_back_to_entry(self):
        df = _weekly_df([100.0])
        signal_date = df.index[0]  # no rows after the signal date at all
        result = backtest.simulate_weekly_trailing_sma(
            "weekly_breakout", _signal(entry=100.0, stop_loss=80.0, targets=[]), signal_date, df, sma_period=3
        )
        assert result.outcome == "open"
        assert result.exit_price == 100.0
        assert result.holding_days == 0


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
