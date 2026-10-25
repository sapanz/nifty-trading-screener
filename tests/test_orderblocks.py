import numpy as np
import pandas as pd

from signals import config, orderblock_backtest as obb
from signals.orderblocks import active_blocks, find_order_blocks, first_touch


def _series(closes, spread=0.5):
    """Candles from closes: open = previous close, small wicks."""
    c = np.array(closes, dtype=float)
    o = np.concatenate([[c[0]], c[:-1]])
    h = np.maximum(o, c) + spread
    l = np.minimum(o, c) - spread
    return o, h, l, c


def _bullish_path():
    # flat chop around 100 with a distinct swing high at 102, a down candle (the block), then a thrust
    base = [100, 101, 100, 101, 100, 101, 102, 101, 100.5, 100, 100.5, 100, 100.5, 100, 100.5, 100, 100.5, 100, 100.5, 100]
    path = base + [99, 108, 111, 112, 112.5]
    return _series(path)


def test_bullish_block_is_the_last_down_candle_before_the_break():
    o, h, l, c = _bullish_path()
    blocks = [b for b in find_order_blocks(o, h, l, c) if b.direction == "bull"]
    assert blocks, "a thrust through the swing high should leave a demand zone"
    b = blocks[0]
    assert c[b.idx] < o[b.idx]                      # a down candle
    assert b.idx < b.formed_idx                     # known only after the break closes
    assert b.low == l[b.idx] and b.high == h[b.idx]
    assert c[b.formed_idx] > 102.5                    # the break closed above the swing high


def test_bearish_block_mirrors_bullish():
    o, h, l, c = _bullish_path()
    mirror = 200 - c
    mo, mh, ml, mc = _series(mirror)
    bears = [b for b in find_order_blocks(mo, mh, ml, mc) if b.direction == "bear"]
    assert bears
    assert mc[bears[0].idx] > mo[bears[0].idx]      # an up candle


def test_no_block_without_displacement():
    c = [100 + 0.3 * np.sin(i / 2) for i in range(60)] + [100.6, 100.9, 101.2]
    o, h, l, cc = _series(c, spread=0.4)
    assert find_order_blocks(o, h, l, cc) == []


def test_no_lookahead_blocks_do_not_change_when_future_bars_are_appended():
    o, h, l, c = _bullish_path()
    n = len(c)
    full = _series(list(c) + [113, 112, 111, 110, 109, 108])
    early = [(b.direction, b.idx, b.formed_idx) for b in find_order_blocks(o, h, l, c)]
    later = [(b.direction, b.idx, b.formed_idx) for b in find_order_blocks(*full) if b.formed_idx < n]
    assert early == later


def test_first_touch_and_active():
    o, h, l, c = _bullish_path()
    b = next(b for b in find_order_blocks(o, h, l, c) if b.direction == "bull")
    assert first_touch(b, o, h, l) is None          # price never came back
    assert any(x.idx == b.idx for x in active_blocks(o, h, l, c))
    # price returns to the zone
    o2, h2, l2, c2 = _series(list(c) + [b.high + 3, b.high + 1, b.high - 0.2])
    t = first_touch(b, o2, h2, l2)
    assert t is not None and t > b.formed_idx
    assert not any(x.idx == b.idx for x in active_blocks(o2, h2, l2, c2))


def test_block_expires():
    o, h, l, c = _bullish_path()
    b = next(b for b in find_order_blocks(o, h, l, c) if b.direction == "bull")
    far = list(c) + [c[-1] + 1 + 0.1 * i for i in range(config.OB_EXPIRY_BARS + 5)]
    o2, h2, l2, c2 = _series(far)
    assert first_touch(b, o2, h2, l2) is None
    assert not any(x.idx == b.idx for x in active_blocks(o2, h2, l2, c2))


# ---- backtest

def _df(o, h, l, c):
    idx = pd.bdate_range("2024-01-01", periods=len(c))
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c}, index=idx)


def _long_with_retest(retest):
    o, h, l, c = _bullish_path()
    pad = [c[-1] + 0.2 * i for i in range(1, 4)]
    return _series(list(c) + pad + retest)


def test_trade_target_and_costs():
    o, h, l, c = _long_with_retest([102, 100.5, 104, 110, 118, 125, 130])
    b = next(b for b in find_order_blocks(o, h, l, c) if b.direction == "bull")
    dates = list(_df(o, h, l, c).index)
    tr = obb.trade_for(b, "X", dates, o, h, l, c)
    if tr is None:      # risk filter may reject this synthetic geometry; relax it for the mechanics test
        config.OB_MAX_RISK_PCT, old = 50.0, config.OB_MAX_RISK_PCT
        try:
            tr = obb.trade_for(b, "X", dates, o, h, l, c)
        finally:
            config.OB_MAX_RISK_PCT = old
    assert tr is not None
    assert tr.entry <= b.high and tr.stop < b.low
    assert abs(tr.net_pct - (tr.gross_pct - config.ROUND_TRIP_COST_PCT)) < 1e-9
    assert tr.outcome in {"target", "stop", "time"}


def test_walk_stop_first_on_same_bar():
    o = np.array([100.0, 100.0]); h = np.array([100.0, 110.0]); l = np.array([100.0, 90.0]); c = o.copy()
    x, price, outcome = obb._walk("bull", o, h, l, c, 0, 100.0, 95.0, 105.0, 5)
    assert (outcome, price) == ("stop", 95.0)


def test_walk_gap_through_stop_exits_at_open():
    o = np.array([100.0, 90.0]); h = np.array([100.0, 91.0]); l = np.array([100.0, 89.0]); c = o.copy()
    x, price, outcome = obb._walk("bull", o, h, l, c, 0, 100.0, 95.0, 110.0, 5)
    assert (outcome, price) == ("stop", 90.0)


def test_shorts_only_for_fo_symbols_and_summary_runs():
    o, h, l, c = _long_with_retest([102, 100.5, 104, 110, 118, 125, 130])
    # pad to the 120-bar minimum
    pad = _series([100 + (i % 3) * 0.2 for i in range(120)])
    df = _df(*[np.concatenate([p, a]) for p, a in zip(pad, (o, h, l, c))])
    mirror = _df(*[np.concatenate([p, a]) for p, a in zip(pad, _series(200 - np.concatenate([[0], c])[1:]))])
    trades, arrays = obb.run_orderblock_backtest({"A": df, "B": mirror}, short_eligible=set())
    assert all(t.direction == "bull" for t in trades)
    trades2, arrays2 = obb.run_orderblock_backtest({"A": df, "B": mirror}, short_eligible={"B"})
    assert len(trades2) >= len(trades)
    base = obb.random_baseline(trades2, arrays2)
    text = obb.summarize(trades2, base) if trades2 else obb.summarize([], [])
    assert isinstance(text, str)
