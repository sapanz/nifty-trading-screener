"""Daily Swing strategy: Darvas Box breakout + technical CANSLIM (runs
every trading day after close).

A stock qualifies when, on the daily timeframe:
  - the overall market isn't weak: at least DAILY_SWING_MIN_MARKET_BREADTH
    of the scanned universe is trading above its own 200 SMA ("M" - don't
    fight the tape; checked once per scan, not per symbol)
  - the stock ranks in the top DAILY_SWING_RS_TOP_PERCENTILE of the
    scanned universe by trailing DAILY_SWING_RS_LOOKBACK_DAYS return
    ("L" - only trade leaders, not laggards with an otherwise-decent chart)
  - it is above its own 200 SMA (long-term uptrend)
  - the DARVAS_BOX_MIN_DAYS-DARVAS_BOX_MAX_DAYS candles right before today
    formed a tight (DARVAS_BOX_TIGHTNESS) box whose top is itself a fresh
    DARVAS_NEW_HIGH_LOOKBACK-day high ("N" - new high)
  - today's candle closed above the box top on volume ("S" - the breakout
    itself is the supply/demand signal), with a proper close

See signals/config.py for the full rationale, including why CANSLIM's
fundamentals-only legs (C, A, I) are left out.
"""
from __future__ import annotations

import pandas as pd

from signals import config
from signals.indicators import add_avg_volume, add_sma, is_above_sma, is_proper_close, is_volume_candle
from signals.models import Signal

VOL_COL = f"avg_vol{config.VOLUME_LOOKBACK}"


def _market_breadth(daily_data: dict[str, pd.DataFrame]) -> float:
    """Fraction of the scanned universe trading above its own 200 SMA - a
    simple proxy for O'Neil's "M" (overall market direction)."""
    above = 0
    total = 0
    for df in daily_data.values():
        if len(df) < config.SMA_LONG:
            continue
        sma = df["close"].rolling(config.SMA_LONG).mean().iloc[-1]
        if pd.isna(sma):
            continue
        total += 1
        if df["close"].iloc[-1] > sma:
            above += 1
    return above / total if total else 0.0


def _leading_symbols(daily_data: dict[str, pd.DataFrame]) -> set[str]:
    """The top DAILY_SWING_RS_TOP_PERCENTILE of the scanned universe by
    trailing DAILY_SWING_RS_LOOKBACK_DAYS return - O'Neil's "L", only
    trade market leaders."""
    lookback = config.DAILY_SWING_RS_LOOKBACK_DAYS
    returns: dict[str, float] = {}
    for symbol, df in daily_data.items():
        if len(df) <= lookback:
            continue
        past = float(df["close"].iloc[-1 - lookback])
        if past <= 0:
            continue
        returns[symbol] = df["close"].iloc[-1] / past - 1

    if not returns:
        return set()

    cutoff = max(1, int(len(returns) * config.DAILY_SWING_RS_TOP_PERCENTILE))
    ranked = sorted(returns.items(), key=lambda kv: kv[1], reverse=True)
    return {symbol for symbol, _ in ranked[:cutoff]}


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

    if _market_breadth(daily_data) < config.DAILY_SWING_MIN_MARKET_BREADTH:
        return signals  # M: don't fight a weak overall market

    min_len = config.SMA_LONG + config.DARVAS_NEW_HIGH_LOOKBACK + config.DARVAS_BOX_MAX_DAYS + 2

    for symbol in _leading_symbols(daily_data):  # L: only the strongest stocks
        df = daily_data[symbol].copy()
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
                note=f"Darvas box {box_bottom:.2f}-{box_top:.2f} | Vol {vol_ratio:.1f}x avg | RS leader",
            )
        )

    signals.sort(key=lambda s: s.sort_key, reverse=True)
    return signals
