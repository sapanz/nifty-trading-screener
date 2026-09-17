"""Money Flow Accumulation (runs every trading day after close).

Inspired by a YouTube demo of a proprietary tool's volume-delta-style
histogram marking price reversals - but built from a real, documented
indicator instead of guessing at that tool's actual formula, which isn't
public. See signals/indicators.py's add_money_flow_volume and
signals/config.py's Money Flow Accumulation section for the full honesty
caveat: this is Chaikin Money Flow (CMF), a close-position-in-range x
volume proxy for buying/selling pressure, not literal tick-classified
buy/sell volume - Upstox only exposes OHLCV, so there's no true volume
delta available to compute here.

A stock qualifies when:
  - it is above its 200 SMA (long-term uptrend)
  - it is testing support at its SMA_SWING (the same "pullback support"
    SMA Daily Swing uses) - low within MONEY_FLOW_SUPPORT_TOLERANCE of
    the SMA, closing back above it
  - Chaikin Money Flow (CMF, summed over MFV_LOOKBACK candles) is net
    positive (CMF_MIN_VALUE) and itself rising (CMF_SLOPE_LOOKBACK) -
    money is still flowing IN while price pulls back, not out: buyers
    absorbing the dip rather than distributing into it, a Wyckoff-style
    accumulation signature, not mere sideways drift
  - the candle closed properly (small upper wick, bullish) on volume at
    least at its own trailing average - not a thin, illiquid bounce

Entry is the signal candle's high (a buy-stop, the same construction
every other strategy here uses); stop-loss is the lower of the signal
candle's own low and the previous candle's low. Targets are risk-
multiples of that entry-to-SL distance (RISK_REWARD_TARGETS), the same
as Daily Swing.
"""
from __future__ import annotations

import pandas as pd

from signals import config
from signals.indicators import (
    add_avg_volume,
    add_money_flow_volume,
    add_sma,
    is_above_sma,
    is_bullish,
    is_proper_close,
    is_sma_rising,
    is_support_test,
    is_volume_candle,
)
from signals.models import Signal

VOL_COL = f"avg_vol{config.VOLUME_LOOKBACK}"
CMF_COL = f"cmf{config.MFV_LOOKBACK}"


def scan(daily_data: dict[str, pd.DataFrame]) -> list[Signal]:
    signals: list[Signal] = []
    min_len = config.SMA_LONG + config.MFV_LOOKBACK + config.CMF_SLOPE_LOOKBACK + 2

    for symbol, raw_df in daily_data.items():
        df = raw_df.copy()
        if len(df) < min_len:
            continue

        add_sma(df, config.SMA_LONG)
        add_sma(df, config.SMA_SWING)
        add_avg_volume(df, config.VOLUME_LOOKBACK)
        add_money_flow_volume(df, config.MFV_LOOKBACK)
        row = df.iloc[-1]
        prev_row = df.iloc[-2]

        if pd.isna(row.get(f"sma{config.SMA_LONG}")) or pd.isna(row.get(f"sma{config.SMA_SWING}")) or pd.isna(row.get(CMF_COL)):
            continue
        if not is_above_sma(row, f"sma{config.SMA_LONG}"):
            continue
        if not is_support_test(row, f"sma{config.SMA_SWING}", config.MONEY_FLOW_SUPPORT_TOLERANCE):
            continue

        cmf_val = float(row[CMF_COL])
        if cmf_val <= config.CMF_MIN_VALUE:
            continue  # net distribution, not accumulation - money isn't flowing in on this dip
        if not is_sma_rising(df, CMF_COL, lookback=config.CMF_SLOPE_LOOKBACK):
            continue  # accumulation must be strengthening, not just barely positive

        if not (is_bullish(row) and is_proper_close(row)):
            continue
        if not is_volume_candle(row, VOL_COL, config.MONEY_FLOW_VOLUME_MULTIPLIER):
            continue

        entry = float(row["high"])
        stop_loss = float(min(row["low"], prev_row["low"]))
        risk = entry - stop_loss
        if risk <= 0:
            continue

        targets = [round(entry + risk * mult, 2) for mult in config.RISK_REWARD_TARGETS]
        sma_swing_val = float(row[f"sma{config.SMA_SWING}"])

        signals.append(
            Signal(
                symbol=symbol,
                entry=round(entry, 2),
                stop_loss=round(stop_loss, 2),
                targets=targets,
                sort_key=cmf_val,
                note=f"CMF {cmf_val:+.2f} (rising) | Support SMA{config.SMA_SWING} {sma_swing_val:.2f}",
                candle_date=row.name.date(),
                extra={"cmf": round(cmf_val, 3)},
            )
        )

    signals.sort(key=lambda s: s.sort_key, reverse=True)
    return signals
