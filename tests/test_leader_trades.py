"""High-range leader trades: one position per stock, the Momentum strategy's entry / stop / exit rules, the compact file and the page section."""
import json
from pathlib import Path

import numpy as np
import pandas as pd

from signals import leader_trades as lt
from signals import momentum


def _universe(seed=1, stocks=40, days=360):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2024-01-01", periods=days)
    frames = {}
    for k in range(stocks):
        drift = rng.normal(0.0008, 0.0006)
        vol = 0.012 + 0.012 * (k % 7) / 6                                  # different daily ranges, so some stocks are the widest
        ret = rng.normal(drift, vol, days)
        c = 100 * np.cumprod(1 + ret)
        o = np.r_[c[0], c[:-1]] * (1 + rng.normal(0, 0.004, days))
        h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, vol / 2, days)))
        l = np.minimum(o, c) * (1 - np.abs(rng.normal(0, vol / 2, days)))
        frames[f"S{k:02d}"] = pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": np.full(days, 5e6)}, index=idx)
    return frames


def test_trades_follow_the_strategy_rules_and_hold_one_position_per_stock():
    frames = _universe()
    tr, dates, info = lt.trades(frames, set(frames))
    assert tr and info["asof"] == dates[-1]
    last = {}
    for t in sorted(tr, key=lambda x: x["ent"]):
        f = frames[t["symbol"]]
        o, c = f["open"].to_numpy(), f["close"].to_numpy()
        i = [d.strftime("%Y-%m-%d") for d in f.index].index(dates[t["sig"]])
        assert t["ent"] == t["sig"] + 1 and t["entry"] <= c[i] * (1 + momentum.GAP_CAP_PCT / 100) + 1e-9           # the next open, not above the gap cap
        assert abs(t["stop"] - t["entry"] * (1 - momentum.STOP_ATR * t["atr"] / 100)) < 1e-6 < t["entry"]
        assert t["ext"] <= t["sig"] + momentum.HOLD_SESSIONS and t["ext"] >= t["ent"]                           # sold by the close of the 20th session
        if t["out"] == 1:
            assert t["ext"] == t["sig"] + momentum.HOLD_SESSIONS and abs(t["exit"] - c[i + momentum.HOLD_SESSIONS]) < 1e-9
        if t["out"] == 0:
            assert t["exit"] <= t["stop"] + 1e-9 or t["exit"] == o[i + 1 + (t["ext"] - t["ent"])]
        assert t["ent"] > last.get(t["symbol"], -1), "a stock is not bought again while it is still held"      # one position per stock
        last[t["symbol"]] = t["ext"]
        assert 1 <= t["mv"] <= 10 and t["mom"] <= 30


def test_the_file_has_compact_rows_and_a_sell_date_for_every_trade():
    frames = _universe(seed=2)
    h = lt.history(frames, set(frames))
    json.dumps(h)
    assert h["cols"] == lt.COLS + ["sell"] and h["hold"] == momentum.HOLD_SESSIONS and h["rows"]
    c = {k: i for i, k in enumerate(h["cols"])}
    for r in h["rows"]:
        assert r[c["ent"]] > r[c["sig"]] and r[c["ext"]] >= r[c["ent"]] and r[c["sell"]] > r[c["sig"]] and r[c["stop"]] < r[c["entry"]] and r[c["out"]] in (0, 1, 2)
        assert (r[c["sell"]] - r[c["sig"]]) >= momentum.HOLD_SESSIONS                                         # 20 weekdays after the signal is at least 20 calendar days
    assert h["rows"] == sorted(h["rows"], key=lambda r: (r[c["ent"]], r[c["mv"]]))


def test_no_new_entries_while_the_market_is_below_its_average():
    frames = _universe(seed=3, days=330)
    for f in frames.values():                                                                                   # a market that falls the whole way: the gate stays closed
        f[["open", "high", "low", "close"]] = f[["open", "high", "low", "close"]].to_numpy() * np.linspace(1, 0.4, len(f))[:, None]
    tr, _, info = lt.trades(frames, set(frames))
    assert info["market_up"] is False and not [t for t in tr if t["sig"] > 150]


def test_the_page_has_the_leader_trades_section():
    root = Path(__file__).resolve().parent.parent
    html = (root / "docs" / "index.html").read_text()
    for needle in ('id="leaderTr"', 'id="ltViewNow"', "Holding now", 'id="ltViewHist"', "function renderLeaderTrades", "function ltChart", 'liveJson("leader_trades.json", "dashboard-setups")', '"leader"', "leader_hist/c/"):
        assert needle in html, needle
    assert "leader_trades" in (root / "scripts" / "build_dashboard_setups.py").read_text()


def test_the_break_entry_buys_only_above_the_signal_high_within_the_window():
    frames = _universe(seed=5)
    tr, dates, _ = lt.trades(frames, set(frames), entry="break", window=2)
    assert tr
    for t in tr:
        f = frames[t["symbol"]]
        i = [d.strftime("%Y-%m-%d") for d in f.index].index(dates[t["sig"]])
        high = f["high"].to_numpy()
        assert t["sig"] < t["ent"] <= t["sig"] + 2 and t["entry"] >= high[i] - 1e-9          # filled at the high, or at the open on a gap over it
        assert abs(t["stop"] - t["entry"] * (1 - momentum.STOP_ATR * t["atr"] / 100)) < 1e-6 and t["ext"] <= t["sig"] + momentum.HOLD_SESSIONS
    one = lt.trades(frames, set(frames), entry="break", window=1)[0]
    assert all(t["ent"] == t["sig"] + 1 for t in one) and len(one) <= len(tr) + len(one)
    anchored, _, _ = lt.trades(frames, set(frames), entry="break", window=3, anchor="entry")
    assert anchored and all(t["ext"] <= t["ent"] + momentum.HOLD_SESSIONS - 1 for t in anchored)


def test_todays_leaders_carry_close_and_atr_for_the_position_size_calculator():
    frames = _universe(seed=8)
    h = lt.history(frames, set(frames))
    assert [(t["s"], t["mv"]) for t in h["today"]] == [tuple(x) for x in h["leaders_today"]] or h["today"] == []
    for t in h["today"]:
        f = frames[t["s"]]
        assert abs(t["close"] - round(float(f["close"].iloc[-1]), 2)) < 1e-9 and 0 < t["atr"] < 40 and 1 <= t["mv"] <= 10
    json.dumps(h)


def test_the_page_has_the_position_size_calculator():
    html = (Path(__file__).resolve().parent.parent / "docs" / "index.html").read_text()
    for needle in ('id="ltCap"', 'id="ltRisk"', 'id="ltNewRows"', "function ltQty", "ltCap", "d.today"):
        assert needle in html, needle


def test_the_page_opens_on_the_today_view_with_buys_only():
    html = (Path(__file__).resolve().parent.parent / "docs" / "index.html").read_text()
    for needle in ('id="modeToday"', 'id="todayView"', "function renderToday", "function enterToday", "  enterToday();\n  setInterval(refresh, REFRESH_MS);", 'id="tdBuyRows"', 'id="tdHoldRows"', "todaySkip"):
        assert needle in html, needle
    today = html[html.index("function renderToday"):html.index("function enterSwing")]
    assert "short" not in today.lower() and "sell below" not in today.lower()          # buys only: no bearish rows in the Today view
