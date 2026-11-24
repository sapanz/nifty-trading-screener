#!/usr/bin/env python3
"""Backtest of the SMA support / resistance trades (signals/sma_support.py): LONG - a green candle on a rising 21-day (daily) or 30-week (weekly) average,
buy above its high, stop below its low; SHORT - the bear mirror (a red candle under a falling average, sell below its low, stop above its high); target = the
next weekly resistance / support (when it pays 1.5 R) else 3 R. Prints, per side and timeframe, the trades by sector support, by 20-day volume ratio, per year, against the equal-weight index, with a month-block bootstrap. Needs the research data:

  python scripts/backtest_sma_support.py /path/to/equity_daily.csv.gz /path/to/swing.json
"""
import json
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, __file__.rsplit("/scripts/", 1)[0])
from signals import sma_support as ss  # noqa: E402


def load(csv: str, swing_json: str):
    raw = pd.read_csv(csv, parse_dates=["ts"])
    raw = raw[raw.symbol != "NIFTY"]
    frames = {s: g.set_index("ts")[["open", "high", "low", "close", "volume"]].sort_index() for s, g in raw.groupby("symbol")}
    sectors = {r["symbol"]: r["sector"] for r in json.load(open(swing_json))["stocks"]}
    return frames, sectors


def trades(frames, sectors):
    flags = ss.sector_flags(frames, sectors)
    tables = ss.collect_trend_tables(frames, sectors)                         # the trend rank of a daily long signal
    close = pd.DataFrame({s: df["close"] for s, df in frames.items()}).sort_index()
    idx = (1 + close.pct_change(fill_method=None).mean(axis=1).fillna(0)).cumprod()
    rows = []
    for sym, df in frames.items():
        dates = pd.DatetimeIndex(df.index).normalize()
        for tf in ("D", "W"):
            for side in ss.SIDES:
                fl = ss.flag_for(flags.get(sectors.get(sym)), tf, side)
                for sig in ss.signals(df, tf, fl, side):
                    tr = ss.outcome(df, sig)
                    if tr is None or tr["reason"] == "open":
                        continue
                    pct, r = ss.pct_and_r(tr, sig)
                    a, b = dates[tr["entry_i"]], dates[tr["exit_i"]]
                    move = (idx.asof(b) / idx.asof(a) - 1) * 100
                    rows.append({"tf": tf, "side": side, "sym": sym, "date": sig["date"], "year": sig["date"].year, "pct": pct, "R": r, "sector": sig["sector_ok"], "vr": sig["vr"],
                                 "rr": sig["rr"], "kind": sig["target_kind"], "tr": ss.trend_rank(sig, tables) if tf == "D" and side == "long" else -1, "out": tr["reason"], "bars": tr["exit_i"] - tr["entry_i"],
                                 "idx": move if side == "long" else -move})              # the index move that helps / hurts the trade
    return pd.DataFrame(rows)


def stat(d: pd.DataFrame) -> str:
    if not len(d):
        return "no trades"
    w, l = d.pct[d.pct > 0].sum(), -d.pct[d.pct <= 0].sum()
    return f"n={len(d):5d}  ROI/trade={d.pct.mean():+.2f}%  R={d.R.mean():+.2f}  win={(d.pct > 0).mean() * 100:3.0f}%  PF={w / l if l else float('nan'):.2f}  vs index {(d.pct - d.idx).mean():+.2f}%"


def main(csv: str, swing_json: str) -> None:
    d = trades(*load(csv, swing_json))
    for side, tf in ((sd, f) for sd in ss.SIDES for f in ("D", "W")):
        name = {"D": "DAILY 21-day average", "W": "WEEKLY 30-week average"}[tf]
        t = d[(d.tf == tf) & (d.side == side)]
        print(f"\n===== {side.upper()} - {name}")
        print("  every signal                      ", stat(t))
        print("  sector supports                   ", stat(t[t.sector]))
        print("  sector does not support           ", stat(t[~t.sector]))
        for thr in (1.0, 1.2, 1.5, 2.0):
            print(f"  sector + volume >= {thr:.1f}x 20-avg    ", stat(t[t.sector & (t.vr >= thr)]))
        print("  sector + volume < 1.0x            ", stat(t[t.sector & (t.vr < 1.0)]))
        if side == "long" and tf == "D":
            base = t[t.sector & (t.vr >= ss.TF[tf]["vol_default"])]
            print(f"  trend rank (0-100, {ss.CLEAR_PERCENTILE}+ = clear uptrend):")
            for lab, lo, hi in (("0-20", 0, 20), ("20-40", 20, 40), ("40-60", 40, 60), ("60-80", 60, 80), (f"{ss.CLEAR_PERCENTILE}-100 clear uptrend", ss.CLEAR_PERCENTILE, 101)):
                print(f"     every signal {lab:24s}", stat(t[(t.tr >= lo) & (t.tr < hi)]), "\n     default rule", f"{lab:24s}", stat(base[(base.tr >= lo) & (base.tr < hi)]))
            clear = base[base.tr >= ss.CLEAR_PERCENTILE]
            print("     default rule + clear uptrend by year:", {int(y): f"{g.pct.mean():+.2f}% (n {len(g)})" for y, g in clear.groupby("year")})
            print("     untouched 2024+ clear uptrend:", stat(clear[clear.year >= 2024]), "| 2021-23:", stat(clear[clear.year <= 2023]))
        pick = t[t.sector & (t.vr >= ss.TF[tf]["vol_default"])]
        print(f"  --> the rule (sector + volume >= {ss.TF[tf]['vol_default']}x):", stat(pick))
        for y, g in pick.groupby("year"):
            print(f"        {y}: {stat(g)}")
        print("        selected on 2021-23:", stat(pick[pick.year <= 2023]), "\n        untouched 2024+   :", stat(pick[pick.year >= 2024]))
        print("        target kind:", {k: stat(g) for k, g in pick.groupby('kind')})
        mon = pd.Series(pd.DatetimeIndex(pick.date).to_period("M").astype(str)).to_numpy()
        ms, rng = np.unique(mon), np.random.default_rng(3)
        bm = [pick.pct.to_numpy()[np.isin(mon, rng.choice(ms, len(ms)))].mean() for _ in range(2000)]
        print("        month-block bootstrap 90% interval of ROI/trade:", np.round(np.percentile(bm, [5, 95]), 2), "; positive in", round(np.mean(np.array(bm) > 0) * 100), "% of resamples")


if __name__ == "__main__":
    main(*sys.argv[1:3])
