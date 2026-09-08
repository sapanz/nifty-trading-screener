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
    properly (in the top 25% of its own range, i.e. a small upper wick)
    on strong volume

There's no fixed profit target: the trade is held as long as the weekly
close stays above its own BREAKOUT_TREND_SMA-week SMA, exiting the week it
closes back below (see backtest.simulate_weekly_trailing_sma). The stop-loss
is whichever of the breakout level (range_high) or a fixed
WEEKLY_MAX_RISK_PCT below entry is tighter - capping how much risk an
extended entry can carry (see config.py for why).
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

        entry = float(row["close"])
        # Anchor SL to the breakout level itself (old resistance becomes new
        # support), not the bottom of the consolidation range - the latter
        # let risk balloon with however wide the whole range was, producing
        # a high win rate but large tail losses in backtesting. But once
        # entries are allowed to sit up to BREAKOUT_MAX_EXTENSION above that
        # level, the range_high anchor alone lets risk balloon right back
        # with the extension - so take whichever of the two is tighter.
        stop_loss = float(max(range_high * (1 - config.SL_BUFFER), entry * (1 - config.WEEKLY_MAX_RISK_PCT)))
        risk = entry - stop_loss
        if risk <= 0:
            continue

        vol_ratio = float(row["volume"] / row[VOL_COL])

        signals.append(
            Signal(
                symbol=symbol,
                entry=round(entry, 2),
                stop_loss=round(stop_loss, 2),
                targets=[],
                sort_key=vol_ratio,
                note=(
                    f"Vol {vol_ratio:.1f}x avg | Range {range_low:.2f}-{range_high:.2f} ({window}w) | "
                    f"trail until close < {config.BREAKOUT_TREND_SMA}-week SMA"
                ),
            )
        )

    signals.sort(key=lambda s: s.sort_key, reverse=True)
    return signals
