"""Daily Swing strategy: SMA-30 support in all-time-high stocks (runs
every trading day after close).

A stock qualifies when, on the daily timeframe:
  - its close is within DAILY_SWING_ATH_TOLERANCE of its own all-time-high
    close (within the fetched history) - only trade stocks that are
    already leaders, not laggards that happen to sit near their own SMA
  - the DAILY_SWING_SMA_SUPPORT-day SMA is itself higher than it was
    DAILY_SWING_SMA_SLOPE_LOOKBACK periods back - a genuine uptrend, not
    a flat or declining average that a bounce would otherwise misread as
    "support"
  - today's candle is bullish (close > open), closed properly (small
    upper wick), and takes support at that SMA: its low comes within
    DAILY_SWING_SUPPORT_TOLERANCE above the SMA (doesn't need to touch it
    exactly) and its close is back above it
  - today's volume is below DAILY_SWING_MAX_VOLUME_RATIO x its trailing
    average - a quiet pullback with light selling, not a panic dump that
    happens to close green

Entry is today's high (a buy-stop triggered the next day price trades up
to it); stop-loss is the lower of today's own low and the previous
candle's low; the target is a fixed 1:3 risk-reward.
"""
from __future__ import annotations

import pandas as pd

from signals import config
from signals.indicators import add_avg_volume, add_sma, is_proper_close
from signals.models import Signal

SMA_COL = f"sma{config.DAILY_SWING_SMA_SUPPORT}"
VOL_COL = f"avg_vol{config.VOLUME_LOOKBACK}"


def _is_support_test(row: pd.Series, tolerance: float) -> bool:
    sma_val = row.get(SMA_COL)
    if sma_val is None or pd.isna(sma_val) or sma_val <= 0:
        return False
    return bool(row["low"] <= sma_val * (1 + tolerance) and row["close"] > sma_val)


def _is_sma_rising(df: pd.DataFrame, lookback: int) -> bool:
    sma = df[SMA_COL]
    if len(sma) <= lookback:
        return False
    current, prior = sma.iloc[-1], sma.iloc[-1 - lookback]
    if pd.isna(current) or pd.isna(prior):
        return False
    return bool(current > prior)


def scan(daily_data: dict[str, pd.DataFrame]) -> list[Signal]:
    signals: list[Signal] = []

    for symbol, raw_df in daily_data.items():
        df = raw_df.copy()
        if len(df) < config.DAILY_SWING_MIN_HISTORY_DAYS:
            continue

        add_sma(df, config.DAILY_SWING_SMA_SUPPORT)
        add_avg_volume(df, config.VOLUME_LOOKBACK)
        row = df.iloc[-1]
        prev_row = df.iloc[-2]

        if pd.isna(row.get(SMA_COL)):
            continue

        all_time_high = float(df["close"].max())
        if all_time_high <= 0 or row["close"] < all_time_high * (1 - config.DAILY_SWING_ATH_TOLERANCE):
            continue

        if not _is_sma_rising(df, config.DAILY_SWING_SMA_SLOPE_LOOKBACK):
            continue
        if not row["close"] > row["open"]:  # must be a green candle
            continue
        if not is_proper_close(row):
            continue
        if not _is_support_test(row, config.DAILY_SWING_SUPPORT_TOLERANCE):
            continue

        avg_vol = row.get(VOL_COL)
        if avg_vol is None or pd.isna(avg_vol) or avg_vol <= 0:
            continue
        if row["volume"] >= avg_vol * config.DAILY_SWING_MAX_VOLUME_RATIO:
            continue  # a heavy-volume day here is selling pressure, not a quiet pullback

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
