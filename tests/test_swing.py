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
