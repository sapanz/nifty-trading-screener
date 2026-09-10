"""Stock-futures OI buildup strategy (runs every trading day after close).

Trades both directions on a stock's current-month futures contract, using
the classic price+OI matrix - the day-over-day combination of the
contract's own price change and open-interest change:

  - price up + OI up   = **Long Buildup**   (fresh longs entering) -> long
  - price up + OI down = **Short Covering** (shorts forced out)    -> long
  - price down + OI up   = **Short Buildup**   (fresh shorts entering) -> short
  - price down + OI down = **Long Unwinding**  (longs exiting)         -> short

All four quadrants are traded, in the direction their quadrant implies -
unlike the other three strategies here, which are long-only.

Beyond the quadrant itself, a signal also needs:
  - the underlying equity trending the same way: above its 200 SMA for a
    long, below it for a short (long-term context from the deep equity
    daily history - the futures contract itself only has ~2-3 months of
    its own life to look at, nowhere near enough for a 200-period
    anything; see fetch_fo_instrument_master/debug_futures.py)
  - a properly-closed candle in the signal's own direction (small upper
    wick + bullish for a long, small lower wick + bearish for a short) on
    elevated volume

Entry is a resting stop order at the signal candle's own high (long) or
low (short) - the same buy-stop construction Daily Swing uses, mirrored
for the short side. Stop-loss is the opposite extreme of the signal/
previous candle; targets are risk-multiples of that entry-to-SL distance
(FUTURES_RISK_REWARD_TARGETS).

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
    is_bearish,
    is_below_sma,
    is_bullish,
    is_proper_close,
    is_proper_close_bearish,
    is_volume_candle,
)
from signals.models import Signal

VOL_COL = f"avg_vol{config.VOLUME_LOOKBACK}"


def _classify(price_up: bool, oi_change_pct: float) -> tuple[str, str] | None:
    """Map a (price direction, OI direction) pair to (trade direction,
    buildup-type label), or None for the unchanged-OI case (neither
    quadrant)."""
    if oi_change_pct == 0:
        return None
    if price_up:
        return ("long", "Long Buildup" if oi_change_pct > 0 else "Short Covering")
    return ("short", "Short Buildup" if oi_change_pct > 0 else "Long Unwinding")


def scan(daily_data: dict[str, pd.DataFrame], futures_data: dict[str, pd.DataFrame]) -> list[Signal]:
    signals: list[Signal] = []

    for symbol, raw_fut in futures_data.items():
        equity_df = daily_data.get(symbol)
        if equity_df is None or len(equity_df) < config.SMA_LONG + 2:
            continue

        equity_df = equity_df.copy()
        add_sma(equity_df, config.SMA_LONG)
        equity_row = equity_df.iloc[-1]
        sma_col = f"sma{config.SMA_LONG}"
        if pd.isna(equity_row.get(sma_col)):
            continue
        equity_above = is_above_sma(equity_row, sma_col)
        equity_below = is_below_sma(equity_row, sma_col)

        fut = raw_fut.copy()
        if len(fut) < config.VOLUME_LOOKBACK + 3 or "open_interest" not in fut.columns:
            continue

        add_avg_volume(fut, config.VOLUME_LOOKBACK)
        row = fut.iloc[-1]
        prev_row = fut.iloc[-2]

        if pd.isna(row.get(VOL_COL)) or pd.isna(row.get("open_interest")) or pd.isna(prev_row.get("open_interest")):
            continue

        prev_oi = float(prev_row["open_interest"])
        if prev_oi <= 0:
            continue
        oi_change_pct = (float(row["open_interest"]) - prev_oi) / prev_oi * 100
        price_up = row["close"] > prev_row["close"]

        classified = _classify(bool(price_up), oi_change_pct)
        if classified is None:
            continue
        direction, buildup_type = classified

        if direction == "long":
            if not equity_above:
                continue
            if not (is_proper_close(row) and is_bullish(row)):
                continue
        else:
            if not equity_below:
                continue
            if not (is_proper_close_bearish(row) and is_bearish(row)):
                continue

        if not is_volume_candle(row, VOL_COL, config.FUTURES_VOLUME_MULTIPLIER):
            continue

        if direction == "long":
            entry = float(row["high"])
            stop_loss = float(min(row["low"], prev_row["low"]))
            risk = entry - stop_loss
        else:
            entry = float(row["low"])
            stop_loss = float(max(row["high"], prev_row["high"]))
            risk = stop_loss - entry
        if risk <= 0:
            continue

        target_mult_sign = 1 if direction == "long" else -1
        targets = [round(entry + target_mult_sign * risk * mult, 2) for mult in config.FUTURES_RISK_REWARD_TARGETS]
        lot_size = row.get("lot_size")
        expiry = row.get("expiry")
        expiry_str = expiry.strftime("%d %b") if pd.notna(expiry) else "?"
        side = "BUY" if direction == "long" else "SELL"

        signals.append(
            Signal(
                symbol=symbol,
                entry=round(entry, 2),
                stop_loss=round(stop_loss, 2),
                targets=targets,
                direction=direction,
                sort_key=abs(oi_change_pct),
                note=(
                    f"{side} | {buildup_type} | OI {oi_change_pct:+.1f}% | "
                    f"Lot size {lot_size:.0f} | Expiry {expiry_str}"
                ),
                candle_date=row.name.date(),
                extra={"oi_change_pct": round(oi_change_pct, 2), "buildup_type": buildup_type},
            )
        )

    signals.sort(key=lambda s: s.sort_key, reverse=True)
    return signals
