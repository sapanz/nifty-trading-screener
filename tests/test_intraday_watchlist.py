import pandas as pd

from signals.strategies import intraday_watchlist as iw


def _trend_df(direction: int = 1, n: int = 80, step: float = 0.003, half_range: float = 0.01) -> pd.DataFrame:
    """A steady trend (direction 1 = up, -1 = down, 0 = flat), each day's
    range +/- `half_range` around its close. Tests then overwrite the last
    candle with the setup they're checking."""
    dates = pd.bdate_range(end="2026-10-16", periods=n)
    closes = [100.0 * (1 + direction * step) ** i for i in range(n)]
    return pd.DataFrame(
        {
            "open": [c * (1 - direction * 0.002) for c in closes],
            "high": [c * (1 + half_range) for c in closes],
            "low": [c * (1 - half_range) for c in closes],
            "close": closes,
            "volume": [100_000.0] * n,
        },
        index=dates,
    )


def _set_last(df: pd.DataFrame, o: float, h: float, l: float, c: float, v: float = 100_000.0) -> pd.DataFrame:
    """Overwrite the last candle, prices given as multiples of the previous close."""
    prev = df["close"].iloc[-2]
    df.iloc[-1] = [prev * o, prev * h, prev * l, prev * c, v]
    return df


def _bull_breakout(**kwargs) -> pd.DataFrame:
    return _set_last(_trend_df(1, **kwargs), 1.002, 1.03, 1.0, 1.028, 200_000.0)


def _scan(data: dict, industries: dict | None = None, events: set | None = None):
    return iw.scan(data, industries or {}, events or set())


def test_bull_breakout_close_is_picked_with_reasons():
    result = _scan({"TESTCO": _bull_breakout()})
    assert [p.symbol for p in result.bulls] == ["TESTCO"]
    pick = result.bulls[0]
    assert pick.setup == iw.BREAKOUT_SETUP
    assert "Closed above 10-day high" in pick.reasons
    assert "Volume 2.0x 20-day avg" in pick.reasons
    assert "Above rising 20 & 50 EMA" in pick.reasons
    assert pick.candle_date == pd.Timestamp("2026-10-16").date()


def test_bear_breakdown_close_is_picked():
    df = _set_last(_trend_df(-1), 0.998, 1.0, 0.97, 0.972, 200_000.0)
    result = _scan({"TESTCO": df})
    assert [p.symbol for p in result.bears] == ["TESTCO"]
    assert result.bears[0].setup == iw.BREAKDOWN_SETUP
    assert "Below falling 20 & 50 EMA" in result.bears[0].reasons


def test_coiled_nr7_inside_day_in_uptrend_is_picked():
    df = _set_last(_trend_df(1), 1.001, 1.004, 0.999, 1.003)
    result = _scan({"TESTCO": df})
    assert [p.symbol for p in result.bulls] == ["TESTCO"]
    pick = result.bulls[0]
    assert pick.setup == iw.COILED_SETUP
    assert "NR7 - narrowest range in 7 days" in pick.reasons
    assert "Inside day - range within yesterday's" in pick.reasons


def test_coiled_closing_against_the_trend_is_not_picked():
    # NR7/inside day in an uptrend, but closing in the lower half of its range
    df = _set_last(_trend_df(1), 1.003, 1.004, 0.999, 0.9995)
    assert _scan({"TESTCO": df}).picks == []


def test_breakout_without_volume_is_not_picked():
    df = _set_last(_trend_df(1), 1.002, 1.03, 1.0, 1.028, 110_000.0)
    assert _scan({"TESTCO": df}).picks == []


def test_no_pick_without_a_trend():
    df = _set_last(_trend_df(0), 1.002, 1.03, 1.0, 1.028, 200_000.0)
    assert _scan({"TESTCO": df}).picks == []


def test_symbol_with_calendar_event_is_skipped_and_counted():
    result = _scan({"TESTCO": _bull_breakout()}, events={"TESTCO"})
    assert result.picks == []
    assert result.skipped_events == ["TESTCO"]


def test_news_like_gap_is_skipped_and_counted():
    df = _set_last(_trend_df(1), 1.06, 1.08, 1.05, 1.075, 200_000.0)
    result = _scan({"TESTCO": df})
    assert result.picks == []
    assert result.skipped_shock == ["TESTCO"]


def test_abnormally_wide_day_is_skipped_and_counted():
    # tiny gap, but a ~9% range against a usual ~2% day
    df = _set_last(_trend_df(1), 1.002, 1.08, 0.99, 1.075, 200_000.0)
    result = _scan({"TESTCO": df})
    assert result.picks == []
    assert result.skipped_shock == ["TESTCO"]


def test_chronically_volatile_stock_is_skipped_silently():
    df = _set_last(_trend_df(1, half_range=0.04), 1.002, 1.06, 1.0, 1.058, 200_000.0)
    result = _scan({"TESTCO": df})
    assert result.picks == []
    assert result.skipped_shock == []


def _sector_peers(direction: int) -> dict:
    return {f"PEER{i}": _trend_df(direction) for i in range(3)}


def test_bull_in_a_falling_sector_is_not_picked():
    data = {"TESTCO": _bull_breakout(), **_sector_peers(-1)}
    industries = {sym: "Capital Goods" for sym in data}
    assert [p.symbol for p in _scan(data, industries).bulls] == []


def test_bull_in_a_rising_sector_gets_a_sector_reason():
    data = {"TESTCO": _bull_breakout(), **_sector_peers(1)}
    industries = {sym: "Capital Goods" for sym in data}
    bulls = _scan(data, industries).bulls
    assert [p.symbol for p in bulls] == ["TESTCO"]
    assert any(r.startswith("Sector Capital Goods +") and "#1 of 1" in r for r in bulls[0].reasons)


def test_sector_strength_needs_minimum_members():
    data = {"A": _trend_df(1), "B": _trend_df(1), "C": _trend_df(-1)}
    assert iw.sector_strength(data, {"A": "X", "B": "X", "C": "Y"}) == {}
    strength = iw.sector_strength(data, {"A": "X", "B": "X", "C": "X"})
    assert strength["X"] > 0  # median of two rising, one falling


def test_breakouts_sort_before_coiled_setups():
    coiled = _set_last(_trend_df(1), 1.001, 1.004, 0.999, 1.003)
    result = _scan({"COILED": coiled, "BREAKOUT": _bull_breakout()})
    assert [p.symbol for p in result.bulls] == ["BREAKOUT", "COILED"]
