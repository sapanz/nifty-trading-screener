"""Context for a selected stock that the chart cannot show: value migration (is money moving into or out of its sector?) and catalysts (dated
events and event-sized moves). Each item carries its reason in words. None of it is in the probability - it is context for reading a setup.

Value migration here is a PROXY from price and turnover, not from profit pools: a sector is gaining value when it has beaten the market over 6
and 12 months AND its share of all the turnover has grown (last 3 months against the year before). The reverse is value leaving.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from signals import nse_events

MIN_MEMBERS = 4
EVENT_MOVE_PCT, EVENT_VOLUME_X, GAP_PCT, LOOKBACK = 4.0, 2.5, 3.0, 15


def sector_flows(frames: dict[str, pd.DataFrame], sectors: dict[str, str]) -> dict[str, dict]:
    """{sector: 6- and 12-month return against the market, its share of market turnover now and a year earlier, rank and verdict}."""
    members = {s: sec for s, sec in sectors.items() if sec and s in frames}
    if len(members) < 20:
        return {}
    idx = lambda df: pd.DatetimeIndex(df.index).normalize()        # noqa: E731
    close = pd.DataFrame({s: frames[s]["close"].set_axis(idx(frames[s])) for s in members}).sort_index()
    turn = pd.DataFrame({s: (frames[s]["close"] * frames[s]["volume"]).set_axis(idx(frames[s])) for s in members}).sort_index()
    if len(close) < 320:
        return {}
    ret6, ret12 = close.iloc[-1] / close.iloc[-127] - 1, close.iloc[-1] / close.iloc[-253] - 1
    now, before = turn.iloc[-63:].sum(), turn.iloc[-315:-63].sum()
    sec = pd.Series(members)
    mkt6, mkt12 = float(ret6.mean()), float(ret12.mean())
    out = {}
    for name, syms in sec.groupby(sec).groups.items():
        syms = list(syms)
        r6, r12 = ret6[syms].dropna(), ret12[syms].dropna()
        if len(r6) < MIN_MEMBERS:
            continue
        share_now, share_before = float(now[syms].sum() / now.sum() * 100), float(before[syms].sum() / before.sum() * 100)
        rel6, rel12 = (float(r6.mean()) - mkt6) * 100, (float(r12.mean()) - mkt12) * 100 if len(r12) >= MIN_MEMBERS else float("nan")
        verdict = ("in" if rel6 > 2 and rel12 > 0 and share_now > share_before else "out" if rel6 < -2 and rel12 < 0 and share_now < share_before else "neutral")
        out[name] = {"n": len(syms), "ret6": round(float(r6.mean()) * 100, 1), "rel6": round(rel6, 1), "rel12": None if np.isnan(rel12) else round(rel12, 1),
                     "share_now": round(share_now, 1), "share_before": round(share_before, 1), "verdict": verdict}
    order = sorted(out, key=lambda k: -out[k]["rel6"])
    for rank, name in enumerate(order, start=1):
        out[name].update(rank=rank, of=len(order))
    return out


def value_migration(symbol: str, sector: str | None, side: str, flows: dict[str, dict], frames: dict[str, pd.DataFrame]) -> dict:
    """Is value moving into or out of the stock's sector, is the stock leading or lagging it, and does that help this trade - with the reasons."""
    f = flows.get(sector or "")
    if not f:
        return {"verdict": "nodata", "for": None, "summary": "No sector data for this stock (it is outside the Nifty 500 list, or its sector has too few members to measure).", "reasons": []}
    long = side != "short"
    rel12 = "" if f["rel12"] is None else f" and {f['rel12']:+.1f}% over 12"
    perf = f"{sector} is {f['rel6']:+.1f}% against the market over 6 months (ranked {f['rank']} of {f['of']} sectors){rel12}"
    share = f"its share of all turnover went from {f['share_before']:.1f}% to {f['share_now']:.1f}%"
    flow = (": about the same share of the market's money as before." if f["share_now"] == f["share_before"]
            else ": more of the market's money is trading here." if f["share_now"] > f["share_before"] else ": less of the market's money is trading here.")
    reasons = [perf + ".", share[0].upper() + share[1:] + flow]
    if f["verdict"] == "in":
        summary, helps = f"Value is migrating INTO {sector}: it is beating the market and taking a larger share of the money traded.", long
    elif f["verdict"] == "out":
        summary, helps = f"Value is migrating OUT OF {sector}: it is lagging the market and losing its share of the money traded.", not long
    else:
        summary, helps = f"No clear value migration in {sector}: performance and money flow do not point the same way.", None
    df = frames.get(symbol)
    if df is not None and len(df) > 127:
        own = float(df["close"].iloc[-1] / df["close"].iloc[-127] - 1) * 100
        gap = own - f["ret6"]
        role = "a leader within its sector" if gap > 5 else "a laggard within its sector" if gap < -5 else "in line with its sector"
        reasons.append(f"The stock is {own:+.1f}% over 6 months against {f['ret6']:+.1f}% for the sector: {role}.")
    if helps is not None:
        reasons.append(("That is a tailwind for this " if helps else "That is a headwind for this ") + ("long" if long else "short") +
                       (": money flowing into a sector tends to lift its members." if f["verdict"] == "in" else ": money leaving a sector tends to weigh on its members."))
    return {"verdict": f["verdict"], "for": helps, "summary": summary, "reasons": reasons}


def price_catalysts(df: pd.DataFrame, side: str = "long") -> list[dict]:
    """Event-sized moves in the last LOOKBACK sessions (a big day on big volume, a gap) and a fresh 52-week extreme - the footprints of news."""
    if df is None or len(df) < 60:
        return []
    o, h, l, c, v = (df[x].to_numpy(float) for x in ("open", "high", "low", "close", "volume"))
    dates = pd.DatetimeIndex(df.index).normalize()
    n, long, out = len(c), side != "short", []
    for i in range(max(21, n - LOOKBACK), n):
        move, gap = (c[i] / c[i - 1] - 1) * 100, (o[i] / c[i - 1] - 1) * 100
        base = v[i - 20:i].mean()
        vol_x = v[i] / base if base > 0 else float("nan")
        day = dates[i].strftime("%d %b")
        if abs(move) >= EVENT_MOVE_PCT and vol_x >= EVENT_VOLUME_X:
            with_trade = bool((move > 0) == long)
            out.append({"date": dates[i].strftime("%Y-%m-%d"), "kind": "move", "for": with_trade, "text": f"{day}: {move:+.1f}% on {vol_x:.1f}× its usual volume",
                        "why": "a move that size on that volume is almost always results or news - the stock is being re-priced" +
                               (", in the trade's direction" if with_trade else ", against the trade's direction")})
        elif abs(gap) >= GAP_PCT:
            with_trade = bool((gap > 0) == long)
            out.append({"date": dates[i].strftime("%Y-%m-%d"), "kind": "gap", "for": with_trade, "text": f"{day}: opened {gap:+.1f}% away from the previous close",
                        "why": "an opening gap means news arrived while the market was shut" + (", in the trade's direction" if with_trade else ", against the trade's direction")})
    out = out[-3:]
    win = slice(max(0, n - 252), n)
    if h[win].max() == h[n - 5:].max():
        out.append({"date": dates[-1].strftime("%Y-%m-%d"), "kind": "high", "for": long, "text": "New 52-week high in the last 5 sessions",
                    "why": "nobody who bought in the past year is sitting on a loss, so there is no supply from trapped holders overhead"})
    if l[win].min() == l[n - 5:].min():
        out.append({"date": dates[-1].strftime("%Y-%m-%d"), "kind": "low", "for": not long, "text": "New 52-week low in the last 5 sessions",
                    "why": "everyone who bought in the past year is at a loss, so rallies meet selling from holders trying to get out"})
    return out


def catalysts(symbol: str, df: pd.DataFrame, side: str, meetings: dict | None, actions: dict | None, today: date, calendar_ok: bool) -> dict:
    """Dated events (board meetings / results, ex-dates) around today plus the price footprints of news, with a one-line summary."""
    dated = nse_events.for_symbol(symbol, meetings or {}, actions or {}, today) if calendar_ok else []
    prices = price_catalysts(df, side)
    upcoming = [e for e in dated if e["upcoming"] and e["kind"] == "meeting"]
    if upcoming:
        first = upcoming[0]
        summary = f"Event ahead - {first['text']} ({'today' if first['days'] == 0 else 'in ' + str(first['days']) + (' day' if first['days'] == 1 else ' days')})."
    elif dated or prices:
        summary = "Recent catalyst: " + (prices[-1]["text"] if prices else dated[-1]["text"]) + "."
    else:
        summary = "No catalyst found: no dated event in the next three weeks and no event-sized move in the last three - this is a chart setup only."
    note = None if calendar_ok else "The exchange's event calendar could not be read for this build, so results dates and ex-dates are not shown - check them before entering."
    return {"summary": summary, "dated": dated, "price": prices, "calendar": calendar_ok, "note": note}
