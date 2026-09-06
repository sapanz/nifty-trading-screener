"""Weekly SMA-30 support strategy (runs Fridays after close).

A stock qualifies when, on the weekly timeframe:
  - it is above its 200-period SMA (long-term uptrend)
  - the week's low tested the 30 SMA and closed back above it (support held)
  - the candle closed properly (small upper wick, bullish)
  - volume was elevated (institutional participation on the bounce)
"""
from __future__ import annotations

import pandas as pd

from signals import config
from signals.indicators import (
    add_avg_volume,
    add_sma,
    is_above_sma,
    is_bullish,
    is_proper_close,
    is_support_test,
    is_volume_candle,
)
from signals.models import Signal

SMA_SUPPORT = config.SMA_SUPPORT_WEEKLY
VOL_COL = f"avg_vol{config.VOLUME_LOOKBACK}"


def scan(weekly_data: dict[str, pd.DataFrame]) -> list[Signal]:
    signals: list[Signal] = []

    for symbol, raw_df in weekly_data.items():
        df = raw_df.copy()
        if len(df) < config.SMA_LONG + 2:
            continue

        add_sma(df, config.SMA_LONG)
        add_sma(df, SMA_SUPPORT)
        add_avg_volume(df, config.VOLUME_LOOKBACK)
        row = df.iloc[-1]

        if pd.isna(row.get(f"sma{config.SMA_LONG}")) or pd.isna(row.get(f"sma{SMA_SUPPORT}")):
            continue
        if not is_above_sma(row, f"sma{config.SMA_LONG}"):
            continue
        if not is_support_test(row, f"sma{SMA_SUPPORT}", config.WEEKLY_SUPPORT_TOLERANCE):
            continue
        if not (is_proper_close(row) and is_bullish(row)):
            continue
        if not is_volume_candle(row, VOL_COL, config.WEEKLY_VOLUME_MULTIPLIER):
            continue

        entry = float(row["close"])
        stop_loss = float(min(row["low"], row[f"sma{SMA_SUPPORT}"]) * (1 - config.SL_BUFFER))
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
                note=f"Vol {vol_ratio:.1f}x avg | SMA30 {row[f'sma{SMA_SUPPORT}']:.2f}",
            )
        )

    signals.sort(key=lambda s: s.sort_key, reverse=True)
    return signals
