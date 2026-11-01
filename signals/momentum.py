"""Momentum watchlist - the strongest and weakest stocks, with a market switch (the Swing tab's panel; the page has a JS twin).

Rank = the average of a stock's percentile ranks of its 20-session and 60-session returns, among liquid F&O stocks (at least
MIN_TURNOVER_CR traded on the day). The market switch is the equal-weight index of those stocks against its own MA_DAYS-session
average: above it the strongest stocks are the list to use (long), below it the weakest (short).

What the backtest found (scripts/backtest_momentum.py, 23 Jun - 30 Oct 2026, entry at the next open, 0.10% round-trip cost):
  * accuracy (picks ending in profit) was 52-57%, not 80%; the money came from winners (+3.9% to +5.5%) being larger than losers;
  * long side (market up): 54% / 52% (5 / 10 sessions) against 45% / 43% for a random pick in the same direction - a real but
    not yet stable edge (most of it came from one month);
  * short side (market down): 58% / 65%, but a random short won 61% / 70% in the falling market - NO edge over random shorts.
Treat the strongest list as the one with evidence; the weakest list is context. See the README.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

MIN_TURNOVER_CR = 20.0
LOOKBACKS = (20, 60)
MA_DAYS = 50
ATR_DAYS = 14
TOP_N = 10


def _aligned(candles: dict) -> tuple[list[str], dict[str, np.ndarray]]:
    """Per-stock (n, 4) open/high/low/close arrays placed on the shared date axis (NaN where a stock has no candle)."""
    dates = candles["dates"]
    index = {d: i for i, d in enumerate(dates)}
    out = {}
    for symbol, s in candles["stocks"].items():
        arr = np.full((len(dates), 4), np.nan)
        start = index.get(s["d0"])
        if start is None:
            continue
        data = np.asarray(s["c"], dtype=float)[: len(dates) - start]
        arr[start:start + len(data)] = data
        out[symbol] = arr
    return dates, out


def momentum_lists(candles: dict, turnover: dict[str, float], asof: str | None = None, top_n: int = TOP_N) -> dict | None:
    """{"asof", "universe", "market": {"vs_ma_pct", "up"}, "strong": [...], "weak": [...]} as of the close of `asof` (default: the last
    candle), or None when there is too little history. `turnover` is crore rupees traded on that day, by symbol."""
    dates, series = _aligned(candles)
    last = len(dates) - 1 if asof is None else max((i for i, d in enumerate(dates) if d <= asof), default=-1)
    if last < max(LOOKBACKS):
        return None
    universe = sorted(s for s in series if (turnover.get(s) or 0) >= MIN_TURNOVER_CR and not np.isnan(series[s][last, 3]))
    if len(universe) < 20:
        return None
    close = np.array([series[s][: last + 1, 3] for s in universe])          # stocks x days
    prev = np.concatenate([np.full((len(universe), 1), np.nan), close[:, :-1]], axis=1)
    have = ~np.isnan(prev) & ~np.isnan(close)
    daily = np.where(have, close / np.where(have, prev, 1) - 1, 0).sum(axis=0) / np.maximum(have.sum(axis=0), 1)
    index = np.cumprod(1 + np.nan_to_num(daily))                             # equal-weight index of the universe
    if len(index) < MA_DAYS:
        return None
    ma = index[-MA_DAYS:].mean()
    vs_ma = (index[-1] / ma - 1) * 100

    rets = {n: close[:, -1] / close[:, -1 - n] - 1 for n in LOOKBACKS}
    ok = ~np.isnan(rets[LOOKBACKS[0]]) & ~np.isnan(rets[LOOKBACKS[1]])
    names = [s for s, keep in zip(universe, ok) if keep]
    score = sum(pd.Series(rets[n][ok]).rank(pct=True).to_numpy() for n in LOOKBACKS) / len(LOOKBACKS)

    rows = []
    for k, symbol in enumerate(names):
        a = series[symbol][: last + 1]
        o_h_l_c = a[-ATR_DAYS:]
        prev_close = a[-ATR_DAYS - 1:-1, 3]
        tr = np.maximum(o_h_l_c[:, 1] - o_h_l_c[:, 2], np.maximum(np.abs(o_h_l_c[:, 1] - prev_close), np.abs(o_h_l_c[:, 2] - prev_close)))
        px = a[-1, 3]
        rows.append({
            "symbol": symbol, "score": round(float(score[k]) * 100, 1), "close": round(float(px), 2),
            "m5": round(float(px / a[-6, 3] - 1) * 100, 1), "m20": round(float(px / a[-21, 3] - 1) * 100, 1), "m60": round(float(px / a[-61, 3] - 1) * 100, 1),
            "atr": round(float(tr.mean() / px) * 100, 1),
            "from_hi20": round(float(px / np.nanmax(a[-20:, 1]) - 1) * 100, 1), "from_lo20": round(float(px / np.nanmin(a[-20:, 2]) - 1) * 100, 1),
            "turnover_cr": turnover.get(symbol),
        })
    rows.sort(key=lambda r: (-r["score"], r["symbol"]))
    weak = sorted(rows, key=lambda r: (r["score"], r["symbol"]))[:top_n]
    return {"asof": dates[last], "universe": len(rows), "market": {"vs_ma_pct": round(float(vs_ma), 1), "up": bool(index[-1] > ma)},
            "strong": rows[:top_n], "weak": weak}
