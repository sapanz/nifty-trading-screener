"""Shared statistics for the strategy backtests (trades as a DataFrame with R, ret_pct, bars, risk_pct, reason, entry_date, exit_date)."""
from __future__ import annotations

import numpy as np
import pandas as pd


def _block_ci(values: np.ndarray, blocks: np.ndarray, reps: int = 2000, seed: int = 3) -> tuple[float, float]:
    """90% bootstrap range of the mean, resampling whole calendar months (trades in the same month are not independent)."""
    rng = np.random.default_rng(seed)
    groups = [values[blocks == b] for b in np.unique(blocks)]
    means = [np.concatenate([groups[k] for k in rng.integers(0, len(groups), len(groups))]).mean() for _ in range(reps)]
    return float(np.percentile(means, 5)), float(np.percentile(means, 95))


def stats(trades: pd.DataFrame) -> dict | None:
    if trades.empty:
        return None
    r = trades["R"].to_numpy()
    wins, losses = r[r > 0], r[r <= 0]
    ordered = trades.sort_values("exit_date")["R"].to_numpy()
    equity = np.cumsum(ordered)
    drawdown = float((np.maximum.accumulate(np.maximum(equity, 0)) - equity).max())
    months = trades["exit_date"].dt.to_period("M").astype(str).to_numpy()
    lo, hi = _block_ci(r, months) if len(np.unique(months)) >= 6 else (float("nan"), float("nan"))
    return {"n": len(r), "win": float((r > 0).mean()), "avg_r": float(r.mean()), "ci": (lo, hi), "pf": float(wins.sum() / -losses.sum()) if losses.sum() < 0 else float("inf"),
            "avg_ret": float(trades["ret_pct"].mean()), "total_r": float(r.sum()), "max_dd_r": drawdown, "avg_bars": float(trades["bars"].mean()),
            "avg_risk": float(trades["risk_pct"].mean()), "stopped": float(trades["reason"].str.startswith("stop").mean())}


def by_year(trades: pd.DataFrame) -> list[tuple[int, int, float, float]]:
    out = []
    for year, g in trades.groupby(trades["entry_date"].dt.year):
        out.append((int(year), len(g), float((g["R"] > 0).mean()), float(g["R"].mean())))
    return out


def fmt(label: str, s: dict | None) -> str:
    if s is None:
        return f"  {label:<46} no trades"
    lo, hi = s["ci"]
    ci = "n/a" if np.isnan(lo) else f"{lo:+.2f}..{hi:+.2f}"
    return (f"  {label:<46} trades {s['n']:>5} | win {s['win']:.0%} | avg {s['avg_r']:+.2f}R (90% {ci}) | PF {s['pf']:.2f} | avg {s['avg_ret']:+.2f}% "
            f"| stopped {s['stopped']:.0%} | max drawdown {s['max_dd_r']:.0f}R")
