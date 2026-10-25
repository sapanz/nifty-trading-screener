"""Backtest: 5-minute order-block retests, F&O stocks, in-session only.

The dashboard's intraday list (signals/intraday_blocks.py) in test form. For every
order block found on a stock's 5-minute chart:
  * it must form in the same session as its candle, and be retested the same session
    (zones never carry overnight);
  * enter on the first bar that reaches back into the zone, no later than
    INTRADAY_OB_LAST_ENTRY - a limit order at the zone's near edge, or the open if the bar
    gaps into the zone; skipped if it opens through the zone;
  * stop beyond the far edge (OB_STOP_BUFFER_PCT), target OB_TARGET_R x the risk, flat at
    the close of the INTRADAY_OB_EXIT_BAR bar; if a bar reaches both stop and target the
    stop is assumed first, and the entry bar can stop the trade out but not take profit;
  * trades whose risk (entry to stop) is outside INTRADAY_OB_MIN/MAX_RISK_PCT are skipped;
  * net of TRENDLINE_ROUND_TRIP_COST_PCT (intraday costs).
Judged against a random baseline (same stop/target distances and direction, entered on
random bars of the same stock in the same entry window, same exit time), by in-sample /
out-of-sample split, by whether the trade was WITH the day's direction (bullish zone while
the stock is up on the day), by stop size, time of day and impulse size.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from statistics import mean

import numpy as np
import pandas as pd

from signals import config
from signals.intraday_blocks import BAR_MINUTES
from signals.orderblock_backtest import _gross, _walk
from signals.orderblocks import OrderBlock, find_order_blocks, first_touch

COST = config.TRENDLINE_ROUND_TRIP_COST_PCT


@dataclass
class IBTrade:
    symbol: str
    direction: str
    day: pd.Timestamp
    entry_time: str
    entry: float
    outcome: str
    net_pct: float
    r_multiple: float
    risk_pct: float
    impulse_atr: float
    with_day: bool        # bullish zone and stock up on the day at formation (bearish: down on the day)
    minutes_in: int       # entry minutes after 09:15
    session: int = 0      # session number within the symbol's own series


def _minute(ts: pd.Timestamp) -> int:
    return ts.hour * 60 + ts.minute


def _hhmm(text: str) -> int:
    return int(text[:2]) * 60 + int(text[3:])


def _sessions(times: pd.DatetimeIndex) -> tuple[np.ndarray, dict]:
    """Session id per bar and, per session, (first bar, last bar to hold to = the exit bar)."""
    days = times.normalize()
    ids = pd.factorize(days)[0]
    exit_min = _hhmm(config.INTRADAY_OB_EXIT_BAR)
    info = {}
    for sid in np.unique(ids):
        pos = np.flatnonzero(ids == sid)
        mins = np.array([_minute(times[i]) for i in pos])
        ok = pos[mins <= exit_min]
        info[int(sid)] = (int(pos[0]), int(ok[-1]) if len(ok) else int(pos[-1]))
    return ids, info


def _walk_to_exit(direction, o, h, l, c, t, entry, stop, target, exit_idx):
    return _walk(direction, o, h, l, c, t, entry, stop, target, max(0, exit_idx - t), target_on_entry_bar=False)


def trade_for(block: OrderBlock, symbol, times, ids, info, o, h, l, c, prev_close) -> IBTrade | None:
    sid = ids[block.formed_idx]
    if ids[block.idx] != sid:
        return None
    first, exit_idx = info[int(sid)]
    t = first_touch(block, o, h, l, config.INTRADAY_OB_EXPIRY_BARS)
    if t is None or ids[t] != sid or t > exit_idx or _minute(times[t]) > _hhmm(config.INTRADAY_OB_LAST_ENTRY):
        return None
    buf = config.OB_STOP_BUFFER_PCT / 100
    if block.direction == "bull":
        if o[t] < block.low:
            return None
        entry, stop = float(min(o[t], block.high)), block.low * (1 - buf)
        risk = entry - stop
    else:
        if o[t] > block.high:
            return None
        entry, stop = float(max(o[t], block.low)), block.high * (1 + buf)
        risk = stop - entry
    risk_pct = risk / entry * 100
    if not (config.INTRADAY_OB_MIN_RISK_PCT <= risk_pct <= config.INTRADAY_OB_MAX_RISK_PCT):
        return None
    target = entry + config.OB_TARGET_R * risk if block.direction == "bull" else entry - config.OB_TARGET_R * risk
    x, exit_price, outcome = _walk_to_exit(block.direction, o, h, l, c, t, entry, stop, target, exit_idx)
    gross = _gross(block.direction, entry, exit_price)
    up_on_day = prev_close is not None and c[block.formed_idx] > prev_close
    return IBTrade(
        symbol=symbol, direction=block.direction, day=times[t].normalize(), entry_time=times[t].strftime("%H:%M"), entry=entry,
        outcome=outcome, net_pct=gross - COST, r_multiple=gross / risk_pct, risk_pct=risk_pct, impulse_atr=block.impulse_atr,
        with_day=(up_on_day if block.direction == "bull" else (prev_close is not None and not up_on_day)),
        minutes_in=_minute(times[t]) - (9 * 60 + 15), session=int(sid),
    )


def run_intraday_backtest(intraday: dict[str, pd.DataFrame]) -> tuple[list[IBTrade], dict]:
    trades: list[IBTrade] = []
    arrays: dict = {}
    for symbol, df in intraday.items():
        if len(df) < 200:
            continue
        times = df.index
        o, h, l, c = (df[col].to_numpy(dtype=float) for col in ("open", "high", "low", "close"))
        ids, info = _sessions(times)
        # previous session's last close per session
        last_close = {sid: float(c[info[sid][1]]) for sid in info}
        arrays[symbol] = (times, ids, info, o, h, l, c, last_close)
        for block in find_order_blocks(o, h, l, c, impulse_atr=config.INTRADAY_OB_IMPULSE_ATR):
            sid = int(ids[block.formed_idx])
            trade = trade_for(block, symbol, times, ids, info, o, h, l, c, last_close.get(sid - 1))
            if trade:
                trades.append(trade)
    return trades, arrays


def random_baseline(trades: list[IBTrade], arrays: dict, draws: int = 5, seed: int = 7) -> list[float]:
    rng = random.Random(seed)
    last_entry = _hhmm(config.INTRADAY_OB_LAST_ENTRY)
    out = []
    windows: dict[str, list[int]] = {}
    for tr in trades:
        times, ids, info, o, h, l, c, _ = arrays[tr.symbol]
        if tr.symbol not in windows:
            windows[tr.symbol] = [i for i in range(len(c)) if 15 <= _minute(times[i]) - 9 * 60 - 15 and _minute(times[i]) <= last_entry]
        eligible = windows[tr.symbol]
        if not eligible:
            continue
        for _ in range(draws):
            t = rng.choice(eligible)
            _, exit_idx = info[int(ids[t])]
            entry = float(o[t])
            risk = entry * tr.risk_pct / 100
            stop, target = (entry - risk, entry + config.OB_TARGET_R * risk) if tr.direction == "bull" else (entry + risk, entry - config.OB_TARGET_R * risk)
            x, price, _ = _walk_to_exit(tr.direction, o, h, l, c, t, entry, stop, target, exit_idx)
            out.append(_gross(tr.direction, entry, price) - COST)
    return out


def _stats(trades: list[IBTrade]) -> str:
    n = len(trades)
    if not n:
        return "n=0"
    nets = [t.net_pct for t in trades]
    wins, losses = sum(v for v in nets if v > 0), -sum(v for v in nets if v <= 0)
    return (f"n={n:<6} win {sum(v > 0 for v in nets) / n * 100:3.0f}%  avg {mean(nets):+.3f}%  {mean(t.r_multiple for t in trades):+.2f}R  "
            f"PF {wins / losses if losses > 0 else float('inf'):.2f}  [target {sum(t.outcome == 'target' for t in trades) / n * 100:.0f}% "
            f"stop {sum(t.outcome == 'stop' for t in trades) / n * 100:.0f}% time {sum(t.outcome == 'time' for t in trades) / n * 100:.0f}%]")


def _plain(values: list[float]) -> str:
    n = len(values)
    if not n:
        return "n=0"
    wins, losses = sum(v for v in values if v > 0), -sum(v for v in values if v <= 0)
    return f"n={n:<6} win {sum(v > 0 for v in values) / n * 100:3.0f}%  avg {mean(values):+.3f}%  PF {wins / losses if losses > 0 else float('inf'):.2f}"


def _clustered_t(trades: list[IBTrade]) -> str:
    """t-statistic of the mean net %, treating each trading day as one observation (trades on one day are correlated)."""
    by_day: dict = {}
    for t in trades:
        by_day.setdefault(t.day, []).append(t.net_pct)
    means = [mean(v) for v in by_day.values()]
    if len(means) < 5:
        return "t n/a"
    sd = float(np.std(means, ddof=1))
    return f"day-clustered t {mean(means) / (sd / len(means) ** 0.5):+.2f} ({len(means)} days)" if sd > 0 else "t n/a"


def summarize(trades: list[IBTrade], baseline: list[float]) -> str:
    if not trades:
        return "No intraday order-block trades."
    days = sorted({t.day for t in trades})
    cut = days[int(len(days) * 0.6) - 1]
    ins, oos = [t for t in trades if t.day <= cut], [t for t in trades if t.day > cut]
    lines = [
        f"{len(trades)} 5-minute order-block retests on {len({t.symbol for t in trades})} F&O stocks, {days[0].date()} to {days[-1].date()}; "
        f"limit entry at the zone, stop beyond it, {config.OB_TARGET_R:g}R target, flat at 15:15, net of {COST}% costs.",
        f"All trades:       {_stats(trades)}   {_clustered_t(trades)}",
        f"Random baseline:  {_plain(baseline)}   (same stop/target distances and direction, random entry bars in the same window)",
        f"In-sample (to {cut.date()}): {_stats(ins)}",
        f"Out-of-sample:    {_stats(oos)}   {_clustered_t(oos)}",
        "", "<b>By direction</b>",
    ]
    for d, label in (("bull", "Bullish block (long)"), ("bear", "Bearish block (short)")):
        group = [t for t in trades if t.direction == d]
        lines += [f"  {label:<24} {_stats(group)}", f"  {'  out-of-sample':<24} {_stats([t for t in group if t.day > cut])}"]
    lines += ["", "<b>With vs against the day's direction</b> (bullish zone while the stock is up on the day; bearish while down)"]
    for flag, label in ((True, "With the day"), (False, "Against the day")):
        group = [t for t in trades if t.with_day is flag]
        lines += [f"  {label:<24} {_stats(group)}", f"  {'  out-of-sample':<24} {_stats([t for t in group if t.day > cut])}"]
    lines += ["", "<b>Bullish, with the day, by stop size</b> (the 'minor SL' question; IS | OOS)"]
    core = [t for t in trades if t.direction == "bull" and t.with_day]
    for lo, hi in ((0, 0.3), (0.3, 0.5), (0.5, 0.8), (0.8, 3.1)):
        pick = lambda grp: [t for t in grp if lo <= t.risk_pct < hi]
        lines += [f"  stop {lo:.1f}-{hi:.1f}%".ljust(26) + f"IS  {_stats(pick([t for t in core if t.day <= cut]))}",
                  " " * 26 + f"OOS {_stats(pick([t for t in core if t.day > cut]))}"]
    lines += ["", "<b>By entry time</b> (all trades; IS | OOS)"]
    for lo, hi, label in ((0, 45, "09:15-10:00"), (45, 105, "10:00-11:00"), (105, 195, "11:00-12:30"), (195, 400, "12:30-14:30")):
        pick = lambda grp: [t for t in grp if lo <= t.minutes_in < hi]
        lines += [f"  {label:<24} IS  {_stats(pick([t for t in trades if t.day <= cut]))}", " " * 26 + f"OOS {_stats(pick([t for t in trades if t.day > cut]))}"]
    lines += ["", "<b>By impulse size</b> (all trades; quartiles; IS | OOS)"]
    values = [t.impulse_atr for t in trades]
    edges = [-np.inf, *sorted(set(np.quantile(values, [0.25, 0.5, 0.75]).round(2))), np.inf]
    for lo, hi in zip(edges[:-1], edges[1:]):
        pick = lambda grp: [t for t in grp if lo < t.impulse_atr <= hi]
        label = f"({lo:.2f}, {hi:.2f}]".replace("-inf", "-∞").replace("inf", "∞")
        lines += [f"  {label:<24} IS  {_stats(pick([t for t in trades if t.day <= cut]))}", " " * 26 + f"OOS {_stats(pick([t for t in trades if t.day > cut]))}"]
    return "\n".join(lines)
