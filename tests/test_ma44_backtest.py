import numpy as np
import pandas as pd

from signals import ma44_backtest as m


def _frame(days=520, drift=-0.0015, wave=0.04, period=17, seed=0, noise=0.002):
    rng = np.random.default_rng(seed)
    t = np.arange(days)
    close = 100 * np.exp(drift * t) * (1 + wave * np.sin(2 * np.pi * t / period)) * (1 + rng.normal(0, noise, days))
    open_ = np.concatenate([[close[0]], close[:-1]]) * (1 + rng.normal(0, noise, days))
    high = np.maximum(open_, close) * 1.008
    low = np.minimum(open_, close) * 0.992
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": 1e6}, index=pd.bdate_range("2021-01-04", periods=days))


def _arrays(o, h, l, c, sma):
    n = len(c)
    return {"o": np.array(o, float), "h": np.array(h, float), "l": np.array(l, float), "c": np.array(c, float), "sma": np.array(sma, float),
            "prev_sma": np.array(sma, float), "slope": np.full(n, -5.0), "wslope": np.full(n, -5.0), "wdown": np.ones(n)}


def test_short_trade_hits_the_2r_target_and_costs_come_out_of_r():
    # signal on bar 2 (high 101, sma 100): stop = 101 * 1.002 = 101.202, entry = open of bar 3 = 100 -> risk 1.202, target = 100 - 2.404
    a = _arrays(o=[99, 99, 99, 100, 99, 98], h=[100, 100, 101, 100.5, 99.5, 98.5], l=[98, 98, 99, 99, 97.4, 97], c=[99, 99, 99.5, 99.5, 98, 97.5], sma=[100] * 6)
    t = m._simulate(a, 2, m.Params(side="short", min_risk_pct=0.1))
    assert t["reason"] == "target" and abs(t["exit"] - (100 - 2 * 1.202)) < 1e-6
    assert abs(t["R"] - (2 - 0.001 * 100 / 1.202)) < 1e-6


def test_short_stop_wins_when_one_bar_hits_both_and_gaps_exit_at_the_open():
    a = _arrays(o=[99, 99, 99, 100, 100, 100], h=[100, 100, 101, 100.5, 102, 100], l=[98, 98, 99, 99, 97, 99], c=[99, 99, 99.5, 99.5, 99, 99.5], sma=[100] * 6)
    t = m._simulate(a, 2, m.Params(side="short", min_risk_pct=0.1))
    assert t["reason"] == "stop" and t["R"] < -1                                   # both stop and target inside bar 4: the stop wins
    a = _arrays(o=[99, 99, 99, 100, 103, 100], h=[100, 100, 101, 100.5, 103.5, 100], l=[98, 98, 99, 99, 102.5, 99], c=[99, 99, 99.5, 99.5, 103, 99.5], sma=[100] * 6)
    t = m._simulate(a, 2, m.Params(side="short", min_risk_pct=0.1))
    assert t["reason"] == "stop-gap" and t["exit"] == 103 and t["R"] < -2          # it opened above the stop: filled at the open, a loss bigger than 1R


def test_long_trade_is_the_mirror_and_ma_exit_leaves_on_a_reclaim():
    a = _arrays(o=[101, 101, 101, 100, 101, 102], h=[102, 102, 102, 101, 102.6, 103], l=[100, 100, 99, 99.5, 100.5, 101.5], c=[101, 101, 100.5, 100.5, 102.5, 102.8], sma=[100] * 6)
    t = m._simulate(a, 2, m.Params(side="long", min_risk_pct=0.1))
    assert t["reason"] == "target" and t["exit"] > t["entry"] and t["R"] > 1.9
    s = _arrays(o=[99, 99, 99, 100, 99.5, 99], h=[100, 100, 101, 100.4, 100.2, 99.5], l=[98, 98, 99, 99, 98.8, 98], c=[99, 99, 99.5, 99.5, 100.5, 98.5], sma=[100] * 6)
    t = m._simulate(s, 2, m.Params(side="short", exit_mode="ma", min_risk_pct=0.1))
    assert t["reason"] == "ma-reclaim" and t["exit"] == 100.5                      # a close above the 44 SMA ends the trade


def test_risk_outside_the_limits_skips_the_trade():
    a = _arrays(o=[99, 99, 99, 100, 99, 98], h=[100, 100, 101, 100.5, 99.5, 98.5], l=[98, 98, 99, 99, 97.4, 97], c=[99, 99, 99.5, 99.5, 98, 97.5], sma=[100] * 6)
    assert m._simulate(a, 2, m.Params(side="short", max_risk_pct=0.5)) is None


def test_signals_do_not_look_ahead():
    df = _frame()
    full = m.signals(df, m.Params())
    for cut in (260, 333, 400, 471):
        assert (m.signals(df.iloc[:cut], m.Params()) == full[:cut]).all()          # cutting off the future changes no earlier signal
    assert full.sum() > 3


def test_flat_and_wrong_way_trends_give_no_short_signals():
    flat = _frame(drift=0.0, wave=0.04)
    assert m.signals(flat, m.Params(side="short", min_slope_pct=3.0, weekly_min_slope_pct=3.0)).sum() == 0
    rising = _frame(drift=0.0015)
    assert m.signals(rising, m.Params(side="short")).sum() == 0                    # a rising market is not a short setup
    falling = _frame(drift=-0.0015)
    assert m.signals(falling, m.Params(side="long")).sum() == 0                    # and a falling one is not a long setup
    assert m.signals(rising, m.Params(side="long")).sum() > 0 and m.signals(falling, m.Params(side="short")).sum() > 0


def test_one_open_trade_per_stock_and_the_control_runs():
    frames = {"A": _frame(seed=1), "B": _frame(seed=2, period=21)}
    trades = m.run(frames, m.Params())
    assert len(trades) > 3
    for _, g in trades.groupby("symbol"):
        g = g.sort_values("entry_date")
        assert (g["entry_date"].iloc[1:].to_numpy() > g["exit_date"].iloc[:-1].to_numpy()).all()
    assert (trades["entry_date"] > trades["signal_date"]).all()                    # entered after the signal close
    control = m.run(frames, m.Params(), control=True)
    assert len(control) > 3


def test_stats_and_summary_text():
    frames = {f"S{k}": _frame(seed=k, period=15 + k) for k in range(6)}
    trades = m.run(frames, m.Params())
    s = m.stats(trades)
    assert s["n"] == len(trades) and 0 <= s["win"] <= 1 and s["max_dd_r"] >= 0
    text = m.summarize(frames, "short")
    assert "SHORT" in text and "Control" in text and "By year" in text and "survivorship" in text
    assert m.stats(pd.DataFrame(columns=trades.columns)) is None
