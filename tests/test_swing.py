from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

from signals import swing

NOW = datetime(2026, 10, 24, 17, 45)


def _fut(days, oi_values):
    idx = pd.DatetimeIndex(days)
    return pd.DataFrame({"close": 100.0, "open_interest": oi_values}, index=idx)


def _equity(days, closes, volumes):
    return pd.DataFrame({"open": closes, "high": closes, "low": closes, "close": closes, "volume": volumes},
                        index=pd.DatetimeIndex(days))


def test_combined_oi_sums_contracts_only_on_dates_both_have():
    d = pd.bdate_range("2026-10-01", periods=5)
    near = _fut(d, [100, 110, 120, 130, 140])
    nxt = _fut(d[2:], [10, 20, 30])                           # listed later: no step before it appears
    out = swing.combined_oi([near, nxt])
    assert list(out.index) == list(d[2:]) and list(out.values) == [130, 150, 170]
    assert list(swing.combined_oi([near]).values) == [100, 110, 120, 130, 140]
    assert swing.combined_oi([None, pd.DataFrame()]).empty


def test_store_never_overwrites_a_saved_date_and_keeps_expiries():
    d = pd.bdate_range("2026-10-01", periods=3)
    store = swing.update_store(None, {"AAA": pd.Series([100.0, 110.0, 120.0], index=d)}, {date(2026, 10, 27)})
    later = swing.update_store(store, {"AAA": pd.Series([999.0, 999.0, 999.0, 130.0], index=pd.bdate_range("2026-10-01", periods=4))},
                               {date(2026, 11, 24)})
    assert later["oi"]["AAA"] == {"2026-10-01": 100, "2026-10-02": 110, "2026-10-05": 120, "2026-10-06": 130}
    assert later["expiries"] == ["2026-10-27", "2026-11-24"]


def _world(n_stocks=14, sessions=30, expiries=None):
    days = pd.bdate_range(end="2026-10-23", periods=sessions)
    store = {"oi": {}, "expiries": expiries or []}
    equity, industries = {}, {}
    for i in range(n_stocks):
        sym = f"S{i}"
        industries[sym] = "Banks" if i % 2 else "IT"
        closes = 100 * (1 + 0.002 * i * np.arange(sessions) / sessions * 5)       # higher i: bigger rise
        volumes = np.full(sessions, 1000.0); volumes[-1] = 1000 * (1 + i * 0.2)    # higher i: bigger volume today
        equity[sym] = _equity(days, closes, volumes)
        oi = 1_000_000 * (1 + 0.004 * i * np.arange(sessions) / 5)                  # higher i: more OI added
        store["oi"][sym] = {d.date().isoformat(): int(v) for d, v in zip(days, oi)}
    return store, equity, industries, days


def test_swing_r_is_highest_where_volume_oi_and_price_move_are_highest():
    store, equity, industries, _ = _world()
    out = swing.build_swing(store, equity, industries, NOW)
    rows = {r["symbol"]: r for r in out["stocks"]}
    assert out["asof"] == "2026-10-23" and out["stock_count"] == 14 and out["window"] == 5
    assert rows["S13"]["r"] == max(r["r"] for r in rows.values()) and rows["S13"]["bias"] == "long"
    assert rows["S13"]["r"] > rows["S7"]["r"] > rows["S1"]["r"]
    assert out["stocks"][0]["symbol"] == "S13"                                  # sorted by R
    assert rows["S13"]["oi_chg"] > 0 and rows["S13"]["vol_vs_avg"] > 1 and len(rows["S13"]["series"]) == 20


def test_oi_falling_scores_zero_and_short_buildup_is_tagged():
    store, equity, industries, days = _world()
    # S0: price falling with OI rising -> short buildup, scored; S1: OI falling -> unwind, R = 0
    equity["S0"]["close"] = 100 * (1 - 0.01 * np.arange(len(days)))
    store["oi"]["S0"] = {d.date().isoformat(): 1_000_000 + 30_000 * i for i, d in enumerate(days)}
    equity["S0"].iloc[-1, equity["S0"].columns.get_loc("volume")] = 9_000.0      # heavy volume today (the lowest rank on any leg gives R = 0)
    store["oi"]["S1"] = {d.date().isoformat(): 2_000_000 - 20_000 * i for i, d in enumerate(days)}
    rows = {r["symbol"]: r for r in swing.build_swing(store, equity, industries, NOW)["stocks"]}
    assert rows["S0"]["bias"] == "short" and rows["S0"]["r"] > 0
    assert rows["S1"]["r"] == 0 and rows["S1"]["bias"] in ("cover", "unwind")


def test_window_straddling_an_expiry_is_flagged_roll():
    store, equity, industries, days = _world(expiries=["2026-10-20"])             # inside the last 5 sessions
    rows = {r["symbol"]: r for r in swing.build_swing(store, equity, industries, NOW)["stocks"]}
    assert all(r["roll"] for r in rows.values())
    store["expiries"] = ["2026-09-29"]
    assert not any(r["roll"] for r in swing.build_swing(store, equity, industries, NOW)["stocks"])


def test_stocks_without_enough_history_or_todays_oi_are_skipped_and_few_stocks_are_unranked():
    store, equity, industries, days = _world()
    del store["oi"]["S3"][days[-1].date().isoformat()]                            # no OI for the latest session
    store["oi"]["NEW"] = {days[-1].date().isoformat(): 5}                          # one day only
    equity["NEW"] = _equity(days[-3:], [1, 1, 1], [1, 1, 1])
    out = swing.build_swing(store, equity, industries, NOW)
    assert {r["symbol"] for r in out["stocks"]} == {f"S{i}" for i in range(14)} - {"S3"}
    few = swing.build_swing({"oi": {k: v for k, v in list(store["oi"].items())[:4]}, "expiries": []}, equity, industries, NOW)
    assert all(r["r"] is None for r in few["stocks"])


def test_sectors_average_the_five_day_move():
    store, equity, industries, _ = _world()
    out = swing.build_swing(store, equity, industries, NOW)
    names = {s["name"]: s for s in out["sectors"]}
    assert set(names) == {"Banks", "IT"} and names["Banks"]["stocks"] == 7
    assert out["sectors"][0]["change_pct"] >= out["sectors"][1]["change_pct"]


def test_snapshot_is_json_serialisable_with_plain_python_numbers():
    import json
    store, equity, industries, _ = _world()
    out = swing.build_swing(store, equity, industries, NOW)
    text = json.dumps(out)                                   # numpy scalars would raise here (and in the nightly job)
    assert json.loads(text)["stock_count"] == out["stock_count"]
    assert all(type(r["chg_5d"]) is float and type(r["r"]) is int for r in out["stocks"])


def test_previous_monthly_expiry_is_the_last_tuesday_of_the_month_before():
    assert swing.previous_monthly_expiry(date(2026, 10, 27)) == date(2026, 9, 29)
    assert swing.previous_monthly_expiry(date(2026, 11, 24)) == date(2026, 10, 27)
    assert swing.previous_monthly_expiry(date(2027, 1, 26)) == date(2026, 12, 29)


def test_seed_floor_drops_history_before_the_last_expiry_once_and_never_again():
    days = pd.bdate_range("2026-09-20", "2026-10-09")
    series = pd.Series(np.arange(len(days)) + 100.0, index=days)
    stale = {"oi": {"AAA": {"2026-09-21": 5, "2026-09-29": 6, "2026-09-30": 7}}, "expiries": []}   # an old store, seeded without a floor
    store = swing.update_store(stale, {"AAA": series}, set(), None, seed_floor=date(2026, 9, 29))
    saved = store["oi"]["AAA"]
    assert min(saved) == "2026-09-30" and saved["2026-09-30"] == 7                   # kept what was there after the floor
    assert "2026-09-29" not in saved and store["seed_floor"] == "2026-09-29"
    # a later night with a different floor (a new month) must not wipe the saved history
    again = swing.update_store(store, {}, set(), None, seed_floor=date(2026, 10, 27))
    assert min(again["oi"]["AAA"]) == "2026-09-30" and again["seed_floor"] == "2026-09-29"
    # a brand-new store only takes dates after the floor
    fresh = swing.update_store(None, {"AAA": series}, set(), None, seed_floor=date(2026, 10, 1))
    assert min(fresh["oi"]["AAA"]) == "2026-10-02"


def test_as_of_snapshots_are_causal_and_keep_stocks_without_saved_oi():
    store, equity, industries, days = _world(sessions=40)
    cut = days[-8]
    earlier = {s: df[df.index.normalize() <= cut] for s, df in equity.items()}
    now_ = swing.build_swing(store, equity, industries, NOW)
    then = swing.build_swing(store, earlier, industries, NOW)
    assert then["asof"] == cut.date().isoformat()
    rt = {r["symbol"]: r for r in then["stocks"]}
    # later bars do not change the earlier day's numbers
    again = swing.build_swing(store, {s: df[df.index.normalize() <= cut] for s, df in equity.items()}, industries, NOW)
    assert {r["symbol"]: r["r"] for r in again["stocks"]} == {s: r["r"] for s, r in rt.items()}
    # OI saved only for the last 10 sessions: the old date has no rows unless missing OI is allowed
    for sym in store["oi"]:
        store["oi"][sym] = {d: v for d, v in store["oi"][sym].items() if d >= days[-10].date().isoformat()}
    old = {s: df[df.index.normalize() <= days[-20]] for s, df in equity.items()}
    assert swing.build_swing(store, old, industries, NOW)["stock_count"] == 0
    loose = swing.build_swing(store, old, industries, NOW, allow_missing_oi=True)
    assert loose["stock_count"] == 14 and all(r["r"] is None and r["oi_chg"] is None and r["series"] == [] for r in loose["stocks"])
    assert now_["stock_count"] == 14
