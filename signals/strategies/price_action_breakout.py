"""Price Action Breakout: base -> high-volume breakout -> retest -> green
confirmation candle. Runs on daily AND weekly candles (see
scripts/run_signals.py and signals/backtest.py, which each call scan()
twice with a different set of window sizes below - the same shape of
setup, just zoomed to a different timeframe). No monthly leg - a 5-year
backtest showed it never fired under these thresholds (too little
monthly history per stock to form a base this strict), and Monthly ATH
Breakout already covers that timeframe.

A stock qualifies when:
  - it is above its own 200-period SMA (long-term uptrend, same baseline
    filter every other strategy here uses)
  - looking back from a candle within the last `breakout_window` candles,
    _detect_base finds a **base**: the longest window (between
    `pattern_min_lookback` and `pattern_max_lookback`) ending right before
    it whose high-low band stays within PRICE_ACTION_RANGE_TIGHTNESS, with
    more volume on its up (green) candles than its down ones -
    accumulation, not mere drift (is_accumulation_range). The base's
    actual detected length varies per stock/signal - it isn't a fixed
    number - and _classify_shape labels its shape (Range, Ascending/
    Descending/Symmetrical Triangle, Rising/Falling Wedge) from the slopes
    of lines fit through its highs and lows; see the caveat on that in
    signals/config.py.
  - that candle itself ("the breakout candle") closed above the base's
    high, bullish and properly closed, on clearly elevated volume
    (PRICE_ACTION_BREAKOUT_VOLUME_MULTIPLIER) - the "high volumes" the
    breakout itself needs
  - at least one candle after the breakout pulled back to retest that
    broken level (within PRICE_ACTION_RETEST_TOLERANCE) without any close
    in between falling PRICE_ACTION_INVALIDATION_PCT below it - the level
    held as support rather than failing
  - today is a green, properly-closed candle (is_bullish + is_proper_close
    - the existing 20%-wick rule, unchanged) that closes back above the
    breakout level - the actual signal trigger; everything above it is
    context this candle confirms

Entry is a resting buy-stop at the confirmation candle's own high, same
construction every other strategy here uses - the trade only fills once a
later candle actually trades up through it. Stop-loss is the lower of the
retest's own low and the confirmation candle's low (the support just
demonstrated holding). Targets are a measured move: the base's own height
projected up from the breakout level. Signals sort by the base's own
range (high-low as a % of low), largest first - not by volume or
proximity, per explicit request.

Brand new strategy - every threshold in signals/config.py's Price Action
Breakout section is a judgment call, not something mined from a backtest
CSV the way the older strategies' numbers were. Validate by watching
signals accumulate, the same way Futures OI Buildup is being validated,
rather than trusting these numbers are already tuned.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from signals import config
from signals.indicators import (
    add_avg_volume,
    add_sma,
    is_above_sma,
    is_accumulation_range,
    is_bullish,
    is_proper_close,
    is_volume_candle,
)
from signals.models import Signal


def _classify_shape(pattern: pd.DataFrame) -> str:
    """Label a base's shape from the slopes of straight lines fit through
    its highs and its lows - flat/rising/falling per line, per
    PRICE_ACTION_FLAT_SLOPE_PCT. A heuristic (slope sign/magnitude), not
    genuine trendline-touch-point geometry - see the caveat in
    signals/config.py."""
    n = len(pattern)
    x = np.arange(n)
    avg_price = float(pattern["close"].mean())
    if avg_price <= 0 or n < 2:
        return "Range"

    upper_slope = float(np.polyfit(x, pattern["high"].to_numpy(), 1)[0])
    lower_slope = float(np.polyfit(x, pattern["low"].to_numpy(), 1)[0])
    upper_pct = upper_slope / avg_price * 100
    lower_pct = lower_slope / avg_price * 100

    def direction(pct: float) -> str:
        if abs(pct) < config.PRICE_ACTION_FLAT_SLOPE_PCT:
            return "flat"
        return "rising" if pct > 0 else "falling"

    upper_dir, lower_dir = direction(upper_pct), direction(lower_pct)
    if upper_dir == "flat" and lower_dir == "flat":
        return "Range"
    if upper_dir == "flat" and lower_dir == "rising":
        return "Ascending Triangle"
    if upper_dir == "falling" and lower_dir == "flat":
        return "Descending Triangle"
    if upper_dir == "falling" and lower_dir == "rising":
        return "Symmetrical Triangle"
    if upper_dir == "rising" and lower_dir == "rising":
        return "Rising Wedge"
    if upper_dir == "falling" and lower_dir == "falling":
        return "Falling Wedge"
    return "Range"  # e.g. one line widening away from the other - not one of the six named shapes


def _rolling_bands(
    df: pd.DataFrame, pattern_min_lookback: int, pattern_max_lookback: int
) -> tuple[dict[int, np.ndarray], dict[int, np.ndarray]]:
    """Precompute, once per symbol, each candidate base length's rolling
    high-max / low-min as plain arrays - `highs[length][i]` is the max high
    over df.iloc[i-length+1:i+1] (so a base ending right before index
    `breakout_idx` reads index `breakout_idx - 1`). _detect_base is tried
    for up to `breakout_window` breakout candidates per symbol, and without
    this its per-length high/low band got re-sliced-and-aggregated from
    scratch on every one of those tries even though the windows overlap
    heavily - a real cost once the base-length search itself got wider."""
    highs = {}
    lows = {}
    for length in range(pattern_min_lookback, pattern_max_lookback + 1):
        highs[length] = df["high"].rolling(length).max().to_numpy()
        lows[length] = df["low"].rolling(length).min().to_numpy()
    return highs, lows


def _detect_base(
    df: pd.DataFrame,
    breakout_idx: int,
    pattern_min_lookback: int,
    pattern_max_lookback: int,
    high_bands: dict[int, np.ndarray],
    low_bands: dict[int, np.ndarray],
) -> tuple[pd.DataFrame, float, float] | None:
    """Find the longest tight base ending right before `breakout_idx`,
    between `pattern_min_lookback` and `pattern_max_lookback` candles long.
    Checked longest-first so the reported base length is the base's real
    extent, not just the shortest window that happens to qualify."""
    for length in range(pattern_max_lookback, pattern_min_lookback - 1, -1):
        start = breakout_idx - length
        if start < 0:
            continue
        pattern_high = high_bands[length][breakout_idx - 1]
        pattern_low = low_bands[length][breakout_idx - 1]
        if pd.isna(pattern_high) or pd.isna(pattern_low) or pattern_low <= 0:
            continue
        if (pattern_high - pattern_low) / pattern_low > config.PRICE_ACTION_RANGE_TIGHTNESS:
            continue
        pattern = df.iloc[start:breakout_idx]
        if not is_accumulation_range(pattern):
            continue
        return pattern, float(pattern_high), float(pattern_low)
    return None


def scan(
    price_data: dict[str, pd.DataFrame],
    pattern_min_lookback: int,
    pattern_max_lookback: int,
    breakout_window: int,
    volume_lookback: int,
) -> list[Signal]:
    signals: list[Signal] = []
    vol_col = f"avg_vol{volume_lookback}"
    min_len = config.SMA_LONG + pattern_max_lookback + breakout_window + 2

    for symbol, raw_df in price_data.items():
        df = raw_df.copy()
        if len(df) < min_len:
            continue

        add_sma(df, config.SMA_LONG)
        add_avg_volume(df, volume_lookback)

        today_idx = len(df) - 1
        today = df.iloc[today_idx]

        if pd.isna(today.get(f"sma{config.SMA_LONG}")):
            continue
        if not is_above_sma(today, f"sma{config.SMA_LONG}"):
            continue
        if not (is_bullish(today) and is_proper_close(today)):
            continue  # the actual trigger: today must itself be the green confirmation candle

        high_bands, low_bands = _rolling_bands(df, pattern_min_lookback, pattern_max_lookback)

        best = None
        for b in range(today_idx - 1, today_idx - 1 - breakout_window, -1):
            if b - pattern_min_lookback < 0:
                break

            base = _detect_base(df, b, pattern_min_lookback, pattern_max_lookback, high_bands, low_bands)
            if base is None:
                continue
            pattern, pattern_high, pattern_low = base

            breakout_row = df.iloc[b]
            if not breakout_row["close"] > pattern_high:
                continue
            if not (is_bullish(breakout_row) and is_proper_close(breakout_row)):
                continue
            if pd.isna(breakout_row.get(vol_col)):
                continue
            if not is_volume_candle(breakout_row, vol_col, config.PRICE_ACTION_BREAKOUT_VOLUME_MULTIPLIER):
                continue  # the breakout itself must come on clearly elevated volume

            between = df.iloc[b + 1 : today_idx]  # candles after the breakout, before today
            if between.empty:
                continue  # need at least one candle to have actually retested the level
            if float(between["close"].min()) < pattern_high * (1 - config.PRICE_ACTION_INVALIDATION_PCT):
                continue  # a close this far back below the breakout level - the level failed, not "retested"
            retest_low = float(between["low"].min())
            if retest_low > pattern_high * (1 + config.PRICE_ACTION_RETEST_TOLERANCE):
                continue  # price never actually came back down to retest the level

            if not today["close"] > pattern_high:
                continue  # today must reclaim the breakout level, not just be green somewhere below it

            best = (b, pattern, pattern_high, pattern_low, retest_low, breakout_row)
            break  # most recent valid breakout wins

        if best is None:
            continue

        b, pattern, pattern_high, pattern_low, retest_low, breakout_row = best
        entry = float(today["high"])
        stop_loss = float(min(retest_low, today["low"]))
        risk = entry - stop_loss
        if risk <= 0:
            continue

        pattern_height = pattern_high - pattern_low
        targets = [round(pattern_high + pattern_height * mult, 2) for mult in config.PRICE_ACTION_TARGET_MULTIPLES]
        if targets[0] <= entry:
            continue  # target already sits behind entry - skip rather than take a guaranteed-bad trade

        vol_ratio = float(breakout_row["volume"] / breakout_row[vol_col])
        candles_since_breakout = today_idx - b
        base_len = len(pattern)
        base_start = pattern.index[0].date()
        base_end = pattern.index[-1].date()
        range_pct = pattern_height / pattern_low * 100
        shape = _classify_shape(pattern)

        signals.append(
            Signal(
                symbol=symbol,
                entry=round(entry, 2),
                stop_loss=round(stop_loss, 2),
                targets=targets,
                sort_key=range_pct,
                note=(
                    f"{shape} | Base {base_len} candles ({base_start} to {base_end}), range {range_pct:.1f}% | "
                    f"Breakout {breakout_row['close']:.2f} ({candles_since_breakout} candles ago, {vol_ratio:.1f}x vol) | "
                    f"Retest held {pattern_high:.2f}"
                ),
                candle_date=today.name.date(),
                extra={
                    "vol_ratio": round(vol_ratio, 2),
                    "tightness_pct": round(range_pct, 2),
                    "breakout_type": shape,
                    "base_candles": base_len,
                    "base_start": base_start.isoformat(),
                    "base_end": base_end.isoformat(),
                },
            )
        )

    signals.sort(key=lambda s: s.sort_key, reverse=True)
    return signals
