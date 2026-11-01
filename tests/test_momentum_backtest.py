import numpy as np

from signals import momentum, momentum_backtest


def _candles(n_days=150, count=60, seed=3, market_drift=0.001, spread=0.0003, noise=0.01):
    rng = np.random.default_rng(seed)
    dates = [(np.datetime64("2026-01-01") + i).astype(str) for i in range(n_days)]
    stocks = {}
    for i in range(count):
        close = 100 * np.cumprod(1 + market_drift + (i - count / 2) * spread + rng.normal(0, noise, n_days))
        open_ = np.concatenate([[close[0]], close[:-1]])
        stocks[f"S{i:02d}"] = {"d0": dates[0], "c": [[o, max(o, c) * 1.002, min(o, c) * 0.998, c] for o, c in zip(open_, close)]}
    return {"dates": dates, "stocks": stocks}


def test_persistent_trends_make_the_picks_beat_random_picks():
    c = _candles()
    turn = {s: 100.0 for s in c["stocks"]}
    df = momentum_backtest.run_backtest(c, turn, hold=5)
    assert df["date"].iloc[0] == c["dates"][max(momentum.LOOKBACKS)]          # first signal needs 60 sessions of history
    assert df["date"].iloc[-1] == c["dates"][-1 - 5]                          # the last signal leaves room for the 5-session hold
    r = momentum_backtest.summarize_group(df[df["up"]], 5)
    assert r["accuracy"] > r["random_accuracy"] and r["return_edge"] > 0
    assert all(len(p) == momentum.TOP_N for p in df["picks"])


def test_illiquid_stocks_never_enter_the_universe():
    c = _candles(count=70)                                  # the backtest needs at least 60 liquid stocks per day
    turn = {s: (1.0 if s in ("S68", "S69") else 100.0) for s in c["stocks"]}
    df = momentum_backtest.run_backtest(c, turn, hold=5)
    assert len(df) and all(len(u) <= 68 for u in df["universe"])


def test_summary_text_reports_accuracy_and_the_baseline():
    c = _candles()
    text = momentum_backtest.summarize(c, {s: 100.0 for s in c["stocks"]})
    assert "accuracy" in text and "vs random" in text and "held 5 sessions" in text and "held 10 sessions" in text
