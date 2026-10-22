"""Backtest for Trendline Break Short on 5-minute candles.

Replays the live detection (signals/trendlines.find_break, the same function
strategy.scan uses) on every 5-minute bar of every session, handing it only
the bars up to that point, and simulates each signal forward:

  * a signal on bar t is entered at the OPEN of bar t+1 (the earliest a human
    or bot could act on a close), skipped if that open is already beyond the
    stop;
  * the stop and target keep the levels set at signal time;
  * if one bar touches both, the stop is assumed to hit first (conservative);
  * anything still open is squared off at the close of the TRENDLINE_SQUARE_OFF
    bar - intraday positions aren't carried overnight;
  * returns are net of TRENDLINE_ROUND_TRIP_COST_PCT.

One signal per stock per session (the first), so a single trendline break
can't be counted again on the next bar. Signals are also ranked within each
5-minute scan by trendline width (rank_at_bar, 1 = widest), because the
strategy's selling point is "when several stocks break at once, take the wider
trendline" - the summary tests that directly rather than assuming it.

Caveats: today's F&O list applied across the window (survivorship); daily-
and 5-minute-OHLC only, so intra-bar order of events is assumed, not known;
no slippage beyond the cost allowance; fills at the open are assumed.
"""
from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
from statistics import mean

import numpy as np
import pandas as pd

from signals import config
from signals.strategies.trendline_break_short import _hhmm, in_entry_window, session_arrays
from signals.trendlines import find_break, swing_lows

_MIN_SESSION_BARS = 30


@dataclass
class IntradayTrade:
    symbol: str
    date: pd.Timestamp
    bar_time: str            # start of the break candle
    entry: float             # actual fill (open of the next bar)
    stop: float
    target: float
    outcome: str             # "target", "stop", "square_off"
    exit_time: str
    exit_price: float
    gross_pct: float
    net_pct: float
    r_multiple: float        # gross return / the risk taken from the fill
    width_bars: int
    touches: int
    rise_pct: float
    break_depth_pct: float
    risk_pct: float
    vol_ratio: float
    rank_at_bar: int = 0     # by trendline width among signals on the same 5-min scan (1 = widest)
    signals_at_bar: int = 0


def _simulate(o, h, l, c, times, t: int, stop: float, target: float):
    """Walk bars after the break bar `t`. Returns (entry, outcome, exit idx, exit price) or None."""
    entry_idx = t + 1
    if entry_idx >= len(o):
        return None
    entry = float(o[entry_idx])
    if entry >= stop:
        return None
    square_off = _hhmm(config.TRENDLINE_SQUARE_OFF)
    for j in range(entry_idx, len(o)):
        if h[j] >= stop:
            return entry, "stop", j, stop
        if l[j] <= target:
            return entry, "target", j, target
        if times[j].time() >= square_off or j == len(o) - 1:
            return entry, "square_off", j, float(c[j])
    return None


def _trade_from(symbol, day, times, t, brk, vol_ratio, sim) -> IntradayTrade:
    entry, outcome, j, exit_price = sim
    gross = (1 - exit_price / entry) * 100
    risk_from_fill = (brk.stop / entry - 1) * 100
    return IntradayTrade(
        symbol=symbol, date=day, bar_time=times[t].strftime("%H:%M"), entry=entry, stop=brk.stop,
        target=brk.target, outcome=outcome, exit_time=times[j].strftime("%H:%M"), exit_price=exit_price,
        gross_pct=gross, net_pct=gross - config.TRENDLINE_ROUND_TRIP_COST_PCT,
        r_multiple=gross / risk_from_fill if risk_from_fill > 0 else 0.0,
        width_bars=brk.width_bars, touches=brk.touches, rise_pct=brk.rise_pct,
        break_depth_pct=brk.break_depth_pct, risk_pct=brk.risk_pct, vol_ratio=vol_ratio,
    )


def run_trendline_backtest(intraday_data: dict[str, pd.DataFrame], months: int | None = None) -> list[IntradayTrade]:
    trades: list[IntradayTrade] = []
    for symbol, df in intraday_data.items():
        if df.empty:
            continue
        if months is not None:
            df = df[df.index >= df.index[-1].normalize() - pd.DateOffset(months=months)]
        for day, session in df.groupby(df.index.normalize()):
            if len(session) < _MIN_SESSION_BARS:
                continue
            _, o, h, l, c, v = session_arrays(session)
            times = session.index
            pivots = swing_lows(l)
            for t in range(config.TRENDLINE_MIN_WIDTH_BARS, len(session) - 1):
                if not in_entry_window(times[t]):
                    continue
                brk = find_break(o, h, l, c, t, pivots)
                if brk is None:
                    continue
                window = v[brk.anchor_idx:t]
                ratio = float(v[t] / window.mean()) if len(window) and window.mean() > 0 else float("nan")
                sim = _simulate(o, h, l, c, times, t, brk.stop, brk.target)
                if sim is not None:
                    trades.append(_trade_from(symbol, day, times, t, brk, ratio, sim))
                break  # first break of the session only
    _rank_by_width(trades)
    return trades


def _rank_by_width(trades: list[IntradayTrade]) -> None:
    groups: dict[tuple, list[IntradayTrade]] = {}
    for tr in trades:
        groups.setdefault((tr.date, tr.bar_time), []).append(tr)
    for group in groups.values():
        group.sort(key=lambda tr: (tr.width_bars, tr.touches), reverse=True)
        for rank, tr in enumerate(group, start=1):
            tr.rank_at_bar, tr.signals_at_bar = rank, len(group)


# --- summary ---------------------------------------------------------------

def _stats(trades: list[IntradayTrade]) -> str:
    n = len(trades)
    if not n:
        return "n=0"
    wins = [t.net_pct for t in trades if t.net_pct > 0]
    losses = [-t.net_pct for t in trades if t.net_pct <= 0]
    pf = sum(wins) / sum(losses) if losses and sum(losses) > 0 else float("inf")
    return (
        f"n={n} win {len(wins) / n * 100:.0f}% avg {mean(t.net_pct for t in trades):+.3f}% "
        f"({mean(t.r_multiple for t in trades):+.2f}R) PF {pf:.2f}"
    )


def _outcomes(trades: list[IntradayTrade]) -> str:
    n = len(trades)
    return ", ".join(
        f"{name} {sum(t.outcome == name for t in trades) / n * 100:.0f}%" for name in ("target", "stop", "square_off")
    )


def _quartile_edges(trades, name):
    values = np.array([getattr(t, name) for t in trades], dtype=float)
    values = values[~np.isnan(values)]
    return [-np.inf, *np.unique(np.quantile(values, [0.25, 0.5, 0.75])), np.inf]


def summarize_trendline(trades: list[IntradayTrade], months: int) -> str:
    if not trades:
        return f"No trendline-break trades in the last {months} month(s)."
    days = len({t.date for t in trades})
    cut = sorted({t.date for t in trades})[max(int(days * 0.6) - 1, 0)]
    ins = [t for t in trades if t.date <= cut]
    oos = [t for t in trades if t.date > cut]
    lines = [
        f"{len(trades)} trades over {days} sessions (~{len(trades) / days:.1f}/day), last {months} month(s). "
        f"Net of {config.TRENDLINE_ROUND_TRIP_COST_PCT}% round-trip cost; entry at next bar's open; "
        f"{config.TRENDLINE_TARGET_R:g}R target, stop above the highest high since the last touch.",
        f"All: {_stats(trades)}  [{_outcomes(trades)}]",
        f"In-sample (to {cut.date()}): {_stats(ins)}",
        f"Out-of-sample: {_stats(oos)}",
        f"Gross (before costs): avg {mean(t.gross_pct for t in trades):+.3f}%",
        "",
        "<b>Does width matter?</b> (rank = by trendline width among stocks breaking on the same 5-min scan)",
        f"  Widest on its scan (rank 1): {_stats([t for t in trades if t.rank_at_bar == 1])}",
        f"  Top 3: {_stats([t for t in trades if t.rank_at_bar <= 3])}",
        f"  Rank 4+: {_stats([t for t in trades if t.rank_at_bar > 3])}",
        f"  Rank 1, scans with 2+ signals: {_stats([t for t in trades if t.rank_at_bar == 1 and t.signals_at_bar >= 2])}",
        f"  Last-ranked, scans with 2+ signals: {_stats([t for t in trades if t.rank_at_bar == t.signals_at_bar >= 2])}",
        "",
        "<b>By feature</b> (quartile buckets; compare in-sample vs out-of-sample)",
    ]
    for name in ("width_bars", "touches", "rise_pct", "break_depth_pct", "risk_pct", "vol_ratio"):
        edges = _quartile_edges(trades, name)
        lines.append(f"  {name}:")
        for lo, hi in zip(edges[:-1], edges[1:]):
            def pick(group):
                return [t for t in group if lo < getattr(t, name) <= hi]
            label = f"({lo:.2f}, {hi:.2f}]".replace("-inf", "-∞").replace("inf", "∞")
            lines.append(f"    {label}: IS {_stats(pick(ins))} | OOS {_stats(pick(oos))}")
    lines += ["", "<b>By hour of the break candle</b>"]
    for hour in sorted({t.bar_time[:2] for t in trades}):
        group = [t for t in trades if t.bar_time[:2] == hour]
        lines.append(f"  {hour}:xx  {_stats(group)}")
    lines += ["", "<b>By month</b>"]
    for month in sorted({t.date.strftime("%Y-%m") for t in trades}):
        group = [t for t in trades if t.date.strftime("%Y-%m") == month]
        lines.append(f"  {month}  {_stats(group)}")
    return "\n".join(lines)


def write_trendline_csv(trades: list[IntradayTrade], path: str) -> None:
    fields = list(IntradayTrade.__dataclass_fields__)
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for t in trades:
            row = asdict(t)
            row["date"] = t.date.date()
            writer.writerow({k: (round(v, 4) if isinstance(v, float) else v) for k, v in row.items()})
