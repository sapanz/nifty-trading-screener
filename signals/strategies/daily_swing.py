"""Daily swing-trading strategy (runs every trading day after close).

A stock qualifies when, on the daily timeframe:
  - it is above its 200 SMA (long-term uptrend; the 200 SMA itself can be
    flat or rising, only price needs to be above it)
  - the 44 SMA is itself rising (not flat or falling)
  - price is testing support at the 44 SMA, closing back above it
  - the 44 SMA and the lower Bollinger Band sit right on top of each other
    (a confluence of two independent support levels, not just one)
  - the candle closed properly (small upper wick, bullish)
  - volume is at or above its own average, and price already sits a healthy
    distance above the 200 SMA (see DAILY_SWING_MIN_VOL_RATIO /
    DAILY_SWING_MIN_DIST_FROM_SMA200_PCT) - both found by mining a 5-year
    backtest's diagnostic columns: below-average-volume pullbacks were net
    losers, and pullbacks still close to the 200 SMA underperformed ones
    with more established trend beneath them

Entry is the signal candle's high; stop-loss is the lower of the signal
candle's own low and the previous candle's low.
"""
from __future__ import annotations

import pandas as pd

from signals import config
from signals.indicators import (
    add_avg_volume,
    add_bollinger_bands,
    add_rsi,
    add_sma,
    confluence_gap,
    is_above_sma,
    is_bullish,
    is_proper_close,
    is_sma_rising,
    is_support_test,
)
from signals.models import Signal

SMA_SWING = config.SMA_SWING


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
        add_rsi(df)
        row = df.iloc[-1]

        if pd.isna(row.get(f"sma{config.SMA_LONG}")) or pd.isna(row.get(f"sma{SMA_SWING}")) or pd.isna(row.get("bb_lower")):
            continue
        if not is_above_sma(row, f"sma{config.SMA_LONG}"):
            continue
        if not is_sma_rising(df, f"sma{SMA_SWING}"):
            continue
        if not is_support_test(row, f"sma{SMA_SWING}", config.DAILY_SUPPORT_TOLERANCE):
            continue
        if not (is_proper_close(row) and is_bullish(row)):
            continue

        sma_swing_val = float(row[f"sma{SMA_SWING}"])
        bb_lower_val = float(row["bb_lower"])
        gap = confluence_gap(sma_swing_val, bb_lower_val)
        if gap > config.CONFLUENCE_TOLERANCE:
            continue  # SMA44 and lower BB aren't close enough to call it confluence
        if not row["low"] <= bb_lower_val * (1 + config.DAILY_SUPPORT_TOLERANCE):
            continue  # candle didn't actually reach down to the lower band too

        prev_row = df.iloc[-2]
        entry = float(row["high"])
        stop_loss = float(min(row["low"], prev_row["low"]))
        risk = entry - stop_loss
        if risk <= 0:
            continue

        targets = [round(entry + risk * mult, 2) for mult in config.RISK_REWARD_TARGETS]

        # Volume and trend-extension confirm the pullback is being bought,
        # not just drifting back up on thin interest - found by mining a
        # 5-year backtest's diagnostic columns (logged but not gated on
        # until now): trades with below-average volume were net losers
        # (PF 0.91), and trades still close to the 200 SMA (a weaker, newer
        # uptrend) underperformed those with real room already built up.
        # Neither filter touches entry/stop/target, so it doesn't widen risk
        # - it only trims which setups get taken (PF 1.19 -> 1.39 in
        # backtest, retaining ~16% of signals).
        sma_long_val = float(row[f"sma{config.SMA_LONG}"])
        avg_vol_val = row.get(f"avg_vol{config.VOLUME_LOOKBACK}")
        vol_ratio = float(row["volume"] / avg_vol_val) if pd.notna(avg_vol_val) and avg_vol_val > 0 else None
        if vol_ratio is None or vol_ratio < config.DAILY_SWING_MIN_VOL_RATIO:
            continue
        dist_from_sma200_pct = (entry / sma_long_val - 1) * 100
        if dist_from_sma200_pct < config.DAILY_SWING_MIN_DIST_FROM_SMA200_PCT:
            continue
        rsi_val = row.get("rsi14")

        signals.append(
            Signal(
                symbol=symbol,
                entry=round(entry, 2),
                stop_loss=round(stop_loss, 2),
                targets=targets,
                sort_key=-gap,  # tightest SMA44/lower-BB confluence first
                note=f"SMA44 {sma_swing_val:.2f} / LowerBB {bb_lower_val:.2f}",
                candle_date=row.name.date(),
                extra={
                    "confluence_gap_pct": round(gap * 100, 3),
                    "dist_from_sma200_pct": round(dist_from_sma200_pct, 2),
                    "vol_ratio": round(vol_ratio, 2),
                    **({"rsi14": round(float(rsi_val), 1)} if pd.notna(rsi_val) else {}),
                },
            )
        )

    signals.sort(key=lambda s: s.sort_key, reverse=True)
    return signals
