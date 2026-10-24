import numpy as np
import pandas as pd

from signals import dashboard_history as dh
from signals.oi import oi_arrays
from tests.test_dashboard_history import _daily, _session, _world


def _futures(day, n=75, oi_start=1_000_000, step=1000, start="09:15", drop=None):
    idx = pd.date_range(f"{day} {start}", periods=n, freq="5min")
    df = pd.DataFrame({"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0 + np.arange(n) * 0.1,
                       "volume": 500, "open_interest": oi_start + np.arange(n) * step}, index=idx)
    return df.drop(df.index[drop]) if drop is not None else df


def test_oi_arrays_align_to_slots_and_carry_forward_quiet_slots():
    out = oi_arrays(_futures("2026-10-23", drop=10), pd.Timestamp("2026-10-23"))
    assert len(out["o"]) == 75 and out["o"][0] == 1_000_000 and out["o"][74] == 1_074_000
    assert out["o"][10] == out["o"][9] and out["p"][10] == out["p"][9]       # no candle in slot 10
    assert out["p"][1] == 100.1


def test_oi_arrays_leave_slots_before_the_first_candle_empty():
    out = oi_arrays(_futures("2026-10-23", n=50, start="10:00"), pd.Timestamp("2026-10-23"))
    assert out["o"][:9] == [None] * 9 and out["o"][9] == 1_000_000 and out["o"][74] == out["o"][58]


def test_oi_arrays_none_without_usable_open_interest():
    day = pd.Timestamp("2026-10-23")
    assert oi_arrays(None, day) is None and oi_arrays(pd.DataFrame(), day) is None
    assert oi_arrays(_futures("2026-10-23").drop(columns="open_interest"), day) is None
    zero = _futures("2026-10-23"); zero["open_interest"] = 0
    assert oi_arrays(zero, day) is None
    assert oi_arrays(_futures("2026-10-22"), day) is None                    # other day only


def test_day_files_carry_oi_for_fo_stocks_with_futures_only():
    day, intraday, daily, industries = _world()
    payload = dh.build_day(day, intraday, daily, industries, {"S0", "S1"}, futures={"S0": _futures("2026-10-23")})
    assert payload["stocks"]["S0"]["o"][74] == 1_074_000 and len(payload["stocks"]["S0"]["p"]) == 75
    assert "o" not in payload["stocks"]["S1"] and "o" not in payload["stocks"]["S2"]
    assert "o" not in dh.build_day(day, intraday, daily, industries, {"S0"})["stocks"]["S0"]
