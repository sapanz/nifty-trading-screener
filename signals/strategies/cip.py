"""Shared Change-In-Polarity (CIP) detection logic, used by both the daily
and weekly CIP strategy modules (cip_daily.py, cip_weekly.py).

CIP: an old resistance level that price has tested multiple times and then
broken through on strong volume later gets retested from above - if a
bullish candle holds that old-resistance-turned-support level, that is the
"change in polarity" confirming the level flipped from resistance to
support.
"""
from __future__ import annotations

import pandas as pd

from signals import config
from signals.indicators import add_avg_volume, add_sma, is_above_sma, is_proper_close, is_volume_candle
from signals.models import Signal

VOL_COL = f"avg_vol{config.VOLUME_LOOKBACK}"


def _find_prior_breakout(
    df: pd.DataFrame,
    signal_iloc: int,
    touch_lookback: int,
    breakout_search: int,
    min_touches: int,
) -> tuple[int, float] | None:
    """Search backward from just before `signal_iloc` for the most recent
    candle that broke out above a resistance level tested at least
    `min_touches` times in the touch_lookback candles immediately preceding
    it. Returns (breakout_iloc, resistance_level), or None if no qualifying
    breakout is found within breakout_search candles."""
    earliest = max(touch_lookback, signal_iloc - breakout_search)
    for i in range(signal_iloc - 1, earliest - 1, -1):
        touches_start = i - touch_lookback
        if touches_start < 0:
            continue
        touches_window = df.iloc[touches_start:i]
        resistance = float(touches_window["high"].max())
        if resistance <= 0:
            continue
        touch_count = int((touches_window["high"] >= resistance * (1 - config.CIP_ZONE_TOLERANCE)).sum())
        if touch_count < min_touches:
            continue
        # the level must have actually held every time - no close in the
        # touches window already broke decisively above it
        if (touches_window["close"] > resistance * (1 + config.CIP_ZONE_TOLERANCE)).any():
            continue

        row = df.iloc[i]
        if not row["close"] > resistance:
            continue
        if not is_proper_close(row):
            continue
        if not is_volume_candle(row, VOL_COL, config.CIP_VOLUME_MULTIPLIER):
            continue
        return i, resistance
    return None


def scan_cip(
    data: dict[str, pd.DataFrame], *, touch_lookback: int, breakout_search: int, min_touches: int, timeframe_label: str
) -> list[Signal]:
    """Scan for CIP setups. `touch_lookback`/`breakout_search` are in bars
    of whatever timeframe `data` is already in (daily or weekly)."""
    signals: list[Signal] = []
    min_len = config.SMA_LONG + breakout_search + touch_lookback + 2

    for symbol, raw_df in data.items():
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

        found = _find_prior_breakout(df, len(df) - 1, touch_lookback, breakout_search, min_touches)
        if found is None:
            continue
        breakout_iloc, resistance = found

        # retest: today's low comes back down near the old resistance (now
        # support) without closing back below it
        if not row["low"] <= resistance * (1 + config.CIP_ZONE_TOLERANCE):
            continue
        if not row["close"] > resistance:
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
                note=f"CIP ({timeframe_label}): {resistance:.2f} broke {breakout_date.date()}, now retested as support",
            )
        )

    signals.sort(key=lambda s: s.sort_key, reverse=True)
    return signals
