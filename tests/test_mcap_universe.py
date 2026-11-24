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
    assert "fetch_screen_symbols" in build and "fetch_extra_constituents" in build and "on_nse | fo" in build and "frames_all" in build and "leader_trades.history(frames, set(frames), fo)" in build and "mcap_min_cr" in build
    html = (root / "docs" / "index.html").read_text()
    assert html.count("market cap above") >= 3


def test_every_module_the_build_script_names_is_imported():
    """The build script's undefined names only show up at run time in CI (a skipped market-cap filter once hid a missing import): check them here."""
    import ast
    import builtins
    tree = ast.parse((Path(__file__).resolve().parent.parent / "scripts" / "build_dashboard_setups.py").read_text())
    bound = set(dir(builtins)) | {"__file__", "__name__"}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            bound |= {(a.asname or a.name).split(".")[0] for a in node.names}
        elif isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            bound.add(node.name)
            if isinstance(node, ast.FunctionDef):
                bound |= {a.arg for a in node.args.args + node.args.kwonlyargs} | ({node.args.vararg.arg} if node.args.vararg else set())
        elif isinstance(node, ast.Lambda):
            bound |= {a.arg for a in node.args.args}
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bound.add(node.id)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
        elif isinstance(node, ast.comprehension):
            bound |= {n.id for n in ast.walk(node.target) if isinstance(n, ast.Name)}
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    assert not (used - bound), sorted(used - bound)


def test_extra_constituent_lists_fail_soft_and_merge(monkeypatch):
    from signals import universe
    pages = {config.NSE_EXTRA_LIST_URLS[0]: "Company Name,Industry,Symbol\nAcme Ltd,Capital Goods,ACME\nBeta Ltd,Healthcare,BETA\n", config.NSE_EXTRA_LIST_URLS[2]: "Company Name,Industry,Symbol\nBeta Two,Other,BETA\nGamma,Services,GAMMA\n"}

    def fake(session, url):
        if url not in pages:
            raise RuntimeError("blocked")
        return pages[url]
    monkeypatch.setattr(universe, "_fetch_csv_text", fake)
    monkeypatch.setattr(universe.requests.Session, "get", lambda *a, **k: None)
    industry, names = universe.fetch_extra_constituents()
    assert industry == {"ACME": "Capital Goods", "BETA": "Healthcare", "GAMMA": "Services"} and names["BETA"] == "Beta Ltd"


def test_nse_lists_are_retried_after_a_403(monkeypatch):
    import requests
    from signals import universe
    monkeypatch.setattr(universe, "RETRY_PAUSE", 0)
    calls = []

    class Bad:
        def raise_for_status(self):
            raise requests.HTTPError("403")

    class Good:
        text = "ok"

        def raise_for_status(self):
            pass

    class Session:
        def get(self, url, headers=None, timeout=None):
            calls.append(url)
            return Bad() if len(calls) < 3 else Good()
    assert universe._fetch_csv_text(Session(), "u") == "ok" and len(calls) == 3
    calls.clear()

    class Always:
        def get(self, url, headers=None, timeout=None):
            calls.append(url)
            return Bad()
    with pytest.raises(requests.HTTPError):
        universe._fetch_csv_text(Always(), "u")
    assert len(calls) == universe.RETRIES


def test_the_build_saves_and_reuses_the_sector_map():
    build = (Path(__file__).resolve().parent.parent / "scripts" / "build_dashboard_setups.py").read_text()
    assert "sector_map.json" in build and "_saved_context(repository)" in build and "**saved_sectors, **sectors" in build and "**saved_names" in build
