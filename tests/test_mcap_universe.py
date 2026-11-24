"""The market-cap universe: the screener.in screen walks its pages, the build only applies a credible list."""
from pathlib import Path

import pytest

from signals import config, value_universe


class _Resp:
    def __init__(self, symbols):
        self.text = "<html>" + "".join(f'<a href="/company/{s}/consolidated/">{s}</a>' for s in symbols) + "</html>"
        self.status_code, self.url = 200, "https://www.screener.in/screen/raw/"

    def raise_for_status(self):
        pass


class _Session:
    def __init__(self, pages):
        self.pages, self.asked = pages, []

    def get(self, url, params=None, timeout=None):
        self.asked.append(params["page"])
        return _Resp(self.pages.get(params["page"], []))


def test_the_screen_is_read_page_by_page_until_a_page_adds_nothing(monkeypatch):
    pages = {1: [f"A{i}" for i in range(25)], 2: [f"B{i}" for i in range(25)], 3: [f"C{i}" for i in range(10)]}
    session = _Session(pages)
    monkeypatch.setattr(value_universe, "_login", lambda *a, **k: session)
    got = value_universe.fetch_screen_symbols("me", "pw", config.MCAP_SCREEN_QUERY)
    assert len(got) == 60 and session.asked == [1, 2, 3, 4]                       # page 4 is empty: it stops there


def test_a_site_that_ignores_the_page_parameter_stops_after_the_repeat(monkeypatch):
    same = [f"A{i}" for i in range(25)]
    session = _Session({p: same for p in range(1, 100)})
    monkeypatch.setattr(value_universe, "_login", lambda *a, **k: session)
    assert len(value_universe.fetch_screen_symbols("me", "pw", "q")) == 25 and session.asked == [1, 2]


def test_nothing_on_the_first_page_is_an_error(monkeypatch):
    monkeypatch.setattr(value_universe, "_login", lambda *a, **k: _Session({}))
    with pytest.raises(RuntimeError):
        value_universe.fetch_screen_symbols("me", "pw", "q")


def test_the_page_limit_holds(monkeypatch):
    session = _Session({p: [f"S{p}X{i}" for i in range(25)] for p in range(1, 500)})
    monkeypatch.setattr(value_universe, "_login", lambda *a, **k: session)
    assert len(value_universe.fetch_screen_symbols("me", "pw", "q", max_pages=5)) == 125 and session.asked == [1, 2, 3, 4, 5]


def test_the_query_the_build_sends_and_the_workflow_that_carries_the_login():
    root = Path(__file__).resolve().parent.parent
    assert config.MCAP_SCREEN_QUERY == "Market Capitalization > 10000" and config.MCAP_MIN_SYMBOLS >= 100
    wf = (root / ".github" / "workflows" / "dashboard_setups.yml").read_text()
    assert "SCREENER_EMAIL" in wf and "SCREENER_PASSWORD" in wf
    build = (root / "scripts" / "build_dashboard_setups.py").read_text()
    assert "fetch_screen_symbols" in build and "frames_all" in build and "leader_trades.history(frames_all, fo)" in build and "mcap_min_cr" in build
    html = (root / "docs" / "index.html").read_text()
    assert html.count("market cap above") >= 3
