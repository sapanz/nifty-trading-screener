from pathlib import Path

HTML = (Path(__file__).resolve().parent.parent / "docs" / "index.html").read_text()


def test_every_stock_gets_a_chart_tab_not_only_the_ones_with_an_order_block():
    assert 'hasZone ? "Chart & order block" : "Chart"' in HTML
    assert "if (obActive()) {                                              // every stock gets the chart tab" in HTML


def test_live_mode_falls_back_to_the_daily_candles_for_stocks_without_5_minute_candles():
    assert "function dailyFallback(sym)" in HTML and "function loadDailyForCharts()" in HTML
    assert "c && c.c && c.c.length ? c : dailyFallback(sym)" in HTML


def test_the_removed_tools_are_gone_from_the_page():
    for gone in ('id="momNear"', 'id="paSide"', 'id="mBearCol"', 'value="pause"', 'value="candle"', 'value="double"', 'value="hs"', 'value="flag"', 'value="triple"'):
        assert gone not in HTML, gone
    assert 'var PA_KINDS = ["zone", "range", "triangle", "cup", "multiyear"]' in HTML
    assert 'sides=("bull",)' in (Path(__file__).resolve().parent.parent / "scripts" / "build_dashboard_swing.py").read_text()
