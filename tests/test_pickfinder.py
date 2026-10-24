import numpy as np
import pandas as pd
import pytest

from signals import pickfinder as pf


def make_bars(n_days=40, slope=0.0001, vol=100.0, start="2025-01-01"):
    """Business-day sessions of 75 five-minute bars. Each session opens at the
    previous close and climbs by `slope` per bar (close_k = open * (1 + slope*(k+1)))."""
    days = pd.bdate_range(start, periods=n_days)
    frames, prev = [], 100.0
    for d in days:
        idx = d + pd.Timedelta(hours=9, minutes=15) + pd.to_timedelta(np.arange(pf.SLOTS) * 5, unit="min")
        close = prev * (1 + slope * np.arange(1, pf.SLOTS + 1))
        open_ = np.r_[prev, close[:-1]]
        frames.append(pd.DataFrame({
            "open": open_, "high": np.maximum(open_, close), "low": np.minimum(open_, close),
            "close": close, "volume": vol,
        }, index=idx))
        prev = close[-1]
    return pd.concat(frames)


def test_slot_of_maps_clock_time_to_bar_index():
    assert pf.slot_of("09:15") == 0
    assert pf.slot_of("09:30") == 3
    assert pf.slot_of("15:10") == 71


def test_to_grid_shape_and_drops_short_sessions():
    bars = make_bars(5)
    short_day = bars.index.normalize().unique()[2]
    bars = bars.drop(bars[bars.index.normalize() == short_day].index[30:])      # only 30 bars survive
    days, g = pf.to_grid(bars)
    assert len(days) == 4 and short_day not in days
    assert g["close"].shape == (4, pf.SLOTS)


def test_to_grid_fills_holes_inside_a_kept_session():
    bars = make_bars(3)
    hole = bars.index[10]
    bars = bars.drop(hole)
    _, g = pf.to_grid(bars)
    assert np.isfinite(g["close"]).all() and np.isfinite(g["open"]).all()
    assert g["close"][0, 10] == pytest.approx(g["close"][0, 9])            # carried forward
    assert g["volume"][0, 10] == 0


def test_session_features_return_and_forward_return_are_exact():
    f = pf.session_features("AAA", make_bars(40))
    row = f[(f["t"] == "09:30")].iloc[-1]
    assert row["ret_pc"] == pytest.approx(0.0003)                           # 3 bars x 0.01%
    # entry = open of the 09:30 bar (= close of the 09:25 bar); exit = close of the 15:10 bar
    assert row["fwd_ret"] == pytest.approx((1 + 0.0072) / (1 + 0.0003) - 1)
    assert row["gap"] == pytest.approx(0.0)
    assert row["range_atr"] == pytest.approx(0.0003 / 0.0075, rel=1e-6)     # range so far / typical full-day range
    assert row["pdh_break"] and not row["pdl_break"]                        # each session climbs past the prior session's high


def test_features_use_only_completed_bars():
    bars = make_bars(70)                                                    # enough history for the 50-day average
    f1 = pf.session_features("AAA", bars)
    # rewrite the last session's afternoon (15:00 on): every input available at the
    # 09:30 decision - including the ATR / SMA / volume baselines, which also see
    # that session's own high, low, close and volume - must not move; the outcome must
    last = bars.index.normalize().unique()[-1]
    mask = (bars.index.normalize() == last) & (bars.index >= last + pd.Timedelta(hours=15))
    tampered = bars.copy()
    tampered.loc[mask, ["open", "high", "low", "close"]] *= 1.05
    tampered.loc[mask, "volume"] = 10_000.0
    f2 = pf.session_features("AAA", tampered)
    a = f1[(f1["t"] == "09:30") & (f1["date"] == last)].iloc[0]
    b = f2[(f2["t"] == "09:30") & (f2["date"] == last)].iloc[0]
    for col in ("atr_pct", "ret_pc", "ret_atr", "range_atr", "relvol", "pos", "gap", "dist20", "dist50"):
        assert a[col] == pytest.approx(b[col]), col
    assert a["fwd_ret"] != pytest.approx(b["fwd_ret"], abs=1e-3)


def test_relative_volume_spike_is_measured_against_the_same_window_on_prior_days():
    bars = make_bars(40)
    last = bars.index.normalize().unique()[-1]
    first_three = (bars.index.normalize() == last) & (bars.index < last + pd.Timedelta(hours=9, minutes=30))
    bars.loc[first_three, "volume"] = 500.0
    f = pf.session_features("AAA", bars)
    row = f[(f["t"] == "09:30") & (f["date"] == last)].iloc[0]
    assert row["relvol"] == pytest.approx(5.0)


def test_corporate_action_gaps_are_dropped():
    bars = make_bars(40)
    last = bars.index.normalize().unique()[-1]
    m = bars.index.normalize() == last
    bars.loc[m, ["open", "high", "low", "close"]] *= 0.5                    # a 2:1 split-style gap
    f = pf.session_features("AAA", bars)
    assert last not in set(f["date"])


def test_short_history_gives_empty_frame():
    assert pf.session_features("AAA", make_bars(10)).empty


def _cross_section(dates=("2025-03-03", "2025-03-04", "2025-03-05")):
    """5 stocks/day, one decision time. AAA is the strongest, high-volume, wide-range
    mover and goes on to gain 1%; ZZZ is the mirror image on the short side."""
    base = {"AAA": (3.0, 4.0, 3.0, 0.01), "BBB": (1.0, 1.0, 1.0, 0.0), "CCC": (0.0, 1.2, 1.1, 0.0),
            "DDD": (-1.0, 1.0, 1.0, 0.0), "ZZZ": (-3.0, 4.0, 3.0, -0.01)}
    rows = []
    for d in dates:
        for sym, (ret_atr, relvol, range_atr, fwd) in base.items():
            rows.append({"date": pd.Timestamp(d), "t": "10:00", "symbol": sym, "ret_atr": ret_atr, "relvol": relvol,
                         "range_atr": range_atr, "ret_pc": ret_atr * 0.01, "fwd_ret": fwd, "day_ret": fwd * 2,
                         "pdh_break": ret_atr > 2, "pdl_break": ret_atr < -2})
    return pf.add_scores(pd.DataFrame(rows))


def test_scores_rank_the_strong_mover_first_in_each_direction():
    df = _cross_section()
    day = df[df["date"] == pd.Timestamp("2025-03-03")]
    assert day.loc[day["long_score"].idxmax(), "symbol"] == "AAA"
    assert day.loc[day["short_score"].idxmax(), "symbol"] == "ZZZ"
    assert df["long_score"].between(0, 3).all()


def test_top_k_enters_each_stock_in_its_better_direction():
    df = _cross_section()
    day = df[df["date"] == pd.Timestamp("2025-03-03")]
    top = pf._top_k(day, 2)
    got = dict(zip(top["symbol"], top["d"]))
    assert got == {"AAA": 1.0, "ZZZ": -1.0}


def test_screen_recall_and_net_return_on_the_synthetic_cross_section():
    df = _cross_section()
    picks = pd.DataFrame({"date": ["2025-03-03", "2025-03-04", "2025-03-05"], "symbol": "AAA", "side": "Call", "pnl": 1.0})
    df = pf.attach_picks(df, picks)
    text = pf.screen_test(df, ks=(2,))
    assert "symbol+direction 100.0%" in text
    # both top-2 picks gain 1% in their direction, minus the 0.10% cost
    assert "mean 0.90%" in text
    wrong_way = pd.DataFrame({"date": ["2025-03-03"], "symbol": "AAA", "side": "Put", "pnl": 1.0})
    text2 = pf.screen_test(pf.attach_picks(_cross_section(), wrong_way), ks=(2,))
    assert "symbol 100.0%, symbol+direction 0.0%" in text2


def test_pick_signature_reports_a_top_decile_pick():
    df = pf.attach_picks(_cross_section(), pd.DataFrame(
        {"date": ["2025-03-03", "2025-03-04"], "symbol": "AAA", "side": "Call", "pnl": 1.0}))
    text = pf.pick_signature(df)
    assert "[10:00] n=2" in text
    assert "top decile 100%" in text


def test_coverage_lists_symbols_without_data():
    df = _cross_section()
    picks = pd.DataFrame({"date": ["2025-03-03", "2025-03-03"], "symbol": ["AAA", "NOPE"], "side": "Call", "pnl": 1.0})
    text = pf.coverage(picks, df)
    assert "with morning data: 1 (50%)" in text and "NOPE" in text
