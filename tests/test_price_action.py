import json

import numpy as np
import pandas as pd

from signals import price_action as pa
from signals import price_action_board as pb
from signals import price_action_model as pm

P = pa.Params()


def _weeks(highs, lows=None, closes=None):
    H = np.array(highs, float)
    L = np.array(lows, float) if lows is not None else H - 2
    C = np.array(closes, float) if closes is not None else (H + L) / 2
    return H, L, C


# ---------------------------------------------------------------- weekly patterns
def _box(n=12, top=110.0, bot=100.0, lead=30):
    """`lead` trending weeks, then n weeks oscillating between bot and top (touching each side every few weeks), last week in the upper half."""
    H = list(np.linspace(60, 98, lead) + 2)
    L = list(np.linspace(60, 98, lead) - 2)
    C = list(np.linspace(60, 98, lead))
    for k in range(n):
        phase = k % 6
        hi = top if phase in (0, 1) else top - 3
        lo = bot if phase in (3, 4) else bot + 3
        H.append(hi)
        L.append(lo)
        C.append((hi + lo) / 2)
    C[-1] = top - 1
    return _weeks(H, L, C)


def test_range_is_the_box_with_spaced_touches():
    H, L, C = _box()
    r = pa.range_at(H, L, C, len(H) - 1, P)
    assert r and r["level"] == 110.0 and r["base"] == 100.0 and r["weeks"] == 12 and abs(r["height"] - 10) < 1e-9
    assert "12-week range" in r["why"]


def test_range_rejects_too_wide_too_short_and_the_lower_half():
    H, L, C = _box(top=140.0, bot=100.0)                             # 40% wide
    assert pa.range_at(H, L, C, len(H) - 1, P) is None
    H, L, C = _box(n=6)                                             # only 6 weeks of box: no window of 8+ fits inside the limits
    assert pa.range_at(H, L, C, len(H) - 1, P) is None
    H, L, C = _box()
    C[-1] = 101.0                                                   # sitting near the bottom of the box
    assert pa.range_at(H, L, C, len(H) - 1, P) is None


def test_range_is_not_reported_once_a_weekly_close_is_through_the_top():
    H, L, C = _box()
    C[-1] = 111.0
    H[-1] = 112.0
    assert pa.range_at(H, L, C, len(H) - 1, P) is None


def _triangle(flat_top=False):
    """Zig-zag with highs at weeks 4, 10, 16 (falling, or flat) and lows at 7, 13, 19 (rising), ending inside at week 22."""
    n = 23
    mid = np.full(n, 100.0)
    H, L = mid + 1.0, mid - 1.0
    tops = {4: 120.0, 10: 120.0 if flat_top else 115.0, 16: 120.0 if flat_top else 110.0}
    bots = {7: 84.0, 13: 90.0, 19: 96.0}
    for k in range(n):
        near_top = min(tops, key=lambda t: abs(t - k))
        near_bot = min(bots, key=lambda t: abs(t - k))
        if abs(k - near_top) <= abs(k - near_bot):
            H[k] = tops[near_top] - 3 * abs(k - near_top)
            L[k] = H[k] - 4
        else:
            L[k] = bots[near_bot] + 3 * abs(k - near_bot)
            H[k] = L[k] + 4
    C = (H + L) / 2
    C[-1] = 102.0
    H[-1], L[-1] = 103.0, 101.0
    lead = 20
    return np.concatenate([np.full(lead, 101.0), H]), np.concatenate([np.full(lead, 99.0), L]), np.concatenate([np.full(lead, 100.0), C])


def test_symmetric_triangle_has_falling_highs_and_rising_lows_that_converge():
    H, L, C = _triangle()
    t = pa.triangle_at(H, L, C, len(H) - 1, P)
    assert t and t["shape"] == "Symmetric"
    assert t["upper"][1] > t["upper"][3] and t["lower"][1] < t["lower"][3]          # upper line falls, lower line rises
    assert t["base"] < t["level"] < 115.0 and t["height"] > t["level"] - t["base"]  # the lines have come together


def test_ascending_triangle_has_a_flat_top():
    H, L, C = _triangle(flat_top=True)
    t = pa.triangle_at(H, L, C, len(H) - 1, P)
    assert t and t["shape"] == "Ascending" and abs(t["level"] - 120.0) < 1.0


def test_no_triangle_when_the_lows_are_falling():
    H, L, C = _triangle()
    lows = L.copy()
    lows[20 + 7], lows[20 + 13], lows[20 + 19] = 96.0, 90.0, 84.0                               # lower lows
    assert pa.triangle_at(H, lows, C, len(H) - 1, P) is None


def _cup(handle_depth=0.06, depth=0.25, weeks=24):
    lead = list(np.linspace(60, 99, 20))
    left = 100.0
    x = np.linspace(-1, 1, weeks + 1)
    cup = left * (1 - depth * (1 - x ** 2))                          # a parabola: rims at 100, bottom at 100 * (1 - depth)
    closes = lead + list(cup) + [left * (1 - handle_depth / 2), left * (1 - handle_depth) + 1.0, left * (1 - handle_depth / 2)]
    C = np.array(closes)
    H, L = C + 0.5, C - 0.5
    H[20], H[20 + weeks] = left, left                                # the two rims
    L[-2] = left * (1 - handle_depth)
    return H, L, C


def test_cup_and_handle():
    H, L, C = _cup()
    c = pa.cup_at(H, L, C, len(H) - 1, P)
    assert c and c["level"] == 100.0 and c["left"] == 20 and c["right"] == 44 and abs(c["bottom_px"] - 74.5) < 1.0
    assert abs(c["base"] - 94.0) < 1e-9 and "cup and handle" in c["why"]


def test_cup_rejects_a_deep_handle_and_a_shallow_cup():
    H, L, C = _cup(handle_depth=0.20)
    assert pa.cup_at(H, L, C, len(H) - 1, P) is None
    H, L, C = _cup(depth=0.06)
    assert pa.cup_at(H, L, C, len(H) - 1, P) is None


# ---------------------------------------------------------------- resistance and plan
def test_next_resistance_is_the_lowest_confirmed_swing_high_above():
    H = np.array([50, 51, 50, 50, 60, 80, 60, 55, 50, 52, 70, 54, 50, 48, 46, 47, 48], float)
    assert pa.next_resistance(H, len(H) - 1, 49.0, P) == 70.0        # 70 is nearer than 80
    assert pa.next_resistance(H, len(H) - 1, 72.0, P) == 80.0
    assert pa.next_resistance(H, len(H) - 1, 85.0, P) is None


def test_recent_high_counts_even_before_it_is_confirmed():
    H = np.array([50] * 10 + [52, 58, 51], float)                    # the 58 is 1 week old: not a confirmed swing yet
    assert pa.next_resistance(H, len(H) - 1, 53.0, P) == 58.0


def test_plan_reward_risk_and_measured_fallback():
    H = np.array([50, 51, 50, 50, 60, 80, 60, 55, 50, 52, 70, 54, 50, 48, 46, 47, 48], float)
    pl = pa.plan(50.0, 48.0, H, len(H) - 1, 10.0, 49.5, P)           # risk 2 (4%), next resistance 70
    assert pl["target"] == 70.0 and pl["target_kind"] == "resistance" and abs(pl["rr"] - 10.0) < 1e-9 and abs(pl["risk_pct"] - 4.0) < 1e-9
    pl = pa.plan(90.0, 87.0, H, len(H) - 1, 10.0, 89.0, P)           # nothing overhead: the pattern's height projected from the entry
    assert pl["target"] == 100.0 and pl["target_kind"] == "measured"
    assert pa.plan(50.0, 49.9, H, len(H) - 1, 10.0, 49.5, P) is None  # 0.2% risk: below the minimum
    assert pa.plan(50.0, 40.0, H, len(H) - 1, 10.0, 49.5, P) is None  # 20% risk: above the maximum


def test_resistance_closer_than_half_the_risk_is_skipped():
    H = np.array([50] * 6 + [51, 60, 52] + [50] * 5 + [50.6, 50.5, 50.2], float)
    pl = pa.plan(50.0, 48.0, H, len(H) - 1, 5.0, 49.5, P)            # 50.6 is only 0.3R away -> the next one, 60
    assert pl["target"] == 60.0


# ---------------------------------------------------------------- one stock, end to end
def _daily(seed=0, n=700, breakout_at=None):
    """A random walk long enough to hold weekly structure."""
    rng = np.random.default_rng(seed)
    c = 100 * np.cumprod(1 + rng.normal(0.0004, 0.014, n))
    o = np.concatenate([[c[0]], c[:-1]]) * (1 + rng.normal(0, 0.003, n))
    h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, 0.006, n)))
    l = np.minimum(o, c) * (1 - np.abs(rng.normal(0, 0.006, n)))
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": rng.integers(1e5, 1e6, n).astype(float)}, index=pd.bdate_range("2022-01-03", periods=n))


def test_scan_never_looks_ahead():
    """Cutting the history at any day must not change the triggers found before that day."""
    for seed in (1, 2, 3):
        df = _daily(seed)
        full, _ = pa.scan_stock(df)
        assert full, "the walk should produce at least one setup"
        for cut in (400, 520, 640):
            part, _ = pa.scan_stock(df.iloc[:cut])
            key = lambda t: (t["kind"], t["i"], round(t["stop"], 6), round(t["target"], 6))
            assert [key(t) for t in part] == [key(t) for t in full if t["i"] < cut]


def test_trigger_is_a_close_above_the_level_with_the_stop_under_it():
    for seed in range(6):
        df = _daily(seed)
        c, l = df["close"].to_numpy(), df["low"].to_numpy()
        for t in pa.scan_stock(df)[0]:
            i = t["i"]
            assert t["stop"] < c[i] < t["target"] and P.min_risk_pct <= t["risk_pct"] <= P.max_risk_pct
            assert abs(t["rr"] - (t["target"] - c[i]) / (c[i] - t["stop"])) < 1e-9
            if t["kind"] != "zone":
                assert c[i] > t["level"] and t["stop"] <= min(t["level"], l[i])
            else:
                assert c[i] > t["level"] and t["stop"] < t["geo"]["base"]
            assert t["reason"] and t["geo"]["from"] <= df.index[i].strftime("%Y-%m-%d")


def test_one_trigger_per_kind_inside_the_cooldown():
    for seed in range(6):
        last = {}
        for t in pa.scan_stock(_daily(seed))[0]:
            assert t["i"] - last.get(t["kind"], -10 ** 9) >= P.cooldown_days
            last[t["kind"]] = t["i"]


def test_short_history_gives_nothing():
    assert pa.scan_stock(_daily(0, n=200)) == ([], [])


# ---------------------------------------------------------------- outcomes
def _ohlc(rows):
    a = np.array(rows, float)
    return a[:, 0], a[:, 1], a[:, 2], a[:, 3]


def test_outcome_target_stop_tie_and_gap():
    o, h, l, c = _ohlc([[100, 101, 99, 100], [100, 103, 99.5, 102], [102, 111, 101, 110]])
    r = pm.outcome(o, h, l, c, 0, 95.0, 110.0, P)                    # entry 100, risk 5, target 110 -> +2R less cost
    assert r["hit"] and r["reason"] == "target" and abs(r["R"] - (2.0 - 0.001 * 100 / 5)) < 1e-9 and abs(r["rr_actual"] - 2.0) < 1e-9
    o, h, l, c = _ohlc([[100, 101, 99, 100], [100, 112, 94, 100]])
    r = pm.outcome(o, h, l, c, 0, 95.0, 110.0, P)                    # the same bar reaches both: the stop counts
    assert not r["hit"] and r["reason"] == "stop" and abs(r["R"] - (-1.0 - 0.02)) < 1e-9
    o, h, l, c = _ohlc([[100, 101, 99, 100], [100, 101, 99, 100], [92, 93, 91, 92]])
    r = pm.outcome(o, h, l, c, 0, 95.0, 110.0, P)                    # gaps below the stop: out at the open, worse than -1R
    assert r["reason"] == "stop" and r["R"] < -1.5


def test_outcome_none_when_the_open_leaves_no_trade():
    o, h, l, c = _ohlc([[100, 101, 99, 100], [111, 112, 110, 111]])
    assert pm.outcome(o, h, l, c, 0, 95.0, 110.0, P) is None         # opened beyond the target
    o, h, l, c = _ohlc([[100, 101, 99, 100], [94, 95, 93, 94]])
    assert pm.outcome(o, h, l, c, 0, 95.0, 110.0, P) is None         # opened below the stop
    assert pm.outcome(o, h, l, c, 1, 95.0, 110.0, P) is None         # no next session


def test_outcome_still_open_is_marked():
    o, h, l, c = _ohlc([[100, 101, 99, 100], [100, 101, 99, 100.5], [100.5, 102, 100, 101]])
    r = pm.outcome(o, h, l, c, 0, 95.0, 110.0, P)
    assert r["reason"] == "open" and not r["hit"]


# ---------------------------------------------------------------- model
def _events(n=1500, seed=0):
    rng = np.random.default_rng(seed)
    rr = np.exp(rng.uniform(np.log(1.5), np.log(8), n))
    vol = np.exp(rng.normal(0, 0.5, n))
    p_true = 1 / (1 + rr * 0.9) * (1 + 0.25 * np.tanh(np.log(vol)))  # roughly the break-even odds, better on volume
    hit = rng.random(n) < p_true
    dates = pd.bdate_range("2021-01-01", periods=n)
    kind = rng.choice(list(pa.KINDS), n)
    return pd.DataFrame({"symbol": "S", "kind": kind, "date": dates, "exit_date": dates + pd.Timedelta(days=20), "entry_date": dates, "rr": rr, "risk_pct": rng.uniform(1, 8, n),
                         "reward_pct": rr * 3, "log_rr": np.log(rr), "log_vol": np.log(vol), "vol": vol, "mkt50": rng.integers(0, 2, n).astype(float),
                         "mkt200": rng.integers(0, 2, n).astype(float), "above200": rng.integers(0, 2, n).astype(float), "mom": rng.random(n), "off_high": -rng.random(n) * 0.3,
                         "is_range": (kind == "range").astype(float), "is_triangle": (kind == "triangle").astype(float), "is_cup": (kind == "cup").astype(float),
                         "hit": hit, "R": np.where(hit, rr, -1.0) - 0.03, "reason": np.where(hit, "target", "stop"), "target_kind": "resistance"})


def test_fit_learns_that_a_bigger_reward_is_reached_less_often_and_volume_helps():
    ev = _events()
    m = pm.fit(ev)
    w = dict(zip(m["features"], m["beta"][1:]))
    assert w["log_rr"] < -0.3 and w["log_vol"] > 0.05
    lo = pm.predict(m, ev.assign(log_rr=np.log(1.5)))
    hi = pm.predict(m, ev.assign(log_rr=np.log(8.0)))
    assert lo.mean() > hi.mean() + 0.15 and 0 < hi.min() and lo.max() < 1
    assert abs(pm.predict(m, ev).mean() - ev["hit"].mean()) < 0.03                   # calibrated in the large on its own data
    assert -1.1 < m["miss_r"] < -0.9


def test_fit_needs_enough_setups_and_recent_ones_count_more(monkeypatch):
    assert pm.fit(_events(n=150)) is None
    ev = _events()
    ev.loc[ev.index[-300:], "hit"] = False                                          # the last stretch was all misses
    recent = pm.fit(ev)
    monkeypatch.setattr(pm, "HALF_LIFE_DAYS", 1e9)                                  # no recency weighting at all
    flat = pm.fit(ev)
    assert recent["base_rate"] < flat["base_rate"] - 0.02 and abs(flat["base_rate"] - ev["hit"].mean()) < 1e-6


def test_validate_only_scores_years_from_models_fitted_on_finished_setups():
    v = pm.validate(_events())
    assert v and v["test_n"] > 300 and len(v["table"]) == 4 and v["test_from"] >= "2022-01-01"
    assert [r["predicted"] for r in v["table"]] == sorted(r["predicted"] for r in v["table"])
    assert v["table"][-1]["actual"] > v["table"][0]["actual"]                       # the ordering carries to the unseen years


def test_expected_r_uses_the_average_miss():
    m = {"miss_r": -0.9}
    assert abs(pm.expected_r(m, np.array([0.25]), np.array([3.0]))[0] - (0.75 - 0.675)) < 1e-9


# ---------------------------------------------------------------- the board
def test_oi_tag_quadrants_and_rollover_guard():
    close = pd.Series([100, 101, 102, 103, 104, 105, 110.0])
    store = {"oi": {"A": {f"2026-01-{d:02d}": v for d, v in zip(range(1, 8), [100, 100, 100, 100, 100, 100, 110])}}}
    t = pb.oi_tag(store, "A", close)
    assert t["tag"] == "long buildup" and t["for"] and t["oi_chg"] == 10.0
    store["oi"]["A"]["2026-01-07"] = 90
    assert pb.oi_tag(store, "A", close)["tag"] == "short covering"
    fall = pd.Series([110, 109, 108, 107, 106, 105, 100.0])
    assert pb.oi_tag(store, "A", fall)["tag"] == "long unwinding" and not pb.oi_tag(store, "A", fall)["for"]
    store["oi"]["A"]["2026-01-07"] = 120
    assert pb.oi_tag(store, "A", fall)["tag"] == "short buildup"
    store["oi"]["A"]["2026-01-07"] = 400                                            # a rollover step
    assert pb.oi_tag(store, "A", fall) is None and pb.oi_tag(store, "B", fall) is None and pb.oi_tag(None, "A", fall) is None


def test_calibration_only_scales_down():
    assert pb._calibration(None) == 1.0
    assert pb._calibration({"table": [{"predicted": 0.4, "actual": 0.2, "n": 10}, {"predicted": 0.2, "actual": 0.16, "n": 10}]}) == 0.6
    assert pb._calibration({"table": [{"predicted": 0.2, "actual": 0.4, "n": 10}]}) == 1.0
    assert pb._calibration({"table": [{"predicted": 0.5, "actual": 0.05, "n": 10}]}) == 0.5


def test_board_payload_is_sorted_serialisable_and_consistent():
    frames = {f"S{k:02d}": _daily(k) for k in range(14)}
    payload, candles = pb.build(frames, fo={"S00", "S01", "S02"}, sectors={"S00": "Banks"}, oi_store=None)
    json.dumps(payload)
    json.dumps(candles)
    rows = payload["setups"]
    assert payload["universe"] == 14 and payload["fo_count"] == 3 and payload["asof"] == frames["S00"].index[-1].strftime("%Y-%m-%d")
    assert [r["rr"] for r in rows] == sorted((r["rr"] for r in rows), reverse=True)
    for r in rows:
        assert r["rr"] >= payload["min_rr"] and r["stop"] < r["entry"] < r["target"] and r["status"] in ("triggered", "pending")
        assert abs(r["rr"] - (r["target"] - r["entry"]) / (r["entry"] - r["stop"])) < 0.02 and r["reason"] and r["symbol"] in candles["stocks"]
        assert r["fo"] == (r["symbol"] in {"S00", "S01", "S02"}) and r["oi"] is None
        assert 0 < r["breakeven"] < 100 and (r["p"] is None or 0 <= r["p"] <= 100)
        if r["status"] == "triggered":
            assert 0 <= r["age"] <= pb.FRESH_DAYS and r["entry"] == r["price"]
        else:
            assert r["entry"] > r["price"] * 0.999 and r["age"] is None
    assert set(candles["stocks"]) == {r["symbol"] for r in rows}
    assert all(len(v["d"]) == len(v["c"]) <= pb.CHART_BARS for v in candles["stocks"].values())
