"""Weekly SMA-30 support strategy (runs Fridays after close).

A stock qualifies when, on the weekly timeframe:
  - it is above its 200-period SMA (long-term uptrend; the 200 SMA itself
    can be flat or rising, only price needs to be above it)
  - the 30 SMA is itself rising (not flat or falling)
  - the week's low tested the 30 SMA and closed back above it (support held)
  - the candle closed properly (small upper wick, bullish)
"""
from __future__ import annotations

import pandas as pd

from signals import config
from signals.indicators import (
    add_sma,
    is_above_sma,
    is_bullish,
    is_proper_close,
    is_sma_rising,
    is_support_test,
)
from signals.models import Signal

SMA_SUPPORT = config.SMA_SUPPORT_WEEKLY


def scan(weekly_data: dict[str, pd.DataFrame]) -> list[Signal]:
    signals: list[Signal] = []

    for symbol, raw_df in weekly_data.items():
        df = raw_df.copy()
        if len(df) < config.SMA_LONG + 2:
            continue

        add_sma(df, config.SMA_LONG)
        add_sma(df, SMA_SUPPORT)
        row = df.iloc[-1]

        if pd.isna(row.get(f"sma{config.SMA_LONG}")) or pd.isna(row.get(f"sma{SMA_SUPPORT}")):
            continue
        if not is_above_sma(row, f"sma{config.SMA_LONG}"):
            continue
        if not is_sma_rising(df, f"sma{SMA_SUPPORT}"):
            continue
        if not is_support_test(row, f"sma{SMA_SUPPORT}", config.WEEKLY_SUPPORT_TOLERANCE):
            continue
        if not (is_proper_close(row) and is_bullish(row)):
            continue

        sma_support_val = float(row[f"sma{SMA_SUPPORT}"])
        entry = float(row["close"])
        stop_loss = float(min(row["low"], sma_support_val) * (1 - config.SL_BUFFER))
        risk = entry - stop_loss
        if risk <= 0:
            continue

        support_gap = abs(row["low"] - sma_support_val) / sma_support_val
        targets = [round(entry + risk * mult, 2) for mult in config.RISK_REWARD_TARGETS]

        signals.append(
            Signal(
                symbol=symbol,
                entry=round(entry, 2),
                stop_loss=round(stop_loss, 2),
                targets=targets,
                sort_key=-support_gap,  # tightest support test first
                note=f"SMA30 {sma_support_val:.2f}",
            )
        )

    signals.sort(key=lambda s: s.sort_key, reverse=True)
    return signals
