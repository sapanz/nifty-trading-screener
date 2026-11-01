"""Movers watchlist - the stocks most CAPABLE of a big (10-12%) move in the next ~10 sessions, in either direction (the Swing tab's panel; the
page has a JS twin).

Rank = the stock's 14-session average daily range as a % of price (ATR%), among liquid F&O stocks (at least MIN_TURNOVER_CR traded on the day).
The squeeze reading (average range of the last 10 sessions / of the last 50) is shown for information: quiet-then-expanding ranges looked
interesting, but ranking by it added nothing over the range itself.

What the backtest found (scripts/backtest_movers.py, 78 signal days, 24 Jun - 14 Oct 2026, 10 sessions from the next open):
  * the 10 widest-range stocks moved 10% or more in EITHER direction in 27% of cases, against 13% for a typical stock (12% or more: 17% vs 7%) -
    about a 2x lift, with a 90% range well above zero (+8 to +19 points);
  * so roughly three in four picks still did NOT make a 10% move, and the list says nothing about which direction: it is a list of stocks that
    can move, not a signal. A wide range also means a wide stop.
"""
from __future__ import annotations

import numpy as np

from signals import momentum

TOP_N = 10
SQUEEZED_BELOW = 0.85
EXPANDING_ABOVE = 1.15
SHORT_DAYS, LONG_DAYS = 10, 50


def _true_range(a: np.ndarray) -> np.ndarray:
    """True range of each candle after the first of an (n, 4) open/high/low/close array."""
    prev = a[:-1, 3]
    return np.maximum(a[1:, 1] - a[1:, 2], np.maximum(np.abs(a[1:, 1] - prev), np.abs(a[1:, 2] - prev)))


def movers_list(candles: dict, turnover: dict[str, float], asof: str | None = None, top_n: int = TOP_N) -> dict | None:
    """{"asof", "universe", "rows": [...]} - the widest-range stocks as of the close of `asof` (default: the last candle), or None with too little
    history. `turnover` is crore rupees traded on that day, by symbol."""
    dates, series = momentum._aligned(candles)
    last = len(dates) - 1 if asof is None else max((i for i, d in enumerate(dates) if d <= asof), default=-1)
    if last < LONG_DAYS + 1:
        return None
    rows = []
    for symbol in sorted(series):
        if (turnover.get(symbol) or 0) < momentum.MIN_TURNOVER_CR:
            continue
        a = series[symbol][: last + 1]
        window = a[-(LONG_DAYS + 1):]
        if np.isnan(window).any() or np.isnan(a[-21, 3]):
            continue
        tr = _true_range(window)
        px = a[-1, 3]
        atr14, atr10, atr50 = tr[-momentum.ATR_DAYS:].mean(), tr[-SHORT_DAYS:].mean(), tr.mean()
        ratio = atr10 / atr50
        rows.append({
            "symbol": symbol, "atr": round(float(atr14 / px) * 100, 1), "close": round(float(px), 2), "squeeze": round(float(ratio), 2),
            "state": "squeezed" if ratio <= SQUEEZED_BELOW else "expanding" if ratio >= EXPANDING_ABOVE else "normal",
            "m20": round(float(px / a[-21, 3] - 1) * 100, 1),
            "from_hi20": round(float(px / np.nanmax(a[-20:, 1]) - 1) * 100, 1), "from_lo20": round(float(px / np.nanmin(a[-20:, 2]) - 1) * 100, 1),
            "turnover_cr": turnover.get(symbol),
            "_key": float(atr14 / px),
        })
    if len(rows) < 20:
        return None
    rows.sort(key=lambda r: (-r["_key"], r["symbol"]))
    for r in rows:
        del r["_key"]
    return {"asof": dates[last], "universe": len(rows), "rows": rows[:top_n]}
