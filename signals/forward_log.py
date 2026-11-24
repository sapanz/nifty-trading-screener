"""Forward log - a frozen record of the signals the strategies gave as they happened, and what became of them.

Every build appends the day's signals (the high-range leaders and the F&O-futures SMA preset) with the plan as it stood that evening; a logged signal is never changed or
removed. Later builds only fill in what the real candles did afterwards (entry, stop, exit) by the same rules as the backtests, and a closed trade is never touched again.
`update` re-resolves nothing that is closed. The ledger is read back from its own data branch each build; a build that cannot read it leaves the branch alone instead
of starting a new ledger.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

import numpy as np
import pandas as pd

from signals import momentum
from signals import sma_support as ss

logger = logging.getLogger(__name__)

BRANCH = "dashboard-forward"
FILE = "forward_log.json"
EXTRA_COST_PCT = 0.30            # on top of the strategies' own 0.10%: statutory charges, brokerage and slippage on a delivery round trip (0.40% in all)
STRATEGIES = {"L": "High-range leaders", "S": "SMA support, F&O futures"}


class LedgerUnavailable(RuntimeError):
    """The saved ledger could not be read (not 'there is none yet'): the build must not publish a new one over it."""


# ------------------------------------------------------------------ the ledger file
def _loads(raw: bytes):
    from signals import secure                                                      # decrypts when the dashboard has a password
    return secure.loads(raw)


def load(repository: str | None, token: str | None, retries: int = 3) -> dict:
    """The saved ledger from its branch ({} entries when the branch / file does not exist yet). Raises LedgerUnavailable on any other failure."""
    empty = {"entries": []}
    if not repository:
        return empty
    url = f"https://api.github.com/repos/{repository}/contents/{FILE}?ref={BRANCH}"
    headers = {"Accept": "application/vnd.github.raw", "User-Agent": "forward-log"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    last: Exception | None = None
    for _ in range(retries):
        try:
            data = _loads(urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30).read())
            if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
                raise ValueError("unexpected ledger shape")
            return data
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return empty
            last = exc
        except Exception as exc:  # noqa: BLE001
            last = exc
    raise LedgerUnavailable(str(last))


# ------------------------------------------------------------------ new signals
def _atr_pct(df: pd.DataFrame, i: int) -> float:
    h, l, c = (df[k].to_numpy(float) for k in ("high", "low", "close"))
    tr = np.maximum(h[1:] - l[1:], np.maximum(np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])))              # tr[k - 1] is session k
    return float(tr[i - momentum.ATR_DAYS:i].mean() / c[i] * 100)


def leader_signals(lead: dict, frames: dict[str, pd.DataFrame]) -> list[dict]:
    """Today's leaders (from leader_trades.history) as ledger entries - only while the market is above its average, as the live rule."""
    if not lead.get("market_up"):
        return []
    out = []
    for sym, rank in lead.get("leaders_today", []):
        df = frames.get(sym)
        if df is None or len(df) <= momentum.ATR_DAYS + 1 or pd.DatetimeIndex(df.index).normalize()[-1].strftime("%Y-%m-%d") != lead["asof"]:
            continue
        i = len(df) - 1
        out.append({"strat": "L", "sym": sym, "sig": lead["asof"], "rank": int(rank), "close": round(float(df["close"].iloc[i]), 2), "atr": round(_atr_pct(df, i), 2),
                    "plan": {"entry": "next open", "gap_cap": momentum.GAP_CAP_PCT, "stop_atr": momentum.STOP_ATR, "hold": momentum.HOLD_SESSIONS}})
    return out


def sma_signals(sma: dict, asof: str) -> list[dict]:
    """The F&O-futures preset's signals of today's candle: daily, long, sector support, default volume, clear uptrend, F&O stock."""
    vol = (sma.get("defaults") or {}).get("D", 1.5)
    out = []
    for r in sma.get("rows", []):
        if r["tf"] == "D" and r.get("side", "long") == "long" and r["fo"] and r["sector_ok"] and r["vr"] is not None and r["vr"] >= vol and r["signal_date"] == asof and r["age"] == 0 \
                and r.get("trend") is not None and r["trend"] >= sma.get("clear_percentile", ss.CLEAR_PERCENTILE):
            out.append({"strat": "S", "sym": r["symbol"], "sig": r["signal_date"], "trend": r["trend"], "vr": r["vr"], "close": r["price"],
                        "plan": {"entry": r["entry"], "stop": r["stop"], "target": r["target"], "rr": r["rr"], "risk_pct": r["risk_pct"]}})
    return out


def add(ledger: dict, signals: list[dict], now_iso: str) -> int:
    """Append the signals that are not in the ledger yet (an id is strategy, stock and signal day); existing entries are never replaced. Returns how many were added."""
    have = {e["id"] for e in ledger["entries"]}
    n = 0
    for s in signals:
        eid = f'{s["strat"]}|{s["sym"]}|{s["sig"]}'
        if eid in have:
            continue
        ledger["entries"].append({"id": eid, **s, "logged": now_iso, "st": "pending"})
        have.add(eid)
        n += 1
    return n


# ------------------------------------------------------------------ resolving
def _resolve_leader(e: dict, df: pd.DataFrame, held_until: int) -> tuple[dict, int]:
    """(fields to set, the session index the stock is held through) by the leader trades' rules, from the candles after the signal."""
    dates = pd.DatetimeIndex(df.index).normalize()
    i = int(dates.get_indexer([pd.Timestamp(e["sig"])])[0])
    n = len(df)
    if i < 0 or i + 1 >= n:
        return {}, held_until                                                       # the next session has not happened yet
    O, L, C = (df[k].to_numpy(float) for k in ("open", "low", "close"))
    gap_cap, stop_atr, hold = e["plan"]["gap_cap"], e["plan"]["stop_atr"], e["plan"]["hold"]
    ent = float(O[i + 1])
    if ent > e["close"] * (1 + gap_cap / 100):
        return {"st": "skipped", "why": "gap"}, held_until                          # gapped up past the cap: the limit order does not fill
    if held_until >= i + 1:
        return {"st": "skipped", "why": "held"}, held_until                         # still held from an earlier signal: no second position
    stop = ent * (1 - stop_atr * e["atr"] / 100)
    end = i + hold
    xi = xp = why = None
    for k in range(i + 1, min(end, n - 1) + 1):
        if k > i + 1 and O[k] <= stop:
            xi, xp, why = k, float(O[k]), "stop"
            break
        if L[k] <= stop:
            xi, xp, why = k, float(stop), "stop"
            break
    if xi is None:
        xi, xp, why = (end, float(C[end]), "time") if end <= n - 1 else (n - 1, float(C[n - 1]), "open")
    base = {"ent": dates[i + 1].strftime("%Y-%m-%d"), "entry": round(ent, 2), "stop": round(stop, 2)}
    if why == "open":
        return {**base, "st": "open", "last": round(xp, 2), "pct": round((xp / ent - 1) * 100, 2)}, n + hold
    pct = (xp / ent - 1) * 100 - 0.10                                               # the strategy's own cost, as in the backtests
    return {**base, "st": "closed", "ext": dates[xi].strftime("%Y-%m-%d"), "exit": round(xp, 2), "why": why, "pct": round(pct, 2)}, xi


def _resolve_sma(e: dict, df: pd.DataFrame, held_until: int) -> tuple[dict, int]:
    dates = pd.DatetimeIndex(df.index).normalize()
    i = int(dates.get_indexer([pd.Timestamp(e["sig"])])[0])
    n = len(df)
    if i < 0 or i + 1 >= n:
        return {}, held_until
    p = e["plan"]
    sig = {"i": i, "tf": "D", "date": dates[i], "entry": p["entry"], "stop": p["stop"], "target": p["target"], "side": "long"}
    tr = ss.outcome(df, sig)
    if tr is None:
        return {"st": "expired", "why": "not triggered"}, held_until                # the next session never traded above the signal high
    if held_until >= tr["entry_i"]:
        return {"st": "skipped", "why": "held"}, held_until
    pct, r = ss.pct_and_r(tr, sig)
    base = {"ent": dates[tr["entry_i"]].strftime("%Y-%m-%d"), "entry": round(tr["entry"], 2), "stop": round(p["stop"], 2)}
    if tr["reason"] == "open":
        return {**base, "st": "open", "last": round(float(df["close"].iloc[-1]), 2), "pct": round((float(df["close"].iloc[-1]) / tr["entry"] - 1) * 100, 2)}, n + 20
    return {**base, "st": "closed", "ext": dates[tr["exit_i"]].strftime("%Y-%m-%d"), "exit": round(tr["exit"], 2), "why": tr["reason"], "pct": round(pct, 2)}, tr["exit_i"]


def update(ledger: dict, frames: dict[str, pd.DataFrame]) -> None:
    """Fill in what the candles have done to every entry that is not final yet. Closed, skipped and expired entries are never touched again."""
    held: dict[tuple[str, str], int] = {}
    for e in sorted(ledger["entries"], key=lambda x: (x["sig"], x["id"])):
        df = frames.get(e["sym"])
        key = (e["strat"], e["sym"])
        if e["st"] in ("closed", "skipped", "expired"):                            # final: only remember how long the stock was held
            if e["st"] == "closed" and df is not None:
                dates = [d.strftime("%Y-%m-%d") for d in pd.DatetimeIndex(df.index).normalize()]
                if e["ext"] in dates:
                    held[key] = max(held.get(key, -1), dates.index(e["ext"]))
            continue
        if df is None:
            continue
        fn = _resolve_leader if e["strat"] == "L" else _resolve_sma
        fields, held[key] = fn(e, df, held.get(key, -1))
        e.update(fields)


# ------------------------------------------------------------------ the numbers the page shows
def _r_multiple(e: dict) -> float | None:
    risk = (e["entry"] - e["stop"]) / e["entry"] * 100
    return None if risk <= 0 else (e["pct"] - EXTRA_COST_PCT) / risk


# The thresholds the verdict uses - fixed in advance (not tuned to the results): the backtest's planning figure after costs was about +1.5% to +2.5% a trade.
MIN_CLOSED, EARLY_CLOSED = 40, 15      # closed trades needed for a verdict / for an early warning
MIN_AVG, WARN_AVG = 1.0, 0.5           # average net % a trade: on track at or above MIN_AVG, warning below WARN_AVG
MIN_PF, WARN_PF = 1.3, 1.2             # profit factor
WARN_DD = -15.0                        # worst drawdown at 1% of capital risked per trade, % of capital


def verdict(closed: int, avg_net: float | None, pf: float | None, dd: float) -> dict:
    """'Should I rely on it yet?': early (too few closed trades), ontrack, watch (between the limits) or warning - with the checks behind it."""
    checks = [{"name": "Closed trades", "value": f"{closed} of {MIN_CLOSED}", "ok": bool(closed >= MIN_CLOSED)},
              {"name": "Average net a trade", "value": "—" if avg_net is None else f"{avg_net:+.2f}%", "ok": bool(avg_net is not None and avg_net >= MIN_AVG), "need": f"at least {MIN_AVG:+.1f}%"},
              {"name": "Profit factor", "value": "no loss yet" if pf is None and closed else "—" if pf is None else f"{pf:.2f}", "ok": bool(closed > 0 and (pf is None or pf >= MIN_PF)), "need": f"at least {MIN_PF}"},
              {"name": "Worst drawdown at 1% risk", "value": f"{dd:.1f}%", "ok": bool(dd > WARN_DD), "need": f"better than {WARN_DD:.0f}%"}]
    bad = avg_net is not None and (avg_net < WARN_AVG or (pf is not None and pf < WARN_PF) or dd <= WARN_DD)
    if closed < EARLY_CLOSED:
        level, text = "early", f"Too early to tell - {closed} of {MIN_CLOSED} closed trades"
    elif closed < MIN_CLOSED:
        level = "warning" if avg_net is not None and (avg_net < 0 or dd <= WARN_DD) else "early"
        text = "Early warning - results are poor so far (" + f"{closed} of {MIN_CLOSED} closed trades)" if level == "warning" else f"Still collecting - {closed} of {MIN_CLOSED} closed trades"
    elif bad:
        level, text = "warning", "Warning - below the limits it needed to meet; halve the size or pause"
    elif avg_net >= MIN_AVG and (pf is None or pf >= MIN_PF):
        level, text = "ontrack", "On track - in line with what the backtest led to expect after costs"
    else:
        level, text = "watch", "Watch - positive but below the on-track limits"
    return {"level": level, "text": text, "checks": checks}


def summary(ledger: dict, nifty: pd.Series | None = None) -> dict:
    """Per strategy and for both: counts, win rate, average net % (after EXTRA_COST_PCT), profit factor, and the equity curve at 1% of capital risked per trade (R multiples
    booked on the exit day, so the drawdown counts closed trades only); plus the Nifty 50 move since the first signal."""
    out = {"strategies": {}}
    entries = ledger["entries"]
    first = min((e["sig"] for e in entries), default=None)
    groups = {"L": [e for e in entries if e["strat"] == "L"], "S": [e for e in entries if e["strat"] == "S"], "all": entries}
    for key, group in groups.items():
        closed = sorted((e for e in group if e["st"] == "closed"), key=lambda x: (x["ext"], x["id"]))
        net = np.array([e["pct"] - EXTRA_COST_PCT for e in closed], float)
        rs = np.array([_r_multiple(e) or 0.0 for e in closed], float)
        gain, loss = net[net > 0].sum(), -net[net <= 0].sum()
        curve, total, peak, dd = [], 0.0, 0.0, 0.0
        for e, r in zip(closed, rs):
            total += r
            peak = max(peak, total)
            dd = min(dd, total - peak)
            curve.append([e["ext"], round(total, 2)])
        out["strategies"][key] = {"logged": len(group), "pending": sum(e["st"] == "pending" for e in group), "open": sum(e["st"] == "open" for e in group), "closed": len(closed),
                                  "skipped": sum(e["st"] in ("skipped", "expired") for e in group), "win": round(float((net > 0).mean() * 100), 1) if len(net) else None,
                                  "avg_net": round(float(net.mean()), 2) if len(net) else None, "pf": round(float(gain / loss), 2) if loss else None,
                                  "avg_r": round(float(rs.mean()), 2) if len(rs) else None, "equity_1pct": round(total, 2), "max_dd_1pct": round(dd, 2), "curve": curve}
        st = out["strategies"][key]
        st["verdict"] = verdict(st["closed"], st["avg_net"], st["pf"], st["max_dd_1pct"])
    out["since"] = first
    if nifty is not None and first is not None:
        nifty = nifty.copy()
        nifty.index = pd.DatetimeIndex(nifty.index).tz_localize(None).normalize()
        s = nifty[nifty.index >= pd.Timestamp(first)]
        out["nifty_pct"] = round(float((s.iloc[-1] / s.iloc[0] - 1) * 100), 2) if len(s) > 1 else None
    return out


def payload(ledger: dict, asof: str, now_iso: str, nifty: pd.Series | None = None) -> dict:
    """The file the page reads: the entries (compact, newest first) and the summary."""
    entries = sorted(ledger["entries"], key=lambda e: (e["sig"], e["id"]), reverse=True)
    return {"version": 1, "asof": asof, "updated_at": now_iso, "extra_cost_pct": EXTRA_COST_PCT, "names": STRATEGIES, "entries": entries, "summary": summary(ledger, nifty)}


def dumps(obj) -> str:
    return json.dumps(obj, separators=(",", ":"))
