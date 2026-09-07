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


def test_is_above_sma():
    row = _row(close=110)
    row["sma200"] = 100
    assert indicators.is_above_sma(row, "sma200") is True
    row["close"] = 90
    assert indicators.is_above_sma(row, "sma200") is False


def test_is_volume_candle():
    row = _row(volume=150)
    row["avg_vol20"] = 100
    assert indicators.is_volume_candle(row, "avg_vol20", multiplier=1.5) is True
    row["volume"] = 140
    assert indicators.is_volume_candle(row, "avg_vol20", multiplier=1.5) is False


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
