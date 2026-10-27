import numpy as np
import pandas as pd

from research import dataset as ds, walkforward as wf


def _synth(seed, days=45):
    rng = np.random.default_rng(seed)
    frames, price = [], 100.0
    for d in pd.bdate_range("2025-01-01", periods=days):
        idx = pd.date_range(d + pd.Timedelta(hours=9, minutes=15), periods=75, freq="5min")
        c = price * np.exp(np.cumsum(rng.normal(0, 0.0012, 75)))
        o = np.concatenate([[price], c[:-1]])
        sp = np.abs(rng.normal(0, 0.0006, 75))
        frames.append(pd.DataFrame({"open": o, "high": np.maximum(o, c) * (1 + sp), "low": np.minimum(o, c) * (1 - sp),
                                    "close": c, "volume": rng.integers(1000, 5000, 75)}, index=idx))
        price = c[-1]
    return pd.concat(frames)


def test_events_have_features_labels_and_costs_only_pnl_on_a_random_walk():
    data = {f"S{k}": _synth(k) for k in range(6)}
    ev = ds.build_events(data, {f"S{k}": "A" for k in range(6)}, processes=1)
    assert len(ev) > 300 and set(ds.ENTRIES) == set(ev["ent"].unique())
    assert all(f in ev.columns for f in ds.FEATURES)
    for g, var in enumerate(ds.VARIANTS):
        for side in "LS":
            assert abs(ev[f"pnl_{side}{g}"].mean() + ds.COST_PCT) < 0.2      # no edge: only the cost remains
            assert 0 <= ev[f"win_{side}{g}"].mean() <= 1
            assert set(ev[f"ex_{side}{g}"].unique()) <= {0.0, 1.0, 2.0, 3.0}
            cap = ds.CAP_IDX[var["pol"]] - ev["ent"]
            assert (ev[f"bars_{side}{g}"] <= cap).all()                       # never held past the policy's cap
    # a flat-12:00 variant is not held past 12:00; the dynamic exit fires sometimes and ends trades earlier on average
    assert ev["bars_L5"].max() <= 32 - 9
    assert (ev["ex_L4"] == 2).any() and ev["bars_L4"].mean() < ev["bars_L5"].mean()


def test_features_do_not_look_ahead_but_labels_do_depend_on_the_future():
    base = {"A": _synth(1)}
    other = {"A": base["A"].copy()}
    day = other["A"].index.normalize().unique()[30]
    late = other["A"].index.normalize() == day
    rows = np.flatnonzero(late)[30:]                        # bars after 11:45
    other["A"].iloc[rows, other["A"].columns.get_loc("close")] *= 1.05
    other["A"].iloc[rows, other["A"].columns.get_loc("high")] *= 1.05
    a = ds.build_events(base, None, processes=1)
    b = ds.build_events(other, None, processes=1)
    ra, rb = a[a["date"] == day].reset_index(drop=True), b[b["date"] == day].reset_index(drop=True)
    for f in ds.FEATURES:
        if f.startswith(("mkt_", "rank_", "sec_", "rs_")):
            continue
        assert np.allclose(ra[f].to_numpy(dtype=float), rb[f].to_numpy(dtype=float), equal_nan=True), f
    pnl_cols = [c for c in ra.columns if c.startswith("pnl_")]
    assert not np.allclose(ra[pnl_cols].to_numpy(dtype=float), rb[pnl_cols].to_numpy(dtype=float))


def test_walk_forward_only_scores_later_days_and_search_reports():
    data = {f"S{k}": _synth(k, days=140) for k in range(8)}
    ev = ds.build_events(data, None, processes=1)
    oos = wf.walk_forward(ev, geoms=[0], sides=("L",), params={"n_estimators": 20})
    dates = sorted(ev["date"].unique())
    assert oos["date"].min() >= dates[int(len(dates) * 0.4)] and oos["p_L0"].notna().all()
    text = wf.search(oos, min_n=10)
    assert "holdout" in text and "configurations tried" in text
    t = wf.pick_trades(oos, "L", 0, 0.0, 2)
    assert t.groupby("date").size().max() <= 2 and not t.duplicated(["date", "symbol"]).any()


def test_regime_report_runs_and_describes_the_dip_subset():
    from research import regime
    data = {f"S{k}": _synth(k, days=60) for k in range(8)}
    ev = ds.build_events(data, None, processes=1)
    ev["volx"] = ev["volx"].fillna(1.5) + 1.0
    text = regime.regime_report(ev, variants=(14,))
    assert "dip subset" in text and "mkt_r1" in text and "quintile" in text
