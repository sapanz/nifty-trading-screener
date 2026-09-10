"""Stock-futures OI buildup strategy (runs every trading day after close).

A stock's current-month futures contract qualifies when:
  - the underlying equity is above its 200 SMA (long-term uptrend context,
    from the deep equity daily history - the futures contract itself only
    has ~2-3 months of its own life to look at, nowhere near enough for a
    200-period anything, see fetch_fo_instrument_master/debug_futures.py)
  - the futures contract's own price closed up day-over-day
  - AND its open interest either rose (Long Buildup - fresh longs
    entering) or fell (Short Covering - shorts being forced out) day-over-
    day - the classic price+OI matrix. Both are bullish; the other two
    quadrants (price down either way: Short Buildup, Long Unwinding) are
    bearish and not traded here, since this system only goes long
  - the candle closed properly (small upper wick, bullish) on elevated
    volume

Entry is the futures candle's own high (a resting buy-stop, same as Daily
Swing); stop-loss is the lower of the signal candle's own low and the
previous candle's low. Targets are risk-multiples of that entry-to-SL
distance (FUTURES_RISK_REWARD_TARGETS).

Unlike the other three strategies, this one has NOT been backtested - a
futures contract only carries its own ~2-3 month trading history (Upstox
has no way to serve an expired contract's history, confirmed live via
tools/debug_futures.py), so there's no multi-year trade CSV to validate
against the way the equity strategies were tuned. This is deliberately
close to the textbook price+OI definition rather than dressed up with
unvalidated thresholds - see signals/config.py. Validate by watching live
signals accumulate, not by trusting these levels are already tuned.
"""
from __future__ import annotations

import pandas as pd

from signals import config
from signals.indicators import (
    add_avg_volume,
    add_sma,
    is_above_sma,
    is_bullish,
    is_proper_close,
    is_volume_candle,
)
from signals.models import Signal

VOL_COL = f"avg_vol{config.VOLUME_LOOKBACK}"


def scan(daily_data: dict[str, pd.DataFrame], futures_data: dict[str, pd.DataFrame]) -> list[Signal]:
    signals: list[Signal] = []

    for symbol, raw_fut in futures_data.items():
        equity_df = daily_data.get(symbol)
        if equity_df is None or len(equity_df) < config.SMA_LONG + 2:
            continue

        equity_df = equity_df.copy()
        add_sma(equity_df, config.SMA_LONG)
        equity_row = equity_df.iloc[-1]
        if pd.isna(equity_row.get(f"sma{config.SMA_LONG}")) or not is_above_sma(equity_row, f"sma{config.SMA_LONG}"):
            continue

        fut = raw_fut.copy()
        if len(fut) < config.VOLUME_LOOKBACK + 3 or "open_interest" not in fut.columns:
            continue

        add_avg_volume(fut, config.VOLUME_LOOKBACK)
        row = fut.iloc[-1]
        prev_row = fut.iloc[-2]

        if pd.isna(row.get(VOL_COL)) or pd.isna(row.get("open_interest")) or pd.isna(prev_row.get("open_interest")):
            continue
        if not row["close"] > prev_row["close"]:
            continue  # price must be up day-over-day - the other two OI quadrants are bearish

        prev_oi = float(prev_row["open_interest"])
        if prev_oi <= 0:
            continue
        oi_change_pct = (float(row["open_interest"]) - prev_oi) / prev_oi * 100
        if oi_change_pct > 0:
            buildup_type = "Long Buildup"
        elif oi_change_pct < 0:
            buildup_type = "Short Covering"
        else:
            continue  # unchanged OI is neither quadrant

        if not (is_proper_close(row) and is_bullish(row)):
            continue
        if not is_volume_candle(row, VOL_COL, config.FUTURES_VOLUME_MULTIPLIER):
            continue

        entry = float(row["high"])
        stop_loss = float(min(row["low"], prev_row["low"]))
        risk = entry - stop_loss
        if risk <= 0:
            continue

        targets = [round(entry + risk * mult, 2) for mult in config.FUTURES_RISK_REWARD_TARGETS]
        lot_size = row.get("lot_size")
        expiry = row.get("expiry")
        expiry_str = expiry.strftime("%d %b") if pd.notna(expiry) else "?"

        signals.append(
            Signal(
                symbol=symbol,
                entry=round(entry, 2),
                stop_loss=round(stop_loss, 2),
                targets=targets,
                sort_key=abs(oi_change_pct),
                note=(
                    f"{buildup_type} | OI {oi_change_pct:+.1f}% | "
                    f"Lot size {lot_size:.0f} | Expiry {expiry_str}"
                ),
                candle_date=row.name.date(),
                extra={"oi_change_pct": round(oi_change_pct, 2), "buildup_type": buildup_type},
            )
        )

    signals.sort(key=lambda s: s.sort_key, reverse=True)
    return signals
