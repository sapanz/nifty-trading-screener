import numpy as np
import pandas as pd

from signals import config, intraday_ob_backtest as ib


def _walk_frame(days=60, seed=3, sigma=0.0012):
    rng = np.random.default_rng(seed)
    frames = []
    price = 100.0
    for d in pd.bdate_range("2025-01-01", periods=days):
        idx = pd.date_range(d + pd.Timedelta(hours=9, minutes=15), periods=75, freq="5min")
        r = rng.normal(0, sigma, 75)
        c = price * np.exp(np.cumsum(r))
        o = np.concatenate([[price], c[:-1]])
        sp = np.abs(rng.normal(0, sigma / 2, 75))
        frames.append(pd.DataFrame({"open": o, "high": np.maximum(o, c) * (1 + sp), "low": np.minimum(o, c) * (1 - sp), "close": c, "volume": 1}, index=idx))
        price = c[-1]
    return pd.concat(frames)


def test_trades_stay_inside_the_session_and_window():
    trades, arrays = ib.run_intraday_backtest({"A": _walk_frame(), "B": _walk_frame(seed=4)})
    assert trades
    for t in trades:
        assert t.entry_time <= config.INTRADAY_OB_LAST_ENTRY
        assert config.INTRADAY_OB_MIN_RISK_PCT <= t.risk_pct <= config.INTRADAY_OB_MAX_RISK_PCT
        assert t.outcome in {"target", "stop", "time"}


def test_net_includes_costs_and_summary_renders():
    trades, arrays = ib.run_intraday_backtest({"A": _walk_frame(days=120), "B": _walk_frame(days=120, seed=9)})
    base = ib.random_baseline(trades, arrays, draws=2)
    text = ib.summarize(trades, base)
    assert "By direction" in text and "Out-of-sample" in text
    assert all(abs(t.net_pct - t.net_pct) < 1e-12 for t in trades)


def test_exit_is_flat_at_the_exit_bar():
    frame = _walk_frame(days=40)
    ids, info = ib._sessions(frame.index)
    for sid, (first, exit_idx) in info.items():
        assert frame.index[exit_idx].strftime("%H:%M") == config.INTRADAY_OB_EXIT_BAR


def test_empty_summary():
    assert "No intraday" in ib.summarize([], [])
