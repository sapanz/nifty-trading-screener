"""Order blocks on OHLC arrays (daily or intraday).

An order block is the last candle going the OTHER way before a strong,
structure-breaking move:

  bullish: the last down (bearish) candle before an up-move that closes above
           a recent swing high (a break of structure); its high-low range is
           the demand zone, where price is expected to find buyers on a return.
  bearish: the mirror - the last up candle before a down-move that closes below
           a recent swing low; its range is the supply zone.

Precisely (so it can be tested, and argued with):
  * swing highs / lows: a bar higher (lower) than the OB_SWING_K bars either side;
    known only OB_SWING_K bars later - a break can only use swings already known;
  * break of structure at bar i: close[i] crosses the latest known swing level
    for the first time (the previous close was still on the other side);
  * the leg: the extreme (lowest low for a bullish block) of the last OB_MAX_LEG_BARS
    bars up to i; the move from it to close[i] must be at least OB_IMPULSE_ATR x
    ATR(14) - a displacement, not drift;
  * the block: that extreme bar if it is a down candle, else the nearest down
    candle within OB_BLOCK_LOOKBACK bars before it (none -> no block).
A block is "mitigated" once price trades back into its zone, and stops being
useful after OB_EXPIRY_BARS.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from signals import config


@dataclass
class OrderBlock:
    direction: str        # "bull" (demand zone) or "bear" (supply zone)
    idx: int              # the order-block candle
    formed_idx: int       # the bar whose close broke structure (when the block became known)
    low: float            # zone: the candle's low ...
    high: float           # ... to its high
    impulse_atr: float    # size of the displacement leg in ATRs

    @property
    def width_pct(self) -> float:
        return (self.high - self.low) / self.high * 100


def atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    prev = np.concatenate([[np.nan], close[:-1]])
    tr = np.fmax.reduce([high - low, np.abs(high - prev), np.abs(low - prev)])
    return pd.Series(tr).ewm(alpha=1 / period, adjust=False, min_periods=period).mean().to_numpy()


def _pivots(values: np.ndarray, k: int, highs: bool) -> list[int]:
    out = []
    for i in range(k, len(values) - k):
        left, right = values[i - k:i], values[i + 1:i + k + 1]
        if highs and values[i] > left.max() and values[i] >= right.max():
            out.append(i)
        elif not highs and values[i] < left.min() and values[i] <= right.min():
            out.append(i)
    return out


def find_order_blocks(
    open_: np.ndarray, high: np.ndarray, low: np.ndarray, close: np.ndarray,
    swing_k: int | None = None, impulse_atr: float | None = None,
) -> list[OrderBlock]:
    """Every order block formed in the series, in the order they became known."""
    k = swing_k or config.OB_SWING_K
    need = impulse_atr or config.OB_IMPULSE_ATR
    n = len(close)
    a = atr(high, low, close)
    swing_hi, swing_lo = _pivots(high, k, True), _pivots(low, k, False)
    blocks: list[OrderBlock] = []
    seen: set[tuple[str, int]] = set()
    hi_ptr = lo_ptr = 0
    level_hi = level_lo = None
    for i in range(2 * k + 1, n):
        # swings confirmed by bar i-1 (pivot index + k <= i-1)
        while hi_ptr < len(swing_hi) and swing_hi[hi_ptr] + k <= i - 1:
            level_hi = high[swing_hi[hi_ptr]]; hi_ptr += 1
        while lo_ptr < len(swing_lo) and swing_lo[lo_ptr] + k <= i - 1:
            level_lo = low[swing_lo[lo_ptr]]; lo_ptr += 1
        if np.isnan(a[i]) or a[i] <= 0:
            continue
        start = max(0, i - config.OB_MAX_LEG_BARS)
        # ---- bullish: close breaks above the latest swing high for the first time
        if level_hi is not None and close[i] > level_hi >= close[i - 1]:
            o = start + int(np.argmin(low[start:i + 1]))
            if close[i] - low[o] >= need * a[o]:
                j = next((x for x in range(o, max(-1, o - config.OB_BLOCK_LOOKBACK - 1), -1) if close[x] < open_[x]), None)
                if j is not None and ("bull", j) not in seen and j < i:
                    seen.add(("bull", j))
                    blocks.append(OrderBlock("bull", j, i, float(low[j]), float(high[j]), float((close[i] - low[o]) / a[o])))
        # ---- bearish: close breaks below the latest swing low for the first time
        if level_lo is not None and close[i] < level_lo <= close[i - 1]:
            o = start + int(np.argmax(high[start:i + 1]))
            if high[o] - close[i] >= need * a[o]:
                j = next((x for x in range(o, max(-1, o - config.OB_BLOCK_LOOKBACK - 1), -1) if close[x] > open_[x]), None)
                if j is not None and ("bear", j) not in seen and j < i:
                    seen.add(("bear", j))
                    blocks.append(OrderBlock("bear", j, i, float(low[j]), float(high[j]), float((high[o] - close[i]) / a[o])))
    return blocks


def first_touch(block: OrderBlock, open_: np.ndarray, high: np.ndarray, low: np.ndarray, expiry: int | None = None) -> int | None:
    """Index of the first bar after formation whose range reaches back into the zone
    (bullish: low <= zone high; bearish: high >= zone low), within `expiry` bars; None if never."""
    last = min(len(low), block.formed_idx + 1 + (expiry or config.OB_EXPIRY_BARS))
    for t in range(block.formed_idx + 1, last):
        if (block.direction == "bull" and low[t] <= block.high) or (block.direction == "bear" and high[t] >= block.low):
            return t
    return None


def active_blocks(
    open_: np.ndarray, high: np.ndarray, low: np.ndarray, close: np.ndarray, expiry: int | None = None,
) -> list[OrderBlock]:
    """Blocks still untouched as of the last bar (and not expired): the ones to watch."""
    n = len(close)
    out = []
    for b in find_order_blocks(open_, high, low, close):
        if n - 1 - b.formed_idx > (expiry or config.OB_EXPIRY_BARS):
            continue
        if first_touch(b, open_, high, low, expiry) is None:
            out.append(b)
    return out
