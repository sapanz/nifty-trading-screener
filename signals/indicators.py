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


def upper_wick_ratio(row: pd.Series) -> float:
    """(high - close) / (high - low). 0 = closed at the high, 1 = closed at the low."""
    candle_range = row["high"] - row["low"]
    if candle_range <= 0:
        return 0.0
    return (row["high"] - row["close"]) / candle_range


def is_proper_close(row: pd.Series, max_ratio: float = config.MAX_UPPER_WICK_RATIO) -> bool:
    return bool(upper_wick_ratio(row) <= max_ratio)


def is_bullish(row: pd.Series) -> bool:
    return bool(row["close"] > row["open"])


def is_volume_candle(row: pd.Series, avg_vol_column: str, multiplier: float) -> bool:
    avg = row.get(avg_vol_column)
    if avg is None or pd.isna(avg) or avg <= 0:
        return False
    return bool(row["volume"] >= avg * multiplier)


def is_above_sma(row: pd.Series, sma_column: str) -> bool:
    sma_value = row.get(sma_column)
    return bool(sma_value is not None and not pd.isna(sma_value) and row["close"] > sma_value)


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
