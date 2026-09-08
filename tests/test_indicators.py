import pandas as pd

from signals import indicators


def _row(**kwargs):
    base = {"open": 100.0, "high": 105.0, "low": 98.0, "close": 104.0, "volume": 1000.0}
    base.update(kwargs)
    return pd.Series(base)


def test_upper_wick_ratio_closed_at_high():
    row = _row(high=110, low=100, close=110)
    assert indicators.upper_wick_ratio(row) == 0.0


def test_upper_wick_ratio_closed_at_low():
    row = _row(high=110, low=100, close=100)
    assert indicators.upper_wick_ratio(row) == 1.0


def test_upper_wick_ratio_flat_candle_is_safe():
    row = _row(high=100, low=100, close=100)
    assert indicators.upper_wick_ratio(row) == 0.0


def test_is_proper_close_thresholds():
    tight = _row(high=110, low=100, close=108)  # wick ratio 0.2
    assert indicators.is_proper_close(tight, max_ratio=0.25) is True
    assert indicators.is_proper_close(_row(high=110, low=100, close=101), max_ratio=0.25) is False


def test_is_bullish():
    assert indicators.is_bullish(_row(open=100, close=105)) is True
    assert indicators.is_bullish(_row(open=105, close=100)) is False


def test_is_above_sma():
    row = _row(close=110)
    row["sma200"] = 100
    assert indicators.is_above_sma(row, "sma200") is True
    row["close"] = 90
    assert indicators.is_above_sma(row, "sma200") is False


def test_is_support_test():
    row = _row(low=99, close=101)
    row["sma30"] = 100
    # low is within 2% above sma (100*1.02=102 >= 99) and close (101) > sma (100)
    assert indicators.is_support_test(row, "sma30", tolerance=0.02) is True

    row["low"] = 90  # too far below to count as a controlled support test... actually low <= sma*(1+tol) is about touching from above
    assert indicators.is_support_test(row, "sma30", tolerance=0.02) is True  # still touched and held

    row["close"] = 95  # closed back below the MA -> support failed
    assert indicators.is_support_test(row, "sma30", tolerance=0.02) is False


def test_is_volume_candle():
    row = _row(volume=150)
    row["avg_vol20"] = 100
    assert indicators.is_volume_candle(row, "avg_vol20", multiplier=1.5) is True
    row["volume"] = 140
    assert indicators.is_volume_candle(row, "avg_vol20", multiplier=1.5) is False


def test_confluence_gap():
    assert indicators.confluence_gap(100, 101) < 0.02
    assert indicators.confluence_gap(100, 110) > 0.02


def test_add_sma_and_avg_volume():
    df = pd.DataFrame(
        {
            "close": list(range(1, 31)),
            "volume": [100] * 30,
        }
    )
    indicators.add_sma(df, 5)
    assert pd.isna(df["sma5"].iloc[3])
    assert df["sma5"].iloc[4] == sum(range(1, 6)) / 5

    indicators.add_avg_volume(df, 5)
    # shift(1) means row 5 (0-indexed) averages rows 0-4, not including itself
    assert df["avg_vol5"].iloc[5] == 100
    assert pd.isna(df["avg_vol5"].iloc[4])


def test_add_bollinger_bands():
    df = pd.DataFrame({"close": [100] * 25})
    indicators.add_bollinger_bands(df, period=20, num_std=2)
    row = df.iloc[-1]
    assert row["bb_mid"] == 100
    assert row["bb_upper"] == 100  # zero std when all closes equal
    assert row["bb_lower"] == 100


def test_is_sma_rising():
    df = pd.DataFrame({"sma": [10.0, 11.0, 12.0, 13.0, 14.0, 15.0]})
    assert indicators.is_sma_rising(df, "sma", lookback=3) is True


def test_is_sma_rising_false_when_flat():
    df = pd.DataFrame({"sma": [10.0] * 6})
    assert indicators.is_sma_rising(df, "sma", lookback=3) is False


def test_is_sma_rising_false_when_falling():
    df = pd.DataFrame({"sma": [15.0, 14.0, 13.0, 12.0, 11.0, 10.0]})
    assert indicators.is_sma_rising(df, "sma", lookback=3) is False


def test_is_sma_rising_false_with_insufficient_history():
    df = pd.DataFrame({"sma": [10.0, 11.0]})
    assert indicators.is_sma_rising(df, "sma", lookback=3) is False


def test_is_sma_rising_false_with_nan():
    df = pd.DataFrame({"sma": [float("nan"), 11.0, 12.0, 13.0]})
    assert indicators.is_sma_rising(df, "sma", lookback=3) is False
