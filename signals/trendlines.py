"""Support-trendline geometry on raw OHLC arrays (one trading session).

Pure numpy, no pandas: the backtest evaluates this on every 5-minute bar of
every stock-day, so the per-call overhead matters - a cheap pre-check
rejects almost every bar before any line is fitted.

A "support trendline" here is a rising straight line through swing lows. It
is valid if:
  * its anchors are two swing lows, the later one higher (it rises);
  * at least TRENDLINE_MIN_TOUCHES swing lows sit on it (within a tolerance);
  * no bar closed below it between the first anchor and the bar before the
    break (wicks through it are fine - a close is what counts);
  * it climbs at least TRENDLINE_MIN_RISE_PCT and spans at least
    TRENDLINE_MIN_WIDTH_BARS.
A break is the current bar closing below the line, as a bearish candle. The
widest valid line wins ("give importance to the wider trendline"): width is
the number of bars from the first anchor to the break bar.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from signals import config


@dataclass(frozen=True)
class TrendlineParams:
    """Everything find_break reads from config, bundled so the same geometry
    can run on 5-minute and daily candles with timeframe-appropriate
    tolerances. Built at call time (not import time) from config."""
    swing_bars: int
    min_touches: int
    min_width_bars: int
    min_rise_pct: float
    touch_tolerance_pct: float
    break_buffer_pct: float
    stop_buffer_pct: float
    min_risk_pct: float
    max_risk_pct: float
    target_r: float
    max_lookback_bars: int | None = None   # first anchor at most this many bars before the break


def intraday_params() -> TrendlineParams:
    return TrendlineParams(
        swing_bars=config.TRENDLINE_SWING_BARS, min_touches=config.TRENDLINE_MIN_TOUCHES,
        min_width_bars=config.TRENDLINE_MIN_WIDTH_BARS, min_rise_pct=config.TRENDLINE_MIN_RISE_PCT,
        touch_tolerance_pct=config.TRENDLINE_TOUCH_TOLERANCE_PCT,
        break_buffer_pct=config.TRENDLINE_BREAK_BUFFER_PCT, stop_buffer_pct=config.TRENDLINE_STOP_BUFFER_PCT,
        min_risk_pct=config.TRENDLINE_MIN_RISK_PCT, max_risk_pct=config.TRENDLINE_MAX_RISK_PCT,
        target_r=config.TRENDLINE_TARGET_R,
    )


def daily_params() -> TrendlineParams:
    return TrendlineParams(
        swing_bars=config.TRENDLINE_DAILY_SWING_BARS, min_touches=config.TRENDLINE_DAILY_MIN_TOUCHES,
        min_width_bars=config.TRENDLINE_DAILY_MIN_WIDTH_BARS, min_rise_pct=config.TRENDLINE_DAILY_MIN_RISE_PCT,
        touch_tolerance_pct=config.TRENDLINE_DAILY_TOUCH_TOLERANCE_PCT,
        break_buffer_pct=config.TRENDLINE_DAILY_BREAK_BUFFER_PCT, stop_buffer_pct=config.TRENDLINE_DAILY_STOP_BUFFER_PCT,
        min_risk_pct=config.TRENDLINE_DAILY_MIN_RISK_PCT, max_risk_pct=config.TRENDLINE_DAILY_MAX_RISK_PCT,
        target_r=config.TRENDLINE_DAILY_TARGET_R, max_lookback_bars=config.TRENDLINE_DAILY_MAX_LOOKBACK_BARS,
    )


@dataclass
class Break:
    anchor_idx: int          # first swing low on the line
    touch_idxs: list[int]    # every swing low sitting on it (anchors included)
    slope: float             # price per bar
    line_at_break: float
    width_bars: int          # first anchor -> break bar
    rise_pct: float          # line's rise from first anchor to the bar before the break
    break_depth_pct: float   # how far under the line the break bar closed
    entry: float
    stop: float
    target: float
    risk_pct: float

    @property
    def touches(self) -> int:
        return len(self.touch_idxs)


def swing_lows(low: np.ndarray, k: int | None = None) -> list[int]:
    """Indices whose low is lower than the k bars before and no higher than
    the k bars after. Needs k bars on both sides, so a swing at index i is
    only known once bar i+k has printed - callers must not use one earlier."""
    k = config.TRENDLINE_SWING_BARS if k is None else k
    n = len(low)
    found = []
    for i in range(k, n - k):
        v = low[i]
        if v < low[i - k:i].min() and v <= low[i + 1:i + k + 1].min():
            found.append(i)
    return found


def find_break(
    open_: np.ndarray, high: np.ndarray, low: np.ndarray, close: np.ndarray, t: int,
    pivots: list[int] | None = None, params: TrendlineParams | None = None,
) -> Break | None:
    """Does bar `t` (an index into this session's arrays; bars after it are
    ignored) break a rising support trendline? `pivots` may be passed in when
    already computed for the whole session - swing lows only ever look k
    bars around themselves, so the result is identical to computing them
    from bars up to t. `params` defaults to the 5-minute settings."""
    p = params or intraday_params()
    k = p.swing_bars
    if t < p.min_width_bars or close[t] >= open_[t]:
        return None
    # Cheap pre-check: a trendline break closes below the recent lows.
    if close[t] >= low[t - 3:t].min():
        return None

    if pivots is None:
        pivots = swing_lows(low[:t], k)
    earliest = t - p.max_lookback_bars if p.max_lookback_bars is not None else 0
    # confirmed before the break bar, within the lookback; recent ones only
    pivots = [i for i in pivots if i + k <= t - 1 and i >= earliest][-8:]
    if len(pivots) < p.min_touches:
        return None

    buffer = p.break_buffer_pct / 100
    touch_tol = p.touch_tolerance_pct / 100
    best: tuple | None = None

    for ai, a in enumerate(pivots):
        for b in pivots[ai + 1:]:
            if low[b] <= low[a]:
                continue
            slope = (low[b] - low[a]) / (b - a)
            width = t - a
            if width < p.min_width_bars:
                continue
            xs = np.arange(a, t + 1)
            line = low[a] + slope * (xs - a)
            line_before = line[:-1]
            if line[-2] / line[0] - 1 < p.min_rise_pct / 100:
                continue
            threshold = line * (1 - buffer)
            # closed under the line anywhere before the break -> not intact;
            # closes through the break bar itself must be the first one under
            if (close[a:t] < threshold[:-1]).any() or close[t] >= threshold[-1]:
                continue
            touching = [j for j in pivots if j >= a and abs(low[j] - line[j - a]) / line_before[j - a] <= touch_tol]
            if len(touching) < p.min_touches:
                continue
            key = (width, len(touching))
            if best is None or key > best[0]:
                best = (key, a, slope, touching, line)

    if best is None:
        return None
    _, a, slope, touching, line = best

    entry = float(close[t])
    stop = float(high[max(touching):t + 1].max()) * (1 + p.stop_buffer_pct / 100)
    risk = stop - entry
    risk_pct = risk / entry * 100
    if not (p.min_risk_pct <= risk_pct <= p.max_risk_pct):
        return None
    return Break(
        anchor_idx=a, touch_idxs=touching, slope=float(slope), line_at_break=float(line[-1]),
        width_bars=t - a, rise_pct=float((line[-2] / line[0] - 1) * 100),
        break_depth_pct=float((1 - close[t] / line[-1]) * 100),
        entry=entry, stop=stop, target=entry - p.target_r * risk, risk_pct=risk_pct,
    )
