"""Price Action Breakout: base -> high-volume breakout, entered immediately
on the breakout candle itself - no retest wait. Runs on daily AND weekly
candles (see scripts/run_signals.py and signals/backtest.py, which each
call scan() twice with a different set of window sizes below - the same
shape of setup, just zoomed to a different timeframe). No monthly leg - a
5-year backtest of the (since-replaced) retest-based version showed it
never fired under these thresholds (too little monthly history per stock
to form a base this strict), and Monthly ATH Breakout already covers that
timeframe.

A stock qualifies for a LONG when:
  - it is above its own 200-period SMA (long-term uptrend, same baseline
    filter every other strategy here uses)
  - _detect_base finds a **base** ending right before today: the longest
    window (between `pattern_min_lookback` and `pattern_max_lookback`)
    whose high-low band stays within PRICE_ACTION_RANGE_TIGHTNESS, with
    more volume on its up (green) candles than its down ones -
    accumulation, not mere drift (is_accumulation_range). The base's
    actual detected length varies per stock/signal - it isn't a fixed
    number - and _classify_shape labels its shape (Range, Ascending/
    Descending/Symmetrical Triangle, Rising/Falling Wedge) from the slopes
    of lines fit through its highs and lows; see the caveat on that in
    signals/config.py.
  - today itself closed above the base's high, bullish and properly closed
    (is_bullish + is_proper_close - the existing 20%-wick rule, unchanged),
    on clearly elevated volume (PRICE_ACTION_BREAKOUT_VOLUME_MULTIPLIER) -
    the "high volumes" the breakout itself needs. That's the whole
    trigger - no later retest/reclaim confirmation is waited for, per
    explicit direction: waiting for one made entries late relative to the
    breakout that actually mattered.

Entry is a resting buy-stop at today's (the breakout candle's) own high,
same construction every other strategy here uses - the trade only fills
once a later candle actually trades up through it. Stop-loss is today's
own low - but only up to `max_risk_pct` away from entry
(PRICE_ACTION_MAX_RISK_PCT_DAILY/_WEEKLY in config.py - split by
timeframe since a weekly candle's natural range runs structurally wider
than a daily one's); a signal whose natural stop sits further than that is
skipped outright rather than tightened to fit. Targets are a measured
move: the base's own height projected up from the breakout level - but
only if that reward is at least PRICE_ACTION_MIN_REWARD_RISK_RATIO (2:1)
times the actual risk being taken; the risk cap above bounds how much is
risked in absolute terms, this bounds whether the reward on offer
justifies it, and the two are checked independently since base height and
the breakout candle's own range aren't tied to each other. Signals sort by
the base's own range (high-low as a % of low), largest first - not by
volume or recency, per explicit request.

SHORT leg (breakdown, the exact mirror of the above): only considered for
symbols in `short_eligible` (the F&O-eligible universe - see
data.fetch_fo_eligible_symbols - since a cash-segment equity short is
intraday-only for retail in India; carrying one overnight means selling
the futures contract instead, so shorting a name with no futures market
isn't practically actionable the way a long always is). A stock qualifies
for a short when it's BELOW its 200 SMA, _detect_base finds a
*distribution* base (down-volume > up-volume - is_distribution_range,
the bearish mirror of is_accumulation_range) ending right before today,
and today itself closed below the base's low, bearish and properly closed
(is_bearish + is_proper_close_bearish), on elevated volume. Entry is a
resting sell-stop at today's own low; stop-loss is today's own high;
targets project the base's height down from the breakdown level. Every
threshold is identical to the long side's, just mirrored - no separate
short-specific tuning in config.py.

Replaces an earlier version of this strategy that waited for a later
candle to retest the breakout level and reclaim it before entering - per
explicit direction, that confirmation stage was making entries late
relative to the breakout that actually mattered. The retest-based
version's 5-year backtest numbers (daily: 2,654 signals, 48% win rate, PF
1.13; weekly: 190 signals, 63% win rate, PF 1.10, before the risk cap and
reward:risk gate existed) no longer describe this code - see git history
for that version if it's ever worth reverting to. This immediate-entry
version is unbacktested; every threshold in signals/config.py's Price
Action Breakout section is still a judgment call, not something mined
from a backtest CSV the way the older strategies' numbers were.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from signals import config
from signals.indicators import (
    add_avg_volume,
    add_sma,
    is_above_sma,
    is_accumulation_range,
    is_below_sma,
    is_bearish,
    is_bullish,
    is_distribution_range,
    is_proper_close,
    is_proper_close_bearish,
    is_volume_candle,
)
from signals.models import Signal


def _classify_shape(pattern: pd.DataFrame) -> str:
    """Label a base's shape from the slopes of straight lines fit through
    its highs and its lows - flat/rising/falling per line, per
    PRICE_ACTION_FLAT_SLOPE_PCT. A heuristic (slope sign/magnitude), not
    genuine trendline-touch-point geometry - see the caveat in
    signals/config.py."""
    n = len(pattern)
    x = np.arange(n)
    avg_price = float(pattern["close"].mean())
    if avg_price <= 0 or n < 2:
        return "Range"

    upper_slope = float(np.polyfit(x, pattern["high"].to_numpy(), 1)[0])
    lower_slope = float(np.polyfit(x, pattern["low"].to_numpy(), 1)[0])
    upper_pct = upper_slope / avg_price * 100
    lower_pct = lower_slope / avg_price * 100

    def direction(pct: float) -> str:
        if abs(pct) < config.PRICE_ACTION_FLAT_SLOPE_PCT:
            return "flat"
        return "rising" if pct > 0 else "falling"

    upper_dir, lower_dir = direction(upper_pct), direction(lower_pct)
    if upper_dir == "flat" and lower_dir == "flat":
        return "Range"
    if upper_dir == "flat" and lower_dir == "rising":
        return "Ascending Triangle"
    if upper_dir == "falling" and lower_dir == "flat":
        return "Descending Triangle"
    if upper_dir == "falling" and lower_dir == "rising":
        return "Symmetrical Triangle"
    if upper_dir == "rising" and lower_dir == "rising":
        return "Rising Wedge"
    if upper_dir == "falling" and lower_dir == "falling":
        return "Falling Wedge"
    return "Range"  # e.g. one line widening away from the other - not one of the six named shapes


def _rolling_bands(
    df: pd.DataFrame, pattern_min_lookback: int, pattern_max_lookback: int
) -> tuple[dict[int, np.ndarray], dict[int, np.ndarray]]:
    """Precompute, once per symbol, each candidate base length's rolling
    high-max / low-min as plain arrays - `highs[length][i]` is the max high
    over df.iloc[i-length+1:i+1] (so a base ending right before today's
    index reads index `today_idx - 1`). Used by _detect_base's search over
    every candidate length from pattern_max_lookback down to
    pattern_min_lookback - without this, each length's high/low band got
    re-sliced-and-aggregated from scratch on every one of those tries."""
    highs = {}
    lows = {}
    for length in range(pattern_min_lookback, pattern_max_lookback + 1):
        highs[length] = df["high"].rolling(length).max().to_numpy()
        lows[length] = df["low"].rolling(length).min().to_numpy()
    return highs, lows


def _detect_base(
    df: pd.DataFrame,
    breakout_idx: int,
    pattern_min_lookback: int,
    pattern_max_lookback: int,
    high_bands: dict[int, np.ndarray],
    low_bands: dict[int, np.ndarray],
    bearish: bool = False,
) -> tuple[pd.DataFrame, float, float] | None:
    """Find the longest tight base ending right before `breakout_idx`,
    between `pattern_min_lookback` and `pattern_max_lookback` candles long.
    Checked longest-first so the reported base length is the base's real
    extent, not just the shortest window that happens to qualify.

    `bearish=False` (default, the long side) requires accumulation
    (is_accumulation_range - more volume on up candles); `bearish=True`
    (the short side) requires its mirror, distribution (is_distribution_range
    - more volume on down candles) instead."""
    range_check = is_distribution_range if bearish else is_accumulation_range
    for length in range(pattern_max_lookback, pattern_min_lookback - 1, -1):
        start = breakout_idx - length
        if start < 0:
            continue
        pattern_high = high_bands[length][breakout_idx - 1]
        pattern_low = low_bands[length][breakout_idx - 1]
        if pd.isna(pattern_high) or pd.isna(pattern_low) or pattern_low <= 0:
            continue
        if (pattern_high - pattern_low) / pattern_low > config.PRICE_ACTION_RANGE_TIGHTNESS:
            continue
        pattern = df.iloc[start:breakout_idx]
        if not range_check(pattern):
            continue
        return pattern, float(pattern_high), float(pattern_low)
    return None


def scan(
    price_data: dict[str, pd.DataFrame],
    pattern_min_lookback: int,
    pattern_max_lookback: int,
    volume_lookback: int,
    max_risk_pct: float,
    short_eligible: set[str] | None = None,
) -> list[Signal]:
    """`short_eligible`, when given, is the set of symbols the short
    (breakdown) leg is allowed to fire on - see data.fetch_fo_eligible_symbols.
    None (the default) means no shorts at all, same as before this leg
    existed. The long leg is unaffected either way and still runs on every
    symbol in `price_data`.

    `max_risk_pct` is timeframe-specific (PRICE_ACTION_MAX_RISK_PCT_DAILY /
    _WEEKLY in config.py) - pass whichever matches the timeframe of
    `price_data`, since a weekly candle's natural range is structurally
    wider as a % of price than a daily one's.

    Today's own candle is the breakout candle - there's no retest/reclaim
    wait, so this only ever looks at whether *today* qualifies, not
    whether some earlier candle did."""
    signals: list[Signal] = []
    vol_col = f"avg_vol{volume_lookback}"
    min_len = config.SMA_LONG + pattern_max_lookback + 1
    sma_col = f"sma{config.SMA_LONG}"

    for symbol, raw_df in price_data.items():
        df = raw_df.copy()
        if len(df) < min_len:
            continue

        add_sma(df, config.SMA_LONG)
        add_avg_volume(df, volume_lookback)

        today_idx = len(df) - 1
        today = df.iloc[today_idx]

        if pd.isna(today.get(sma_col)):
            continue

        # Which direction(s) today's own candle even qualifies to trigger -
        # checked before the (relatively expensive) rolling-bands precompute
        # below, so a symbol matching neither skips that work entirely.
        directions = []
        if is_above_sma(today, sma_col) and is_bullish(today) and is_proper_close(today):
            directions.append(False)  # long
        if (
            short_eligible and symbol in short_eligible
            and is_below_sma(today, sma_col) and is_bearish(today) and is_proper_close_bearish(today)
        ):
            directions.append(True)  # short
        if not directions:
            continue
        if pd.isna(today.get(vol_col)):
            continue
        if not is_volume_candle(today, vol_col, config.PRICE_ACTION_BREAKOUT_VOLUME_MULTIPLIER):
            continue  # the breakout/breakdown itself must come on clearly elevated volume

        high_bands, low_bands = _rolling_bands(df, pattern_min_lookback, pattern_max_lookback)

        for bearish in directions:
            base = _detect_base(df, today_idx, pattern_min_lookback, pattern_max_lookback, high_bands, low_bands, bearish=bearish)
            if base is None:
                continue
            pattern, pattern_high, pattern_low = base

            if bearish:
                if not today["close"] < pattern_low:
                    continue
                entry = float(today["low"])
                stop_loss = float(today["high"])
                risk = stop_loss - entry
            else:
                if not today["close"] > pattern_high:
                    continue
                entry = float(today["high"])
                stop_loss = float(today["low"])
                risk = entry - stop_loss
            if risk <= 0:
                continue
            if risk / entry * 100 > max_risk_pct:
                continue  # today's own range sits too far from entry - a messy breakout candle, not a tight one

            pattern_height = pattern_high - pattern_low
            if bearish:
                targets = [round(pattern_low - pattern_height * mult, 2) for mult in config.PRICE_ACTION_TARGET_MULTIPLES]
                if targets[0] >= entry:
                    continue  # target already sits behind entry - skip rather than take a guaranteed-bad trade
                reward = entry - targets[0]
            else:
                targets = [round(pattern_high + pattern_height * mult, 2) for mult in config.PRICE_ACTION_TARGET_MULTIPLES]
                if targets[0] <= entry:
                    continue
                reward = targets[0] - entry
            if reward / risk < config.PRICE_ACTION_MIN_REWARD_RISK_RATIO:
                continue  # target sizing (base height) and stop sizing (today's range) are independent - this one didn't earn its risk

            vol_ratio = float(today["volume"] / today[vol_col])
            base_len = len(pattern)
            base_start = pattern.index[0].date()
            base_end = pattern.index[-1].date()
            range_pct = pattern_height / pattern_low * 100
            shape = _classify_shape(pattern)

            signals.append(
                Signal(
                    symbol=symbol,
                    entry=round(entry, 2),
                    stop_loss=round(stop_loss, 2),
                    targets=targets,
                    direction="short" if bearish else "long",
                    sort_key=range_pct,
                    note=(
                        f"{shape} | Base {base_len} candles ({base_start} to {base_end}), range {range_pct:.1f}% | "
                        f"Break{'down' if bearish else 'out'} {today['close']:.2f} ({vol_ratio:.1f}x vol)"
                    ),
                    candle_date=today.name.date(),
                    extra={
                        "vol_ratio": round(vol_ratio, 2),
                        "tightness_pct": round(range_pct, 2),
                        "breakout_type": shape,
                        "base_candles": base_len,
                        "base_start": base_start.isoformat(),
                        "base_end": base_end.isoformat(),
                    },
                )
            )

    signals.sort(key=lambda s: s.sort_key, reverse=True)
    return signals
