import numpy as np

from signals import momentum


def _candles(paths: dict[str, list[float]]) -> dict:
    n = len(next(iter(paths.values())))
    dates = [f"2026-01-{i + 1:02d}" if i < 31 else f"2026-02-{i - 30:02d}" for i in range(n)]
    stocks = {}
    for symbol, closes in paths.items():
        c = np.asarray(closes, float)
        stocks[symbol] = {"d0": dates[0], "c": [[x, x * 1.01, x * 0.99, x] for x in c]}
    return {"dates": dates, "stocks": stocks}


def _universe(trend: float, n_days: int = 80, count: int = 30) -> dict[str, list[float]]:
    """count stocks drifting at `trend` per day plus a steady spread of extra drift, so ranks are unambiguous."""
    return {f"S{i:02d}": list(100 * np.cumprod(np.full(n_days, 1 + trend + (i - count / 2) * 0.0004))) for i in range(count)}


def test_strongest_are_the_highest_drifts_and_weakest_the_lowest():
    c = _candles(_universe(0.002))
    turn = {s: 100.0 for s in c["stocks"]}
    r = momentum.momentum_lists(c, turn)
    assert r["universe"] == 30
    assert [x["symbol"] for x in r["strong"]][:3] == ["S29", "S28", "S27"]
    assert [x["symbol"] for x in r["weak"]][:3] == ["S00", "S01", "S02"]
    assert r["strong"][0]["score"] > r["strong"][-1]["score"] > r["weak"][0]["score"]
    assert len(r["strong"]) == len(r["weak"]) == momentum.TOP_N


def test_market_switch_follows_the_equal_weight_index():
    up = momentum.momentum_lists(_candles(_universe(0.004)), {f"S{i:02d}": 100.0 for i in range(30)})
    down = momentum.momentum_lists(_candles(_universe(-0.004)), {f"S{i:02d}": 100.0 for i in range(30)})
    assert up["market"]["up"] and up["market"]["vs_ma_pct"] > 0
    assert not down["market"]["up"] and down["market"]["vs_ma_pct"] < 0


def test_illiquid_stocks_are_left_out_and_short_history_returns_none():
    c = _candles(_universe(0.002))
    turn = {s: 100.0 for s in c["stocks"]}
    turn["S29"] = 5.0
    r = momentum.momentum_lists(c, turn)
    assert r["universe"] == 29 and "S29" not in [x["symbol"] for x in r["strong"]]
    assert momentum.momentum_lists(_candles(_universe(0.002, n_days=40)), turn) is None


def test_asof_uses_only_candles_up_to_that_date():
    paths = _universe(0.002, n_days=90)
    for s in paths:                                         # a crash on the last 5 days must not leak into an earlier as-of
        paths[s][-5:] = [x * 0.5 for x in paths[s][-5:]]
    c = _candles(paths)
    turn = {s: 100.0 for s in c["stocks"]}
    early = momentum.momentum_lists(c, turn, asof=c["dates"][-6])
    late = momentum.momentum_lists(c, turn)
    assert early["asof"] == c["dates"][-6] and early["market"]["up"] and not late["market"]["up"]
    assert early["strong"][0]["m5"] > 0 > late["strong"][0]["m5"]


def test_rows_carry_the_columns_the_page_shows():
    c = _candles(_universe(0.002))
    row = momentum.momentum_lists(c, {s: 100.0 for s in c["stocks"]})["strong"][0]
    for key in ("symbol", "score", "close", "m5", "m20", "m60", "atr", "from_hi20", "from_lo20", "turnover_cr"):
        assert key in row
    assert 0 < row["atr"] < 5 and row["from_hi20"] <= 0 <= row["from_lo20"]
