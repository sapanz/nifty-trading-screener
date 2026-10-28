"""Order blocks on DAILY candles - the Swing tab's zones (same definition as the 5-minute ones, see signals/orderblocks.py).

A zone is listed while it is still useful for a swing trade: formed within the last OB_EXPIRY_BARS sessions, and either
  fresh    - price has not come back to it yet, or
  testing  - price touched it within the last INTRADAY_OB_RECENT_BARS sessions without trading through the far edge
             (zone's low less OB_STOP_BUFFER_PCT for a bullish block, high plus it for a bearish one).
Strength 0-100 is signals.intraday_blocks.block_score: thrust size, thrust volume, a liquidity sweep before the thrust, and
"with the trend" - here, the close that broke structure is on the right side of the close 20 sessions earlier.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from signals import config
from signals.intraday_blocks import block_score, key_levels
from signals.orderblocks import find_order_blocks, first_touch
from signals.swing_trendlines import analyze as trendlines_daily, confluence

CHART_BARS = 90
TREND_LOOKBACK = 20
MAX_ZONES_PER_SIDE = 2
POOL_TOLERANCE_PCT = 0.4      # daily swing extremes within 0.4% count as one pool of stops


def scan_daily(df: pd.DataFrame, lines: dict | None = None) -> list[dict]:
    """The (up to two) best bullish and bearish live zones as of the last candle (testing beats fresh, then the most recent). With the
    stock's live trendlines (signals/swing_trendlines.analyze, computed here if not given) each zone also carries "cf": 1 when
    the trendline that confirms its direction runs through it."""
    if df is None or len(df) < 40:
        return []
    lines = trendlines_daily(df) if lines is None else lines
    o, h, l, c = (df[col].to_numpy(dtype=float) for col in ("open", "high", "low", "close"))
    v = df["volume"].to_numpy(dtype=float) if "volume" in df else None
    n = len(c)
    buf = config.OB_STOP_BUFFER_PCT / 100
    found: dict[str, list] = {}
    for b in find_order_blocks(o, h, l, c, volume=v):
        if n - 1 - b.formed_idx > config.OB_EXPIRY_BARS:
            continue
        t = first_touch(b, o, h, l, config.OB_EXPIRY_BARS)
        wick = False
        if t is None:
            state = "fresh"
        else:
            # A zone fails when a candle CLOSES beyond its far edge, not when a wick pokes through: a wick through the zone that
            # closes back inside / before it is a rejection - the zone held (ULTRACEMCO, 19 Oct 2026: high 11,219 through a
            # 11,027-11,184 supply zone, close 10,650).
            if b.direction == "bull":
                failed, wick = (c[t:] < b.low * (1 - buf)).any(), (l[t:] <= b.low * (1 - buf)).any()
            else:
                failed, wick = (c[t:] > b.high * (1 + buf)).any(), (h[t:] >= b.high * (1 + buf)).any()
            if failed or n - 1 - t > config.INTRADAY_OB_RECENT_BARS:
                continue
            state = "testing"
        back = b.formed_idx - TREND_LOOKBACK
        with_trend = back >= 0 and ((c[b.formed_idx] > c[back]) if b.direction == "bull" else (c[b.formed_idx] < c[back]))
        rec = {"d": b.direction, "st": state, "lo": round(b.low, 2), "hi": round(b.high, 2), "t": df.index[b.idx].strftime("%Y-%m-%d"),
               "imp": round(b.impulse_atr, 1), "sw": int(b.swept), "vr": b.vol_ratio, "wd": int(with_trend), "wk": int(wick),
               "sc": block_score(b.impulse_atr, b.vol_ratio, b.swept, with_trend)}
        rec["cf"] = int(confluence(rec, lines))
        found.setdefault(b.direction, []).append(((state == "testing", b.formed_idx), rec))
    out = []
    for direction in ("bull", "bear"):                                # the two best per direction: the nearest zone is not always the newest
        out += [rec for _, rec in sorted(found.get(direction, []), key=lambda x: x[0], reverse=True)[:MAX_ZONES_PER_SIDE]]
    return out


def chart_payload_daily(df: pd.DataFrame) -> dict | None:
    """Last CHART_BARS daily candles (with their dates) and the swing levels around the last close."""
    if df is None or len(df) < 20:
        return None
    tail = df.tail(CHART_BARS)
    return {"dates": [d.strftime("%Y-%m-%d") for d in tail.index],
            "c": [[round(float(x), 2) for x in row] for row in tail[["open", "high", "low", "close"]].to_numpy()],
            "lv": key_levels(df["high"].to_numpy(dtype=float)[-160:], df["low"].to_numpy(dtype=float)[-160:], float(df["close"].iloc[-1]),
                             tol_pct=POOL_TOLERANCE_PCT)}
