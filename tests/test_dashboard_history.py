import numpy as np
import pandas as pd
import pytest

from signals import dashboard_history as dh


def _session(day, n=75, start="09:15", base=100.0, volume=1000, gap_at=None):
    idx = pd.date_range(f"{day} {start}", periods=n, freq="5min")
    closes = base + np.arange(n) * 0.1
    df = pd.DataFrame({"open": closes, "high": closes + 0.1, "low": closes - 0.1, "close": closes,
                       "volume": [volume] * n}, index=idx)
    if gap_at is not None:
        df = df.drop(df.index[gap_at])
    return df


def _daily(days, close=99.0, volume=5000.0):
    idx = pd.to_datetime(days)
    return pd.DataFrame({"open": close, "high": close, "low": close, "close": close, "volume": volume}, index=idx)


def _world(n_stocks=60):
    day = "2026-10-23"
    intraday = {f"S{i}": _session(day, base=100 + i) for i in range(n_stocks)}
    daily = {s: _daily(pd.bdate_range(end="2026-10-22", periods=30), close=98 + i) for i, s in enumerate(intraday)}
    industries = {s: "Banks" if i % 2 else "IT" for i, s in enumerate(intraday)}
    return pd.Timestamp(day), intraday, daily, industries


def test_day_file_has_slot_arrays_previous_close_and_average_volume():
    day, intraday, daily, industries = _world()
    payload = dh.build_day(day, intraday, daily, industries, {"S0"})
    assert payload["date"] == "2026-10-23" and payload["slots"] == 75 and len(payload["stocks"]) == 60
    s0 = payload["stocks"]["S0"]
    assert len(s0["c"]) == 75 and len(s0["v"]) == 75
    assert s0["c"][0] == 100.0 and s0["c"][74] == pytest.approx(107.4)
    assert s0["v"] == [1000] * 75
    assert s0["pc"] == 98.0 and s0["av"] == 5000.0 and s0["fo"] == 1 and s0["s"] == "IT"
    assert payload["stocks"]["S1"]["fo"] == 0 and payload["stocks"]["S1"]["s"] == "Banks"


def test_previous_close_and_average_ignore_the_day_itself():
    day, intraday, daily, industries = _world()
    daily["S0"] = pd.concat([daily["S0"], _daily(["2026-10-23"], close=555.0, volume=999999.0)])
    s0 = dh.build_day(day, intraday, daily, industries, None)["stocks"]["S0"]
    assert s0["pc"] == 98.0 and s0["av"] == 5000.0


def test_quiet_slots_carry_the_last_price_and_have_zero_volume():
    day, intraday, daily, industries = _world()
    intraday["S0"] = _session("2026-10-23", gap_at=10)          # no candle for slot 10
    s0 = dh.build_day(day, intraday, daily, industries, None)["stocks"]["S0"]
    assert s0["c"][10] == s0["c"][9] and s0["v"][10] == 0 and s0["v"][11] == 1000


def test_late_first_trade_leaves_earlier_slots_empty_and_thin_stocks_are_dropped():
    day, intraday, daily, industries = _world()
    intraday["S0"] = _session("2026-10-23", n=50, start="10:00")        # first trade at slot 9
    intraday["THIN"] = _session("2026-10-23", n=10)
    daily["THIN"] = daily["S1"]
    payload = dh.build_day(day, intraday, daily, industries, None)
    assert payload["stocks"]["S0"]["c"][:9] == [None] * 9 and payload["stocks"]["S0"]["c"][9] is not None
    assert "THIN" not in payload["stocks"]


def test_stock_without_history_is_skipped_and_a_thin_market_is_not_a_session():
    day, intraday, daily, industries = _world()
    del daily["S5"]
    assert "S5" not in dh.build_day(day, intraday, daily, industries, None)["stocks"]
    assert dh.build_day(day, {k: v for k, v in list(intraday.items())[:10]}, daily, industries, None) is None


def test_trading_days_keeps_recent_days_where_most_stocks_traded():
    frames = {}
    for i in range(10):
        parts = [_session(d) for d in ("2026-10-20", "2026-10-21", "2026-10-22")]
        if i < 2:                                            # only 2 of 10 stocks have a stray extra day
            parts.append(_session("2026-10-19"))
        frames[f"S{i}"] = pd.concat(parts)
    days = dh.trading_days(frames, limit=2)
    assert [d.date().isoformat() for d in days] == ["2026-10-21", "2026-10-22"]
    assert pd.Timestamp("2026-10-19") not in dh.trading_days(frames, limit=10)


def test_day_file_records_previous_day_levels_and_the_first_slot_each_was_broken():
    day, intraday, daily, industries = _world()
    for sym in daily:                                     # previous session: high 104, low 96
        daily[sym]["high"], daily[sym]["low"] = 104.0, 96.0
    # S0 trades 100..107.4 (high = close + 0.1): first slot with high > 104 is where close passes 103.9
    out = dh.build_day(day, intraday, daily, industries, None)["stocks"]
    s0 = out["S0"]
    assert (s0["ph"], s0["pl"]) == (104.0, 96.0)
    expected = next(i for i in range(75) if 100.0 + i * 0.1 + 0.1 > 104.0)
    assert s0["bh"] == expected and s0["bl"] is None
    # S1 starts at 101: breaks earlier
    assert out["S1"]["bh"] < s0["bh"]
    # a stock that dips under the previous low
    low_df = _session("2026-10-23", base=90.0)            # opens at 90 < 96: low broken on the very first candle
    intraday["S0"] = low_df
    assert dh.build_day(day, intraday, daily, industries, None)["stocks"]["S0"]["bl"] == 0


def test_day_file_carries_order_blocks_for_fo_stocks_only():
    from tests.test_intraday_blocks import CHOP, UP, DAY, PREV, _frame
    frame = _frame(CHOP, UP + [111, 109, 100.2, 104])
    frame["volume"] = 1000
    day, intraday, daily, industries = _world()
    intraday["S0"], intraday["S1"] = frame, frame.copy()
    for sym in ("S0", "S1"):
        daily[sym] = _daily(pd.bdate_range(end="2026-10-21", periods=30), close=100.0)
    payload = dh.build_day(pd.Timestamp(DAY), intraday, daily, industries, {"S0"})
    blocks = payload["stocks"]["S0"]["ob"]
    assert blocks and {"d", "i", "f", "lo", "hi", "t", "x", "imp"} <= set(blocks[0])
    assert "ob" not in payload["stocks"]["S1"]
