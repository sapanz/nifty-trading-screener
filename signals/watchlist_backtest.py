"""Backtest for the Intraday Watchlist: what did each pick do the next day?

The watchlist has no entry, stop or target, so the trade simulator in
backtest.py has nothing to walk forward. This instead replays the real
intraday_watchlist.scan() on every historical date (handed only data up to
that date, exactly like a live run) and measures each pick's *next trading
day* from its daily candle:

  - open -> close, in the bias direction (what you'd have if you'd taken
    the open and held to the close: a bull is right when it rises)
  - gap (prior close -> open), in the bias direction, and the full
    close -> close, so overnight follow-through is visible separately
  - favorable / adverse excursion from the open (high/low vs open) - "did it
    move", and how much heat it took on the way
  - the day's range, the bias-free "did it move at all" measure

Every number is compared with all the other Nifty 500 stocks on the same
days (the baseline), because a pick that "moved 2%" means little if the
average stock did too, and a bull that closed up means little in a market
that rose that day.

What this cannot do, and the results are biased accordingly:
  - Historical corporate events are not filtered. NSE's calendar only looks
    forward, so past results/board-meeting days stay in (live, they'd be
    excluded). Only the price-based shock filter (big gap / wide day) still
    applies. Next-day event moves can therefore inflate both movement and
    volatility here relative to live.
  - It's daily OHLC only: it says how far a pick traveled, not whether you
    could have caught it - no entries, slippage or costs are modelled, and
    open -> close assumes you got the opening price.
  - The universe is *today's* Nifty 500 applied to the whole window
    (survivorship, same caveat as the other backtests).
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from statistics import mean, median

import numpy as np
import pandas as pd

from signals import config
from signals.backtest import _dates_in_window, _scan_as_of
from signals.strategies import intraday_watchlist


_LOOKBACK_ROWS = 300


@dataclass
class WatchlistOutcome:
    symbol: str
    signal_date: pd.Timestamp
    next_date: pd.Timestamp
    direction: str  # "bull" / "bear"
    setup: str
    open_to_close_pct: float   # bias-direction adjusted: positive = bias played out
    close_to_close_pct: float  # prior close -> next close, bias-direction adjusted
    gap_pct: float             # prior close -> next open, bias-direction adjusted
    favorable_pct: float       # best excursion from the open in the bias direction
    adverse_pct: float         # worst excursion from the open against the bias (>= 0)
    range_pct: float           # (high - low) / open, direction-free
    # open -> close minus the average Nifty 500 stock's open -> close that same
    # day (in the pick's direction): strips out "the whole market rose/fell
    # next day", which flatters bulls on up days and bears on down days.
    excess_open_to_close_pct: float = 0.0
    # The pick's signal-candle diagnostics (IntradayPick.metrics) plus the
    # market's median 1-day / 5-day move on the signal date.
    features: dict[str, float] = field(default_factory=dict)


@dataclass
class DayBaseline:
    """One stock's next day, direction-free - the comparison population."""
    open_to_close_pct: float
    range_pct: float


def _next_day(df: pd.DataFrame, asof: pd.Timestamp) -> tuple[pd.Timestamp, pd.Series] | None:
    """The row after `asof`, only if the stock actually traded on `asof`
    (otherwise its "signal candle" is stale and the next row isn't the day
    after the signal)."""
    pos = df.index.searchsorted(asof, side="left")
    if pos >= len(df) - 1 or df.index[pos] != asof:
        return None
    return df.index[pos + 1], df.iloc[pos + 1]


def _outcome(symbol: str, asof: pd.Timestamp, prior_close: float, next_date: pd.Timestamp,
             nxt: pd.Series, direction: str, setup: str, market_open_to_close: float = 0.0,
             features: dict[str, float] | None = None) -> WatchlistOutcome:
    o, h, l, c = float(nxt["open"]), float(nxt["high"]), float(nxt["low"]), float(nxt["close"])
    sign = 1.0 if direction == "bull" else -1.0
    favorable = (h / o - 1) * 100 if direction == "bull" else (o / l - 1) * 100
    adverse = (1 - l / o) * 100 if direction == "bull" else (h / o - 1) * 100
    open_to_close = sign * (c / o - 1) * 100
    return WatchlistOutcome(
        symbol=symbol, signal_date=asof, next_date=next_date, direction=direction, setup=setup,
        open_to_close_pct=open_to_close,
        close_to_close_pct=sign * (c / prior_close - 1) * 100,
        gap_pct=sign * (o / prior_close - 1) * 100,
        favorable_pct=favorable, adverse_pct=adverse,
        range_pct=(h - l) / o * 100,
        excess_open_to_close_pct=open_to_close - sign * market_open_to_close,
        features=dict(features or {}),
    )


def run_watchlist_backtest(
    daily_data: dict[str, pd.DataFrame], months: int, industries: dict[str, str],
) -> tuple[list[WatchlistOutcome], list[DayBaseline], int]:
    """Returns (outcomes, baseline, number of scanned days). The last day of
    the data has no "next day" yet, so picks on it are left out."""
    end = pd.Timestamp.today().normalize()
    start = end - pd.DateOffset(months=months)
    outcomes: list[WatchlistOutcome] = []
    baseline: list[DayBaseline] = []
    days = 0

    for asof in _dates_in_window(daily_data, start, end):
        # Only the trailing _LOOKBACK_ROWS candles per symbol: scan() needs ~60
        # rows to warm up, and the EMA/ATR seed weight left after 300 is
        # ~1e-7, so this matches a full-history scan while keeping a 5-year
        # replay (~1,250 dates x 500 symbols) from recomputing 6 years of
        # indicators every single date.
        sliced = {sym: df.tail(_LOOKBACK_ROWS) for sym, df in _scan_as_of(daily_data, asof).items()}
        # No historical event calendar exists - see module docstring.
        picks = intraday_watchlist.scan(sliced, industries, set()).picks
        days += 1

        day_baseline: list[DayBaseline] = []
        ret1: list[float] = []
        ret5: list[float] = []
        for symbol, df in sliced.items():
            if df.index[-1] != asof:
                continue
            if len(df) > 5:
                closes = df["close"]
                ret1.append((float(closes.iloc[-1]) / float(closes.iloc[-2]) - 1) * 100)
                ret5.append((float(closes.iloc[-1]) / float(closes.iloc[-6]) - 1) * 100)
            nxt = _next_day(daily_data[symbol], asof)
            if nxt is None:
                continue
            _, row = nxt
            day_baseline.append(DayBaseline(
                open_to_close_pct=(float(row["close"]) / float(row["open"]) - 1) * 100,
                range_pct=(float(row["high"]) - float(row["low"])) / float(row["open"]) * 100,
            ))
        baseline.extend(day_baseline)
        market_open_to_close = mean(b.open_to_close_pct for b in day_baseline) if day_baseline else 0.0
        market = {
            "mkt_ret1_pct": float(np.median(ret1)) if ret1 else float("nan"),
            "mkt_ret5_pct": float(np.median(ret5)) if ret5 else float("nan"),
        }
        for pick in picks:
            if pick.candle_date != asof.date():
                continue
            nxt = _next_day(daily_data[pick.symbol], asof)
            if nxt is None:
                continue
            next_date, row = nxt
            prior_close = float(sliced[pick.symbol]["close"].iloc[-1])
            outcomes.append(_outcome(
                pick.symbol, asof, prior_close, next_date, row, pick.direction, pick.setup,
                market_open_to_close, {**pick.metrics, **market},
            ))
    return outcomes, baseline, days


def _pct(part: int, whole: int) -> float:
    return part / whole * 100 if whole else 0.0


def _group_line(label: str, group: list[WatchlistOutcome]) -> str:
    n = len(group)
    if not n:
        return f"{label}: none"
    moved = sum(o.favorable_pct >= config.INTRADAY_BACKTEST_MOVE_PCT for o in group)
    return (
        f"{label} ({n}): open→close {mean(o.open_to_close_pct for o in group):+.2f}% avg "
        f"({median(o.open_to_close_pct for o in group):+.2f}% median), "
        f"vs mkt {mean(o.excess_open_to_close_pct for o in group):+.2f}%, "
        f"right way {_pct(sum(o.open_to_close_pct > 0 for o in group), n):.0f}% | "
        f"moved ≥{config.INTRADAY_BACKTEST_MOVE_PCT:g}% my way {_pct(moved, n):.0f}% | "
        f"avg best {mean(o.favorable_pct for o in group):.2f}% / worst {mean(o.adverse_pct for o in group):.2f}% | "
        f"gap {mean(o.gap_pct for o in group):+.2f}%"
    )


def summarize_watchlist(
    outcomes: list[WatchlistOutcome], baseline: list[DayBaseline], days: int, months: int,
) -> str:
    if not outcomes:
        return "No watchlist picks with a next day in this window."

    bulls = [o for o in outcomes if o.direction == "bull"]
    bears = [o for o in outcomes if o.direction == "bear"]
    bar = config.INTRADAY_BACKTEST_RANGE_PCT
    lines = [
        f"{len(outcomes)} picks over {days} trading days (~{len(outcomes) / days:.1f}/day): "
        f"{len(bulls)} bull, {len(bears)} bear. Next-day results, last {months} month(s):",
        "",
        "<b>Vs. every Nifty 500 stock, same days</b>",
        f"Avg day range: picks {mean(o.range_pct for o in outcomes):.2f}% vs all {mean(b.range_pct for b in baseline):.2f}%",
        f"Range ≥{bar:g}%: picks {_pct(sum(o.range_pct >= bar for o in outcomes), len(outcomes)):.0f}% "
        f"vs all {_pct(sum(b.range_pct >= bar for b in baseline), len(baseline)):.0f}%",
        f"Closed above open: all stocks {_pct(sum(b.open_to_close_pct > 0 for b in baseline), len(baseline)):.0f}% "
        f"| closed below: {_pct(sum(b.open_to_close_pct < 0 for b in baseline), len(baseline)):.0f}% "
        f"(what bulls / bears had to beat)",
        "",
        "<b>Did the bias work?</b> (return = open→close in the pick's direction)",
        _group_line("Bull", bulls),
        _group_line("Bear", bears),
        "",
        "<b>By setup</b>",
    ]
    for setup in sorted({o.setup for o in outcomes}):
        lines.append(_group_line(setup, [o for o in outcomes if o.setup == setup]))
    return "\n".join(lines)


def write_watchlist_csv(outcomes: list[WatchlistOutcome], path: str) -> None:
    base = [f for f in WatchlistOutcome.__dataclass_fields__ if f != "features"]
    feature_names = sorted({k for o in outcomes for k in o.features})
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=base + feature_names)
        writer.writeheader()
        for o in outcomes:
            row = {k: getattr(o, k) for k in base}
            row["signal_date"] = o.signal_date.date()
            row["next_date"] = o.next_date.date()
            row.update(o.features)
            writer.writerow({k: (round(v, 4) if isinstance(v, float) else v) for k, v in row.items()})


# Hold-out split for mine_features: bucket edges are learned on the earlier
# part of the window and the same buckets are then re-measured on the later
# part, so a "pattern" that is only noise in one half shows up as not
# repeating instead of being mistaken for an edge.
_IN_SAMPLE_FRACTION = 0.6
_BUCKETS = 4


def _bucket_stats(group: list[WatchlistOutcome]) -> str:
    if not group:
        return "n=0"
    return (
        f"n={len(group)} vs mkt {mean(o.excess_open_to_close_pct for o in group):+.2f}% "
        f"rw {_pct(sum(o.open_to_close_pct > 0 for o in group), len(group)):.0f}%"
    )


def mine_features(outcomes: list[WatchlistOutcome]) -> str:
    """For each direction and diagnostic feature: split the picks into
    quartile buckets (edges from the in-sample, i.e. earlier, 60% of days)
    and show each bucket's average next-day open->close vs the market
    ('vs mkt') and right-way rate, in-sample and out-of-sample side by side.
    A feature is only interesting if the same buckets look good in both."""
    if not outcomes:
        return "No picks to mine."
    dates = sorted({o.signal_date for o in outcomes})
    cut = dates[max(int(len(dates) * _IN_SAMPLE_FRACTION) - 1, 0)]
    lines = [f"Feature mining: in-sample = signal days up to {cut.date()}, out-of-sample = after."]
    for direction in ("bull", "bear"):
        group = [o for o in outcomes if o.direction == direction]
        ins = [o for o in group if o.signal_date <= cut]
        oos = [o for o in group if o.signal_date > cut]
        lines += ["", f"<b>{direction.title()}</b> overall: IS {_bucket_stats(ins)} | OOS {_bucket_stats(oos)}"]
        for setup in sorted({o.setup for o in group}):
            lines.append(
                f"  {setup}: IS {_bucket_stats([o for o in ins if o.setup == setup])} | "
                f"OOS {_bucket_stats([o for o in oos if o.setup == setup])}"
            )
        for name in sorted({k for o in group for k in o.features}):
            values = np.array([o.features.get(name, np.nan) for o in ins], dtype=float)
            values = values[~np.isnan(values)]
            if len(values) < _BUCKETS * 20:
                continue
            if name == "weekday":
                edges = [-0.5, 0.5, 1.5, 2.5, 3.5, 4.5]
            else:
                edges = [-np.inf, *np.unique(np.quantile(values, np.linspace(0, 1, _BUCKETS + 1)[1:-1])), np.inf]
            lines.append(f"  {name}:")
            for lo, hi in zip(edges[:-1], edges[1:]):
                def inside(o: WatchlistOutcome) -> bool:
                    v = o.features.get(name, np.nan)
                    return not np.isnan(v) and lo < v <= hi
                label = f"({lo:.2f}, {hi:.2f}]".replace("-inf", "-∞").replace("inf", "∞")
                lines.append(
                    f"    {label}: IS {_bucket_stats([o for o in ins if inside(o)])} | "
                    f"OOS {_bucket_stats([o for o in oos if inside(o)])}"
                )
    return "\n".join(lines)
