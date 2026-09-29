import pytest

from signals import value_universe


class _FakeResponse:
    def __init__(self, text: str, status_code: int = 200, url: str = "https://www.screener.in/screen/raw/"):
        self.text = text
        self.status_code = status_code
        self.url = url

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeSession:
    """Stands in for requests.Session across the login GET/POST and the
    subsequent query GET, recording every call so tests can inspect them."""

    def __init__(self, query_response: _FakeResponse, csrf_token: str | None = "tok123", login_success: bool = True):
        self.headers: dict = {}
        self.cookies = {"csrftoken": csrf_token} if csrf_token else {}
        self._query_response = query_response
        self._login_success = login_success
        self.calls: list[tuple] = []

    def get(self, url, params=None, timeout=None):
        self.calls.append(("GET", url, params))
        if url == value_universe.LOGIN_URL:
            return _FakeResponse("<html>login page</html>")
        return self._query_response

    def post(self, url, data=None, headers=None, timeout=None):
        self.calls.append(("POST", url, data))
        result_url = "https://www.screener.in/" if self._login_success else value_universe.LOGIN_URL
        return _FakeResponse("", url=result_url)


def _screener_html(symbols: list[str]) -> str:
    rows = "".join(
        f'<tr><td><a href="/company/{sym}/">{sym} Ltd</a></td><td>1234</td></tr>' for sym in symbols
    )
    return f"<html><body><table>{rows}</table></body></html>"


def _fake_session(monkeypatch, html: str, **kwargs) -> _FakeSession:
    session = _FakeSession(_FakeResponse(html), **kwargs)
    monkeypatch.setattr(value_universe.requests, "Session", lambda: session)
    return session


def test_extracts_symbols_from_company_links(monkeypatch):
    _fake_session(monkeypatch, _screener_html(["TCS", "INFY", "RELIANCE"]))

    symbols = value_universe.fetch_value_stock_symbols("me@x.com", "pw", query="Profit growth > 25")
    assert symbols == {"TCS", "INFY", "RELIANCE"}


def test_ignores_non_company_links(monkeypatch):
    html = (
        "<html><body>"
        '<a href="/company/TCS/">TCS Ltd</a>'
        '<a href="/screens/new/">New screen</a>'
        '<a href="/user/login/">Login</a>'
        "</body></html>"
    )
    _fake_session(monkeypatch, html)

    symbols = value_universe.fetch_value_stock_symbols("me@x.com", "pw", query="Profit growth > 25")
    assert symbols == {"TCS"}


def test_raises_when_no_symbols_found(monkeypatch):
    html = "<html><body><p>No results, or a login wall we don't recognize</p></body></html>"
    _fake_session(monkeypatch, html)

    with pytest.raises(RuntimeError):
        value_universe.fetch_value_stock_symbols("me@x.com", "pw", query="Profit growth > 25")


def test_raises_on_http_error(monkeypatch):
    session = _FakeSession(_FakeResponse("", status_code=500))
    monkeypatch.setattr(value_universe.requests, "Session", lambda: session)

    with pytest.raises(RuntimeError):
        value_universe.fetch_value_stock_symbols("me@x.com", "pw", query="Profit growth > 25")


def test_raises_when_login_fails(monkeypatch):
    _fake_session(monkeypatch, _screener_html(["TCS"]), login_success=False)

    with pytest.raises(RuntimeError):
        value_universe.fetch_value_stock_symbols("me@x.com", "wrong-password")


def test_raises_when_no_csrf_cookie(monkeypatch):
    _fake_session(monkeypatch, _screener_html(["TCS"]), csrf_token=None)

    with pytest.raises(RuntimeError):
        value_universe.fetch_value_stock_symbols("me@x.com", "pw")


def test_query_is_passed_through(monkeypatch):
    session = _fake_session(monkeypatch, _screener_html(["TCS"]))
    value_universe.fetch_value_stock_symbols("me@x.com", "pw", query="Debt to equity < 0.5")

    query_calls = [c for c in session.calls if c[0] == "GET" and c[1] == value_universe.SCREENER_URL]
    assert query_calls == [("GET", value_universe.SCREENER_URL, {"query": "Debt to equity < 0.5"})]
