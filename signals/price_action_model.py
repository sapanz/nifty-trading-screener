"""Outcomes and the probability for the price-action setups (signals/price_action.py).

Every historical trigger is traded the same way the list suggests: entry at the next open, the planned stop and target, a time exit after
MAX_HOLD sessions, stop first on a same-bar tie, 0.10% round-trip cost. "Hit" = the target was reached before the stop.

Probability = a logistic regression of hit on what is known at the trigger close: the planned reward:risk and risk %, volume on the trigger
day, the market's trend (equal-weight index vs its 50- and 200-session averages), the stock's own trend (above its 200-session average,
60-session return rank, distance from the 52-week high) and the kind of setup. It is fitted on past setups only and checked on later ones
(`validate`). Open interest is NOT in it: there are only a few weeks of stored OI, far too little to measure its effect - it is shown next
to the probability as a tag instead.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from signals import price_action as pa

MAX_HOLD = 60
COST_PCT = 0.10
TREND_KINDS = ("multiyear",)       # these are not sold at a target: they are held while the weekly close stays above its 30-week average
TREND_WEEKS = 30
TREND_MAX_HOLD = 520               # about two years; a trade still above the average then is closed at the cap
HALF_LIFE_DAYS = 365        # in the fit a setup from a year ago counts half as much as one from today: hit rates drift with the market
KIND_FLAGS = [k for k in pa.KINDS if k != "zone"]                  # the zone is the baseline kind
FEATURES = ["log_rr", "risk_pct", "log_vol", "mkt50", "mkt200", "above200", "mom", "off_high", "is_short"] + [f"is_{k}" for k in KIND_FLAGS]


# ---------------------------------------------------------------- context shared by all stocks
def context(frames: dict[str, pd.DataFrame]) -> dict:
    """Market trend by date and each stock's momentum rank by date (percentile of the 20- and 60-session returns, averaged)."""
    close = pd.DataFrame({s: df["close"].set_axis(pd.DatetimeIndex(df.index).normalize()) for s, df in frames.items()}).sort_index()
    index = (1 + close.pct_change(fill_method=None).mean(axis=1).fillna(0)).cumprod()
    rank = (close.pct_change(20, fill_method=None).rank(axis=1, pct=True) + close.pct_change(60, fill_method=None).rank(axis=1, pct=True)) / 2
    return {"mkt50": index > index.rolling(50).mean(), "mkt200": index > index.rolling(200).mean(), "mom": rank}


def _row(symbol: str, t: dict, date: pd.Timestamp, ctx: dict) -> dict:
    """Features as the trade sees them: for a short the market / trend / momentum columns are flipped, so "1" always means "with the trade" and
    off_high is the distance from the 52-week extreme behind it (the low for a short)."""
    f = t["f"]
    short = t.get("side", "long") == "short"
    flip = (lambda x: 1.0 - x) if short else (lambda x: x)
    mom = ctx["mom"][symbol].get(date, np.nan) if symbol in ctx["mom"] else np.nan
    row = {"symbol": symbol, "kind": t["kind"], "candle": t["geo"].get("candle") if t["kind"] == "candle" else None, "tf": t["geo"].get("tf") if t["kind"] == "pause" else None, "side": "short" if short else "long", "date": date, "rr": t["rr"], "risk_pct": t["risk_pct"], "reward_pct": t["reward_pct"],
           "target_kind": t["target_kind"], "log_rr": float(np.log(max(t["rr"], 0.05))), "log_vol": float(np.log(np.clip(f["vol"], 0.2, 8))) if not np.isnan(f["vol"]) else np.nan,
           "vol": f["vol"], "mkt50": flip(float(bool(ctx["mkt50"].get(date, False)))), "mkt200": flip(float(bool(ctx["mkt200"].get(date, False)))),
           "above200": flip(f["above200"]), "mom": flip(float(mom)), "off_high": -f["off_low"] if short else f["off_high"], "is_short": float(short)}
    row.update({f"is_{k}": float(t["kind"] == k) for k in KIND_FLAGS})
    return row


# ---------------------------------------------------------------- one trade
def outcome(o: np.ndarray, h: np.ndarray, l: np.ndarray, c: np.ndarray, i: int, stop: float, target: float, p: pa.Params, side: str = "long") -> dict | None:
    """Entry at the open of i+1 with the stop and target planned at the close of i. None when the open leaves no trade (already beyond the
    target or the stop, or the risk from that open is outside the limits). A short is the mirror (stop above, target below)."""
    n = len(c)
    if i + 1 >= n:
        return None
    sg = -1 if side == "short" else 1
    entry = o[i + 1]
    risk = sg * (entry - stop)
    if not risk > 0 or sg * (target - entry) <= 0 or not (p.min_risk_pct <= risk / entry * 100 <= p.max_risk_pct):
        return None
    last = min(i + 1 + MAX_HOLD, n - 1)
    exit_px, reason, j = c[last], "time" if last == i + 1 + MAX_HOLD else "open", last
    for k in range(i + 1, last + 1):
        worst, best = (l[k], h[k]) if sg == 1 else (h[k], l[k])
        if k > i + 1 and sg * (o[k] - stop) <= 0:
            exit_px, reason, j = o[k], "stop", k
            break
        if sg * (worst - stop) <= 0:
            exit_px, reason, j = stop, "stop", k
            break
        if k > i + 1 and sg * (o[k] - target) >= 0:
            exit_px, reason, j = o[k], "target", k
            break
        if sg * (best - target) >= 0:
            exit_px, reason, j = target, "target", k
            break
    return {"entry": float(entry), "exit": float(exit_px), "R": float(sg * (exit_px - entry) / risk - COST_PCT / 100 * entry / risk), "hit": reason == "target", "reason": reason,
            "bars": j - (i + 1), "rr_actual": float(sg * (target - entry) / risk), "entry_i": i + 1, "exit_i": j}


# ---------------------------------------------------------------- the trend exit (multi-year breakouts)
def trend_exit_flags(dates: pd.DatetimeIndex, close: np.ndarray, weeks: int = TREND_WEEKS) -> np.ndarray:
    """Per daily bar: True when it is the last session of its week and that week's close is below the average of the last `weeks` weekly closes
    (that week included). The newest bar is never flagged - its week may not be over."""
    n = len(close)
    flags = np.zeros(n, dtype=bool)
    if n < 2:
        return flags
    wk = np.asarray(dates.to_period("W").astype(str))
    last_of_week = np.r_[wk[1:] != wk[:-1], False]
    idx = np.flatnonzero(last_of_week)
    wclose = pd.Series(close[idx])
    sma = wclose.rolling(weeks).mean().to_numpy()
    flags[idx] = wclose.to_numpy() < sma
    return flags


def trend_sma(dates: pd.DatetimeIndex, close: np.ndarray, weeks: int = TREND_WEEKS) -> float | None:
    """The 30-week average of the weekly closes as of the latest completed week (None with too little history)."""
    n = len(close)
    if n < 2:
        return None
    wk = np.asarray(dates.to_period("W").astype(str))
    idx = np.flatnonzero(np.r_[wk[1:] != wk[:-1], False])
    wclose = close[idx]
    return float(wclose[-weeks:].mean()) if len(wclose) >= weeks else None


def outcome_trend(o: np.ndarray, h: np.ndarray, l: np.ndarray, c: np.ndarray, i: int, entry_stop: float, flags: np.ndarray, max_hold: int = TREND_MAX_HOLD) -> dict:
    """The same long trade held with no target: out at the planned stop (stop first, gaps fill at the open), or at the open after the first weekly close
    below the 30-week average (`flags`), or at the close after `max_hold` sessions. The entry and the 1R risk are those of `outcome`."""
    n = len(c)
    entry = o[i + 1]
    risk = entry - entry_stop
    last = min(i + 1 + max_hold, n - 1)
    exit_px, reason, j = c[last], "time" if last == i + 1 + max_hold else "open", last
    for k in range(i + 1, last + 1):
        if k > i + 1 and o[k] <= entry_stop:
            exit_px, reason, j = o[k], "stop", k
            break
        if l[k] <= entry_stop:
            exit_px, reason, j = entry_stop, "stop", k
            break
        if flags[k] and k + 1 < n:
            exit_px, reason, j = o[k + 1], "trend", k + 1
            break
    return {"t_exit": float(exit_px), "t_R": float((exit_px - entry) / risk - COST_PCT / 100 * entry / risk), "t_out": reason, "t_bars": j - (i + 1), "t_exit_i": j}


def collect(frames: dict[str, pd.DataFrame], ctx: dict | None = None, p: pa.Params = pa.Params()) -> tuple[pd.DataFrame, dict[str, tuple[list, list]]]:
    """(every historical trigger with its features and outcome, {symbol: (triggers, pending)} for the live list). Triggers still open at the
    end of the data (no exit yet) have reason "open" and are left out of the fitting by `closed`."""
    ctx = ctx or context(frames)
    rows, scans = [], {}
    for symbol, df in frames.items():
        triggers, pending = pa.scan_stock(df, p)
        scans[symbol] = (triggers, pending)
        if not triggers:
            continue
        dates = pd.DatetimeIndex(df.index).normalize()
        o, h, l, c = (df[x].to_numpy(float) for x in ("open", "high", "low", "close"))
        flags = trend_exit_flags(dates, c) if any(t["kind"] in TREND_KINDS for t in triggers) else None
        for t in triggers:
            res = outcome(o, h, l, c, t["i"], t["stop"], t["target"], p, t.get("side", "long"))
            if res is None:
                continue
            # the trade as it is taken: a target for most kinds (same as the fixed-target view the probability is fitted on), the trend exit for the others
            tr = outcome_trend(o, h, l, c, t["i"], t["stop"], flags) if t["kind"] in TREND_KINDS and t.get("side", "long") == "long" else \
                {"t_exit": res["exit"], "t_R": res["R"], "t_out": res["reason"], "t_bars": res["bars"], "t_exit_i": res["exit_i"]}
            rows.append({**_row(symbol, t, dates[t["i"]], ctx), **res, **tr, "exit_date": dates[res["exit_i"]], "entry_date": dates[res["entry_i"]], "t_exit_date": dates[tr["t_exit_i"]],
                         "stop": t["stop"], "target": t["target"], "why": t["reason"], "geo": t["geo"]})          # the plan and the reason, for the backtest view
    return pd.DataFrame(rows), scans


def closed(ev: pd.DataFrame, min_rr: float = 0.0) -> pd.DataFrame:
    return ev[(ev["reason"] != "open") & (ev["rr"] >= min_rr)] if len(ev) else ev


# ---------------------------------------------------------------- logistic regression (numpy, ridge-regularised IRLS)
def fit(ev: pd.DataFrame, ridge: float = 1.0, asof: pd.Timestamp | None = None) -> dict | None:
    """Model for P(hit), recent setups weighted more (HALF_LIFE_DAYS, counted back from `asof`, default the last setup). None when there are
    too few setups to fit anything."""
    if len(ev) < 200 or ev["hit"].nunique() < 2:
        return None
    asof = asof if asof is not None else ev["date"].max()
    sw = 0.5 ** ((asof - ev["date"]).dt.days.to_numpy(float) / HALF_LIFE_DAYS)
    sw = sw / sw.mean()
    X = ev[FEATURES].to_numpy(float)
    med = np.nanmedian(X, axis=0)
    X = np.where(np.isnan(X), med, X)
    mu, sd = X.mean(axis=0), X.std(axis=0)
    sd = np.where(sd > 0, sd, 1.0)
    Z = np.column_stack([np.ones(len(X)), (X - mu) / sd])
    y = ev["hit"].to_numpy(float)
    beta = np.zeros(Z.shape[1])
    pen = np.eye(Z.shape[1]) * ridge
    pen[0, 0] = 0.0
    for _ in range(50):
        prob = 1 / (1 + np.exp(-Z @ beta))
        wts = sw * prob * (1 - prob)
        step = np.linalg.solve(Z.T @ (Z * wts[:, None]) + pen, Z.T @ (sw * (y - prob)) - pen @ beta)
        beta += step
        if np.abs(step).max() < 1e-8:
            break
    miss = ~ev["hit"].to_numpy(bool)
    return {"features": FEATURES, "beta": beta.tolist(), "mu": mu.tolist(), "sd": sd.tolist(), "median": med.tolist(), "n": int(len(ev)),
            "base_rate": float(np.average(y, weights=sw)), "miss_r": float(np.average(ev["R"].to_numpy()[miss], weights=sw[miss])) if miss.any() else -1.0}


def predict(model: dict, rows: pd.DataFrame) -> np.ndarray:
    X = rows[model["features"]].to_numpy(float)
    X = np.where(np.isnan(X), np.asarray(model["median"]), X)
    Z = np.column_stack([np.ones(len(X)), (X - np.asarray(model["mu"])) / np.asarray(model["sd"])])
    return 1 / (1 + np.exp(-Z @ np.asarray(model["beta"])))


def expected_r(model: dict, prob: np.ndarray, rr: np.ndarray) -> np.ndarray:
    """Expected result in R: the target's reward when hit, the average result of the past misses otherwise (stops and time exits), less nothing
    more - costs are already inside both."""
    return prob * rr + (1 - prob) * model["miss_r"]


def validate(ev: pd.DataFrame, bins: int = 4, min_train: int = 300) -> dict | None:
    """Walk forward a calendar year at a time: each year's setups are scored by a model fitted only on setups that had already EXITED before
    that year began. Returns how the predicted probability matched what happened, pooled over the years."""
    ev = ev.sort_values("date")
    parts = []
    for year in sorted(ev["date"].dt.year.unique()):
        start = pd.Timestamp(year=int(year), month=1, day=1)
        train, test = ev[ev["exit_date"] < start], ev[ev["date"].dt.year == year]
        model = fit(train, asof=start) if len(train) >= min_train else None
        if model is None or not len(test):
            continue
        parts.append(test.assign(prob=predict(model, test), base=model["base_rate"], er=expected_r(model, predict(model, test), test["rr"].to_numpy())))
    if not parts:
        return None
    test = pd.concat(parts)
    if len(test) < 100:
        return None
    prob, y = test["prob"].to_numpy(), test["hit"].to_numpy(float)
    table = []
    for chunk in np.array_split(np.argsort(prob), bins):
        table.append({"predicted": float(prob[chunk].mean()), "actual": float(y[chunk].mean()), "avg_r": float(test["R"].to_numpy()[chunk].mean()), "n": int(len(chunk))})
    er_table = []
    for chunk in np.array_split(np.argsort(test["er"].to_numpy()), bins):
        er_table.append({"expected": float(test["er"].to_numpy()[chunk].mean()), "actual": float(test["R"].to_numpy()[chunk].mean()), "n": int(len(chunk))})
    return {"test_n": int(len(test)), "test_from": test["date"].iloc[0].strftime("%Y-%m-%d"), "table": table, "er_table": er_table,
            "brier": float(((prob - y) ** 2).mean()), "brier_base": float(((test["base"].to_numpy() - y) ** 2).mean())}


# ---------------------------------------------------------------- report
def _stat(g: pd.DataFrame) -> str:
    g = g[g["t_out"] != "open"] if len(g) else g                 # the trade as taken: a trend-exit trade can still be running
    if not len(g):
        return "no trades"
    r = g["t_R"].to_numpy()
    wins, losses = r[r > 0], r[r <= 0]
    pf = wins.sum() / -losses.sum() if losses.sum() < 0 else float("inf")
    return (f"trades {len(g):>5} | target hit {g['hit'].mean():.0%} | profitable {(r > 0).mean():.0%} | planned RR {g['rr'].median():.1f} | avg {r.mean():+.2f}R | PF {pf:.2f} "
            f"| avg risk {g['risk_pct'].mean():.1f}%")


def summarize(ev: pd.DataFrame, p: pa.Params = pa.Params()) -> str:
    done = closed(ev)
    if not len(done):
        return "no setups"
    lines = [f"Price-action setups (weekly structure, daily trigger; bullish shapes long, bearish shapes short). {done['symbol'].nunique()} stocks, {done['date'].min().date()} to {done['date'].max().date()}.",
             "Entry at the next open, stop below the zone / back inside the pattern, target = next weekly resistance (or a measured move), 60-session time exit, 0.10% cost.",
             "", "ALL setups:", "  " + _stat(done), f"\nListed setups only (planned reward:risk >= {p.min_rr:g}):"]
    listed = closed(ev, p.min_rr)
    lines.append("  " + _stat(listed))
    for (kind, side), name in pa.NAMES.items():
        g = listed[(listed["kind"] == kind) & (listed["side"] == side)]
        if len(g):
            lines.append(f"  {name + (' [short]' if side == 'short' else ''):<34} " + _stat(g))
    cs = listed[listed["kind"] == "candle"]
    for (side, name), g in sorted(cs.groupby(["side", "candle"]), key=lambda kv: (kv[0][0], -len(kv[1]))):
        lines.append(f"      {name + (' [short]' if side == 'short' else ''):<30} " + _stat(g))
    lines.append("  " + "all longs".ljust(34) + " " + _stat(listed[listed["side"] == "long"]))
    lines.append("  " + "all shorts".ljust(34) + " " + _stat(listed[listed["side"] == "short"]))
    lines.append("\nBy planned reward:risk (all setups):")
    for lo, hi in ((0, 1), (1, 1.5), (1.5, 2.5), (2.5, 4), (4, 99)):
        lines.append(f"  RR {lo:g}-{hi:g}".ljust(26) + _stat(done[(done["rr"] >= lo) & (done["rr"] < hi)]))
    lines.append("\nBy market (listed setups):")
    lines.append("  market with the trade".ljust(26) + _stat(listed[listed["mkt50"] == 1]))
    lines.append("  market against the trade".ljust(26) + _stat(listed[listed["mkt50"] == 0]))
    lines.append("\nBy year (listed setups): trades / target hit / avg R")
    for year, g in listed.groupby(listed["date"].dt.year):
        lines.append(f"  {year}: {len(g):>4} / {g['hit'].mean():.0%} / {g['R'].mean():+.2f}R")
    v = validate(listed)
    if v:
        lines.append(f"\nProbability check, walk-forward by year ({v['test_n']} setups from {v['test_from']} on, each scored by a model that had only seen earlier, finished setups):")
        for row in v["table"]:
            lines.append(f"  predicted {row['predicted']:.0%} -> actual {row['actual']:.0%} hit, avg {row['avg_r']:+.2f}R ({row['n']} setups)")
        lines.append(f"  Brier score {v['brier']:.4f} against {v['brier_base']:.4f} for always quoting the base rate (lower is better)")
        lines.append("  Expected R against what happened:")
        for row in v["er_table"]:
            lines.append(f"    expected {row['expected']:+.2f}R -> actual {row['actual']:+.2f}R ({row['n']} setups)")
    m = fit(listed)
    if m:
        lines.append("\nWhat moves the probability (standardised weights; + raises it):")
        lines.append("  " + ", ".join(f"{name} {b:+.2f}" for name, b in zip(m["features"], m["beta"][1:])))
        lines.append(f"  recent base rate {m['base_rate']:.0%}; a miss costs {m['miss_r']:+.2f}R on average (stops and time exits)")
    return "\n".join(lines)
