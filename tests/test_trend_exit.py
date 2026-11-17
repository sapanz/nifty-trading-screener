import json

import numpy as np
import pandas as pd

from signals import price_action as pa
from signals import price_action_board as pb
from signals import price_action_model as pm


def _breakout_stock(seed=1, days=(520, 300, 150, 200, 260, 180), ends=(200, 120, 135, 230, 330, 230)):
    """Rises to 200 over two years, falls to 120, bases at 120-135, then breaks the old high, runs to 330 and falls back to 230."""
    path = [100.0]
    for d, e in zip(days, ends):
        path += list(np.linspace(path[-1], e, d + 1)[1:])
    c = np.array(path) * (1 + np.random.default_rng(seed).normal(0, 0.004, len(path)))
    o = np.r_[c[0], c[:-1]]
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) * 1.004, "low": np.minimum(o, c) * 0.996, "close": c, "volume": np.full(len(c), 5e5)},
                        index=pd.bdate_range("2019-01-01", periods=len(c)))


def test_flags_mark_only_week_ends_below_the_30_week_average_and_never_the_newest_bar():
    dates = pd.bdate_range("2024-01-01", periods=5 * 70)                         # 70 whole weeks, Monday to Friday
    c = np.r_[np.linspace(100, 200, 5 * 45), np.linspace(200, 120, 5 * 25)]
    f = pm.trend_exit_flags(dates, c)
    assert f.sum() > 0 and not f[: 5 * 30].any(), "no flag until 30 weekly closes exist"
    assert all(dates[i].dayofweek == 4 for i in np.flatnonzero(f)), "only on the last session of a week"
    first = int(np.flatnonzero(f)[0])
    assert first > 5 * 45 and c[first] < c[first - 5 * 30: first + 1].mean() * 1.2    # only once the fall has taken the close under the average
    cut = pm.trend_exit_flags(dates[:-2], c[:-2])                                # the data ends on a Wednesday: that week is not over
    assert not cut[-1] and not cut[-2]
    assert pm.trend_exit_flags(dates[:1], c[:1]).tolist() == [False]


def test_trend_sma_is_the_average_of_the_last_30_completed_weekly_closes():
    dates = pd.bdate_range("2024-01-01", periods=5 * 40 + 2)                     # 40 whole weeks and two more sessions of week 41
    c = np.arange(len(dates), dtype=float) + 100
    got = pm.trend_sma(dates, c)
    weekly = [c[5 * w + 4] for w in range(40)]
    assert abs(got - np.mean(weekly[-30:])) < 1e-9
    assert pm.trend_sma(dates[:5 * 20], c[:5 * 20]) is None


def _bars(rows):
    a = np.array(rows, float)
    return a[:, 0], a[:, 1], a[:, 2], a[:, 3]


def test_outcome_trend_exits_at_the_open_after_the_flagged_week_and_the_stop_still_wins():
    # i = 0 is the trigger; entry at the open of bar 1 (100), stop 95 -> 1R = 5
    o, h, l, c = _bars([[100, 101, 99, 100], [100, 103, 99, 102], [102, 112, 101, 111], [112, 130, 110, 128], [127, 128, 119, 120], [118, 119, 100, 101], [100, 101, 99, 100]])
    flags = np.zeros(7, bool)
    flags[4] = True                                                              # the weekly close on bar 4 is under the average
    r = pm.outcome_trend(o, h, l, c, 0, 95.0, flags)
    assert r["t_out"] == "trend" and r["t_exit_i"] == 5 and r["t_exit"] == 118 and r["t_bars"] == 4
    assert abs(r["t_R"] - ((118 - 100) / 5 - 0.001 * 100 / 5)) < 1e-9
    fixed = pm.outcome(o, h, l, c, 0, 95.0, 110.0, pa.Params())                 # the fixed target would have sold at 110 on bar 2
    assert fixed["reason"] == "target" and r["t_R"] > fixed["R"]
    low = l.copy()
    low[3] = 94.0                                                                # the stop is hit first on bar 3
    s = pm.outcome_trend(o, h, low, c, 0, 95.0, flags)
    assert s["t_out"] == "stop" and s["t_exit"] == 95.0 and s["t_exit_i"] == 3 and s["t_R"] < -1
    gap = o.copy()
    gap[3] = 90.0                                                                # a gap through the stop fills at the open, worse than -1R
    g = pm.outcome_trend(gap, h, l, c, 0, 95.0, flags)
    assert g["t_out"] == "stop" and g["t_exit"] == 90.0 and g["t_R"] < -2


def test_outcome_trend_stays_open_to_the_end_and_is_closed_at_the_cap():
    o, h, l, c = _bars([[100, 101, 99, 100]] + [[100 + k, 102 + k, 99 + k, 101 + k] for k in range(1, 9)])
    none = np.zeros(9, bool)
    assert pm.outcome_trend(o, h, l, c, 0, 90.0, none)["t_out"] == "open"
    capped = pm.outcome_trend(o, h, l, c, 0, 90.0, none, max_hold=3)
    assert capped["t_out"] == "time" and capped["t_bars"] == 3 and capped["t_exit"] == c[4]
    last_flag = none.copy()
    last_flag[8] = True                                                          # flagged on the final bar: no next open to sell at, still open
    assert pm.outcome_trend(o, h, l, c, 0, 90.0, last_flag)["t_out"] == "open"


def test_a_multi_year_breakout_is_traded_with_the_trend_exit_and_other_kinds_are_unchanged():
    df = _breakout_stock()
    ev, _ = pm.collect({"X": df})
    my = ev[ev["kind"] == "multiyear"]
    assert len(my) == 1
    r = my.iloc[0]
    assert r["t_out"] == "trend" and r["t_bars"] > r["bars"] and r["t_R"] > r["R"] and r["t_exit_date"] > r["exit_date"]
    assert r["reason"] != "open" and r["hit"] in (True, False)                    # the fixed-target view the probability is fitted on is still there
    rest = ev[ev["kind"] != "multiyear"]
    assert len(rest) and (rest["t_R"] == rest["R"]).all() and (rest["t_out"] == rest["reason"]).all() and (rest["t_exit_i"] == rest["exit_i"]).all()


def test_history_and_statistics_use_the_trade_as_taken():
    df = _breakout_stock()
    _, ev, _ = pb.collect({"X": df})
    rows = [r for g in pb.history(ev).values() for r in g if r["kind"] == "multiyear"]
    assert len(rows) == 1
    row = rows[0]
    mine = ev[ev["kind"] == "multiyear"].iloc[0]
    assert row["trend"] is True and row["out"] == "trend" and abs(row["R"] - round(float(mine["t_R"]), 2)) < 1e-9 and row["exit_date"] == mine["t_exit_date"].strftime("%Y-%m-%d")
    assert all(r["trend"] is False for g in pb.history(ev).values() for r in g if r["kind"] != "multiyear")
    json.dumps(pb.history(ev))
    summary = pb._model_summary(pm.closed(ev, 1.5), None, None, 1.0)
    k = next(x for x in summary["by_kind"] if x["kind"] == "multiyear")
    assert k["n"] == 1 and k["avg_r"] == round(float(mine["t_R"]), 2) and k["win"] == 100.0


def test_live_rows_for_a_trend_exit_kind_carry_the_average_and_no_hit_probability():
    from tests.test_price_action import _daily
    waiting = _breakout_stock(days=(520, 300, 150, 215), ends=(200, 120, 135, 199))      # ends a hair under its two-year high
    others = {f"S{k:02d}": _daily(k) for k in range(14)}
    waiting.index = pd.bdate_range(end=others["S00"].index[-1], periods=len(waiting))        # the same last day as everyone else
    frames = {"X": waiting, **others}
    payload, _ = pb.build(frames, fo=set(frames))
    rows = [r for r in payload["setups"] if r["kind"] == "multiyear"]
    assert [r["symbol"] for r in rows] == ["X"] and rows[0]["status"] == "pending"
    for r in rows:
        assert r["trail"]["weeks"] == 30 and r["trail"]["sma"] > 0 and r["p"] is None and r["breakeven"] is None
        assert r["record"] is None or "win" in r["record"]
    for r in payload["setups"]:
        if r["kind"] != "multiyear":
            assert r["trail"] is None and r["breakeven"] is not None
    json.dumps(payload)
