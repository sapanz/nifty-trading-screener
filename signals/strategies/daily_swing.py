"""Daily Swing strategy: SMA-30 support in all-time-high stocks (runs
every trading day after close).

A stock qualifies when, on the daily timeframe:
  - its close is within DAILY_SWING_ATH_TOLERANCE of its own all-time-high
    close (within the fetched history) - only trade stocks that are
    already leaders, not laggards that happen to sit near their own SMA
  - today's candle is bullish (close > open) and takes support at the
    DAILY_SWING_SMA_SUPPORT-day SMA: its low comes within
    DAILY_SWING_SUPPORT_TOLERANCE above the SMA (doesn't need to touch it
    exactly) and its close is back above it

Entry is today's high (a buy-stop triggered the next day price trades up
to it); stop-loss is the lower of today's own low and the previous
candle's low; the target is a fixed 1:3 risk-reward.
"""
from __future__ import annotations

import pandas as pd

from signals import config
from signals.indicators import add_sma
from signals.models import Signal

SMA_COL = f"sma{config.DAILY_SWING_SMA_SUPPORT}"


def _is_support_test(row: pd.Series, tolerance: float) -> bool:
    sma_val = row.get(SMA_COL)
    if sma_val is None or pd.isna(sma_val) or sma_val <= 0:
        return False
    return bool(row["low"] <= sma_val * (1 + tolerance) and row["close"] > sma_val)


def scan(daily_data: dict[str, pd.DataFrame]) -> list[Signal]:
    signals: list[Signal] = []

    for symbol, raw_df in daily_data.items():
        df = raw_df.copy()
        if len(df) < config.DAILY_SWING_MIN_HISTORY_DAYS:
            continue

        add_sma(df, config.DAILY_SWING_SMA_SUPPORT)
        row = df.iloc[-1]
        prev_row = df.iloc[-2]

        if pd.isna(row.get(SMA_COL)):
            continue

        all_time_high = float(df["close"].max())
        if all_time_high <= 0 or row["close"] < all_time_high * (1 - config.DAILY_SWING_ATH_TOLERANCE):
            continue

        if not row["close"] > row["open"]:  # must be a green candle
            continue
        if not _is_support_test(row, config.DAILY_SWING_SUPPORT_TOLERANCE):
            continue

        entry = float(row["high"])
        stop_loss = float(min(row["low"], prev_row["low"]))
        risk = entry - stop_loss
        if risk <= 0:
            continue

        targets = [round(entry + risk * mult, 2) for mult in config.DAILY_SWING_RISK_REWARD_TARGETS]
        ath_pct = row["close"] / all_time_high * 100

        signals.append(
            Signal(
                symbol=symbol,
                entry=round(entry, 2),
                stop_loss=round(stop_loss, 2),
                targets=targets,
                sort_key=ath_pct,  # closer to its all-time high sorts first
                note=f"SMA{config.DAILY_SWING_SMA_SUPPORT} support | {ath_pct:.0f}% of ATH",
            )
        )

    signals.sort(key=lambda s: s.sort_key, reverse=True)
    return signals
