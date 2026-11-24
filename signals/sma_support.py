"""Bullish moving-average support (the Swing tab's "SMA support" section): buy a green candle that sits on a clearly rising 21-day (daily) or
30-week (weekly) average, in a stock whose sector is also in an up-trend.

The rules (the numbers come from the backtest in scripts/backtest_sma_support.py, README "Bullish SMA support"):

  daily   the 21-session average is clearly rising (it is higher than 3 sessions ago and up 1% or more over 10 sessions) and the stock is in an up-trend
          (21-day average above the 50-day average and the close above the 50-day average); a GREEN candle closes at or above the 21-day average with its low
          within 1.5% above it (and no more than 3% below it).
  weekly  the same on weekly candles with the 30-week average: higher than 4 weeks ago and up 1% or more over 8 weeks, an up-trend (close above it and the
          average above where it was 20 weeks ago), a green weekly candle closing at or above it with its low within 3% above it (no more than 5% below).
  sector  the stock's sector (an equal-weight index of its stocks in the universe, three stocks at least) is above its own average of the same length
          (21 days / 30 weeks) and that average is rising (5 days / 4 weeks).
  volume  the signal candle's volume against the average of the 20 sessions (20 weeks) before it - the page filters on it. It is the one condition that
          separated the winners in the backtest.

Trade: buy ABOVE the green candle's high on the next session (next week for the weekly one) - a buy-stop order, filled at the high or at the open if
the price gaps over it; stop just below the candle's low (0.1%); target = the next weekly resistance (capped at 4 R) when it pays at least 1.5 R,
otherwise 3 R. The reward:risk is therefore never under 1.5. The risk (entry to stop) must be 0.5%-8% (daily) / 1%-8% (weekly).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from signals import price_action as pa
from signals.weekly_zone_backtest import weekly_bars

COST_PCT = 0.10
STOP_BUFFER = 0.001
MIN_RR, DEFAULT_RR, MAX_RR = 1.5, 3.0, 4.0
SECTOR_MIN_STOCKS = 3
TF = {
    "D": dict(n=21, slope_bars=10, slope_pct=1.0, short_up=3, near_pct=1.5, below_pct=3.0, min_risk=0.5, max_risk=8.0, max_hold=20, vol_default=1.5, name="21-day average"),
    "W": dict(n=30, slope_bars=8, slope_pct=1.0, short_up=4, near_pct=3.0, below_pct=5.0, min_risk=1.0, max_risk=8.0, max_hold=130, vol_default=1.2, name="30-week average"),
}
P = pa.Params()


# ---------------------------------------------------------------- sector support
def sector_flags(frames: dict[str, pd.DataFrame], sectors: dict[str, str]) -> dict[str, dict]:
    """{sector: {"d": bool Series by date, "w": bool Series by week-ending Friday}}: the sector's equal-weight index is above its average
    (21 days / 30 weeks) and that average is rising (5 days / 4 weeks). Sectors with fewer than SECTOR_MIN_STOCKS stocks are left out."""
    close = pd.DataFrame({s: df["close"].set_axis(pd.DatetimeIndex(df.index).normalize()) for s, df in frames.items()}).sort_index()
    ret = close.pct_change(fill_method=None)
    out = {}
    for name in sorted({sectors[s] for s in frames if sectors.get(s)}):
        cols = [s for s in frames if sectors.get(s) == name]
        if len(cols) < SECTOR_MIN_STOCKS:
            continue
        idx = (1 + ret[cols].mean(axis=1).fillna(0)).cumprod()
        wk = idx.resample("W-FRI").last().dropna()
        sd, sw = idx.rolling(21).mean(), wk.rolling(30).mean()
        out[name] = {"d": (idx > sd) & (sd > sd.shift(5)), "w": (wk > sw) & (sw > sw.shift(4))}
    return out


def _asof(flag: pd.Series | None, date: pd.Timestamp) -> bool:
    if flag is None or not len(flag):
        return False
    i = flag.index.searchsorted(date, side="right") - 1
    return bool(flag.iloc[i]) if i >= 0 else False


# ---------------------------------------------------------------- the signals
def _plan(entry: float, stop: float, wH: np.ndarray, w: int) -> dict | None:
    risk = entry - stop
    if not risk > 0:
        return None
    res = pa.next_resistance(wH, w, entry + 0.5 * risk, P) if w >= 20 else None
    if res is not None and (res - entry) / risk >= MIN_RR:
        target, kind = min(res, entry + MAX_RR * risk), "resistance"
    else:
        target, kind = entry + DEFAULT_RR * risk, "3R"
    return {"target": float(target), "target_kind": kind, "risk_pct": risk / entry * 100, "reward_pct": (target / entry - 1) * 100, "rr": (target - entry) / risk}


def signals(df: pd.DataFrame, tf: str, sector_flag: pd.Series | None) -> list[dict]:
    """Every green candle on the rising average in the stock's history (the signal candle's index `i` in daily bars, `w` the weekly bar, `date` its close),
    with the planned entry (above its high), stop, target, the volume ratio and whether the sector supported it. Nothing looks ahead."""
    cfg = TF[tf]
    if df is None or len(df) < 260:
        return []
    dates = pd.DatetimeIndex(df.index).normalize()
    O, H, L, C = (df[c].to_numpy(float) for c in ("open", "high", "low", "close"))
    V = df["volume"].to_numpy(float) if "volume" in df else np.full(len(df), np.nan)
    wk = weekly_bars(df.set_axis(dates))
    wO, wH, wL, wC = (wk[c].to_numpy(float) for c in ("open", "high", "low", "close"))
    wV = wk["volume"].to_numpy(float) if "volume" in wk else np.full(len(wk), np.nan)
    wdates = wk.index
    out = []
    n = cfg["n"]
    if tf == "D":
        sma, sma50 = pd.Series(C).rolling(n).mean().to_numpy(), pd.Series(C).rolling(50).mean().to_numpy()
        wk_of = np.searchsorted(wdates.values, dates.values, side="left") - 1             # the last week that closed before each session's own week
        for t in range(60, len(C)):
            s = sma[t]
            if np.isnan(sma[t - cfg["slope_bars"]]) or np.isnan(sma50[t]):
                continue
            if not (s > sma[t - cfg["short_up"]] and s / sma[t - cfg["slope_bars"]] - 1 >= cfg["slope_pct"] / 100 and s > sma50[t] and C[t] > sma50[t]):
                continue
            if not (C[t] > O[t] and C[t] >= s and L[t] <= s * (1 + cfg["near_pct"] / 100) and L[t] >= s * (1 - cfg["below_pct"] / 100)):
                continue
            entry, stop = float(H[t]), float(L[t] * (1 - STOP_BUFFER))
            risk = (entry - stop) / entry * 100
            if not cfg["min_risk"] <= risk <= cfg["max_risk"]:
                continue
            pl = _plan(entry, stop, wH, int(wk_of[t]))
            if pl is None:
                continue
            base = V[t - 20:t].mean() if t >= 20 else np.nan
            out.append({"tf": "D", "i": t, "w": int(wk_of[t]), "date": dates[t], "sma": float(s), "slope": float((s / sma[t - cfg["slope_bars"]] - 1) * 100), "entry": entry,
                        "stop": stop, **pl, "vr": float(V[t] / base) if base and base > 0 else None, "sector_ok": _asof(sector_flag, dates[t])})
    else:
        wsma = pd.Series(wC).rolling(n).mean().to_numpy()
        for w in range(40, len(wC)):
            s = wsma[w]
            if np.isnan(wsma[w - cfg["slope_bars"]]):
                continue
            if not (s > wsma[w - cfg["short_up"]] and s / wsma[w - cfg["slope_bars"]] - 1 >= cfg["slope_pct"] / 100 and wC[w] > s and (w < 50 or s > wsma[w - 20])):
                continue
            if not (wC[w] > wO[w] and wC[w] >= s and wL[w] <= s * (1 + cfg["near_pct"] / 100) and wL[w] >= s * (1 - cfg["below_pct"] / 100)):
                continue
            entry, stop = float(wH[w]), float(wL[w] * (1 - STOP_BUFFER))
            risk = (entry - stop) / entry * 100
            if not cfg["min_risk"] <= risk <= cfg["max_risk"]:
                continue
            pl = _plan(entry, stop, wH, w)
            if pl is None:
                continue
            base = np.nanmean(wV[w - 20:w]) if w >= 20 else np.nan
            out.append({"tf": "W", "i": int(np.searchsorted(dates.values, wdates.values[w], side="right")) - 1, "w": w, "date": wdates[w], "sma": float(s),
                        "slope": float((s / wsma[w - cfg["slope_bars"]] - 1) * 100), "entry": entry, "stop": stop, **pl,
                        "vr": float(wV[w] / base) if base and base > 0 else None, "sector_ok": _asof(sector_flag, wdates[w])})
    return out


def outcome(df: pd.DataFrame, sig: dict) -> dict | None:
    """The trade as taken: daily - a buy-stop above the signal candle's high on the NEXT session only; weekly - the first session of the next week that trades
    above the weekly high. Fill at the high (or the open when it gaps over it). Stop first (a gap through it fills at the open), target as a limit, a time
    exit at the close after `max_hold` sessions, 0.10% costs. None when the order never fills."""
    dates = pd.DatetimeIndex(df.index).normalize()
    O, H, L, C = (df[c].to_numpy(float) for c in ("open", "high", "low", "close"))
    n, cfg = len(C), TF[sig["tf"]]
    if sig["tf"] == "D":
        window = range(sig["i"] + 1, min(n, sig["i"] + 2))
    else:
        a = sig["i"] + 1
        b = int(np.searchsorted(dates.values, np.datetime64(sig["date"] + pd.Timedelta(days=7)), side="right"))
        window = range(a, min(n, b))
    e = next((k for k in window if H[k] > sig["entry"]), None)
    if e is None:
        return None
    entry = max(O[e], sig["entry"])
    stop, target = sig["stop"], sig["target"]
    for k in range(e, min(n, e + cfg["max_hold"])):
        if k > e and O[k] <= stop:
            return {"entry_i": e, "entry": entry, "exit_i": k, "exit": float(O[k]), "reason": "stop"}
        if L[k] <= stop:
            return {"entry_i": e, "entry": entry, "exit_i": k, "exit": float(stop), "reason": "stop"}
        if H[k] >= target:
            return {"entry_i": e, "entry": entry, "exit_i": k, "exit": float(max(target, O[k]) if k > e and O[k] > target else target), "reason": "target"}
    k = min(n, e + cfg["max_hold"]) - 1
    return {"entry_i": e, "entry": entry, "exit_i": k, "exit": float(C[k]), "reason": "time" if e + cfg["max_hold"] <= n else "open"}


def pct_and_r(trade: dict, sig: dict) -> tuple[float, float]:
    pct = (trade["exit"] / trade["entry"] - 1) * 100 - COST_PCT
    risk_pct = (trade["entry"] - sig["stop"]) / trade["entry"] * 100
    return pct, pct / risk_pct if risk_pct > 0 else float("nan")


# ---------------------------------------------------------------- the live list
CANDLES = {"D": 90, "W": 80}


def live(df: pd.DataFrame, tf: str, sector_flag: pd.Series | None, asof: pd.Timestamp) -> list[dict]:
    """The setups that can still be traded as of the last close. Daily: the signal candle is one of the last 3 sessions - 'waiting' while it is the last candle
    (the buy-stop is for the next session), 'triggered' when the next session traded above its high and neither the stop nor the target has been reached since.
    Weekly: the last completed week (waiting while the next week has not traded above its high; triggered afterwards, until the stop or the target is reached)."""
    dates = pd.DatetimeIndex(df.index).normalize()
    O, H, L, C = (df[c].to_numpy(float) for c in ("open", "high", "low", "close"))
    n = len(C)
    out = []
    for sig in signals(df, tf, sector_flag):
        done_week = sig["tf"] == "W" and sig["date"] <= asof                       # a weekly candle stamped with its Friday counts once that day has closed
        if sig["tf"] == "W" and not done_week:
            continue
        age = (n - 1 - sig["i"]) if sig["tf"] == "D" else None
        if sig["tf"] == "D" and age > 3:
            continue
        if sig["tf"] == "W" and (asof - sig["date"]).days > 35:
            continue
        tr = outcome(df, sig)
        if tr is None:
            fresh = (sig["tf"] == "D" and age == 0) or (sig["tf"] == "W" and (asof - sig["date"]).days < 7)
            if not fresh:
                continue
            status, entry = "waiting", sig["entry"]
        else:
            if tr["reason"] in ("stop", "target") or tr["exit_i"] < n - 1:
                continue                                                           # already stopped or at the target
            status, entry = "triggered", tr["entry"]
            risk = (entry - sig["stop"]) / entry * 100
            if not 0 < risk <= TF[tf]["max_risk"] * 1.5 or (sig["target"] / entry - 1) * 100 <= 0:
                continue
        last = float(C[-1])
        out.append({**sig, "status": status, "entry": float(entry), "price": last, "risk_pct": (entry - sig["stop"]) / entry * 100,
                    "reward_pct": (sig["target"] / entry - 1) * 100, "rr": (sig["target"] - entry) / (entry - sig["stop"]), "age": age})
    return out


def build(frames: dict[str, pd.DataFrame], sectors: dict[str, str], fo: set[str], now) -> dict:
    """The sma_support.json payload: every tradable setup of both timeframes, with the sector flag and the volume ratio (the page filters on them), the reason in
    words and a small candle series per setup for the chart."""
    flags = sector_flags(frames, sectors)
    asof = max(pd.DatetimeIndex(df.index).normalize()[-1] for df in frames.values())
    rows = []
    for symbol, df in frames.items():
        sec = sectors.get(symbol)
        wk = weekly_bars(df.set_axis(pd.DatetimeIndex(df.index).normalize()))
        for tf in ("D", "W"):
            cfg = TF[tf]
            bars, closes = (df, df["close"].to_numpy(float)) if tf == "D" else (wk, wk["close"].to_numpy(float))
            bars = bars.tail(CANDLES[tf])
            avg = pd.Series(closes).rolling(cfg["n"]).mean().to_numpy()[-len(bars):]
            found = live(df, tf, (flags.get(sec) or {}).get("d" if tf == "D" else "w"), asof)
            for r in sorted(found, key=lambda x: x["date"])[-1:]:                      # the latest signal per stock and timeframe
                rows.append({
                    "symbol": symbol, "fo": symbol in fo, "sector": sec, "tf": tf, "status": r["status"], "age": r["age"], "signal_date": r["date"].strftime("%Y-%m-%d"),
                    "price": round(r["price"], 2), "sma": round(r["sma"], 2), "slope": round(r["slope"], 1), "entry": round(r["entry"], 2), "stop": round(r["stop"], 2),
                    "target": round(r["target"], 2), "target_kind": r["target_kind"], "risk_pct": round(r["risk_pct"], 2), "reward_pct": round(r["reward_pct"], 2), "rr": round(r["rr"], 2),
                    "vr": None if r["vr"] is None else round(r["vr"], 2), "sector_ok": bool(r["sector_ok"]), "sector_known": sec in flags,
                    "reason": reason(r, cfg),
                    "candles": {"d": [d.strftime("%Y-%m-%d") for d in pd.DatetimeIndex(bars.index).normalize()], "c": [[round(float(x), 2) for x in row] for row in bars[["open", "high", "low", "close"]].to_numpy()],
                                "sma": [None if np.isnan(v) else round(float(v), 2) for v in avg]}})
    rows.sort(key=lambda r: (r["tf"], -(r["vr"] or 0)))
    return {"asof": asof.strftime("%Y-%m-%d"), "updated_at": now.isoformat(), "rows": rows, "universe": len(frames), "sectors": len(flags),
            "defaults": {"D": TF["D"]["vol_default"], "W": TF["W"]["vol_default"]}}


def reason(r: dict, cfg: dict) -> str:
    unit = "sessions" if r["tf"] == "D" else "weeks"
    vol = "" if r["vr"] is None else f"; volume {r['vr']:.1f}x its {'20-day' if r['tf'] == 'D' else '20-week'} average"
    return (f"green {'daily' if r['tf'] == 'D' else 'weekly'} candle on the {cfg['name']} ({r['sma']:,.1f}, up {r['slope']:.1f}% over {cfg['slope_bars']} {unit}); "
            f"buy above its high {r['entry']:,.1f}, stop {r['stop']:,.1f} below its low{vol}")
