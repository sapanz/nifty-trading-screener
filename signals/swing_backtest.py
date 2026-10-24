"""Backtest of the swing R-factor, from the saved store alone (swing_oi.json).

For every saved date t: rebuild what the swing view would have shown at that
evening's close - the SAME `swing.build_swing` as the live build, handed only
data up to t - and take the stocks the R-factor favours, in the direction of
their buildup (long if price rose over the 5 sessions with OI up, short if it
fell). The trade is entered at the close of the NEXT session (a signal that
exists after the close can't be filled at that close; a close is the nearest
price the store keeps) and measured over the next `horizon` sessions, net of a
flat round-trip cost. Compared with every stock with OI building, the same
split by the roll flag, and the top stocks by volume alone.

Limits, plainly: the store only reaches back as far as the futures contracts'
history allowed when it was seeded (about a month) and grows by one session per
night, so early results rest on a few dozen signal dates and are very noisy; a
day-clustered t-stat is shown because a day's picks move together. This is a
sanity check that becomes meaningful as history accumulates, not proof.
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd

from signals import swing
from signals.rfactor import _stats

HORIZONS = (3, 5, 10)
GROUPS = ["Top 5 by R-factor", "R >= 70", "R 40-69", "R < 40 (OI building)", "Every stock with OI building",
          "  ... no expiry in the window", "  ... window includes an expiry (⚠ roll)",
          "Top 5 by volume only (direction = 5-day price)"]


def frames_from_store(store: dict) -> dict[str, pd.DataFrame]:
    frames = {}
    for symbol, saved in store.get("px", {}).items():
        if len(saved) < swing.WINDOW + 2:
            continue
        idx = pd.DatetimeIndex(sorted(saved))
        frames[symbol] = pd.DataFrame(
            {"close": [saved[d.date().isoformat()][0] for d in idx], "volume": [saved[d.date().isoformat()][1] for d in idx]},
            index=idx,
        )
    return frames


def signal_dates(store: dict, frames: dict[str, pd.DataFrame]) -> list[pd.Timestamp]:
    """Dates for which most stocks have OI now and 5 sessions earlier."""
    counts: dict[pd.Timestamp, int] = {}
    for symbol, df in frames.items():
        saved = store["oi"].get(symbol, {})
        dates = list(df.index)
        for i in range(swing.WINDOW, len(dates)):
            if dates[i].date().isoformat() in saved and dates[i - swing.WINDOW].date().isoformat() in saved:
                counts[dates[i]] = counts.get(dates[i], 0) + 1
    if not counts:
        return []
    bar = max(counts.values()) * 0.5
    return sorted(d for d, n in counts.items() if n >= bar)


def run_backtest(store: dict, top_n: int = 5, cost_pct: float = 0.10, delay: int = 1) -> dict:
    """{group: {horizon: [(signal date index, net directional return %), ...]}} plus 'dates'."""
    frames = frames_from_store(store)
    dates = signal_dates(store, frames)
    out = {g: {h: [] for h in HORIZONS} for g in GROUPS}
    used = 0
    for di, t in enumerate(dates):
        truncated = {s: df.loc[:t] for s, df in frames.items() if t in df.index}
        snapshot = swing.build_swing(store, truncated, {}, datetime(2026, 1, 1))
        rows = snapshot["stocks"]
        if not rows or rows[0]["r"] is None:
            continue
        used += 1
        building = [r for r in rows if r["bias"] in ("long", "short")]
        picks = {
            "Top 5 by R-factor": sorted(building, key=lambda r: (r["r"], r["vol_vs_avg"]), reverse=True)[:top_n],
            "R >= 70": [r for r in building if r["r"] >= 70],
            "R 40-69": [r for r in building if 40 <= r["r"] < 70],
            "R < 40 (OI building)": [r for r in building if r["r"] < 40],
            "Every stock with OI building": building,
            "  ... no expiry in the window": [r for r in building if not r["roll"]],
            "  ... window includes an expiry (⚠ roll)": [r for r in building if r["roll"]],
            "Top 5 by volume only (direction = 5-day price)": sorted(rows, key=lambda r: r["vol_vs_avg"], reverse=True)[:top_n],
        }
        for group, chosen in picks.items():
            for r in chosen:
                df = frames[r["symbol"]]
                pos = df.index.get_loc(t)
                direction = 1 if r["chg_5d"] >= 0 else -1
                for h in HORIZONS:
                    entry, exit_ = pos + delay, pos + delay + h
                    if exit_ >= len(df):
                        continue
                    ret = direction * (df["close"].iloc[exit_] / df["close"].iloc[entry] - 1) * 100 - cost_pct
                    out[group][h].append((di, float(ret)))
    out["dates"] = {"count": len(dates), "scored": used,
                    "first": dates[0].date().isoformat() if dates else None, "last": dates[-1].date().isoformat() if dates else None}
    return out


def summarize(results: dict, cost_pct: float = 0.10) -> str:
    info = results["dates"]
    lines = [
        f"Swing R-factor backtest: {info['scored']} signal evenings ({info['first']} to {info['last']}), entry at the next session's close, "
        f"net of {cost_pct}% round-trip cost, in the direction of the buildup (long if price up with OI up, short if down).",
    ]
    for h in HORIZONS:
        lines += ["", f"<b>held {h} sessions</b>"]
        for group in GROUPS:
            lines.append(f"  {group:<48} {_stats(results[group][h])}")
    lines += ["", "Day-clustered t treats each signal evening as one observation; with this few evenings, |t| below ~2 is noise."]
    return "\n".join(lines)
