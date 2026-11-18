import json

import numpy as np
import pandas as pd

from signals import price_action as pa
from signals import price_action_board as pb
from signals import price_action_model as pm

P = pa.Params()


def _bars(rows):
    a = np.array(rows, float)
    return a[:, 0], a[:, 1], a[:, 2], a[:, 3]


def _flat(n=40, base=100.0):
    """Quiet candles around `base` with a range of about 1.2 (the ATR)."""
    rng = np.random.default_rng(3)
    mid = base + rng.normal(0, 0.15, n)
    o = mid + rng.normal(0, 0.1, n)
    c = mid + rng.normal(0, 0.1, n)
    h = np.maximum(o, c) + 0.5
    l = np.minimum(o, c) - 0.5
    return [[a, b, d, e] for a, b, d, e in zip(o, h, l, c)]


REDS = [[100.0, 100.1, 98.5, 98.6], [98.6, 98.7, 97.1, 97.2], [97.2, 97.3, 95.7, 95.8], [95.8, 95.9, 94.3, 94.4]]       # strong red candles, each closing at its low
DOJI = [94.4, 94.9, 94.0, 94.45]


def _arrays(rows):
    o, h, l, c = _bars(rows)
    return o, h, l, c, pa._atr(h, l, c), pd.Series(c).rolling(20).mean().to_numpy()


def test_pause_after_clear_selling_is_found_with_its_run_and_levels():
    rows = _flat() + REDS + [DOJI]
    o, h, l, c, atr, sma = _arrays(rows)
    j = len(rows) - 1
    got = pa.pause_at(o, h, l, c, atr, sma, j, P)
    assert got and got["pause"] == "Doji" and got["run"] == 4 and got["low"] == 94.0 and got["high"] == 94.9 and got["drop_atr"] >= 2
    green = rows[:-1] + [[94.4, 94.9, 94.2, 94.75]]                                            # a small green body instead of a doji
    g = pa.pause_at(*_arrays(green)[:4], *_arrays(green)[4:], len(green) - 1, P)
    assert g and g["pause"] == "Small green candle"


def test_the_pause_needs_every_part_of_the_selling_pressure():
    base = _flat()
    def at(rows):
        a = _arrays(rows)
        return pa.pause_at(*a[:4], a[4], a[5], len(rows) - 1, P)
    assert at(base + REDS + [DOJI])
    assert at(base + REDS[1:] + [DOJI])["run"] == 3, "three red candles are enough"
    assert at(base + REDS[2:] + [DOJI]) is None                                                # two red candles are not a run
    weak = [list(r) for r in REDS]
    weak[2] = [97.2, 98.2, 95.7, 96.9]                                                          # a red candle that closed mid-range: no clear selling
    assert at(base + weak + [DOJI]) is None
    assert at(base + REDS + [[94.4, 96.5, 93.9, 96.3]]) is None                                # a big green candle is not a pause
    assert at(base + REDS + [[94.4, 96.4, 94.0, 94.5]]) is None                                # the pause's high is above the last red candle's high
    assert at(base + REDS + [[94.4, 94.45, 94.4, 94.42]]) is None                              # a flat bar, not a candle
    shallow = [[100.0, 100.1, 99.5, 99.6], [99.6, 99.7, 99.1, 99.2], [99.2, 99.3, 98.7, 98.8]]  # three red candles that fell less than 2 ATR
    assert at(base + shallow + [[98.8, 99.2, 98.6, 98.85]]) is None
    rise = [[100 + k, 101.5 + k, 99.5 + k, 101.2 + k] for k in range(30)]                       # a pullback inside a strong uptrend: still above its average
    pull = [[130.0, 130.1, 128.5, 128.6], [128.6, 128.7, 127.1, 127.2], [127.2, 127.3, 125.7, 125.8], [125.8, 126.3, 125.4, 125.85]]
    assert at(base[:20] + rise + pull) is None


def _stock(tail, n_base=300):
    """A daily frame: quiet candles then the engineered `tail`."""
    rows = _flat(n_base) + tail
    o, h, l, c = _bars(rows)
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": np.full(len(rows), 5e5)}, index=pd.bdate_range("2024-01-01", periods=len(rows)))


def _pause_rows(df):
    t, p = pa.scan_stock(df)
    return [x for x in t if x["kind"] == "pause"], [x for x in p if x["kind"] == "pause"]


def test_a_daily_close_below_the_pause_low_triggers_a_short_with_the_pause_high_as_the_stop():
    df = _stock(REDS + [DOJI, [94.4, 94.7, 93.2, 93.4]])                                       # next session closes under the doji's low (94.0)
    trig, pend = _pause_rows(df)
    assert len(trig) == 1 and not pend
    t = trig[0]
    assert t["side"] == "short" and t["i"] == len(df) - 1 and t["geo"]["tf"] == "daily" and t["geo"]["run"] == 4 and t["geo"]["pause"] == "Doji"
    assert t["stop"] == 94.9 and t["level"] == 94.0 and t["target"] < t["geo"]["level"] and t["rr"] > 0 and "selling pressure" in t["reason"]
    assert pb.setup_name("pause", "short", t["geo"]) == "Bearish continuation (daily): 4 red days, then doji"
    json.dumps(t["geo"])


def test_no_trigger_when_price_first_trades_above_the_pause_high_or_waits_too_long():
    above = _stock(REDS + [DOJI, [94.4, 95.2, 93.9, 94.1], [94.1, 94.2, 93.0, 93.1]])          # high 95.2 > 94.9 first: the stop level was already hit
    assert not _pause_rows(above)[0]
    slow = _stock(REDS + [DOJI] + [[94.45, 94.7, 94.1, 94.5]] * 3 + [[94.5, 94.6, 93.0, 93.1]])  # the break comes on the 4th session: window is 3
    assert not _pause_rows(slow)[0]


def test_a_pause_candle_on_the_latest_bar_is_pending_with_its_entry_below_the_low():
    df = _stock(REDS + [DOJI])
    trig, pend = _pause_rows(df)
    assert not trig and len(pend) == 1
    q = pend[0]
    assert q["side"] == "short" and q["entry"] < 94.0 and q["stop"] == 94.9 and q["geo"]["level"] == 94.0
    assert "short below its low" in q["reason"]


def _weekly_days(O, H, L, C):
    """Five daily bars whose weekly candle is exactly (O, H, L, C)."""
    return [[O, O, O, O], [O, H, O, H], [H, H, L, L], [L, L, L, L], [L, max(L, C), min(L, C), C]]


def test_a_weekly_pause_triggers_on_a_daily_close_in_the_following_week():
    base = [[x, y, z, w] for x, y, z, w in _flat(280, 100.0)]
    weeks = [(100.0, 100.1, 98.5, 98.6), (98.6, 98.7, 97.1, 97.2), (97.2, 97.3, 95.7, 95.8), (95.8, 95.9, 94.3, 94.4), (94.4, 94.9, 94.0, 94.45)]
    rows = base[:280 - 280 % 5]
    for w in weeks:
        rows += _weekly_days(*w)
    rows += [[94.4, 94.6, 93.4, 93.6]] + [[93.6, 93.8, 93.0, 93.2]] * 4                          # the following week: closes under 94.0 on its first day
    o, h, l, c = _bars(rows)
    start = pd.Timestamp("2024-01-01")                                                           # a Monday: the weekly candles line up with the days
    df = pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": np.full(len(rows), 5e5)}, index=pd.bdate_range(start, periods=len(rows)))
    trig, _ = _pause_rows(df)
    weekly = [t for t in trig if t["geo"]["tf"] == "weekly"]
    assert len(weekly) == 1
    t = weekly[0]
    assert t["geo"]["run"] == 4 and t["stop"] == 94.9 and t["level"] == 94.0 and df.index[t["i"]] == df.index[len(rows) - 5]
    assert pb.setup_name("pause", "short", t["geo"]).startswith("Bearish continuation (weekly): 4 red weeks")


def test_the_trade_is_a_short_outcome_and_the_board_lists_it_for_fo_stocks_only():
    df = _stock(REDS + [DOJI, [94.4, 94.7, 93.2, 93.4], [93.3, 93.4, 90.0, 90.5], [90.5, 91.0, 89.0, 89.5]])
    ev, _ = pm.collect({"X": df})
    r = ev[ev["kind"] == "pause"]
    assert len(r) == 1 and r.iloc[0]["side"] == "short" and r.iloc[0]["R"] > 0 and r.iloc[0]["tf"] == "daily"
    from tests.test_price_action import _daily
    frames = {f"S{k:02d}": _daily(k) for k in range(14)}
    payload, _ = pb.build(frames, fo={"S00"})
    assert all(x["fo"] for x in payload["setups"] if x["side"] == "short")
