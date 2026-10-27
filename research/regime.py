"""Descriptive look at WHEN the morning dip-buy works, with no model: events grouped by the market's state at the entry time.

The walk-forward models leaned on market-wide features (yesterday's market return, the market's gap, breadth) and the stock-only
ablation lost most of the edge, so this prints the plain relationship: for a variant, win rate / average net / profit factor of
every event, and of the 'dip' subset (below VWAP and the opening-range high, 1.3x+ normal volume, down since the open), by
quintile of each market feature - over all years and split by year, so a regime rule that only worked once is visible as such.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from research.dataset import VARIANTS

MARKET_FEATURES = ("mkt_r1", "mkt_gap", "mkt_day_chg", "mkt_ret_open", "mkt_breadth")


def _row(label: str, g: pd.DataFrame, side: str, v: int) -> str:
    pnl, win = g[f"pnl_{side}{v}"].astype(float), g[f"win_{side}{v}"].astype(float)
    if len(g) < 30:
        return f"    {label:<26} n={len(g)}"
    gains, losses = pnl[pnl > 0].sum(), -pnl[pnl <= 0].sum()
    days = g["date"].nunique()
    return (f"    {label:<26} n={len(g):<6} days={days:<4} win {win.mean() * 100:5.1f}%  avg {pnl.mean():+.3f}%  "
            f"PF {gains / losses if losses > 0 else float('inf'):.2f}")


def dip_mask(ev: pd.DataFrame) -> pd.Series:
    return (ev["vwap_dist"] < -0.005) & (ev["or_hi"] < -0.005) & (ev["volx"] >= 1.3) & (ev["ret_open"] < -0.005)


def regime_report(ev: pd.DataFrame, variants=(14, 11, 16), side: str = "L") -> str:
    lines = ["", "<b>When does the morning dip-buy work?</b> (no model; all events, all years)"]
    dips = ev[dip_mask(ev)]
    for v in variants:
        lines.append(f"  {'long' if side == 'L' else 'short'} v{v} [{VARIANTS[v]['name']}]")
        lines.append(_row("all events", ev, side, v))
        lines.append(_row("dip subset", dips, side, v))
        for feat in MARKET_FEATURES:
            try:
                bins = pd.qcut(dips[feat], 5, duplicates="drop")
            except ValueError:
                continue
            lines.append(f"   dip subset by {feat} quintile (low -> high):")
            for interval, g in dips.groupby(bins, observed=True):
                lines.append(_row(f"{interval}", g, side, v))
        # stability of the most informative split, by year
        lines.append("   dip subset by year, market gap below / above its median:")
        med = dips["mkt_gap"].median()
        for year, g in dips.groupby(dips["date"].dt.year):
            lines.append(_row(f"{year} gap<=med", g[g["mkt_gap"] <= med], side, v))
            lines.append(_row(f"{year} gap>med", g[g["mkt_gap"] > med], side, v))
    return "\n".join(lines)
