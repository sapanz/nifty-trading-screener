"""Weekly Change-In-Polarity (CIP) strategy (runs Fridays after close).

A stock qualifies when, on the weekly timeframe:
  - it is above its 200-period SMA (long-term uptrend)
  - price formed at least CIP_WEEKLY_MIN_ZONE_POINTS distinct swing highs
    (confirmed local peaks, not just any candle sitting near the top of a
    flat run) within CIP_ZONE_TOLERANCE of each other, in the
    CIP_WEEKLY_TOUCH_LOOKBACK weeks before a candidate breakout candle -
    i.e. a genuine resistance *zone*, not a single exact price line
  - that candle finally closed above the top of the zone on strong volume
  - price later pulled back and retested that former zone - now support -
    with a bullish, properly-closed candle that held above it: the
    "change in polarity" from resistance to support
"""
from __future__ import annotations

import pandas as pd

from signals import config
from signals.indicators import add_avg_volume, add_sma, is_above_sma, is_proper_close, is_volume_candle
from signals.models import Signal

VOL_COL = f"avg_vol{config.VOLUME_LOOKBACK}"


def _swing_high_mask(df: pd.DataFrame) -> pd.Series:
    """True for bars whose high is a confirmed local peak (higher than the
    bar immediately before and after) - a genuine resistance test, not
    just any candle that happens to sit near the top of a flat run."""
    high = df["high"]
    return (high > high.shift(1)) & (high > high.shift(-1))


def _find_prior_breakout(df: pd.DataFrame, signal_iloc: int) -> tuple[int, float, float] | None:
    """Search backward from just before `signal_iloc` for the most recent
    candle that broke out above a resistance *zone* validated by at least
    CIP_WEEKLY_MIN_ZONE_POINTS distinct swing-high peaks within
    CIP_ZONE_TOLERANCE of each other, in the CIP_WEEKLY_TOUCH_LOOKBACK
    weeks immediately preceding it. Returns (breakout_iloc, zone_low,
    zone_high), or None if no qualifying breakout is found within
    CIP_WEEKLY_BREAKOUT_SEARCH weeks."""
    touch_lookback = config.CIP_WEEKLY_TOUCH_LOOKBACK
    earliest = max(touch_lookback, signal_iloc - config.CIP_WEEKLY_BREAKOUT_SEARCH)

    # Everything strictly before today's signal candle is already fully
    # known, so the swing-high geometry can be computed once up front
    # rather than re-derived (identically) inside every loop iteration.
    known = df.iloc[:signal_iloc]
    is_swing_high = _swing_high_mask(known)

    for i in range(signal_iloc - 1, earliest - 1, -1):
        touches_start = i - touch_lookback
        if touches_start < 0:
            continue

        touches_window = df.iloc[touches_start:i]
        zone_high = float(touches_window["high"].max())
        if zone_high <= 0:
            continue
        zone_low = zone_high * (1 - config.CIP_ZONE_TOLERANCE)

        peaks_in_zone = is_swing_high.loc[touches_window.index] & (touches_window["high"] >= zone_low)
        if int(peaks_in_zone.sum()) < config.CIP_WEEKLY_MIN_ZONE_POINTS:
            continue

        row = df.iloc[i]
        if not row["close"] > zone_high:
            continue
        if not is_proper_close(row):
            continue
        if not is_volume_candle(row, VOL_COL, config.CIP_VOLUME_MULTIPLIER):
            continue
        return i, zone_low, zone_high
    return None


def scan(weekly_data: dict[str, pd.DataFrame]) -> list[Signal]:
    signals: list[Signal] = []
    min_len = config.SMA_LONG + config.CIP_WEEKLY_BREAKOUT_SEARCH + config.CIP_WEEKLY_TOUCH_LOOKBACK + 2

    for symbol, raw_df in weekly_data.items():
        df = raw_df.copy()
        if len(df) < min_len:
            continue

        add_sma(df, config.SMA_LONG)
        add_avg_volume(df, config.VOLUME_LOOKBACK)
        row = df.iloc[-1]
        prev_row = df.iloc[-2]

        if pd.isna(row.get(f"sma{config.SMA_LONG}")):
            continue
        if not is_above_sma(row, f"sma{config.SMA_LONG}"):
            continue
        if not row["close"] > row["open"]:  # retest candle must be bullish
            continue
        if not is_proper_close(row):
            continue

        found = _find_prior_breakout(df, len(df) - 1)
        if found is None:
            continue
        breakout_iloc, zone_low, zone_high = found

        # retest: today's low comes back down near the old zone (now
        # support) without closing back below its top
        if not row["low"] <= zone_high * (1 + config.CIP_ZONE_TOLERANCE):
            continue
        if not row["close"] > zone_high:
            continue

        entry = float(row["high"])
        stop_loss = float(min(row["low"], prev_row["low"]))
        risk = entry - stop_loss
        if risk <= 0:
            continue

        targets = [round(entry + risk * mult, 2) for mult in config.CIP_RISK_REWARD_TARGETS]

        breakout_row = df.iloc[breakout_iloc]
        breakout_avg_vol = breakout_row.get(VOL_COL)
        vol_ratio = float(breakout_row["volume"] / breakout_avg_vol) if breakout_avg_vol else 0.0
        breakout_date = df.index[breakout_iloc]

        signals.append(
            Signal(
                symbol=symbol,
                entry=round(entry, 2),
                stop_loss=round(stop_loss, 2),
                targets=targets,
                sort_key=vol_ratio,
                note=f"CIP (weekly): {zone_low:.2f}-{zone_high:.2f} broke {breakout_date.date()}, now retested as support",
            )
        )

    signals.sort(key=lambda s: s.sort_key, reverse=True)
    return signals
