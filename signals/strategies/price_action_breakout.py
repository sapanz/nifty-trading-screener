"""Price Action Breakout: base -> high-volume breakout -> retest -> green
confirmation candle. Runs on both daily and weekly candles (see
scripts/run_signals.py and signals/backtest.py, which each call scan()
twice with a different set of window sizes below - the same shape of
setup, just zoomed to a different timeframe).

A stock qualifies when:
  - it is above its own 200-period SMA (long-term uptrend, same baseline
    filter every other strategy here uses)
  - looking back from a candle within the last `breakout_window` candles,
    the `pattern_lookback` candles right before it formed a tight base
    (high-low band within PRICE_ACTION_RANGE_TIGHTNESS) with more volume
    on its up (green) candles than its down ones - accumulation, not mere
    drift (is_accumulation_range)
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

This deliberately doesn't fit trendlines to tell a rectangle range apart
from a triangle's converging sides - what matters for trading it is that
the base got tight right before it broke, which is true of both shapes at
that point, and a real geometric classifier is a lot more machinery for a
label that doesn't change the trade. The note field still reports the raw
base high/low so a human can eyeball the shape themselves.

Entry is a resting buy-stop at the confirmation candle's own high, same
construction every other strategy here uses - the trade only fills once a
later candle actually trades up through it. Stop-loss is the lower of the
retest's own low and the confirmation candle's low (the support just
demonstrated holding). Targets are a measured move: the base's own height
projected up from the breakout level.

Brand new strategy - every threshold in signals/config.py's Price Action
Breakout section is a judgment call, not something mined from a backtest
CSV the way the older strategies' numbers were. Validate by watching
signals accumulate, the same way Futures OI Buildup is being validated,
rather than trusting these numbers are already tuned.
"""
from __future__ import annotations

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


def scan(
    price_data: dict[str, pd.DataFrame],
    pattern_lookback: int,
    breakout_window: int,
    volume_lookback: int,
) -> list[Signal]:
    signals: list[Signal] = []
    vol_col = f"avg_vol{volume_lookback}"
    min_len = config.SMA_LONG + pattern_lookback + breakout_window + 2

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

        best = None
        for b in range(today_idx - 1, today_idx - 1 - breakout_window, -1):
            if b - pattern_lookback < 0:
                break

            pattern = df.iloc[b - pattern_lookback : b]
            pattern_high = float(pattern["high"].max())
            pattern_low = float(pattern["low"].min())
            if pattern_low <= 0:
                continue
            if (pattern_high - pattern_low) / pattern_low > config.PRICE_ACTION_RANGE_TIGHTNESS:
                continue  # not a tight enough base
            if not is_accumulation_range(pattern):
                continue  # sellers outweighed buyers while the base built

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

            best = (b, pattern_high, pattern_low, retest_low, breakout_row)
            break  # most recent valid breakout wins

        if best is None:
            continue

        b, pattern_high, pattern_low, retest_low, breakout_row = best
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

        signals.append(
            Signal(
                symbol=symbol,
                entry=round(entry, 2),
                stop_loss=round(stop_loss, 2),
                targets=targets,
                sort_key=vol_ratio,
                note=(
                    f"Breakout {breakout_row['close']:.2f} ({candles_since_breakout} candles ago, {vol_ratio:.1f}x vol) | "
                    f"Retest held {pattern_high:.2f} | Base {pattern_low:.2f}-{pattern_high:.2f}"
                ),
                candle_date=today.name.date(),
                extra={
                    "vol_ratio": round(vol_ratio, 2),
                    "tightness_pct": round((pattern_high - pattern_low) / pattern_low * 100, 2),
                },
            )
        )

    signals.sort(key=lambda s: s.sort_key, reverse=True)
    return signals
