"""The dashboard's Price action list: today's setups (signals/price_action.py) with their plan, probability and chart data.

`build(frames, ...)` is pure: daily candles in, two JSON-ready dicts out (the list and the candles for the charts). The probability comes
from signals/price_action_model.py - fitted on this same history, recent setups weighted more, then scaled by how far its walk-forward
predictions were from what happened (`calibration`), so it is not allowed to promise more than the past delivered.
"""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from signals import macro as macro_ctx
from signals import peers as peers_mod
from signals import price_action as pa
from signals import price_action_model as pm
from signals import stock_context as sc
from signals import stock_macro

FRESH_DAYS = 4            # a trigger stays on the list this many sessions, while the plan still holds from the current price
CHART_BARS = 330          # daily candles per chart: enough to see a 65-week cup
WEEKLY_BARS = 160         # weekly candles per chart: the three years the resistance levels are taken from


def weekly_chart(df: pd.DataFrame) -> dict:
    """Weekly candles for the chart, stamped with their Friday, the week still forming included (the last bar)."""
    idx = pd.DatetimeIndex(df.index).normalize()
    wk = df.set_axis(idx).resample("W-FRI").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna(subset=["close"]).tail(WEEKLY_BARS)
    return {"d": [d.strftime("%Y-%m-%d") for d in wk.index], "c": [[round(float(x), 2) for x in row] for row in wk[["open", "high", "low", "close"]].to_numpy()]}


def setup_name(kind: str, side: str, geo: dict) -> str:
    """The setup's name as shown: a triangle says which, a candle says which candle and where."""
    if kind == "triangle":
        return f"{geo['shape']} triangle breakout"
    if kind == "candle":
        return f"{geo['candle']} at weekly {'support' if side == 'long' else 'resistance'}"
    if kind == "pause":
        unit = "week" if geo["tf"] == "weekly" else "day"
        return f"Bearish continuation ({geo['tf']}): {geo['run']} red {unit}s, then a {geo['zone']}-{unit} halt zone"
    return pa.NAMES[(kind, side)]


def oi_tag(store: dict | None, symbol: str, close: pd.Series, sessions: int = 5, side: str = "long") -> dict | None:
    """What open interest did over the last `sessions` against price: long buildup / short covering / short buildup / long unwinding. "for" says
    whether that agrees with the trade's side."""
    series = ((store or {}).get("oi") or {}).get(symbol)
    if not series:
        return None
    dates = sorted(series)
    if len(dates) <= sessions or len(close) <= sessions:
        return None
    oi_now, oi_then = series[dates[-1]], series[dates[-1 - sessions]]
    if not oi_then or not oi_now:
        return None
    oi_chg, px_chg = oi_now / oi_then - 1, float(close.iloc[-1] / close.iloc[-1 - sessions] - 1)
    if abs(oi_chg) > 0.5:                                         # a rollover step, not positioning
        return None
    tag = ("long buildup" if oi_chg > 0 else "short covering") if px_chg > 0 else ("short buildup" if oi_chg > 0 else "long unwinding")
    bullish = tag in ("long buildup", "short covering")
    return {"tag": tag, "oi_chg": round(oi_chg * 100, 1), "px_chg": round(px_chg * 100, 1), "for": bullish if side == "long" else not bullish}


def _calibration(v: dict | None) -> float:
    """Actual hits / predicted hits over the walk-forward test, kept within 0.5-1: the model may be scaled down, never up."""
    if not v:
        return 1.0
    pred = sum(r["predicted"] * r["n"] for r in v["table"])
    act = sum(r["actual"] * r["n"] for r in v["table"])
    return float(min(1.0, max(0.5, act / pred))) if pred > 0 else 1.0


def _model_summary(listed: pd.DataFrame, model: dict | None, v: dict | None, calibration: float) -> dict:
    def stat(g: pd.DataFrame) -> dict:
        g = g[g["t_out"] != "open"]                                # results are of the trade as taken (a trend-exit trade can still be running)
        if not len(g):
            return {"n": 0, "hit": 0.0, "win": 0.0, "avg_r": 0.0, "pf": None, "rr": 0.0}
        r = g["t_R"].to_numpy()
        wins, losses = r[r > 0].sum(), -r[r <= 0].sum()
        return {"n": int(len(g)), "hit": round(float(g["hit"].mean()) * 100, 1), "win": round(float((r > 0).mean()) * 100, 1), "avg_r": round(float(r.mean()), 2),
                "pf": round(float(wins / losses), 2) if losses > 0 else None, "rr": round(float(g["rr"].median()), 1)}
    out = {"n": int(len(listed)), "calibration": round(calibration, 2)}
    if not len(listed):
        return out
    out.update(all=stat(listed), first=listed["date"].min().strftime("%Y-%m-%d"), last=listed["date"].max().strftime("%Y-%m-%d"), stocks=int(listed["symbol"].nunique()),
               by_kind=[{"kind": k, "side": sd, "name": name, **stat(listed[(listed["kind"] == k) & (listed["side"] == sd)])}
                        for (k, sd), name in pa.NAMES.items() if ((listed["kind"] == k) & (listed["side"] == sd)).any()],
               by_side=[{"side": sd, **stat(listed[listed["side"] == sd])} for sd in ("long", "short") if (listed["side"] == sd).any()],
               by_candle=[{"side": sd, "candle": nm, **stat(g)} for (sd, nm), g in listed[listed["kind"] == "candle"].groupby(["side", "candle"])],
               by_tf=[{"side": sd, "tf": tf, **stat(g)} for (sd, tf), g in listed[listed["kind"] == "pause"].groupby(["side", "tf"])],
               by_year=[{"year": int(y), **stat(g)} for y, g in listed.groupby(listed["date"].dt.year)])
    if model:
        out.update(base_rate=round(model["base_rate"] * 100, 1), miss_r=round(model["miss_r"], 2), weights={f: round(b, 2) for f, b in zip(model["features"], model["beta"][1:])})
    if v:
        out["walk"] = {"n": v["test_n"], "from": v["test_from"], "rows": [{"predicted": round(r["predicted"] * 100), "actual": round(r["actual"] * 100), "avg_r": round(r["avg_r"], 2), "n": r["n"]}
                                                                           for r in v["table"]]}
    return out


def collect(frames: dict[str, pd.DataFrame], p: pa.Params = pa.Params()) -> tuple[dict, pd.DataFrame, dict]:
    """(market context, every historical trigger with its outcome, the per-stock scans) - the slow part, shared by `build` and `history`."""
    ctx = pm.context(frames)
    ev, scans = pm.collect(frames, ctx, p)
    return ctx, ev, scans


EPOCH = pd.Timestamp("2000-01-01")


def history(ev: pd.DataFrame, fo: set[str] | None = None, sectors: dict[str, str] | None = None, min_rr: float = pa.Params().min_rr) -> dict[int, list[dict]]:
    """The backtest view's rows, by the year of the trigger: every past setup that would have been listed (planned reward:risk >= min_rr), with
    the trade as it was taken (entry at the next open) and what happened. Still-open trades are included and marked "open". `rr` is the
    reward:risk as planned at the trigger close - what the list showed, and what it is filtered on; `rr_entry` is the same from the actual entry."""
    fo, sectors = fo or set(), sectors or {}
    out: dict[int, list[dict]] = {}
    if not len(ev):
        return out
    day = lambda d: d.strftime("%Y-%m-%d")                         # noqa: E731
    for r in ev[ev["rr"] >= min_rr].sort_values(["date", "symbol"]).itertuples():
        sg = -1 if r.side == "short" else 1
        row = {"symbol": r.symbol, "fo": r.symbol in fo, "sector": sectors.get(r.symbol), "kind": r.kind, "side": r.side,
               "name": setup_name(r.kind, r.side, r.geo),
               "date": day(r.date), "entry_date": day(r.entry_date), "entry": round(float(r.entry), 2), "stop": round(float(r.stop), 2), "target": round(float(r.target), 2),
               "target_kind": r.target_kind, "trend": r.kind in pm.TREND_KINDS and r.side == "long", "rr": round(float(r.rr), 2), "rr_entry": round(float(r.rr_actual), 2), "risk_pct": round(float(sg * (r.entry - r.stop) / r.entry * 100), 2),
               "reward_pct": round(float(sg * (r.target / r.entry - 1) * 100), 2), "out": r.t_out, "exit_date": day(r.t_exit_date), "exit": round(float(r.t_exit), 2),
               "R": round(float(r.t_R), 2), "bars": int(r.t_bars), "vol": None if np.isnan(r.vol) else round(float(r.vol), 2), "with_market": bool(r.mkt50 == 1),
               "reason": r.why, "geo": r.geo}
        out.setdefault(int(r.date.year), []).append(row)
    return out


def history_candles(df: pd.DataFrame) -> dict:
    """One stock's whole daily history for the backtest charts, compact: `o` = days since 2000-01-01 for each candle, `c` = [open, high, low, close]."""
    idx = pd.DatetimeIndex(df.index).normalize()
    return {"o": [int(x) for x in (idx - EPOCH).days], "c": [[round(float(x), 2) for x in row] for row in df[["open", "high", "low", "close"]].to_numpy()]}


def build(frames: dict[str, pd.DataFrame], fo: set[str] | None = None, sectors: dict[str, str] | None = None, oi_store: dict | None = None,
          now: datetime | None = None, p: pa.Params = pa.Params(), macro_factors: dict | None = None, events: tuple | None = None,
          collected: tuple | None = None, news_fn=None, names: dict[str, str] | None = None, global_fn=None) -> tuple[dict, dict]:
    """-> (setups.json payload, setups_candles.json payload). `macro_factors` = signals.macro.readings(...) and `events` = (board meetings,
    corporate actions, calendar readable?) are optional context for the selected stock; without them those sections say there is no data."""
    fo, sectors = fo or set(), sectors or {}
    macro_factors = macro_factors or {}
    meetings, actions, calendar_ok = events or ({}, {}, False)
    names, news_cache, world_cache = names or {}, {}, {}

    def news_for(symbol: str):
        """Headlines for one listed stock, fetched on demand (news_fn(symbol, company name) -> (items, source)) and kept for its other setups."""
        if news_fn is None:
            return None
        if symbol not in news_cache:
            try:
                news_cache[symbol] = news_fn(symbol, names.get(symbol, symbol))
            except Exception:                                      # noqa: BLE001 - optional context
                news_cache[symbol] = ([], "failed")
        return news_cache[symbol]

    def world_for(symbol: str, sector: str | None):
        """The global peer / sector-fund series for the stock's group (global_fn(symbol, sector) -> {ticker: closes}), once per group."""
        if global_fn is None:
            return None
        k = peers_mod.group_of(symbol, sector)["key"] or sector
        if k not in world_cache:
            try:
                world_cache[k] = global_fn(symbol, sector)
            except Exception:                                      # noqa: BLE001
                world_cache[k] = {}
        return world_cache[k]
    flows = sc.sector_flows(frames, sectors)
    ctx, ev, scans = collected or collect(frames, p)
    listed = pm.closed(ev, p.min_rr)
    model = pm.fit(listed) if len(listed) else None
    v = pm.validate(listed) if len(listed) else None
    calibration = _calibration(v)
    asof = max(pd.DatetimeIndex(df.index).normalize()[-1] for df in frames.values())
    summary = _model_summary(listed, model, v, calibration)
    record = {(r["kind"], r["side"]): {"n": r["n"], "hit": r["hit"], "win": r["win"], "avg_r": r["avg_r"]} for r in summary.get("by_kind", [])}     # each type's own past result
    record.update({("pause", r["side"], r["tf"]): {"n": r["n"], "hit": r["hit"], "win": r["win"], "avg_r": r["avg_r"]} for r in summary.get("by_tf", [])})            # a continuation: its own timeframe
    record.update({("candle", r["side"], r["candle"]): {"n": r["n"], "hit": r["hit"], "win": r["win"], "avg_r": r["avg_r"]} for r in summary.get("by_candle", [])})     # a candle: its own
    rows, candles = [], {}
    for symbol, (triggers, pending) in scans.items():
        df = frames[symbol]
        dates = pd.DatetimeIndex(df.index).normalize()
        n = len(dates)
        if dates[-1] != asof:                                      # a stock that stopped trading: nothing to act on
            continue
        close, lows, highs = df["close"].to_numpy(float), df["low"].to_numpy(float), df["high"].to_numpy(float)
        last = close[-1]
        found = []
        for t in triggers:
            age, sg = n - 1 - t["i"], (1 if t["side"] == "long" else -1)
            if age > FRESH_DAYS:
                continue
            if age:                                                # already stopped or already there
                worst, best = (lows, highs) if sg == 1 else (highs, lows)
                if (sg * (worst[t["i"] + 1:] - t["stop"]) <= 0).any() or (sg * (best[t["i"] + 1:] - t["target"]) >= 0).any():
                    continue
            risk = sg * (last - t["stop"])
            if not risk > 0 or not (p.min_risk_pct <= risk / last * 100 <= p.max_risk_pct) or sg * (t["target"] - last) <= 0:
                continue
            found.append({**t, "status": "triggered", "age": age, "entry": last, "risk_pct": risk / last * 100, "reward_pct": sg * (t["target"] / last - 1) * 100,
                          "rr": sg * (t["target"] - last) / risk, "trigger_date": dates[t["i"]].strftime("%Y-%m-%d")})
        found += [{**t, "status": "pending", "age": None, "trigger_date": None} for t in pending]
        found = [t for t in found if t["side"] == "long" or symbol in fo]          # a short needs a futures contract: cash shorts cannot be carried overnight
        added = False
        for t in found:
            if t["rr"] < p.min_rr:
                continue
            added = True
            feat = pd.DataFrame([pm._row(symbol, t, dates[-1], ctx)])
            mom_raw = ctx["mom"][symbol].get(dates[-1], np.nan) if symbol in ctx["mom"] else np.nan       # raw values for the page: it words them by side itself
            market_up = bool(ctx["mkt50"].get(dates[-1], False))
            prob = float(pm.predict(model, feat)[0]) * calibration if model else None
            miss = model["miss_r"] if model else -1.0
            f = t["f"]
            trend = t["kind"] in pm.TREND_KINDS and t["side"] == "long"          # held while the weekly close stays above its 30-week average: no target, so no hit probability
            sma30 = pm.trend_sma(dates, df["close"].to_numpy(float)) if trend else None
            if not t["rr"] - miss > 0:
                continue
            rows.append({
                "symbol": symbol, "fo": symbol in fo, "sector": sectors.get(symbol), "kind": t["kind"], "side": t["side"],
                "name": setup_name(t["kind"], t["side"], t["geo"]), "status": t["status"], "age": t["age"], "trigger_date": t["trigger_date"],
                "reason": t["reason"], "price": round(float(last), 2), "entry": round(float(t["entry"]), 2), "stop": round(float(t["stop"]), 2), "target": round(float(t["target"]), 2),
                "target_kind": t["target_kind"], "risk_pct": round(float(t["risk_pct"]), 2), "reward_pct": round(float(t["reward_pct"]), 2), "rr": round(float(t["rr"]), 2),
                "p": None if trend else round(prob * 100, 1) if prob is not None else None, "breakeven": None if trend else round(-miss / (t["rr"] - miss) * 100, 1),
                "trail": {"weeks": pm.TREND_WEEKS, "sma": None if sma30 is None else round(sma30, 2)} if trend else None,
                "vol": None if np.isnan(f["vol"]) else round(float(f["vol"]), 2), "above200": None if np.isnan(f["above200"]) else bool(f["above200"]),
                "mom": None if np.isnan(mom_raw) else round(float(mom_raw) * 100), "market_up": market_up,
                "oi": oi_tag(oi_store, symbol, df["close"], side=t["side"]) if symbol in fo else None, "record": record.get(("candle", t["side"], t["geo"].get("candle"))) if t["kind"] == "candle" else record.get(("pause", t["side"], t["geo"].get("tf"))) if t["kind"] == "pause" else record.get((t["kind"], t["side"])),
                "macro": stock_macro.assess_stock(symbol, sectors.get(symbol), t["side"], macro_factors, world_for(symbol, sectors.get(symbol)), frames, sectors,
                                                  news_for(symbol), asof.date()), "vm": sc.value_migration(symbol, sectors.get(symbol), t["side"], flows, frames),
                "catalysts": sc.catalysts(symbol, df, t["side"], meetings, actions, asof.date(), calendar_ok), "geo": t["geo"]})
        if added:
            tail = df.tail(CHART_BARS)
            candles[symbol] = {"d": [d.strftime("%Y-%m-%d") for d in pd.DatetimeIndex(tail.index).normalize()],
                               "c": [[round(float(x), 2) for x in row] for row in tail[["open", "high", "low", "close"]].to_numpy()], "w": weekly_chart(df)}
    rows.sort(key=lambda r: (-r["rr"], r["risk_pct"], r["symbol"]))
    now = now or datetime.now()
    payload = {"asof": asof.strftime("%Y-%m-%d"), "updated_at": now.isoformat(timespec="seconds"), "universe": len(frames), "fo_count": sum(1 for s in frames if s in fo),
               "min_rr": p.min_rr, "market_up": bool(ctx["mkt50"].iloc[-1]), "model": summary,
               "context": {"macro": list(macro_factors.values()), "sectors": flows, "calendar": bool(calendar_ok)}, "setups": rows}
    return payload, {"asof": payload["asof"], "stocks": candles}
