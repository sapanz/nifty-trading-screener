import pytest

from signals import value_universe


class _FakeResponse:
    def __init__(self, text: str, status_code: int = 200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _screener_html(symbols: list[str]) -> str:
    rows = "".join(
        f'<tr><td><a href="/company/{sym}/">{sym} Ltd</a></td><td>1234</td></tr>' for sym in symbols
    )
    return f"<html><body><table>{rows}</table></body></html>"


def test_extracts_symbols_from_company_links(monkeypatch):
    html = _screener_html(["TCS", "INFY", "RELIANCE"])
    monkeypatch.setattr(value_universe.requests, "get", lambda *a, **kw: _FakeResponse(html))

    symbols = value_universe.fetch_value_stock_symbols(query="Profit growth > 25")
    assert symbols == {"TCS", "INFY", "RELIANCE"}


def test_ignores_non_company_links(monkeypatch):
    html = (
        "<html><body>"
        '<a href="/company/TCS/">TCS Ltd</a>'
        '<a href="/screens/new/">New screen</a>'
        '<a href="/user/login/">Login</a>'
        "</body></html>"
    )
    monkeypatch.setattr(value_universe.requests, "get", lambda *a, **kw: _FakeResponse(html))

    symbols = value_universe.fetch_value_stock_symbols(query="Profit growth > 25")
    assert symbols == {"TCS"}


def test_raises_when_no_symbols_found(monkeypatch):
    html = "<html><body><p>No results, or a login wall we don't recognize</p></body></html>"
    monkeypatch.setattr(value_universe.requests, "get", lambda *a, **kw: _FakeResponse(html))

    with pytest.raises(RuntimeError):
        value_universe.fetch_value_stock_symbols(query="Profit growth > 25")


def test_raises_on_http_error(monkeypatch):
    monkeypatch.setattr(value_universe.requests, "get", lambda *a, **kw: _FakeResponse("", status_code=500))

    with pytest.raises(RuntimeError):
        value_universe.fetch_value_stock_symbols(query="Profit growth > 25")


def test_query_is_passed_through(monkeypatch):
    captured = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        return _FakeResponse(_screener_html(["TCS"]))

    monkeypatch.setattr(value_universe.requests, "get", fake_get)
    value_universe.fetch_value_stock_symbols(query="Debt to equity < 0.5")

    assert captured["url"] == value_universe.SCREENER_URL
    assert captured["params"] == {"query": "Debt to equity < 0.5"}
