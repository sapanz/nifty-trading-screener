from pathlib import Path

HTML = (Path(__file__).resolve().parent.parent / "docs" / "index.html").read_text()


def test_every_stock_gets_a_chart_tab_not_only_the_ones_with_an_order_block():
    assert 'hasZone ? "Chart & order block" : "Chart"' in HTML
    assert "if (obActive()) {                                              // every stock gets the chart tab" in HTML


def test_live_mode_falls_back_to_the_daily_candles_for_stocks_without_5_minute_candles():
    assert "function dailyFallback(sym)" in HTML and "function loadDailyForCharts()" in HTML
    assert "c && c.c && c.c.length ? c : dailyFallback(sym)" in HTML


def test_the_removed_tools_are_gone_from_the_page():
    for gone in ('id="paSide"', 'value="pause"', 'value="candle"', 'value="double"', 'value="hs"', 'value="flag"', 'value="triple"'):
        assert gone not in HTML, gone
    assert 'var PA_KINDS = ["zone", "range", "triangle", "cup", "multiyear", "retest"]' in HTML
    assert 'sides=("bull",)' not in (Path(__file__).resolve().parent.parent / "scripts" / "build_dashboard_swing.py").read_text()   # supply zones are published again


def test_swing_shorts_are_back_with_their_record_next_to_them():
    assert 'id="sBearNote"' in HTML and "lost money - −0.64% a trade" in HTML and 'id="mBearCol"' in HTML and 'id="momNear"' in HTML and "function renderMomNear" in HTML and "weak: out.slice().sort(cmp(-1))" in HTML


def test_swing_has_a_zone_test_filter_for_each_side():
    assert 'id="sState"' in HTML and 'id="sStateBear"' in HTML and "Bullish zone test" in HTML and "Bearish zone test" in HTML
    assert 'var test = dir === "bear" ? state.sStateBear : state.sState;' in HTML
