import numpy as np

from signals import movers, movers_backtest


def _candles(ranges: list[float], n_days: int = 120, seed: int = 5, noise: float = 0.1) -> dict:
    """One stock per entry of `ranges`: a random walk whose daily high-low range is that fraction of price (the widest range is last);
    `noise` scales the close-to-close moves relative to the range (small: measured ranges are ordered like the true ones)."""
    rng = np.random.default_rng(seed)
    dates = [(np.datetime64("2026-01-01") + i).astype(str) for i in range(n_days)]
    stocks = {}
    for k, r in enumerate(ranges):
        close = 100 * np.cumprod(1 + rng.normal(0, r * noise, n_days))
        open_ = np.concatenate([[close[0]], close[:-1]])
        stocks[f"S{k:02d}"] = {"d0": dates[0], "c": [[o, max(o, c) * (1 + r / 2), min(o, c) * (1 - r / 2), c] for o, c in zip(open_, close)]}
    return {"dates": dates, "stocks": stocks}


def _ranges(count=60):
    return [0.01 + 0.001 * k for k in range(count)]


def test_the_widest_ranges_come_first():
    c = _candles(_ranges())
    r = movers.movers_list(c, {s: 100.0 for s in c["stocks"]})
    assert r["universe"] == 60 and len(r["rows"]) == movers.TOP_N
    names = [x["symbol"] for x in r["rows"]]
    assert names[0] == "S59" and all(int(n[1:]) >= 45 for n in names)       # the list comes from the widest-range end
    atrs = [x["atr"] for x in r["rows"]]
    assert atrs == sorted(atrs, reverse=True) and atrs[-1] > 0              # and is sorted by the measured range
    for key in ("symbol", "atr", "close", "squeeze", "state", "m20", "from_hi20", "from_lo20", "turnover_cr"):
        assert key in r["rows"][0]


def test_illiquid_stocks_and_short_history_are_left_out():
    c = _candles(_ranges())
    turn = {s: 100.0 for s in c["stocks"]}
    turn["S59"] = 5.0
    r = movers.movers_list(c, turn)
    assert r["universe"] == 59 and "S59" not in [x["symbol"] for x in r["rows"]]
    assert movers.movers_list(_candles(_ranges(), n_days=40), turn) is None


def test_squeeze_state_follows_the_recent_range():
    c = _candles(_ranges())
    for s in c["stocks"].values():                               # the last 10 sessions go very quiet
        for row in s["c"][-10:]:
            row[1], row[2] = row[3] * 1.001, row[3] * 0.999
    r = movers.movers_list(c, {s: 100.0 for s in c["stocks"]})
    assert all(x["state"] == "squeezed" and x["squeeze"] < movers.SQUEEZED_BELOW for x in r["rows"])


def test_asof_uses_only_candles_up_to_that_date():
    c = _candles(_ranges(), n_days=100)
    for s in c["stocks"].values():                               # a wild last 3 sessions must not leak into an earlier as-of
        for row in s["c"][-3:]:
            row[1], row[2] = row[3] * 1.2, row[3] * 0.8
    turn = {s: 100.0 for s in c["stocks"]}
    early = movers.movers_list(c, turn, asof=c["dates"][-4])
    late = movers.movers_list(c, turn)
    assert early["asof"] == c["dates"][-4] and early["rows"][0]["atr"] < late["rows"][0]["atr"]


def test_backtest_finds_that_wide_range_stocks_make_bigger_moves():
    c = _candles([0.004 + 0.0008 * k for k in range(80)], n_days=160, noise=1.0)     # here price moves scale with the range, as in real markets
    turn = {s: 100.0 for s in c["stocks"]}
    rows = movers_backtest.run_backtest(c, turn, hold=10, threshold=0.10)
    assert rows and np.mean([r["picks"] for r in rows]) > np.mean([r["base"] for r in rows])
    text = movers_backtest.summarize(c, turn)
    assert "picks" in text and "base" in text and "NOT make" in text
