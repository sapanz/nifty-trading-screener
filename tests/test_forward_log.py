"""Forward log: signals are frozen, outcomes are filled in by the backtests' own rules, closed trades never change, an unreadable ledger is never replaced."""
import copy
import json
import urllib.error
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from signals import forward_log as fl
from signals import leader_trades as lt
from signals import momentum


def _universe(seed=1, stocks=40, days=360):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2024-01-01", periods=days)
    frames = {}
    for k in range(stocks):
        vol = 0.012 + 0.012 * (k % 7) / 6
        c = 100 * np.cumprod(1 + rng.normal(rng.normal(0.0008, 0.0006), vol, days))
        o = np.r_[c[0], c[:-1]] * (1 + rng.normal(0, 0.004, days))
        h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, vol / 2, days)))
        l = np.minimum(o, c) * (1 - np.abs(rng.normal(0, vol / 2, days)))
        frames[f"S{k:02d}"] = pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": np.full(days, 5e6)}, index=idx)
    return frames


def _ledger_from_trades(frames):
    tr, dates, _ = lt.trades(frames, set(frames))
    led = {"entries": []}
    for t in tr:
        f = frames[t["symbol"]]
        i = [d.strftime("%Y-%m-%d") for d in f.index].index(dates[t["sig"]])
        led["entries"].append({"id": f"L|{t['symbol']}|{dates[t['sig']]}", "strat": "L", "sym": t["symbol"], "sig": dates[t["sig"]], "rank": t["mv"], "close": float(f["close"].iloc[i]), "atr": t["atr"],
                               "plan": {"entry": "next open", "gap_cap": momentum.GAP_CAP_PCT, "stop_atr": momentum.STOP_ATR, "hold": momentum.HOLD_SESSIONS}, "logged": "x", "st": "pending"})
    return tr, dates, led


def test_a_logged_leader_resolves_exactly_as_the_backtest_does():
    frames = _universe()
    tr, dates, led = _ledger_from_trades(frames)
    fl.update(led, frames)
    by = {e["id"]: e for e in led["entries"]}
    assert tr
    for t in tr:
        e = by[f"L|{t['symbol']}|{dates[t['sig']]}"]
        if t["out"] == 2:
            assert e["st"] == "open"
            continue
        assert e["st"] == "closed" and e["ent"] == dates[t["ent"]] and e["ext"] == dates[t["ext"]]
        assert abs(e["entry"] - t["entry"]) < 0.006 and abs(e["exit"] - t["exit"]) < 0.006 and abs(e["pct"] - t["pct"]) < 0.02 and e["why"] == lt.OUT[t["out"]]


def test_the_signal_the_log_records_matches_the_trades_atr():
    frames = _universe(seed=4)
    h = lt.history(frames, set(frames))
    sigs = fl.leader_signals(h, frames)
    if h["market_up"]:
        assert sigs and all(s["sig"] == h["asof"] for s in sigs)
    tr, dates, _ = lt.trades({k: v.iloc[:-3] for k, v in frames.items()}, set(frames))            # the ATR the log stores is the one the trade rule uses
    sym = tr[-1]["symbol"]
    df = frames[sym].iloc[:-3]
    assert abs(fl._atr_pct(df, [d.strftime("%Y-%m-%d") for d in df.index].index(dates[tr[-1]["sig"]])) - tr[-1]["atr"]) < 1e-6


def test_a_second_signal_while_the_stock_is_held_is_skipped_and_a_gap_is_skipped():
    frames = _universe(seed=5)
    tr, dates, led = _ledger_from_trades(frames)
    t = tr[0]
    f = frames[t["symbol"]]
    later = [d.strftime("%Y-%m-%d") for d in f.index][t["sig"] + 2]
    dup = copy.deepcopy(led["entries"][0]); dup.update({"id": f"L|{t['symbol']}|{later}", "sig": later, "close": float(f["close"].iloc[t["sig"] + 2])})
    led["entries"] = [led["entries"][0], dup]
    fl.update(led, frames)
    assert led["entries"][1]["st"] == "skipped" and led["entries"][1]["why"] == "held"
    gap = copy.deepcopy(led["entries"][0]); gap.update({"id": "G", "sym": t["symbol"], "sig": dates[t["sig"]], "close": float(f["open"].iloc[t["sig"] + 1]) / 1.05, "st": "pending"})
    led2 = {"entries": [gap]}
    fl.update(led2, frames)
    assert led2["entries"][0]["st"] == "skipped" and led2["entries"][0]["why"] == "gap"


def test_closed_entries_never_change_and_the_ledger_only_grows():
    frames = _universe(seed=6)
    _, _, led = _ledger_from_trades(frames)
    fl.update(led, frames)
    snap = copy.deepcopy(led)
    shorter = {k: v.iloc[:-30] for k, v in frames.items()}                          # even re-running against different candles leaves final entries alone
    fl.update(led, shorter)
    for a, b in zip(sorted(snap["entries"], key=lambda e: e["id"]), sorted(led["entries"], key=lambda e: e["id"])):
        if a["st"] == "closed":
            assert a == b
    n = len(led["entries"])
    sigs = [{"strat": "L", "sym": e["sym"], "sig": e["sig"], "close": 1.0, "atr": 1.0, "plan": e["plan"]} for e in led["entries"][:5]] + [{"strat": "L", "sym": "NEW", "sig": "2030-01-01", "close": 1.0, "atr": 1.0, "plan": led["entries"][0]["plan"]}]
    assert fl.add(led, sigs, "now") == 1 and len(led["entries"]) == n + 1
    assert [e for e in led["entries"] if e["id"] == led["entries"][0]["id"]][0]["logged"] == "x"      # an existing entry keeps its original record


def test_pending_until_the_next_session_exists():
    frames = _universe(seed=7)
    sym = "S03"
    df = frames[sym]
    e = {"id": "L|S03|d", "strat": "L", "sym": sym, "sig": df.index[-1].strftime("%Y-%m-%d"), "close": float(df["close"].iloc[-1]), "atr": 3.0,
         "plan": {"entry": "next open", "gap_cap": 3.0, "stop_atr": 4.0, "hold": 20}, "logged": "x", "st": "pending"}
    led = {"entries": [e]}
    fl.update(led, frames)
    assert led["entries"][0]["st"] == "pending" and "entry" not in led["entries"][0]


def _sma_frame():
    idx = pd.bdate_range("2024-01-01", periods=120)
    c = np.linspace(100, 130, 120)
    return pd.DataFrame({"open": c, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": 1e6}, index=idx)


def test_sma_entries_trigger_expire_and_close_by_the_backtest_rules():
    df = _sma_frame()
    i = 100
    sig = df.index[i].strftime("%Y-%m-%d")
    hi, lo = float(df["high"].iloc[i]), float(df["low"].iloc[i])
    plan = {"entry": round(hi, 2), "stop": round(lo, 2), "target": round(hi * 1.1, 2), "rr": 2.0, "risk_pct": 2.0}
    base = {"strat": "S", "sym": "X", "sig": sig, "trend": 90, "vr": 2.0, "close": 100.0, "plan": plan, "logged": "x", "st": "pending"}
    up = {"id": "S|X|1", **base}
    led = {"entries": [up]}
    fl.update(led, {"X": df})
    assert led["entries"][0]["st"] in ("open", "closed") and led["entries"][0]["entry"] >= plan["entry"] - 0.01        # a rising stock trades through the buy-stop
    flat = df.copy()
    flat.iloc[i + 1:, flat.columns.get_loc("high")] = hi * 0.99
    flat.iloc[i + 1:, flat.columns.get_loc("open")] = hi * 0.98
    flat.iloc[i + 1:, flat.columns.get_loc("close")] = hi * 0.98
    flat.iloc[i + 1:, flat.columns.get_loc("low")] = hi * 0.97
    led2 = {"entries": [{"id": "S|X|2", **base}]}
    fl.update(led2, {"X": flat})
    assert led2["entries"][0]["st"] == "expired"                                    # never traded above the signal high


def test_summary_counts_win_rate_equity_and_costs():
    led = {"entries": [
        {"id": "a", "strat": "L", "sym": "A", "sig": "2026-01-01", "st": "closed", "ext": "2026-01-20", "entry": 100.0, "stop": 80.0, "pct": 10.0},
        {"id": "b", "strat": "L", "sym": "B", "sig": "2026-01-02", "st": "closed", "ext": "2026-01-22", "entry": 100.0, "stop": 80.0, "pct": -20.0},
        {"id": "c", "strat": "S", "sym": "C", "sig": "2026-01-03", "st": "open", "entry": 50.0, "stop": 45.0, "pct": 1.0},
        {"id": "d", "strat": "S", "sym": "D", "sig": "2026-01-04", "st": "pending"},
        {"id": "e", "strat": "S", "sym": "E", "sig": "2026-01-04", "st": "expired"}]}
    nifty = pd.Series([100.0, 105.0], index=pd.to_datetime(["2026-01-01", "2026-01-30"]))
    s = fl.summary(led, nifty)
    L = s["strategies"]["L"]
    assert L["closed"] == 2 and L["win"] == 50.0 and L["logged"] == 2
    assert abs(L["avg_net"] - ((10 - fl.EXTRA_COST_PCT) + (-20 - fl.EXTRA_COST_PCT)) / 2) < 0.01
    assert abs(L["equity_1pct"] - ((10 - fl.EXTRA_COST_PCT) / 20 + (-20 - fl.EXTRA_COST_PCT) / 20)) < 0.02 and L["max_dd_1pct"] < 0
    A = s["strategies"]["all"]
    assert A["open"] == 1 and A["pending"] == 1 and A["skipped"] == 1 and s["nifty_pct"] == 5.0
    json.dumps(fl.payload(led, "2026-01-30", "now", nifty))


def test_an_unreadable_ledger_raises_but_a_missing_one_starts_empty(monkeypatch):
    def not_found(*a, **k):
        raise urllib.error.HTTPError("u", 404, "nf", {}, None)

    def boom(*a, **k):
        raise urllib.error.URLError("down")
    monkeypatch.setattr(fl.urllib.request, "urlopen", not_found)
    assert fl.load("o/r", "t") == {"entries": []}
    monkeypatch.setattr(fl.urllib.request, "urlopen", boom)
    with pytest.raises(fl.LedgerUnavailable):
        fl.load("o/r", "t", retries=2)
    assert fl.load(None, None) == {"entries": []}


def test_sma_signals_are_only_todays_fo_clear_uptrend_candles():
    row = {"symbol": "A", "fo": True, "tf": "D", "side": "long", "sector_ok": True, "vr": 2.0, "signal_date": "2026-11-23", "age": 0, "trend": 90, "price": 100.0, "entry": 101.0, "stop": 96.0, "target": 110.0, "rr": 1.8, "risk_pct": 5.0}
    sma = {"defaults": {"D": 1.5, "W": 1.2}, "clear_percentile": 80, "rows": [row, {**row, "symbol": "B", "fo": False}, {**row, "symbol": "C", "trend": 50}, {**row, "symbol": "D", "vr": 1.0},
                                                                              {**row, "symbol": "E", "age": 1}, {**row, "symbol": "F", "sector_ok": False}, {**row, "symbol": "G", "tf": "W"}]}
    assert [s["sym"] for s in fl.sma_signals(sma, "2026-11-23")] == ["A"]


def test_the_build_script_wires_the_log_without_overwriting_an_unreadable_ledger():
    root = Path(__file__).resolve().parent.parent
    build = (root / "scripts" / "build_dashboard_setups.py").read_text()
    assert "forward_log" in build and "LedgerUnavailable" in build and "dashboard-forward" in build.replace("forward_log.BRANCH", "dashboard-forward")


def test_the_page_has_the_forward_test_section():
    html = (Path(__file__).resolve().parent.parent / "docs" / "index.html").read_text()
    for needle in ('id="fwdLog"', "function renderForward", 'liveJson("forward_log.json", "dashboard-forward")', 'id="fwStatus"', 'name === "forward_log.json" ? "fwd"', "renderLeaderTrades(); renderForward();"):
        assert needle in html, needle
