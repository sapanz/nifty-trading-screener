import numpy as np
import pandas as pd

from signals import swing_blocks as sb


def _daily(closes, volume=None, spread=0.5):
    c = np.array(closes, dtype=float)
    o = np.concatenate([[c[0]], c[:-1]])
    idx = pd.bdate_range("2026-01-01", periods=len(c))
    vol = np.full(len(c), 1000.0) if volume is None else volume
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) + spread, "low": np.minimum(o, c) - spread, "close": c, "volume": vol}, index=idx)


CHOP = [100, 101, 102, 101, 100.5, 100, 100.5, 100, 100.5, 100, 100.5, 100] * 4
THRUST = [99, 108, 111, 112, 112.5]


def test_fresh_bullish_zone_after_a_thrust_and_ranked_fields():
    df = _daily(CHOP + THRUST)
    zones = sb.scan_daily(df)
    bull = [z for z in zones if z["d"] == "bull"]
    assert len(bull) == 1 and bull[0]["st"] == "fresh" and 0 <= bull[0]["sc"] <= 100
    assert bull[0]["t"] in df.index.strftime("%Y-%m-%d")
    assert bull[0]["lo"] < 99 < bull[0]["hi"]


def test_testing_then_dropped_when_broken_or_old():
    testing = sb.scan_daily(_daily(CHOP + THRUST + [111, 109, 100.2]))
    assert [z["st"] for z in testing if z["d"] == "bull"] == ["testing"]
    broken = sb.scan_daily(_daily(CHOP + THRUST + [111, 100, 96, 95]))
    assert not [z for z in broken if z["d"] == "bull"]
    aged = sb.scan_daily(_daily(CHOP + THRUST + [111, 109, 100.2] + [104 + 0.1 * i for i in range(8)]))
    assert not [z for z in aged if z["d"] == "bull"]          # retested more than 3 sessions ago


def test_bearish_mirror_and_short_history():
    mirror = sb.scan_daily(_daily([200 - x for x in CHOP + THRUST]))
    assert [z for z in mirror if z["d"] == "bear"]
    assert sb.scan_daily(_daily(CHOP[:10])) == []


def test_chart_payload():
    df = _daily(CHOP + THRUST)
    p = sb.chart_payload_daily(df)
    assert len(p["c"]) == len(p["dates"]) == min(len(df), sb.CHART_BARS) and len(p["c"][0]) == 4
    assert set(p["lv"]) == {"sup", "res", "liq_lo", "liq_hi"}
    assert sb.chart_payload_daily(df.iloc[:5]) is None
