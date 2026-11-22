#!/usr/bin/env python3
"""Failed breakdown ("bear trap") LONG: a base's floor breaks on a daily close, then price closes back above the floor within a few sessions.

  python scripts/backtest_failed_breakdown.py [path/to/equity_daily.csv.gz]

Built after every short setup lost money - even hedged with the index - because stocks that broke down tended to bounce (README, "Removed tools").
Base = signals.price_action.floor_at (8-40 weeks, <= 30% wide, floor tested twice). Breakdown = the first daily close under the floor within 40
sessions of the base being seen. Trigger = the first daily close back above the floor (or above the band's top) within `window` sessions; the stop is
the lowest low since the breakdown less 0.3%; the target the next weekly resistance or the base's height projected up; entry at the next open,
60-session limit, stop first, 0.10% costs (signals.price_action_model.outcome). Prints variants, years and a market-hedged view.
"""
import dataclasses
import os
import sys
import warnings

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
warnings.filterwarnings("ignore")

from signals import price_action as pa  # noqa: E402
from signals import price_action_model as pm  # noqa: E402
from signals.weekly_zone_backtest import weekly_bars  # noqa: E402

P = pa.Params()


def find(df: pd.DataFrame, window: int, above_band: bool) -> list[dict]:
    if len(df) < 260:
        return []
    dates = pd.DatetimeIndex(df.index).normalize()
    o, h, l, c = (df[x].to_numpy(float) for x in ("open", "high", "low", "close"))
    wk = weekly_bars(df.set_axis(dates))
    wH, wL, wC = (wk[x].to_numpy(float) for x in ("high", "low", "close"))
    wdates, n = wk.index, len(c)
    wk_of = np.searchsorted(wdates.values, dates.values, side="left") - 1
    out, seen, last = [], set(), -10 ** 9
    for w in range(40, len(wC)):
        f = pa.floor_at(wH, wL, wC, w, P)
        if f is None:
            continue
        key = (f["start"], round(np.log(f["floor"]) / 0.01))
        if key in seen:
            continue
        seen.add(key)
        floor, top, band = f["floor"], f["top"], f["band_top"]
        s0 = int(np.searchsorted(dates.values, wdates.values[w], side="right"))
        b = next((i for i in range(s0, min(n, s0 + P.retest_break_window)) if c[i] < floor), None)
        if b is None:
            continue
        level = band if above_band else floor
        r = next((i for i in range(b + 1, min(n, b + 1 + window)) if c[i] > level), None)
        if r is None or r < 260 or r - last < P.cooldown_days or int(wk_of[r]) < 40:
            continue
        stop = float(l[b:r + 1].min()) * (1 - P.stop_buffer_pct / 100)
        pl = pa.plan(c[r], stop, wH, int(wk_of[r]), top - floor, floor, P)
        last = r
        if pl is None:
            continue
        res = pm.outcome(o, h, l, c, r, pl["stop"], pl["target"], P, "long")
        if res is None:
            continue
        out.append({"date": dates[r], "entry_date": dates[res["entry_i"]], "exit_date": dates[res["exit_i"]], "rr": pl["rr"], "risk_pct": pl["risk_pct"], "days_to_reclaim": r - b,
                    "ret": (res["exit"] / res["entry"] - 1) * 100 - pm.COST_PCT, "R": res["R"], "hit": res["hit"], "reason": res["reason"], "bars": res["bars"]})
    return out


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else "equity_daily.csv.gz"
    eq = pd.read_csv(path, parse_dates=["ts"])
    frames = {s: g.set_index("ts").sort_index()[["open", "high", "low", "close", "volume"]] for s, g in eq.groupby("symbol") if s != "NIFTY"}
    close = pd.DataFrame({s: f["close"] for s, f in frames.items()}).sort_index()
    index = (1 + close.pct_change(fill_method=None).mean(axis=1).fillna(0)).cumprod()
    for window, above_band in ((3, False), (5, False), (10, False), (20, False), (10, True)):
        rows = [dict(r, symbol=s) for s, df in frames.items() for r in find(df, window, above_band)]
        d = pd.DataFrame(rows)
        d = d[(d.rr >= 1.5) & (d.reason != "open")]
        ir = np.array([index.asof(x) / index.asof(e) - 1 for e, x in zip(d.entry_date, d.exit_date)]) * 100
        pf = d.R[d.R > 0].sum() / -d.R[d.R <= 0].sum()
        print(f"reclaim within {window:>2} sessions, above {'band top' if above_band else 'floor   '}: n {len(d):>4}  ROI {d.ret.mean():+.2f}%  R {d.R.mean():+.2f}  hit {d.hit.mean() * 100:.0f}%  win {(d.ret > 0).mean() * 100:.0f}%  "
              f"PF {pf:.2f}  hold {d.bars.mean():.0f}  | index same days {ir.mean():+.2f}% -> hedged {d.ret.mean() - ir.mean():+.2f}%")
        if (window, above_band) == (10, False):
            print("   by year:", {int(y): (len(g), round(g.ret.mean(), 2)) for y, g in d.groupby(d.date.dt.year)})
            d.to_csv("failed_breakdown_trades.csv", index=False)


if __name__ == "__main__":
    main()
