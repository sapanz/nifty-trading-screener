"""Multi-year high retest: a multi-year high is broken, price moves away, and the FIRST return to the old high that holds as support is the trade (once per breakout)."""
import numpy as np
import pandas as pd

from signals import price_action as pa


def _frame(touch="holds", second_touch=True, fail_after_break=False, n=1000):
    """0-299 a climb to the old high (about 100), 300-699 a range under it, 700 the first close above it, a rise to ~118, then the return."""
    c = np.r_[np.linspace(60, 99.3, 300), 85 + 8 * np.sin(np.linspace(0, 20, 400)), np.full(n - 700, 0.0)]
    c[700:760] = np.linspace(101.5, 118, 60)                                  # breakout (first close above 100), then away from the old high
    c[760:800] = np.linspace(118, 104.2, 40)                                  # the pullback
    c[800:] = np.linspace(103.0, 126, n - 800)                                # and up again later
    o, h, l = c.copy(), c + 0.5, c - 0.5
    h[299] = 100.0                                                            # the old multi-year high
    l[299] = 99.0
    c[299] = 99.3
    if fail_after_break:
        c[701:] = 90.0
        o, h, l = c.copy(), c + 0.5, c - 0.5
        h[299] = 100.0
    j = 800
    if touch == "holds":                                                      # the support candle: low into the band, close above the old high in the upper half
        o[j], l[j], h[j], c[j] = 101.0, 100.2, 101.9, 101.6
    elif touch == "zone":                                                     # dips into the zone and closes just UNDER the old high but inside the zone (zone based: still support)
        o[j], l[j], h[j], c[j] = 99.4, 98.6, 99.9, 99.6
    elif touch == "weak":                                                     # touches the band but closes under the old high
        o[j], l[j], h[j], c[j] = 101.0, 99.0, 101.2, 99.6
    c[j + 1] = 102.6 if touch != "none" else c[j + 1]
    o[j + 1], l[j + 1], h[j + 1] = 101.7, 101.5, 102.9
    if second_touch:                                                          # another return to the old high weeks later (never a second trade)
        for k in range(860, 868):
            l[k], c[k], o[k], h[k] = 100.1, 101.0, 101.5, 101.6
    idx = pd.bdate_range("2021-01-01", periods=n)
    return pd.DataFrame({"open": o, "high": np.maximum.reduce([o, h, c]), "low": np.minimum.reduce([o, l, c]), "close": c, "volume": np.full(n, 1e6)}, index=idx)


def _d(df):
    return {"o": df["open"].to_numpy(float), "h": df["high"].to_numpy(float), "l": df["low"].to_numpy(float), "c": df["close"].to_numpy(float), "v": df["volume"].to_numpy(float)}


def test_the_first_return_that_holds_is_the_trade_and_only_once():
    out, pend = pa.mretest_scan(_d(_frame()), pa.Params())
    assert len(out) == 1 and not pend
    t = out[0]
    assert t["b"] == 700 and t["j"] == 800 and t["i"] == 801 and abs(t["level"] - 100.0) < 1e-9          # the later returns (860-867) add nothing


def test_a_close_inside_the_zone_under_the_old_high_is_still_support():
    out, _ = pa.mretest_scan(_d(_frame(touch="zone")), pa.Params())
    assert len(out) == 1 and out[0]["j"] == 800 and out[0]["zone"] == (98.0, 102.0)
    assert pa.mretest_scan(_d(_frame(touch="zone")), pa.Params(mr_zone_pct=0.2))[0] == []          # with a thin zone (bottom 99.8) the same candle (close 99.6) is not enough


def test_a_weak_first_touch_uses_the_breakout_up():
    out, _ = pa.mretest_scan(_d(_frame(touch="weak")), pa.Params())
    assert out == []                                                         # the later strong touch is not the first time


def test_a_failed_breakout_gives_nothing():
    out, pend = pa.mretest_scan(_d(_frame(fail_after_break=True)), pa.Params())
    assert out == [] and pend == []


def test_the_old_high_must_be_a_year_old():
    df = _frame()
    d = _d(df)
    out, _ = pa.mretest_scan(d, pa.Params(mr_min_age=450))                   # the old high is 400 sessions old: not old enough for this setting
    assert out == []


def test_the_scan_turns_it_into_a_planned_trade_with_a_stop_under_the_support_candle():
    df = _frame()
    trig, pend = pa.scan_stock(df)
    mr = [t for t in trig if t["kind"] == "mretest"]
    assert len(mr) == 1
    t = mr[0]
    assert t["side"] == "long" and t["i"] == 801 and t["stop"] < df["low"].iloc[800] and t["target"] > df["close"].iloc[801] and t["rr"] > 1.5
    g = t["geo"]
    assert g["source"] == "multiyear" and g["floor"] == 100.0 and g["retest_date"] == df.index[800].strftime("%Y-%m-%d") and g["break_date"] == df.index[700].strftime("%Y-%m-%d")
    assert "first time" in t["reason"] and "old-high zone 98.0-102.0" in t["reason"]
    assert pa.NAMES[("mretest", "long")] == "Multi-year high retest" and "mretest" in pa.KINDS


def test_a_pending_setup_waits_for_the_trigger_on_the_latest_bar():
    df = _frame().iloc[:801]                                                 # the support candle is the last bar: the trigger has not come yet
    trig, pend = pa.scan_stock(df)
    mp = [t for t in pend if t["kind"] == "mretest"]
    assert len(mp) == 1 and "waiting for a daily close above" in mp[0]["reason"] and not [t for t in trig if t["kind"] == "mretest"]


def test_the_page_lists_and_draws_the_multi_year_high_retest():
    from pathlib import Path
    html = (Path(__file__).resolve().parent.parent / "docs" / "index.html").read_text()
    assert 'value="mretest">Multi-year high retest' in html and '"mretest"' in html and 'src === "multiyear" ? "Old-high zone "' in html and 'g.source === "multiyear" ? "Old high"' in html


def test_the_stop_can_be_put_under_the_whole_zone():
    df = _frame()
    t = [x for x in pa.scan_stock(df, pa.Params(mr_stop_zone=True))[0] if x["kind"] == "mretest"]
    assert len(t) == 1 and t[0]["stop"] < 98.0 and t[0]["stop"] < [x for x in pa.scan_stock(df)[0] if x["kind"] == "mretest"][0]["stop"]


def test_overlapping_bases_do_not_give_the_same_waiting_retest_several_times():
    mk = lambda top, rr, src="range", day="2026-11-09": {"kind": "retest", "side": "long", "rr": rr, "geo": {"source": src, "retest_date": day, "top": top}}
    items = [mk(1208.6, 1.69), mk(1208.6, 1.69), mk(1175.0, 1.8), mk(1175.0, 1.69), mk(900.0, 1.2, src="zone"), mk(1100.0, 1.5, day="2026-11-10")]
    out = pa.one_per_retest(items)
    assert len(out) == 3 and sorted((o["geo"]["source"], o["geo"]["retest_date"]) for o in out) == [("range", "2026-11-09"), ("range", "2026-11-10"), ("zone", "2026-11-09")]
    assert [o for o in out if o["geo"]["source"] == "range" and o["geo"]["retest_date"] == "2026-11-09"][0]["rr"] == 1.8            # the best reward:risk wins


def _weekly_box():
    k = np.arange(40)
    c = 100 + 3 * np.sin(k * 0.9)
    o = np.r_[c[0], c[:-1]]
    return o, np.maximum(o, c) + 3.0, np.minimum(o, c) - 3.0, c


def test_the_range_box_can_be_drawn_around_the_bodies_and_capped_in_height():
    o, h, l, c = _weekly_box()
    body = pa.range_at(h, l, c, 21, pa.Params(rng_body_height=0.12, rng_min_weeks=6, rng_level="body"), o)
    assert body and abs(body["level"] - max(np.maximum(o, c)[body["start"]:22].max(), 0)) < 1e-9 and body["why"].count("body wide") == 1        # level = the highest body edge
    high = pa.range_at(h, l, c, 21, pa.Params(rng_body_height=0.12, rng_min_weeks=6, rng_level="high"), o)
    assert high and high["level"] > body["level"]                                       # the highest high sits above the body edge
    assert pa.range_at(h, l, c, 21, pa.Params(rng_body_height=0.03, rng_min_weeks=6), o) is None             # bodies taller than the cap: no box
    assert pa.range_at(h, l, c, 21, pa.Params(), o) is not None                         # the default (wick) rule is unchanged


def test_the_minimum_number_of_weeks_and_the_touch_rule_are_options():
    o, h, l, c = _weekly_box()
    assert pa.range_at(h, l, c, 21, pa.Params(rng_min_weeks=30), o) is None and pa.range_at(h, l, c, 21, pa.Params(rng_min_weeks=6), o)["weeks"] >= 6


def test_kind_report_splits_fo_and_other_stocks():
    from signals import price_action_model as pm
    ev = pd.DataFrame({"kind": ["range"] * 5 + ["zone"], "side": ["long"] * 6, "symbol": ["A", "A", "B", "B", "B", "A"], "reason": ["target", "stop", "target", "stop", "open", "stop"],
                       "R": [2.0, -1.0, 1.5, -1.0, 0.0, -1.0], "hit": [1, 0, 1, 0, 0, 0], "date": pd.to_datetime(["2023-01-02", "2023-02-02", "2024-01-02", "2024-02-02", "2024-03-02", "2024-04-02"])})
    r = pm.kind_report(ev, "range", {"A"})
    assert r["all"]["n"] == 4 and r["fo"] == {"n": 2, "hit": 50.0, "avg_r": 0.5, "pf": 2.0} and r["non_fo"]["n"] == 2 and r["non_fo_by_year"] == {2024: 0.25}


def _box_after(start: float, end: float = 100.0) -> pd.DataFrame:
    """400 sessions of drift from `start` to `end`, then 140 sessions inside a 100-114 box, then a breakout close at 118 on 5x volume."""
    c = np.concatenate([np.linspace(start, end, 400), 107.0 + 6.5 * np.cos((np.arange(140) - 139) * 2 * np.pi / 35.0), [118.0]])
    n = len(c)
    o, h, l = c.copy(), c + 0.4, c - 0.4
    v = np.full(n, 1e6)
    v[-1] = 5e6
    o[-1], l[-1], h[-1] = 114.5, 114.0, 118.5
    idx = pd.bdate_range("2021-01-01", periods=n)
    return pd.DataFrame({"open": o, "high": np.maximum.reduce([o, h, c]), "low": np.minimum.reduce([o, l, c]), "close": c, "volume": v}, index=idx)


def test_range_breakout_needs_the_weekly_30_sma_rising():
    up, down = _box_after(60.0, 100.0), _box_after(160.0, 135.0)
    free = pa.Params(rng_sma_weeks=0)
    assert pa.Params().rng_sma_weeks == 4
    got = lambda df, p: [t for t in pa.scan_stock(df, p)[0] if t["kind"] == "range"]            # noqa: E731
    assert got(up, free) and got(down, free)                                                  # the box and breakout are found either way
    assert got(up, pa.Params()) and not got(down, pa.Params())                                # a falling weekly 30 SMA drops the trade
