"""Backtest of the momentum watchlist (signals/momentum.py), from the published daily candles alone.

For every signal close t (from the 61st session on): the strongest TOP_N stocks if the equal-weight index is above its 50-session average,
the weakest TOP_N if below; entered at the NEXT session's open (a signal that exists after the close can't be filled at that close), exited at the
close `hold` sessions after t, net of a flat round-trip cost, in the direction of the list (long strongest / short weakest). "Accuracy" is the
share of picks that ended in profit, compared with a random pick in the same direction on the same days - exactly the share of the whole liquid
universe that ended in profit - so the difference between the two is what the ranking adds. Overlapping holds make neighbouring days
dependent, so confidence ranges are a block bootstrap with blocks of `hold` days.

Limits: the candle history is ~150 sessions, so this is one up phase and one down phase; liquidity is today's turnover; stocks are not
adjusted for corporate actions beyond what the candles already carry.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from signals import momentum

HOLDS = (5, 10)


def _rank_pct(x: np.ndarray) -> np.ndarray:
    out = np.full_like(x, np.nan, dtype=float)
    ok = ~np.isnan(x)
    out[ok] = pd.Series(x[ok]).rank(pct=True).to_numpy()
    return out


def run_backtest(candles: dict, turnover: dict[str, float], hold: int = 5, top_n: int = momentum.TOP_N, cost_pct: float = 0.10) -> pd.DataFrame:
    """One row per signal day: market direction, the picks' net returns (%), and the whole universe's (the random-pick baseline)."""
    dates, series = momentum._aligned(candles)
    names = sorted(s for s in series if (turnover.get(s) or 0) >= momentum.MIN_TURNOVER_CR)
    a = np.stack([series[s] for s in names], axis=1)                       # days x stocks x (o, h, l, c)
    open_, close = a[:, :, 0], a[:, :, 3]
    prev = np.vstack([np.full((1, len(names)), np.nan), close[:-1]])
    have = ~np.isnan(prev) & ~np.isnan(close)
    daily = np.where(have, close / np.where(have, prev, 1) - 1, 0).sum(axis=1) / np.maximum(have.sum(axis=1), 1)
    index = np.cumprod(1 + daily)
    rows = []
    first = max(momentum.LOOKBACKS)
    for i in range(first, len(dates) - hold):
        if i + 1 < momentum.MA_DAYS:
            continue
        up = bool(index[i] > index[i - momentum.MA_DAYS + 1:i + 1].mean())
        score = sum(_rank_pct(close[i] / close[i - n] - 1) for n in momentum.LOOKBACKS) / len(momentum.LOOKBACKS)
        ok = np.flatnonzero(~np.isnan(score) & ~np.isnan(open_[i + 1]) & ~np.isnan(close[i + hold]))
        if len(ok) < 60:
            continue
        direction = 1 if up else -1
        net = direction * (close[i + hold, ok] / open_[i + 1, ok] - 1) * 100 - cost_pct
        order = np.argsort(-score[ok] if up else score[ok], kind="stable")[:top_n]
        rows.append({"date": dates[i], "up": up, "picks": net[order], "universe": net})
    return pd.DataFrame(rows)


def _block_ci(x: np.ndarray, block: int, reps: int = 2000, seed: int = 1) -> tuple[float, float]:
    rng = np.random.default_rng(seed)
    n = len(x)
    means = [np.concatenate([x[s:s + block] for s in rng.integers(0, n, int(np.ceil(n / block)))])[:n].mean() for _ in range(reps)]
    return float(np.percentile(means, 5)), float(np.percentile(means, 95))


def summarize_group(df: pd.DataFrame, hold: int) -> dict:
    picks = np.concatenate(df["picks"].to_list())
    win_edge = np.array([(p > 0).mean() - (u > 0).mean() for p, u in zip(df["picks"], df["universe"])]) * 100
    ret_edge = np.array([p.mean() - u.mean() for p, u in zip(df["picks"], df["universe"])])
    wins, losses = picks[picks > 0], picks[picks <= 0]
    return {"days": len(df), "picks": len(picks), "accuracy": float((picks > 0).mean() * 100),
            "random_accuracy": float(np.mean([(u > 0).mean() for u in df["universe"]]) * 100),
            "accuracy_edge": float(win_edge.mean()), "accuracy_edge_ci": _block_ci(win_edge, hold),
            "avg_net": float(picks.mean()), "random_avg_net": float(np.mean([u.mean() for u in df["universe"]])),
            "return_edge": float(ret_edge.mean()), "return_edge_ci": _block_ci(ret_edge, hold),
            "profit_factor": float(wins.sum() / -losses.sum()) if losses.sum() < 0 else float("inf"),
            "avg_win": float(wins.mean()) if len(wins) else 0.0, "avg_loss": float(losses.mean()) if len(losses) else 0.0}


def summarize(candles: dict, turnover: dict[str, float], cost_pct: float = 0.10) -> str:
    lines = [f"Momentum watchlist backtest: strongest {momentum.TOP_N} when the market is above its {momentum.MA_DAYS}-day average, weakest {momentum.TOP_N} when below; "
             f"entry at the next open, net of {cost_pct}% round trip. Accuracy = picks ending in profit; random = any pick in the same direction on the same days."]
    for hold in HOLDS:
        df = run_backtest(candles, turnover, hold, cost_pct=cost_pct)
        if df.empty:
            lines.append(f"\nheld {hold} sessions: not enough history"); continue
        lines.append(f"\nheld {hold} sessions - signal days {df['date'].iloc[0]} to {df['date'].iloc[-1]}")
        for label, part in (("all", df), ("market up -> long strongest", df[df["up"]]), ("market down -> short weakest", df[~df["up"]])):
            if len(part) < 5:
                continue
            r = summarize_group(part, hold)
            lines.append(f"  {label:<30} days {r['days']:>3} picks {r['picks']:>4} | accuracy {r['accuracy']:.1f}% vs random {r['random_accuracy']:.1f}% "
                         f"(edge {r['accuracy_edge']:+.1f} pts, 90% {r['accuracy_edge_ci'][0]:+.1f}..{r['accuracy_edge_ci'][1]:+.1f}) | avg net {r['avg_net']:+.2f}% vs {r['random_avg_net']:+.2f}% "
                         f"(edge {r['return_edge']:+.2f}, 90% {r['return_edge_ci'][0]:+.2f}..{r['return_edge_ci'][1]:+.2f}) | PF {r['profit_factor']:.2f} | avg win {r['avg_win']:+.2f}% avg loss {r['avg_loss']:+.2f}%")
    lines.append("\nWith this few independent days, an edge whose 90% range includes zero is not distinguishable from luck.")
    return "\n".join(lines)
