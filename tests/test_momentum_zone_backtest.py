import numpy as np
import pandas as pd

from signals import momentum as mom
from signals import momentum_zone_backtest as mz


def _P(o, h, l, c):
    return {"open": np.array(o, float)[:, None], "high": np.array(h, float)[:, None], "low": np.array(l, float)[:, None], "close": np.array(c, float)[:, None]}


# ---- volume thrust
def test_volume_thrust_is_recent_three_over_the_twenty_before():
    v = [100.0] * 20 + [150.0, 150.0, 150.0]
    assert mom.volume_thrust(v) == 1.5
    assert mom.volume_thrust([100.0] * 23) == 1.0
    assert mom.volume_thrust([999.0] * 5 + [100.0] * 20 + [200.0] * 3) == 2.0       # sessions older than the 23 do not count


def test_volume_thrust_is_nan_when_it_cannot_be_told():
    assert np.isnan(mom.volume_thrust([100.0] * 22))                                   # too short
    assert np.isnan(mom.volume_thrust([100.0] * 20 + [150.0, np.nan, 150.0]))          # a missing recent session
    assert np.isnan(mom.volume_thrust([np.nan] * 15 + [100.0] * 5 + [150.0] * 3))      # most of the base missing
    assert np.isnan(mom.volume_thrust([0.0] * 20 + [5.0] * 3))                         # no base volume


def test_momentum_lists_carry_vt_and_filter_on_it():
    n, m = 90, 40
    dates = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2026-01-01", periods=n)]
    rng = np.random.default_rng(0)
    stocks, turnover = {}, {}
    for k in range(m):
        c = 100 * np.cumprod(1 + 0.002 * (k - m / 2) / m + rng.normal(0, 0.004, n))
        v = np.full(n, 1000.0)
        if k % 2 == 0:
            v[-3:] = 2000.0                                                                # even stocks: volume doubled over the last 3 sessions
        stocks[f"S{k}"] = {"c": [[x, x * 1.01, x * 0.99, x] for x in c], "v": v.tolist(), "d0": dates[0]}
        turnover[f"S{k}"] = 50.0
    cd = {"dates": dates, "stocks": stocks}
    res = mom.momentum_lists(cd, turnover, top_n=40)
    assert {r["vt"] for r in res["strong"]} == {2.0, 1.0}
    kept = mom.momentum_lists(cd, turnover, top_n=40, min_thrust=1.5)
    assert kept["strong"] and all(r["vt"] >= 1.5 for r in kept["strong"] + kept["weak"])
    assert [r["symbol"] for r in kept["strong"]] == [r["symbol"] for r in res["strong"] if r["vt"] >= 1.5]    # same order: the ranking is unchanged
    for s in stocks.values():
        del s["v"]
    assert all(r["vt"] is None for r in mom.momentum_lists(cd, turnover)["strong"])
    assert mom.momentum_lists(cd, turnover, min_thrust=1.5)["strong"] == []                 # no volume -> nothing confirms


# ---- one trade
def test_long_limit_fills_at_the_edge_and_hits_2r():
    #        t   t+1  t+2  t+3  (entry 100, stop 95 -> R = 5, target 110)
    P = _P([101, 101, 104, 108, 111], [102, 102, 106, 112, 113], [100.5, 99, 103, 107, 110], [101, 100, 105, 110, 112])
    r = mz.simulate(P, 0, 0, 1, 100.0, 95.0, 2.0)
    assert r["reason"] == "target" and r["entry_i"] == 1 and r["exit_i"] == 3
    assert abs(r["R"] - (2.0 - 0.001 * 100 / 5)) < 1e-9                                  # 2R less the 0.10% cost expressed in R


def test_long_stop_wins_a_same_bar_tie_and_gap_open_fills_better():
    P = _P([101, 101, 100, 100], [102, 102, 111, 100], [100.5, 99, 94, 99], [101, 100, 100, 100])
    r = mz.simulate(P, 0, 0, 1, 100.0, 95.0, 2.0)                                       # day 2 both reaches 111 and 94 -> stop first
    assert r["reason"] == "stop" and abs(r["R"] - (-1.0 - 0.02)) < 1e-9
    P = _P([101, 98, 99, 100], [102, 99, 100, 101], [100.5, 97, 98, 99], [101, 98, 99, 100])
    r = mz.simulate(P, 0, 0, 1, 100.0, 95.0, 2.0)                                       # gapped down to 98 -> filled at the open, not 100
    assert r["entry_i"] == 1 and r["reason"] == "time"                                  # (the data ends before the hold period does)
    assert abs(r["R"] - ((100 - 98) / 5 - 0.001 * 98 / 5)) < 1e-9                       # measured from the 98 fill, in units of the planned 5 risk


def test_no_fill_and_gap_through_the_stop():
    P = _P([101] * 14, [102] * 14, [101] * 14, [101.5] * 14)
    assert mz.simulate(P, 0, 0, 1, 100.0, 95.0, 2.0) is None                           # never comes back down within 10 sessions
    P = _P([101, 93, 93, 93], [102, 94, 94, 94], [100.5, 92, 92, 92], [101, 93, 93, 93])
    r = mz.simulate(P, 0, 0, 1, 100.0, 95.0, 2.0)                                       # opens below the stop: booked as a stop from the planned entry
    assert r["reason"] == "stop-gap" and r["R"] < -1.0


def test_short_is_the_mirror():
    P = _P([99, 99, 96, 92, 89], [99.5, 101, 97, 93, 90], [98, 98, 95, 90, 88], [99, 100, 96, 91, 89])
    r = mz.simulate(P, 0, 0, -1, 100.0, 105.0, 2.0)                                     # sell limit 100, stop 105, target 90
    assert r["reason"] == "target" and r["exit_i"] == 3 and abs(r["R"] - (2.0 - 0.02)) < 1e-9


def test_time_exit_after_the_hold_period():
    n = 40
    c = [101.0] + [100.0] * (n - 1)
    P = _P(c, [x + 0.5 for x in c], [x - 0.2 for x in c], c)
    r = mz.simulate(P, 0, 0, 1, 100.0, 90.0, 2.0)
    assert r["reason"] == "time" and r["exit_i"] - r["entry_i"] == mz.HOLD_DAYS


# ---- selection
def _ev(rows):
    base = {"up": True, "gap": 1.0, "rank": 10, "vt": 1.0, "filled": True, "R": 1.0, "t": 1, "reason": "target", "exit_date": pd.Timestamp("2024-01-02"),
            "entry_date": pd.Timestamp("2024-01-01"), "bars": 3, "ret_pct": 1.0, "risk_pct": 3.0}
    return pd.DataFrame([{**base, **r} for r in rows])


def test_select_filters_and_counts_a_zone_once():
    ev = _ev([{"key": "A", "t": 1}, {"key": "A", "t": 2}, {"key": "B", "gap": 6.0}, {"key": "C", "rank": 40}, {"key": "D", "vt": 1.6},
              {"key": "E", "up": False}, {"key": "F", "filled": False}])
    assert sorted(mz.select(ev, "long")["key"]) == ["A", "C", "D"]                       # A once; B too far; E is short; F never filled
    assert sorted(mz.select(ev, "long", pool=30)["key"]) == ["A", "D"]
    assert list(mz.select(ev, "long", pool=30, min_vt=1.5)["key"]) == ["D"]
    assert list(mz.select(ev, "long", rest_beyond=30)["key"]) == ["C"]
    assert list(mz.select(ev, "short")["key"]) == ["E"]


def test_first_appearance_is_judged_inside_the_filter_set():
    ev = _ev([{"key": "A", "t": 1, "vt": 1.0}, {"key": "A", "t": 5, "vt": 1.8}])
    assert list(mz.select(ev, "long")["t"]) == [1]                                       # without the filter the zone is first seen at t=1
    assert list(mz.select(ev, "long", min_vt=1.5)["t"]) == [5]                           # with it, the first day it passes is t=5


def test_candidates_pick_the_nearest_zone_on_the_markets_side_and_rank_by_momentum():
    n, m = 90, 30
    dates = pd.bdate_range("2025-01-01", periods=n)
    rng = np.random.default_rng(2)
    frames = {}
    for k in range(m):
        c = 100 * np.cumprod(1 + 0.0004 * k + rng.normal(0, 0.003, n))
        frames[f"S{k:02d}"] = pd.DataFrame({"open": c, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": 5e6}, index=dates)   # ~50 cr a day
    P = mz.prepare(frames)
    t = n - 5
    up = np.ones(n, bool)
    close = P["close"][t, 29]
    zones = {("S29", P["dates"][t]): [{"d": "bull", "lo": close * 0.90, "hi": close * 0.93, "sc": 50, "t": "2025-03-01", "cf": 0},
                                       {"d": "bull", "lo": close * 0.95, "hi": close * 0.97, "sc": 40, "t": "2025-03-05", "cf": 1},
                                       {"d": "bear", "lo": close * 1.02, "hi": close * 1.04, "sc": 90, "t": "2025-03-06", "cf": 0}]}
    ev = mz.candidates(P, zones, [t], up)
    assert len(ev) == 1
    r = ev.iloc[0]
    assert r["key"] == "S29|bull|2025-03-05"                                              # the nearer demand zone; the supply zone is the wrong side
    assert abs(r["gap"] - (1 / 0.97 - 1) * 100) < 1e-6 and r["rank"] <= 3                 # price is 3.09% above the zone's top; the steepest riser ranks near the top
    assert abs(r["near"] - close * 0.97) < 1e-9 and abs(r["stop"] - close * 0.95 * 0.999) < 1e-9
    assert abs(r["vt"] - 1.0) < 1e-9


def test_market_state_follows_the_trend():
    n, m = 120, 25
    dates = pd.bdate_range("2025-01-01", periods=n)
    frames = {}
    for k in range(m):
        c = np.concatenate([np.linspace(100, 140, 80), np.linspace(140, 90, 40)])
        frames[f"S{k}"] = pd.DataFrame({"open": c, "high": c, "low": c, "close": c, "volume": 1e7}, index=dates)
    up = mz.market_state(mz.prepare(frames))
    assert up[70] and not up[-1]


def test_summary_runs_and_names_the_groups():
    ev = _ev([{"key": f"K{i}", "t": i, "rank": 5 + i, "vt": 1.0 + i / 10, "R": 1.0 if i % 2 else -1.0, "reason": "target" if i % 2 else "stop",
               "exit_date": pd.Timestamp("2023-01-01") + pd.Timedelta(days=40 * i), "entry_date": pd.Timestamp("2023-01-01") + pd.Timedelta(days=40 * i - 3)}
              for i in range(12)] + [{"key": f"S{i}", "up": False, "t": i, "rank": 5 + i, "R": 2.0, "reason": "target",
                                      "exit_date": pd.Timestamp("2023-01-01") + pd.Timedelta(days=40 * i), "entry_date": pd.Timestamp("2023-01-01") + pd.Timedelta(days=40 * i - 3)}
                                     for i in range(8)])
    P = {"symbols": ["A"] * 3, "dates": pd.bdate_range("2023-01-01", periods=5)}
    text = mz.summarize(ev, P)
    for needle in ("LONG", "SHORT", "momentum top 30", "volume thrust >= 1.5x", "rest of universe", "By year"):
        assert needle in text
