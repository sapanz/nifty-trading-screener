#!/usr/bin/env python3
"""Which of the optional context sources answer from here? (global macro series, NSE event calendar / corporate actions). No login needed."""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from signals import macro, nse_events  # noqa: E402

series, status = macro.fetch_global()
for key in macro.SOURCES:
    s = series.get(key)
    print(f"macro {key:<7} {status.get(key):<28} " + (f"{len(s)} closes, last {s.index[-1].date()} {s.iloc[-1]:.2f}" if s is not None else ""))
for key, r in macro.readings(series).items():
    print("  ", r["text"])
from signals import news, peers, universe  # noqa: E402

for t in ("XLK", "KBE", "ACN", "JPM", "SLX", "PPH", "CARZ", "IYZ"):
    ser = macro.fetch_ticker(t)
    print(f"global ticker {t:<5}", f"{len(ser)} closes, last {ser.index[-1].date()} {ser.iloc[-1]:.2f}" if ser is not None else "NO DATA")
names = universe.fetch_nifty500_names()
print("company names:", len(names), {k: names.get(k) for k in ("INFY", "SBIN", "TATASTEEL", "ETERNAL", "HDFCBANK")})
for sym in ("INFY", "SBIN", "TATASTEEL"):
    items, source = news.fetch(names.get(sym, sym), sym, date.today())
    print(f"news {sym}: {source}, {len(items)} relevant headlines")
    for it in items[:3]:
        c = news.classify(it["title"]); print(f"    {it['date']} [{c['score']:+d} {c['cue']}] {it['title'][:90]}")
meetings, actions, st = nse_events.fetch(date.today())
print("nse", st)
today = date.today()
soon = sorted((e["date"], sym, e["what"][:60]) for sym, evs in meetings.items() for e in evs if 0 <= (e["date"] - today).days <= 7)[:8]
print("next board meetings:", soon)
print("sample actions:", [(s, a[0]["date"].isoformat(), a[0]["what"][:40]) for s, a in list(actions.items())[:5]])
