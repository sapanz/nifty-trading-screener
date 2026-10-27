"""Walk-forward models and the selection search for the intraday research loop.

For every side (long / short) and geometry (target, stop) a LightGBM classifier predicts P(target hit first) from the
features at the decision time. Models are refit on an expanding window and only ever score data AFTER their training
window (folds of ~a quarter, one-day embargo), so every prediction used below is out-of-sample.

The out-of-sample period is then split: the first 60% of its days is where configurations are *chosen*
(side, geometry, probability threshold, trades per day); the last 40% is a holdout that takes no part in the choice and
is what the report quotes as the result. The number of configurations tried is printed, because the best of many
tries flatters itself - the holdout is the honest figure.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from research.dataset import EX_REASON, FEATURES, VARIANTS

PARAMS = dict(n_estimators=220, learning_rate=0.04, num_leaves=15, min_child_samples=300, subsample=0.7, subsample_freq=1,
              colsample_bytree=0.7, reg_lambda=5.0, n_jobs=4, verbose=-1)


def folds(dates: list[pd.Timestamp], train_frac: float = 0.4, fold_days: int = 63):
    start = int(len(dates) * train_frac)
    while start < len(dates):
        yield dates[start], dates[min(start + fold_days, len(dates)) - 1]
        start += fold_days


def walk_forward(ev: pd.DataFrame, features: list[str] | None = None, sides=("L", "S"), geoms=None, train_frac: float = 0.4,
                 params: dict | None = None) -> pd.DataFrame:
    from lightgbm import LGBMClassifier
    features = features or FEATURES
    geoms = list(range(len(VARIANTS))) if geoms is None else geoms
    dates = sorted(ev["date"].unique())
    out = ev[list(dict.fromkeys(["symbol", "date", "ent", "sector"] + features))].copy()
    first_test = None
    for f_start, f_end in folds(dates, train_frac):
        first_test = f_start if first_test is None else first_test
        train = ev["date"] < f_start - pd.Timedelta(days=1)
        test = (ev["date"] >= f_start) & (ev["date"] <= f_end)
        for side in sides:
            for g in geoms:
                y = ev[f"win_{side}{g}"]
                model = LGBMClassifier(**{**PARAMS, **(params or {})})
                model.fit(ev.loc[train, features], y[train])
                out.loc[test, f"p_{side}{g}"] = model.predict_proba(ev.loc[test, features])[:, 1]
    for side in sides:
        for g in geoms:
            for col in ("win", "pnl", "bars", "ex"):
                out[f"{col}_{side}{g}"] = ev[f"{col}_{side}{g}"]
    return out[out["date"] >= first_test].reset_index(drop=True)


def pick_trades(oos: pd.DataFrame, side: str, g: int, threshold: float, k: int, entries=None) -> pd.DataFrame:
    """One trade per stock per day (its highest-probability entry time), the top-k per day above the threshold."""
    p = oos[f"p_{side}{g}"]
    sel = oos[(p >= threshold)] if entries is None else oos[(p >= threshold) & oos["ent"].isin(entries)]
    if sel.empty:
        return sel
    sel = sel.assign(p=sel[f"p_{side}{g}"]).sort_values(["date", "p"], ascending=[True, False])
    sel = sel.drop_duplicates(["date", "symbol"]).groupby("date").head(k)
    return sel.assign(win=sel[f"win_{side}{g}"], pnl=sel[f"pnl_{side}{g}"], bars=sel[f"bars_{side}{g}"], ex=sel[f"ex_{side}{g}"]).sort_values(["date", "ent"])


def stats(t: pd.DataFrame) -> dict:
    n = len(t)
    if n == 0:
        return {"n": 0, "win": np.nan, "avg": np.nan, "pf": np.nan, "dd": np.nan, "worst_day": np.nan, "per_day": 0.0, "months_pos": np.nan, "total": 0.0, "hold": np.nan, "ex": {}}
    pnl = t["pnl"].to_numpy()
    equity = np.cumsum(pnl)
    dd = float(np.max(np.maximum.accumulate(np.concatenate([[0], equity])) - np.concatenate([[0], equity])))
    gains, losses = pnl[pnl > 0].sum(), -pnl[pnl <= 0].sum()
    daily = t.groupby("date")["pnl"].sum()
    monthly = t.groupby(t["date"].dt.to_period("M"))["pnl"].sum()
    return {"n": n, "win": float(t["win"].mean()), "avg": float(pnl.mean()), "pf": float(gains / losses) if losses > 0 else np.inf,
            "dd": dd, "worst_day": float(daily.min()), "per_day": n / max(t["date"].nunique(), 1), "months_pos": float((monthly > 0).mean()),
            "total": float(pnl.sum()), "hold": float((t["bars"].mean() + 1) * 5),
            "ex": {EX_REASON[int(k)]: float(v) for k, v in t["ex"].value_counts(normalize=True).items()}}


def fmt(s: dict) -> str:
    if s["n"] == 0:
        return "n=0"
    return (f"n={s['n']:<5} win {s['win'] * 100:5.1f}%  avg {s['avg']:+.3f}%  PF {s['pf']:.2f}  total {s['total']:+.1f}%  maxDD {s['dd']:.1f}%  "
            f"worst day {s['worst_day']:+.2f}%  {s['per_day']:.1f}/day  +months {s['months_pos'] * 100:.0f}%  hold {s['hold']:.0f}min  "
            + "/".join(f"{k} {v * 100:.0f}%" for k, v in sorted(s["ex"].items())))


THRESHOLDS = [0.62, 0.66, 0.70, 0.74, 0.78, 0.82, 0.86, 0.90]
TOP_K = [1, 2, 3, 5, 10]


def search(oos: pd.DataFrame, min_n: int = 120, target_win: float = 0.80) -> str:
    dates = sorted(oos["date"].unique())
    cut = dates[int(len(dates) * 0.6) - 1]
    sel_part, hold_part = oos[oos["date"] <= cut], oos[oos["date"] > cut]
    lines = [f"OOS days {dates[0].date()} to {dates[-1].date()} ({len(dates)}); configurations are chosen on days up to {cut.date()}; "
             f"the holdout is the {len(hold_part['date'].unique())} days after."]
    rows = []
    tried = 0
    for side in ("L", "S"):
        for g, var in enumerate(VARIANTS):
            if f"p_{side}{g}" not in oos:
                continue
            base_sel = sel_part[f"win_{side}{g}"].mean()
            lines.append(f"  {'long' if side == 'L' else 'short'} v{g} {var['name']}: unconditional win {base_sel * 100:.1f}% (sel), "
                         f"{hold_part[f'win_{side}{g}'].mean() * 100:.1f}% (holdout)")
            for thr in THRESHOLDS:
                for k in TOP_K:
                    tried += 1
                    ts = stats(pick_trades(sel_part, side, g, thr, k))
                    if ts["n"] >= min_n:
                        rows.append((side, g, thr, k, ts))
    lines.append(f"{tried} configurations tried, {len(rows)} with at least {min_n} trades on the selection days.")
    def show(title, picked):
        lines.append("")
        lines.append(title)
        for side, g, thr, k, ts in picked:
            hs = stats(pick_trades(hold_part, side, g, thr, k))
            lines.append(f"  {'long ' if side == 'L' else 'short'} v{g} [{VARIANTS[g]['name']}] p>={thr:.2f} top{k}/day")
            lines.append(f"      selection: {fmt(ts)}")
            lines.append(f"      HOLDOUT  : {fmt(hs)}")
    hi_win = sorted([r for r in rows if r[4]["win"] >= target_win], key=lambda r: -r[4]["avg"])[:8]
    show(f"Configurations with win rate >= {target_win * 100:.0f}% on the selection days (best expectancy first):", hi_win)
    best = sorted([r for r in rows if r[4]["avg"] > 0], key=lambda r: -(r[4]["total"] / max(r[4]["dd"], 0.5)))[:8]
    show("Best return / drawdown on the selection days (any win rate):", best)
    return "\n".join(lines)
