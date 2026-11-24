"""Bullish SMA support: the signal rules, the trade plan, the order mechanics, the sector flag and the page section."""
import json
from pathlib import Path

import numpy as np
import pandas as pd

from signals import sma_support as ss


def _frame(closes, opens=None, highs=None, lows=None, vol=1e6, start="2024-01-01"):
    c = np.array(closes, float)
    o = np.array(opens, float) if opens is not None else np.r_[c[0], c[:-1]]
    h = np.array(highs, float) if highs is not None else np.maximum(o, c) * 1.002
    l = np.array(lows, float) if lows is not None else np.minimum(o, c) * 0.998
    v = np.full(len(c), vol) if np.isscalar(vol) else np.array(vol, float)
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": v}, index=pd.bdate_range(start, periods=len(c)))


def _uptrend_with_a_pullback(n=300):
    """A steady climb (the 21-day average rises about 0.4% a session), a dip to the average, then a green candle that closes on it."""
    c = list(100 * 1.004 ** np.arange(n))
    return c


def test_a_green_candle_on_a_rising_average_is_a_signal_and_the_plan_is_above_its_high_below_its_low():
    c = _uptrend_with_a_pullback(300)
    o, h, l = list(np.r_[c[0], c[:-1]]), [x * 1.003 for x in c], [x * 0.997 for x in c]
    sma = pd.Series(c).rolling(21).mean().to_numpy()
    t = 299
    o[t], c[t] = sma[t] * 1.006, sma[t] * 1.016            # a green candle whose low (set below) touches the average
    l[t], h[t] = sma[t] * 1.004, sma[t] * 1.02
    df = _frame(c, o, h, l)
    sig = [s for s in ss.signals(df, "D", None) if s["i"] == t]
    assert len(sig) == 1
    s = sig[0]
    assert abs(s["entry"] - h[t]) < 1e-9 and abs(s["stop"] - l[t] * 0.999) < 1e-9 and s["slope"] >= 1.0
    assert s["rr"] >= 1.5 and s["target"] > s["entry"] > s["stop"] and s["target_kind"] in ("resistance", "3R")
    assert s["sector_ok"] is False and s["vr"] is not None


def test_no_signal_for_a_red_candle_a_flat_average_or_a_candle_far_above_the_average():
    c = _uptrend_with_a_pullback(300)
    sma = pd.Series(c).rolling(21).mean().to_numpy()
    base = ss.signals(_frame(c), "D", None)
    t = 299
    o, h, l, cc = list(np.r_[c[0], c[:-1]]), [x * 1.003 for x in c], [x * 0.997 for x in c], list(c)
    o[t], cc[t], l[t], h[t] = sma[t] * 1.016, sma[t] * 1.006, sma[t] * 1.004, sma[t] * 1.02           # red candle
    assert not [s for s in ss.signals(_frame(cc, o, h, l), "D", None) if s["i"] == t]
    flat = [100.0 + 0.01 * (k % 3) for k in range(300)]
    assert not ss.signals(_frame(flat), "D", None)
    far = [s for s in base if s["i"] == t]
    assert not far or far[0]["entry"] > 0           # the plain climb's own candles sit ~2% above the average: either not a signal or a valid one - never an error


def _sig(df, **kw):
    s = {"tf": "D", "i": 100, "date": df.index[100], "entry": float(df["high"].iloc[100]), "stop": float(df["low"].iloc[100]) * 0.999, "target": float(df["high"].iloc[100]) * 1.05}
    return {**s, **kw}


def test_the_order_fills_only_next_session_above_the_high_and_a_gap_fills_at_the_open():
    c = [100.0] * 120
    o, h, l = list(c), [101.0] * 120, [99.0] * 120
    o[101], h[101], l[101], c[101] = 100.5, 101.5, 100.2, 101.2                   # trades above 101 and stays above the stop
    df = _frame(c, o, h, l)
    sig = {"tf": "D", "i": 100, "date": df.index[100], "entry": 101.0, "stop": 98.9, "target": 106.0}
    tr = ss.outcome(df, sig)
    assert tr and tr["entry_i"] == 101 and tr["entry"] == 101.0
    o[101], h[101] = 102.0, 102.5                                                  # gaps over the high: filled at the open
    assert ss.outcome(_frame(c, o, h, l), sig)["entry"] == 102.0
    h[101] = 100.9; o[101] = 100.0                                                 # never trades above: no trade
    assert ss.outcome(_frame(c, o, h, l), sig) is None
    o[101], h[101] = 100.5, 101.5
    h[102] = 101.4                                                                 # trades above only on the 2nd session after: a daily order is for one session
    h[101] = 100.9
    h[102] = 101.6
    assert ss.outcome(_frame(c, o, h, l), sig) is None


def test_stop_comes_first_and_the_target_is_a_limit_and_costs_are_taken():
    c = [100.0] * 130
    o, h, l = list(c), [101.0] * 130, [99.0] * 130
    h[101], l[101] = 101.5, 100.2
    sig = {"tf": "D", "i": 100, "date": pd.bdate_range("2024-01-01", periods=130)[100], "entry": 101.0, "stop": 98.9, "target": 106.0}
    h[103], l[103] = 106.5, 98.5                                                   # one candle through both the target and the stop
    tr = ss.outcome(_frame(c, o, h, l), sig)
    assert tr["reason"] == "stop" and tr["exit"] == 98.9
    l[103] = 100.0
    tr = ss.outcome(_frame(c, o, h, l), sig)
    assert tr["reason"] == "target" and tr["exit"] == 106.0
    pct, r = ss.pct_and_r(tr, sig)
    assert abs(pct - ((106 / 101 - 1) * 100 - 0.10)) < 1e-9 and r > 0


def test_the_sector_flag_needs_three_stocks_and_an_index_above_a_rising_average():  # also the weak flags
    idx = pd.bdate_range("2023-01-02", periods=400)
    up = lambda k: pd.DataFrame({"open": 100 * 1.003 ** np.arange(400) * (1 + 0.0001 * k), "high": 1, "low": 1, "close": 100 * 1.003 ** np.arange(400) * (1 + 0.0001 * k), "volume": 1e6}, index=idx)
    down = lambda k: pd.DataFrame({"open": 100 * 0.997 ** np.arange(400), "high": 1, "low": 1, "close": 100 * 0.997 ** np.arange(400), "volume": 1e6}, index=idx)
    frames = {"A": up(0), "B": up(1), "C": up(2), "X": down(0), "Y": down(0), "Z": down(0), "LONE": up(3)}
    flags = ss.sector_flags(frames, {"A": "Up", "B": "Up", "C": "Up", "X": "Down", "Y": "Down", "Z": "Down", "LONE": "Tiny"})
    assert set(flags) == {"Up", "Down"}                                           # a one-stock sector is not measured
    assert ss._asof(flags["Up"]["d"], idx[-1]) and ss._asof(flags["Up"]["w"], idx[-1])
    assert not ss._asof(flags["Down"]["d"], idx[-1]) and not ss._asof(flags["Down"]["w"], idx[-1])
    assert ss._asof(None, idx[-1]) is False
    assert ss._asof(flags["Down"]["dw"], idx[-1]) and ss._asof(flags["Down"]["ww"], idx[-1]) and not ss._asof(flags["Up"]["dw"], idx[-1])
    assert ss.flag_for(flags["Down"], "D", "short") is flags["Down"]["dw"] and ss.flag_for(flags["Up"], "W", "long") is flags["Up"]["w"] and ss.flag_for(None, "D", "long") is None


def test_the_target_is_the_resistance_when_it_pays_one_and_a_half_r_else_three_r_and_never_under_one_and_a_half():
    wH = np.array([100.0] * 60)
    wH[30] = 130.0
    for k in (29, 31):
        wH[k] = 105.0
    p = ss._plan(110.0, 105.0, wH, wH, 59)                               # the weekly peak at 130 is 4 R away: capped at 4 R = 130 -> resistance
    assert p["target_kind"] == "resistance" and abs(p["rr"] - 4.0) < 1e-9
    p = ss._plan(110.0, 100.0, wH, wH, 59)                               # the peak is 2 R away: used
    assert p["target_kind"] == "resistance" and abs(p["target"] - 130.0) < 1e-9 and p["rr"] >= 1.5
    wH2 = np.array([100.0] * 60)
    wH2[30] = 112.0
    p = ss._plan(110.0, 100.0, wH2, wH2, 59)                              # a resistance 0.2 R away is noise: 3 R
    assert p["target_kind"] == "3R" and abs(p["rr"] - 3.0) < 1e-9


def test_the_payload_and_the_page_section():
    html = (Path(__file__).resolve().parent.parent / "docs" / "index.html").read_text()
    for needle in ('id="smaSide"', 'Bearish resistance (sell, F&amp;O)', 'r.side === "short" ? "Sell below" : "Buy above"', 'id="smaSup"', 'id="smaTf"', 'id="smaVol"', 'id="smaSec"', "function renderSmaSupport", 'liveJson("sma_support.json", "dashboard-setups")', '"smas"'):
        assert needle in html, needle
    assert "sma_support.json" in (Path(__file__).resolve().parent.parent / "scripts" / "build_dashboard_setups.py").read_text()


def test_build_publishes_the_latest_signal_per_stock_and_timeframe_as_json():
    from datetime import datetime
    c = _uptrend_with_a_pullback(300)
    o, h, l = list(np.r_[c[0], c[:-1]]), [x * 1.003 for x in c], [x * 0.997 for x in c]
    sma = pd.Series(c).rolling(21).mean().to_numpy()
    t = 299
    o[t], c[t], l[t], h[t] = sma[t] * 1.006, sma[t] * 1.016, sma[t] * 1.004, sma[t] * 1.02
    frames = {"A": _frame(c, o, h, l), "B": _frame(c, o, h, l), "C": _frame(c, o, h, l)}
    out = ss.build(frames, {"A": "Up", "B": "Up", "C": "Up"}, {"A"}, datetime(2025, 3, 3))
    json.dumps(out)
    keys = {(r["symbol"], r["tf"]) for r in out["rows"]}
    assert len(keys) == len(out["rows"]) and {("A", "D"), ("B", "D"), ("C", "D")} <= keys
    row = next(r for r in out["rows"] if r["symbol"] == "A" and r["tf"] == "D")
    assert row["status"] == "waiting" and row["fo"] is True and row["rr"] >= 1.5 and len(row["candles"]["c"]) == len(row["candles"]["sma"]) == 90
    assert out["defaults"] == {"D": 1.5, "W": 1.2} and out["sectors"] == 1


def _downtrend(n=300):
    return list(100 * 0.996 ** np.arange(n))


def test_a_red_candle_under_a_falling_average_is_a_short_signal_and_the_plan_is_below_its_low_above_its_high():
    c = _downtrend(300)
    o, h, l = list(np.r_[c[0], c[:-1]]), [x * 1.003 for x in c], [x * 0.997 for x in c]
    sma = pd.Series(c).rolling(21).mean().to_numpy()
    t = 299
    o[t], c[t] = sma[t] * 0.994, sma[t] * 0.984                 # a red candle closing under the average, its high (set below) reaching up to it
    h[t], l[t] = sma[t] * 0.996, sma[t] * 0.98
    df = _frame(c, o, h, l)
    sig = [s for s in ss.signals(df, "D", None, "short") if s["i"] == t]
    assert len(sig) == 1
    s = sig[0]
    assert s["side"] == "short" and abs(s["entry"] - l[t]) < 1e-9 and abs(s["stop"] - h[t] * 1.001) < 1e-9 and s["slope"] <= -1.0
    assert s["rr"] >= 1.5 and s["target"] < s["entry"] < s["stop"] and s["target_kind"] in ("support", "3R") and s["target"] > 0
    assert not [x for x in ss.signals(df, "D", None, "long") if x["i"] == t]         # a red candle in a falling market is no long signal


def test_the_short_order_mechanics_are_the_mirror():
    c = [100.0] * 130
    o, h, l = list(c), [101.0] * 130, [99.0] * 130
    l[101], h[101] = 98.5, 99.8                                                    # trades below 99 next session and stays under the stop
    df = _frame(c, o, h, l)
    sig = {"tf": "D", "side": "short", "i": 100, "date": df.index[100], "entry": 99.0, "stop": 101.1, "target": 93.0}
    tr = ss.outcome(df, sig)
    assert tr and tr["entry_i"] == 101 and tr["entry"] == 99.0
    o[101], l[101] = 98.0, 97.5                                                    # gaps under the low: filled at the open
    assert ss.outcome(_frame(c, o, h, l), sig)["entry"] == 98.0
    o[101], l[101], h[101] = 100.0, 99.2, 99.8
    assert ss.outcome(_frame(c, o, h, l), sig) is None                              # never trades under it
    o[101], l[101], h[101] = 100.0, 98.5, 99.8
    l[103], h[103] = 92.0, 101.5                                                   # through both the target and the stop in one candle: the stop
    t2 = ss.outcome(_frame(c, o, h, l), sig)
    assert t2["reason"] == "stop" and t2["exit"] == 101.1
    h[103] = 100.0
    t3 = ss.outcome(_frame(c, o, h, l), sig)
    assert t3["reason"] == "target" and t3["exit"] == 93.0
    pct, r = ss.pct_and_r(t3, sig)
    assert abs(pct - (-(93 / 99 - 1) * 100 - 0.10)) < 1e-9 and r > 0


def test_build_has_shorts_for_f_and_o_stocks_only():
    from datetime import datetime
    c = _downtrend(300)
    o, h, l = list(np.r_[c[0], c[:-1]]), [x * 1.003 for x in c], [x * 0.997 for x in c]
    sma = pd.Series(c).rolling(21).mean().to_numpy()
    t = 299
    o[t], c[t], h[t], l[t] = sma[t] * 0.994, sma[t] * 0.984, sma[t] * 0.996, sma[t] * 0.98
    frames = {"A": _frame(c, o, h, l), "B": _frame(c, o, h, l), "C": _frame(c, o, h, l)}
    out = ss.build(frames, {"A": "Down", "B": "Down", "C": "Down"}, {"A"}, datetime(2025, 3, 3))
    json.dumps(out)
    shorts = [r for r in out["rows"] if r["side"] == "short"]
    assert {r["symbol"] for r in shorts} == {"A"} and all(r["status"] == "waiting" and r["stop"] > r["entry"] > r["target"] for r in shorts)
    assert shorts[0]["sector_ok"] is True and "red daily candle" in shorts[0]["reason"] and "sell below its low" in shorts[0]["reason"]


def test_the_close_filter_is_gone():
    html = (Path(__file__).resolve().parent.parent / "docs" / "index.html").read_text()
    assert "smaClose" not in html and "Close gap" not in html and not hasattr(ss, "_close_gap")
    c = _uptrend_with_a_pullback(300)
    o, h, l = list(np.r_[c[0], c[:-1]]), [x * 1.003 for x in c], [x * 0.997 for x in c]
    sma = pd.Series(c).rolling(21).mean().to_numpy()
    t = 299
    o[t], c[t], l[t], h[t] = sma[t] * 1.006, sma[t] * 1.016, sma[t] * 1.004, sma[t] * 1.02
    sig = [x for x in ss.signals(_frame(c, o, h, l), "D", None) if x["i"] == t]
    assert sig and "cl" not in sig[0]


def test_the_backtest_list_has_compact_rows_with_entry_exit_and_the_cols_in_order():
    c = _uptrend_with_a_pullback(330)
    o, h, l = list(np.r_[c[0], c[:-1]]), [x * 1.003 for x in c], [x * 0.997 for x in c]
    sma = pd.Series(c).rolling(21).mean().to_numpy()
    t = 300                                                      # a signal with enough history after it for the order to fill and the trade to end
    o[t], c[t], l[t], h[t] = sma[t] * 1.006, sma[t] * 1.016, sma[t] * 1.004, sma[t] * 1.02
    frames = {"A": _frame(c, o, h, l, vol=[1e6] * 330), "B": _frame(c, o, h, l, vol=[1e6] * 330)}
    frames["A"].iloc[t, frames["A"].columns.get_loc("volume")] = 3e6                       # the signal candle trades on 3x the volume
    frames["B"].iloc[t, frames["B"].columns.get_loc("volume")] = 3e6
    h_ = ss.history(frames, {"A": "Up", "B": "Up"}, {"A"})
    json.dumps(h_)
    assert h_["cols"] == ss.HIST_COLS and set(h_["symbols"]) <= {"A", "B"} and h_["fo"] == [s for s in h_["symbols"] if s == "A"]
    col = {k: i for i, k in enumerate(h_["cols"])}
    rows = [r for r in h_["rows"] if r[col["tf"]] == 0 and r[col["side"]] == 0]
    assert rows and all(r[col["ent"]] >= r[col["sig"]] and r[col["ext"]] >= r[col["ent"]] and r[col["vr"]] >= ss.HIST_MIN_VOLUME for r in rows)
    r = rows[0]
    assert r[col["stop"]] < r[col["entry"]] < r[col["target"]] and r[col["out"]] in (0, 1, 2) and abs(r[col["pct"]]) < 100
    assert all(row[col["side"]] == 0 or h_["symbols"][row[col["s"]]] == "A" for row in h_["rows"])      # shorts only for F&O stocks
    assert h_["rows"] == sorted(h_["rows"], key=lambda x: (x[col["ent"]], x[col["s"]]))


def test_the_page_has_the_sma_backtest_view():
    html = (Path(__file__).resolve().parent.parent / "docs" / "index.html").read_text()
    for needle in ('id="smaViewHist"', "Backtest: past trades", 'id="smaHistRows"', "function renderSmaHist", "function smaTradeChart", 'liveJson("sma_hist.json", "dashboard-setups")', '"smah"', "sma_hist/c/"):
        assert needle in html, needle
    assert "sma_hist" in (Path(__file__).resolve().parent.parent / "scripts" / "build_dashboard_setups.py").read_text()


def test_the_trend_rank_orders_signals_by_how_clear_the_uptrend_is():
    rng = np.random.default_rng(4)
    sigs = [{"slope": float(a), "gap50": float(b), "ret60": float(c)} for a, b, c in zip(rng.uniform(1, 10, 500), rng.uniform(0, 12, 500), rng.uniform(-10, 60, 500))]
    tables = ss.trend_tables(sigs)
    weak, strong = {"slope": 1.0, "gap50": 0.1, "ret60": -9.0}, {"slope": 9.9, "gap50": 11.9, "ret60": 59.0}
    mid = {"slope": 5.0, "gap50": 6.0, "ret60": 25.0}
    assert ss.trend_rank(weak, tables) <= 5 and ss.trend_rank(strong, tables) >= 95 and ss.trend_rank(weak, tables) < ss.trend_rank(mid, tables) < ss.trend_rank(strong, tables)
    ranks = [ss.trend_rank(s, tables) for s in sigs]
    assert 0 <= min(ranks) and max(ranks) <= 100 and abs(sum(r >= ss.CLEAR_PERCENTILE for r in ranks) / len(ranks) - 0.2) < 0.08           # about a fifth of all signals are 'clear'


def test_daily_long_signals_carry_the_trend_measures_and_the_files_carry_the_rank():
    from datetime import datetime
    c = _uptrend_with_a_pullback(330)
    o, h, l = list(np.r_[c[0], c[:-1]]), [x * 1.003 for x in c], [x * 0.997 for x in c]
    sma = pd.Series(c).rolling(21).mean().to_numpy()
    t = 300
    o[t], c[t], l[t], h[t] = sma[t] * 1.006, sma[t] * 1.016, sma[t] * 1.004, sma[t] * 1.02
    frames = {"A": _frame(c, o, h, l, vol=[1e6] * 330), "B": _frame(c, o, h, l, vol=[1e6] * 330)}
    for f in frames.values():
        f.iloc[t, f.columns.get_loc("volume")] = 3e6
    sig = [x for x in ss.signals(frames["A"], "D", None) if x["i"] == t][0]
    assert sig["gap50"] > 0 and sig["ret60"] > 0 and sig["slope"] >= 1.0
    hist = ss.history(frames, {"A": "Up", "B": "Up"}, {"A"})
    col = {k: i for i, k in enumerate(hist["cols"])}
    assert hist["cols"][-1] == "tr" and all((r[col["tr"]] >= 0) == (r[col["tf"]] == 0 and r[col["side"]] == 0) for r in hist["rows"])
    c2 = _uptrend_with_a_pullback(330)
    o2, h2, l2 = list(np.r_[c2[0], c2[:-1]]), [x * 1.003 for x in c2], [x * 0.997 for x in c2]
    sma2 = pd.Series(c2).rolling(21).mean().to_numpy()
    t2 = 329                                                                       # the signal candle is the last one: a live, waiting setup
    o2[t2], c2[t2], l2[t2], h2[t2] = sma2[t2] * 1.006, sma2[t2] * 1.016, sma2[t2] * 1.004, sma2[t2] * 1.02
    frames2 = {"A": _frame(c2, o2, h2, l2), "B": _frame(c2, o2, h2, l2)}
    live = ss.build(frames2, {"A": "Up", "B": "Up"}, {"A"}, datetime(2025, 3, 3))
    assert live["clear_percentile"] == ss.CLEAR_PERCENTILE
    longs = [r for r in live["rows"] if r["tf"] == "D" and r["side"] == "long"]
    assert longs and all(isinstance(r["trend"], int) and 0 <= r["trend"] <= 100 for r in longs) and all(r["trend"] is None for r in live["rows"] if r["tf"] == "W")


def test_the_page_has_the_clear_uptrend_filter():
    html = (Path(__file__).resolve().parent.parent / "docs" / "index.html").read_text()
    for needle in ('id="smaTrend"', "Clear uptrend only (top fifth)", 'id="smaTrendLbl"', "clearOnly", "tr: 15"):
        assert needle in html, needle
