"""Daily Swing strategy: Pocket Pivot (runs every trading day after close).

A different mechanism from the base/breakout/retest sequence this slot
used before (Accumulation Spring): instead of waiting for a multi-week
structure to resolve, this looks for a single day's volume anomaly that
is a well-known footprint of stealth institutional buying (Gil Morales &
Chris Kacher's "pocket pivot", used by O'Neil-style growth investors) -
a day where buying volume actually exceeds the heaviest SELLING day of
the past couple of weeks, while the stock is still trading close to its
last pullback low, not already extended into an obvious breakout. A large
buyer stepping in hard enough to outmuscle the worst recent selling day,
before the stock even breaks out to a new high and the crowd notices, is
a much more direct read of "why would an institution be buying right
now" than waiting for a chart pattern to complete.

A stock qualifies when, on the daily timeframe:
  - price is in a genuine uptrend structure: the two most recent confirmed
    swing lows (a low that's lower than the day before and after it, a
    real pullback low - not just any low candle) within
    DAILY_SWING_SWING_LOOKBACK days show a higher low, not a lower one -
    the raw-price definition of "uptrend" used here, no moving average
  - today's candle is bullish, closed properly, and its close sits no
    more than DAILY_SWING_MAX_EXTENSION above that most recent swing low
    - an entry still near support, not a chase after the move is obvious
  - today's volume is at least DAILY_SWING_MIN_VOLUME_RATIO x its own
    trailing average (rules out illiquid/dead-stock false positives) AND
    exceeds the heaviest single down-day's volume (a day that closed
    lower than the day before) in the trailing DAILY_SWING_DOWN_VOLUME_LOOKBACK
    days - the actual pocket-pivot test: today's buying overwhelms the
    worst recent selling

Entry is today's high (a buy-stop triggered the next day price trades up
to it); stop-loss is the lower of today's own low and the previous
candle's low. As with the version before it, there's no fixed profit
target - the stop trails up to the lowest low of the trailing
DAILY_SWING_TRAIL_LOOKBACK days once the trade is running, cutting losers
fast at the tight initial stop while letting winners run as far as the
trend carries them.

No moving averages or oscillators anywhere in this - every condition is a
direct read of price and volume.
"""
from __future__ import annotations

import pandas as pd

from signals import config
from signals.indicators import add_avg_volume, is_proper_close
from signals.models import Signal

VOL_COL = f"avg_vol{config.VOLUME_LOOKBACK}"


def _swing_low_mask(df: pd.DataFrame) -> pd.Series:
    """True for bars whose low is a confirmed local trough (lower than the
    bar immediately before and after) - a genuine pullback low, not just
    any candle that happens to dip."""
    low = df["low"]
    return (low < low.shift(1)) & (low < low.shift(-1))


def _recent_higher_low(df: pd.DataFrame, signal_iloc: int, lookback: int) -> float | None:
    """Look back over the `lookback` days before today's candle for the two
    most recent confirmed swing lows. Returns the more recent one's price
    if it sits above the one before it (a genuine higher low - the
    raw-price definition of an uptrend used here), or None if that
    structure isn't there."""
    window = df.iloc[max(0, signal_iloc - lookback) : signal_iloc]
    swing_lows = window.loc[_swing_low_mask(window), "low"]
    if len(swing_lows) < 2:
        return None
    recent, earlier = float(swing_lows.iloc[-1]), float(swing_lows.iloc[-2])
    if recent <= earlier:
        return None
    return recent


def _worst_down_day_volume(df: pd.DataFrame, signal_iloc: int, lookback: int) -> float:
    """The heaviest volume traded on any down day (close < prior close) in
    the `lookback` days before today - the volume a pocket pivot has to
    beat."""
    window = df.iloc[max(0, signal_iloc - lookback) : signal_iloc]
    is_down_day = window["close"] < window["close"].shift(1)
    down_volumes = window.loc[is_down_day, "volume"]
    return float(down_volumes.max()) if not down_volumes.empty else 0.0


def scan(daily_data: dict[str, pd.DataFrame]) -> list[Signal]:
    signals: list[Signal] = []
    min_len = max(config.DAILY_SWING_SWING_LOOKBACK, config.DAILY_SWING_DOWN_VOLUME_LOOKBACK) + config.VOLUME_LOOKBACK + 2

    for symbol, raw_df in daily_data.items():
        df = raw_df.copy()
        if len(df) < min_len:
            continue

        add_avg_volume(df, config.VOLUME_LOOKBACK)
        signal_iloc = len(df) - 1
        row = df.iloc[signal_iloc]
        prev_row = df.iloc[signal_iloc - 1]

        if not row["close"] > row["open"]:  # pivot day must be a buying day
            continue
        if not is_proper_close(row):
            continue

        recent_swing_low = _recent_higher_low(df, signal_iloc, config.DAILY_SWING_SWING_LOOKBACK)
        if recent_swing_low is None:
            continue
        if row["close"] > recent_swing_low * (1 + config.DAILY_SWING_MAX_EXTENSION):
            continue  # already run too far from the last pullback low to be an early entry

        avg_vol = row.get(VOL_COL)
        if avg_vol is None or pd.isna(avg_vol) or avg_vol <= 0:
            continue
        if row["volume"] < avg_vol * config.DAILY_SWING_MIN_VOLUME_RATIO:
            continue

        worst_down_volume = _worst_down_day_volume(df, signal_iloc, config.DAILY_SWING_DOWN_VOLUME_LOOKBACK)
        if worst_down_volume <= 0 or row["volume"] < worst_down_volume:
            continue  # the actual pocket-pivot test: buying must beat the worst recent selling day

        entry = float(row["high"])
        stop_loss = float(min(row["low"], prev_row["low"]))
        risk = entry - stop_loss
        if risk <= 0:
            continue

        vol_ratio = float(row["volume"] / worst_down_volume)
        extension_pct = (row["close"] / recent_swing_low - 1) * 100

        signals.append(
            Signal(
                symbol=symbol,
                entry=round(entry, 2),
                stop_loss=round(stop_loss, 2),
                targets=[],  # no fixed target - trailing stop lets winners run (see backtest.simulate_trailing)
                sort_key=vol_ratio,  # the biggest buying-vs-selling imbalance sorts first
                note=(
                    f"Pocket Pivot: volume {vol_ratio:.1f}x the worst down-day in "
                    f"{config.DAILY_SWING_DOWN_VOLUME_LOOKBACK}d, {extension_pct:.0f}% above last higher low | "
                    f"trail stop to last {config.DAILY_SWING_TRAIL_LOOKBACK}-day low, no fixed target"
                ),
            )
        )

    signals.sort(key=lambda s: s.sort_key, reverse=True)
    return signals
