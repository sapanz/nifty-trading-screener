import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

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


def test_strongest_are_the_highest_drifts():
    c = _candles(_universe(0.002))
    turn = {s: 100.0 for s in c["stocks"]}
    r = momentum.momentum_lists(c, turn)
    assert r["universe"] == 30
    assert [x["symbol"] for x in r["strong"]][:3] == ["S29", "S28", "S27"]
    assert r["strong"][0]["score"] > r["strong"][-1]["score"]
    assert len(r["strong"]) == momentum.TOP_N and "weak" not in r


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


# ---------------------------------------------------------------- the Momentum top-10 long strategy card
def test_strategy_trade_plan_follows_the_backtested_rules():
    import sys
    sys.path.insert(0, str(ROOT))
    from signals import momentum
    row = {"symbol": "X", "close": 200.0, "atr": 3.0}
    plan = momentum.trade_plan(row)
    assert plan["stop"] == round(200 * (1 - 4 * 0.03), 2) == 176.0 and plan["risk_pct"] == 12.0 and plan["target"] == round(200 * (1 + 8 * 0.03), 2) and plan["hold"] == 20
    assert momentum.exit_day("2026-11-20") == "2026-12-18"                      # Friday + 20 weekdays
    assert momentum.exit_day("2026-11-19", 1) == "2026-11-20" and momentum.exit_day("2026-11-20", 1) == "2026-11-23"


def test_the_page_twin_uses_the_same_numbers_as_the_python_constants():
    from signals import momentum
    html = (ROOT / "docs" / "index.html").read_text()
    assert f"var MOM_RULE = {{ hold: {momentum.HOLD_SESSIONS}, stopAtr: {momentum.STOP_ATR:g}, targetAtr: {momentum.TARGET_ATR:g}, gapCap: {momentum.GAP_CAP_PCT:g} }};" in html
    assert 'id="momPlan"' in html and "Strategy: Momentum top-10 long" in html


def test_position_size_is_the_smaller_of_an_equal_share_and_the_risk_budget():
    row = {"symbol": "X", "close": 200.0, "atr": 3.0}                          # stop 12% below
    assert momentum.trade_plan(row)["buy_limit"] == 206.0
    p = momentum.position(row, 1_000_000, 1.0)                                  # equal share 100,000; risk budget 10,000 / 0.12 = 83,333 -> 416 shares
    assert p == {"qty": 416, "amount": 83200.0, "loss_if_stopped": 9984.0}
    assert momentum.position({"symbol": "Y", "close": 200.0, "atr": 1.0}, 1_000_000, 1.0)["qty"] == 500   # stop 4%: the equal share (100,000) is the smaller
    assert momentum.position({"symbol": "Z", "close": 11000.0, "atr": 3.0}, 100_000, 1.0)["qty"] == 0      # one share costs more than the amount
