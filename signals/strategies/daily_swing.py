"""Daily swing-trading strategy (runs every trading day after close).

A stock qualifies when, on the daily timeframe:
  - it is above its 200 SMA (long-term uptrend)
  - price is testing support at the 44 SMA, closing back above it
  - the 44 SMA and the lower Bollinger Band sit right on top of each other
    (a confluence of two independent support levels, not just one)
  - the candle closed properly (small upper wick, bullish) on strong volume
"""
from __future__ import annotations

import pandas as pd

from signals import config
from signals.indicators import (
    add_avg_volume,
    add_bollinger_bands,
    add_sma,
    confluence_gap,
    is_above_sma,
    is_bullish,
    is_proper_close,
    is_support_test,
    is_volume_candle,
)
from signals.models import Signal

SMA_SWING = config.SMA_SWING
VOL_COL = f"avg_vol{config.VOLUME_LOOKBACK}"


def scan(daily_data: dict[str, pd.DataFrame]) -> list[Signal]:
    signals: list[Signal] = []

    for symbol, raw_df in daily_data.items():
        df = raw_df.copy()
        if len(df) < config.SMA_LONG + 2:
            continue

        add_sma(df, config.SMA_LONG)
        add_sma(df, SMA_SWING)
        add_bollinger_bands(df)
        add_avg_volume(df, config.VOLUME_LOOKBACK)
        row = df.iloc[-1]

        if pd.isna(row.get(f"sma{config.SMA_LONG}")) or pd.isna(row.get(f"sma{SMA_SWING}")) or pd.isna(row.get("bb_lower")):
            continue
        if not is_above_sma(row, f"sma{config.SMA_LONG}"):
            continue
        if not is_support_test(row, f"sma{SMA_SWING}", config.DAILY_SUPPORT_TOLERANCE):
            continue
        if not (is_proper_close(row) and is_bullish(row)):
            continue
        if not is_volume_candle(row, VOL_COL, config.DAILY_VOLUME_MULTIPLIER):
            continue

        sma_swing_val = float(row[f"sma{SMA_SWING}"])
        bb_lower_val = float(row["bb_lower"])
        if confluence_gap(sma_swing_val, bb_lower_val) > config.CONFLUENCE_TOLERANCE:
            continue  # SMA44 and lower BB aren't close enough to call it confluence
        if not row["low"] <= bb_lower_val * (1 + config.DAILY_SUPPORT_TOLERANCE):
            continue  # candle didn't actually reach down to the lower band too

        entry = float(row["close"])
        stop_loss = float(min(row["low"], sma_swing_val, bb_lower_val) * (1 - config.SL_BUFFER))
        risk = entry - stop_loss
        if risk <= 0:
            continue

        vol_ratio = float(row["volume"] / row[VOL_COL])
        targets = [round(entry + risk * mult, 2) for mult in config.RISK_REWARD_TARGETS]

        signals.append(
            Signal(
                symbol=symbol,
                entry=round(entry, 2),
                stop_loss=round(stop_loss, 2),
                targets=targets,
                sort_key=vol_ratio,
                note=f"Vol {vol_ratio:.1f}x avg | SMA44 {sma_swing_val:.2f} / LowerBB {bb_lower_val:.2f}",
            )
        )

    signals.sort(key=lambda s: s.sort_key, reverse=True)
    return signals
