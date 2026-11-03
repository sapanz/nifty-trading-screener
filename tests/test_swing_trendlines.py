import numpy as np
import pandas as pd

from signals import swing_blocks as sb, swing_trendlines as st


def _frame(closes, spread=0.4):
    c = np.array(closes, dtype=float)
    o = np.concatenate([[c[0]], c[:-1]])
    idx = pd.bdate_range("2026-01-01", periods=len(c))
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) + spread, "low": np.minimum(o, c) - spread, "close": c, "volume": 1000.0}, index=idx)


def _rising_with_pullbacks(n=90, slope=0.15, dip=3.0, every=15):
    """Uptrend whose lows touch a straight line every `every` bars (a support line with several touches)."""
    base = 100 + slope * np.arange(n)
    wave = np.array([dip * (1 - abs(((i % every) / every) * 2 - 1)) for i in range(n)])    # 0 at the touch bars, up in between
    return base + wave


def test_support_line_found_with_touches_and_distance():
    df = _frame(_rising_with_pullbacks())
    lines = st.analyze(df)
    s = lines.get("s")
    assert s and s["n"] >= 3 and s["w"] >= 20 and s["m"] > 0 and s["mv"] >= 3
    assert s["d"] == round((df["close"].iloc[-1] / s["v"] - 1) * 100, 2)


def test_line_broken_by_a_close_is_not_reported():
    c = list(_rising_with_pullbacks())
    c[-1] = c[-1] - 25                                  # last close crashes through any support line
    assert "s" not in st.analyze(_frame(c))


def test_resistance_line_mirror_and_flat_market_has_none():
    c = [300 - x for x in _rising_with_pullbacks()]
    r = st.analyze(_frame(c)).get("r")
    assert r and r["n"] >= 3 and r["m"] < 0
    assert st.analyze(_frame([100 + 0.2 * np.sin(i / 3) for i in range(90)])) == {}


def test_confluence_and_scan_flag():
    zone = {"d": "bull", "lo": 140.0, "hi": 142.0}
    line = {"s": {"v": 141.0, "m": 0.5}}
    assert st.confluence(zone, line)
    assert not st.confluence(zone, {"s": {"v": 150.0, "m": 0.5}})
    assert st.confluence({"d": "bull", "lo": 142.0, "hi": 144.0}, {"s": {"v": 140.0, "m": 1.0}})      # reaches it within 3 bars
    assert not st.confluence({"d": "bear", "lo": 140.0, "hi": 142.0}, line)                              # wrong kind of line
    # scan_daily puts the flag on every zone
    chop = [100, 101, 102, 101, 100.5, 100, 100.5, 100, 100.5, 100, 100.5, 100] * 4
    zones = sb.scan_daily(_frame(chop + [99, 108, 111, 112, 112.5]))
    assert zones and all("cf" in z for z in zones)
    forced = sb.scan_daily(_frame(chop + [99, 108, 111, 112, 112.5]), {"s": {"v": 99.0, "m": 0.0}})
    assert [z["cf"] for z in forced if z["d"] == "bull"] == [1]


def test_zone_gap_matches_confluence_and_is_zero_inside():
    import numpy as np
    from signals.swing_trendlines import CONFLUENCE_TOL_PCT, confluence, zone_gap_pct

    rng = np.random.default_rng(4)
    for _ in range(500):
        lo = float(rng.uniform(50, 500)); hi = lo * float(rng.uniform(1.005, 1.06))
        zone = {"d": "bull" if rng.random() < 0.5 else "bear", "lo": lo, "hi": hi}
        key = "s" if zone["d"] == "bull" else "r"
        lines = {key: {"v": float(rng.uniform(lo * 0.9, hi * 1.1)), "m": float(rng.uniform(-0.01, 0.01)) * lo}}
        gap = zone_gap_pct(zone, lines)
        assert gap >= 0
        if abs(gap - CONFLUENCE_TOL_PCT) > 1e-6:                     # away from the exact tolerance edge the two agree
            assert confluence(zone, lines) == (gap <= CONFLUENCE_TOL_PCT)
    inside = {"d": "bull", "lo": 100.0, "hi": 104.0}
    assert zone_gap_pct(inside, {"s": {"v": 102.0, "m": 0.0}}) == 0.0
    assert abs(zone_gap_pct(inside, {"s": {"v": 98.0, "m": 0.0}}) - 2.0) < 1e-9            # 2% below the zone's low
    assert abs(zone_gap_pct(inside, {"s": {"v": 98.0, "m": 1.0}}) - 0.0) < 1e-9 or zone_gap_pct(inside, {"s": {"v": 98.0, "m": 1.0}}) < 2.0   # a rising line closes in
    assert zone_gap_pct(inside, {}) is None and zone_gap_pct({"d": "bear", "lo": 100.0, "hi": 104.0}, {"s": {"v": 99.0, "m": 0.0}}) is None
