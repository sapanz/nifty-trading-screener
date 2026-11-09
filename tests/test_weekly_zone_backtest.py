import numpy as np
import pandas as pd

from signals import weekly_zone_backtest as w


def _arr(o, h, l, c):
    return {"o": np.array(o, float), "h": np.array(h, float), "l": np.array(l, float), "c": np.array(c, float)}


def _walk(closes, start="2024-01-01", spread=0.5):
    closes = np.array(closes, float)
    dates = pd.bdate_range(start, periods=len(closes))
    return _arr(closes, closes + spread, closes - spread, closes), dates


def _zone(known, lo=100.0, hi=110.0):
    return {"lo": lo, "hi": hi, "known": pd.Timestamp(known), "formed": pd.Timestamp(known), "impulse": 3.0}


def test_long_zone_signals_when_a_close_comes_back_out_above_it():
    a, dates = _walk([130, 125, 120, 112, 108, 109, 111, 115])          # 108 and 109 close inside the zone; 111 is the first close back out above 110
    sig, entered, outcome = w.scan_zone(a, dates, _zone(dates[0] - pd.Timedelta(days=1)), w.Params())
    assert outcome == "signal" and entered == 4 and sig == 6


def test_long_zone_fails_expires_or_never_triggers():
    a, dates = _walk([130, 120, 108, 105, 98, 120, 125])                # a close below the zone low (less the buffer) kills it before any exit
    assert w.scan_zone(a, dates, _zone(dates[0] - pd.Timedelta(days=1)), w.Params())[2] == "failed"
    a, dates = _walk([130, 128, 126, 124, 122])                         # never comes down to the zone
    assert w.scan_zone(a, dates, _zone(dates[0] - pd.Timedelta(days=1)), w.Params())[2] == "never"
    a, dates = _walk([130, 108, 107, 106, 105, 104])                    # goes in and stays inside until the zone expires
    assert w.scan_zone(a, dates, _zone(dates[0] - pd.Timedelta(days=1)), w.Params(expiry_weeks=1))[2] == "expired"


def test_zone_is_only_watched_after_the_confirming_week_closed():
    a, dates = _walk([108, 109, 111, 112, 108, 109, 112, 115])
    sig, _, outcome = w.scan_zone(a, dates, _zone(dates[3]), w.Params())      # known on the 4th session: earlier closes must not count
    assert outcome == "signal" and sig == 6


def test_touch_mode_enters_on_a_wick_into_the_zone():
    c = [130, 125, 120, 118, 120, 125]
    a, dates = _walk(c)
    a["l"][3] = 109.0                                                   # the low reached the zone's high (110) but the close stayed above it
    assert w.scan_zone(a, dates, _zone(dates[0] - pd.Timedelta(days=1)), w.Params())[2] == "never"
    sig, entered, outcome = w.scan_zone(a, dates, _zone(dates[0] - pd.Timedelta(days=1)), w.Params(in_zone="touch"))
    assert outcome == "signal" and entered == 3 and sig == 4


def test_short_zone_is_the_mirror():
    a, dates = _walk([70, 80, 90, 98, 101, 105, 99, 90])               # supply zone 100-110: closes at 101 and 105 are inside, 99 is the first close back out below it
    sig, entered, outcome = w.scan_zone(a, dates, _zone(dates[0] - pd.Timedelta(days=1)), w.Params(side="short"))
    assert outcome == "signal" and entered == 4 and sig == 6
    a, dates = _walk([70, 90, 105, 112, 125])                           # a close above the zone high (less nothing, plus the buffer) kills it
    assert w.scan_zone(a, dates, _zone(dates[0] - pd.Timedelta(days=1)), w.Params(side="short"))[2] == "failed"


def test_long_trade_stop_is_the_zone_low_and_costs_come_out_of_r():
    zone_low = 100.0
    stop = zone_low * 0.999
    a = _arr(o=[112, 112, 111, 112, 120, 125, 130], h=[113, 113, 112, 114, 126, 133, 140], l=[111, 111, 110, 111, 119, 124, 129], c=[112, 111, 111, 113, 125, 130, 138])
    t = w._simulate(a, 2, 1, stop, w.Params(min_risk_pct=0.1))           # entry = open of bar 3 = 112; risk = 112 - 99.9 = 12.1; 2R target = 136.2
    assert t["entry"] == 112 and abs(t["risk_pct"] - 12.1 / 112 * 100) < 1e-6
    assert t["reason"] == "target" and abs(t["R"] - (2 - 0.001 * 112 / 12.1)) < 1e-6


def test_stop_wins_ties_gaps_exit_at_the_open_and_the_risk_limits_skip():
    a = _arr(o=[112, 112, 111, 112, 112, 112], h=[113, 113, 112, 114, 140, 112], l=[111, 111, 110, 111, 95, 111], c=[112, 111, 111, 113, 112, 112])
    t = w._simulate(a, 2, 1, 99.9, w.Params(min_risk_pct=0.1))          # bar 4 trades through both the target and the stop: the stop wins
    assert t["reason"] == "stop" and t["R"] < -1
    a = _arr(o=[112, 112, 111, 112, 90, 100], h=[113, 113, 112, 114, 92, 101], l=[111, 111, 110, 111, 88, 99], c=[112, 111, 111, 113, 91, 100])
    t = w._simulate(a, 2, 1, 99.9, w.Params(min_risk_pct=0.1))
    assert t["reason"] == "stop-gap" and t["exit"] == 90 and t["R"] < -1.5      # opened below the stop: filled at the open
    assert w._simulate(a, 2, 1, 99.9, w.Params(max_risk_pct=5.0)) is None
    assert w._simulate(a, 2, 1, 111.9, w.Params(min_risk_pct=2.0)) is None


def test_short_trade_mirror_and_trail_exit():
    a = _arr(o=[88, 88, 89, 88, 80, 75], h=[89, 89, 90, 89, 81, 76], l=[87, 87, 88, 87, 79, 70], c=[88, 89, 89, 88, 78, 72])
    t = w._simulate(a, 2, -1, 110.1, w.Params(side="short", min_risk_pct=0.1, max_risk_pct=40))     # entry 88, risk 22.1, 2R target 43.8: not reached -> exits on time
    assert t["reason"] == "time" and t["R"] > 0
    up = [100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111, 112, 113, 114, 115, 116, 117, 118, 119, 120, 121, 122, 123, 90]
    a = _arr(o=up, h=[x + 1 for x in up], l=[x - 1 for x in up], c=up)
    t = w._simulate(a, 1, 1, 50.0, w.Params(exit_mode="trail", min_risk_pct=0.1, max_risk_pct=90, trail_days=5, max_hold=60))
    assert t["reason"] == "trail" and t["exit"] == 90                    # a close under the prior 5 sessions' lowest low ends the trend trade


def _random_frame(seed, days=1500):
    rng = np.random.default_rng(seed)
    t = np.arange(days)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0003, 0.02, days)) + 0.25 * np.sin(2 * np.pi * t / 130))
    open_ = np.concatenate([[close[0]], close[:-1]]) * (1 + rng.normal(0, 0.004, days))
    return pd.DataFrame({"open": open_, "high": np.maximum(open_, close) * 1.01, "low": np.minimum(open_, close) * 0.99, "close": close, "volume": 1e6}, index=pd.bdate_range("2019-01-01", periods=days))


def test_zones_do_not_look_ahead():
    df = _random_frame(3)
    p = w.Params()
    full = w.find_zones(df, p)
    assert len(full) > 5 and all(z["known"].weekday() == 4 for z in full)
    for cut in (700, 950, 1200):
        part = w.find_zones(df.iloc[:cut], p)
        known_by_cut = [z for z in full if z["known"] <= df.index[cut - 1]]
        assert [(round(z["lo"], 6), round(z["hi"], 6), z["known"]) for z in part] == [(round(z["lo"], 6), round(z["hi"], 6), z["known"]) for z in known_by_cut]


def test_run_funnel_invariants_and_both_sides():
    frames = {f"S{k}": _random_frame(k) for k in range(12)}
    for side in ("long", "short"):
        p = w.Params(side=side, impulse_atr=1.5)
        trades = w.run(frames, p)
        f = trades.attrs
        assert f["zones"] >= f["in_zone"] >= f["signals"] >= f["taken"] and f["taken"] > 3
        assert (trades["entry_date"] > trades["signal_date"]).all() and (trades["exit_date"] >= trades["entry_date"]).all()
        assert trades["risk_pct"].between(p.min_risk_pct, p.max_risk_pct).all()
        edge = trades["zone_lo"] * (1 - 0.001) if side == "long" else trades["zone_hi"] * 1.001
        assert np.allclose(trades["stop"], edge)                                            # the stop is the zone's far edge, as specified
        for _, g in trades.groupby("symbol"):
            g = g.sort_values("entry_date")
            assert (g["entry_date"].iloc[1:].to_numpy() > g["exit_date"].iloc[:-1].to_numpy()).all()
        ctrl = w.run(frames, p, control=True)
        assert len(ctrl) > 3 and ctrl["zone_lo"].isna().all()


def test_summary_text_covers_both_sides_the_control_and_the_gate():
    frames = {f"S{k}": _random_frame(k + 20) for k in range(8)}
    text = w.summarize(frames, "both")
    for needle in ("LONG side", "SHORT side", "Funnel", "Control", "market gate", "By year", "survivorship"):
        assert needle in text
    gate = w.market_gate(frames)
    assert gate.dtype == bool and len(gate) > 1000
