"""Live trendlines on DAILY candles - for finding setups before the entry, and as a confirmation for order-block zones.

(signals/trendlines.py finds a trendline being BROKEN; this finds the ones still intact that price is sitting on or approaching.)

  support line     rising, through swing lows: at least TRENDLINE_DAILY_MIN_TOUCHES lows within TRENDLINE_DAILY_TOUCH_TOLERANCE_PCT of it,
                   rising at least TRENDLINE_DAILY_MIN_RISE_PCT over its span, no close below it (less a small buffer) since the first anchor
  resistance line  falling, through swing highs, mirror image
The widest valid line wins ("give importance to the wider trendline"); ties go to more touches. Swing points need
TRENDLINE_DAILY_SWING_BARS candles on each side, so only confirmed ones are used.

Confluence: a bullish zone is confirmed by a support line that passes through (or within CONFLUENCE_TOL_PCT of) the zone now or a few
candles ahead; a bearish zone by a resistance line. Both say "buyers (sellers) have defended this area", from independent geometry.
"""
from __future__ import annotations

import numpy as np

from signals import config
from signals.trendlines import daily_params

CONFLUENCE_TOL_PCT = 1.0
PROJECT_BARS = 3


def _pivots(values: np.ndarray, k: int, t: int, highs: bool) -> list[int]:
    out = []
    for i in range(k, t - k + 1):
        left, right = values[i - k:i], values[i + 1:i + k + 1]
        if highs and values[i] > left.max() and values[i] >= right.max():
            out.append(i)
        elif not highs and values[i] < left.min() and values[i] <= right.min():
            out.append(i)
    return out


def _best_line(values: np.ndarray, close: np.ndarray, t: int, support: bool) -> dict | None:
    p = daily_params()
    k = p.swing_bars
    earliest = max(0, t - (p.max_lookback_bars or t))
    pivots = [i for i in _pivots(values, k, t, highs=not support) if i >= earliest][-8:]
    if len(pivots) < p.min_touches:
        return None
    buf, tol = p.break_buffer_pct / 100, p.touch_tolerance_pct / 100
    best = None
    for ai, a in enumerate(pivots):
        for b in pivots[ai + 1:]:
            if (support and values[b] <= values[a]) or (not support and values[b] >= values[a]):
                continue
            width = t - a
            if width < p.min_width_bars:
                continue
            slope = (values[b] - values[a]) / (b - a)
            xs = np.arange(a, t + 1)
            line = values[a] + slope * (xs - a)
            move = (line[-1] / line[0] - 1) * 100 if support else (line[0] / line[-1] - 1) * 100
            if move < p.min_rise_pct or line[-1] <= 0:
                continue
            seg = close[a:t + 1]
            if (support and (seg < line * (1 - buf)).any()) or (not support and (seg > line * (1 + buf)).any()):
                continue                                                  # a close went through it: not intact
            touching = [j for j in pivots if j >= a and abs(values[j] - line[j - a]) / line[j - a] <= tol]
            if len(touching) < p.min_touches:
                continue
            key = (width, len(touching))
            if best is None or key > best[0]:
                best = (key, slope, float(line[-1]), len(touching), width, float(move))
    if best is None:
        return None
    _, slope, v, n, width, move = best
    price = float(close[t])
    return {"v": round(v, 2), "m": round(float(slope), 4), "n": n, "w": int(width), "mv": round(move, 1),
            "d": round(((price / v - 1) if support else (v / price - 1)) * 100, 2)}


def analyze(df) -> dict:
    """{"s": support line or absent, "r": resistance line or absent} as of the last candle. d = distance from price to the line, %
    (support: price above it; resistance: price below it)."""
    if df is None or len(df) < 40:
        return {}
    high, low, close = (df[c].to_numpy(dtype=float) for c in ("high", "low", "close"))
    t = len(close) - 1
    out = {}
    s, r = _best_line(low, close, t, True), _best_line(high, close, t, False)
    if s:
        out["s"] = s
    if r:
        out["r"] = r
    return out


def confluence(zone: dict, lines: dict) -> bool:
    """Does the line that confirms this zone's direction run through the zone (+/- CONFLUENCE_TOL_PCT) now or PROJECT_BARS candles ahead?"""
    line = lines.get("s" if zone["d"] == "bull" else "r")
    if not line:
        return False
    lo, hi = zone["lo"] * (1 - CONFLUENCE_TOL_PCT / 100), zone["hi"] * (1 + CONFLUENCE_TOL_PCT / 100)
    return any(lo <= line["v"] + line["m"] * j <= hi for j in range(PROJECT_BARS + 1))
