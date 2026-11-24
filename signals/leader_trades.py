"""High-range leader trades - every day's leaders traded by the Momentum strategy's rules, one position per stock, to show WHEN each was bought and when it
is sold (the leader list changes daily; a bought stock is held on its own clock, not on the list).

A "high-range leader" (signals.movers.leaders) is a stock in the 10 widest-range liquid F&O stocks that is also in the 30 strongest by momentum. The trade rules are the
Momentum top-10 long strategy's (signals.momentum): only while the market (equal-weight index of the F&O stocks) is above its 50-session average; entry at the NEXT open,
skipped when the open is more than GAP_CAP_PCT above the signal close; stop = entry x (1 - STOP_ATR x ATR%) (stop first, a gap through it fills at the open); exit at the
close of the HOLD_SESSIONS-th session after the signal, whether or not the stock is still on the list; no new entry in a stock that is still held; 0.10% costs.
"""
from __future__ import annotations

import warnings

import numpy as np
import pandas as pd

from signals import momentum, movers

COST_PCT = 0.10
EPOCH = pd.Timestamp("2000-01-01")
COLS = ["s", "sig", "ent", "entry", "stop", "ext", "exit", "out", "pct", "mv", "mom", "atr", "last"]      # out: 0 stop / 1 time exit / 2 still held (exit = the last close)
OUT = ["stop", "time", "open"]


def _percentile_rank(x: np.ndarray) -> np.ndarray:
    ok = ~np.isnan(x)
    r = np.full(x.shape, np.nan)
    r[ok] = pd.Series(x[ok]).rank(pct=True).to_numpy()
    return r


def trades(frames: dict[str, pd.DataFrame], fo: set[str], hold: int = momentum.HOLD_SESSIONS) -> tuple[list[dict], list[str], dict]:
    """(trades, dates, info) for the F&O stocks in `frames`. Each trade: symbol, sig (signal date index), entry / stop / exit prices and dates, out (stop / time / open),
    net %, the Movers rank, the momentum rank, ATR% and the market gate state. `info` = {"asof", "market_up", "leaders_today"}."""
    syms = sorted(s for s in frames if s in fo)
    dates = sorted({d for s in syms for d in pd.DatetimeIndex(frames[s].index).normalize()})
    didx = pd.DatetimeIndex(dates)
    n, m = len(dates), len(syms)

    def mat(col):
        out = np.full((n, m), np.nan)
        for j, s in enumerate(syms):
            f = frames[s][col]
            out[didx.get_indexer(pd.DatetimeIndex(frames[s].index).normalize()), j] = f.to_numpy(float)
        return out
    O, H, L, C, V = mat("open"), mat("high"), mat("low"), mat("close"), mat("volume")
    prev = np.vstack([np.full((1, m), np.nan), C[:-1]])
    tr = np.maximum(H - L, np.maximum(np.abs(H - prev), np.abs(L - prev)))
    atr = pd.DataFrame(tr).rolling(momentum.ATR_DAYS).mean().to_numpy() / C * 100
    ret = np.where(~np.isnan(prev) & ~np.isnan(C), C / np.where(np.isnan(prev), 1, prev) - 1, np.nan)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)                  # days with no stock trading give an empty mean
        idx = np.cumprod(1 + np.nan_to_num(np.nanmean(ret, axis=1)))
    sma = pd.Series(idx).rolling(momentum.MA_DAYS).mean().to_numpy()
    up = idx > sma
    held_until = np.full(m, -1)                                                   # index of the last session each stock is held through
    out, leaders_today = [], []
    for i in range(80, n):
        liquid = np.flatnonzero((C[i] * V[i] / 1e7 >= momentum.MIN_TURNOVER_CR) & ~np.isnan(atr[i]) & ~np.isnan(C[i - 20]) & ~np.isnan(C[i - 60]))
        if len(liquid) < 20:
            continue
        mv_order = liquid[np.argsort(-atr[i][liquid], kind="stable")][:movers.TOP_N]
        score = (_percentile_rank(C[i][liquid] / C[i - 20][liquid] - 1) + _percentile_rank(C[i][liquid] / C[i - 60][liquid] - 1)) / 2
        mom_order = liquid[np.argsort(-score, kind="stable")]
        mom_rank = {int(j): r + 1 for r, j in enumerate(mom_order)}
        leaders = [(rank + 1, int(j)) for rank, j in enumerate(mv_order) if mom_rank[int(j)] <= movers.LEADER_POOL]
        if i == n - 1:
            leaders_today = [(syms[j], rk) for rk, j in leaders]
        if not up[i] or i + 1 >= n:
            continue
        for mv_rank, j in leaders:
            if held_until[j] >= i + 1 or np.isnan(O[i + 1, j]):
                continue                                                          # still held from an earlier signal: no second position
            e = float(O[i + 1, j])
            if e > C[i, j] * (1 + momentum.GAP_CAP_PCT / 100):
                continue                                                          # gapped up more than the cap: the limit order does not fill
            stop = e * (1 - momentum.STOP_ATR * atr[i, j] / 100)
            end = i + hold
            xi, xp, why = None, None, None
            for k in range(i + 1, min(end, n - 1) + 1):
                if k > i + 1 and O[k, j] <= stop:
                    xi, xp, why = k, float(O[k, j]), "stop"
                    break
                if L[k, j] <= stop:
                    xi, xp, why = k, float(stop), "stop"
                    break
            if xi is None:
                if end <= n - 1:
                    xi, xp, why = end, float(C[end, j]), "time"
                else:
                    xi, xp, why = n - 1, float(C[n - 1, j]), "open"
            held_until[j] = xi if why != "open" else n + hold
            pct = (xp / e - 1) * 100 - (COST_PCT if why != "open" else 0)
            out.append({"symbol": syms[j], "sig": i, "ent": i + 1, "entry": e, "stop": float(stop), "ext": xi, "exit": xp, "out": OUT.index(why), "pct": float(pct),
                        "mv": mv_rank, "mom": mom_rank[j], "atr": float(atr[i, j]), "last": float(C[n - 1, j]), "sell_by": end})
    info = {"asof": dates[-1].strftime("%Y-%m-%d"), "market_up": bool(up[-1]), "leaders_today": leaders_today}
    return out, [d.strftime("%Y-%m-%d") for d in dates], info


def history(frames: dict[str, pd.DataFrame], fo: set[str]) -> dict:
    """The page's file (leader_trades.json): every trade as a compact row [symbol index, signal day, entry day, entry, stop, exit day, exit, out, net %, Movers rank,
    momentum rank, ATR%, last close] with days as days since 2000-01-01, plus `sell_by` day for each (the planned exit session) and today's leaders."""
    tr, dates, info = trades(frames, fo)
    day = lambda i: int((pd.Timestamp(dates[min(i, len(dates) - 1)]) - EPOCH).days)           # noqa: E731
    symbols, rows = [], []
    for t in tr:
        if t["symbol"] not in symbols:
            symbols.append(t["symbol"])
        # the planned exit session may lie in the future (a held trade): extend the calendar by weekdays
        sell = pd.Timestamp(dates[t["sig"]])
        left = momentum.HOLD_SESSIONS
        while left:
            sell += pd.Timedelta(days=1)
            left -= sell.weekday() < 5
        rows.append([symbols.index(t["symbol"]), day(t["sig"]), day(t["ent"]), round(t["entry"], 2), round(t["stop"], 2), day(t["ext"]), round(t["exit"], 2), t["out"], round(t["pct"], 2),
                     t["mv"], t["mom"], round(t["atr"], 1), round(t["last"], 2), int((sell - EPOCH).days)])
    rows.sort(key=lambda r: (r[2], r[9]))
    return {"cols": COLS + ["sell"], "symbols": symbols, "rows": rows, **info, "hold": momentum.HOLD_SESSIONS, "stop_atr": momentum.STOP_ATR}
