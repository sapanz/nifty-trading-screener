import json

import numpy as np
import pandas as pd

from signals import price_action as pa
from signals import price_action_board as pb

P = pa.Params()


def _week(o, h, l, c):
    """Five daily bars whose weekly candle is exactly (o, h, l, c)."""
    return [[o, o, o, o], [o, h, o, h], [h, h, l, l], [l, l, l, l], [l, max(l, c), min(l, c), c]]


def _base_weeks():
    """16 weekly candles between 100 and 125: the floor (low 100) is tested in weeks 2 and 9; the last week closes in the lower part of the box."""
    rows = []
    for k in range(16):
        low = 100.0 if k in (2, 9) else 104.0 + (k % 3)
        high = 125.0 if k in (5, 6) else 118.0 - (k % 4)
        close = 108.0 if k == 15 else (110.0 + (k % 5))
        rows.append((close - 1, high, low, close))
    return rows


def test_floor_needs_a_tested_floor_a_close_above_it_and_a_not_too_wide_box():
    wk = np.array(_base_weeks() * 3)                                                  # 48 weeks so there is room for 8-40 week bases
    H, L, C = wk[:, 1].copy(), wk[:, 2].copy(), wk[:, 3].copy()
    w = 15 + 32
    f = pa.floor_at(H, L, C, w, P)
    assert f and f["floor"] == 100.0 and f["top"] == 125.0 and f["weeks"] >= 8 and f["band_top"] <= 100 * 1.03
    C2 = C.copy()
    C2[w] = 98.0                                                                      # the last week already closed under the floor
    assert pa.floor_at(H, L, C2, w, P) is None
    L3 = np.maximum(L, 104.5)                                                         # the floor was never tested twice: only one low near the minimum
    L3[w - 3] = 100.0
    assert pa.floor_at(H, L3, C, w, P) is None
    assert pa.floor_at(H * 1.5, L, C, w, P) is None                                   # 150% wide: not a base


def _bars(rows):
    a = np.array(rows, float)
    return {"o": a[:, 0], "h": a[:, 1], "l": a[:, 2], "c": a[:, 3]}


def _pad(rows, n=40, price=110.0):
    return [[price, price + 1, price - 1, price]] * n + rows


def test_retest_search_finds_the_failure_candle_and_the_trigger():
    # floor 100 (band top 101): breakdown close 96, a bounce that fails with an upper wick (high 101, close 98), then a close under its low
    rows = _pad([[104, 105, 103, 104], [103, 104, 95, 96],                           # the breakdown: a close under 100
                 [96, 97, 94, 95], [95, 96, 93, 94],
                 [94.2, 101.0, 93.8, 94.5],                                          # the retest: high gets to the floor, close back under it
                 [94.5, 95, 92, 92.5]])                                              # next session closes under the failure candle's low (93.8)
    d = _bars(rows)
    atr = pa._atr(d["h"], d["l"], d["c"])
    res = pa.retest_search(d, atr, 100.0, 101.0, 40, 40, P)
    assert res and res[0] == "trigger" and res[1]["i"] == len(rows) - 1 and d["l"][res[1]["j"]] == 93.8


def test_no_setup_without_the_wick_or_after_a_reclaim_or_a_later_high():
    base = [[104, 105, 103, 104], [103, 104, 95, 96], [96, 97, 94, 95], [95, 96, 93, 94]]
    def run(tail):
        d = _bars(_pad(base + tail))
        return pa.retest_search(d, pa._atr(d["h"], d["l"], d["c"]), 100.0, 101.0, 40, 40, P)
    assert run([[94, 100.6, 93, 99.8], [99.8, 100, 92, 92.5]]) is None                  # a retest candle closing at its high: no upper wick
    assert run([[94, 98.5, 93, 97], [97, 98, 92, 92.5]]) is None                        # the bounce stayed more than 1% under the floor: not a retest
    assert run([[94, 102, 93, 105], [105, 106, 92, 92.5]]) is None                      # a close back above the floor band: the breakdown failed
    assert run([[94.2, 101.0, 93.8, 94.5], [94.5, 95, 94.2, 94.4], [94.4, 95, 94.2, 94.6], [94.6, 95, 94.3, 94.8], [94.8, 95.1, 94.4, 94.9], [94.9, 95.2, 94.5, 95.0]]) is None   # no close under the failure candle's low within 3 sessions (and the data goes on, so it is not still armed)
    res = run([[94.2, 101.0, 93.8, 94.5], [94.5, 95, 94.2, 94.4]])
    assert res and res[0] == "pending"                                                  # data ends inside the 3-session window: still armed


def test_a_later_candle_counts_when_the_first_attempt_is_overrun():
    base = [[104, 105, 103, 104], [103, 104, 95, 96], [96, 97, 94, 95]]
    tail = [[95, 100.5, 94, 95], [95, 101, 94.5, 95.5]]                                  # a first failure candle, then a higher one that sets a new high (101) ...
    tail += [[95.5, 98, 92, 92.8], [92.8, 95, 91, 91.5]]                                  # ... and price closes under the second candle's low (94.5)
    d = _bars(_pad(base + tail))
    res = pa.retest_search(d, pa._atr(d["h"], d["l"], d["c"]), 100.0, 101.0, 40, 40, P)
    assert res and res[0] == "trigger" and d["h"][res[1]["j"]] == 101


def test_end_to_end_a_range_breaks_down_retests_and_the_short_triggers_with_the_failure_high_as_the_stop():
    prefix = [[110.0, 111.0, 109.0, 110.0]] * 285
    rows = prefix[:]
    for w in _base_weeks():
        rows += _week(*w)
    rows += [[107.0, 108.0, 106.0, 107.0], [106.0, 106.5, 95.0, 96.0],               # the breakdown day closes at 96, under the floor (100)
             [96.0, 97.0, 94.5, 95.0], [95.0, 96.0, 93.5, 94.0],
             [94.2, 101.0, 93.8, 94.5],                                              # the retest: high 101, close back under it, upper wick 87% of the range
             [94.5, 95.0, 92.0, 92.5]]                                               # the trigger: a close under the failure candle's low
    rows += [[92.5, 93.0, 90.0, 91.0]] * 3
    a = np.array(rows, float)
    df = pd.DataFrame({"open": a[:, 0], "high": a[:, 1], "low": a[:, 2], "close": a[:, 3], "volume": np.full(len(a), 5e5)}, index=pd.bdate_range("2024-01-01", periods=len(a)))
    trig, pend = pa.scan_stock(df)
    rt = [t for t in trig if t["kind"] == "retest"]
    assert len(rt) == 1 and not [x for x in pend if x["kind"] == "retest"]
    t = rt[0]
    assert t["side"] == "short" and abs(t["stop"] - 101.0 * 1.003) < 1e-6 and t["level"] == 93.8 and t["target"] < 93.8 and t["rr"] > 1.5
    assert t["geo"]["floor"] == 100.0 and "failed there" in t["reason"] and t["geo"]["break_date"] < t["geo"]["retest_date"]
    assert pb.setup_name("retest", "short", t["geo"]) == "Range breakdown retest (short)"
    json.dumps(t["geo"])


def test_the_page_lists_the_retest_short_and_draws_its_geometry():
    from pathlib import Path
    html = (Path(__file__).resolve().parent.parent / "docs" / "index.html").read_text()
    assert 'var PA_KINDS = ["zone", "range", "triangle", "cup", "multiyear", "retest", "flag", "trendline"]' in html and 'value="retest"' in html
    assert 'if (s.kind === "retest") {' in html and "Retest fails" in html and "Broken floor" in html
