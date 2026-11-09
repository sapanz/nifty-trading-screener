"""Backtest: weekly demand / supply zones, bought (sold) when price comes back OUT of the zone on the daily chart.

Long side
  1. Weekly zone: a weekly bullish order block (signals/orderblocks.py on weekly candles) - the last down week before a strong, structure-breaking
     up-move of at least IMPULSE_ATR weekly ATRs: where buying was strong. It is known only once the week that confirms it has closed.
  2. The zone is watched on the daily chart. When a daily close falls INTO it (at or below the zone's high) the stock is "in the zone" - the screener list.
  3. When a later daily close comes back OUT above the zone's high, buy at the next open. Stop = the zone's low (less a small buffer).
  4. A close below the zone's low (less the buffer) kills the zone; zones expire after EXPIRY_WEEKS; each zone is traded at most once.
Short side: the exact mirror - a weekly supply zone, a daily close rising INTO it, entry when a close falls back OUT below its low, stop = the zone's high.

Exits (after the stop): TARGET_R x risk, or "trail" (a close beyond the prior TRAIL_DAYS-session extreme), or MAX_HOLD sessions. A gap through the stop exits at the
open; if one bar could have hit both stop and target the stop wins. Costs are charged in R. One open trade per stock.

`control=True` is the benchmark: for every real trade, random entries in the same stock with the same stop distance and exits - what a long (short) with that
geometry earns anywhere, i.e. the market's drift plus the stop / target arithmetic, with no zone. The zone only matters if the real trades beat it.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from signals.backtest_stats import by_year, fmt, stats
from signals.orderblocks import find_order_blocks


@dataclass(frozen=True)
class Params:
    side: str = "long"                  # "long" | "short"
    impulse_atr: float = 2.0            # weekly displacement that makes a zone ("where buying was strong")
    swing_k: int = 2                    # weekly swing = higher / lower than this many weeks either side
    expiry_weeks: int = 104
    in_zone: str = "close"              # "close": a daily CLOSE inside the zone | "touch": the daily low (high) reaches it
    stop_buffer_pct: float = 0.1
    min_risk_pct: float = 1.0
    max_risk_pct: float = 15.0
    exit_mode: str = "target"           # "target" | "trail"
    target_r: float = 2.0
    trail_days: int = 20
    max_hold: int = 60
    cost_pct: float = 0.10              # round trip


def weekly_bars(df: pd.DataFrame) -> pd.DataFrame:
    """Completed weekly candles (stamped with their Friday); a week whose Friday is after the last daily row is still forming and is dropped."""
    agg = {"open": "first", "high": "max", "low": "min", "close": "last"}
    if "volume" in df:
        agg["volume"] = "sum"
    weekly = df.resample("W-FRI").agg(agg).dropna(subset=["close"])
    return weekly[weekly.index <= df.index[-1]]


def find_zones(df: pd.DataFrame, p: Params) -> list[dict]:
    """The weekly zones of the side, each with the date it became known (its confirming week's Friday). Nothing here looks ahead: a block is built only from
    candles up to the bar that confirms it (see orderblocks.find_order_blocks)."""
    weekly = weekly_bars(df)
    if len(weekly) < 30:
        return []
    vol = weekly["volume"].to_numpy(float) if "volume" in weekly else None
    blocks = find_order_blocks(*(weekly[c].to_numpy(float) for c in ("open", "high", "low", "close")), swing_k=p.swing_k, impulse_atr=p.impulse_atr, volume=vol)
    want = "bull" if p.side == "long" else "bear"
    return [{"lo": float(b.low), "hi": float(b.high), "known": weekly.index[b.formed_idx], "formed": weekly.index[b.idx], "impulse": float(b.impulse_atr)}
            for b in blocks if b.direction == want]


def scan_zone(a: dict, dates: pd.DatetimeIndex, zone: dict, p: Params) -> tuple[int | None, int | None, str]:
    """Walk the daily candles after the zone became known. Returns (index of the daily close that came back OUT of the zone, index of the first
    session in the zone, outcome) where outcome is 'signal', 'failed' (a close beyond the far edge), 'expired' or 'never'."""
    n = len(dates)
    start = int(dates.searchsorted(zone["known"], side="right"))                  # first session after the confirming week closed
    end = int(dates.searchsorted(zone["known"] + pd.Timedelta(weeks=p.expiry_weeks), side="right"))
    long = p.side == "long"
    buf = p.stop_buffer_pct / 100
    lo, hi = zone["lo"], zone["hi"]
    entered = None
    for d in range(start, min(n, end)):
        c = a["c"][d]
        if (long and c < lo * (1 - buf)) or (not long and c > hi * (1 + buf)):
            return None, entered, "failed"
        inside = (c <= hi) if long else (c >= lo)
        if p.in_zone == "touch":
            inside = (a["l"][d] <= hi) if long else (a["h"][d] >= lo)
        if entered is None:
            if inside:
                entered = d
            continue
        if (long and c > hi) or (not long and c < lo):
            return d, entered, "signal"
    return None, entered, "expired" if entered is not None else "never"


def _simulate(a: dict, i: int, sg: int, stop: float, p: Params) -> dict | None:
    """Trade entered at the open of session i+1 with the given stop. None when there is no room or the risk is outside the limits."""
    n = len(a["c"])
    if i + 1 >= n:
        return None
    entry = a["o"][i + 1]
    risk = (entry - stop) if sg == 1 else (stop - entry)
    if not (risk > 0) or not (p.min_risk_pct <= risk / entry * 100 <= p.max_risk_pct):
        return None
    target = entry + sg * p.target_r * risk
    last = min(i + 1 + p.max_hold, n - 1)
    exit_px, reason, j = a["c"][last], "time", last
    for k in range(i + 1, last + 1):
        o, h, l, c = a["o"][k], a["h"][k], a["l"][k], a["c"][k]
        if k > i + 1 and ((sg == 1 and o <= stop) or (sg == -1 and o >= stop)):
            exit_px, reason, j = o, "stop-gap", k; break
        if (sg == 1 and l <= stop) or (sg == -1 and h >= stop):
            exit_px, reason, j = stop, "stop", k; break
        if p.exit_mode == "target":
            if k > i + 1 and ((sg == 1 and o >= target) or (sg == -1 and o <= target)):
                exit_px, reason, j = o, "target-gap", k; break
            if (sg == 1 and h >= target) or (sg == -1 and l <= target):
                exit_px, reason, j = target, "target", k; break
        elif k - p.trail_days >= 0:
            window = slice(k - p.trail_days, k)
            if (sg == 1 and c < a["l"][window].min()) or (sg == -1 and c > a["h"][window].max()):
                exit_px, reason, j = c, "trail", k; break
    r = sg * (exit_px - entry) / risk - p.cost_pct / 100 * entry / risk
    return {"signal": i, "entry_i": i + 1, "exit_i": j, "entry": float(entry), "stop": float(stop), "exit": float(exit_px), "risk_pct": risk / entry * 100,
            "R": float(r), "ret_pct": float(sg * (exit_px / entry - 1) * 100 - p.cost_pct), "reason": reason, "bars": j - (i + 1)}


def _arrays(df: pd.DataFrame) -> dict:
    return {k: df[c].to_numpy(dtype=float) for k, c in (("o", "open"), ("h", "high"), ("l", "low"), ("c", "close"))}


COLS = ["symbol", "signal_date", "entry_date", "exit_date", "entry", "stop", "exit", "risk_pct", "R", "ret_pct", "reason", "bars", "zone_lo", "zone_hi", "impulse"]


def run(frames: dict[str, pd.DataFrame], p: Params, control: bool = False, control_reps: int = 10, seed: int = 11) -> pd.DataFrame:
    """Every trade over every stock, one open position per stock at a time. control=True returns the random-entry benchmark instead (see the module doc).
    The result's .attrs carry the funnel: zones found, zones that reached the daily 'in the zone' state, signals, trades taken."""
    sg = 1 if p.side == "long" else -1
    buf = p.stop_buffer_pct / 100
    out, funnel = [], {"zones": 0, "in_zone": 0, "signals": 0}
    for symbol, df in frames.items():
        if df is None or len(df) < 300:
            continue
        df = df.sort_index()
        a, dates = _arrays(df), df.index
        zones = find_zones(df, p)
        funnel["zones"] += len(zones)
        free = -1
        for zone in sorted(zones, key=lambda z: z["known"]):
            sig, entered, outcome = scan_zone(a, dates, zone, p)
            funnel["in_zone"] += entered is not None
            if outcome != "signal" or sig is None:
                continue
            funnel["signals"] += 1
            if sig <= free:
                continue
            stop = zone["lo"] * (1 - buf) if sg == 1 else zone["hi"] * (1 + buf)
            t = _simulate(a, sig, sg, stop, p)
            if t is None:
                continue
            free = t["exit_i"]
            t.update(symbol=symbol, signal_date=dates[sig], entry_date=dates[t["entry_i"]], exit_date=dates[t["exit_i"]], zone_lo=zone["lo"], zone_hi=zone["hi"], impulse=zone["impulse"])
            out.append(t)
    trades = pd.DataFrame(out)[COLS] if out else pd.DataFrame(columns=COLS)
    trades.attrs.update(funnel, taken=len(trades))
    if not control or trades.empty:
        return trades
    rng = np.random.default_rng(seed)
    rows = []
    for t in trades.itertuples():
        df = frames[t.symbol].sort_index()
        a, dates = _arrays(df), df.index
        n = len(dates)
        for i in rng.integers(60, max(61, n - p.max_hold - 3), control_reps):
            entry = a["o"][i + 1]
            stop = entry * (1 - sg * t.risk_pct / 100)
            c = _simulate(a, int(i), sg, stop, p)
            if c is not None:
                c.update(symbol=t.symbol, signal_date=dates[i], entry_date=dates[c["entry_i"]], exit_date=dates[c["exit_i"]], zone_lo=np.nan, zone_hi=np.nan, impulse=np.nan)
                rows.append(c)
    ctrl = pd.DataFrame(rows)[COLS] if rows else pd.DataFrame(columns=COLS)
    return ctrl


def market_gate(frames: dict[str, pd.DataFrame], days: int = 50) -> pd.Series:
    """True on dates when the equal-weight index of the stocks is above its own `days`-session average (known at that close)."""
    closes = pd.concat({s: df["close"] for s, df in frames.items()}, axis=1).sort_index()
    index = (1 + closes.pct_change().mean(axis=1).fillna(0)).cumprod()
    return index > index.rolling(days).mean()


def _gated(trades: pd.DataFrame, gate: pd.Series, side: str) -> pd.DataFrame:
    up = gate.reindex(trades["signal_date"]).fillna(False).to_numpy()
    return trades[up] if side == "long" else trades[~up & gate.reindex(trades["signal_date"]).notna().to_numpy()]


def summarize(frames: dict[str, pd.DataFrame], side: str = "both") -> str:
    sides = ["long", "short"] if side == "both" else [side]
    first, last = min(df.index[0] for df in frames.values()), max(df.index[-1] for df in frames.values())
    lines = [f"Weekly zone, buy (sell) when the daily close comes back OUT of it, stop at the zone's far edge. {len(frames)} F&O stocks, daily candles {first.date()} to {last.date()}. "
             f"Entry at the next open, 0.10% round-trip cost. R = the risk taken (entry to stop); avg R above zero = profitable after costs."]
    gate = market_gate(frames)
    for s in sides:
        main = Params(side=s)
        t0 = run(frames, main)
        f = t0.attrs
        lines.append(f"\n===== {s.upper()} side ({'weekly demand zones' if s == 'long' else 'weekly supply zones'}) =====")
        lines.append(f"Funnel: {f.get('zones', 0)} weekly zones found -> {f.get('in_zone', 0)} reached 'in the zone' on the daily chart -> {f.get('signals', 0)} came back out -> {f.get('taken', 0)} tradable (risk {main.min_risk_pct:g}-{main.max_risk_pct:g}%)")
        lines.append("\nExits (zone strength: weekly thrust >= 2 ATR):")
        lines.append(fmt("exit at 2R target (max 60 sessions)", stats(t0)))
        lines.append(fmt("exit at 3R target", stats(run(frames, Params(side=s, target_r=3.0)))))
        lines.append(fmt("trail: close beyond the prior 20-session extreme", stats(run(frames, Params(side=s, exit_mode="trail")))))
        lines.append("\nControl - random entries in the same stocks with the same stop distance and the 2R exit (no zone):")
        lines.append(fmt("random entries", stats(run(frames, main, control=True))))
        lines.append("\nWith the market gate (long only when the equal-weight index is above its 50-day average, short only when below), 2R:")
        lines.append(fmt("gated by the market", stats(_gated(t0, gate, s))))
        lines.append("\nHow strong the weekly zone's thrust must be (2R exit):")
        for imp in (1.5, 3.0, 4.0):
            lines.append(fmt(f"weekly thrust >= {imp:g} ATR", stats(run(frames, Params(side=s, impulse_atr=imp)))))
        lines.append("\nWhat counts as 'in the zone' (2R exit):")
        lines.append(fmt("the daily low (high) merely touches the zone", stats(run(frames, Params(side=s, in_zone="touch")))))
        lines.append("\nStop distance (2R exit): the average risk taken was "
                     f"{t0['risk_pct'].mean():.1f}% of the entry price" if not t0.empty else "\nno trades")
        if not t0.empty:
            lines.append("\nBy year (2R version): year, trades, win rate, avg R")
            for year, n, win, avg in by_year(t0):
                lines.append(f"  {year}: {n:>4} trades | win {win:.0%} | avg {avg:+.2f}R")
    lines.append("\nNote: only stocks listed today are tested (survivorship bias), one market over 6 years, and trades in the same month move together - read the 90% range, not just the average.")
    return "\n".join(lines)
