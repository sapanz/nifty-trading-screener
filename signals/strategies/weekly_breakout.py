"""Weekly range-breakout strategy (runs Fridays after close).

A stock qualifies when, on the weekly timeframe:
  - it is above its 200-period SMA (long-term uptrend)
  - the preceding N weeks formed a tight consolidation range
  - this week's close broke out above that range's high
  - the breakout candle closed properly (small upper wick) on strong volume

Entry is the breakout candle's close; the initial stop-loss is anchored
to the breakout level itself (old resistance becomes new support). There
is no fixed profit target: a 12-month backtest with fixed 1x/2x
measured-move targets came back at breakeven before costs and net-losing
after them, and widening the second target changed nothing (simulate_forward
exits at the first target touched either way - a fixed target list can't
let a winner run past the nearest one). Instead, once the trade is
running, it's held as long as the weekly close stays above its own
BREAKOUT_TRAIL_SMA-week SMA - a classic Weinstein/Minervini-style trend
trail - and exits the week the close falls back below it.
"""
from __future__ import annotations

import pandas as pd

from signals import config
from signals.indicators import add_avg_volume, add_sma, is_above_sma, is_proper_close, is_volume_candle
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
        add_avg_volume(df, config.VOLUME_LOOKBACK)
        row = df.iloc[-1]

        if pd.isna(row.get(f"sma{config.SMA_LONG}")):
            continue
        if not is_above_sma(row, f"sma{config.SMA_LONG}"):
            continue

        prior = df.iloc[-1 - window : -1]
        range_high = float(prior["high"].max())
        range_low = float(prior["low"].min())
        if range_low <= 0:
            continue
        if (range_high - range_low) / range_low > config.BREAKOUT_RANGE_TIGHTNESS:
            continue  # prior weeks weren't a tight enough consolidation

        if not row["close"] > range_high:
            continue
        if not is_proper_close(row):
            continue
        if not is_volume_candle(row, VOL_COL, config.WEEKLY_VOLUME_MULTIPLIER):
            continue

        entry = float(row["close"])
        # Anchor SL to the breakout level itself (old resistance becomes new
        # support), not the bottom of the consolidation range - the latter
        # let risk balloon with however wide the whole range was, producing
        # a high win rate but large tail losses in backtesting.
        stop_loss = float(range_high * (1 - config.SL_BUFFER))
        risk = entry - stop_loss
        if risk <= 0:
            continue

        vol_ratio = float(row["volume"] / row[VOL_COL])

        signals.append(
            Signal(
                symbol=symbol,
                entry=round(entry, 2),
                stop_loss=round(stop_loss, 2),
                targets=[],  # no fixed target - trails on a close below its own SMA (see backtest.simulate_weekly_trailing_sma)
                sort_key=vol_ratio,
                note=(
                    f"Vol {vol_ratio:.1f}x avg | Range {range_low:.2f}-{range_high:.2f} ({window}w) | "
                    f"trail until close < {config.BREAKOUT_TRAIL_SMA}-week SMA"
                ),
            )
        )

    signals.sort(key=lambda s: s.sort_key, reverse=True)
    return signals
