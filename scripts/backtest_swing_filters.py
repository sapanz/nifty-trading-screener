#!/usr/bin/env python3
"""Which Swing-tab zone filters actually pay? A backtest of the daily order-block retest trade (signals/orderblock_backtest.py) with every zone's features
as the Swing tab would have shown them the evening BEFORE the entry (strength and its parts, trendline confluence, distance, age, stop %, market side), on the
6-year research data (research-data branch: equity_daily.csv.gz, F&O stocks), net of costs (0.22% round trip).

  python scripts/backtest_swing_filters.py [path/to/equity_daily.csv.gz]

Prints, for long (demand) and short (supply) zones separately: every filter's ROI per trade, average R, win rate, profit factor, the gap to the trades
without it with a month-block bootstrap range, ROI by year, and a walk-forward test (combinations chosen on 2021-2023, judged on 2024-2026).
The flow (R-factor / OI) filter cannot be backtested: only about 90 days of futures open interest exist.
"""
import itertools
import os
import sys
import warnings

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
warnings.filterwarnings("ignore")

from signals import config  # noqa: E402,F401
from signals.intraday_blocks import block_score  # noqa: E402
from signals.orderblock_backtest import trade_for  # noqa: E402
from signals.orderblocks import find_order_blocks  # noqa: E402
from signals.swing_trendlines import analyze, confluence, zone_gap_pct  # noqa: E402


def build_trades(path: str) -> pd.DataFrame:
    """One row per historical retest trade with the zone's features as known at the close before the entry bar."""
    eq = pd.read_csv(path, parse_dates=["ts"])
    frames = {s: g.set_index("ts").sort_index()[["open","high","low","close","volume"]] for s, g in eq.groupby("symbol") if s != "NIFTY"}
    # market gate: equal-weight index vs its 50-day average
    close = pd.DataFrame({s: f["close"] for s, f in frames.items()}).sort_index()
    index = (1 + close.pct_change(fill_method=None).mean(axis=1).fillna(0)).cumprod(); up = (index > index.rolling(50).mean())
    rows=[]
    for k,(sym,df) in enumerate(frames.items()):
        if len(df)<120: continue
        o,h,l,c,v=(df[x].to_numpy(float) for x in ("open","high","low","close","volume")); dates=list(df.index)
        for b in find_order_blocks(o,h,l,c,volume=v):
            tr=trade_for(b,sym,dates,o,h,l,c)
            if tr is None: continue
            t=dates.index(tr.touch_date); t0i=t-1
            if t0i<60: continue
            back=b.formed_idx-20; wt=back>=0 and ((c[b.formed_idx]>c[back]) if b.direction=="bull" else (c[b.formed_idx]<c[back]))
            sc=block_score(b.impulse_atr,b.vol_ratio,b.swept,wt)
            zone={"d":b.direction,"lo":b.low,"hi":b.high}
            lines=analyze(df.iloc[:t])                       # candles up to the close before the touch bar
            cf=confluence(zone,lines); gap=zone_gap_pct(zone,lines)
            ln=lines.get("s" if b.direction=="bull" else "r"); pdist=ln["d"] if ln else np.nan
            dist=((c[t0i]/b.high-1) if b.direction=="bull" else (b.low/c[t0i]-1))*100
            vt=v[t0i-2:t0i+1].mean()/v[t0i-22:t0i-2].mean() if v[t0i-22:t0i-2].mean()>0 else np.nan
            mk=bool(up.get(dates[t0i],False))
            rows.append(dict(symbol=sym,dir=b.direction,formed=dates[b.formed_idx],touch=dates[t],year=tr.year,net=tr.net_pct,gross=tr.gross_pct,R=tr.r_multiple,outcome=tr.outcome,risk=tr.risk_pct,width=tr.width_pct,
                             imp=b.impulse_atr,vr=b.vol_ratio if b.vol_ratio is not None else np.nan,sw=int(b.swept),wd=int(wt),sc=sc,age=t-b.formed_idx,dist=dist,cf=int(cf),tl_gap=gap if gap is not None else np.nan,tl_pdist=pdist,vt=vt,mkt_up=int(mk),
                             with_mkt=int(mk if b.direction=="bull" else not mk),hold=(tr.exit_date-tr.touch_date).days))
    return pd.DataFrame(rows)



def analyse(d: pd.DataFrame) -> None:
    d["ym"] = d.touch.dt.to_period("M")

    FIL={
     "zone strength >= 60":lambda x:x.sc>=60, "zone strength >= 70":lambda x:x.sc>=70, "trendline confirms (TL ok)":lambda x:x.cf==1,
     "strength>=60 AND trendline":lambda x:(x.sc>=60)&(x.cf==1), "with the trend":lambda x:x.wd==1, "liquidity sweep":lambda x:x.sw==1,
     "thrust volume >= 2x":lambda x:x.vr>=2, "thrust >= 4 ATR":lambda x:x.imp>=4,
     "price within 1.5% of zone":lambda x:x.dist<=1.5, "price within 3% of zone":lambda x:x.dist<=3,
     "zone formed within 1 week":lambda x:x.age<=5, "zone formed within 2 weeks":lambda x:x.age<=10, "zone formed within 1 month":lambda x:x.age<=20,
     "stop <= 3%":lambda x:x.risk<=3, "stop <= 2%":lambda x:x.risk<=2,
     "with the market (long up / short down)":lambda x:x.with_mkt==1,
     "trendline within 1% of zone":lambda x:x.tl_gap<=1, "price within 3% of trendline":lambda x:x.tl_pdist<=3,
     "volume thrust (3d vs 20d) >= 1.5x":lambda x:x.vt>=1.5,
    }
    def st(x):
        r=x.net.values; w=r[r>0].sum(); l=-r[r<=0].sum()
        return dict(n=len(x),roi=r.mean(),R=x.R.mean(),win=(r>0).mean()*100,pf=w/l if l>0 else np.inf)
    def blk(x,mask,reps=2000,seed=5):
        rng=np.random.default_rng(seed); ms=sorted(x.ym.unique()); a={m:(x[(x.ym==m)&mask].net.values,x[(x.ym==m)&~mask].net.values) for m in ms}; out=[]
        for _ in range(reps):
            pick=rng.choice(len(ms),len(ms)); p=np.concatenate([a[ms[k]][0] for k in pick]); q=np.concatenate([a[ms[k]][1] for k in pick])
            if len(p)>20 and len(q)>20: out.append(p.mean()-q.mean())
        return np.percentile(out,[5,95])
    yrs=[2021,2022,2023,2024,2025,2026]
    for side,nm in (("bull","LONG zones (demand)"),("bear","SHORT zones (supply)")):
        x=d[d.dir==side]; b=st(x)
        print(f"\n=== {nm}: all {b['n']} trades, ROI/trade {b['roi']:+.2f}% (avg {b['R']:+.2f}R), win {b['win']:.0f}%, PF {b['pf']:.2f}")
        print("    baseline by year:", {y:round(x[x.year==y].net.mean(),2) for y in yrs})
        print(f"{'filter':<42}{'n':>6}{'ROI%':>7}{'R':>7}{'win%':>6}{'PF':>6}{'vs rest':>9}  90% range          by year (2021..2026)")
        rows=[]
        for k,f in FIL.items():
            m=f(x).fillna(False) if hasattr(f(x),"fillna") else f(x); s=st(x[m]); 
            if s["n"]<50: continue
            lo,hi=blk(x,m); rows.append((k,s,lo,hi,{y:(round(x[m&(x.year==y)].net.mean(),1) if (m&(x.year==y)).sum()>=15 else None) for y in yrs}, s["roi"]-st(x[~m])["roi"]))
        for k,s,lo,hi,by,df_ in sorted(rows,key=lambda r:-r[1]["roi"]):
            print(f"{k:<42}{s['n']:>6}{s['roi']:>+7.2f}{s['R']:>+7.2f}{s['win']:>6.0f}{s['pf']:>6.2f}{df_:>+9.2f}  [{lo:+.2f},{hi:+.2f}]   "+" ".join("  - " if v is None else f"{v:+.1f}" for v in by.values()))
        # walk-forward combo search: choose on 2021-2023, evaluate 2024-2026
        tr=x[x.year<=2023]; te=x[x.year>=2024]; keys=list(FIL)
        res=[]
        for r in (1,2,3):
            for combo in itertools.combinations(keys,r):
                mt=np.ones(len(tr),bool)
                for k in combo: mt&=FIL[k](tr).fillna(False).values
                if mt.sum()<80: continue
                res.append((tr[mt].net.mean(),mt.sum(),combo))
        res.sort(reverse=True)
        print("  walk-forward: best combos chosen on 2021-2023 (n>=80), then tested on 2024-2026 untouched:")
        for roi_tr,n_tr,combo in res[:5]:
            me=np.ones(len(te),bool)
            for k in combo: me&=FIL[k](te).fillna(False).values
            e=te[me]; print(f"    train {roi_tr:+.2f}% (n {n_tr}) -> TEST {e.net.mean():+.2f}% (n {len(e)}, win {(e.net>0).mean()*100:.0f}%) | all test trades {te.net.mean():+.2f}% | {' + '.join(combo)}")



if __name__ == "__main__":
    trades = build_trades(sys.argv[1] if len(sys.argv) > 1 else "equity_daily.csv.gz")
    print(len(trades), "trades", trades.dir.value_counts().to_dict())
    analyse(trades)
