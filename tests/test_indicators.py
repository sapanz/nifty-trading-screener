import pandas as pd
import pytest

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


def test_is_accumulation_range_true_when_up_volume_wins():
    df = pd.DataFrame({
        "open":   [100.0, 102.0, 99.0],
        "close":  [102.0, 99.0,  101.0],  # green, red, green
        "volume": [500.0, 200.0, 400.0],  # up=900, down=200
    })
    assert indicators.is_accumulation_range(df) is True


def test_is_accumulation_range_false_when_down_volume_wins():
    df = pd.DataFrame({
        "open":   [100.0, 102.0, 99.0],
        "close":  [102.0, 99.0,  101.0],  # green, red, green
        "volume": [100.0, 900.0, 100.0],  # up=200, down=900
    })
    assert indicators.is_accumulation_range(df) is False


def test_is_accumulation_range_false_when_tied():
    df = pd.DataFrame({
        "open":   [100.0, 100.0],
        "close":  [100.0, 100.0],  # both dojis - no up or down volume at all
        "volume": [500.0, 500.0],
    })
    assert indicators.is_accumulation_range(df) is False


def test_add_rsi_high_for_a_steady_uptrend():
    # every close higher than the last - no losses at all, RSI should sit
    # near the top of its 0-100 range.
    df = pd.DataFrame({"close": [100.0 + i for i in range(30)]})
    indicators.add_rsi(df, period=14)
    assert df["rsi14"].iloc[-1] > 90


def test_add_rsi_low_for_a_steady_downtrend():
    df = pd.DataFrame({"close": [100.0 - i for i in range(30)]})
    indicators.add_rsi(df, period=14)
    assert df["rsi14"].iloc[-1] < 10


def test_add_rsi_mid_for_a_flat_series():
    df = pd.DataFrame({"close": [100.0] * 30})
    indicators.add_rsi(df, period=14)
    # no gains and no losses -> 0/0 -> NaN, not a misleading 50 or 100
    assert pd.isna(df["rsi14"].iloc[-1])


def test_add_rsi_nan_before_period_elapses():
    df = pd.DataFrame({"close": [100.0 + i for i in range(10)]})
    indicators.add_rsi(df, period=14)
    assert pd.isna(df["rsi14"].iloc[-1])


def test_add_money_flow_volume_positive_when_closing_near_high():
    # Every candle closes at its own high (pure buying pressure) - CMF
    # should sit at its maximum, +1.
    df = pd.DataFrame({
        "high": [110.0] * 25,
        "low": [100.0] * 25,
        "close": [110.0] * 25,
        "volume": [1000.0] * 25,
    })
    indicators.add_money_flow_volume(df, period=20)
    assert df["cmf20"].iloc[-1] == pytest.approx(1.0)


def test_add_money_flow_volume_negative_when_closing_near_low():
    df = pd.DataFrame({
        "high": [110.0] * 25,
        "low": [100.0] * 25,
        "close": [100.0] * 25,
        "volume": [1000.0] * 25,
    })
    indicators.add_money_flow_volume(df, period=20)
    assert df["cmf20"].iloc[-1] == pytest.approx(-1.0)


def test_add_money_flow_volume_zero_when_closing_at_midpoint():
    df = pd.DataFrame({
        "high": [110.0] * 25,
        "low": [100.0] * 25,
        "close": [105.0] * 25,
        "volume": [1000.0] * 25,
    })
    indicators.add_money_flow_volume(df, period=20)
    assert df["cmf20"].iloc[-1] == pytest.approx(0.0)


def test_add_money_flow_volume_weights_by_volume():
    # Two candles: one closes at its high on heavy volume, the other at
    # its low on light volume - CMF should be dominated by the heavier one,
    # not a simple unweighted average of +1 and -1 (which would be 0).
    df = pd.DataFrame({
        "high": [110.0, 110.0],
        "low": [100.0, 100.0],
        "close": [110.0, 100.0],  # closes at high, then at low
        "volume": [900.0, 100.0],  # heavy volume on the up day, light on the down day
    })
    indicators.add_money_flow_volume(df, period=2)
    assert df["cmf2"].iloc[-1] == pytest.approx((900.0 - 100.0) / 1000.0)


def test_add_money_flow_volume_safe_on_flat_candle():
    # high == low (no range at all) would divide by zero - should come
    # back NaN for that candle rather than raising or producing inf.
    df = pd.DataFrame({
        "high": [100.0] * 5,
        "low": [100.0] * 5,
        "close": [100.0] * 5,
        "volume": [1000.0] * 5,
    })
    indicators.add_money_flow_volume(df, period=3)
    assert pd.isna(df["mfv"].iloc[-1])
