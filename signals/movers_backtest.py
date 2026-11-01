"""Backtest of the movers watchlist (signals/movers.py): does ranking by daily range find the stocks that make 10-12% moves?

For every signal close t (from the 61st session on, leaving `hold` sessions): the TOP_N widest-range liquid stocks; the outcome is whether the
stock's high or low in the next `hold` sessions, measured from the next session's open, reached `threshold` in EITHER direction. Compared with the
share of ALL liquid stocks that did the same that day (the base rate). Day-to-day outcomes overlap, so the 90% range is a block bootstrap.
It says nothing about direction and nothing about trade profit - only whether range finds capability.
"""
from __future__ import annotations

import numpy as np

from signals import momentum, movers


def run_backtest(candles: dict, turnover: dict[str, float], hold: int = 10, threshold: float = 0.10, top_n: int = movers.TOP_N) -> list[dict]:
    dates, series = momentum._aligned(candles)
    names = sorted(s for s in series if (turnover.get(s) or 0) >= momentum.MIN_TURNOVER_CR)
    a = np.stack([series[s] for s in names], axis=1)                       # days x stocks x (o, h, l, c)
    open_, high, low, close = a[:, :, 0], a[:, :, 1], a[:, :, 2], a[:, :, 3]
    prev = np.vstack([np.full((1, len(names)), np.nan), close[:-1]])
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev), np.abs(low - prev)))
    out = []
    for i in range(max(movers.LONG_DAYS, max(momentum.LOOKBACKS)) + 1, len(dates) - hold - 1):
        atr = np.nanmean(tr[i - momentum.ATR_DAYS + 1:i + 1], axis=0) / close[i]
        ent = open_[i + 1]
        move = np.maximum(np.nanmax(high[i + 1:i + 1 + hold], axis=0) / ent - 1, 1 - np.nanmin(low[i + 1:i + 1 + hold], axis=0) / ent)
        ok = np.flatnonzero(~np.isnan(atr) & ~np.isnan(move))
        if len(ok) < 60:
            continue
        top = ok[np.argsort(-atr[ok], kind="stable")][:top_n]
        out.append({"date": dates[i], "base": float((move[ok] >= threshold).mean()), "picks": float((move[top] >= threshold).mean())})
    return out


def _block_ci(x: np.ndarray, block: int, reps: int = 2000, seed: int = 1) -> tuple[float, float]:
    rng = np.random.default_rng(seed)
    n = len(x)
    means = [np.concatenate([x[s:s + block] for s in rng.integers(0, n, int(np.ceil(n / block)))])[:n].mean() for _ in range(reps)]
    return float(np.percentile(means, 5)), float(np.percentile(means, 95))


def summarize(candles: dict, turnover: dict[str, float], hold: int = 10) -> str:
    lines = [f"Movers watchlist backtest: the {movers.TOP_N} widest-range liquid stocks each day; outcome = high or low within {hold} sessions of the next open "
             "reaches the threshold in EITHER direction. Base = the share of all liquid stocks that did."]
    for threshold in (0.10, 0.12):
        rows = run_backtest(candles, turnover, hold, threshold)
        if len(rows) < 8:
            lines.append(f"\n{threshold:.0%}: not enough history"); continue
        picks = np.array([r["picks"] for r in rows]); base = np.array([r["base"] for r in rows])
        lo, hi = _block_ci((picks - base) * 100, hold)
        lines.append(f"\nmove of {threshold:.0%}+ ({len(rows)} signal days, {rows[0]['date']} to {rows[-1]['date']}): picks {picks.mean() * 100:.1f}% vs base {base.mean() * 100:.1f}% "
                     f"(+{(picks - base).mean() * 100:.1f} pts, 90% range {lo:+.1f}..{hi:+.1f}) - so {100 - picks.mean() * 100:.0f}% of picks did NOT make that move")
    lines.append("\nThis ranks capability, not direction: it cannot tell you whether a mover goes up or down.")
    return "\n".join(lines)
