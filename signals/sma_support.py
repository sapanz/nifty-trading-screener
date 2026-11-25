"""Moving-average support and resistance (the Swing tab's "SMA support" section).

LONG - bullish support: a green candle sits on a clearly rising 21-day (daily) or 30-week (weekly) average, in a stock whose sector is also in an up-trend.
SHORT - the bear-market mirror (F&O stocks only: a cash short cannot be carried overnight): a red candle sits under a clearly falling average, in a weak sector.

Long rules (the numbers come from the backtest in scripts/backtest_sma_support.py, README "Bullish SMA support"):

  daily   the 21-session average is clearly rising (higher than 3 sessions ago and up 1% or more over 10 sessions) and the stock is in an up-trend
          (21-day average above the 50-day average and the close above the 50-day average); a GREEN candle closes at or above the 21-day average with its low
          within 1.5% above it (and no more than 3% below it).
  weekly  the same on weekly candles with the 30-week average: higher than 4 weeks ago and up 1% or more over 8 weeks, an up-trend (close above it and the
          average above where it was 20 weeks ago), a green weekly candle closing at or above it with its low within 3% above it (no more than 5% below).
  sector  the stock's sector (an equal-weight index of its stocks in the universe, three stocks at least) is above its own average of the same length
          (21 days / 30 weeks) and that average is rising (5 days / 4 weeks).
  volume  the signal candle's volume against the average of the 20 sessions (20 weeks) before it - the page filters on it.

Short rules are everything flipped: the average clearly FALLING (lower than 3 sessions / 4 weeks ago and down 1% or more over 10 sessions / 8 weeks), a down-trend
(average below the 50-day average and the close below it / close below the average and the average below where it was 20 weeks ago), a RED candle closing at or
below the average with its HIGH within 1.5% (3%) below it (and no more than 3% (5%) above it), the sector below its own falling average.

Trade: LONG - buy ABOVE the green candle's high on the next session (next week for the weekly one), a buy-stop order filled at the high or at the open when the
price gaps over it; stop just below the candle's low (0.1%). SHORT - sell BELOW the red candle's low, stop just above its high. Target = the next weekly
resistance (support for a short) capped at 4 R when it pays at least 1.5 R, otherwise 3 R, so the reward:risk is never under 1.5. The risk (entry to stop) must
be 0.5%-8% (daily) / 1%-8% (weekly).
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
    "D": dict(n=21, slope_bars=10, slope_pct=1.0, short_up=3, near_pct=1.5, below_pct=3.0, min_risk=0.5, max_risk=8.0, max_hold=20, vol_default=1.5, name="moving average"),
    "W": dict(n=30, slope_bars=8, slope_pct=1.0, short_up=4, near_pct=3.0, below_pct=5.0, min_risk=1.0, max_risk=8.0, max_hold=130, vol_default=1.2, name="moving average"),
}
SIDES = ("long", "short")
P = pa.Params()


# ---------------------------------------------------------------- sector support
def sector_flags(frames: dict[str, pd.DataFrame], sectors: dict[str, str]) -> dict[str, dict]:
    """{sector: flags} where flags hold bool Series by date ("d", "dw") and by week-ending Friday ("w", "ww"): the sector's equal-weight index is above its average
    (21 days / 30 weeks) and that average is rising (5 days / 4 weeks) - "d" / "w" - or below a falling one - "dw" / "ww". Sectors with fewer than
    SECTOR_MIN_STOCKS stocks are left out."""
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
        out[name] = {"d": (idx > sd) & (sd > sd.shift(5)), "w": (wk > sw) & (sw > sw.shift(4)), "dw": (idx < sd) & (sd < sd.shift(5)), "ww": (wk < sw) & (sw < sw.shift(4))}
    return out


def flag_for(flags: dict | None, tf: str, side: str) -> pd.Series | None:
    """The sector flag a trade needs: strong for a long, weak for a short."""
    if not flags:
        return None
    return flags.get(("d" if tf == "D" else "w") + ("w" if side == "short" else ""))


def _asof(flag: pd.Series | None, date: pd.Timestamp) -> bool:
    if flag is None or not len(flag):
        return False
    i = flag.index.searchsorted(date, side="right") - 1
    return bool(flag.iloc[i]) if i >= 0 else False


# ---------------------------------------------------------------- the signals
def _plan(entry: float, stop: float, wH: np.ndarray, wL: np.ndarray, w: int, side: str = "long") -> dict | None:
    """Target and reward:risk for an order at `entry` with the stop at `stop`: the next weekly resistance (support for a short) when it pays at least MIN_RR
    (capped at MAX_RR), else DEFAULT_RR."""
    if side == "long":
        risk = entry - stop
        if not risk > 0:
            return None
        res = pa.next_resistance(wH, w, entry + 0.5 * risk, P) if w >= 20 else None
        ok = res is not None and (res - entry) / risk >= MIN_RR
        target, kind = (min(res, entry + MAX_RR * risk), "resistance") if ok else (entry + DEFAULT_RR * risk, "3R")
        return {"target": float(target), "target_kind": kind, "risk_pct": risk / entry * 100, "reward_pct": (target / entry - 1) * 100, "rr": (target - entry) / risk}
    risk = stop - entry
    if not risk > 0:
        return None
    sup = pa.next_support(wL, w, entry - 0.5 * risk, P) if w >= 20 else None
    ok = sup is not None and (entry - sup) / risk >= MIN_RR
    target, kind = (max(sup, entry - MAX_RR * risk), "support") if ok else (entry - DEFAULT_RR * risk, "3R")
    if not target > 0:
        return None
    return {"target": float(target), "target_kind": kind, "risk_pct": risk / entry * 100, "reward_pct": (1 - target / entry) * 100, "rr": (entry - target) / risk}


def signals(df: pd.DataFrame, tf: str, sector_flag: pd.Series | None, side: str = "long") -> list[dict]:
    """Every candle on the rising (falling) average in the stock's history: the signal candle's index `i` in daily bars (`w` the weekly bar before it, for the
    resistance / support lookup), its `date`, the planned trigger price `entry` (above its high / below its low), `stop`, target, the volume ratio `vr`, and whether the sector supported it. Nothing looks ahead."""
    cfg = TF[tf]
    long = side == "long"
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
    n, sb, up_n = cfg["n"], cfg["slope_bars"], cfg["short_up"]
    if long:
        rising = lambda a, b, c: a > b and a / c - 1 >= cfg["slope_pct"] / 100            # noqa: E731
    else:
        rising = lambda a, b, c: a < b and a / c - 1 <= -cfg["slope_pct"] / 100           # noqa: E731

    def candle_ok(o, h, l, c, s):
        if long:
            return c > o and c >= s and l <= s * (1 + cfg["near_pct"] / 100) and l >= s * (1 - cfg["below_pct"] / 100)
        return c < o and c <= s and h >= s * (1 - cfg["near_pct"] / 100) and h <= s * (1 + cfg["below_pct"] / 100)

    def trigger(h, l):
        return (float(h), float(l * (1 - STOP_BUFFER))) if long else (float(l), float(h * (1 + STOP_BUFFER)))

    if tf == "D":
        sma, sma50 = pd.Series(C).rolling(n).mean().to_numpy(), pd.Series(C).rolling(50).mean().to_numpy()
        wk_of = np.searchsorted(wdates.values, dates.values, side="left") - 1             # the last week that closed before each session's own week
        for t in range(60, len(C)):
            s = sma[t]
            if np.isnan(sma[t - sb]) or np.isnan(sma50[t]):
                continue
            trend = (s > sma50[t] and C[t] > sma50[t]) if long else (s < sma50[t] and C[t] < sma50[t])
            if not (rising(s, sma[t - up_n], sma[t - sb]) and trend and candle_ok(O[t], H[t], L[t], C[t], s)):
                continue
            entry, stop = trigger(H[t], L[t])
            if not cfg["min_risk"] <= abs(entry - stop) / entry * 100 <= cfg["max_risk"]:
                continue
            pl = _plan(entry, stop, wH, wL, int(wk_of[t]), side)
            if pl is None:
                continue
            base = V[t - 20:t].mean() if t >= 20 else np.nan
            out.append({"tf": "D", "side": side, "i": t, "w": int(wk_of[t]), "date": dates[t], "sma": float(s), "slope": float((s / sma[t - sb] - 1) * 100), "gap50": float((s / sma50[t] - 1) * 100),
                        "ret60": float((C[t] / C[t - 60] - 1) * 100), "entry": entry,
                        "stop": stop, **pl, "vr": float(V[t] / base) if base and base > 0 else None,
                        "sector_ok": _asof(sector_flag, dates[t])})
    else:
        wsma = pd.Series(wC).rolling(n).mean().to_numpy()
        for w in range(40, len(wC)):
            s = wsma[w]
            if np.isnan(wsma[w - sb]):
                continue
            trend = (wC[w] > s and (w < 50 or s > wsma[w - 20])) if long else (wC[w] < s and (w < 50 or s < wsma[w - 20]))
            if not (rising(s, wsma[w - up_n], wsma[w - sb]) and trend and candle_ok(wO[w], wH[w], wL[w], wC[w], s)):
                continue
            entry, stop = trigger(wH[w], wL[w])
            if not cfg["min_risk"] <= abs(entry - stop) / entry * 100 <= cfg["max_risk"]:
                continue
            pl = _plan(entry, stop, wH, wL, w, side)
            if pl is None:
                continue
            base = np.nanmean(wV[w - 20:w]) if w >= 20 else np.nan
            out.append({"tf": "W", "side": side, "i": int(np.searchsorted(dates.values, wdates.values[w], side="right")) - 1, "w": w, "date": wdates[w], "sma": float(s),
                        "slope": float((s / wsma[w - sb] - 1) * 100), "entry": entry, "stop": stop, **pl, "vr": float(wV[w] / base) if base and base > 0 else None,
                        "sector_ok": _asof(sector_flag, wdates[w])})
    return out


def outcome(df: pd.DataFrame, sig: dict) -> dict | None:
    """The trade as taken: daily - a stop order beyond the signal candle's extreme (above the high for a long, below the low for a short) on the NEXT session only;
    weekly - the first session of the next week that trades through it. Fill at the trigger (or the open when it gaps through). Stop first (a gap through the stop
    fills at the open), target as a limit, a time exit at the close after `max_hold` sessions. None when the order never fills."""
    dates = pd.DatetimeIndex(df.index).normalize()
    O, H, L, C = (df[c].to_numpy(float) for c in ("open", "high", "low", "close"))
    n, cfg, long = len(C), TF[sig["tf"]], sig.get("side", "long") == "long"
    if sig["tf"] == "D":
        window = range(sig["i"] + 1, min(n, sig["i"] + 2))
    else:
        a = sig["i"] + 1
        b = int(np.searchsorted(dates.values, np.datetime64(sig["date"] + pd.Timedelta(days=7)), side="right"))
        window = range(a, min(n, b))
    e = next((k for k in window if (H[k] > sig["entry"] if long else L[k] < sig["entry"])), None)
    if e is None:
        return None
    entry = max(O[e], sig["entry"]) if long else min(O[e], sig["entry"])
    stop, target = sig["stop"], sig["target"]
    for k in range(e, min(n, e + cfg["max_hold"])):
        if k > e and (O[k] <= stop if long else O[k] >= stop):                       # gapped through the stop: filled at the open
            return {"entry_i": e, "entry": float(entry), "exit_i": k, "exit": float(O[k]), "reason": "stop"}
        if (L[k] <= stop) if long else (H[k] >= stop):
            return {"entry_i": e, "entry": float(entry), "exit_i": k, "exit": float(stop), "reason": "stop"}
        if (H[k] >= target) if long else (L[k] <= target):
            gapped = k > e and (O[k] > target if long else O[k] < target)
            return {"entry_i": e, "entry": float(entry), "exit_i": k, "exit": float((max if long else min)(target, O[k]) if gapped else target), "reason": "target"}
    k = min(n, e + cfg["max_hold"]) - 1
    return {"entry_i": e, "entry": float(entry), "exit_i": k, "exit": float(C[k]), "reason": "time" if e + cfg["max_hold"] <= n else "open"}


def pct_and_r(trade: dict, sig: dict) -> tuple[float, float]:
    sg = 1 if sig.get("side", "long") == "long" else -1
    pct = sg * (trade["exit"] / trade["entry"] - 1) * 100 - COST_PCT
    risk_pct = sg * (trade["entry"] - sig["stop"]) / trade["entry"] * 100
    return pct, pct / risk_pct if risk_pct > 0 else float("nan")


# ---------------------------------------------------------------- clear uptrend (daily, long)
CLEAR_PERCENTILE = 80                  # "clear uptrend" = the top fifth of the trend score
TREND_MEASURES = ("slope", "gap50", "ret60")


def trend_tables(daily_long: list[dict]) -> dict:
    """The distributions the trend score is ranked against: for each of the three measures (how fast the 21-day average rose over 10 sessions, how far it sits above the
    50-day average, the stock's 60-session return) the sorted values over every daily long signal, and the sorted trend scores themselves (the average percentile of the
    three). Built from the whole history of signals, so a stock today is ranked against every signal the rules ever gave."""
    cols = {m: np.sort(np.array([x[m] for x in daily_long], float)) for m in TREND_MEASURES}
    raw = np.mean([np.searchsorted(cols[m], np.array([x[m] for x in daily_long], float), side="right") / len(daily_long) for m in TREND_MEASURES], axis=0)
    return {**cols, "scores": np.sort(raw)}


def trend_rank(sig: dict, tables: dict) -> int:
    """0-100: where this signal's trend score sits among every daily long signal (100 = the clearest uptrend). A rank of CLEAR_PERCENTILE or more is a clear uptrend."""
    pcts = [np.searchsorted(tables[m], sig[m], side="right") / len(tables[m]) for m in TREND_MEASURES]
    return int(round(float(np.searchsorted(tables["scores"], float(np.mean(pcts)), side="right") / len(tables["scores"])) * 100))


def collect_trend_tables(frames: dict[str, pd.DataFrame], sectors: dict[str, str]) -> dict:
    """The trend tables from the daily long signals of every stock in `frames` (the sector flag does not matter for the distribution)."""
    sigs = []
    for df in frames.values():
        sigs += signals(df, "D", None, "long")
    return trend_tables(sigs) if sigs else {}


# ---------------------------------------------------------------- the live list
CANDLES = {"D": 90, "W": 80}


def live(df: pd.DataFrame, tf: str, sector_flag: pd.Series | None, asof: pd.Timestamp, side: str = "long") -> list[dict]:
    """The setups that can still be traded as of the last close. Daily: the signal candle is one of the last 3 sessions - 'waiting' while it is the last candle
    (the stop order is for the next session), 'triggered' when the next session went through it and neither the stop nor the target has been reached since.
    Weekly: the last completed weeks (waiting while the next week has not gone through the extreme; triggered afterwards, until the stop or the target)."""
    n = len(df)
    sg = 1 if side == "long" else -1
    out = []
    for sig in signals(df, tf, sector_flag, side):
        if sig["tf"] == "W" and sig["date"] > asof:
            continue                                                                # a weekly candle stamped with its Friday counts once that day has closed
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
            risk = sg * (entry - sig["stop"]) / entry * 100
            if not 0 < risk <= TF[tf]["max_risk"] * 1.5 or sg * (sig["target"] / entry - 1) <= 0:
                continue
        out.append({**sig, "status": status, "entry": float(entry), "price": float(df["close"].iloc[-1]), "risk_pct": sg * (entry - sig["stop"]) / entry * 100,
                    "reward_pct": sg * (sig["target"] / entry - 1) * 100, "rr": abs(sig["target"] - entry) / abs(entry - sig["stop"]), "age": age})
    return out


def build(frames: dict[str, pd.DataFrame], sectors: dict[str, str], fo: set[str], now, tables: dict | None = None) -> dict:
    """The sma_support.json payload: every tradable setup of both timeframes and both sides (shorts for F&O stocks only), with the sector flag and the volume ratio
    (the page filters on them), the reason in words and a small candle series per setup for the chart."""
    flags = sector_flags(frames, sectors)
    tables = tables if tables is not None else collect_trend_tables(frames, sectors)
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
            for side in SIDES:
                if side == "short" and symbol not in fo:
                    continue
                found = live(df, tf, flag_for(flags.get(sec), tf, side), asof, side)
                for r in sorted(found, key=lambda x: x["date"])[-1:]:                      # the latest signal per stock, timeframe and side
                    rows.append({
                        "symbol": symbol, "fo": symbol in fo, "sector": sec, "tf": tf, "side": side, "status": r["status"], "age": r["age"], "signal_date": r["date"].strftime("%Y-%m-%d"),
                        "price": round(r["price"], 2), "sma": round(r["sma"], 2), "slope": round(r["slope"], 1), "entry": round(r["entry"], 2), "stop": round(r["stop"], 2),
                        "target": round(r["target"], 2), "target_kind": r["target_kind"], "risk_pct": round(r["risk_pct"], 2), "reward_pct": round(r["reward_pct"], 2), "rr": round(r["rr"], 2),
                        "vr": None if r["vr"] is None else round(r["vr"], 2), "sector_ok": bool(r["sector_ok"]), "sector_known": sec in flags, "trend": trend_rank(r, tables) if tf == "D" and side == "long" and tables else None,
                        "reason": reason(r, cfg),
                        "candles": {"d": [d.strftime("%Y-%m-%d") for d in pd.DatetimeIndex(bars.index).normalize()], "c": [[round(float(x), 2) for x in row] for row in bars[["open", "high", "low", "close"]].to_numpy()],
                                    "sma": [None if np.isnan(v) else round(float(v), 2) for v in avg]}})
    rows.sort(key=lambda r: (r["tf"], r["side"], -(r["vr"] or 0)))
    return {"asof": asof.strftime("%Y-%m-%d"), "updated_at": now.isoformat(), "rows": rows, "universe": len(frames), "sectors": len(flags), "clear_percentile": CLEAR_PERCENTILE,
            "defaults": {"D": TF["D"]["vol_default"], "W": TF["W"]["vol_default"]}}


EPOCH = pd.Timestamp("2000-01-01")
HIST_MIN_VOLUME = 1.0                       # the backtest list keeps the trades whose volume was at least this multiple of the 20-day (20-week) average
HIST_COLS = ["s", "tf", "side", "sig", "ent", "entry", "stop", "target", "ext", "exit", "out", "pct", "r", "vr", "sec", "tr"]
HIST_OUT = ["stop", "target", "time"]


def history(frames: dict[str, pd.DataFrame], sectors: dict[str, str], fo: set[str], tables: dict | None = None) -> dict:
    """The backtest list for the page: every closed trade of the rules (both sides and timeframes, shorts for F&O stocks only) whose volume was at least
    HIST_MIN_VOLUME x its average, as compact rows - the symbol's index in `symbols`, tf (0 daily / 1 weekly), side (0 long / 1 short), the signal / entry /
    exit dates as days since 2000-01-01, the entry, stop, target and exit prices, the exit reason (0 stop / 1 target / 2 time), the net % (costs in), the R, the volume
    ratio, the sector flag and `tr` (the trend rank 0-100 of a daily long trade, -1 for the others). The sector, volume and trend filters of the page then work on it."""
    flags = sector_flags(frames, sectors)
    tables = tables if tables is not None else collect_trend_tables(frames, sectors)
    symbols, rows = [], []
    day = lambda d: int((pd.Timestamp(d).normalize() - EPOCH).days)             # noqa: E731
    for symbol, df in frames.items():
        sec = sectors.get(symbol)
        dates = pd.DatetimeIndex(df.index).normalize()
        mine = []
        for tf in ("D", "W"):
            for side in SIDES:
                if side == "short" and symbol not in fo:
                    continue
                for sig in signals(df, tf, flag_for(flags.get(sec), tf, side), side):
                    if sig["vr"] is None or sig["vr"] < HIST_MIN_VOLUME:
                        continue
                    tr = outcome(df, sig)
                    if tr is None or tr["reason"] == "open":
                        continue
                    pct, r = pct_and_r(tr, sig)
                    mine.append([0, 0 if tf == "D" else 1, 0 if side == "long" else 1, day(sig["date"]), day(dates[tr["entry_i"]]), round(tr["entry"], 2), round(sig["stop"], 2),
                                 round(sig["target"], 2), day(dates[tr["exit_i"]]), round(tr["exit"], 2), HIST_OUT.index(tr["reason"]), round(pct, 2), round(r, 2),
                                 round(sig["vr"], 2), int(bool(sig["sector_ok"])), trend_rank(sig, tables) if tf == "D" and side == "long" and tables else -1])
        if mine:
            symbols.append(symbol)
            rows += [[len(symbols) - 1] + m[1:] for m in mine]
    rows.sort(key=lambda x: (x[4], x[0]))
    return {"cols": HIST_COLS, "symbols": symbols, "sectors": {s: sectors.get(s) for s in symbols}, "fo": [s for s in symbols if s in fo], "rows": rows, "min_volume": HIST_MIN_VOLUME}


def reason(r: dict, cfg: dict) -> str:
    unit = "sessions" if r["tf"] == "D" else "weeks"
    long = r["side"] == "long"
    vol = "" if r["vr"] is None else f"; volume {r['vr']:.1f}x its {'20-day' if r['tf'] == 'D' else '20-week'} average"
    tf = "daily" if r["tf"] == "D" else "weekly"
    if long:
        return (f"green {tf} candle on the {cfg['name']} ({r['sma']:,.1f}, up {r['slope']:.1f}% over {cfg['slope_bars']} {unit}); "
                f"buy above its high {r['entry']:,.1f}, stop {r['stop']:,.1f} below its low{vol}")
    return (f"red {tf} candle under the {cfg['name']} ({r['sma']:,.1f}, down {abs(r['slope']):.1f}% over {cfg['slope_bars']} {unit}); "
            f"sell below its low {r['entry']:,.1f}, stop {r['stop']:,.1f} above its high{vol}")
