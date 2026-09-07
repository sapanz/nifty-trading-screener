"""Reusable technical-indicator and candle-quality helpers.

All the "add_*" functions take an OHLCV DataFrame (columns open/high/low/
close/volume) and return it with extra columns appended. The scalar
predicate helpers operate on a single row (as produced by ``df.iloc[-1]``)
and encode the discretionary judgement calls a trader would normally make
by eye - "did this candle close properly", etc.
"""
from __future__ import annotations

import pandas as pd

from signals import config


def add_sma(df: pd.DataFrame, period: int, column: str = "close") -> pd.DataFrame:
    df[f"sma{period}"] = df[column].rolling(period).mean()
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


def is_volume_candle(row: pd.Series, avg_vol_column: str, multiplier: float) -> bool:
    avg = row.get(avg_vol_column)
    if avg is None or pd.isna(avg) or avg <= 0:
        return False
    return bool(row["volume"] >= avg * multiplier)


def is_above_sma(row: pd.Series, sma_column: str) -> bool:
    sma_value = row.get(sma_column)
    return bool(sma_value is not None and not pd.isna(sma_value) and row["close"] > sma_value)
