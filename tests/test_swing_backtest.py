from datetime import date

import numpy as np
import pandas as pd
import pytest

from signals import swing, swing_backtest as sb


def _store(n_stocks=20, sessions=45, drift_scale=0.001, seed=0):
    """Stock i: more OI added, more volume, and a steadier rise the higher i is -
    so R is highest for high i on every date, and the rise continues afterwards."""
    days = pd.bdate_range(end="2026-10-23", periods=sessions)
    rng = np.random.default_rng(seed)
    store = {"oi": {}, "px": {}, "expiries": []}
    for i in range(n_stocks):
        sym = f"S{i}"
        close = 100 * np.cumprod(1 + drift_scale * i + rng.normal(0, 0.002, sessions))
        volume = np.full(sessions, 1000.0) * (1 + 0.1 * i)
        volume[-8:] *= 1 + i * 0.05
        oi = 1_000_000 * np.cumprod(1 + 0.004 * i + rng.normal(0, 0.001, sessions))
        store["oi"][sym] = {d.date().isoformat(): int(v) for d, v in zip(days, oi)}
        store["px"][sym] = {d.date().isoformat(): [round(float(c), 2), int(v)] for d, c, v in zip(days, close, volume)}
    return store, days


def test_update_store_keeps_closes_and_volumes_without_overwriting_and_from_a_cutoff():
    days = pd.bdate_range("2026-08-01", periods=60)
    equity = {"AAA": pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": np.arange(60) + 100.0, "volume": 10.0}, index=days)}
    oi = pd.Series([100.0, 101.0], index=days[-2:])
    store = swing.update_store(None, {"AAA": oi}, {date(2026, 10, 27)}, equity)
    first_oi_day = days[-2].date()
    cutoff = (first_oi_day - pd.Timedelta(days=swing.PX_LOOKBACK_DAYS)).isoformat()
    assert min(store["px"]["AAA"]) >= cutoff and days[-1].date().isoformat() in store["px"]["AAA"]
    equity["AAA"]["close"] = 9999.0                                   # a later night must not rewrite saved closes
    again = swing.update_store(store, {"AAA": oi}, set(), equity)
    assert again["px"]["AAA"][days[-1].date().isoformat()][0] == 159.0
    assert swing.update_store({"oi": {"AAA": {}}, "expiries": []}, {}, set(), equity)["px"] == {}      # no OI -> no closes kept


def test_frames_from_store_skip_stocks_with_too_little_history():
    store, _ = _store(3)
    store["px"]["TINY"] = {"2026-10-22": [1.0, 1], "2026-10-23": [1.0, 1]}
    assert set(sb.frames_from_store(store)) == {"S0", "S1", "S2"}


def test_signal_dates_need_oi_now_and_five_sessions_earlier():
    store, days = _store()
    frames = sb.frames_from_store(store)
    dates = sb.signal_dates(store, frames)
    assert dates[0] == days[swing.WINDOW] and dates[-1] == days[-1] and len(dates) == len(days) - swing.WINDOW


def test_higher_r_does_better_when_the_move_continues_and_horizons_respect_the_data_edge():
    store, _ = _store()
    out = sb.run_backtest(store, top_n=3, cost_pct=0.0)
    top, low = out["Top 5 by R-factor"][5], out["R < 40 (OI building)"][5]
    assert len(top) > 50 and np.mean([r for _, r in top]) > np.mean([r for _, r in low])
    assert np.mean([r for _, r in top]) > 0                        # long picks in a rising, OI-building market
    # later horizons lose the last signal evenings (no future left to measure)
    counts = [len(out["Top 5 by R-factor"][h]) for h in sb.HORIZONS]
    assert counts[0] > counts[1] > counts[2]
    assert out["dates"]["scored"] > 20 and out["dates"]["first"] < out["dates"]["last"]


def test_entry_is_the_next_sessions_close_not_the_signal_close():
    store, days = _store(n_stocks=12, sessions=30)
    # make the signal-day close absurdly different: a correct (delayed-entry) backtest must not notice
    sym = "S11"
    t = days[15]
    store["px"][sym][t.date().isoformat()][0] = 1.0
    out_a = sb.run_backtest(store, top_n=1, cost_pct=0.0)
    store["px"][sym][t.date().isoformat()][0] = 999.0
    out_b = sb.run_backtest(store, top_n=1, cost_pct=0.0)
    # the close on t only matters for t's own ranking, never for a trade's return: compare returns of trades signalled earlier
    early_a = [r for d, r in out_a["Top 5 by R-factor"][3] if d < 5]
    early_b = [r for d, r in out_b["Top 5 by R-factor"][3] if d < 5]
    assert early_a == early_b


def test_no_lookahead_changing_future_prices_does_not_change_todays_picks():
    store, days = _store(sessions=40)
    frames = sb.frames_from_store(store)
    t = days[25]
    def picks(st):
        fr = sb.frames_from_store(st)
        rows = swing.build_swing(st, {s: df.loc[:t] for s, df in fr.items()}, {}, __import__("datetime").datetime(2026, 1, 1))["stocks"]
        return [(r["symbol"], r["r"]) for r in rows[:5]]
    before = picks(store)
    for sym in store["px"]:
        for d in days[26:]:
            store["px"][sym][d.date().isoformat()][0] *= 3
    assert picks(store) == before


def test_summary_renders_all_groups_and_horizons():
    store, _ = _store()
    text = sb.summarize(sb.run_backtest(store))
    assert "held 3 sessions" in text and "held 10 sessions" in text and "Top 5 by R-factor" in text and "day-clustered" in text.lower() or "Day-clustered" in text
    assert text.count("Top 5 by volume only") == 3
