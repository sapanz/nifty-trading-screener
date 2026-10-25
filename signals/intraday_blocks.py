"""Order blocks on today's 5-minute candles - the dashboard's intraday setup list.

The setup the user describes: a stock is running up, pulls back with a small red
candle, then runs again - the red candle (the last down candle before the
structure-breaking thrust) is a bullish order block, and its low is a tight stop.
Detection is signals/orderblocks.py on 5-minute bars; this module adds what is
useful live: where each zone stands *now*.

  fresh    the thrust happened and price has not come back yet - the zone to wait for
  testing  price reached the zone within the last INTRADAY_OB_RECENT_BARS bars and has
           not traded through the far edge (plus stop buffer) - the retest is happening
Zones already retested earlier, stopped out, or older than INTRADAY_OB_EXPIRY_BARS are dropped.
Yesterday's session is passed in only as warm-up (ATR, earlier swing levels); only blocks
that formed today are reported. The still-forming last candle is ignored.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from signals import config
from signals.orderblocks import OrderBlock, find_order_blocks, first_touch

BAR_MINUTES = 5


def block_state(block: OrderBlock, high: np.ndarray, low: np.ndarray, open_: np.ndarray) -> str | None:
    n = len(low)
    expiry, recent = config.INTRADAY_OB_EXPIRY_BARS, config.INTRADAY_OB_RECENT_BARS
    if n - 1 - block.formed_idx > expiry:
        return None
    t = first_touch(block, open_, high, low, expiry)
    if t is None:
        return "fresh"
    buf = config.OB_STOP_BUFFER_PCT / 100
    if block.direction == "bull" and (low[t:] <= block.low * (1 - buf)).any():
        return None
    if block.direction == "bear" and (high[t:] >= block.high * (1 + buf)).any():
        return None
    return "testing" if n - 1 - t <= recent else None


def _record(block: OrderBlock, state: str, times) -> dict:
    return {"d": block.direction, "st": state, "lo": round(block.low, 2), "hi": round(block.high, 2),
            "t": times[block.idx].strftime("%H:%M"), "imp": round(block.impulse_atr, 1)}


def scan_symbol(candles: pd.DataFrame, day: pd.Timestamp, now: pd.Timestamp | None = None) -> list[dict]:
    """Best bullish and best bearish zone (testing beats fresh, then the most recent) as of the
    last completed bar; candles are IST wall-clock bar starts, today's bars preceded by warm-up."""
    if candles is None or len(candles) < 30:
        return []
    if now is not None:
        candles = candles[candles.index + pd.Timedelta(minutes=BAR_MINUTES) <= now]
    if len(candles) < 30:
        return []
    o, h, l, c = (candles[col].to_numpy(dtype=float) for col in ("open", "high", "low", "close"))
    times = candles.index
    first_today = int(np.argmax(times.normalize() == day.normalize())) if (times.normalize() == day.normalize()).any() else None
    if first_today is None:
        return []
    best: dict[str, tuple] = {}
    for block in find_order_blocks(o, h, l, c, impulse_atr=config.INTRADAY_OB_IMPULSE_ATR):
        if block.formed_idx < first_today:
            continue
        state = block_state(block, h, l, o)
        if state is None:
            continue
        rank = (state == "testing", block.formed_idx)
        if block.direction not in best or rank > best[block.direction][0]:
            best[block.direction] = (rank, _record(block, state, times))
    return [rec for _, rec in best.values()]
