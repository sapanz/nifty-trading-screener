"""Weekly range-breakout strategy (runs Fridays after close).

A stock qualifies when, on the weekly timeframe:
  - it is above its 200-period SMA (long-term uptrend; the 200 SMA itself
    can be flat or rising, only price needs to be above it)
  - its 30-period SMA is itself rising (a real, faster-moving uptrend, not
    just sideways drift the price happens to sit above)
  - the preceding N weeks formed a tight consolidation range, with more
    volume on the range's up (green) candles than its down (red) ones -
    a classic accumulation signature: buyers stepping in harder than
    sellers while the range builds, not just quiet drift
  - this week's close broke out above that range's high, by between
    BREAKOUT_MIN_EXTENSION and BREAKOUT_MAX_EXTENSION - not a barely-there
    break (weak, easily reversed) and not already extended (often means
    the fixed measured-move target sits behind the entry before the trade
    even starts)
  - the breakout candle is bullish (closed above its own open) and closed
    properly (in the top 20% of its own range, i.e. a small upper wick)
    on strong volume

Entry is a resting buy-stop at the breakout candle's own high, not an
immediate fill at its close - the trade only enters once a later candle
actually trades up through that high, confirming the breakout continues
rather than assuming it does (see backtest.simulate_forward's handling of
entry > signal-close; an entry that's never reached is reported
"unfilled"). Stop-loss sits at the midpoint of the consolidation range
(not the breakout level itself) - price often comes back to retest the
range as support after breaking out, and a stop right at the breakout
level gets hit by that normal retest, not just a genuine failed breakout.
"""
from __future__ import annotations

import pandas as pd

from signals import config
from signals.indicators import (
    add_avg_volume,
    add_rsi,
    add_sma,
    is_above_sma,
    is_accumulation_range,
    is_bullish,
    is_proper_close,
    is_sma_rising,
    is_volume_candle,
)
from signals.models import Signal

VOL_COL = f"avg_vol{config.VOLUME_LOOKBACK}"


def scan(weekly_data: dict[str, pd.DataFrame]) -> list[Signal]:
    signals: list[Signal] = []
    window = config.BREAKOUT_RANGE_WEEKS

    for symbol, raw_df in weekly_data.items():
        df = raw_df.copy()
        if len(df) < config.SMA_LONG + window + 1:
            continue

        add_sma(df, config.SMA_LONG)
        add_sma(df, config.BREAKOUT_TREND_SMA)
        add_avg_volume(df, config.VOLUME_LOOKBACK)
        add_rsi(df)
        row = df.iloc[-1]

        if pd.isna(row.get(f"sma{config.SMA_LONG}")) or pd.isna(row.get(f"sma{config.BREAKOUT_TREND_SMA}")):
            continue
        if not is_above_sma(row, f"sma{config.SMA_LONG}"):
            continue
        if not is_sma_rising(df, f"sma{config.BREAKOUT_TREND_SMA}"):
            continue  # 30 SMA must be trending up - a real uptrend, not just above a flat 200 SMA

        prior = df.iloc[-1 - window : -1]
        range_high = float(prior["high"].max())
        range_low = float(prior["low"].min())
        if range_low <= 0:
            continue
        if (range_high - range_low) / range_low > config.BREAKOUT_RANGE_TIGHTNESS:
            continue  # prior weeks weren't a tight enough consolidation
        if not is_accumulation_range(prior):
            continue  # sellers outweighed buyers during the range - not accumulation

        if not row["close"] > range_high:
            continue
        extension = (row["close"] - range_high) / range_high
        if not (config.BREAKOUT_MIN_EXTENSION < extension <= config.BREAKOUT_MAX_EXTENSION):
            continue  # too weak a break (likely to fail) or too extended (target may already sit behind entry)
        if not is_bullish(row):
            continue  # breakout candle must be green (close > open), not just closed near its own high
        if not is_proper_close(row):
            continue
        if not is_volume_candle(row, VOL_COL, config.WEEKLY_VOLUME_MULTIPLIER):
            continue

        # Entry is a resting buy-stop at this candle's own high, not an
        # immediate fill at its close - simulate_forward (and a live buy-stop
        # order) only fills once a later candle actually trades up through
        # it, confirming the breakout keeps going rather than assuming it
        # will from the close alone.
        entry = float(row["high"])
        # SL at the midpoint of the consolidation range: price often comes
        # back to retest the range as support after breaking out, and a
        # stop right at the breakout level (range_high) gets stopped out by
        # that normal retest rather than a genuine failed breakout.
        stop_loss = float((range_high + range_low) / 2)
        risk = entry - stop_loss
        if risk <= 0:
            continue

        range_height = range_high - range_low
        targets = [round(range_high + range_height * mult, 2) for mult in config.BREAKOUT_RANGE_MULTIPLES]
        vol_ratio = float(row["volume"] / row[VOL_COL])

        # Diagnostic-only fields (not gated on) so a backtest's trade CSV can
        # be mined for what actually differentiates good and bad signals -
        # see backtest.DIAGNOSTIC_KEYS.
        sma_long_val = float(row[f"sma{config.SMA_LONG}"])
        rsi_val = row.get("rsi14")

        signals.append(
            Signal(
                symbol=symbol,
                entry=round(entry, 2),
                stop_loss=round(stop_loss, 2),
                targets=targets,
                sort_key=vol_ratio,
                note=f"Vol {vol_ratio:.1f}x avg | Range {range_low:.2f}-{range_high:.2f} ({window}w)",
                candle_date=row.name.date(),
                extra={
                    "vol_ratio": round(vol_ratio, 2),
                    "extension_pct": round(extension * 100, 2),
                    "tightness_pct": round((range_high - range_low) / range_low * 100, 2),
                    "dist_from_sma200_pct": round((entry / sma_long_val - 1) * 100, 2),
                    **({"rsi14": round(float(rsi_val), 1)} if pd.notna(rsi_val) else {}),
                },
            )
        )

    signals.sort(key=lambda s: s.sort_key, reverse=True)
    return signals
