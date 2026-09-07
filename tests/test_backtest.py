import pandas as pd

from signals import backtest
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
        result = backtest.simulate_forward("daily_swing", _signal(), signal_date, df)
        assert result.outcome == "target1"
        assert result.exit_price == 110.0
        assert result.return_pct > 0

    def test_hits_stop_loss_first(self):
        df = _daily_df([100, 98, 94, 90])  # day index 2: low=94*0.99=93.06 <= stop 95
        signal_date = df.index[0]
        result = backtest.simulate_forward("daily_swing", _signal(), signal_date, df)
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
        result = backtest.simulate_forward("daily_swing", _signal(), dates[0], df)
        assert result.outcome == "stop_loss"

    def test_hits_highest_target_reached_same_day(self):
        dates = pd.date_range("2024-01-01", periods=2, freq="B")
        df = pd.DataFrame(
            {"open": [100.0, 105.0], "high": [101.0, 125.0], "low": [99.0, 104.0], "close": [100.0, 122.0], "volume": [1000.0, 1000.0]},
            index=dates,
        )
        result = backtest.simulate_forward("daily_swing", _signal(), dates[0], df)
        assert result.outcome == "target2"
        assert result.exit_price == 120.0

    def test_still_open_when_neither_hit(self):
        df = _daily_df([100, 101, 102, 103])
        signal_date = df.index[0]
        result = backtest.simulate_forward("daily_swing", _signal(), signal_date, df)
        assert result.outcome == "open"
        assert result.exit_price == df["close"].iloc[-1]

    def test_open_with_no_future_data_falls_back_to_entry(self):
        df = _daily_df([100.0])
        signal_date = df.index[0]  # no rows after the signal date at all
        result = backtest.simulate_forward("daily_swing", _signal(), signal_date, df)
        assert result.outcome == "open"
        assert result.exit_price == 100.0
        assert result.holding_days == 0


class TestSummarize:
    def test_empty_trades(self):
        assert backtest.summarize([]) == "No signals in this window."

    def test_win_rate_and_stats(self):
        trades = [
            backtest.TradeResult("daily_swing", "A", pd.Timestamp("2024-01-01"), 100, 95, [110], "target1", pd.Timestamp("2024-01-05"), 110, 10.0, 4),
            backtest.TradeResult("daily_swing", "B", pd.Timestamp("2024-01-01"), 100, 95, [110], "stop_loss", pd.Timestamp("2024-01-03"), 95, -5.0, 2),
        ]
        text = backtest.summarize(trades)
        assert "2 signals" in text
        assert "1W-1L-0Open" in text
        assert "50% win rate" in text
