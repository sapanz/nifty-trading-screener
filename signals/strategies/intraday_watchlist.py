"""Intraday Watchlist: a next-day bull/bear watchlist built from the daily
chart, for stocks with the potential to trend ("go in flow") intraday.

Deliberately not a trade generator - no entry, stop-loss or target, per
explicit direction: each pick is a symbol, a bull/bear bias and the reasons
behind it, and the actual entry is found intraday next day. So unlike every
other strategy here it doesn't produce Signal objects, and has nothing a
backtest could walk forward (no entry or exit to simulate).

A pick needs all of:
  - Trend: close above a rising 20 EMA above the 50 EMA (bull), or the
    mirror (bear) - trend days tend to go with the existing trend.
  - Sector not against it: the stock's sector (the Nifty 500 list's own
    "Industry" column) has a median 5-day change >= 0 for a bull, <= 0 for
    a bear. Sectors with too few members in the data aren't used either way.
  - One of two daily-chart setups:
      * Breakout / breakdown close - today closed beyond the prior 10 days'
        high (low), in the top (bottom) quarter of its own range, on volume
        well above average: momentum that often carries into the next day.
      * Coiled - an NR7 (narrowest range of the last 7 days) or an inside
        day, closing on the trend's side of its range: compression inside
        a trend, which tends to resolve with a range expansion.
  - No event or excessive volatility, per explicit direction:
      * no corporate event (results, board meeting, dividend...) near
        today on NSE's calendar - the caller passes these in, see
        signals/events.py;
      * no news-like shock today even without a calendar event (a gap
        beyond INTRADAY_MAX_GAP_PCT, or a range beyond
        INTRADAY_MAX_RANGE_ATR_MULTIPLE x the stock's usual ATR) - the
        only way to catch unscheduled news, which no calendar lists;
      * not chronically wild (ATR above INTRADAY_MAX_ATR_PCT of price).

Thresholds are untuned starting points (see config.py).
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from statistics import median

import pandas as pd

from signals import config
from signals.indicators import add_atr, add_avg_volume, add_ema

FAST_COL = f"ema{config.INTRADAY_EMA_FAST}"
SLOW_COL = f"ema{config.INTRADAY_EMA_SLOW}"
ATR_COL = f"atr{config.INTRADAY_ATR_PERIOD}"
VOL_COL = f"avg_vol{config.VOLUME_LOOKBACK}"

BREAKOUT_SETUP = "Breakout close"
BREAKDOWN_SETUP = "Breakdown close"
COILED_SETUP = "Coiled in trend"


@dataclass
class IntradayPick:
    symbol: str
    direction: str  # "bull" or "bear"
    setup: str
    reasons: list[str]
    sort_key: tuple = ()
    candle_date: date | None = None
    # Diagnostic features of the signal candle, direction-adjusted (positive =
    # with the pick's bias). Not shown to the user or gated on: the watchlist
    # backtest writes them next to each pick's next-day result so they can be
    # mined for what separates picks that followed through from those that
    # didn't, rather than tuning thresholds by guesswork.
    metrics: dict[str, float] = field(default_factory=dict)


@dataclass
class WatchlistResult:
    picks: list[IntradayPick] = field(default_factory=list)
    skipped_events: list[str] = field(default_factory=list)  # had a calendar event near today
    skipped_shock: list[str] = field(default_factory=list)   # news-like gap/range today

    @property
    def bulls(self) -> list[IntradayPick]:
        return [p for p in self.picks if p.direction == "bull"]

    @property
    def bears(self) -> list[IntradayPick]:
        return [p for p in self.picks if p.direction == "bear"]


def sector_strength(
    daily_data: dict[str, pd.DataFrame], industries: dict[str, str]
) -> dict[str, float]:
    """{sector: median % change over INTRADAY_SECTOR_LOOKBACK days} across
    each sector's members, for sectors with enough members to mean anything."""
    lookback = config.INTRADAY_SECTOR_LOOKBACK
    changes: dict[str, list[float]] = defaultdict(list)
    for symbol, df in daily_data.items():
        sector = industries.get(symbol)
        if not sector or len(df) <= lookback:
            continue
        past = float(df["close"].iloc[-1 - lookback])
        if past > 0:
            changes[sector].append((float(df["close"].iloc[-1]) / past - 1) * 100)
    return {
        sector: float(median(values))
        for sector, values in changes.items()
        if len(values) >= config.INTRADAY_SECTOR_MIN_MEMBERS
    }


def _trend(df: pd.DataFrame) -> str | None:
    # EMA alignment and slope are judged as of yesterday, so the trend has
    # to exist before today's candle - otherwise one strong close off a flat
    # base is enough to tip the fast EMA over the slow one and manufacture
    # a "trend" out of the very candle being evaluated.
    today, prev = df.iloc[-1], df.iloc[-2]
    fast_then = df[FAST_COL].iloc[-2 - config.INTRADAY_EMA_SLOPE_LOOKBACK]
    if prev[FAST_COL] > prev[SLOW_COL] and prev[FAST_COL] > fast_then and today["close"] > today[FAST_COL]:
        return "bull"
    if prev[FAST_COL] < prev[SLOW_COL] and prev[FAST_COL] < fast_then and today["close"] < today[FAST_COL]:
        return "bear"
    return None


def _metrics(df: pd.DataFrame, trend: str, usual_range: float, close_location: float,
             sector_change: float | None) -> dict[str, float]:
    today, prev = df.iloc[-1], df.iloc[-2]
    sign = 1.0 if trend == "bull" else -1.0
    prior = df.iloc[-1 - config.INTRADAY_BREAKOUT_LOOKBACK:-1]
    level = prior["high"].max() if trend == "bull" else prior["low"].min()
    day_range = today["high"] - today["low"]
    return {
        "vol_ratio": float(today["volume"] / today[VOL_COL]),
        "close_strength": close_location if trend == "bull" else 1 - close_location,
        "range_atr": float(day_range / usual_range),
        "signal_gap_pct": sign * (today["open"] / prev["close"] - 1) * 100,
        "signal_day_pct": sign * (today["close"] / prev["close"] - 1) * 100,
        "atr_pct": float(usual_range / prev["close"] * 100),
        "ema_spread_pct": sign * (prev[FAST_COL] / prev[SLOW_COL] - 1) * 100,
        "ext_ema20_pct": sign * (today["close"] / today[FAST_COL] - 1) * 100,
        "mom5_pct": sign * (today["close"] / df["close"].iloc[-6] - 1) * 100,
        "beyond_level_pct": sign * (today["close"] / level - 1) * 100,
        "sector_change_pct": sign * sector_change if sector_change is not None else float("nan"),
        "weekday": float(df.index[-1].weekday()),
    }


def _breakout(df: pd.DataFrame, trend: str, close_location: float) -> tuple[str, list[str], float] | None:
    today = df.iloc[-1]
    lookback = config.INTRADAY_BREAKOUT_LOOKBACK
    prior = df.iloc[-1 - lookback:-1]
    vol_ratio = float(today["volume"] / today[VOL_COL])
    if vol_ratio < config.INTRADAY_BREAKOUT_VOLUME_MULTIPLIER:
        return None
    strong = config.INTRADAY_STRONG_CLOSE_LOCATION
    quarter = round((1 - strong) * 100)
    if trend == "bull" and today["close"] > prior["high"].max() and close_location >= strong:
        return BREAKOUT_SETUP, [
            f"Closed above {lookback}-day high",
            f"Closed in top {quarter}% of day's range",
            f"Volume {vol_ratio:.1f}x {config.VOLUME_LOOKBACK}-day avg",
        ], vol_ratio
    if trend == "bear" and today["close"] < prior["low"].min() and close_location <= 1 - strong:
        return BREAKDOWN_SETUP, [
            f"Closed below {lookback}-day low",
            f"Closed in bottom {quarter}% of day's range",
            f"Volume {vol_ratio:.1f}x {config.VOLUME_LOOKBACK}-day avg",
        ], vol_ratio
    return None


def _coiled(df: pd.DataFrame, trend: str, close_location: float) -> list[str] | None:
    today, prev = df.iloc[-1], df.iloc[-2]
    ranges = (df["high"] - df["low"]).iloc[-config.INTRADAY_NR_LOOKBACK:]
    narrowest = ranges.iloc[-1] <= ranges.min()
    inside = today["high"] < prev["high"] and today["low"] > prev["low"]
    if not (narrowest or inside):
        return None
    if (trend == "bull" and close_location < 0.5) or (trend == "bear" and close_location > 0.5):
        return None
    reasons = []
    if narrowest:
        reasons.append(f"NR{config.INTRADAY_NR_LOOKBACK} - narrowest range in {config.INTRADAY_NR_LOOKBACK} days")
    if inside:
        reasons.append("Inside day - range within yesterday's")
    reasons.append("Closed in upper half of range" if trend == "bull" else "Closed in lower half of range")
    return reasons


def scan(
    daily_data: dict[str, pd.DataFrame],
    industries: dict[str, str],
    event_symbols: set[str],
) -> WatchlistResult:
    """`industries` maps symbol -> sector (see universe.fetch_nifty500_constituents);
    `event_symbols` are excluded outright (see events.fetch_event_symbols)."""
    result = WatchlistResult()
    sectors = sector_strength(daily_data, industries)
    sector_rank = {s: i + 1 for i, s in enumerate(sorted(sectors, key=sectors.get, reverse=True))}
    min_rows = (
        max(config.INTRADAY_EMA_SLOW, config.VOLUME_LOOKBACK, config.INTRADAY_BREAKOUT_LOOKBACK,
            config.INTRADAY_NR_LOOKBACK, config.INTRADAY_ATR_PERIOD)
        + config.INTRADAY_EMA_SLOPE_LOOKBACK + 2
    )

    for symbol, raw_df in daily_data.items():
        if len(raw_df) < min_rows:
            continue
        if symbol in event_symbols:
            result.skipped_events.append(symbol)
            continue

        df = raw_df.copy()
        add_ema(df, config.INTRADAY_EMA_FAST)
        add_ema(df, config.INTRADAY_EMA_SLOW)
        add_atr(df, config.INTRADAY_ATR_PERIOD)
        add_avg_volume(df, config.VOLUME_LOOKBACK)
        today, prev = df.iloc[-1], df.iloc[-2]
        # Yesterday's ATR is the yardstick, so today's own range can't
        # inflate the baseline it's being judged against.
        usual_range = prev[ATR_COL]
        if pd.isna(usual_range) or pd.isna(today[VOL_COL]) or pd.isna(today[SLOW_COL]) or today[VOL_COL] <= 0:
            continue
        day_range = today["high"] - today["low"]
        if day_range <= 0 or prev["close"] <= 0:
            continue

        gap_pct = abs(today["open"] / prev["close"] - 1) * 100
        if gap_pct > config.INTRADAY_MAX_GAP_PCT or day_range > config.INTRADAY_MAX_RANGE_ATR_MULTIPLE * usual_range:
            result.skipped_shock.append(symbol)
            continue
        if usual_range / prev["close"] * 100 > config.INTRADAY_MAX_ATR_PCT:
            continue

        trend = _trend(df)
        if trend is None:
            continue
        sector = industries.get(symbol)
        sector_change = sectors.get(sector)
        if sector_change is not None and (
            (trend == "bull" and sector_change < 0) or (trend == "bear" and sector_change > 0)
        ):
            continue

        close_location = (today["close"] - today["low"]) / day_range
        breakout = _breakout(df, trend, close_location)
        if breakout:
            setup, reasons, vol_ratio = breakout
            sort_key = (0, -vol_ratio)
        else:
            coiled = _coiled(df, trend, close_location)
            if coiled is None:
                continue
            setup, reasons = COILED_SETUP, coiled
            sort_key = (1, -abs(sector_change or 0.0))

        reasons.append(
            f"Above rising {config.INTRADAY_EMA_FAST} & {config.INTRADAY_EMA_SLOW} EMA" if trend == "bull"
            else f"Below falling {config.INTRADAY_EMA_FAST} & {config.INTRADAY_EMA_SLOW} EMA"
        )
        if sector_change is not None:
            reasons.append(
                f"Sector {sector} {sector_change:+.1f}% ({config.INTRADAY_SECTOR_LOOKBACK}d, "
                f"#{sector_rank[sector]} of {len(sectors)})"
            )
        result.picks.append(IntradayPick(
            symbol=symbol, direction=trend, setup=setup, reasons=reasons,
            sort_key=sort_key, candle_date=df.index[-1].date(),
            metrics=_metrics(df, trend, float(usual_range), float(close_location), sector_change),
        ))

    result.picks.sort(key=lambda p: p.sort_key)
    return result
