"""Daily Swing strategy: Darvas Box breakout (runs every trading day
after close).

A stock qualifies when, on the daily timeframe:
  - it is above its own 200 SMA (long-term uptrend)
  - the DARVAS_BOX_MIN_DAYS-DARVAS_BOX_MAX_DAYS candles right before today
    formed a tight (DARVAS_BOX_TIGHTNESS) box whose top is itself a fresh
    DARVAS_NEW_HIGH_LOOKBACK-day high - Darvas only ever bought stocks
    making new highs, so a box that isn't sitting at one isn't a genuine
    Darvas box
  - today's candle closed above the box top on volume, with a proper close

Entry is the breakout candle's close; stop-loss sits below the box
bottom, per Darvas's own rule - the stop lives below the whole
consolidation, not just the breakout level.
"""
from __future__ import annotations

import pandas as pd

from signals import config
from signals.indicators import add_avg_volume, add_sma, is_above_sma, is_proper_close, is_volume_candle
from signals.models import Signal

VOL_COL = f"avg_vol{config.VOLUME_LOOKBACK}"


def _find_darvas_box(df: pd.DataFrame) -> tuple[float, float] | None:
    """Look for a valid Darvas box (a tight, fresh-new-high consolidation)
    ending right before today. Returns (box_top, box_bottom) for the
    shortest qualifying box, or None if no box between DARVAS_BOX_MIN_DAYS
    and DARVAS_BOX_MAX_DAYS qualifies. Whether today actually breaks out
    of it is checked separately by the caller."""
    for box_days in range(config.DARVAS_BOX_MIN_DAYS, config.DARVAS_BOX_MAX_DAYS + 1):
        box_window = df.iloc[-1 - box_days : -1]
        if len(box_window) < box_days:
            continue

        box_top = float(box_window["high"].max())
        box_bottom = float(box_window["low"].min())
        if box_bottom <= 0:
            continue
        if (box_top - box_bottom) / box_bottom > config.DARVAS_BOX_TIGHTNESS:
            continue

        prior_start = -(1 + box_days + config.DARVAS_NEW_HIGH_LOOKBACK)
        prior_history = df.iloc[prior_start : -1 - box_days]
        if not prior_history.empty:
            prior_high = float(prior_history["high"].max())
            if box_top < prior_high * (1 - config.DARVAS_NEW_HIGH_TOLERANCE):
                continue  # the box didn't actually form at a fresh high

        return box_top, box_bottom
    return None


def scan(daily_data: dict[str, pd.DataFrame]) -> list[Signal]:
    signals: list[Signal] = []
    min_len = config.SMA_LONG + config.DARVAS_NEW_HIGH_LOOKBACK + config.DARVAS_BOX_MAX_DAYS + 2

    for symbol, raw_df in daily_data.items():
        df = raw_df.copy()
        if len(df) < min_len:
            continue

        add_sma(df, config.SMA_LONG)
        add_avg_volume(df, config.VOLUME_LOOKBACK)
        row = df.iloc[-1]

        if pd.isna(row.get(f"sma{config.SMA_LONG}")):
            continue
        if not is_above_sma(row, f"sma{config.SMA_LONG}"):
            continue
        if not is_proper_close(row):
            continue
        if not is_volume_candle(row, VOL_COL, config.DAILY_SWING_VOLUME_MULTIPLIER):
            continue

        box = _find_darvas_box(df)
        if box is None:
            continue
        box_top, box_bottom = box

        if not row["close"] > box_top:
            continue

        entry = float(row["close"])
        stop_loss = float(box_bottom * (1 - config.SL_BUFFER))
        risk = entry - stop_loss
        if risk <= 0:
            continue

        targets = [round(entry + risk * mult, 2) for mult in config.DAILY_SWING_RISK_REWARD_TARGETS]
        vol_ratio = float(row["volume"] / row[VOL_COL])

        signals.append(
            Signal(
                symbol=symbol,
                entry=round(entry, 2),
                stop_loss=round(stop_loss, 2),
                targets=targets,
                sort_key=vol_ratio,
                note=f"Darvas box {box_bottom:.2f}-{box_top:.2f} | Vol {vol_ratio:.1f}x avg",
            )
        )

    signals.sort(key=lambda s: s.sort_key, reverse=True)
    return signals
