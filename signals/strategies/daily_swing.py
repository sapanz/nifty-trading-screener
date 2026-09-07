"""Daily Swing strategy: Accumulation Spring, a Wyckoff-style base /
breakout / low-volume-retest sequence (runs every trading day after close).

The logic models how a real institutional buyer has to behave: they can't
accumulate a full position in a single session without moving price against
themselves, so genuine accumulation shows up as a quiet, tight, sideways
base where volume actually dries up as the base matures (fewer willing
sellers left as the position fills) - followed by a breakout candle whose
volume surges far above that base's own (already low) average, the
"effort" finally showing in price - and finally a retest of the breakout
level on LOW volume: the "Last Point of Support" in Wyckoff terms, where
the last unconvinced sellers get absorbed with essentially no real supply
left. That low-volume retest, not the breakout candle itself, is the
actual entry trigger here.

A stock qualifies when, on the daily timeframe:
  - a DAILY_SWING_BASE_LENGTH-day window forms a tight base (high-low
    range within DAILY_SWING_BASE_TIGHTNESS of its own low) whose second
    half trades on meaningfully lower volume than its first half
    (DAILY_SWING_VOLUME_DRYUP_RATIO) - real supply drying up, not drift
  - within DAILY_SWING_BREAKOUT_SEARCH days after that base, a candle
    closes above the base's high, properly closed, on volume at least
    DAILY_SWING_BREAKOUT_VOLUME_MULTIPLIER x the base's own average -
    and price has held above the base ever since (no failed breakout)
  - today's candle is bullish, closed properly, dips back to within
    DAILY_SWING_RETEST_TOLERANCE of the base's high (now support) without
    closing back below it, on volume below DAILY_SWING_RETEST_MAX_VOLUME_RATIO
    x the base's own average - a real absence of selling pressure, not
    just a quiet day by coincidence

Entry is today's high (a buy-stop triggered the next day price trades up
to it); stop-loss is the lower of today's own low and the previous
candle's low; targets are measured-move multiples of the base's own
height, projected above the base high ("cause equals effect").

No moving averages or oscillators anywhere in this - every condition is a
direct read of price and volume.
"""
from __future__ import annotations

import pandas as pd

from signals import config
from signals.indicators import is_proper_close
from signals.models import Signal


def _base_window(df: pd.DataFrame, breakout_iloc: int) -> pd.DataFrame | None:
    start = breakout_iloc - config.DAILY_SWING_BASE_LENGTH
    if start < 0:
        return None
    return df.iloc[start:breakout_iloc]


def _base_bounds(base: pd.DataFrame) -> tuple[float, float] | None:
    base_high = float(base["high"].max())
    base_low = float(base["low"].min())
    if base_low <= 0:
        return None
    if (base_high - base_low) / base_low > config.DAILY_SWING_BASE_TIGHTNESS:
        return None
    return base_high, base_low


def _has_volume_dryup(base: pd.DataFrame) -> bool:
    half = len(base) // 2
    first_half_vol = float(base["volume"].iloc[:half].mean())
    second_half_vol = float(base["volume"].iloc[half:].mean())
    if first_half_vol <= 0:
        return False
    return second_half_vol <= first_half_vol * config.DAILY_SWING_VOLUME_DRYUP_RATIO


def _find_breakout(df: pd.DataFrame, signal_iloc: int) -> tuple[int, float, float, float] | None:
    """Search backward from just before `signal_iloc` for the most recent
    candle that broke out above a tight, volume-dried-up base on a volume
    surge relative to that base's own (quiet) average, with price having
    held above the base ever since. Returns
    (breakout_iloc, base_high, base_low, base_avg_vol), or None if no
    qualifying breakout is found within DAILY_SWING_BREAKOUT_SEARCH days."""
    earliest = max(config.DAILY_SWING_BASE_LENGTH, signal_iloc - config.DAILY_SWING_BREAKOUT_SEARCH)

    for i in range(signal_iloc - 1, earliest - 1, -1):
        base = _base_window(df, i)
        if base is None:
            continue

        bounds = _base_bounds(base)
        if bounds is None:
            continue
        base_high, base_low = bounds

        if not _has_volume_dryup(base):
            continue

        base_avg_vol = float(base["volume"].mean())
        if base_avg_vol <= 0:
            continue

        row = df.iloc[i]
        if not row["close"] > base_high:
            continue
        if not is_proper_close(row):
            continue
        if not row["volume"] >= base_avg_vol * config.DAILY_SWING_BREAKOUT_VOLUME_MULTIPLIER:
            continue

        # price must have held above the base ever since - no failed
        # breakout round-tripping back into the accumulation range
        span_low = float(df["low"].iloc[i + 1 : signal_iloc + 1].min())
        if span_low < base_low:
            continue

        return i, base_high, base_low, base_avg_vol
    return None


def scan(daily_data: dict[str, pd.DataFrame]) -> list[Signal]:
    signals: list[Signal] = []
    min_len = config.DAILY_SWING_BASE_LENGTH + config.DAILY_SWING_BREAKOUT_SEARCH + 2

    for symbol, raw_df in daily_data.items():
        df = raw_df.copy()
        if len(df) < min_len:
            continue

        signal_iloc = len(df) - 1
        row = df.iloc[signal_iloc]
        prev_row = df.iloc[signal_iloc - 1]

        if not row["close"] > row["open"]:  # retest candle must be bullish
            continue
        if not is_proper_close(row):
            continue

        found = _find_breakout(df, signal_iloc)
        if found is None:
            continue
        breakout_iloc, base_high, base_low, base_avg_vol = found

        # retest: today's low comes back down near the base high (now
        # support) without closing back below it
        if not row["low"] <= base_high * (1 + config.DAILY_SWING_RETEST_TOLERANCE):
            continue
        if not row["close"] > base_high:
            continue
        if row["volume"] >= base_avg_vol * config.DAILY_SWING_RETEST_MAX_VOLUME_RATIO:
            continue  # real supply would show up as volume - a spring shouldn't have any

        entry = float(row["high"])
        stop_loss = float(min(row["low"], prev_row["low"]))
        risk = entry - stop_loss
        if risk <= 0:
            continue

        base_height = base_high - base_low
        targets = [round(base_high + base_height * mult, 2) for mult in config.DAILY_SWING_MEASURED_MOVE_MULTIPLES]
        targets = [t for t in targets if t > entry]
        if not targets:
            continue

        breakout_date = df.index[breakout_iloc]
        days_since_breakout = signal_iloc - breakout_iloc

        signals.append(
            Signal(
                symbol=symbol,
                entry=round(entry, 2),
                stop_loss=round(stop_loss, 2),
                targets=targets,
                sort_key=float(-days_since_breakout),  # freshest retest (soonest after breakout) sorts first
                note=f"Accumulation Spring: base {base_low:.2f}-{base_high:.2f} broke {breakout_date.date()}, low-vol retest",
            )
        )

    signals.sort(key=lambda s: s.sort_key, reverse=True)
    return signals
