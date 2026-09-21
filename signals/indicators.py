"""Reusable technical-indicator and candle-quality helpers.

All the "add_*" functions take an OHLCV DataFrame (columns open/high/low/
close/volume) and return it with extra columns appended. The scalar
predicate helpers operate on a single row (as produced by ``df.iloc[-1]``)
and encode the discretionary judgement calls a trader would normally make
by eye - "did this candle close properly", "is this a support test", etc.
"""
from __future__ import annotations

import pandas as pd

from signals import config


def add_sma(df: pd.DataFrame, period: int, column: str = "close") -> pd.DataFrame:
    df[f"sma{period}"] = df[column].rolling(period).mean()
    return df


def add_bollinger_bands(
    df: pd.DataFrame, period: int = config.BOLLINGER_PERIOD, num_std: float = config.BOLLINGER_STD
) -> pd.DataFrame:
    mid = df["close"].rolling(period).mean()
    std = df["close"].rolling(period).std()
    df["bb_mid"] = mid
    df["bb_upper"] = mid + num_std * std
    df["bb_lower"] = mid - num_std * std
    return df


def add_avg_volume(df: pd.DataFrame, lookback: int) -> pd.DataFrame:
    # shift(1) so today's own volume never inflates its own baseline
    df[f"avg_vol{lookback}"] = df["volume"].shift(1).rolling(lookback).mean()
    return df


def add_rsi(df: pd.DataFrame, period: int = 14, column: str = "close") -> pd.DataFrame:
    """Wilder's RSI - a diagnostic, not currently gated on by any strategy.
    Logged alongside each signal purely so a backtest's trade CSV can be
    mined for whether RSI level correlates with outcome, instead of
    guessing blind through repeated backtest round-trips."""
    delta = df[column].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss
    df[f"rsi{period}"] = 100 - (100 / (1 + rs))
    return df


def upper_wick_ratio(row: pd.Series) -> float:
    """(high - close) / (high - low). 0 = closed at the high, 1 = closed at the low."""
    candle_range = row["high"] - row["low"]
    if candle_range <= 0:
        return 0.0
    return (row["high"] - row["close"]) / candle_range


def lower_wick_ratio(row: pd.Series) -> float:
    """(close - low) / (high - low). 0 = closed at the low, 1 = closed at the high -
    the bearish mirror of upper_wick_ratio, for judging a short signal
    candle's own close quality (closed near its low, not bought back up)."""
    candle_range = row["high"] - row["low"]
    if candle_range <= 0:
        return 0.0
    return (row["close"] - row["low"]) / candle_range


def is_proper_close(row: pd.Series, max_ratio: float = config.MAX_UPPER_WICK_RATIO) -> bool:
    return bool(upper_wick_ratio(row) <= max_ratio)


def is_proper_close_bearish(row: pd.Series, max_ratio: float = config.MAX_UPPER_WICK_RATIO) -> bool:
    """Mirror of is_proper_close for a short signal candle: the close sits
    in the bottom max_ratio of the day's range (small lower wick) instead
    of the top - i.e. sellers were still in control into the close, not
    getting bought back up off the lows."""
    return bool(lower_wick_ratio(row) <= max_ratio)


def is_bullish(row: pd.Series) -> bool:
    return bool(row["close"] > row["open"])


def is_bearish(row: pd.Series) -> bool:
    return bool(row["close"] < row["open"])


def is_volume_candle(row: pd.Series, avg_vol_column: str, multiplier: float) -> bool:
    avg = row.get(avg_vol_column)
    if avg is None or pd.isna(avg) or avg <= 0:
        return False
    return bool(row["volume"] >= avg * multiplier)


def is_above_sma(row: pd.Series, sma_column: str) -> bool:
    sma_value = row.get(sma_column)
    return bool(sma_value is not None and not pd.isna(sma_value) and row["close"] > sma_value)


def is_below_sma(row: pd.Series, sma_column: str) -> bool:
    sma_value = row.get(sma_column)
    return bool(sma_value is not None and not pd.isna(sma_value) and row["close"] < sma_value)


def is_support_test(row: pd.Series, sma_column: str, tolerance: float) -> bool:
    """Low dipped to (or just above) the MA and price closed back above it."""
    sma_value = row.get(sma_column)
    if sma_value is None or pd.isna(sma_value) or sma_value <= 0:
        return False
    touched = row["low"] <= sma_value * (1 + tolerance)
    held = row["close"] > sma_value
    return bool(touched and held)


def confluence_gap(value_a: float, value_b: float) -> float:
    """Relative distance between two support levels, e.g. SMA44 vs lower BB."""
    if value_a <= 0 or value_b <= 0:
        return float("inf")
    return abs(value_a - value_b) / min(value_a, value_b)


def is_accumulation_range(range_df: pd.DataFrame) -> bool:
    """True if the range's up (green) candles carried more volume than its
    down (red) candles - i.e. buyers were more aggressive than sellers while
    the range built, a classic accumulation signature rather than mere
    sideways drift or distribution."""
    up_volume = range_df.loc[range_df["close"] > range_df["open"], "volume"].sum()
    down_volume = range_df.loc[range_df["close"] < range_df["open"], "volume"].sum()
    return bool(up_volume > down_volume)


def is_distribution_range(range_df: pd.DataFrame) -> bool:
    """Bearish mirror of is_accumulation_range: True if the range's down
    (red) candles carried more volume than its up (green) ones - sellers
    more aggressive than buyers while the range built, the short-side
    equivalent of accumulation ahead of a breakdown."""
    up_volume = range_df.loc[range_df["close"] > range_df["open"], "volume"].sum()
    down_volume = range_df.loc[range_df["close"] < range_df["open"], "volume"].sum()
    return bool(down_volume > up_volume)


def is_sma_rising(df: pd.DataFrame, sma_column: str, lookback: int = config.SMA_SLOPE_LOOKBACK) -> bool:
    """True if the SMA's latest value is higher than it was `lookback` periods ago.

    Operates on the full column (not a single row) since a slope needs more
    than one point - comparing against a few periods back rather than just
    the prior one smooths out single-bar noise.
    """
    if len(df) <= lookback:
        return False
    series = df[sma_column]
    current = series.iloc[-1]
    prior = series.iloc[-1 - lookback]
    if pd.isna(current) or pd.isna(prior):
        return False
    return bool(current > prior)
