"""Backtest: momentum stocks that are nearest to a daily zone, with and without a volume-thrust confirmation.

Every session t (signal at the close):
  * market gate  - equal-weight index of the liquid F&O stocks vs its own MA_DAYS-session average: above -> long (demand zones) with the
                   strongest stocks, below -> short (supply zones) with the weakest. (Same switch as signals/momentum.py.)
  * momentum     - average percentile rank of the 20- and 60-session returns among the liquid stocks (turnover >= MIN_TURNOVER_CR that
                   day); rank 1 = the best stock for the side the market allows. "Pool top N" = rank <= N.
  * zone         - the stock's nearest live zone on that side (signals/swing_blocks.scan_daily, zones with a close beyond the far edge
                   are already gone); gap = distance from the close to the zone's near edge (0 when inside).
  * volume thrust- average volume of the last 3 sessions / average volume of the 20 sessions before those (signals.momentum.volume_thrust).
Trade: a limit order at the zone's near edge, valid ORDER_DAYS sessions (a gap through it fills at the open), stop BUF beyond the far
edge, target k x R, TIME-exit after HOLD_DAYS sessions, stop wins any same-bar tie, round-trip cost COST charged in R. Each zone is
traded once per filter set (the first session it passes). Gap-open beyond the stop is booked as a stop from the planned entry (conservative).
"""
from __future__ import annotations

import os
from multiprocessing import Pool

import numpy as np
import pandas as pd

from signals import swing_blocks, swing_trendlines
from signals.backtest_stats import by_year, stats
from signals.momentum import MA_DAYS, MIN_TURNOVER_CR, volume_thrust

WINDOW = 260            # bars shown to the zone scan: about the year of candles the live tool uses
BUF, COST = 0.001, 0.001
ORDER_DAYS, HOLD_DAYS = 10, 20
THRUST_STEPS = (1.2, 1.5, 2.0)
POOLS = (20, 30, 50)


# ---------------------------------------------------------------- data on one shared axis
def prepare(frames: dict[str, pd.DataFrame]) -> dict:
    symbols = sorted(frames)
    norm = {s: pd.DatetimeIndex(frames[s].index).normalize() for s in symbols}
    dates = sorted(set().union(*[set(i) for i in norm.values()]))
    pos = {d: i for i, d in enumerate(dates)}
    shape = (len(dates), len(symbols))
    arr = {c: np.full(shape, np.nan) for c in ("open", "high", "low", "close", "volume")}
    for j, s in enumerate(symbols):
        rows = np.array([pos[d] for d in norm[s]])
        for c in arr:
            arr[c][rows, j] = frames[s][c].to_numpy(dtype=float) if c in frames[s] else np.nan
    return {"symbols": symbols, "dates": pd.DatetimeIndex(dates), **arr}


def market_state(P: dict) -> np.ndarray:
    """True where the equal-weight index of the previous session's liquid stocks closes above its 50-session average (no look-ahead)."""
    c, v = P["close"], P["volume"]
    liquid = np.vstack([np.zeros((1, c.shape[1]), bool), (c[:-1] * v[:-1] / 1e7 >= MIN_TURNOVER_CR)])
    ret = np.vstack([np.zeros((1, c.shape[1])), c[1:] / c[:-1] - 1])
    use = liquid & ~np.isnan(ret)
    daily = np.where(use, ret, 0).sum(axis=1) / np.maximum(use.sum(axis=1), 1)
    index = pd.Series(np.cumprod(1 + daily))
    return (index > index.rolling(MA_DAYS).mean()).to_numpy()


def _rank(x: np.ndarray) -> np.ndarray:
    out = np.full(len(x), np.nan)
    ok = ~np.isnan(x)
    out[ok] = pd.Series(x[ok]).rank(pct=True).to_numpy()
    return out


# ---------------------------------------------------------------- zones (the slow part - parallel)
_FR: dict = {}


def _zones_job(args):
    sym, date = args
    df = _FR[sym]
    i = df.index.searchsorted(date, side="right")
    window = df.iloc[max(0, i - WINDOW):i]
    if len(window) < 60 or window.index[-1].normalize() != date:
        return sym, date, []
    return sym, date, swing_blocks.scan_daily(window, swing_trendlines.analyze(window))


def _scan_zones(frames: dict, jobs: list, workers: int) -> dict:
    global _FR
    _FR = {s: df.set_axis(pd.DatetimeIndex(df.index)) for s, df in frames.items()}
    if workers > 1:
        with Pool(workers) as pool:
            out = pool.map(_zones_job, jobs, chunksize=500)
    else:
        out = [_zones_job(j) for j in jobs]
    return {(s, d): z for s, d, z in out}


# ---------------------------------------------------------------- one trade
def simulate(P: dict, j: int, t: int, dirn: int, entry: float, stop: float, k: float) -> dict | None:
    """-> None (no fill within ORDER_DAYS) or {R, entry_i, exit_i, reason, ret_pct, risk_pct}."""
    O, H, L, C = P["open"][:, j], P["high"][:, j], P["low"][:, j], P["close"][:, j]
    T = len(C)
    risk0 = abs(entry - stop)
    if not risk0 > 0:
        return None
    fill = None
    for d in range(t + 1, min(t + 1 + ORDER_DAYS, T)):
        if np.isnan(L[d]):
            continue
        if (L[d] <= entry) if dirn == 1 else (H[d] >= entry):
            fill = (d, min(O[d], entry) if dirn == 1 else max(O[d], entry))
            break
    if fill is None:
        return None
    d0, px = fill
    cost = COST * px / risk0
    tgt = entry + dirn * k * risk0

    def done(d: int, exit_px: float, reason: str, base: float = px) -> dict:
        return {"R": dirn * (exit_px - base) / risk0 - cost, "entry_i": d0, "exit_i": d, "reason": reason,
                "ret_pct": dirn * (exit_px / px - 1) * 100 - COST * 100, "risk_pct": risk0 / entry * 100}

    if (dirn == 1 and px <= stop) or (dirn == -1 and px >= stop):                 # opened beyond the stop: booked as a stop from the planned entry
        return done(d0, px, "stop-gap", base=entry)
    for d in range(d0, min(d0 + HOLD_DAYS + 1, T)):
        o, h, l, c = O[d], H[d], L[d], C[d]
        if np.isnan(c):
            continue
        if d > d0 and ((dirn == 1 and o <= stop) or (dirn == -1 and o >= stop)):
            return done(d, o, "stop-gap")
        if d > d0 and ((dirn == 1 and o >= tgt) or (dirn == -1 and o <= tgt)):
            return done(d, o, "target")
        if (dirn == 1 and l <= stop) or (dirn == -1 and h >= stop):
            return done(d, stop, "stop")
        if d > d0 and ((dirn == 1 and h >= tgt) or (dirn == -1 and l <= tgt)):
            return done(d, tgt, "target")
        if d - d0 >= HOLD_DAYS or d == T - 1:
            return done(d, c, "time")
    return None


# ---------------------------------------------------------------- candidates and trades
def candidates(P: dict, zones: dict, days: list[int], up: np.ndarray) -> pd.DataFrame:
    """One row per (session, stock) that has a live zone on the market's side: momentum rank, gap, volume thrust."""
    S, dates = P["symbols"], P["dates"]
    C, V = P["close"], P["volume"]
    rows = []
    for t in days:
        side_up = bool(up[t])
        d = "bull" if side_up else "bear"
        liquid = ~np.isnan(C[t]) & (C[t] * V[t] / 1e7 >= MIN_TURNOVER_CR) & ~np.isnan(C[t - 60]) & ~np.isnan(C[t - 20])
        score = (_rank(C[t] / C[t - 20] - 1) + _rank(C[t] / C[t - 60] - 1)) / 2
        valid = np.flatnonzero(liquid & ~np.isnan(score))
        if len(valid) < 20:
            continue
        order = valid[np.argsort(-score[valid] if side_up else score[valid], kind="stable")]
        rank_of = {j: n + 1 for n, j in enumerate(order)}
        for j in valid:
            best = None
            for z in zones.get((S[j], dates[t]), []):
                if z["d"] != d:
                    continue
                price = C[t, j]
                near, far = (z["hi"], z["lo"]) if side_up else (z["lo"], z["hi"])
                stop = far * (1 - BUF) if side_up else far * (1 + BUF)
                if not ((price - stop) if side_up else (stop - price)) > 0:
                    continue
                inside = z["lo"] <= price <= z["hi"]
                gap = 0.0 if inside else max((price / z["hi"] - 1) * 100 if side_up else (z["lo"] / price - 1) * 100, 0.0)
                cand = (gap, -z["sc"])
                if best is None or cand < best[0]:
                    best = (cand, z, near, stop)
            if best is None:
                continue
            (gap, _), z, near, stop = best
            rows.append({"t": t, "j": int(j), "date": dates[t], "up": side_up, "rank": rank_of[j], "gap": gap, "sc": z["sc"], "cf": z.get("cf", 0),
                         "vt": volume_thrust(V[: t + 1, j]), "key": f"{S[j]}|{d}|{z['t']}", "near": near, "stop": stop})
    return pd.DataFrame(rows)


def simulate_all(P: dict, ev: pd.DataFrame, k: float) -> pd.DataFrame:
    """Adds the trade columns for target k (NaN R where the limit order never filled)."""
    res = [simulate(P, r.j, r.t, 1 if r.up else -1, r.near, r.stop, k) for r in ev.itertuples()]
    out = ev.copy()
    out["filled"] = [x is not None for x in res]
    for col in ("R", "entry_i", "exit_i", "reason", "ret_pct", "risk_pct"):
        out[col] = [x[col] if x else np.nan for x in res]
    dates = P["dates"]
    ok = out["filled"].to_numpy()
    out["entry_date"] = pd.NaT
    out["exit_date"] = pd.NaT
    out.loc[ok, "entry_date"] = dates[out.loc[ok, "entry_i"].astype(int).to_numpy()]
    out.loc[ok, "exit_date"] = dates[out.loc[ok, "exit_i"].astype(int).to_numpy()]
    out["bars"] = out["exit_i"] - out["entry_i"]
    return out


def run(frames: dict[str, pd.DataFrame], k: float = 2.0, stride: int = 1, workers: int | None = None) -> tuple[dict, pd.DataFrame]:
    """-> (prepared arrays, every candidate row with its simulated trade for target k)."""
    P = prepare(frames)
    up = market_state(P)
    days = list(range(max(61, MA_DAYS + 1), len(P["dates"]) - 1, stride))
    S, dates, C, V = P["symbols"], P["dates"], P["close"], P["volume"]
    jobs = [(S[j], dates[t]) for t in days for j in np.flatnonzero(~np.isnan(C[t]) & (C[t] * V[t] / 1e7 >= MIN_TURNOVER_CR))]
    zones = _scan_zones(frames, jobs, workers if workers is not None else (os.cpu_count() or 1))
    ev = candidates(P, zones, days, up)
    return P, (simulate_all(P, ev, k) if not ev.empty else ev)


# ---------------------------------------------------------------- selecting and reporting
def select(ev: pd.DataFrame, side: str = "both", pool: int | None = None, rest_beyond: int | None = None, max_gap: float = 5.0,
           min_vt: float | None = None) -> pd.DataFrame:
    """The filled trades of one filter set. Each zone is counted once - the first session it passes the set."""
    if ev.empty:
        return ev
    m = ev["gap"] <= max_gap
    if side != "both":
        m &= ev["up"] == (side == "long")
    if pool is not None:
        m &= ev["rank"] <= pool
    if rest_beyond is not None:
        m &= ev["rank"] > rest_beyond
    if min_vt is not None:
        m &= ev["vt"] >= min_vt
    sel = ev[m].sort_values("t").drop_duplicates("key")
    return sel[sel["filled"]]


def _diff_ci(a: pd.DataFrame, b: pd.DataFrame, reps: int = 2000) -> tuple[float, float]:
    """90% month-block bootstrap range of mean R(a) - mean R(b)."""
    if a.empty or b.empty:
        return float("nan"), float("nan")
    ma = a["R"].groupby(a["exit_date"].dt.to_period("M").astype(str)).agg(["sum", "count"])
    mb = b["R"].groupby(b["exit_date"].dt.to_period("M").astype(str)).agg(["sum", "count"])
    months = sorted(set(ma.index) | set(mb.index))
    sa = ma.reindex(months, fill_value=0).to_numpy()
    sb = mb.reindex(months, fill_value=0).to_numpy()
    rng = np.random.default_rng(7)
    out = []
    for _ in range(reps):
        pick = rng.integers(0, len(months), len(months))
        na, nb = sa[pick, 1].sum(), sb[pick, 1].sum()
        if na and nb:
            out.append(sa[pick, 0].sum() / na - sb[pick, 0].sum() / nb)
    return (float(np.percentile(out, 5)), float(np.percentile(out, 95))) if out else (float("nan"), float("nan"))


HEADER = f"  {'group':<34}{'trades':>6} {'accuracy':>8} {'target':>6} {'RR':>5} {'avg R (90% range)':>21} {'PF':>5} {'maxDD':>6}"


def row(label: str, trades: pd.DataFrame) -> str:
    s = stats(trades) if len(trades) else None
    if s is None:
        return f"  {label:<34}{0:>6}   -"
    r = trades["R"].to_numpy()
    wins, losses = r[r > 0], r[r <= 0]
    rr = wins.mean() / -losses.mean() if len(wins) and len(losses) and losses.mean() < 0 else float("nan")
    lo, hi = s["ci"]
    ci = "n/a" if np.isnan(lo) else f"{lo:+.2f}..{hi:+.2f}"
    pf = "inf" if np.isinf(s["pf"]) else f"{s['pf']:.2f}"
    return (f"  {label:<34}{s['n']:>6} {s['win']:>8.0%} {(trades['reason'] == 'target').mean():>6.0%} {rr:>5.2f} "
            f"{s['avg_r']:>+7.2f}R {ci:>13} {pf:>5} {s['max_dd_r']:>5.0f}R")


def summarize(ev: pd.DataFrame, P: dict) -> str:
    if ev.empty:
        return "no setups"
    first, last = P["dates"][0].date(), P["dates"][-1].date()
    lines = [f"Momentum stocks nearest to a zone, 2R target, {len(P['symbols'])} F&O stocks, daily candles {first} to {last}.",
             "Trade = limit at the zone's near edge (10 sessions), stop beyond the far edge, 2R target, 20-session time exit, 0.10% cost in R.",
             "accuracy = filled trades ending above 0R; target = share that reached 2R; RR = avg win / avg loss; PF = sum of wins / sum of losses.",
             "Zones within 5% of the close. 'vt' = volume thrust: last-3-session volume / the 20 sessions before."]
    for side in ("long", "short"):
        base = select(ev, side)
        lines += [f"\n===== {side.upper()} ({'market above its 50-day average: strongest stocks' if side == 'long' else 'market below: weakest stocks'}) =====", HEADER,
                  row("every aligned zone setup (control)", base)]
        for n in POOLS:
            lines.append(row(f"momentum top {n}", select(ev, side, pool=n)))
            for v in THRUST_STEPS:
                lines.append(row(f"   + volume thrust >= {v:g}x", select(ev, side, pool=n, min_vt=v)))
        lines.append(row("rest of universe (rank > 50)", select(ev, side, rest_beyond=50)))
        lines.append(row("any stock + volume thrust >= 1.5x", select(ev, side, min_vt=1.5)))
        for n in (30,):
            for v in (None, 1.5):
                a, b = select(ev, side, pool=n, min_vt=v), select(ev, side, rest_beyond=50)
                lo, hi = _diff_ci(a, b)
                lines.append(f"  top {n}{'' if v is None else f' + vt>={v:g}x'} minus rest of universe: {a['R'].mean() - b['R'].mean():+.2f}R per trade (90% {lo:+.2f}..{hi:+.2f})"
                             if len(a) and len(b) else f"  top {n}: too few trades")
        lines.append("\n  By year (trades / avg R):   control | top 30 | top 30 + vt>=1.5x")
        groups = [select(ev, side), select(ev, side, pool=30), select(ev, side, pool=30, min_vt=1.5)]
        years = sorted({y for g in groups for y, *_ in (by_year(g) if len(g) else [])})
        yr = [{y: (n, avg) for y, n, _, avg in by_year(g)} if len(g) else {} for g in groups]
        for y in years:
            lines.append(f"    {y}: " + " | ".join(f"{g[y][0]:>4} / {g[y][1]:+.2f}R" if y in g else "      -      " for g in yr))
    lines.append("\nNote: only stocks listed today (survivorship bias), one market over the window, trades in the same month move together - read the 90% "
                 "range, not just the average. Rows are not independent: top 20 sits inside top 30 inside top 50.")
    return "\n".join(lines)
