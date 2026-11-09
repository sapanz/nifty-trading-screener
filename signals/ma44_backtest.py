"""Backtest of a simple trend-pullback strategy on daily candles: weekly 30 SMA + daily 44 SMA, entry when price takes support (long) /
resistance (short) at the 44 SMA.

Short side (the mirror of the long rules; shorts need F&O stocks):
  * the weekly 30 SMA is falling: lower than WEEKLY_SLOPE_WEEKS completed weeks ago (by at least WEEKLY_MIN_SLOPE_PCT) and lower than last week;
  * the daily 44 SMA is falling, not flat: lower than yesterday and at least MIN_SLOPE_PCT lower than SLOPE_DAYS sessions ago;
  * the pullback: the previous close was below the 44 SMA, today's high comes within TOUCH_PCT of it (or through it) and today CLOSES back below it
    on a down candle - price tried the average and was rejected.
Long side: everything mirrored (rising averages, price dips to the 44 SMA and closes back above it on an up candle).

Trade: entered at the NEXT session's open (the signal exists only after the close); stop beyond the rejection candle (and the average) by
STOP_BUFFER_PCT; exit at TARGET_R x risk, or - in "ma" mode - when a close reclaims the 44 SMA (the idea has failed), or after MAX_HOLD sessions.
A gap through the stop exits at the open; when a bar could have hit both stop and target the stop wins. Costs are charged in R.

The weekly average uses only weeks that were complete at the signal close (see _weekly_sma), so nothing here looks ahead.
`trend_only_control` takes the SAME trend conditions without the touch - the benchmark that says whether waiting for the 44 SMA adds anything.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from signals.backtest_stats import by_year, fmt, stats


@dataclass(frozen=True)
class Params:
    side: str = "short"                 # "short" | "long"
    sma_days: int = 44
    weekly_sma: int = 30
    slope_days: int = 10                # the daily SMA is compared with its value this many sessions ago
    min_slope_pct: float = 0.5          # ... and must have moved at least this much (so flat is not a trend)
    weekly_slope_weeks: int = 4
    weekly_min_slope_pct: float = 0.0
    touch_pct: float = 0.5              # the high (low) must come within this % of the SMA, or through it
    require_rejection_candle: bool = True
    stop_buffer_pct: float = 0.2
    min_risk_pct: float = 0.5
    max_risk_pct: float = 8.0
    exit_mode: str = "target"           # "target" | "ma"
    target_r: float = 2.0
    max_hold: int = 20
    cost_pct: float = 0.10              # round trip
    extra: dict = field(default_factory=dict, compare=False)


def _weekly_sma(df: pd.DataFrame, p: Params) -> tuple[np.ndarray, np.ndarray]:
    """(weekly SMA, its change over weekly_slope_weeks in %) per DAILY row, from weeks complete at that row's close: a week's value is stamped with its
    Friday and only applies from that date on, so a Wednesday sees last Friday's value."""
    weekly = df["close"].resample("W-FRI").last().dropna()
    sma = weekly.rolling(p.weekly_sma).mean()
    prior = sma.shift(p.weekly_slope_weeks)
    last_week = sma.shift(1)
    slope_pct = (sma / prior - 1) * 100
    weekly_down = (sma < last_week).astype(float).where(sma.notna() & last_week.notna())
    frame = pd.DataFrame({"sma": sma, "slope": slope_pct, "dir": weekly_down})
    aligned = frame.reindex(frame.index.union(df.index)).ffill().reindex(df.index)
    return aligned["slope"].to_numpy(), aligned["dir"].to_numpy()


def _arrays(df: pd.DataFrame, p: Params) -> dict:
    o, h, l, c = (df[x].to_numpy(dtype=float) for x in ("open", "high", "low", "close"))
    sma = pd.Series(c).rolling(p.sma_days).mean().to_numpy()
    wslope, wdown = _weekly_sma(df, p)
    ago = np.concatenate([np.full(p.slope_days, np.nan), sma[:-p.slope_days]])
    return {"o": o, "h": h, "l": l, "c": c, "sma": sma, "slope": (sma / ago - 1) * 100,
            "prev_sma": np.concatenate([[np.nan], sma[:-1]]), "wslope": wslope, "wdown": wdown}


def _trend_ok(a: dict, p: Params) -> np.ndarray:
    """The two trend conditions: daily 44 SMA moving the right way (and not flat), weekly 30 SMA moving the right way."""
    if p.side == "short":
        daily = (a["sma"] < a["prev_sma"]) & (a["slope"] <= -p.min_slope_pct)
        weekly = (a["wdown"] == 1) & (a["wslope"] <= -p.weekly_min_slope_pct)
    else:
        daily = (a["sma"] > a["prev_sma"]) & (a["slope"] >= p.min_slope_pct)
        weekly = (a["wdown"] == 0) & (a["wslope"] >= p.weekly_min_slope_pct)
    return daily & weekly & ~np.isnan(a["sma"]) & ~np.isnan(a["wslope"]) & ~np.isnan(a["wdown"])


def signals(df: pd.DataFrame, p: Params) -> np.ndarray:
    """Boolean per session: the pullback rejection happened on this session's candle (known at its close)."""
    a = _arrays(df, p)
    o, h, l, c, sma = a["o"], a["h"], a["l"], a["c"], a["sma"]
    prev_c = np.concatenate([[np.nan], c[:-1]])
    if p.side == "short":
        touched = h >= sma * (1 - p.touch_pct / 100)
        came_from = prev_c < a["prev_sma"]
        rejected = c < sma
        candle = c < o
    else:
        touched = l <= sma * (1 + p.touch_pct / 100)
        came_from = prev_c > a["prev_sma"]
        rejected = c > sma
        candle = c > o
    ok = _trend_ok(a, p) & touched & came_from & rejected
    return ok & candle if p.require_rejection_candle else ok


def trend_only_control(df: pd.DataFrame, p: Params, far_pct: float = 2.0, every: int = 5) -> np.ndarray:
    """The same trend conditions but price is NOT at the average: it sits at least far_pct on the trade's side of it (below for shorts), sampled every
    `every` sessions - what the same exits earn from just being in the trend."""
    a = _arrays(df, p)
    c, sma = a["c"], a["sma"]
    far = (c <= sma * (1 - far_pct / 100)) if p.side == "short" else (c >= sma * (1 + far_pct / 100))
    ok = _trend_ok(a, p) & far
    idx = np.zeros(len(c), dtype=bool)
    idx[::every] = True
    return ok & idx


def _simulate(a: dict, i: int, p: Params) -> dict | None:
    """Trade from the signal at session i: entry at the next open. None when there is no room (last bar) or the risk is outside the limits."""
    n = len(a["c"])
    if i + 1 >= n:
        return None
    sg = -1 if p.side == "short" else 1
    entry = a["o"][i + 1]
    if sg == -1:
        stop = max(a["h"][i], a["sma"][i]) * (1 + p.stop_buffer_pct / 100)
        risk = stop - entry
    else:
        stop = min(a["l"][i], a["sma"][i]) * (1 - p.stop_buffer_pct / 100)
        risk = entry - stop
    if not (risk > 0) or not (p.min_risk_pct <= risk / entry * 100 <= p.max_risk_pct):
        return None
    target = entry + sg * p.target_r * risk
    exit_px, reason, j = None, "time", min(i + 1 + p.max_hold, n - 1)
    for k in range(i + 1, min(i + 2 + p.max_hold, n)):
        o, h, l, c = a["o"][k], a["h"][k], a["l"][k], a["c"][k]
        if k > i + 1 and ((sg == -1 and o >= stop) or (sg == 1 and o <= stop)):
            exit_px, reason, j = o, "stop-gap", k; break
        if (sg == -1 and h >= stop) or (sg == 1 and l <= stop):
            exit_px, reason, j = stop, "stop", k; break
        if p.exit_mode == "target":
            if k > i + 1 and ((sg == -1 and o <= target) or (sg == 1 and o >= target)):
                exit_px, reason, j = o, "target-gap", k; break
            if (sg == -1 and l <= target) or (sg == 1 and h >= target):
                exit_px, reason, j = target, "target", k; break
        elif (sg == -1 and c > a["sma"][k]) or (sg == 1 and c < a["sma"][k]):
            exit_px, reason, j = c, "ma-reclaim", k; break
        if k - (i + 1) >= p.max_hold:
            exit_px, reason, j = c, "time", k; break
    if exit_px is None:
        exit_px, reason, j = a["c"][j], "time", j
    r = sg * (exit_px - entry) / risk - p.cost_pct / 100 * entry / risk
    return {"signal": i, "entry_i": i + 1, "exit_i": j, "entry": float(entry), "stop": float(stop), "exit": float(exit_px), "risk_pct": risk / entry * 100,
            "R": float(r), "ret_pct": float(sg * (exit_px / entry - 1) * 100 - p.cost_pct), "reason": reason, "bars": j - (i + 1)}


def run(frames: dict[str, pd.DataFrame], p: Params, control: bool = False) -> pd.DataFrame:
    """Every trade over every stock, one open position per stock at a time. control=True runs the trend-only benchmark instead."""
    out = []
    for symbol, df in frames.items():
        if df is None or len(df) < p.sma_days + p.weekly_sma * 5 + p.slope_days + 20:
            continue
        df = df.sort_index()
        sig = trend_only_control(df, p) if control else signals(df, p)
        a = _arrays(df, p)
        free = -1
        for i in np.flatnonzero(sig):
            if i <= free:
                continue
            t = _simulate(a, int(i), p)
            if t is None:
                continue
            free = t["exit_i"]
            t.update(symbol=symbol, signal_date=df.index[i], entry_date=df.index[t["entry_i"]], exit_date=df.index[t["exit_i"]])
            out.append(t)
    cols = ["symbol", "signal_date", "entry_date", "exit_date", "entry", "stop", "exit", "risk_pct", "R", "ret_pct", "reason", "bars"]
    return pd.DataFrame(out)[cols] if out else pd.DataFrame(columns=cols)


def summarize(frames: dict[str, pd.DataFrame], side: str = "short") -> str:
    """The headline numbers for the side: the strategy under three exits, the trend-only control, sensitivity to the 'not flat' rule, and by year."""
    lines = [f"Weekly 30 SMA + daily 44 SMA pullback, {side.upper()} side. {len(frames)} F&O stocks, daily candles "
             f"{min(df.index[0] for df in frames.values()).date()} to {max(df.index[-1] for df in frames.values()).date()}. Entry at the next open, 0.10% round-trip cost, "
             f"stop beyond the rejection candle. R = the risk taken on a trade; avg R above zero = profitable after costs."]
    variants = [("exit at 2R target (max 20 sessions)", Params(side=side, exit_mode="target", target_r=2.0)),
                ("exit at 3R target (max 20 sessions)", Params(side=side, exit_mode="target", target_r=3.0)),
                ("exit when a close reclaims the 44 SMA (max 20)", Params(side=side, exit_mode="ma"))]
    main = None
    lines.append("\nThe strategy:")
    for label, p in variants:
        t = run(frames, p)
        lines.append(fmt(label, stats(t)))
        if main is None:
            main = (p, t)
    p0, t0 = main
    lines.append("\nControl - the same trend (weekly + daily 44 SMA moving the right way) but price NOT at the average, same stop rule and exits:")
    lines.append(fmt("trend only, 2R target", stats(run(frames, p0, control=True))))
    lines.append("\nSensitivity of the 2R version to how steep the daily 44 SMA must be (0 = any slope the right way):")
    for slope in (0.0, 0.5, 1.0, 2.0):
        lines.append(fmt(f"44 SMA moved at least {slope}% over 10 sessions", stats(run(frames, Params(side=side, min_slope_pct=slope)))))
    lines.append("\nSensitivity of the 2R version to the entry candle:")
    lines.append(fmt("any close back through the average (no candle rule)", stats(run(frames, Params(side=side, require_rejection_candle=False)))))
    lines.append(fmt("must come within 0.25% of the average", stats(run(frames, Params(side=side, touch_pct=0.25)))))
    lines.append(fmt("may stop 1.5% short of the average", stats(run(frames, Params(side=side, touch_pct=1.5)))))
    if not t0.empty:
        lines.append("\nBy year (2R version): year, trades, win rate, avg R")
        for year, n, win, avg in by_year(t0):
            lines.append(f"  {year}: {n:>4} trades | win {win:.0%} | avg {avg:+.2f}R")
    lines.append("\nNote: only stocks listed today are tested (survivorship bias), long history of one market, and trades in the same month move together - so read the 90% range, not just the average.")
    return "\n".join(lines)
