"""Backtest: trade a return to an order block (daily charts, F&O stocks).

For every order block found (signals/orderblocks.py) the trade is:
  * the block becomes known at the close of the bar that broke structure;
  * from the next bar on, wait (up to OB_EXPIRY_BARS) for the first bar whose
    range reaches back into the zone - that is the retest;
  * enter there: a limit order at the zone's near edge (bullish: its high;
    bearish: its low), or at the open if the bar gaps into the zone; skip if
    it gaps clean through the zone (the block failed);
  * stop just beyond the zone's far edge (OB_STOP_BUFFER_PCT), target OB_TARGET_R
    times the risk, and a time exit after OB_MAX_HOLD_BARS; if one bar touches
    both stop and target the STOP is assumed to hit first (conservative);
  * net of ROUND_TRIP_COST_PCT; bearish blocks (shorts) only on F&O stocks.

Because a 2R target needs only ~1 in 3 trades to win to break even, win rate
alone says little - so results are shown as expectancy in R, profit factor,
by year, and against a RANDOM baseline: the same stop/target geometry, direction
and holding period, entered on random bars of the same stocks. If order blocks
carry information, they should beat that. Today's F&O list and today's
constituents are used across the whole window (survivorship), daily OHLC only.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from statistics import mean

import numpy as np
import pandas as pd

from signals import config
from signals.orderblocks import OrderBlock, find_order_blocks, first_touch


@dataclass
class OBTrade:
    symbol: str
    direction: str            # "bull" = long from a demand zone, "bear" = short from a supply zone
    ob_date: pd.Timestamp     # the block candle
    touch_date: pd.Timestamp  # the retest / entry bar
    entry: float
    stop: float
    target: float
    exit_date: pd.Timestamp
    outcome: str              # "target" / "stop" / "time"
    gross_pct: float
    net_pct: float
    r_multiple: float
    risk_pct: float
    impulse_atr: float
    width_pct: float
    wait_bars: int            # formation -> retest
    year: int


def _walk(direction: str, o, h, l, c, start: int, entry: float, stop: float, target: float, max_hold: int,
          target_on_entry_bar: bool = True):
    """Returns (exit index, exit price, outcome). Bar `start` is the entry bar.

    A limit order fills somewhere inside its bar and the bar's daily range doesn't say whether the
    high came before or after the fill, so for limit entries `target_on_entry_bar=False`: the entry
    bar can stop the trade out but cannot take profit (a random-walk check showed that otherwise
    the order-block trades beat a random baseline purely through this optimism)."""
    last = min(len(c) - 1, start + max_hold)
    for x in range(start, last + 1):
        if direction == "bull":
            if x > start and o[x] <= stop:
                return x, float(o[x]), "stop"
            if l[x] <= stop:
                return x, stop, "stop"
            if h[x] >= target and (target_on_entry_bar or x > start):
                return x, target, "target"
        else:
            if x > start and o[x] >= stop:
                return x, float(o[x]), "stop"
            if h[x] >= stop:
                return x, stop, "stop"
            if l[x] <= target and (target_on_entry_bar or x > start):
                return x, target, "target"
    return last, float(c[last]), "time"


def _gross(direction: str, entry: float, exit_: float) -> float:
    return (exit_ / entry - 1) * 100 if direction == "bull" else (1 - exit_ / entry) * 100


def trade_for(block: OrderBlock, symbol: str, dates, o, h, l, c) -> OBTrade | None:
    t = first_touch(block, o, h, l)
    if t is None:
        return None
    buf = config.OB_STOP_BUFFER_PCT / 100
    if block.direction == "bull":
        if o[t] < block.low:                                  # gapped clean through the zone: it failed
            return None
        entry = float(min(o[t], block.high))
        stop = block.low * (1 - buf)
        risk = entry - stop
    else:
        if o[t] > block.high:
            return None
        entry = float(max(o[t], block.low))
        stop = block.high * (1 + buf)
        risk = stop - entry
    risk_pct = risk / entry * 100
    if not (config.OB_MIN_RISK_PCT <= risk_pct <= config.OB_MAX_RISK_PCT):
        return None
    target = entry + config.OB_TARGET_R * risk if block.direction == "bull" else entry - config.OB_TARGET_R * risk
    x, exit_price, outcome = _walk(block.direction, o, h, l, c, t, entry, stop, target, config.OB_MAX_HOLD_BARS,
                                   target_on_entry_bar=False)
    gross = _gross(block.direction, entry, exit_price)
    return OBTrade(
        symbol=symbol, direction=block.direction, ob_date=dates[block.idx], touch_date=dates[t], entry=entry,
        stop=float(stop), target=float(target), exit_date=dates[x], outcome=outcome, gross_pct=gross,
        net_pct=gross - config.ROUND_TRIP_COST_PCT, r_multiple=gross / risk_pct, risk_pct=risk_pct,
        impulse_atr=block.impulse_atr, width_pct=block.width_pct, wait_bars=t - block.formed_idx, year=dates[t].year,
    )


def run_orderblock_backtest(
    daily_data: dict[str, pd.DataFrame], short_eligible: set[str] | None = None,
) -> tuple[list[OBTrade], dict[str, tuple]]:
    trades: list[OBTrade] = []
    arrays: dict[str, tuple] = {}
    for symbol, df in daily_data.items():
        if len(df) < 120:
            continue
        o, h, l, c = (df[col].to_numpy(dtype=float) for col in ("open", "high", "low", "close"))
        dates = list(df.index)
        arrays[symbol] = (dates, o, h, l, c)
        for block in find_order_blocks(o, h, l, c):
            if block.direction == "bear" and (short_eligible is None or symbol not in short_eligible):
                continue
            trade = trade_for(block, symbol, dates, o, h, l, c)
            if trade:
                trades.append(trade)
    return trades, arrays


def random_baseline(trades: list[OBTrade], arrays: dict[str, tuple], draws: int = 5, seed: int = 7) -> list[float]:
    """Net % returns of the same-geometry trades entered on random bars of the same stocks."""
    rng = random.Random(seed)
    out = []
    for tr in trades:
        dates, o, h, l, c = arrays[tr.symbol]
        n = len(c)
        for _ in range(draws):
            t = rng.randrange(60, n - config.OB_MAX_HOLD_BARS - 1)
            entry = float(o[t])
            risk = entry * tr.risk_pct / 100
            if tr.direction == "bull":
                stop, target = entry - risk, entry + config.OB_TARGET_R * risk
            else:
                stop, target = entry + risk, entry - config.OB_TARGET_R * risk
            x, exit_price, _ = _walk(tr.direction, o, h, l, c, t, entry, stop, target, config.OB_MAX_HOLD_BARS,
                                     target_on_entry_bar=False)   # same conservatism as the real trades
            out.append(_gross(tr.direction, entry, exit_price) - config.ROUND_TRIP_COST_PCT)
    return out


def _stats(trades: list[OBTrade]) -> str:
    n = len(trades)
    if not n:
        return "n=0"
    nets = [t.net_pct for t in trades]
    wins, losses = sum(v for v in nets if v > 0), -sum(v for v in nets if v <= 0)
    pf = wins / losses if losses > 0 else float("inf")
    return (f"n={n:<5} win {sum(v > 0 for v in nets) / n * 100:3.0f}%  avg {mean(nets):+.2f}%  "
            f"{mean(t.r_multiple for t in trades):+.2f}R  PF {pf:.2f}  [target {sum(t.outcome == 'target' for t in trades) / n * 100:.0f}% "
            f"stop {sum(t.outcome == 'stop' for t in trades) / n * 100:.0f}% time {sum(t.outcome == 'time' for t in trades) / n * 100:.0f}%]")


def _plain(values: list[float]) -> str:
    n = len(values)
    if not n:
        return "n=0"
    wins, losses = sum(v for v in values if v > 0), -sum(v for v in values if v <= 0)
    return f"n={n:<6} win {sum(v > 0 for v in values) / n * 100:3.0f}%  avg {mean(values):+.2f}%  PF {wins / losses if losses > 0 else float('inf'):.2f}"


def summarize(trades: list[OBTrade], baseline: list[float]) -> str:
    if not trades:
        return "No order-block trades."
    ordered = sorted(t.touch_date for t in trades)
    cut = ordered[int(len(ordered) * 0.6) - 1]
    ins, oos = [t for t in trades if t.touch_date <= cut], [t for t in trades if t.touch_date > cut]
    lines = [
        f"{len(trades)} order-block retests on {len({t.symbol for t in trades})} stocks, {ordered[0].date()} to {ordered[-1].date()}; "
        f"limit entry at the zone, stop beyond it, {config.OB_TARGET_R:g}R target, {config.OB_MAX_HOLD_BARS}-bar max hold, net of {config.ROUND_TRIP_COST_PCT}% costs.",
        f"All trades:        {_stats(trades)}",
        f"Random baseline:   {_plain(baseline)}   (same stop/target distances, direction and hold; random entry bars)",
        "  (calibration: on synthetic random-walk stocks the retest trades score ~0.1% BELOW this baseline - a limit fill "
        "comes after the bar has already moved towards the stop - so only a clear lead over it means something)",
        f"In-sample (to {cut.date()}): {_stats(ins)}",
        f"Out-of-sample:     {_stats(oos)}",
        "", "<b>By direction</b>",
    ]
    for d, label in (("bull", "Bullish block, long from demand"), ("bear", "Bearish block, short from supply (F&O)")):
        lines.append(f"  {label:<40} {_stats([t for t in trades if t.direction == d])}")
    lines += ["", "<b>By year</b>"]
    for y in sorted({t.year for t in trades}):
        lines.append(f"  {y}  {_stats([t for t in trades if t.year == y])}")
    lines += ["", "<b>By feature</b> (quartiles from all trades; in-sample | out-of-sample)"]
    for name in ("impulse_atr", "width_pct", "wait_bars", "risk_pct"):
        values = [getattr(t, name) for t in trades]
        edges = [-np.inf, *sorted(set(np.quantile(values, [0.25, 0.5, 0.75]).round(3))), np.inf]
        lines.append(f"  {name}:")
        for lo, hi in zip(edges[:-1], edges[1:]):
            pick = lambda grp: [t for t in grp if lo < getattr(t, name) <= hi]
            label = f"({lo:.2f}, {hi:.2f}]".replace("-inf", "-∞").replace("inf", "∞")
            lines.append(f"    {label:<18} IS {_stats(pick(ins))}")
            lines.append(f"    {'':<18} OOS {_stats(pick(oos))}")
    return "\n".join(lines)
