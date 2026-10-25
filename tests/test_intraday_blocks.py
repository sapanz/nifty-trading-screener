import numpy as np
import pandas as pd

from signals import config, intraday_blocks as ib

DAY = pd.Timestamp("2026-10-23")
PREV = pd.Timestamp("2026-10-22")


def _frame(closes_prev, closes_today, spread=0.5):
    def bars(day, closes, start_idx_close):
        c = np.array(closes, dtype=float)
        o = np.concatenate([[start_idx_close if start_idx_close is not None else c[0]], c[:-1]])
        idx = pd.date_range(day + pd.Timedelta(hours=9, minutes=15), periods=len(c), freq="5min")
        return pd.DataFrame({"open": o, "high": np.maximum(o, c) + spread, "low": np.minimum(o, c) - spread, "close": c, "volume": 1000}, index=idx)
    prev = bars(PREV, closes_prev, None)
    today = bars(DAY, closes_today, prev["close"].iloc[-1])
    return pd.concat([prev, today])


CHOP = [100, 101, 100.5, 100, 100.5, 100, 100.5, 100, 100.5, 100] * 4
# swing high at 102 then pullback, a red candle (99), and a thrust through the swing high
UP = [100, 101, 102, 101, 100.5, 100, 100.5, 100, 100.5, 100, 100.5, 100, 99, 108, 111, 112, 112.5]


def test_fresh_block_after_thrust_today():
    out = ib.scan_symbol(_frame(CHOP, UP), DAY)
    bull = [b for b in out if b["d"] == "bull"]
    assert len(bull) == 1 and bull[0]["st"] == "fresh"
    assert bull[0]["lo"] < 99 < bull[0]["hi"]


def test_testing_when_price_returns_into_the_zone_and_holds():
    out = ib.scan_symbol(_frame(CHOP, UP + [111, 109, 100.2]), DAY)
    bull = [b for b in out if b["d"] == "bull"]
    assert bull and bull[0]["st"] == "testing"


def test_dropped_once_the_far_edge_is_broken():
    out = ib.scan_symbol(_frame(CHOP, UP + [111, 100, 96, 95]), DAY)
    assert not [b for b in out if b["d"] == "bull"]


def test_dropped_after_an_earlier_retest_has_aged():
    later = UP + [111, 109, 100.2] + [104 + 0.2 * i for i in range(config.INTRADAY_OB_RECENT_BARS + 3)]
    assert not [b for b in ib.scan_symbol(_frame(CHOP, later), DAY) if b["d"] == "bull"]


def test_expires():
    later = UP + [113 + 0.1 * i for i in range(config.INTRADAY_OB_EXPIRY_BARS + 3)]
    assert not ib.scan_symbol(_frame(CHOP, later), DAY)


def test_forming_candle_is_ignored():
    frame = _frame(CHOP, UP)
    now = frame.index[-1] + pd.Timedelta(minutes=2)       # last bar still forming
    seen = ib.scan_symbol(frame, DAY, now)
    # without the thrust's last bars the break may not exist yet, but it must not use the open bar
    assert all(b["t"] <= frame.index[-2].strftime("%H:%M") for b in seen)


def test_block_formed_yesterday_is_not_reported():
    frame = _frame(UP + CHOP, CHOP)
    assert ib.scan_symbol(frame, DAY) == []


def test_bearish_mirror_and_too_little_data():
    mirror = _frame([200 - x for x in CHOP], [200 - x for x in UP])
    assert [b for b in ib.scan_symbol(mirror, DAY) if b["d"] == "bear"]
    assert ib.scan_symbol(_frame(CHOP[:5], UP[:5]), DAY) == []


def _norm(recs):
    return sorted((r["d"], r["st"], r["lo"], r["hi"], r["t"], r["imp"]) for r in recs)


def test_replay_states_match_the_live_scan_at_every_slot():
    scenarios = [
        UP + [111, 109, 100.2, 104, 106, 107],                        # thrust, retest that holds, bounce
        UP + [111, 100, 96, 95, 94],                                  # thrust, retest, broken through
        UP + [113 + 0.1 * i for i in range(config.INTRADAY_OB_EXPIRY_BARS + 4)],   # never retested, expires
    ]
    for today in scenarios:
        for flip in (False, True):
            prev, tod = (CHOP, today) if not flip else ([200 - x for x in CHOP], [200 - x for x in today])
            frame = _frame(prev, tod)
            blocks = ib.day_blocks(frame, DAY)
            assert blocks
            n_prev = len(prev)
            for n in range(1, len(tod) + 1):
                live = ib.scan_symbol(frame.iloc[: n_prev + n], DAY)
                assert _norm(ib.states_at(blocks, n - 1)) == _norm(live), (flip, n)


def test_day_blocks_only_includes_blocks_formed_that_day():
    frame = _frame(UP + CHOP, CHOP)
    assert ib.day_blocks(frame, DAY) == []
    assert ib.day_blocks(frame.iloc[:5], DAY) == []
