#!/usr/bin/env python3
"""Backtest of the high-range leader trades (signals/leader_trades.py): each day's leaders (10 widest-range liquid F&O stocks that are also in the 30 strongest by momentum) bought at
the next open while the market is above its 50-session average, 4 x ATR stop, exit at the close of the 20th session after the signal, one position per stock. Needs the research data:

  python scripts/backtest_leaders.py /path/to/equity_daily.csv.gz
"""
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, __file__.rsplit("/scripts/", 1)[0])
from signals import leader_trades as lt  # noqa: E402


def stat(d: pd.DataFrame) -> str:
    if not len(d):
        return "no trades"
    w, l = d.pct[d.pct > 0].sum(), -d.pct[d.pct <= 0].sum()
    return f"n={len(d):5d}  avg={d.pct.mean():+.2f}%  win={(d.pct > 0).mean() * 100:3.0f}%  stopped={(d.out == 0).mean() * 100:3.0f}%  PF={w / l if l else float('nan'):.2f}  median={d.pct.median():+.2f}%  worst={d.pct.min():+.1f}%"


def main(csv: str) -> None:
    raw = pd.read_csv(csv, parse_dates=["ts"])
    raw = raw[raw.symbol != "NIFTY"]
    frames = {s: g.set_index("ts")[["open", "high", "low", "close", "volume"]].sort_index() for s, g in raw.groupby("symbol")}
    tr, dates, info = lt.trades(frames, set(frames))
    d = pd.DataFrame(tr)
    d["date"] = [pd.Timestamp(dates[i]) for i in d.sig]
    d["year"] = d.date.dt.year
    close = pd.DataFrame({s: f["close"] for s, f in frames.items()}).sort_index()
    idx = (1 + close.pct_change(fill_method=None).mean(axis=1).fillna(0)).cumprod()
    d["idx"] = [(idx.asof(pd.Timestamp(dates[min(r.ext, len(dates) - 1)])) / idx.asof(pd.Timestamp(dates[r.ent])) - 1) * 100 for r in d.itertuples()]
    done = d[d.out != 2]
    print("closed trades:", stat(done), "| still held:", int((d.out == 2).sum()))
    print("vs the equal-weight index over the same days: %+.2f%% (index %+.2f%%)" % ((done.pct - done.idx).mean(), done.idx.mean()))
    for y, g in done.groupby("year"):
        print(f"   {y}: {stat(g)}")
    print("selected on 2021-23:", stat(done[done.year <= 2023]), "\nuntouched 2024+   :", stat(done[done.year >= 2024]))
    mon = pd.Series(done.date.dt.to_period("M").astype(str)).to_numpy()
    ms, rng = np.unique(mon), np.random.default_rng(5)
    bm = [done.pct.to_numpy()[np.isin(mon, rng.choice(ms, len(ms)))].mean() for _ in range(2000)]
    print("month-block bootstrap 90% interval of the average:", np.round(np.percentile(bm, [5, 95]), 2), "; positive in", round(np.mean(np.array(bm) > 0) * 100), "% of resamples")
    print("per signal day: leaders listed", d.groupby("sig").size().describe()[["mean", "max"]].round(1).to_dict(), "| distinct stocks", d.symbol.nunique())
    print("rank in Movers 1-3 vs 4-10:", stat(done[done.mv <= 3]), "|", stat(done[done.mv > 3]))


if __name__ == "__main__":
    main(sys.argv[1])
